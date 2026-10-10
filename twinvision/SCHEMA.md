# TwinVision twin schema

All twins are Eclipse Ditto things in the namespace `org.twinvision.copper`, policy `default:basic_policy`. They are created by [`setup/setup_twins.py`](setup/setup_twins.py) from the catalog in [`sim/twins.py`](sim/twins.py), which is also what the simulator publishes. Run `python3 -m twinvision.setup.setup_twins --print` to see the exact JSON.

## Two copies of the mine

| Copy | Thing ids | Root | Written by |
|---|---|---|---|
| live | `org.twinvision.copper:<name>` | `mine` | simulator, with the what-if scenarios from `scenario-controller` applied |
| baseline | `org.twinvision.copper:<name>-baseline` | `mine-baseline` | the same simulator, same seed, never given a scenario |

Every live twin has `attributes.baseline_twin` and every baseline twin has `attributes.live_twin`, so a client can pair them. Without scenarios the two copies are identical step for step (tested in `tests/test_sim.py`).

## Hierarchy (`attributes._parents`)

```text
mine
├── pit
│   ├── blocks-north, blocks-east, blocks-south        BlockCluster
│   ├── ex-01, ex-02, ex-03                            Excavator
│   ├── truck-01 … truck-08                            HaulTruck
│   └── road-s01 … road-s12                            RoadSegment
├── crusher-01                                         Crusher
├── stockpile-rom, stockpile-cos                       Stockpile
├── plant-feed                                         PlantFeed
├── waste-dump                                         WasteDump
├── dispatcher                                         DispatcherController
├── maintenance                                        MaintenancePlanner
├── fuel-bay                                           FuelBay
├── power-grid                                         PowerGrid
└── scenario-controller   (live copy only)             ScenarioController
```

The baseline tree is the same under `mine-baseline`. Total: 38 live + 37 baseline = 75 twins.

## Twin inventory

`make twinvision-twins` reads every twin from Ditto, prints the hierarchy and checks it against this table (type, count per copy, parent) and checks that every relation attribute points to an existing twin.

| Type | Live | Baseline | Parent |
|---|---|---|---|
| Mine | 1 | 1 | – |
| Pit | 1 | 1 | mine |
| BlockCluster | 3 | 3 | pit |
| Excavator | 3 | 3 | pit |
| HaulTruck | 8 | 8 | pit |
| RoadSegment | 12 | 12 | pit |
| Crusher | 1 | 1 | mine |
| Stockpile | 2 | 2 | mine |
| PlantFeed | 1 | 1 | mine |
| WasteDump | 1 | 1 | mine |
| DispatcherController | 1 | 1 | mine |
| MaintenancePlanner | 1 | 1 | mine |
| FuelBay | 1 | 1 | mine |
| PowerGrid | 1 | 1 | mine |
| ScenarioController | 1 | 0 | mine |

## Relations

Material flow and physical links are attributes holding thing ids or road node names:

| From | Attribute | To |
|---|---|---|
| Excavator | `digs` | its BlockCluster |
| BlockCluster | `excavator` | the Excavator working it |
| Excavator, Crusher, Stockpile (ROM), WasteDump | `node` | road graph node (`mine.attributes.road_nodes`) |
| RoadSegment | `from_node`, `to_node` | road graph nodes |
| Stockpile (ROM) | `feeds` | `crusher-01` (front-end loader reclaim) |
| Crusher | `feeds` | `stockpile-cos` |
| Stockpile (COS) | `feeds` | `plant-feed` |
| DispatcherController | `controls` | every HaulTruck |
| MaintenancePlanner | `maintains` | crusher, excavators, trucks |
| FuelBay | `supplies` | every HaulTruck and the ROM loader (`stockpile-rom`) |
| PowerGrid | `supplies` | `crusher-01`, `plant-feed` |

Changing relations are features: a truck's `assigned_to` (excavator), `destination`, `segment`/`node` (where it is on the road graph), `source` (excavator its load came from); an excavator's `current_truck`.

Every feature has the shape `{"properties": {"value": <v>, "unit": "<unit>"}}`. Each feature keeps one type (number or string) forever, because InfluxDB rejects a field whose type changes (tested).

## Types

### Mine (`mine`)
Static: `road_nodes` (x, y, z, label of every graph node), `facilities` (map positions), `description`.

