"""TwinVision web: live mine map, what-if scenario controls, live vs baseline KPIs.

Everything shown comes from the platform: twins are discovered with the
Ditto search API and their features polled from the Ditto things API every
second; trends are Flux queries on InfluxDB. The only write is to the
scenario-controller thing. Credentials stay on this backend.

    uvicorn twinvision.web.app:app --host 0.0.0.0 --port 8000
"""

import asyncio
import copy
import csv
import io
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from twinvision.ditto import Ditto

NS = os.environ.get("TWINVISION_NS", "org.twinvision.copper")
BASELINE = "-baseline"
CONTROLLER = f"{NS}:scenario-controller"
POLL_S = float(os.environ.get("WEB_POLL_S", "1.0"))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086").rstrip("/")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
GRAFANA_URL = os.environ.get("GRAFANA_PUBLIC_URL", "")

log = logging.getLogger("twinvision.web")

# KPI rows: (key, label, thing, feature, unit, decimals, better)
# better: "up" = higher is better, "down" = lower is better, "band" = inside the feed grade band.
KPIS = [
    ("tph_mined", "Material moved", "mine", "tph_mined", "t/h", 0, "up"),
    ("tph_ore", "Ore mined", "mine", "tph_ore", "t/h", 0, "up"),
    ("plant_feed_tph", "Plant feed", "mine", "plant_feed_tph", "t/h", 0, "up"),
    ("plant_feed_grade", "Feed grade", "mine", "plant_feed_grade", "% Cu", 3, "band"),
    ("copper_tph", "Copper to concentrate", "mine", "copper_tph", "t/h", 2, "up"),
    ("mean_queue_min", "Truck queue time", "mine", "mean_queue_min", "min/cycle", 2, "down"),
    ("availability_pct", "Availability", "mine", "availability_pct", "%", 1, "up"),
    ("crusher_kwh_t", "Crusher energy", "mine", "crusher_kwh_t", "kWh/t", 3, "down"),
    ("site_kwh_t", "Site energy", "mine", "site_kwh_t", "kWh/t", 1, "down"),
    ("diesel_l_t", "Diesel", "mine", "diesel_l_t", "L/t", 3, "down"),
    ("demand_kw", "Grid demand (15 min)", "power-grid", "demand_15min_kw", "kW", 0, "down"),
]

# What-if catalogue: parameters the UI may send, with bounds (the simulator clamps again).
SCENARIOS = {
    "crusher_failure": {"label": "Crusher failure", "params": {}},
    "road_closure": {"label": "Road closure", "params": {"segments": "segments"}},
    "excavator_breakdown": {"label": "Excavator breakdown", "params": {"excavators": "excavators"}},
    "hardness_spike": {"label": "Hardness spike", "params": {"factor": (1.0, 2.5), "clusters": "clusters"}},
    "low_grade_zone": {"label": "Low-grade zone", "params": {"factor": (0.1, 1.0), "clusters": "clusters"}},
    "fuel_shortage": {"label": "Fuel shortage", "params": {"resupply_lph": (0.0, 2200.0),
                                                           "bay_stock_l": (0.0, 80000.0)}},
    "energy_cap": {"label": "Grid power cap", "params": {"cap_kw": (5000.0, 60000.0)}},
}


# Decision modules: (scenario-controller feature, twin holding its decision log, label)
MODULES = {
    "dispatcher": ("dispatcher_module", "dispatcher", "Haulage and dispatch"),
    "maintenance": ("maintenance_module", "maintenance", "Maintenance planner"),
    "energy": ("energy_module", "power-grid", "Energy manager"),
}


def short(thing_id):
    return thing_id.split(":", 1)[1]


def flat(features):
    """Ditto features -> {feature: value}."""
    out = {}
    for k, v in (features or {}).items():
        props = (v or {}).get("properties") or {}
        out[k] = props.get("value", props)
    return out


