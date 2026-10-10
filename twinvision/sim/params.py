"""Equipment and process parameters of the TwinVision Copper Pit.

Every number is either taken from a public manufacturer data sheet (noted
"spec"), a standard engineering approximation (noted "approx."), or chosen for
this fictional site (noted "site", synthetic). See DATA_LICENSES.md and twinvision/DECISIONS.md.
"""

NAMESPACE = "org.twinvision.copper"
SITE_NAME = "TwinVision Copper Pit"

G = 9.81

# --- Haul truck: Cat 793F class -------------------------------------------
TRUCK = {
    "model": "Cat 793F class",
    "payload_t": 226.8,          # spec: nominal payload 226.8 t (250 US t)
    "empty_t": 159.0,            # spec: chassis 123.6 t + body 30.7 t, plus fuel/operator (approx.)
    "net_power_kw": 1848.0,      # spec: net power SAE J1349
    "drive_eff": 0.82,           # approx.: mechanical drivetrain flywheel-to-rim efficiency
    "rolling_res": 0.025,        # approx.: 2.5 % rolling resistance on a maintained haul road
    "vmax_loaded_kmh": 60.0,     # spec: top speed loaded
    "vmax_empty_kmh": 60.0,
    "downhill_loaded_kmh": 22.0,  # approx.: retarder-limited speed on an 8 % ramp, loaded
    "downhill_empty_kmh": 35.0,
    "tank_l": 2839.0,            # spec: standard fuel tank
    "bsfc_l_per_kwh": 0.25,      # approx.: ~210 g/kWh at 0.84 kg/L
    "idle_kw": 70.0,             # approx.: idle + auxiliaries, gives ~18 L/h at idle
    "refuel_below_frac": 0.20,   # site
    "refuel_lpm": 700.0,         # site: fuel bay delivery rate, L/min
    "refuel_fixed_s": 240.0,     # site: positioning, hookup
    "dump_s": 50.0,              # approx.: spot + raise body + lower
}

TRUCK_COUNT = 8

# --- Excavators -------------------------------------------------------------
# bucket_t = heaped capacity (m3) x fill factor x loose density (t/m3).
EXCAVATORS = [
    {
        "id": "ex-01", "node": "EX1", "cluster": "blocks-north",
        "model": "Komatsu PC5500-6 class hydraulic face shovel",
        "bucket_m3": 29.0,        # spec: 29 m3 face shovel bucket
        "bucket_t": 48.0,         # 29 m3 x 0.9 x 1.85 t/m3 loose
        "pass_s": 30.0,           # approx.: swing cycle
        "spot_s": 40.0,           # approx.: truck spotting under the bucket
        "role": "ore",
    },
    {
        "id": "ex-02", "node": "EX2", "cluster": "blocks-east",
        "model": "Caterpillar 6060 class hydraulic face shovel",
        "bucket_m3": 34.0,        # spec: 34 m3 face shovel bucket
        "bucket_t": 56.0,         # 34 m3 x 0.9 x 1.85 t/m3 loose
        "pass_s": 31.0,
        "spot_s": 40.0,
        "role": "ore",
    },
    {
        "id": "ex-03", "node": "EX3", "cluster": "blocks-south",
        "model": "Liebherr R 9600 class hydraulic backhoe",
        "bucket_m3": 34.0,        # approx.: 34 m3 backhoe bucket
        "bucket_t": 55.0,         # 34 m3 x 0.9 x 1.8 t/m3 loose
        "pass_s": 32.0,
        "spot_s": 40.0,
        "role": "waste",
    },
]

# --- Ore definition ----------------------------------------------------------
CUTOFF_GRADE = 0.25            # % Cu, site: below this a block is waste
REF_BWI = 20.0                 # kWh/t, reference Bond work index (GeoMet dataset mean, see blocks.py)
CU_RECOVERY = 0.879            # GeoMet flotation.csv: mean locked-cycle-test recovery, 52 tests
                               # (no significant grade dependence in that data, R2 = 0.006)

# --- Primary crusher: 60-89 class gyratory ---------------------------------
CRUSHER = {
    "model": "60-110 class gyratory crusher",
    "installed_kw": 750.0,       # spec class: 750 kW motor
    "idle_kw": 110.0,            # approx.: no-load power ~15 % of installed
    "vol_cap_tph": 3500.0,       # site: volumetric limit at the chosen CSS
    # Bond's law W = 10 Wi (1/sqrt(P80) - 1/sqrt(F80)), F80 600 mm -> P80 150 mm, in um:
    "kwh_per_t_per_bwi": 0.0129,
    "pocket_t": 460.0,           # site: dump pocket, two truck loads
    "tip_s": 45.0,               # approx.
}

# --- ROM stockpile and front-end loader reclaim -----------------------------
ROM = {
    "capacity_t": 150000.0,      # site
    "initial_t": 25000.0,        # site
    "initial_grade": 0.55,       # site, % Cu
    "initial_bwi": 19.5,
    "tip_spots": 2,
    "tip_s": 50.0,
    "loader_model": "Cat 994K class wheel loader",
    "reclaim_tph": 1400.0,       # approx.: 994-class loader into the crusher pocket
    "loader_l_per_h": 140.0,     # approx.: loader fuel burn while reclaiming
}