| Feature | Unit | Meaning |
|---|---|---|
| `sim_time_h` | h | simulated time since start |
| `tph_mined`, `tph_ore`, `tph_waste` | t/h | material dug, 30-min exponential average |
| `tph_crushed` | t/h | crusher output, 30-min average |
| `plant_feed_tph`, `plant_feed_grade` | t/h, % Cu | concentrator feed, 30-min average |
| `mean_queue_min` | min | truck queue and road wait time per haul cycle |
| `availability_pct` | % | share of excavators, trucks and crusher in service |
| `crusher_kwh_t` | kWh/t | crusher specific energy |
| `site_kwh_t` | kWh/t | crusher + concentrator energy per tonne of plant feed |
| `copper_tph`, `copper_total_t` | t/h, t | copper recovered to concentrate (feed × grade × 87.9 % recovery) |
| `diesel_l_t`, `diesel_lph` | L/t, L/h | fleet diesel per tonne hauled, burn rate |
| `site_power_kw` | kW | crusher + concentrator electrical demand |
| `mined_total_t`, `plant_feed_total_t`, `copper_in_feed_t`, `waste_total_t` | t | totals since start |
| `mass_balance_residual_t` | t | dug + opening stocks − (stocks + in trucks + waste + plant feed); ≈ 0 |
| `fuel_balance_residual_l` | L | opening diesel (tanks + bay) + deliveries − (burned + written off + tanks + bay); ≈ 0 |
| `energy_balance_residual_kwh` | kWh | grid-metered energy − (crusher and concentrator fixed loads + Bond specific energy × tonnes); ≈ 0 |
| `modules_off` | | comma-separated decision modules switched off in this copy (`none`) |
| `trucks_queued` | trucks | trucks queued or waiting now |
| `active_scenarios` | | comma-separated active what-ifs (`none`) |
| `last_event` | | latest simulation event (failure, closure, maintenance decision…) |
| `event_log` | | JSON string: list of the last 12 events `{"t_h": <sim hours>, "text": ...}` |

### Pit (`pit`)
Static: `rim` ellipse, `benches` (z, rx, ry of every bench crest), `floor_z`, `bench_height_m`.
Features: `trucks_in_pit`, `faces_active`, `roads_closed`.

### BlockCluster (`blocks-*`)
Static: `blocks` = list of `[x, y, grade % Cu, Bond work index kWh/t, tonnes]` in digging order, `block_size_m`, `density_t_m3`, `rock`, `bench_z`, `cutoff_grade_pct`.

| Feature | Unit | Meaning |
|---|---|---|
| `current_block` | index | block being dug (index into `blocks`) |
| `block_remaining_t` | t | tonnes left in it |
| `face_grade`, `face_bwi` | % Cu, kWh/t | grade and hardness at the face (after what-if multipliers) |
| `face_rock` | | rock type |
| `cut`, `remaining_t`, `mined_t` | | progress |

### Health features (Excavator, HaulTruck, Crusher)

| Feature | Unit | Meaning |
|---|---|---|
| `health_pct` | % | condition: falls with work (harder rock and heavier loads wear faster), restored by repair or planned maintenance |
| `failure_prob_8h` | probability | P(failure within the 8 h planning horizon) from the current hazard rate |
| `maintenance_status` | | `ok`, `pm_planned`, `in_maintenance`, `breakdown` |
| `failures_total`, `planned_maintenance_total` | | counters |

### Excavator (`ex-*`)
Static: `model`, `bucket_m3`, `bucket_t`, `role` (`ore`/`waste`), `location`, `node`, `digs`.
State enum `state`: `loading`, `waiting_for_truck`, `failed` (what-if), `breakdown`, `maintenance`.
Features: `x`, `y` (m), `tph`, `tonnes_total`, `passes_total`, `queue_len`, `current_truck`, `face_grade`, `face_bwi`, `utilization_pct`, health features.

### HaulTruck (`truck-*`)
Static: `model`, `payload_t`, `empty_t`, `net_power_kw`, `tank_l`.
State enum `state`: `parked`, `returning`, `queued_at_shovel`, `loading`, `hauling`, `queued_at_crusher`, `dumping_crusher`, `queued_at_rom`, `dumping_rom`, `queued_at_dump`, `dumping_waste`, `to_fuel_bay`, `queued_at_fuel_bay`, `refueling`, `waiting_road`, `waiting`, `breakdown`, `to_workshop`, `queued_at_workshop`, `maintenance`, `to_park`.
Features: `x`, `y` (m), `segment`, `node`, `speed_kmh`, `payload_t`, `load_grade`, `material` (`ore`/`waste`/empty), `destination`, `assigned_to`, `source`, `fuel_l`, `fuel_pct`, `fuel_burned_l`, `trips_total`, `tonnes_total`, `last_queue_min`, `waiting_reason` (e.g. `fuel rationing`, `road closed`), health features.

### RoadSegment (`road-s*`)
Static: `from_node`, `to_node`, `length_m`, `grade_pct` (rise from `from_node` to `to_node`), `speed_limit_kmh`, `capacity_trucks`, `geometry` (polyline of x, y, z).
State enum `status`: `open`, `closed`. Features: `trucks_on`, `passes_total`, `mean_speed_kmh`.

### Crusher (`crusher-01`)
Static: `model`, `installed_kw`, `pocket_t`, `vol_cap_tph`, `location`, `feeds`.
State enum `state`: `crushing`, `starved`, `blocked_cos_full`, `failed` (what-if), `breakdown`, `maintenance`.
Features: `throughput_tph`, `throughput_avg_tph`, `capacity_tph` (power- and volume-limited at the current feed hardness), `pocket_t`, `feed_bwi`, `power_kw`, `kwh_t`, `queue_len`, `tonnes_total`, `energy_kwh`, health features.

