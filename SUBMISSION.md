# SUBMISSION

## Concept

**Enterprise:** The pit-to-concentrator chain of an open-pit copper mine: three faces, the shovels and 227 t trucks that serve them, the haul-road network, the primary crusher, the ROM pad, the coarse ore stockpile, the concentrator feed, the waste dump, the diesel fuel bay and the site power supply. The flotation circuit itself is not modelled; the concentrator is the feed it receives and the copper it recovers.

**Users:** The shift dispatcher, the maintenance planner and the site energy engineer, looking at one control-room view.

**Decision:** Whether to accept an operating change, or to leave the mine as it is. A change is a failed crusher, a closed road, a shovel out of service, harder or lower-grade ore, a fuel shortage, a grid cap, or switching off dispatch, maintenance or energy management. The twin runs a second, identical mine that does not take the change. The decision is the difference in copper, plant feed, queue time, availability, kWh/t and diesel L/t.

**Why this choice:** In this chain those quantities move together. Ore that cannot enter the crusher goes to the ROM pad, the coarse ore stockpile then drains, and the concentrator starves. A harder face slows the shovel and raises crusher kWh/t. A grid cap can be met by throttling the concentrator or by tripping it, and the two choices do not yield the same copper. The second mine uses the same seed and the same random draw for each machine, so the difference is the change, not luck.

**Real or fictional:** The site is fictional. It resembles a porphyry copper open pit with a gyratory primary crusher and a concentrator fed from a coarse ore stockpile. Truck and shovel sizes follow public class specification sheets (Caterpillar 793F, Komatsu PC5500, Caterpillar 6060, Liebherr R 9600). Copper grades, Bond work indices and recovery follow the open GeoMet dataset (Hoffimann et al. 2022, CC BY 4.0). Pit geometry, fleet size, stockpiles, the failure model and the grid cap are synthetic; every value and its source is listed in `twinvision/DATA_ASSUMPTIONS.md`.

## Team

