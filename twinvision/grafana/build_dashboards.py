#!/usr/bin/env python3
"""Generate the provisioned TwinVision Grafana dashboards (JSON, committed).

    python3 twinvision/grafana/build_dashboards.py

Every panel reads InfluxDB (bucket "default", measurement "mqtt_consumer"),
where Telegraf stores the Ditto twin events: field
value_<feature>_properties_value, tag thingId.
"""

import json
import os

NS = "org.twinvision.copper"
DS = {"type": "influxdb", "uid": "opentwins-influxdb"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards", "TwinVision")

LIVE_COLOR, BASE_COLOR = "#1f9e89", "#9a9a9a"


def field(feature):
    return f"value_{feature}_properties_value"


def q_pair(thing, feature, fn="last"):
    """Live and baseline series of one feature, labelled series=live|baseline."""
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r._field == "{field(feature)}")\n'
        f'  |> filter(fn: (r) => r.thingId == "{NS}:{thing}" or r.thingId == "{NS}:{thing}-baseline")\n'
        f"  |> aggregateWindow(every: v.windowPeriod, fn: {fn}, createEmpty: false)\n"
        '  |> map(fn: (r) => ({_time: r._time, _value: float(v: r._value), '
        f'series: if r.thingId == "{NS}:{thing}" then "live" else "baseline"}}))\n'
        '  |> group(columns: ["series"])'
    )


def q_state(regex, feature):
    return (
        'import "strings"\n'
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r._field == "{field(feature)}")\n'
        f"  |> filter(fn: (r) => r.thingId =~ /{regex}/)\n"
        '  |> map(fn: (r) => ({_time: r._time, _value: r._value, '
        f'name: strings.trimPrefix(v: r.thingId, prefix: "{NS}:")}}))\n'
        '  |> group(columns: ["name"])'
    )


class Board:
    def __init__(self, uid, title, description):
        self.d = {
            "uid": uid, "title": title, "description": description,
            "tags": ["twinvision", "mine"], "timezone": "browser", "schemaVersion": 39, "version": 1,
            "refresh": "10s", "time": {"from": "now-30m", "to": "now"}, "panels": [],
            "links": [], "editable": True,
        }
        self.y = 0
        self.x = 0
        self.row_h = 0
        self.id = 0

    def _place(self, w, h):
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_h
            self.row_h = 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def row(self, title):
        if self.x:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        self.id += 1
        self.d["panels"].append({"id": self.id, "type": "row", "title": title, "collapsed": False,
                                 "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.y += 1

    def panel(self, ptype, title, queries, w=8, h=8, unit="none", decimals=None, overrides=None,
              options=None, custom=None, mappings=None, thresholds=None, description="", minv=None, maxv=None):
        self.id += 1
        defaults = {"unit": unit, "custom": custom or {}}
        if decimals is not None:
            defaults["decimals"] = decimals
        if mappings:
            defaults["mappings"] = mappings
        if thresholds:
            defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
        if minv is not None:
            defaults["min"] = minv
        if maxv is not None:
            defaults["max"] = maxv
        p = {
            "id": self.id, "type": ptype, "title": title, "description": description, "datasource": DS,
            "gridPos": self._place(w, h),
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
            "options": options or {},
            "targets": [{"refId": chr(65 + i), "datasource": DS, "query": q} for i, q in enumerate(queries)],
        }
        self.d["panels"].append(p)
        return p

    def pair(self, title, thing, feature, unit, decimals=1, w=8, h=8, description="", thresholds=None):
        ov = [
            {"matcher": {"id": "byFrameRefID", "options": "A"},
             "properties": [{"id": "displayName", "value": "${__field.labels.series}"}]},
            {"matcher": {"id": "byRegexp", "options": ".*live.*"},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": LIVE_COLOR}},
                            {"id": "custom.lineWidth", "value": 2}]},
            {"matcher": {"id": "byRegexp", "options": ".*baseline.*"},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": BASE_COLOR}},
                            {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}}]},
        ]
        custom = {"lineWidth": 1, "fillOpacity": 0, "showPoints": "never", "spanNulls": True}
        if thresholds:
            custom["thresholdsStyle"] = {"mode": "line+area"}
        return self.panel("timeseries", title, [q_pair(thing, feature)], w, h, unit, decimals, ov,
                          {"legend": {"displayMode": "list", "placement": "bottom"},
                           "tooltip": {"mode": "multi"}}, custom, thresholds=thresholds,
                          description=description)

    def stat_pair(self, title, thing, feature, unit, decimals=1, w=4, h=4, description=""):
        ov = [{"matcher": {"id": "byFrameRefID", "options": "A"},
               "properties": [{"id": "displayName", "value": "${__field.labels.series}"}]}]
        return self.panel("stat", title, [q_pair(thing, feature)], w, h, unit, decimals, ov,
                          {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                           "colorMode": "value", "graphMode": "area", "textMode": "value_and_name",
                           "orientation": "horizontal"},
                          thresholds=[{"color": LIVE_COLOR, "value": None}], description=description)


