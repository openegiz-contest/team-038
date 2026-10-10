"""Translate the scenario-controller twin's features into simulator settings."""

from . import params as P

SEGMENT_IDS = {f"s{i:02d}" for i in range(1, 13)}
EXCAVATOR_IDS = {e["id"] for e in P.EXCAVATORS}
CLUSTER_IDS = {c["id"] for c in P.CLUSTERS}


def _props(features, name):
    return ((features or {}).get(name) or {}).get("properties") or {}


def _ids(value, allowed):
    if isinstance(value, str):
        value = [value]
    out = []
    for v in value or []:
        v = str(v).removeprefix("road-")
        if v in allowed:
            out.append(v)
    return sorted(set(out))


def _num(value, default, lo, hi):
    try:
        return min(hi, max(lo, float(value)))
    except (TypeError, ValueError):
        return default


def normalise(features):
    """Scenario-controller features -> dict accepted by MineSim.set_scenarios()."""
    sc = {}
    if _props(features, "crusher_failure").get("active"):
        sc["crusher_failure"] = True
    p = _props(features, "road_closure")
    if p.get("active"):
        segs = _ids(p.get("segments"), SEGMENT_IDS)
        if segs:
            sc["road_closure"] = segs
    p = _props(features, "excavator_breakdown")
    if p.get("active"):
        exs = _ids(p.get("excavators"), EXCAVATOR_IDS)
        if exs:
            sc["excavator_breakdown"] = exs
    for name, default, lo, hi in (("hardness_spike", 1.4, 1.0, 2.5), ("low_grade_zone", 0.6, 0.1, 1.0)):
        p = _props(features, name)
        if p.get("active"):
            cl = _ids(p.get("clusters"), CLUSTER_IDS)
            if cl:
                sc[name] = {"factor": _num(p.get("factor"), default, lo, hi), "clusters": cl}
    p = _props(features, "fuel_shortage")
    if p.get("active"):
        sc["fuel_shortage"] = {
            "resupply_lph": _num(p.get("resupply_lph"), 0.0, 0.0, P.FUEL_BAY["resupply_lph"]),
            "bay_stock_l": _num(p.get("bay_stock_l"), 8000.0, 0.0, P.FUEL_BAY["capacity_l"]),
        }
    off = [m for m in ("dispatcher", "maintenance", "energy")
           if _props(features, f"{m}_module").get("enabled", True) is False]
    if off:
        sc["modules_off"] = off
    p = _props(features, "energy_cap")
    if p.get("active"):
        sc["energy_cap"] = {"cap_kw": _num(p.get("cap_kw"), 28000.0, 5000.0, 60000.0)}
    return sc