| GitHub |
|---|
| [mrl3mon](https://github.com/mrl3mon) |

## Mine

**TwinVision Copper Pit** is a fictional open-pit copper mine in Kazakhstan, together with its primary crushing and concentrator feed. It covers the value chain from the blasted face to copper in concentrate:

- block clusters are dug by shovels;
- haul trucks carry the material over a road graph to the crusher, the ROM pad or the waste dump;
- the crusher fills the coarse ore stockpile;
- the stockpile feeds the concentrator.

Diesel comes from a fuel bay, electricity from a capped grid connection, and machines wear, fail and are maintained.

**Twins.** Every object is an Eclipse Ditto thing in the namespace `org.twinvision.copper`. There are 38 live twins and 37 baseline copies, 75 in total:

| Type | Twins | Key state and KPIs |
|---|---|---|
| Mine | `mine` | site KPIs (t/h, feed grade, copper, queue time, availability, kWh/t, diesel L/t), mass, fuel and energy balance residuals, event log |
| Pit | `pit` | bench geometry, trucks in pit, roads closed |
| BlockCluster | `blocks-north`, `blocks-east`, `blocks-south` | block model (grade, Bond work index, tonnes per block), current block, face grade and hardness |
| Excavator | `ex-01`…`ex-03` | state, t/h, queue, health, failure probability |
| HaulTruck | `truck-01`…`truck-08` | position, segment, state, payload, load grade, fuel, assigned shovel, destination, health |
| RoadSegment | `road-s01`…`road-s12` | geometry, grade, speed limit, open/closed, trucks on it |
| Crusher | `crusher-01` | state, t/h, capacity at current hardness, power, kWh/t, health |
| Stockpile | `stockpile-rom`, `stockpile-cos` | stock, grade, hardness, fill, loader reclaim |
| PlantFeed | `plant-feed` | feed t/h and grade vs band, power, limiting factor, copper |
| WasteDump | `waste-dump` | t/h, queue |
| DispatcherController | `dispatcher` | decisions, allocation per shovel, re-routes, blend diversions, fuel rationing, decision log, module switch |
| MaintenancePlanner | `maintenance` | decisions, failures, planned jobs, expected loss avoided, decision log, module switch |
| FuelBay | `fuel-bay` | stock, resupply, consumption, hours of cover, trucks out of fuel |
| PowerGrid | `power-grid` | demand, 15-minute demand, peak, cap, throttle and load-shed state, decision log, module switch |
| ScenarioController | `scenario-controller` (live only) | one feature per what-if and per module switch |

**Live and baseline copies.** The baseline copy (`<name>-baseline`) runs the same simulation with the same seed and every module on, without any what-if. Every random draw comes from a per-entity seeded stream, so both copies see the same luck. Live minus baseline is therefore the effect of the scenario or switch.

**Relations.** Parents are set in `_parents`: pit and facilities under the mine, and equipment, roads and block clusters under the pit. Physical links are attributes: `digs`, `excavator`, `feeds` (ROM → crusher → COS → plant), `controls`, `maintains`, `supplies`, `from_node`/`to_node`. Changing links are features: a truck's `assigned_to`, `destination`, `segment`, `source`.

**Code.**
- [`twinvision/SCHEMA.md`](twinvision/SCHEMA.md): types, features, state enums and relations, plus an inventory table that `./twinvision.sh twins` checks against Ditto.
- [`twinvision/sim/twins.py`](twinvision/sim/twins.py): twin definitions.
- [`twinvision/setup/setup_twins.py`](twinvision/setup/setup_twins.py): creates the twins.
- [`twinvision/sim/engine.py`](twinvision/sim/engine.py): simulation and decision modules.
- [`twinvision/web/`](twinvision/web/): map UI and control API.

**Data path.** The simulator publishes Ditto protocol messages to MQTT `telemetry/<twin>`. They reach Eclipse Ditto, then MQTT `opentwins/...`, then Telegraf, then InfluxDB and Grafana. The map UI reads the twins from the Ditto API and trends from InfluxDB, never from simulator memory. What-if buttons and module switches write the `scenario-controller` twin in Ditto; the simulator follows it through Ditto's event stream.

## Modules and What-if Scenarios

Map UI: http://localhost:8095/. Scenarios are in the left panel under **What-if scenarios**, and modules under **Modules** below them. The live vs baseline KPI table is on the right, and the event log is below it. Every action is also available over HTTP (see the README), and all of them share one undo stack (**Undo last**). **Reset all** turns every scenario off and every module on. **Resync live = baseline** copies the baseline state into the live copy, so that the next comparison starts from identical mines; after an undo the live mine otherwise keeps its history (stockpiles, truck positions, wear).

| Module or What-if Scenario | What it does | How to see it on the Stand | How to undo it (scenarios) |
|---|---|---|---|
| **Haulage and dispatch** (module) | Sends each empty truck to the shovel with the earliest expected loading start, corrected for the target-rate deficit and for the feed grade. Sends ore to the ROM pad when the crusher is down or has a long wait, the coarse ore stockpile is full, or the load would push the feed grade out of band. Re-routes around closed roads. Rations trucks when diesel cover is short. Off: fixed 2/2/4 allocation, all ore to the crusher, no blending, no rationing. | Modules → Haulage and dispatch: decision log with reasons (e.g. "truck-04 -> ex-03, not ex-02: rate balance"), counters. Switch off: queue time and feed grade KPIs move against the baseline. | Switch on again, Undo last, or Reset all |
| **Maintenance planner** (module) | Every 15 simulated minutes, plans maintenance for a crusher, shovel or truck when P(failure in 8 h) × MTTR × loss × 1.3 > PM time × loss. Before crusher maintenance it builds coarse ore stock first. Off: run to failure. | Modules → Maintenance planner: decision log with the numbers behind each decision. Health cards below the map. Machines in maintenance are drawn pink. | Switch on again, Undo last, or Reset all |
| **Energy manager** (module) | Under a grid cap, holds the concentrator at (cap − crusher − base load) / specific energy at the current ore hardness. Off: a 15-minute demand block above the cap trips the concentrator for 15 minutes (load shedding). | Modules → Energy manager: decision log, cap violations, plant state. The plant label shows "GRID CAP" or "LOAD SHED", with a red ring when shed. Gauge "Grid demand (15 min)". | Switch on again, Undo last, or Reset all |
| Crusher failure | Stops the primary crusher. | Crusher pulses red. Ore goes to the ROM pad, the coarse ore stockpile gauge drains, then plant feed and copper fall vs baseline. | Undo on the card, `DELETE /api/scenarios/crusher_failure`, Undo last |
| Road closure | Closes a chosen road segment (default s04, main ramp). | Road drawn red. Trucks re-route; mined t/h, queue time and diesel L/t change vs baseline. | Same as above |
| Excavator breakdown | Takes a chosen shovel out of service. | Shovel pulses red. Its trucks are re-assigned; queue time rises and feed grade drops vs baseline. | Same |
| Hardness spike | Multiplies the Bond work index at chosen faces (×1.0–2.5). | Slower digging, lower crusher capacity, higher crusher and site kWh/t vs baseline. | Same |
| Low-grade zone | Multiplies the copper grade at chosen faces (×0.1–1.0). | Feed grade and copper fall. The dispatcher re-blends (blend diversions, ROM reclaim in its log). | Same |
| Fuel shortage | Cuts diesel deliveries and sets the fuel bay stock. | Fuel bay gauge falls. The dispatcher parks trucks (waste first, at least 3 keep running), and its log shows the rationing decision. Mined t/h falls while plant feed is protected. | Same |
| Energy cap | Caps site electrical demand (5–60 MW, default 28 MW). | The energy manager throttles the concentrator; plant feed falls vs baseline, while demand stays at the cap. | Same |

**Effect sizes.** These numbers come from `./twinvision.sh compare --hours 24`, the same simulator code run headless:

- seed 42, 2 h warm-up, then 24 h of simulated time with the case applied to the live copy only;
- totals over the 24 h window, with queue time and availability averaged over it;
- each cell is live / baseline.

| Case | Plant feed t | Feed grade % Cu | Copper t | Mined t | Queue min/cycle | Availability % | Site kWh/t | Diesel L/t | Failures |
|---|---|---|---|---|---|---|---|---|---|
| Crusher failure (kept on for 24 h) | 3,613 / 47,666 | 0.715 / 0.634 | 22.7 / 265.4 | 174,888 / 173,136 | 0.47 / 0.85 | 87.6 / 95.0 | 32.05 / 17.47 | 0.204 / 0.215 | 2 / 2 |
| Road closure s04 | 47,369 / 47,666 | 0.623 / 0.634 | 259.3 / 265.4 | 161,870 / 173,136 | 0.65 / 0.85 | 94.4 / 95.0 | 17.30 / 17.47 | 0.235 / 0.215 | 2 / 2 |
| Excavator breakdown ex-01 | 33,542 / 47,666 | 0.542 / 0.634 | 159.8 / 265.4 | 145,363 / 173,136 | 4.99 / 0.85 | 87.5 / 95.0 | 17.62 / 17.47 | 0.200 / 0.215 | 1 / 2 |
| Hardness spike ×1.4 north | 44,146 / 47,666 | 0.637 / 0.634 | 247.1 / 265.4 | 171,736 / 173,136 | 0.80 / 0.85 | 95.1 / 95.0 | 21.41 / 17.47 | 0.211 / 0.215 | 2 / 2 |
| Low-grade zone ×0.6 north | 34,473 / 47,666 | 0.537 / 0.634 | 162.9 / 265.4 | 164,609 / 173,136 | 2.10 / 0.85 | 95.3 / 95.0 | 17.64 / 17.47 | 0.213 / 0.215 | 1 / 2 |
| Fuel shortage (no deliveries, 8,000 L left) | 32,977 / 47,666 | 0.647 / 0.634 | 187.5 / 265.4 | 38,944 / 173,136 | 0.07 / 0.85 | 97.6 / 95.0 | 17.82 / 17.47 | 0.313 / 0.215 | 0 / 2 |
| Energy cap 28 MW, energy module on | 37,590 / 47,666 | 0.649 / 0.634 | 214.3 / 265.4 | 172,462 / 173,136 | 0.79 / 0.85 | 94.7 / 95.0 | 17.88 / 17.47 | 0.209 / 0.215 | 2 / 2 |
| Energy cap 28 MW, energy module off (48 load-shed events) | 24,027 / 47,666 | 0.666 / 0.634 | 140.7 / 265.4 | 172,828 / 173,136 | 0.66 / 0.85 | 94.7 / 95.0 | 17.56 / 17.47 | 0.205 / 0.215 | 2 / 2 |
| Dispatcher off | 47,700 / 47,666 | 0.581 / 0.634 | 243.8 / 265.4 | 168,888 / 173,136 | 1.74 / 0.85 | 95.3 / 95.0 | 17.12 / 17.47 | 0.199 / 0.215 | 1 / 2 |
| Maintenance off | 36,499 / 47,666 | 0.657 / 0.634 | 210.9 / 265.4 | 174,020 / 173,136 | 1.11 / 0.85 | 94.8 / 95.0 | 17.89 / 17.47 | 0.208 / 0.215 | 4 / 2 |

What the table shows about the modules:

- **Energy manager.** Under the same 28 MW cap it keeps 214.3 t of copper against 140.7 t with load shedding.
- **Dispatcher.** It adds 22 t of copper per day (+9 %) and halves queue time.
- **Maintenance planner.** It avoids two failures and 11,000 t of lost plant feed per day; most of that loss comes from one crusher breakdown that run-to-failure lets happen.

## What works and what does not

**Verified on the stack.** `./twinvision.sh smoke` checks all of the following and passes:

- **Twins.** All 75 are created; types, counts per copy, parents and relation targets match `SCHEMA.md`.
- **Simulation and balances.** Both copies advance. The mass, fuel and energy balance residuals are 0 on both. Trucks move.
- **Telemetry.** It reaches InfluxDB for the live and baseline twins.
- **Grafana.** The dashboard is provisioned.
- **Map UI.** It serves geometry from twin attributes, live state from Ditto and trends from InfluxDB.
- **What-if round trip.** A crusher failure started through the API fails the live crusher only. Live crushed t/h falls more than 500 t/h below the baseline; it measured −528 t/h. After undo the crusher returns and the gap closes.
- **Module round trip.** Maintenance off reaches the live planner twin only, and undo turns it back on.
- **Resync.** After a resync, live and baseline report identical totals.

`./twinvision.sh platform-smoke` (the platform's `make smoke`) also passes.

**Unit tests.** `./twinvision.sh test` runs 46 tests in the image. They cover:

- mass, fuel and energy balance at every step (absolute residual below 10⁻⁶);
- determinism and identical live and baseline runs without scenarios;
- propagation of every scenario;
- each module on vs off;
- decision-log reasons;
- scenario parsing;
- feature type stability (InfluxDB rejects a field whose type changes);
- the web API, including undo.

**Clean start.** The README steps were run as a user `judge` on a fresh Ubuntu 24.04 container with Docker 29.1.5 and Compose 2.40.0, without `make`. `./twinvision.sh up` took 122 s on a fast connection, including all image pulls; it was followed by the TwinVision smoke test, the platform smoke test and the unit tests. That run was on arm64. `.github/workflows/twinvision-clean-start.yml` runs the same steps on GitHub's Ubuntu 24.04 amd64 runners.

**Hand-checked in the UI.** Each scenario and module switch was run from the map UI and checked on screen:

- the closed road turns red;
- a failed crusher shows a red ring;
- the energy cap with the energy module off sheds the plant; the event log, the plant label and the header badge ("Energy manager OFF") show it;
- the decision logs update.

**Not done.** There is no 3D (Unity) scene and no computer-vision module.

## Data and Assumptions

- **Real data.** The open GeoMet dataset (Hoffimann et al. 2022, Zenodo record 7051975, CC BY 4.0) is included unmodified in `twinvision/data/geomet/`, with checksums. Block grades follow the distribution of its 2,000 copper assays; Bond work indices come from its 60 grindability tests; copper recovery (87.9 %) is the mean of its 53 flotation tests.
- **Published parameters.** Equipment parameters come from public data sheets (Cat 793F class trucks, Komatsu PC5500, Cat 6060 and Liebherr R 9600 class shovels, a 750 kW gyratory crusher). Comminution energy uses Bond's third law.
- **Synthetic.** The following are synthetic and labelled as such: the mine itself, its location, pit geometry, road network, the spatial pattern of the block model, fleet size, stockpile sizes, plant target, fuel logistics, failure and maintenance parameters, and the grid cap.
- **No sensitive data.** No confidential or personal data is used.
- **Details.** [`twinvision/DATA_ASSUMPTIONS.md`](twinvision/DATA_ASSUMPTIONS.md) lists every parameter with its value and source; [`DATA_LICENSES.md`](DATA_LICENSES.md) covers licences; [`twinvision/DECISIONS.md`](twinvision/DECISIONS.md) explains the design choices.

## Mine visualization

**Live mine map: http://localhost:8095/.** No login is needed. It opens with moving trucks and needs no clicks.

- **Map.** A 2D SVG map refreshed every second from Ditto. It shows:
  - pit benches and block clusters coloured by copper grade, with dug blocks faded;
  - roads (closed ones in red);
  - shovels and trucks coloured by state, with load-grade dots and health rings;
  - the crusher, stockpile fill levels, the concentrator with feed-grade status, the waste dump and the fuel bay;
  - failed or closed items pulsing red, and optional dashed baseline "ghost" trucks.
- **Views and inspector.** Mine, Pit and Plant views are available. Clicking any object shows its Ditto thing, live next to baseline.
- **Header and gauges.** The header shows the Ditto connection, the live mass, fuel and energy balance badges, and the active what-ifs or switched-off modules. Below the map are gauges with baseline markers and machine health cards.
- **Right panel.** It shows 11 KPIs live vs baseline with deltas (t/h, ore, plant feed, feed grade, copper, queue time, availability, crusher and site kWh/t, diesel L/t, 15-minute grid demand), an InfluxDB trend of the selected KPI and the event log.

**Grafana: http://localhost:3000/d/twinvision-overview.** The dashboard "TwinVision Copper Pit – live vs baseline" pairs a live and a baseline series for every KPI, the balance residuals and maintenance/reliability. The twin hierarchy is under Grafana → OpenEgiz app → Twins.

## Limitations

- **Map.** It is 2D only; there is no Unity scene.
- **Fictional site.** The mine is fictional. Only the grade, hardness and recovery distributions come from real data; the spatial pattern of the block model is synthetic.
- **Concentrator model.** The concentrator is a feed sink with Bond energy and a constant recovery. There are no flotation or grinding circuit dynamics, and no concentrate or tailings handling.
- **Fixed fleet.** The fleet is fixed (8 trucks, 3 shovels), and there is one crusher and one dump.
- **Restarts.** The simulator does not persist its state: after a container restart both copies start again from simulated time 0, which keeps them comparable. The undo history is held in memory by the web service; the scenario state itself is stored in Ditto and survives restarts.
- **KPI averaging.** KPIs are 30-minute moving averages in simulated time (about 90 s of real time at the default 20× speed), so effects build up over a minute or two.
- **Simplified energy cap.** The grid cap acts on the concentrator only, and load shedding is modelled as a fixed 15-minute trip.
- **Lab-tested clean start.** The clean start was verified in an arm64 container that mirrors the judging setup; amd64 is covered by the CI workflow.

## Presentation

`presentation.pdf` in the repository root.

---

Platform: OpenEgiz v1.0.4.
