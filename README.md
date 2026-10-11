# TwinVision Copper Pit: a digital open-pit copper mine on OpenEgiz

Team repository of team TwinVision for the contest "Digital Mine on OpenEgiz". A fictional open-pit copper mine in Kazakhstan is represented by 75 Eclipse Ditto twins:

- **Equipment.** A pit with three block clusters, three excavators, eight 227 t haul trucks and a road graph of 12 segments.
- **Processing.** A primary crusher, ROM and coarse ore stockpiles, the concentrator feed and a waste dump.
- **Control and support.** A truck dispatcher, a maintenance planner, a fuel bay, the site power supply and a scenario controller.

There are two copies of the mine. The **live** copy reacts to what-if scenarios. The **baseline** copy runs the same simulation with the same random seed and no scenario, so the consequences of every what-if can be read as live minus baseline.

The three operating modules are linked through the twins:

- **Haulage and dispatch:** truck allocation, ore routing and fuel rationing.
- **Ore quality and blending:** a block model with grades and Bond work indices drawn from the open GeoMet dataset, and a feed grade band.
- **Maintenance and energy:** wear-dependent failures, condition-based maintenance decisions, crusher and concentrator power from throughput and hardness, diesel, and grid peak demand.

Each module makes logged decisions and has an on/off switch, so its value can be seen as live (module off) against baseline (module on). Mass, diesel and energy balances are audited every simulation step.

Data path: simulator → MQTT `telemetry/<twin>` (Ditto protocol) → Eclipse Ditto → MQTT `opentwins/...` → Telegraf → InfluxDB → Grafana. The map UI reads the twins from the Ditto API and trends from InfluxDB; what-if buttons write the `scenario-controller` twin in Ditto, which the simulator follows. Details: [twinvision/SCHEMA.md](twinvision/SCHEMA.md), [SUBMISSION.md](SUBMISSION.md).

## Running (clean Ubuntu 24.04 with Docker Engine and Compose v2)

Only `bash`, `git` and Docker are needed; `make` is optional.

```bash
git clone https://github.com/openegiz-contest/team-038.git
cd team-038
./twinvision.sh up
```

`./twinvision.sh up` (same as `make twinvision`) does the following:

1. Starts the OpenEgiz platform (same as `make up`).
2. Builds the TwinVision image.
3. Creates the twins in Ditto.
4. Starts the simulator and the map UI.
5. Provisions the Grafana dashboards.

The first run generates random credentials into `deploy/compose/.env`, downloads about 2.3 GB of images and builds two images from source. Expect 10–20 minutes depending on the connection; later starts take about 2 minutes. When it finishes it prints the URLs and logins again; `./twinvision.sh urls` prints them at any time. The map shows moving trucks as soon as the command returns.

Check that everything works end to end (about 2 minutes, 1 minute and 1 minute):

```bash
./twinvision.sh smoke            # make twinvision-smoke: twins vs SCHEMA.md, live + baseline telemetry in InfluxDB,
                                 #   mass/fuel/energy balances, dashboards, map UI, what-if KPI delta vs baseline,
                                 #   undo and recovery, module switch and undo
./twinvision.sh platform-smoke   # make smoke: the platform's own end-to-end check
./twinvision.sh test             # make twinvision-test: unit tests in the image (balances, determinism,
                                 #   scenario propagation, decisions, module switches, web API)
./twinvision.sh twins            # make twinvision-twins: print the twin hierarchy from Ditto, check it against SCHEMA.md
./twinvision.sh compare --hours 24   # headless: live vs baseline totals for every scenario and module switch
```

## What to open

All ports are bound to `127.0.0.1`. The passwords are generated on the first run and stored in `deploy/compose/.env` (`GRAFANA_ADMIN_PASSWORD`, `DITTO_PASSWORD`). The map UI needs no login.

