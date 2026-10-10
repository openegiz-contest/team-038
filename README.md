# TwinVision Copper Pit: a digital open-pit copper mine on OpenEgiz

Team Repository of team TwinVision for the contest "Digital Mine on OpenEgiz". A fictional open-pit copper mine in Kazakhstan (pit, three excavators, eight 227 t haul trucks, a road graph of 12 segments, primary crusher, ROM and coarse ore stockpiles, concentrator feed, waste dump) is represented by 69 Eclipse Ditto twins: a **live** copy that reacts to what-if scenarios and a **baseline** copy that runs the same simulation with the same random seed and no scenario, so the consequences of every what-if can be read as live-minus-baseline.

Data path: simulator → MQTT `telemetry/<twin>` (Ditto protocol) → Eclipse Ditto → MQTT `opentwins/...` → Telegraf → InfluxDB → Grafana. Details: [twinvision/SCHEMA.md](twinvision/SCHEMA.md), [SUBMISSION.md](SUBMISSION.md).

## Running (clean Ubuntu 24.04 with Docker Engine and Compose v2)

```bash
sudo apt-get update && sudo apt-get install -y make git
git clone https://github.com/openegiz-contest/team-038.git
cd team-038
make twinvision
```

`make twinvision` starts the OpenEgiz platform (`make up`), builds the TwinVision image, creates the twins in Ditto, starts the simulator and provisions the Grafana dashboards. The first run generates random credentials into `deploy/compose/.env`, downloads about 2.3 GB of images and builds two images from source; expect 10–20 minutes depending on the connection (about 2 minutes on later starts). When it finishes it prints the URLs and logins again; `make twinvision-urls` prints them at any time.

Check that everything works end to end:

```bash
make twinvision-smoke   # twins in Ditto, live + baseline telemetry in InfluxDB, mass balance, dashboards
make smoke              # the platform's own end-to-end check
make twinvision-test    # unit tests in the image: mass/fuel balance, determinism, scenario propagation
```

## What to open

All ports are bound to `127.0.0.1`. The passwords are generated on the first run and stored in `deploy/compose/.env` (`GRAFANA_ADMIN_PASSWORD`, `DITTO_PASSWORD`).

| What | Where |
|---|---|
| Grafana dashboard "TwinVision Copper Pit - live vs baseline" | http://localhost:3000/d/twinvision-overview (user `admin`) |
| Twin hierarchy | Grafana → OpenEgiz app → Twins |
| Ditto REST API | http://localhost:8080/api/2/things/org.twinvision.copper:mine (user `ditto`) |

## Commands

| Command | What it does |
|---|---|
| `make twinvision` | Platform + TwinVision twins, simulator and dashboards; from then on `make up` / `make down` include TwinVision |
| `make twinvision-urls` | Print the URLs and logins |
| `make twinvision-smoke` | End-to-end check of the TwinVision services |
| `make twinvision-test` | Unit tests inside the image |
| `make twinvision-stop` | Stop the simulator (twins and data stay) |
| `make down` / `make up` | Stop / start everything, keeping data |
| `make clean` | Stop and delete all data and credentials |

Simulation speed: 20 simulated seconds per real second by default; set `TWINVISION_SPEEDUP` in `deploy/compose/.env` and run `make up` to change it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make: command not found` | `sudo apt-get install -y make` |
| `permission denied ... docker.sock` | the user must be in the `docker` group (`sudo usermod -aG docker $USER`, then log in again) |
| `make twinvision` fails on the init job or a Ditto service | Ditto was still starting on a busy host; run `make twinvision` again |
| A port is already in use | change it in `deploy/compose/.env` and run `make twinvision` again |
| Anything else | `make ps`, `make logs S=twinvision-sim`; `make clean && make twinvision` starts from scratch |

## Repository contents

| Path | Contents |
|---|---|
| `twinvision/` | TwinVision: simulator, dispatcher, twin setup, dashboards, tests, compose overlay |
| `twinvision/SCHEMA.md` | Twin types, features, relations |
| `twinvision/DECISIONS.md` | Design decisions |
| `SUBMISSION.md` | Description of the Submission |
| `OPENEGIZ.md`, `deploy/`, `scripts/`, … | The OpenEgiz platform v1.0.4 (unchanged apart from Makefile targets) |
| `examples/mine/` | The platform's Example Mine (not used by TwinVision) |

## License

MIT, see [LICENSE](LICENSE). Platform: [aleka07/openegiz](https://github.com/aleka07/openegiz).
