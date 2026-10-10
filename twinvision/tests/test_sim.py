import hashlib
import json

import pytest

from twinvision.sim import params as P
from twinvision.sim.engine import MineSim
from twinvision.sim.scenarios import normalise
from twinvision.sim.twins import definitions, ditto_message, snapshot


def run(sim, hours, dt=1.0):
    for _ in range(int(hours * 3600 / dt)):
        sim.step(dt)
    return sim


def digest(sim):
    return hashlib.sha256(json.dumps(snapshot(sim), sort_keys=True).encode()).hexdigest()


def test_mass_balance_every_step():
    sim = MineSim(7)
    worst = 0.0
    for _ in range(4 * 3600):
        sim.step(1.0)
        worst = max(worst, abs(sim.mass_balance()))
    assert worst < 1e-6
    assert sum(e.tonnes for e in sim.excavators) > 10000      # the mine actually produced


def test_mass_balance_holds_through_scenarios():
    sim = run(MineSim(3), 1)
    for sc in ({"crusher_failure": True}, {"road_closure": ["s04", "s06"]},
               {"excavator_breakdown": ["ex-01", "ex-03"]}, {}):
        sim.set_scenarios(sc)
        for _ in range(3600):
            sim.step(1.0)
            assert abs(sim.mass_balance()) < 1e-6
            assert abs(sim.fuel_balance()) < 1e-6


def test_fuel_balance_and_refuelling():
    sim = run(MineSim(11), 16)
    assert abs(sim.fuel_balance()) < 1e-6
    assert sim.fuel_dispensed > 0                               # trucks went to the fuel bay
    assert all(t.fuel > 0 for t in sim.trucks)


def test_deterministic_for_same_seed():
    assert digest(run(MineSim(42), 2)) == digest(run(MineSim(42), 2))
    assert digest(run(MineSim(42), 2)) != digest(run(MineSim(43), 2))


def test_baseline_equals_live_without_scenarios():
    live, base = MineSim(42), MineSim(42, "-baseline")
    for _ in range(3):
        run(live, 1)
        run(base, 1)
        assert digest(live) == digest(base)


def test_plausible_operation():
    sim = run(MineSim(42), 6)
    k = sim.kpis()
    assert 5000 < k["tph_mined"] < 11000
    assert 1500 < k["plant_feed_tph"] <= P.PLANT["target_tph"] + 1
    assert 0.3 < k["plant_feed_grade"] < 0.8
    assert 0.1 < k["crusher_kwh_t"] < 0.4
    assert 0.1 < k["diesel_l_t"] < 0.6


def paired(scenario, before_h=2, during_h=2):
    live, base = run(MineSim(42), before_h), run(MineSim(42, "-baseline"), before_h)
    live.set_scenarios(scenario)
    run(live, during_h)
    run(base, during_h)
    return live, base


def test_crusher_failure_propagates():
    live, base = paired({"crusher_failure": True})
    assert live.crusher.state == "failed" and live.crusher.tonnes < base.crusher.tonnes
    assert live.cos.t < base.cos.t - 2000                       # coarse ore stockpile drawn down
    assert live.rom.t > base.rom.t + 2000                       # ore re-routed to the ROM pad
    assert live.plant_t < base.plant_t                          # plant feed fell
    assert live.crusher.power_kw == 0
    assert live.dispatcher.to_rom > base.dispatcher.to_rom


def test_road_closure_reroutes_and_costs():
    def ore(sim):
        return sum(e.tonnes for e in sim.excavators if e.role == "ore")

    live, base = run(MineSim(42), 2), run(MineSim(42, "-baseline"), 2)
    ore0 = ore(live)
    live.set_scenarios({"road_closure": ["s04"]})
    run(live, 0.1)
    passes_at_close = live.net.segments["s04"].passes     # trucks already on it may finish
    run(live, 1.9)
    run(base, 2)
    assert live.net.segments["s04"].passes == passes_at_close
    s10_live, s10_base = live.net.segments["s10"].passes, base.net.segments["s10"].passes
    assert s10_live > s10_base + 10                             # traffic moved to the west ramp
    assert ore(live) - ore0 < 0.97 * (ore(base) - ore0)         # longer ore cycles, fewer ore tonnes
    assert live.kpis()["diesel_l_t"] > base.kpis()["diesel_l_t"]


def test_excavator_breakdown_shifts_fleet():
    live, base = paired({"excavator_breakdown": ["ex-01"]})
    ex = live.ex_by_id["ex-01"]
    assert ex.state == "failed" and not ex.queue and ex.current is None
    assert not any(t.dest == ("excavator", "ex-01") for t in live.trucks)
    assert live.ex_by_id["ex-02"].tonnes > base.ex_by_id["ex-02"].tonnes


def test_hardness_spike_slows_crusher_and_costs_energy():
    live, base = paired({"hardness_spike": {"factor": 1.6, "clusters": ["blocks-north", "blocks-east"]}}, 2, 3)
    assert live.kpis()["crusher_kwh_t"] > base.kpis()["crusher_kwh_t"] * 1.15
    assert live.crusher.capacity_tph(20) < live.crusher.capacity_tph(14)


def test_undo_restores_normal_operation():
    live, base = paired({"crusher_failure": True, "road_closure": ["s04"]}, 1, 1)
    live.set_scenarios({})
    run(live, 4)
    run(base, 4)
    assert live.crusher.state != "failed" and not live.net.segments["s04"].closed
    kl, kb = live.kpis(), base.kpis()
    assert abs(kl["plant_feed_tph"] - kb["plant_feed_tph"]) < 0.1 * kb["plant_feed_tph"]
    assert abs(kl["tph_mined"] - kb["tph_mined"]) < 0.15 * kb["tph_mined"]


def test_scenario_normalisation():
    feats = {
        "crusher_failure": {"properties": {"active": True}},
        "road_closure": {"properties": {"active": True, "segments": ["road-s04", "bogus"]}},
        "excavator_breakdown": {"properties": {"active": False, "excavators": ["ex-01"]}},
        "hardness_spike": {"properties": {"active": True, "factor": 9, "clusters": ["blocks-north"]}},
    }
    assert normalise(feats) == {"crusher_failure": True, "road_closure": ["s04"],
                                "hardness_spike": {"factor": 2.5, "clusters": ["blocks-north"]}}
    assert normalise({}) == {}
    assert normalise(None) == {}


def test_twin_definitions_and_messages():
    sim = MineSim(42)
    live, base = definitions(sim), definitions(sim, baseline=True)
    assert len(live) == len(base) + 1                           # scenario controller is live-only
    for tid, body in {**live, **base}.items():
        assert body["policyId"] == "default:basic_policy"
        parent = body["attributes"].get("_parents")
        assert parent is None or parent in live or parent in base
        for feat in body["features"].values():
            assert "properties" in feat
    msg = ditto_message("truck-01", snapshot(sim)["truck-01"])
    assert msg["topic"] == f"{P.NAMESPACE}/truck-01/things/twin/commands/modify"
    assert msg["path"] == "/features"
    assert msg["value"]["payload_t"]["properties"]["unit"] == "t"


@pytest.mark.parametrize("name", ["mine", "crusher-01", "truck-01", "road-s04", "plant-feed"])
def test_feature_types_are_stable(name):
    """InfluxDB rejects a field whose type changes; keep each feature's type fixed."""
    sim = MineSim(5)
    types = {}
    for _ in range(40):
        run(sim, 0.25)
        for feat, props in snapshot(sim)[name].items():
            t = type(props["value"]).__name__
            t = "number" if t in ("int", "float") else t
            assert types.setdefault(feat, t) == t, feat