| What | Where |
|---|---|
| **Live mine map** with what-if buttons and live vs baseline KPIs | http://localhost:8095/ |
| Control API (same actions as the buttons, with interactive docs) | http://localhost:8095/docs |
| Grafana dashboard "TwinVision Copper Pit - live vs baseline" | http://localhost:3000/d/twinvision-overview (user `admin`) |
| Twin hierarchy | Grafana → OpenEgiz app → Twins |
| Ditto REST API | http://localhost:8080/api/2/things/org.twinvision.copper:mine (user `ditto`) |

### The map

The map shows the following, refreshed every second from Ditto:

- **Pit and blocks.** The pit benches, and the block clusters coloured by copper grade, with dug blocks faded and the current block outlined.
- **Roads.** Closed roads are drawn red.
- **Equipment.**
  - Excavators are coloured by state, with a health ring.
  - Trucks are coloured by state, and the inner dot of a loaded truck shows its load grade.
  - The baseline trucks can be shown as dashed ghosts.
- **Facilities.** The crusher, the ROM and coarse ore stockpile fill levels, the concentrator with its feed grade status, the waste dump, and the fuel bay level.

Failed equipment pulses red. Below the map are stockpile, fuel, grid and grade gauges with a baseline marker, and the health of every machine. The right panel shows the following:

- The live, baseline and delta values of eleven KPIs.
- An InfluxDB trend for the selected KPI.
- The event log.

Clicking a truck, excavator, road, block cluster or facility shows the underlying Ditto thing, with live and baseline values side by side. `Mine`, `Pit` and `Plant` switch the view; the mouse wheel zooms and dragging pans.

### What-if scenarios

Each scenario has a card in the left panel with **Run** and **Undo**, plus **Undo last** and **Reset all**. The same actions are available over HTTP:

```bash
curl -X POST localhost:8095/api/scenarios/crusher_failure
curl -X POST localhost:8095/api/scenarios/road_closure        -H 'Content-Type: application/json' -d '{"segments":["s04"]}'
curl -X POST localhost:8095/api/scenarios/excavator_breakdown -H 'Content-Type: application/json' -d '{"excavators":["ex-01"]}'
curl -X POST localhost:8095/api/scenarios/hardness_spike      -H 'Content-Type: application/json' -d '{"factor":1.6,"clusters":["blocks-north"]}'
curl -X POST localhost:8095/api/scenarios/low_grade_zone      -H 'Content-Type: application/json' -d '{"factor":0.5,"clusters":["blocks-north"]}'
curl -X POST localhost:8095/api/scenarios/fuel_shortage       -H 'Content-Type: application/json' -d '{"resupply_lph":0,"bay_stock_l":8000}'
curl -X POST localhost:8095/api/scenarios/energy_cap          -H 'Content-Type: application/json' -d '{"cap_kw":28000}'
curl -X DELETE localhost:8095/api/scenarios/crusher_failure   # undo one scenario
curl -X POST localhost:8095/api/scenarios/undo                # undo the last action
curl -X POST localhost:8095/api/scenarios/reset               # all scenarios off, all modules on (one undo step)
curl -X POST localhost:8095/api/resync                        # copy the baseline state into the live copy (not undoable)
```

After a scenario is undone, the live mine keeps its history (stockpile levels, truck positions, machine wear), so it does not return exactly to the baseline. **Resync live = baseline** copies the baseline state into the live copy, so the next comparison starts from two identical mines.

The scenario state lives in the `org.twinvision.copper:scenario-controller` twin, so it can also be changed with any Ditto client. KPIs are 30-minute moving averages in simulated time, which is about 90 s of real time at the default speed.

### Decision modules

The **Modules** section of the left panel shows the decision log of each module (read from the `decision_log` feature of the `dispatcher`, `maintenance` and `power-grid` twins), with counters and an on/off switch. Switching a module off affects the live copy only, so the KPI deltas show what the module is worth:

