"""Twin catalog: thing ids, types, hierarchy, static attributes and features.

Used by the setup job (definitions) and by the simulator (features). The
schema is documented in twinvision/SCHEMA.md.
"""

from . import params as P
from .layout import FACILITIES, NODES, PIT_FLOOR_Z, BENCH_HEIGHT, bench_contours

NS = P.NAMESPACE
POLICY = "default:basic_policy"
BASELINE = "-baseline"

# Twins published often (every tick) vs. slowly changing ones.
SLOW = ("pit", "blocks-", "road-")


def is_slow(name):
    return any(name.startswith(p) for p in SLOW)


def thing_id(name):
    return f"{NS}:{name}"


def r(x, nd=2):
    return round(float(x), nd)


def f(value, unit=None):
    d = {"value": value}
    if unit:
        d["unit"] = unit
    return d


# ----------------------------------------------------------------- definitions
def definitions(sim, baseline=False):
    """{thingId: thing JSON} for one copy of the mine, parents before children."""
    sfx = BASELINE if baseline else ""
    role = "baseline (no what-if scenarios)" if baseline else "live"
    out = {}

    def add(name, ttype, parent, label, **attrs):
        a = {"name": label + (" [baseline]" if baseline else ""), "type": ttype, "_isType": False,
             "copy": role, "site": P.SITE_NAME}
        if parent:
            a["_parents"] = thing_id(parent + sfx)
        if baseline:
            a["live_twin"] = thing_id(name)
        elif name != "scenario-controller" and name != "mine":
            a["baseline_twin"] = thing_id(name + BASELINE)
        a.update(attrs)
        out[thing_id(name + sfx)] = {"policyId": POLICY, "attributes": a, "features": {}}

    add("mine", "Mine", None, P.SITE_NAME,
        description="Fictional open-pit copper mine in Kazakhstan (synthetic data, see DATA.md)",
        commodity="copper", time_compression="see sim_time_h feature",
        **({"baseline_twin": thing_id("mine" + BASELINE)} if not baseline else {}))
    add("pit", "Pit", "mine", "Main pit",
        rim={"rx": 700.0, "ry": 450.0}, floor_z=PIT_FLOOR_Z, bench_height_m=BENCH_HEIGHT,
        benches=[{"z": z, "rx": rx, "ry": ry} for z, rx, ry in bench_contours()])
    for cid, cl in sim.clusters.items():
        add(cid, "BlockCluster", "pit", f"Block cluster {cid.split('-')[1]} (bench {cl.spec['bench_z']})",
            bench_z=cl.spec["bench_z"], rock=cl.rock, excavator=thing_id(cl.spec["excavator"] + sfx),
            cutoff_grade_pct=P.CUTOFF_GRADE, **cl.geometry())
    for ex in sim.excavators:
        s = ex.spec
        x, y, z = NODES[ex.node][0]
        add(ex.id, "Excavator", "pit", f"Excavator {ex.id.upper()}", model=s["model"],
            bucket_m3=s["bucket_m3"], bucket_t=s["bucket_t"], role=s["role"], node=ex.node,
            location={"x": x, "y": y, "z": z}, digs=thing_id(s["cluster"] + sfx))
    for tr in sim.trucks:
        T = P.TRUCK
        add(tr.id, "HaulTruck", "pit", f"Haul truck {tr.id.upper()}", model=T["model"],
            payload_t=T["payload_t"], empty_t=T["empty_t"], net_power_kw=T["net_power_kw"],
            tank_l=T["tank_l"])
    for sid, seg in sim.net.segments.items():
        add(f"road-{sid}", "RoadSegment", "pit", f"Road {sid}: {seg.name}",
            from_node=seg.a, to_node=seg.b, length_m=r(seg.length, 1), grade_pct=r(seg.grade * 100, 2),
            speed_limit_kmh=seg.limit_kmh, capacity_trucks=seg.capacity,
            geometry=[[p[0], p[1], p[2]] for p in seg.pts])
    fac = FACILITIES
    add("crusher-01", "Crusher", "mine", "Primary crusher CR-01", model=P.CRUSHER["model"],
        installed_kw=P.CRUSHER["installed_kw"], pocket_t=P.CRUSHER["pocket_t"],
        vol_cap_tph=P.CRUSHER["vol_cap_tph"], location=fac["crusher"], node="CR",
        feeds=thing_id("stockpile-cos" + sfx))
    add("stockpile-rom", "Stockpile", "mine", "ROM stockpile", role="run-of-mine buffer before the crusher",
        capacity_t=P.ROM["capacity_t"], location=fac["stockpile-rom"], node="ROM",
        reclaimed_by=P.ROM["loader_model"], feeds=thing_id("crusher-01" + sfx))
    add("stockpile-cos", "Stockpile", "mine", "Coarse ore stockpile", role="crushed ore buffer before the plant",
        capacity_t=P.COS["capacity_t"], location=fac["stockpile-cos"], feeds=thing_id("plant-feed" + sfx))
    add("plant-feed", "PlantFeed", "mine", "Concentrator feed", target_tph=P.PLANT["target_tph"],
        grade_min_pct=P.PLANT["grade_min"], grade_max_pct=P.PLANT["grade_max"], location=fac["plant-feed"])
    add("waste-dump", "WasteDump", "mine", "Waste dump", location=fac["waste-dump"], node="DUMP")
    add("dispatcher", "DispatcherController", "mine", "Truck dispatcher",
        strategy="min expected loading start with target-rate deficit; ore to crusher/ROM by COS level and crusher wait",
        targets_tph=P.DISPATCH["target_tph"])
    if not baseline:
        add("scenario-controller", "ScenarioController", "mine", "What-if scenario controller",
            description="Written by the TwinVision web API; the live simulator applies it")
        out[thing_id("scenario-controller")]["features"] = scenario_defaults()
    # Road graph nodes, for the map.
    out[thing_id("mine" + sfx)]["attributes"]["road_nodes"] = {
        k: {"x": v[0][0], "y": v[0][1], "z": v[0][2], "label": v[1]} for k, v in NODES.items()}
    out[thing_id("mine" + sfx)]["attributes"]["facilities"] = FACILITIES
    snap = snapshot(sim)
    for tid, body in out.items():
        if not body["features"]:
            name = tid.split(":", 1)[1].removesuffix(BASELINE)
            feats = snap.get(name, {})
            body["features"] = {k: {"properties": v} for k, v in feats.items()}
    return out


