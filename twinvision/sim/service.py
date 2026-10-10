"""TwinVision simulator service.

Runs the live and the baseline copy of the mine in lockstep and publishes
every twin as a Ditto protocol `modify /features` command on MQTT
`telemetry/<thing-name>`. What-if scenarios come from the
`scenario-controller` twin in Ditto: the service listens to its change
events (republished by Ditto on MQTT `opentwins/...`) and re-reads it over
the Ditto REST API, so scenario state lives in the platform, not here.
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

from ..ditto import Ditto
from . import params as P
from .engine import MineSim
from .scenarios import normalise
from .twins import BASELINE, ditto_message, is_slow, snapshot

log = logging.getLogger("twinvision.sim")
SCENARIO_THING = f"{P.NAMESPACE}:scenario-controller"
SCENARIO_TOPIC = f"opentwins/twin/events/{P.NAMESPACE}/scenario-controller"
HEARTBEAT = "/tmp/twinvision-sim.alive"


def env(name, default):
    return os.environ.get(name, default)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt-host", default=env("MQTT_HOST", "localhost"))
    ap.add_argument("--mqtt-port", type=int, default=int(env("MQTT_PORT", "1883")))
    ap.add_argument("--speedup", type=float, default=float(env("SIM_SPEEDUP", "20")),
                    help="simulated seconds per real second")
    ap.add_argument("--interval", type=float, default=float(env("SIM_INTERVAL", "2")),
                    help="real seconds between publishes of the fast twins")
    ap.add_argument("--slow-every", type=int, default=int(env("SIM_SLOW_EVERY", "5")),
                    help="publish roads, blocks and pit every N ticks")
    ap.add_argument("--dt", type=float, default=float(env("SIM_DT", "1.0")), help="simulation step, s")
    ap.add_argument("--seed", type=int, default=int(env("SIM_SEED", "42")))
    ap.add_argument("--no-baseline", action="store_true", default=env("SIM_BASELINE", "1") == "0")
    ap.add_argument("--no-scenarios", action="store_true", help="do not read the scenario controller")
    ap.add_argument("--dry-run", action="store_true", help="print messages instead of publishing")
    ap.add_argument("--ticks", type=int, default=0, help="stop after N ticks (0 = forever)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sims = [MineSim(args.seed, "")]
    if not args.no_baseline:
        sims.append(MineSim(args.seed, BASELINE))
    live = sims[0]

    ditto = None if (args.dry_run or args.no_scenarios) else Ditto()
    dirty = threading.Event()
    dirty.set()

    client = None
    if not args.dry_run:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"twinvision-sim-{os.getpid()}")

        def on_connect(c, userdata, flags, reason, props):
            log.info("MQTT connected (%s)", reason)
            if ditto:
                c.subscribe(SCENARIO_TOPIC, qos=1)
                dirty.set()

        def on_message(c, userdata, msg):
            dirty.set()

        client.on_connect = on_connect
        client.on_message = on_message
        for _ in range(60):
            try:
                client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
                break
            except OSError as exc:
                log.warning("MQTT %s:%s not reachable (%s), retrying", args.mqtt_host, args.mqtt_port, exc)
                time.sleep(2)
        else:
            log.error("giving up on MQTT")
            return 1
        client.loop_start()

    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    steps = max(1, round(args.speedup * args.interval / args.dt))
    log.info("speedup %.0fx, %d steps of %.1fs per %.1fs tick, copies: %s", args.speedup, steps, args.dt,
             args.interval, ", ".join(s.suffix or "live" for s in sims))
    tick = 0
    last_sync = 0.0
    next_t = time.monotonic()
    while not stop:
        if ditto and (dirty.is_set() or time.monotonic() - last_sync > 30):
            dirty.clear()
            try:
                feats = ditto.request("GET", f"/api/2/things/{SCENARIO_THING}/features", timeout=10)[1]
                sc = normalise(feats)
                if sc != live.scenarios:
                    log.info("scenarios: %s", json.dumps(sc) if sc else "none")
                live.set_scenarios(sc)
                last_sync = time.monotonic()
            except Exception as exc:  # keep running on the last known scenarios
                log.warning("could not read scenario controller: %s", exc)
                last_sync = time.monotonic() - 25
        for _ in range(steps):
            for s in sims:
                s.step(args.dt)
        slow = tick % args.slow_every == 0
        for s in sims:
            for name, feats in snapshot(s).items():
                if is_slow(name) and not slow:
                    continue
                thing = name + s.suffix
                payload = json.dumps(ditto_message(thing, feats), separators=(",", ":"))
                if client:
                    client.publish(f"telemetry/{thing}", payload, qos=0)
                elif args.ticks:
                    pass
                else:
                    print(payload)
        tick += 1
        with open(HEARTBEAT, "w") as fh:
            fh.write(str(time.time()))
        if tick % 30 == 0:
            k = live.kpis()
            log.info("t=%.1fh mined %.0f t/h, plant %.0f t/h @ %.3f%% Cu, queue %.1f min, COS %.0f t, "
                     "mass residual %.2e t, scenarios %s", live.t / 3600, k["tph_mined"], k["plant_feed_tph"],
                     k["plant_feed_grade"], k["mean_queue_min"], live.cos.t, live.mass_balance(),
                     ",".join(live.scenarios) or "none")
        if args.ticks and tick >= args.ticks:
            break
        next_t += args.interval
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_t = time.monotonic()

    if client:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