### Stockpile (`stockpile-rom`, `stockpile-cos`)
Static: `role`, `capacity_t`, `location`, `feeds`.
ROM features: `stock_t`, `grade`, `bwi`, `fill_pct`, `inflow_tph`, `reclaim_tph`, `loader_state` (`reclaiming`/`standby`), `tonnes_in_total`, `tonnes_out_total`, `queue_len`.
COS features: `stock_t`, `grade`, `bwi`, `fill_pct`, `hours_of_feed`.

### PlantFeed (`plant-feed`)
Static: `target_tph`, `grade_min_pct`, `grade_max_pct`, `location`.
State enum `grade_status`: `in_spec`, `low`, `high`, `starved`.
Features: `feed_tph`, `feed_grade` (instant), `feed_tph_avg`, `feed_grade_avg`, `target_tph`, `grade_min`, `grade_max`, `power_kw`, `capacity_tph` (lowered by a grid cap), `limited_by` (`none`, `feed`, `grid_cap`, `load_shed`), `tonnes_total`, `copper_t` (copper in feed), `copper_tph`, `recovered_copper_t`.

### WasteDump (`waste-dump`)
Features: `tonnes_total`, `tph`, `trucks_tipping`, `queue_len`.

### DispatcherController (`dispatcher`)
Static: `strategy`, `targets_tph` (per excavator), `target_grade_pct`, `controls`.
Features: `decisions_total`, `last_decision`, `reroutes_total`, `ore_to_rom_total`, `trucks_<ex>` (trucks allocated to each excavator), `assigned_tph_<ex>`, `blend_diversions_total` (ore loads sent to the ROM pad to keep the feed grade in band), `fuel_rationed_trucks`, `last_fuel_decision`, `module_enabled`, `decision_log`.

Every decision module twin (dispatcher, maintenance, power-grid) publishes `module_enabled` (boolean) and `decision_log`: a JSON string with its last 10 decisions, `[{"t_h": <sim hours>, "text": "..."}]`. The dispatcher logs only notable decisions: assignments that differ from the fastest shovel, re-routes, ROM diversions and fuel rationing.

### MaintenancePlanner (`maintenance`)
Static: `strategy`, `horizon_h`, `unplanned_factor`, `review_interval_s`, `workshop_bays`, `failure_model` (per machine class: base hazard, wear-hazard exponent, wear rate, MTTR, PM duration), `maintains`.
Features: `decisions_total`, `last_decision` (with the numbers behind it), `expected_loss_avoided_t`, `units_broken`, `units_in_maintenance`, `units_pm_planned`, `failures_total`, `planned_total`, `fleet_health_pct`, `module_enabled`, `decision_log`.

### FuelBay (`fuel-bay`)
Static: `capacity_l`, `spots`, `normal_resupply_lph`, `location`, `node`, `supplies`.
Features: `stock_l`, `fill_pct`, `resupply_lph`, `consumption_lph`, `hours_of_cover` (999 = not depleting), `dispensed_total_l`, `trucks_refueling`, `queue_len`, `status` (`normal`, `supply_cut`).

### PowerGrid (`power-grid`)
Static: `demand_window_s`, `supplies`.
Features: `site_power_kw`, `crusher_kw`, `plant_kw`, `demand_15min_kw` (last completed 15-min block average), `peak_demand_kw`, `cap_kw` (0 = none), `cap_active`, `energy_total_mwh`, `kwh_per_t`, `module_enabled`, `plant_throttled` (energy module holding the concentrator below the cap), `plant_load_shed` (concentrator tripped for 15 min after a block above the cap, energy module off), `cap_violations_total`, `decisions_total`, `last_decision`, `decision_log`.

### ScenarioController (`scenario-controller`, live only)
Written by the TwinVision web API (or any Ditto client); the simulator reads it. Each scenario is one feature:

| Feature | Properties |
|---|---|
| `crusher_failure` | `active` |
| `road_closure` | `active`, `segments` (e.g. `["s04"]`) |
| `excavator_breakdown` | `active`, `excavators` (e.g. `["ex-01"]`) |
| `hardness_spike` | `active`, `factor` (1.0–2.5), `clusters` |
| `low_grade_zone` | `active`, `factor` (0.1–1.0), `clusters` |
| `fuel_shortage` | `active`, `resupply_lph` (0–2200), `bay_stock_l` (stock left at the fuel bay when the shortage starts) |
| `energy_cap` | `active`, `cap_kw` (5,000–60,000; normal site load is about 34–35 MW) |
| `dispatcher_module`, `maintenance_module`, `energy_module` | `enabled` (default `true`); `false` switches that decision module off in the live copy only |