def scenario_defaults():
    return {
        "crusher_failure": {"properties": {"active": False}},
        "road_closure": {"properties": {"active": False, "segments": ["s04"]}},
        "excavator_breakdown": {"properties": {"active": False, "excavators": ["ex-01"]}},
        "hardness_spike": {"properties": {"active": False, "factor": 1.4, "clusters": ["blocks-north"]}},
        "low_grade_zone": {"properties": {"active": False, "factor": 0.6, "clusters": ["blocks-north"]}},
    }


# ----------------------------------------------------------------- features
def snapshot(sim):
    """{thing name (no suffix): {feature: {value, unit}}} for one MineSim."""
    k = sim.kpis()
    out = {}
    c = sim.crusher
    trucks_q = sum(1 for t in sim.trucks if t.state in ("queued", "waiting") or t.why)
    active = sorted(n for n, v in sim.scenarios.items() if v)
    site_kw = c.power_kw + sim.plant_kw
    out["mine"] = {
        "sim_time_h": f(r(sim.t / 3600.0, 3), "h"),
        "tph_mined": f(r(k["tph_mined"], 1), "t/h"),
        "tph_ore": f(r(k["tph_ore"], 1), "t/h"),
        "tph_waste": f(r(k["tph_waste"], 1), "t/h"),
        "tph_crushed": f(r(k["tph_crushed"], 1), "t/h"),
        "plant_feed_tph": f(r(k["plant_feed_tph"], 1), "t/h"),
        "plant_feed_grade": f(r(k["plant_feed_grade"], 4), "% Cu"),
        "mean_queue_min": f(r(k["mean_queue_min"], 2), "min"),
        "availability_pct": f(r(k["availability_pct"], 2), "%"),
        "crusher_kwh_t": f(r(k["crusher_kwh_t"], 4), "kWh/t"),
        "diesel_l_t": f(r(k["diesel_l_t"], 4), "L/t"),
        "diesel_lph": f(r(k["diesel_lph"], 1), "L/h"),
        "site_power_kw": f(r(site_kw, 1), "kW"),
        "mined_total_t": f(r(sum(e.tonnes for e in sim.excavators), 1), "t"),
        "plant_feed_total_t": f(r(sim.plant_t, 1), "t"),
        "copper_in_feed_t": f(r(sim.plant_cu_t, 2), "t"),
        "waste_total_t": f(r(sim.waste_t, 1), "t"),
        "mass_balance_residual_t": f(r(sim.mass_balance(), 6), "t"),
        "fuel_balance_residual_l": f(r(sim.fuel_balance(), 6), "L"),
        "trucks_queued": f(trucks_q, "trucks"),
        "active_scenarios": f(",".join(active) if active else "none"),
        "last_event": f(sim.events[-1][1] if sim.events else ""),
    }
    out["pit"] = {
        "trucks_in_pit": f(sum(1 for t in sim.trucks if t.state != "parked"), "trucks"),
        "faces_active": f(sum(1 for e in sim.excavators if not e.down), "faces"),
        "roads_closed": f(sum(1 for s in sim.net.segments.values() if s.closed), "segments"),
    }
    for cid, cl in sim.clusters.items():
        out[cid] = {
            "face_grade": f(r(cl.face_grade, 3), "% Cu"),
            "face_bwi": f(r(cl.face_bwi, 2), "kWh/t"),
            "face_rock": f(cl.rock),
            "current_block": f(cl.k, "index"),
            "block_remaining_t": f(r(cl.current.remaining, 1), "t"),
            "cut": f(cl.cut, "cut"),
            "remaining_t": f(r(cl.remaining_t(), 1), "t"),
            "mined_t": f(r(cl.mined_t, 1), "t"),
        }
    for ex in sim.excavators:
        x, y, _ = NODES[ex.node][0]
        out[ex.id] = {
            "state": f(ex.state),
            "x": f(x, "m"), "y": f(y, "m"),
            "tph": f(r(ex.rate.v, 1), "t/h"),
            "tonnes_total": f(r(ex.tonnes, 1), "t"),
            "passes_total": f(ex.passes, "passes"),
            "queue_len": f(len(ex.queue), "trucks"),
            "current_truck": f(ex.current.id if ex.current else ""),
            "face_grade": f(r(ex.cluster.face_grade, 3), "% Cu"),
            "face_bwi": f(r(ex.cluster.face_bwi, 2), "kWh/t"),
            "utilization_pct": f(r(100 * ex.util.v, 1), "%"),
        }
    for tr in sim.trucks:
        out[tr.id] = {
            "state": f(truck_state(tr)),
            "x": f(r(tr.x, 1), "m"), "y": f(r(tr.y, 1), "m"),
            "segment": f(tr.seg.id if tr.seg is not None else ""),
            "node": f(tr.node if tr.seg is None else ""),
            "speed_kmh": f(r(tr.speed, 1), "km/h"),
            "payload_t": f(r(tr.payload, 1), "t"),
            "load_grade": f(r(tr.grade, 3), "% Cu"),
            "material": f(tr.material),
            "destination": f(tr.dest[1] if tr.dest else ""),
            "assigned_to": f(tr.dest[1] if tr.dest and tr.dest[0] == "excavator" else ""),
            "source": f(tr.source if tr.payload > 0 else ""),
            "fuel_l": f(r(tr.fuel, 1), "L"),
            "fuel_pct": f(r(100 * tr.fuel / P.TRUCK["tank_l"], 1), "%"),
            "trips_total": f(tr.trips, "trips"),
            "tonnes_total": f(r(tr.tonnes, 1), "t"),
            "fuel_burned_l": f(r(tr.fuel_burned, 1), "L"),
            "last_queue_min": f(r(tr.last_wait / 60.0, 2), "min"),
            "waiting_reason": f(tr.why),
        }
    for sid, seg in sim.net.segments.items():
        out[f"road-{sid}"] = {
            "status": f("closed" if seg.closed else "open"),
            "trucks_on": f(len(seg.occupants), "trucks"),
            "passes_total": f(seg.passes, "passes"),
            "mean_speed_kmh": f(r(seg.speed_sum / seg.passes if seg.passes else 0.0, 1), "km/h"),
        }
    out["crusher-01"] = {
        "state": f(c.state),
        "throughput_tph": f(r(c.rate_tph, 1), "t/h"),
        "throughput_avg_tph": f(r(k["tph_crushed"], 1), "t/h"),
        "capacity_tph": f(r(c.cap_tph, 1), "t/h"),
        "pocket_t": f(r(c.pocket.t, 1), "t"),
        "feed_bwi": f(r(c.pocket.bwi, 2), "kWh/t"),
        "power_kw": f(r(c.power_kw, 1), "kW"),
        "kwh_t": f(r(k["crusher_kwh_t"], 4), "kWh/t"),
        "queue_len": f(len(c.queue) + (1 if c.tipping else 0), "trucks"),
        "tonnes_total": f(r(c.tonnes, 1), "t"),
        "energy_kwh": f(r(c.kwh, 1), "kWh"),
    }
    out["stockpile-rom"] = {
        "stock_t": f(r(sim.rom.t, 1), "t"),
        "grade": f(r(sim.rom.grade, 3), "% Cu"),
        "bwi": f(r(sim.rom.bwi, 2), "kWh/t"),
        "fill_pct": f(r(100 * sim.rom.t / sim.rom.capacity, 2), "%"),
        "inflow_tph": f(r(sim.k["rom_in"].v, 1), "t/h"),
        "reclaim_tph": f(r(sim.k["rom_out"].v, 1), "t/h"),
        "loader_state": f("reclaiming" if sim.loader_tph > 0 else "standby"),
        "tonnes_in_total": f(r(sim.rom_in_t, 1), "t"),
        "tonnes_out_total": f(r(sim.rom_out_t, 1), "t"),
        "queue_len": f(len(sim.rom_pad.queue) + len(sim.rom_pad.active), "trucks"),
    }
    out["stockpile-cos"] = {
        "stock_t": f(r(sim.cos.t, 1), "t"),
        "grade": f(r(sim.cos.grade, 3), "% Cu"),
        "bwi": f(r(sim.cos.bwi, 2), "kWh/t"),
        "fill_pct": f(r(100 * sim.cos.t / sim.cos.capacity, 2), "%"),
        "hours_of_feed": f(r(sim.cos.t / P.PLANT["target_tph"], 2), "h"),
    }
    g = sim.plant_grade
    status = "starved" if sim.plant_tph < 1 else ("low" if g < P.PLANT["grade_min"] else
                                                   ("high" if g > P.PLANT["grade_max"] else "in_spec"))
    out["plant-feed"] = {
        "feed_tph": f(r(sim.plant_tph, 1), "t/h"),
        "feed_grade": f(r(g, 4), "% Cu"),
        "feed_tph_avg": f(r(k["plant_feed_tph"], 1), "t/h"),
        "feed_grade_avg": f(r(k["plant_feed_grade"], 4), "% Cu"),
        "target_tph": f(P.PLANT["target_tph"], "t/h"),
        "grade_min": f(P.PLANT["grade_min"], "% Cu"),
        "grade_max": f(P.PLANT["grade_max"], "% Cu"),
        "grade_status": f(status),
        "power_kw": f(r(sim.plant_kw, 1), "kW"),
        "tonnes_total": f(r(sim.plant_t, 1), "t"),
        "copper_t": f(r(sim.plant_cu_t, 2), "t"),
    }
    out["waste-dump"] = {
        "tonnes_total": f(r(sim.waste_t, 1), "t"),
        "tph": f(r(sim.k["waste"].v, 1), "t/h"),
        "trucks_tipping": f(len(sim.dump.active), "trucks"),
        "queue_len": f(len(sim.dump.queue), "trucks"),
    }
    d = sim.dispatcher
    alloc = {ex.id: 0 for ex in sim.excavators}
    for t in sim.trucks:
        if t.dest and t.dest[0] == "excavator":
            alloc[t.dest[1]] += 1
        elif t.source in alloc and t.payload > 0:
            alloc[t.source] += 1
    out["dispatcher"] = {
        "decisions_total": f(d.decisions, "decisions"),
        "last_decision": f(d.last),
        "reroutes_total": f(d.reroutes, "reroutes"),
        "ore_to_rom_total": f(d.to_rom, "loads"),
        **{f"trucks_{e}": f(n, "trucks") for e, n in alloc.items()},
        **{f"assigned_tph_{e}": f(r(d.assigned[e].v, 1), "t/h") for e in alloc},
    }
    return out


def truck_state(tr):
    kind = tr.dest[0] if tr.dest else ""
    if tr.state == "travel":
        if tr.why:
            return "waiting_road"
        return "hauling" if tr.payload > 0 else ("to_fuel_bay" if kind == "fuel" else "returning")
    if tr.state == "queued":
        return {"excavator": "queued_at_shovel", "crusher": "queued_at_crusher", "rom": "queued_at_rom",
                "dump": "queued_at_dump", "fuel": "queued_at_fuel_bay"}.get(kind, "queued")
    if tr.state == "tipping":
        return {"crusher": "dumping_crusher", "rom": "dumping_rom", "dump": "dumping_waste"}.get(kind, "dumping")
    return tr.state


def ditto_message(name, features):
    """Ditto protocol command replacing every feature of one twin."""
    return {
        "topic": f"{NS}/{name}/things/twin/commands/modify",
        "path": "/features",
        "value": {k: {"properties": v} for k, v in features.items()},
    }
