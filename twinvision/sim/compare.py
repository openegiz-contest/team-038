"""Live vs baseline comparison of every what-if scenario and module switch, headless.

    python -m twinvision.sim.compare [--warmup-h 2] [--hours 4] [--seed 42]

Both copies run the same seed for a warm-up, then the scenario or module switch
is applied to the live copy only and both run on. The table gives totals over
that window (tonnes, copper, diesel, energy, failures) and window averages of
the sampled KPIs (queue time, availability), as the stand shows them.
"""

import argparse

from twinvision.sim.engine import MineSim
from twinvision.sim.scenarios import normalise

# Scenario-controller properties, parsed by the same normalise() as on the stack.
CASES = [
    ("crusher failure", {"crusher_failure": {}}),
    ("road closure s04", {"road_closure": {"segments": ["s04"]}}),
    ("excavator breakdown ex-01", {"excavator_breakdown": {"excavators": ["ex-01"]}}),
    ("hardness spike x1.4 north", {"hardness_spike": {"factor": 1.4, "clusters": ["blocks-north"]}}),
    ("low-grade zone x0.6 north", {"low_grade_zone": {"factor": 0.6, "clusters": ["blocks-north"]}}),
    ("fuel shortage", {"fuel_shortage": {"resupply_lph": 0, "bay_stock_l": 8000}}),
    ("grid cap 28 MW", {"energy_cap": {"cap_kw": 28000}}),
    ("dispatcher off", {"dispatcher_module": {"enabled": False}}),
    ("maintenance off", {"maintenance_module": {"enabled": False}}),
    ("grid cap 28 MW, energy module off", {"energy_cap": {"cap_kw": 28000}, "energy_module": {"enabled": False}}),
]


def controller(case):
    feats = {}
    for name, props in case.items():
        props = dict(props) if name.endswith("_module") else {"active": True, **props}
        feats[name] = {"properties": props}
    return normalise(feats)


def totals(sim):
    units = [sim.crusher, *sim.excavators, *sim.trucks]
    return {
        "mined_t": sum(ex.tonnes for ex in sim.excavators),
        "crushed_t": sim.crusher.tonnes,
        "plant_t": sim.plant_t,
        "cu_in_feed_t": sim.plant_cu_t,
        "copper_t": sim.copper_t,
        "diesel_l": sum(t.fuel_burned for t in sim.trucks) + sim.loader_fuel,
        "site_kwh": sim.site_kwh,
        "failures": sum(u.health.failures for u in units),
    }


def window(sim, hours, sample_s=60):
    start, q, av, n = totals(sim), 0.0, 0.0, 0
    for i in range(int(hours * 3600)):
        sim.step(1.0)
        if i % sample_s == 0:
            k = sim.kpis()
            q, av, n = q + k["mean_queue_min"], av + k["availability_pct"], n + 1
    end = totals(sim)
    d = {key: end[key] - start[key] for key in end}
    return {
        "plant_t": d["plant_t"],
        "feed_grade": 100 * d["cu_in_feed_t"] / d["plant_t"] if d["plant_t"] else 0.0,
        "copper_t": d["copper_t"],
        "mined_t": d["mined_t"],
        "queue_min": q / n,
        "avail_pct": av / n,
        "kwh_t": d["site_kwh"] / d["plant_t"] if d["plant_t"] > 100 else 0.0,
        "diesel_l_t": d["diesel_l"] / d["mined_t"] if d["mined_t"] else 0.0,
        "failures": d["failures"],
        "violations": sim.energy.violations,
    }


COLUMNS = [("plant_t", "plant feed t", 0), ("feed_grade", "grade %Cu", 3), ("copper_t", "copper t", 1),
           ("mined_t", "mined t", 0), ("queue_min", "queue min", 2), ("avail_pct", "avail %", 1),
           ("kwh_t", "kWh/t", 2), ("diesel_l_t", "diesel L/t", 3), ("failures", "failures", 0)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--warmup-h", type=float, default=2.0)
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    print(f"seed {a.seed}, warm-up {a.warmup_h} h, window {a.hours} h of simulated time; "
          "each cell: live / baseline")
    print("| case | " + " | ".join(c[1] for c in COLUMNS) + " |")
    print("|---" * (len(COLUMNS) + 1) + "|")
    for label, scen in CASES:
        live, base = MineSim(a.seed), MineSim(a.seed, "-baseline")
        for _ in range(int(a.warmup_h * 3600)):
            live.step(1.0)
            base.step(1.0)
        live.set_scenarios(controller(scen))
        lw, bw = window(live, a.hours), window(base, a.hours)
        cells = [f"{lw[k]:,.{dp}f} / {bw[k]:,.{dp}f}" for k, _, dp in COLUMNS]
        extra = f" ({lw['violations']} cap violations)" if lw["violations"] else ""
        print(f"| {label}{extra} | " + " | ".join(cells) + " |", flush=True)


if __name__ == "__main__":
    main()