class Store:
    """Polls Ditto in a background thread and keeps the latest snapshot."""

    def __init__(self, ditto):
        self.ditto = ditto
        self.lock = threading.Lock()
        self.attrs = {}          # short id -> attributes
        self.units = {}          # short id -> {feature: unit}
        self.ids = []
        self.state = None
        self.version = 0
        self.error = "starting"
        self.last_ok = 0.0
        self.history = []        # undo stack: {"label", "changes": [(feature, before, after)], "at"}
        self.discovered_at = 0.0
        self.stop = threading.Event()

    # ------------------------------------------------------------ discovery
    def discover(self):
        found, cursor = {}, None
        while True:
            q = {"filter": f'like(thingId,"{NS}:*")', "fields": "thingId,attributes,features",
                 "option": "size(200)" + (f",cursor({cursor})" if cursor else "")}
            _, page = self.ditto.request("GET", "/api/2/search/things?" + urllib.parse.urlencode(q))
            for item in page.get("items", []):
                found[short(item["thingId"])] = item
            cursor = page.get("cursor")
            if not cursor:
                break
        if not found:
            raise RuntimeError("no TwinVision twins in Ditto yet")
        attrs, units = {}, {}
        for name, item in found.items():
            attrs[name] = item.get("attributes", {})
            units[name] = {k: ((v or {}).get("properties") or {}).get("unit", "")
                           for k, v in (item.get("features") or {}).items()}
        with self.lock:
            self.attrs, self.units = attrs, units
            self.ids = sorted(f"{NS}:{n}" for n in found)
            self.discovered_at = time.time()

    # ------------------------------------------------------------ polling
    def fetch(self):
        things = {}
        for i in range(0, len(self.ids), 40):
            chunk = self.ids[i:i + 40]
            _, items = self.ditto.request("GET", "/api/2/things?ids=" + ",".join(chunk) + "&fields=thingId,features",
                                          timeout=10)
            for item in items or []:
                things[short(item["thingId"])] = flat(item.get("features"))
        return things

    def build(self, things):
        live = {n: f for n, f in things.items() if not n.endswith(BASELINE)}
        base = {n[: -len(BASELINE)]: f for n, f in things.items() if n.endswith(BASELINE)}
        band = (live.get("plant-feed", {}).get("grade_min", 0.55), live.get("plant-feed", {}).get("grade_max", 0.85))
        kpis = []
        for key, label, thing, feat, unit, dec, better in KPIS:
            lv = live.get(thing, {}).get(feat)
            bv = base.get(thing, {}).get(feat)
            row = {"key": key, "label": label, "thing": thing, "feature": feat, "unit": unit, "decimals": dec,
                   "better": better, "live": lv, "base": bv, "delta": None, "pct": None, "verdict": "same"}
            if isinstance(lv, (int, float)) and isinstance(bv, (int, float)):
                d = lv - bv
                row["delta"] = d
                row["pct"] = 100.0 * d / abs(bv) if abs(bv) > 1e-9 else None
                tol = max(abs(bv) * 0.01, 10 ** -dec)
                if abs(d) > tol:
                    if better == "band":
                        mid = (band[0] + band[1]) / 2
                        row["verdict"] = "better" if abs(lv - mid) < abs(bv - mid) else "worse"
                    else:
                        row["verdict"] = "better" if (d > 0) == (better == "up") else "worse"
            kpis.append(row)
        try:
            events = json.loads(live.get("mine", {}).get("event_log") or "[]")
        except ValueError:
            events = []
        scen = live.pop("scenario-controller", {})
        return {"ts": time.time(), "live": live, "base": base, "kpis": kpis, "events": events,
                "scenarios": scen, "band": band}

    def run(self):
        while not self.stop.is_set():
            t0 = time.time()
            try:
                if not self.ids or time.time() - self.discovered_at > 120:
                    self.discover()
                state = self.build(self.fetch())
                with self.lock:
                    self.state = state
                    self.version += 1
                    self.error = ""
                    self.last_ok = time.time()
            except Exception as exc:  # keep serving the last good snapshot
                with self.lock:
                    self.error = f"{type(exc).__name__}: {exc}"
                log.warning("poll failed: %s", self.error)
            self.stop.wait(max(0.05, POLL_S - (time.time() - t0)))

    def snapshot(self):
        with self.lock:
            if self.state is None:
                return None
            s = dict(self.state)
            s["version"] = self.version
            s["ok"] = not self.error and time.time() - self.last_ok < 10
            s["error"] = self.error
            s["undo"] = [{"label": h["label"], "at": h["at"]} for h in self.history[-10:]]
            return s

    # ------------------------------------------------------------ scenarios
    def controller(self):
        _, feats = self.ditto.request("GET", f"/api/2/things/{CONTROLLER}/features")
        return feats or {}

    def write(self, label, changes, record=True):
        """Apply {feature: new properties} to the scenario controller as one undoable step."""
        feats = self.controller()
        done = []
        for name, props in changes.items():
            before = ((feats.get(name) or {}).get("properties") or {})
            self.ditto.request("PUT", f"/api/2/things/{CONTROLLER}/features/{name}/properties", props)
            done.append((name, before, props))
        if record and done:
            with self.lock:
                sim_h = ((self.state or {}).get("live", {}).get("mine", {}) or {}).get("sim_time_h")
                self.history.append({"label": label, "changes": done, "at": sim_h})
                del self.history[:-50]
        return done


