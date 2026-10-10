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

Mass and diesel balances are audited every simulation step.

Data path: simulator → MQTT `telemetry/<twin>` (Ditto protocol) → Eclipse Ditto → MQTT `opentwins/...` → Telegraf → InfluxDB → Grafana. The map UI reads the twins from the Ditto API and trends from InfluxDB; what-if buttons write the `scenario-controller` twin in Ditto, which the simulator follows. Details: [twinvision/SCHEMA.md](twinvision/SCHEMA.md), [SUBMISSION.md](SUBMISSION.md).

## Running (clean Ubuntu 24.04 with Docker Engine and Compose v2)

```bash
sudo apt-get update && sudo apt-get install -y make git
git clone https://github.com/openegiz-contest/team-038.git
cd team-038
make twinvision
```

`make twinvision` does the following:

1. Starts the OpenEgiz platform (`make up`).
2. Builds the TwinVision image.
3. Creates the twins in Ditto.
4. Starts the simulator and the map UI.
5. Provisions the Grafana dashboards.

The first run generates random credentials into `deploy/compose/.env`, downloads about 2.3 GB of images and builds two images from source. Expect 10–20 minutes depending on the connection; later starts take about 2 minutes. When it finishes it prints the URLs and logins again; `make twinvision-urls` prints them at any time.

Check that everything works end to end:

```bash
make twinvision-smoke   # twins, live + baseline telemetry in InfluxDB, balances, dashboards, map UI, what-if + undo
make smoke              # the platform's own end-to-end check
make twinvision-test    # unit tests in the image: balances, determinism, scenario propagation, decisions, web API
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
curl -X POST localhost:8095/api/scenarios/reset               # undo all
```

The scenario state lives in the `org.twinvision.copper:scenario-controller` twin, so it can also be changed with any Ditto client. KPIs are 30-minute moving averages in simulated time, which is about 90 s of real time at the default speed.

## Commands

| Command | What it does |
|---|---|
| `make twinvision` | Platform + TwinVision twins, simulator, map UI and dashboards; from then on `make up` / `make down` include TwinVision |
| `make twinvision-urls` | Print the URLs and logins |
| `make twinvision-smoke` | End-to-end check of the TwinVision services |
| `make twinvision-test` | Unit tests inside the image |
| `make twinvision-stop` | Stop the simulator (twins and data stay) |
| `make down` / `make up` | Stop / start everything, keeping data |
| `make clean` | Stop and delete all data and credentials |

Simulation speed: 20 simulated seconds per real second by default. To change it, set `TWINVISION_SPEEDUP` in `deploy/compose/.env` and run `make up`. The map port is `TWINVISION_PORT` (default 8095).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make: command not found` | `sudo apt-get install -y make` |
| `permission denied ... docker.sock` | the user must be in the `docker` group (`sudo usermod -aG docker $USER`, then log in again) |
| `make twinvision` fails on the init job or a Ditto service | Ditto was still starting on a busy host; run `make twinvision` again |
| A port is already in use | change it in `deploy/compose/.env` and run `make twinvision` again |
| The map says "waiting for twins" | the setup job has not finished; `make logs S=twinvision-setup` |
| Anything else | `make ps`, `make logs S=twinvision-sim` (or `twinvision-web`); `make clean && make twinvision` starts from scratch |

## Repository contents

| Path | Contents |
|---|---|
| `twinvision/` | TwinVision: simulator, dispatcher, maintenance planner, twin setup, map UI and control API, dashboards, tests, compose overlay |
| `twinvision/SCHEMA.md` | Twin types, features, relations |
| `twinvision/DECISIONS.md` | Design decisions and model assumptions |
| `twinvision/data/geomet/` | Open GeoMet dataset files (CC BY 4.0) used for grades and hardness |
| `SUBMISSION.md` | Description of the submission |
| `DATA_LICENSES.md` | Data sources, licences and synthetic parameters |
| `OPENEGIZ.md`, `deploy/`, `scripts/`, … | The OpenEgiz platform v1.0.4 (unchanged apart from Makefile targets) |
| `examples/mine/` | The platform's Example Mine (not used by TwinVision) |

## License

MIT, see [LICENSE](LICENSE). Platform: [aleka07/openegiz](https://github.com/aleka07/openegiz). Data: see [DATA_LICENSES.md](DATA_LICENSES.md).
