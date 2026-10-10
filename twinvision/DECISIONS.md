# Design decisions

Short record of choices made without a blocking question, newest last.

1. **Overlay, not core changes.** TwinVision joins the platform as `twinvision/compose.yml`, an overlay on `deploy/compose/docker-compose.yml`, with a marker file `deploy/compose/.twinvision`, exactly like the Example Mine. The only core edits are Makefile targets and one `.gitignore` line.
2. **One image for all Python services** (`twinvision/app:local`, `python:3.12-slim`, multi-arch), built by `twinvision-setup` only; other services use `pull_policy: never`, as the Example Mine explains.
3. **Grafana dashboards** are mounted over the platform's empty `/var/lib/grafana/dashboards` directory (Compose merges volumes by target), so they appear under the existing `openegiz` provider in a `TwinVision` folder without editing core provisioning files.
4. **Namespace** `org.twinvision.copper`; baseline twins use the suffix `-baseline` and their own root `mine-baseline`.
5. **Simulation clock**: fixed 1 s steps, 20x speed-up by default (`TWINVISION_SPEEDUP`), publish every 2 s real time. Roads, block clusters and the pit (slowly changing) are published every 5th tick. About 69 twins → roughly 20 Ditto updates per second for both copies.
6. **Common random numbers**: each entity (truck, excavator, block cluster) has its own seeded stream (`random.Random(f"{seed}:{kind}:{id}")`), so live and baseline only diverge through the consequences of a scenario.
7. **Fleet size 8 trucks** (Cat 793F class): with 9, all three shovels ran above their target rates and ore far exceeded plant demand. 8 gives a strip ratio of about 1.9 and a modest ore surplus to the ROM pad.
8. **Coarse ore stockpile live capacity 6,000 t (~3 h of plant feed)** is smaller than at real concentrators (often 12–24 h) so that a crusher failure visibly starves the plant within minutes of real time at 20x. Documented as a synthetic parameter.
9. **Scenario path through the platform**: the web API writes the `scenario-controller` thing in Ditto; Ditto republishes the change on MQTT `opentwins/twin/events/...`; the simulator subscribes to that topic and re-reads the thing over REST (plus a 30 s periodic re-sync and a read at start-up), so the scenario state survives a simulator restart.
10. **KPIs are 30-minute exponential averages** (simulated time), computed identically in both copies, so live-minus-baseline deltas are like-for-like.