def clean_params(name, raw, current, attrs):
    spec = SCENARIOS[name]["params"]
    props = dict(current)
    lists = {
        "segments": sorted(n.removeprefix("road-") for n in attrs if n.startswith("road-") and not n.endswith(BASELINE)),
        "excavators": sorted(n for n in attrs if n.startswith("ex-") and not n.endswith(BASELINE)),
        "clusters": sorted(n for n in attrs if n.startswith("blocks-") and not n.endswith(BASELINE)),
    }
    for key, rule in spec.items():
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(rule, tuple):
            try:
                props[key] = min(rule[1], max(rule[0], float(val)))
            except (TypeError, ValueError):
                raise HTTPException(422, f"{key} must be a number")
        else:
            vals = [val] if isinstance(val, str) else list(val or [])
            vals = [str(v).removeprefix("road-") for v in vals]
            bad = [v for v in vals if v not in lists[rule]]
            if bad or not vals:
                raise HTTPException(422, f"{key}: unknown or empty {bad or vals}; allowed {lists[rule]}")
            props[key] = sorted(set(vals))
    return props


ditto = Ditto(os.environ.get("DITTO_URL", "http://ditto-nginx:80"), "ditto", os.environ.get("DITTO_PASSWORD", ""))
store = Store(ditto)


@asynccontextmanager
async def lifespan(_app):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    threading.Thread(target=store.run, name="ditto-poller", daemon=True).start()
    yield
    store.stop.set()


app = FastAPI(title="TwinVision Copper Pit", version="1.0", lifespan=lifespan,
              description="Live mine map and what-if control API on top of Eclipse Ditto and InfluxDB.")


@app.get("/api/health")
def health():
    s = store.snapshot()
    ok = bool(s and s["ok"])
    if not ok:
        raise HTTPException(503, store.error or "no data yet")
    return {"ok": True, "twins": len(store.ids), "sim_time_h": s["live"].get("mine", {}).get("sim_time_h")}


@app.get("/api/static")
def static_model():
    """Static twin attributes for drawing the map, plus units and the what-if catalogue."""
    if not store.attrs:
        raise HTTPException(503, store.error or "twins not discovered yet")
    with store.lock:
        attrs = {n: a for n, a in store.attrs.items() if not n.endswith(BASELINE)}
        units = {n: u for n, u in store.units.items() if not n.endswith(BASELINE)}
    return {"namespace": NS, "attributes": attrs, "units": units, "grafana": GRAFANA_URL,
            "kpis": [{"key": k[0], "label": k[1], "unit": k[4], "decimals": k[5], "better": k[6]} for k in KPIS],
            "scenarios": {k: {"label": v["label"], "params": list(v["params"])} for k, v in SCENARIOS.items()},
            "modules": {k: {"label": v[2], "twin": v[1], "feature": v[0]} for k, v in MODULES.items()}}


@app.get("/api/state")
def state():
    s = store.snapshot()
    if s is None:
        raise HTTPException(503, store.error or "no data yet")
    return s


@app.get("/api/stream")
async def stream():
    """Server-sent events: one state snapshot per Ditto poll."""
    async def gen():
        seen = -1
        while True:
            s = store.snapshot()
            if s is not None and s["version"] != seen:
                seen = s["version"]
                yield f"data: {json.dumps(s, separators=(',', ':'))}\n\n"
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(POLL_S / 2)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/twins/{name}")
def twin(name: str):
    """One live twin and its baseline counterpart, straight from Ditto."""
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise HTTPException(404)
    out = {}
    for key, tid in (("live", f"{NS}:{name}"), ("baseline", f"{NS}:{name}{BASELINE}")):
        try:
            out[key] = ditto.request("GET", f"/api/2/things/{tid}", timeout=10)[1]
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise HTTPException(502, f"Ditto: {exc}")
            out[key] = None
    if not out["live"]:
        raise HTTPException(404, f"{NS}:{name} not found")
    return out


@app.get("/api/scenarios")
def scenarios():
    snap = store.snapshot()
    return {"catalogue": {k: v["label"] for k, v in SCENARIOS.items()},
            "state": flat(store.controller()),
            "undo": snap["undo"] if snap else []}


