"""Create or update every TwinVision twin in Ditto (idempotent PUTs).

Live twins first, then the baseline copy; parents are created before their
children. Run inside the compose network by the `twinvision-setup` service.
"""

import argparse
import json
import sys

from ..ditto import Ditto
from ..sim.engine import MineSim
from ..sim.twins import definitions


def all_definitions(seed=42):
    sim = MineSim(seed)
    out = dict(definitions(sim, baseline=False))
    out.update(definitions(sim, baseline=True))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", help="print the thing JSON instead of creating it")
    args = ap.parse_args()
    things = all_definitions()
    if args.print:
        print(json.dumps(things, indent=1))
        return 0
    ditto = Ditto()
    for thing_id, body in things.items():
        status = ditto.put_thing(thing_id, body)
        print(f"{'created' if status == 201 else 'updated'}  {thing_id}", flush=True)
    print(f"{len(things)} TwinVision twins ready in {ditto.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
