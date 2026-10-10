"""List the TwinVision twins in Ditto and check them against twinvision/SCHEMA.md.

    python -m twinvision.setup.check_twins          (exit code 1 on any mismatch)

Checks: count per type and copy, parent of every twin, that relation
attributes (digs, excavator, feeds, controls, maintains, supplies,
live_twin, baseline_twin) point to existing twins, and that every
truck publishes its relation features (assigned_to, segment, destination).
"""

import os
import re
import sys
import urllib.parse
from collections import Counter, defaultdict

from twinvision.ditto import Ditto

NS = "org.twinvision.copper"
BASE = "-baseline"
SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SCHEMA.md")
RELATIONS = ("digs", "excavator", "feeds", "controls", "maintains", "supplies", "live_twin", "baseline_twin")
TRUCK_RELATION_FEATURES = ("assigned_to", "segment", "node", "destination", "source")


def inventory():
    """{type: (live, baseline, parent)} from the 'Twin inventory' table of SCHEMA.md."""
    text = open(SCHEMA, encoding="utf-8").read()
    section = text.split("## Twin inventory", 1)[1].split("\n## ", 1)[0]
    out = {}
    for line in section.splitlines():
        m = re.match(r"\|\s*([A-Za-z]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            parent = m.group(4)
            out[m.group(1)] = (int(m.group(2)), int(m.group(3)), None if parent in ("–", "-", "") else parent)
    return out


def fetch(ditto):
    things, cursor = {}, None
    while True:
        q = {"filter": f'like(thingId,"{NS}:*")', "fields": "thingId,attributes,features",
             "option": "size(200)" + (f",cursor({cursor})" if cursor else "")}
        _, page = ditto.retry("GET", "/api/2/search/things?" + urllib.parse.urlencode(q), attempts=10)
        for item in page.get("items", []):
            things[item["thingId"]] = item
        cursor = page.get("cursor")
        if not cursor:
            return things


def main():
    ditto = Ditto()
    things = fetch(ditto)
    expected = inventory()
    errors = []

    children = defaultdict(list)
    for tid, t in things.items():
        children[t.get("attributes", {}).get("_parents")].append(tid)

    def short(tid):
        return tid.split(":", 1)[1]

    def tree(tid, depth=0):
        a = things[tid].get("attributes", {})
        print(f"{'  ' * depth}{short(tid):<26} {a.get('type', '?')}")
        for c in sorted(children.get(tid, [])):
            tree(c, depth + 1)

    for root in sorted(children.get(None, [])):
        tree(root)
        print()

    counts = Counter()
    for tid, t in things.items():
        a = t.get("attributes", {})
        copy = "baseline" if tid.endswith(BASE) else "live"
        typ = a.get("type", "?")
        counts[(typ, copy)] += 1
        if typ not in expected:
            errors.append(f"{short(tid)}: type {typ} not in SCHEMA.md")
            continue
        parent = expected[typ][2]
        want = None if parent is None else f"{NS}:{parent}{BASE if copy == 'baseline' else ''}"
        if a.get("_parents") != want:
            errors.append(f"{short(tid)}: parent {a.get('_parents')} != {want}")
        for rel in RELATIONS:
            vals = a.get(rel)
            for v in ([vals] if isinstance(vals, str) else vals or []):
                if isinstance(v, str) and v.startswith(f"{NS}:") and v not in things:
                    errors.append(f"{short(tid)}: {rel} -> {v} does not exist")
        if typ == "HaulTruck":
            missing = [f for f in TRUCK_RELATION_FEATURES if f not in (t.get("features") or {})]
            if missing:
                errors.append(f"{short(tid)}: missing relation features {missing}")

    print(f"{'Type':<22} {'live':>5} {'base':>5}   SCHEMA.md")
    for typ, (n_live, n_base, _) in expected.items():
        got = (counts[(typ, "live")], counts[(typ, "baseline")])
        ok = got == (n_live, n_base)
        print(f"{typ:<22} {got[0]:>5} {got[1]:>5}   {n_live:>3} {n_base:>3}  {'ok' if ok else 'MISMATCH'}")
        if not ok:
            errors.append(f"{typ}: {got} twins, SCHEMA.md says {(n_live, n_base)}")
    total = sum(n + b for n, b, _ in expected.values())
    print(f"{'Total':<22} {len(things):>11}   {total:>7}")

    if errors:
        print("\n" + "\n".join(f"FAIL {e}" for e in errors))
        sys.exit(1)
    print("\nAll twins match SCHEMA.md.")


if __name__ == "__main__":
    main()