TRUCK_STATES = {
    "loading": "#2ca02c", "hauling": "#1f77b4", "returning": "#9edae5", "queued_at_shovel": "#ff7f0e",
    "queued_at_crusher": "#d62728", "queued_at_rom": "#e377c2", "queued_at_dump": "#c49c94",
    "dumping_crusher": "#8c564b", "dumping_rom": "#bcbd22", "dumping_waste": "#7f7f7f",
    "waiting_road": "#ff0000", "waiting": "#ffbb78", "parked": "#cccccc", "refueling": "#9467bd",
    "to_fuel_bay": "#c5b0d5", "queued_at_fuel_bay": "#aec7e8", "breakdown": "#d62728",
    "to_workshop": "#f7b6d2", "queued_at_workshop": "#f7b6d2", "maintenance": "#e377c2", "to_park": "#dddddd",
}
CRUSHER_STATES = {"crushing": "#2ca02c", "starved": "#ffbb78", "blocked_cos_full": "#ff7f0e",
                  "failed": "#d62728", "breakdown": "#a50f15", "maintenance": "#e377c2"}


def mappings(colors):
    return [{"type": "value", "options": {k: {"color": c, "text": k} for k, c in colors.items()}}]


def overview():
    b = Board("twinvision-overview", "TwinVision Copper Pit - live vs baseline",
              "Live twin (what-if scenarios applied) against the baseline twin (same seed, no scenarios). "
              "Data: twinvision-sim -> MQTT -> Ditto -> Telegraf -> InfluxDB.")
    b.row("Key figures (live vs baseline)")
    b.stat_pair("Mined, t/h", "mine", "tph_mined", "none", 0)
    b.stat_pair("Ore mined, t/h", "mine", "tph_ore", "none", 0)
    b.stat_pair("Plant feed, t/h", "mine", "plant_feed_tph", "none", 0)
    b.stat_pair("Plant feed grade, % Cu", "mine", "plant_feed_grade", "none", 3)
    b.stat_pair("Copper in concentrate, t/h", "mine", "copper_tph", "none", 2)
    b.stat_pair("Availability, %", "mine", "availability_pct", "none", 1)
    b.stat_pair("Mean truck queue, min/cycle", "mine", "mean_queue_min", "none", 2)
    b.stat_pair("Diesel, L/t hauled", "mine", "diesel_l_t", "none", 3)
    b.stat_pair("Site energy, kWh/t feed", "mine", "site_kwh_t", "none", 1)
    b.stat_pair("Peak 15-min demand, kW", "power-grid", "peak_demand_kw", "none", 0)

    b.row("Production and plant feed")
    b.pair("Plant feed (30-min avg), t/h", "mine", "plant_feed_tph", "none", 0)
    b.pair("Plant feed grade, % Cu (band 0.55-0.85)", "mine", "plant_feed_grade", "none", 3,
           thresholds=[{"color": "rgba(214,39,40,0.15)", "value": None},
                       {"color": "rgba(44,160,44,0.10)", "value": 0.55},
                       {"color": "rgba(214,39,40,0.15)", "value": 0.85}])
    b.pair("Copper in concentrate, t/h", "mine", "copper_tph", "none", 2,
           description="plant feed x grade x 87.9 % flotation recovery (GeoMet dataset mean)")
    b.pair("Material mined, t/h", "mine", "tph_mined", "none", 0)
    b.pair("Coarse ore stockpile, t", "stockpile-cos", "stock_t", "none", 0)
    b.pair("ROM stockpile, t", "stockpile-rom", "stock_t", "none", 0)
    b.pair("Crusher throughput (instant), t/h", "crusher-01", "throughput_tph", "none", 0)

    b.row("Fleet, energy and fuel")
    b.pair("Mean truck queue time, min per cycle", "mine", "mean_queue_min", "none", 2)
    b.pair("Diesel per tonne hauled, L/t", "mine", "diesel_l_t", "none", 3)
    b.pair("Crusher specific energy, kWh/t", "mine", "crusher_kwh_t", "none", 3)
    b.pair("Crusher power, kW", "crusher-01", "power_kw", "none", 0)
    b.pair("15-min electrical demand (crusher + plant), kW", "power-grid", "demand_15min_kw", "none", 0)
    b.pair("Site specific energy, kWh/t plant feed", "mine", "site_kwh_t", "none", 1)
    b.pair("Fuel bay stock, L", "fuel-bay", "stock_l", "none", 0)
    b.pair("Fuel bay hours of cover, h", "fuel-bay", "hours_of_cover", "none", 1)
    b.pair("Trucks parked by fuel rationing", "dispatcher", "fuel_rationed_trucks", "none", 0)

    b.row("Maintenance and reliability")
    b.pair("Equipment availability, %", "mine", "availability_pct", "none", 1)
    b.pair("Fleet mean health, %", "maintenance", "fleet_health_pct", "none", 1)
    b.pair("Crusher health, %", "crusher-01", "health_pct", "none", 1)
    b.pair("Failures (cumulative)", "maintenance", "failures_total", "none", 0)
    b.pair("Planned maintenance jobs (cumulative)", "maintenance", "planned_total", "none", 0)
    b.pair("Crusher failure probability, next 8 h", "crusher-01", "failure_prob_8h", "percentunit", 2)

    b.row("Equipment states (live)")
    b.panel("state-timeline", "Haul truck states", [q_state(f"^{NS}:truck-[0-9]+$", "state")], 24, 9,
            mappings=mappings(TRUCK_STATES),
            overrides=[{"matcher": {"id": "byFrameRefID", "options": "A"},
                        "properties": [{"id": "displayName", "value": "${__field.labels.name}"}]}],
            options={"showValue": "never", "mergeValues": True, "rowHeight": 0.8,
                     "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"}},
            custom={"fillOpacity": 85, "lineWidth": 0})
    b.panel("state-timeline", "Crusher and excavators (live and baseline)",
            [q_state(f"^{NS}:(crusher-01|ex-0[1-3])(-baseline)?$", "state")], 24, 7,
            mappings=mappings({**CRUSHER_STATES, "loading": "#2ca02c", "waiting_for_truck": "#ffbb78"}),
            overrides=[{"matcher": {"id": "byFrameRefID", "options": "A"},
                        "properties": [{"id": "displayName", "value": "${__field.labels.name}"}]}],
            options={"showValue": "never", "mergeValues": True, "rowHeight": 0.8,
                     "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"}},
            custom={"fillOpacity": 85, "lineWidth": 0})

    b.row("Audit")
    b.pair("Mass balance residual, t (must stay ~0)", "mine", "mass_balance_residual_t", "none", 6, w=12,
           description="tonnes dug + opening stocks - (stocks + payload in trucks + waste dumped + plant feed)")
    b.pair("Fuel balance residual, L (must stay ~0)", "mine", "fuel_balance_residual_l", "none", 6, w=12,
           description="opening diesel (tanks + bay) + deliveries - (burned + written off + tanks + bay)")
    b.pair("Energy balance residual, kWh (must stay ~0)", "mine", "energy_balance_residual_kwh", "none", 6, w=12,
           description="grid-metered energy - (fixed loads + specific energy x tonnes crushed and milled)")
    b.pair("Grid cap violations (load shedding events)", "power-grid", "cap_violations_total", "none", 0, w=12)
    return b.d


def main():
    os.makedirs(OUT, exist_ok=True)
    for board in (overview(),):
        path = os.path.join(OUT, f"{board['uid']}.json")
        with open(path, "w") as fh:
            json.dump(board, fh, indent=1)
            fh.write("\n")
        print("wrote", os.path.relpath(path))


if __name__ == "__main__":
    main()
