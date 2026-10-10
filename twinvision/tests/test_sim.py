import hashlib
import json

import pytest

from twinvision.sim import params as P
from twinvision.sim.blocks import geomet
from twinvision.sim.engine import Health, MineSim
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
    assert P.PLANT["grade_min"] <= k["plant_feed_grade"] <= P.PLANT["grade_max"]
    assert 0.2 < k["crusher_kwh_t"] < 0.4                      # Bond: 0.0129 kWh/t per kWh/t of BWi
    assert 14 < k["site_kwh_t"] < 20
    assert 8 < k["copper_tph"] < 16
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


def test_fuel_shortage_rations_trucks_and_protects_ore():
    live, base = paired({"fuel_shortage": {"resupply_lph": 0.0, "bay_stock_l": 8000.0}}, 2, 4)
    assert live.dispatcher.ration > 0
    parked = [t for t in live.trucks if t.why == "fuel rationing"]
    assert len(parked) == live.dispatcher.ration
    assert live.ex_by_id["ex-03"].rate.v < 0.2 * base.ex_by_id["ex-03"].rate.v   # waste stripping suspended
    kl, kb = live.kpis(), base.kpis()
    assert kl["diesel_lph"] < 0.6 * kb["diesel_lph"]
    assert kl["tph_ore"] > 0.6 * kb["tph_ore"]
    assert live.bay_l >= 0 and abs(live.fuel_balance()) < 1e-6
    live.set_scenarios({})
    run(live, 2)
    assert live.dispatcher.ration == 0


def test_energy_cap_limits_demand_and_plant():
    cap = 28000.0
    live, base = paired({"energy_cap": {"cap_kw": cap}}, 2, 2)
    assert live.demand_kw <= cap + 1
    assert base.demand_kw > cap
    assert live.kpis()["plant_feed_tph"] < 0.9 * base.kpis()["plant_feed_tph"]
    live.set_scenarios({})
    run(live, 1)
    assert live.plant_cap_tph == P.PLANT["target_tph"]


def test_health_failures_are_reproducible():
    a, b = Health("truck", "truck-01", 1), Health("truck", "truck-01", 1)
    fa = [i for i in range(20000) if a.use(60.0, 1.0)]
    fb = [i for i in range(20000) if b.use(60.0, 1.0)]
    assert fa == fb and fa
    worn, fresh = Health("truck", "x", 1), Health("truck", "x", 1)
    worn.h, fresh.h = 20.0, 95.0
    assert worn.p_fail(8) > 5 * fresh.p_fail(8)


def test_maintenance_planner_pulls_worn_truck():
    sim = MineSim(42)
    worn = sim.trucks[0]
    worn.health.h = 5.0
    worn.health.budget = 1e9                                   # no breakdown before the review
    run(sim, P.MAINT["review_s"] / 3600 + 1)
    assert worn.health.pms >= 1 or worn.state == "maintenance" or worn.health.pm_pending
    assert "PM truck-01" in " ".join(e[1] for e in sim.events)


def test_crusher_pm_waits_for_stockpile_cover():
    sim = MineSim(42)
    sim.crusher.health.h = 50.0
    sim.crusher.health.budget = 1e9
    for _ in range(int(10 * 3600)):
        sim.step(1.0)
        if sim.crusher.health.in_pm(sim.t):
            break
    assert sim.crusher.health.pms == 1
    assert sim.cos.t >= 0.4 * sim.cos.capacity                  # stock built up before the stop
    assert any("building COS" in e[1] for e in sim.events)


def test_blending_keeps_feed_in_band():
    for seed in (1, 3):
        sim = run(MineSim(seed), 8)
        assert P.PLANT["grade_min"] <= sim.kpis()["plant_feed_grade"] <= P.PLANT["grade_max"]


def test_low_grade_zone_reduces_copper_and_shifts_trucks():
    live, base = paired({"low_grade_zone": {"factor": 0.5, "clusters": ["blocks-north"]}}, 2, 4)
    kl, kb = live.kpis(), base.kpis()
    assert kl["plant_feed_grade"] < kb["plant_feed_grade"]
    assert kl["copper_tph"] < kb["copper_tph"]
    assert live.dispatcher.assigned["ex-01"].v < base.dispatcher.assigned["ex-01"].v


def test_block_model_uses_geomet_distributions():
    cu, bwi = geomet()
    assert len(cu) > 1500 and len(bwi) > 50
    assert 0.6 < sum(cu) / len(cu) < 0.9 and 18 < sum(bwi) / len(bwi) < 23
    sim = MineSim(42)
    north, south = sim.clusters["blocks-north"], sim.clusters["blocks-south"]
    assert min(b.grade for b in north.blocks) > P.CUTOFF_GRADE
    assert max(b.grade for b in south.blocks) < P.CUTOFF_GRADE
    assert all(min(bwi) - 0.01 <= b.bwi <= max(bwi) + 0.01 for b in north.blocks)


def test_scenario_normalisation():
    feats = {
        "crusher_failure": {"properties": {"active": True}},
        "road_closure": {"properties": {"active": True, "segments": ["road-s04", "bogus"]}},
        "excavator_breakdown": {"properties": {"active": False, "excavators": ["ex-01"]}},
        "hardness_spike": {"properties": {"active": True, "factor": 9, "clusters": ["blocks-north"]}},
        "fuel_shortage": {"properties": {"active": True, "resupply_lph": -5}},
        "energy_cap": {"properties": {"active": True, "cap_kw": "abc"}},
    }
    assert normalise(feats) == {"crusher_failure": True, "road_closure": ["s04"],
                                "hardness_spike": {"factor": 2.5, "clusters": ["blocks-north"]},
                                "fuel_shortage": {"resupply_lph": 0.0, "bay_stock_l": 8000.0},
                                "energy_cap": {"cap_kw": 28000.0}}
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


@pytest.mark.parametrize("name", ["mine", "crusher-01", "truck-01", "ex-01", "road-s04", "plant-feed",
                                  "maintenance", "fuel-bay", "power-grid", "dispatcher"])
def test_feature_types_are_stable(name):
    """InfluxDB rejects a field whose type changes; keep each feature's type fixed."""
    sim = MineSim(5)
    types = {}
    for i in range(40):
        if i == 10:
            sim.set_scenarios({"energy_cap": {"cap_kw": 25000.0}, "fuel_shortage": {"resupply_lph": 0.0,
                                                                                      "bay_stock_l": 5000.0}})
        run(sim, 0.25)
        for feat, props in snapshot(sim)[name].items():
            t = type(props["value"]).__name__
            t = "number" if t in ("int", "float") else t
            assert types.setdefault(feat, t) == t, feat