# --- Coarse ore stockpile (crusher product) and concentrator feed ------------
COS = {
    "capacity_t": 6000.0,        # site: live capacity, ~3 h of plant feed (small on purpose, see DECISIONS.md)
    "initial_t": 3000.0,
    "initial_grade": 0.70,
    "initial_bwi": 20.0,
}

PLANT = {
    "target_tph": 2000.0,        # site: ~16 Mt/a at 92 % availability
    "grade_min": 0.55,           # site: feed grade band, % Cu
    "grade_max": 0.85,
    "base_kw": 2500.0,           # approx.: pumps, flotation, auxiliaries
    # Bond's law, F80 150 mm -> P80 150 um: 10 (1/sqrt(150) - 1/sqrt(150000)) = 0.79 kWh/t per Wi
    "kwh_per_t_per_bwi": 0.79,
}

WASTE_DUMP = {"tip_spots": 3, "tip_s": 50.0}

FUEL_BAY = {
    "spots": 2,
    "capacity_l": 80000.0,       # site: diesel storage at the fuel bay
    "initial_l": 50000.0,
    "resupply_lph": 2200.0,      # site: road tanker deliveries, averaged (fleet burns ~1,500 L/h)
    "min_dispense_l": 300.0,     # a truck waits in the bay queue if less is in stock
}

WORKSHOP = {"bays": 2}

GRID = {
    "demand_window_s": 900.0,    # 15-min demand interval used for peak-demand billing
}

# --- Maintenance (condition-based) -------------------------------------------
# Health is a 0-100 condition index. It falls with use (wear_ph per operating
# hour at nominal intensity). Failure hazard per operating hour grows as health
# falls: lambda = lambda0_ph * exp(k * (1 - health/100)). All site/synthetic;
# MTTR and PM durations are typical orders of magnitude for this equipment.
MAINT = {
    "truck": {"wear_ph": 1.6, "lambda0_ph": 1 / 300, "k": 4.5, "mttr_h": 4.0, "pm_h": 2.0,
              "loss_tph": 900.0},
    "excavator": {"wear_ph": 1.3, "lambda0_ph": 1 / 300, "k": 4.5, "mttr_h": 5.0, "pm_h": 3.0,
                  "loss_tph": None},
    "crusher": {"wear_ph": 1.0, "lambda0_ph": 1 / 400, "k": 4.5, "mttr_h": 6.0, "pm_h": 3.0,
                "loss_tph": None},
    "horizon_h": 8.0,            # look-ahead for the failure probability
    "unplanned_factor": 1.3,     # an unplanned stop costs more than the same planned stop
    "review_s": 900.0,           # planner runs every 15 simulated minutes
}

# --- Block model (synthetic, see blocks.py) ----------------------------------
BLOCK = {"dx": 10.0, "dy": 10.0, "dz": 15.0, "density": 2.6}   # m, m, m, t/m3 in situ

# Grade and hardness of every block are drawn from the empirical distributions
# of the GeoMet dataset (Cu assays: drillholes.csv, Bond work index from the
# grindability tests in comminution.csv), restricted to a quantile range per
# geological domain; the spatial pattern is synthetic (see blocks.py).
CLUSTERS = [
    {"id": "blocks-north", "bench_z": -120, "excavator": "ex-01", "nx": 12, "ny": 6,
     "cu_q": (0.40, 0.97), "bwi_q": (0.30, 1.00), "rock": "hypogene sulphide ore (high grade, hard)"},
    {"id": "blocks-east", "bench_z": -60, "excavator": "ex-02", "nx": 12, "ny": 6,
     "cu_q": (0.18, 0.72), "bwi_q": (0.00, 0.70), "rock": "transitional sulphide ore (medium grade)"},
    {"id": "blocks-south", "bench_z": -15, "excavator": "ex-03", "nx": 12, "ny": 6,
     "cu_q": (0.00, 0.17), "bwi_q": (0.00, 0.35), "rock": "leached cap (waste)"},
]
SUPPORT_FACTOR = 0.7           # affine support correction: 2 m assay -> 3,900 t block variance

# --- Dispatcher ----------------------------------------------------------------
DISPATCH = {
    "target_tph": {"ex-01": 1250.0, "ex-02": 1050.0, "ex-03": 4500.0},  # site plan, strip ratio ~2
    "deficit_weight_s": 900.0,
    "grade_weight_s": 600.0,     # s of start time worth moving the feed grade a full half-band toward target
    "cos_high_frac": 0.65,       # above this COS level ore goes to the ROM pad
    "cos_reclaim_frac": 0.45,    # below this the loader reclaims ROM into the crusher
    "crusher_wait_max_s": 600.0,  # expected wait at the crusher above which ore goes to ROM
}

KPI_TAU_S = 1800.0             # s, time constant of the rolling KPI averages
