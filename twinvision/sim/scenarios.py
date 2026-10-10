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
    return sc
