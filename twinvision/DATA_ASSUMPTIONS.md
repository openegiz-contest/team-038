# Data and modelling assumptions

The TwinVision Copper Pit is a fictional mine. Each number in the simulator is one of three kinds:

- **spec**: from a public manufacturer data sheet.
- **approx.**: a standard engineering approximation.
- **synthetic**: chosen for this site.

The values live in `twinvision/sim/params.py`, which carries the same labels. Licences for the external data are in [`DATA_LICENSES.md`](../DATA_LICENSES.md); the reasons behind the design choices are in [`DECISIONS.md`](DECISIONS.md).

## Ore body (GeoMet dataset, CC BY 4.0, plus a synthetic spatial pattern)

| Quantity | Value | Kind | Source / method |
|---|---|---|---|
| Copper grade distribution | 2,000 assay intervals (`Cu ppm` / 10,000 = % Cu) | open data | GeoMet `drillholes.csv` |
| Bond ball mill work index distribution | 60 tests, Wi = 44.5 / (A^0.23 · Gbp^0.82 · (10/√P80 − 10/√F80)) × 1.1023 | open data + formula | GeoMet `comminution.csv`; Bond (1961) |
| Copper recovery | 0.879 (mean of 53 locked-cycle tests, no grade dependence found, R² = 0.006) | open data | GeoMet `flotation.csv` |
| Spatial pattern of grade and hardness | seeded smooth random field, quantile-mapped per domain; hardness correlated 0.5 with grade | synthetic | `twinvision/sim/blocks.py` |
| Domain quantile ranges | north 0.40–0.97, east 0.18–0.72, south 0.00–0.17 (grade) | synthetic | gives about 0.95 %, 0.47 % and 0.13 % Cu |
| Support correction (2 m assay to 3,900 t block) | block = mean + 0.7 × (assay − mean) | approx. | affine correction |
| Block size, in-situ density | 10 × 10 × 15 m, 2.6 t/m³ | synthetic | |
| Cut-off grade | 0.25 % Cu | synthetic | |

## Equipment

| Quantity | Value | Kind | Source |
|---|---|---|---|
| Haul truck payload / empty mass / net power / tank / top speed | 226.8 t / 159 t / 1,848 kW / 2,839 L / 60 km/h | spec | Caterpillar 793F specification sheet |
| Drivetrain efficiency, rolling resistance | 0.82, 2.5 % | approx. | |
| Retarder-limited downhill speed | 22 km/h loaded, 35 km/h empty | approx. | |
| Diesel brake-specific fuel consumption | 0.25 L/kWh (about 210 g/kWh) | approx. | typical large mining diesel |
| Truck idle power | 70 kW (about 18 L/h) | approx. | |
| Shovel buckets | 29 m³ / 34 m³ / 34 m³ | spec / approx. | Komatsu PC5500, Cat 6060, Liebherr R 9600 class sheets |
| Bucket payload | m³ × fill factor 0.9 × loose density 1.8–1.85 t/m³ | approx. | |
| Swing cycle, truck spotting | 30–32 s, 40 s | approx. | |
| Primary crusher | 750 kW installed, 110 kW no-load, 3,500 t/h volumetric limit | spec class / approx. / synthetic | 60-110 gyratory class |
| ROM reclaim loader | 1,400 t/h, 140 L/h | approx. | Cat 994K class |
| Fleet | 8 trucks, 3 shovels | synthetic | |

## Process and energy

| Quantity | Value | Kind | Source / method |
|---|---|---|---|
| Crusher specific energy | 0.0129 kWh/t per unit Wi (F80 600 mm → P80 150 mm) | formula | Bond's third law |
| Concentrator specific energy | 0.79 kWh/t per unit Wi (F80 150 mm → P80 150 µm) | formula | Bond's third law |
| Concentrator base load | 2.5 MW | approx. | pumps, flotation, auxiliaries |
| Plant target rate, feed grade band | 2,000 t/h, 0.55–0.85 % Cu | synthetic | |
| Coarse ore stockpile | 6,000 t live (about 3 h of feed) | synthetic | small on purpose, see DECISIONS.md |
| ROM stockpile | 150,000 t capacity, 25,000 t opening | synthetic | |
| Demand interval | 15 min | approx. | usual peak-demand billing interval |
| Default grid cap (energy cap scenario) | 28,000 kW | synthetic | |

## Fuel

| Quantity | Value | Kind |
|---|---|---|
| Fuel bay storage, opening stock | 80,000 L, 50,000 L | synthetic |
| Average road tanker resupply | 2,200 L/h (fleet burns about 1,500 L/h) | synthetic |
| Bay delivery rate, fixed refuel time | 700 L/min, 240 s | synthetic |
| Refuel threshold | 20 % of tank | synthetic |

## Reliability and maintenance (all synthetic)

Health is a 0–100 condition index. It falls with use at `wear_ph` per operating hour. The failure hazard per operating hour is λ = λ0 · exp(k · (1 − health/100)). Each unit draws an Exp(1) budget from its own seeded random stream and fails when the accumulated hazard exceeds it. This means the live and baseline copies face the same luck.

| Unit | wear %/h | λ0 per h | k | MTTR | PM duration |
|---|---|---|---|---|---|
| Truck | 1.6 | 1/300 | 4.5 | 4 h | 2 h |
| Excavator | 1.3 | 1/300 | 4.5 | 5 h | 3 h |
| Crusher | 1.0 | 1/400 | 4.5 | 6 h | 3 h |

Planner rule: maintenance is planned when P(failure within 8 h) × MTTR × loss rate × 1.3 exceeds the PM duration × loss rate. The planner reviews every 15 simulated minutes, and the crusher loss is discounted by the hours of feed in the coarse ore stockpile.

## Conservation checks (computed every simulation step, published on the `mine` twins)

| Check | Definition | Feature |
|---|---|---|
| Mass | opening stocks + mined − (stocks + in trucks + dumped + plant feed) | `mass_balance_residual_t` |
| Fuel (trucks and ROM loader) | opening diesel (tanks + bay) + deliveries − (burned + written off + tanks + bay) | `fuel_balance_residual_l` |
| Energy | grid-metered energy − (fixed loads + specific energy × tonnes crushed and milled) | `energy_balance_residual_kwh` |

The unit tests check every step of multi-hour runs (including scenarios and load shedding) and require the absolute residual to stay below 10⁻⁶ t, L or kWh. The smoke test checks the residuals on the running stack, and the map header shows them live.

## Time and randomness

- One simulation step is 1 simulated second. By default the simulator runs 20 simulated seconds per real second (`TWINVISION_SPEEDUP`).
- Every stochastic element (cycle-time noise, failures, block field) uses its own seeded stream (`TWINVISION_SEED`, default 42). The same seed always gives the same run.
- Both copies use the same streams per entity, so a difference between live and baseline is caused by the scenario or module switch, not by chance.