@app.post("/api/scenarios/reset")
def reset_all():
    """Deactivate every scenario and switch every module back on, as one undo step."""
    feats = store.controller()
    changes = {}
    for name in SCENARIOS:
        props = (feats.get(name) or {}).get("properties") or {}
        if props.get("active"):
            changes[name] = {**props, "active": False}
    modules_on = []
    for name, (feature, _, _) in MODULES.items():
        if ((feats.get(feature) or {}).get("properties") or {}).get("enabled") is False:
            changes[feature] = {"enabled": True}
            modules_on.append(name)
    store.write("Reset all", changes)
    return {"deactivated": sorted(k for k in changes if k in SCENARIOS), "modules_on": modules_on}


@app.post("/api/scenarios/undo")
def undo_last():
    """Restore the scenario-controller features changed by the last action."""
    with store.lock:
        if not store.history:
            raise HTTPException(409, "nothing to undo")
        h = store.history.pop()
    store.write(h["label"], {name: before for name, before, _ in h["changes"]}, record=False)
    return {"undone": h["label"], "restored": {name: before for name, before, _ in h["changes"]}}


@app.post("/api/scenarios/{name}")
def activate(name: str, params: dict = Body(default={})):
    """Activate (or update) one what-if scenario."""
    if name not in SCENARIOS:
        raise HTTPException(404, f"unknown scenario {name}; known: {sorted(SCENARIOS)}")
    current = ((store.controller().get(name) or {}).get("properties") or {})
    props = clean_params(name, params or {}, current, store.attrs)
    props["active"] = True
    store.write(f"{SCENARIOS[name]['label']} on", {name: props})
    return {"scenario": name, "properties": props}


@app.delete("/api/scenarios/{name}")
def deactivate(name: str):
    """Undo one scenario: set it inactive, keeping its parameters."""
    if name not in SCENARIOS:
        raise HTTPException(404, f"unknown scenario {name}")
    current = ((store.controller().get(name) or {}).get("properties") or {})
    props = {**copy.deepcopy(current), "active": False}
    store.write(f"{SCENARIOS[name]['label']} off", {name: props})
    return {"scenario": name, "properties": props}


@app.post("/api/resync")
def resync():
    """Copy the baseline state into the live copy (stockpiles, positions, wear, random streams).

    Active scenarios and module switches stay as they are and act on the copied state.
    A state copy cannot be undone, so it is not put on the undo stack."""
    seq = ((store.controller().get("resync") or {}).get("properties") or {}).get("seq") or 0
    store.write("Resync live to baseline", {"resync": {"seq": int(seq) + 1}}, record=False)
    return {"resync": int(seq) + 1}


@app.post("/api/modules/{name}")
def set_module(name: str, body: dict = Body(...)):
    """Switch a decision module on or off in the live twin (the baseline keeps every module on)."""
    if name not in MODULES:
        raise HTTPException(404, f"unknown module {name}; known: {sorted(MODULES)}")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(422, "body must be {\"enabled\": true|false}")
    feature, _, label = MODULES[name]
    store.write(f"{label} {'on' if enabled else 'off'}", {feature: {"enabled": enabled}})
    return {"module": name, "enabled": enabled}


@app.get("/api/trend")
def trend(thing: str = "mine", feature: str = "plant_feed_tph", minutes: int = 30):
    """Live and baseline series of one feature from InfluxDB (Telegraf-written twin events)."""
    if not re.fullmatch(r"[a-z0-9-]+", thing) or not re.fullmatch(r"[a-z0-9_]+", feature):
        raise HTTPException(422, "bad thing or feature name")
    minutes = max(2, min(int(minutes), 24 * 60))
    every = max(5, minutes * 60 // 180)
    flux = (
        f'from(bucket: "default") |> range(start: -{minutes}m)'
        f' |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r._field == "value_{feature}_properties_value")'
        f' |> filter(fn: (r) => r.thingId == "{NS}:{thing}" or r.thingId == "{NS}:{thing}{BASELINE}")'
        f' |> aggregateWindow(every: {every}s, fn: last, createEmpty: false)'
        ' |> keep(columns: ["_time", "_value", "thingId"])'
    )
    req = urllib.request.Request(
        f"{INFLUX_URL}/api/v2/query?org=opentwins", method="POST",
        data=json.dumps({"query": flux, "type": "flux", "dialect": {"annotations": []}}).encode(),
        headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "application/json",
                 "Accept": "application/csv"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(502, f"InfluxDB: {exc}")
    series = {"live": [], "baseline": []}
    for row in csv.DictReader(io.StringIO(text)):
        if not row.get("_time") or row.get("_value") in (None, ""):
            continue
        try:
            v = float(row["_value"])
        except ValueError:
            continue
        key = "baseline" if row.get("thingId", "").endswith(BASELINE) else "live"
        series[key].append([row["_time"], v])
    return {"thing": thing, "feature": feature, "minutes": minutes, "source": "influxdb", **series}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