| Module | On | Off |
|---|---|---|
| Haulage and dispatch | trucks to the shovel with the earliest expected loading start, corrected for target-rate deficit and feed grade; ore to ROM when the crusher is down or the grade is off band; fuel rationing | fixed allocation (2/2/4 trucks), all ore to the crusher, no blending, no rationing |
| Maintenance planner | condition-based planned maintenance when the expected failure cost exceeds the planned stop | run to failure |
| Energy manager | under a grid cap, throttles the concentrator to stay below it | a 15-minute block above the cap trips the concentrator for 15 minutes |

```bash
curl -X POST localhost:8095/api/modules/maintenance -H 'Content-Type: application/json' -d '{"enabled":false}'
curl -X POST localhost:8095/api/scenarios/undo      # module switches share the undo stack with the scenarios
```

## Commands

| `./twinvision.sh …` | `make …` | What it does |
|---|---|---|
| `up` | `twinvision` | Platform + TwinVision twins, simulator, map UI and dashboards; from then on `start` / `down` (`make up` / `make down`) include TwinVision |
| `urls` | `twinvision-urls` | Print the URLs and logins |
| `smoke` | `twinvision-smoke` | End-to-end check of the TwinVision services |
| `test` | `twinvision-test` | Unit tests inside the image |
| `twins` | `twinvision-twins` | Twin hierarchy from Ditto, checked against `twinvision/SCHEMA.md` |
| `compare [--hours N]` | – | Headless live vs baseline table of every scenario and module switch (same simulator code, seed 42) |
| `stop` | `twinvision-stop` | Stop the simulator (twins and data stay) |
| `down` / `start` | `down` / `up` | Stop / start everything, keeping data |
| `ps`, `logs <service>` | `ps`, `logs S=<service>` | Container status, logs |
| `clean` | `clean` | Stop and delete all data and credentials |

Simulation speed: 20 simulated seconds per real second by default. To change it, set `TWINVISION_SPEEDUP` in `deploy/compose/.env` and run `./twinvision.sh start`. The map port is `TWINVISION_PORT` (default 8095).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `permission denied ... docker.sock` | the user must be in the `docker` group (`sudo usermod -aG docker $USER`, then log in again) |
| `./twinvision.sh up` fails on the init job or a Ditto service | Ditto was still starting on a busy host; run `./twinvision.sh up` again (it continues where it stopped) |
| A port is already in use | change it in `deploy/compose/.env` and run `./twinvision.sh up` again |
| The map says "waiting for twins" | the setup job has not finished; `./twinvision.sh logs twinvision-setup` |
| Anything else | `./twinvision.sh ps`, `./twinvision.sh logs twinvision-sim` (or `twinvision-web`); `./twinvision.sh clean && ./twinvision.sh up` starts from scratch |

## Repository contents

| Path | Contents |
|---|---|
| `twinvision/` | TwinVision: simulator, dispatcher, maintenance planner, twin setup, map UI and control API, dashboards, tests, compose overlay |
| `twinvision/SCHEMA.md` | Twin types, features, relations |
| `twinvision/DECISIONS.md` | Design decisions |
| `twinvision/DATA_ASSUMPTIONS.md` | Every model parameter with its source (data sheet, formula, open data or synthetic) |
| `twinvision.sh` | The TwinVision commands for hosts without `make` |
| `twinvision/data/geomet/` | Open GeoMet dataset files (CC BY 4.0) used for grades and hardness |
| `SUBMISSION.md` | Description of the submission |
| `DATA_LICENSES.md` | Data sources, licences and synthetic parameters |
| `OPENEGIZ.md`, `deploy/`, `scripts/`, … | The OpenEgiz platform v1.0.4 (unchanged apart from the TwinVision Makefile targets) |
| `examples/mine/` | The platform's Example Mine (not used by TwinVision) |

## License

MIT, see [LICENSE](LICENSE). Platform: [aleka07/openegiz](https://github.com/aleka07/openegiz). Data: see [DATA_LICENSES.md](DATA_LICENSES.md).
