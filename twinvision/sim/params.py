"""Equipment and process parameters of the TwinVision Copper Pit.

Every number is either taken from a public manufacturer data sheet (noted
"spec"), a standard engineering approximation (noted "approx."), or chosen for
this fictional site (noted "site", synthetic). See twinvision/DATA.md.
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
REF_BWI = 14.0                 # kWh/t, reference Bond work index of the ore

# --- Primary crusher: 60-89 class gyratory ---------------------------------
CRUSHER = {
    "model": "60-89 class gyratory crusher",
    "installed_kw": 600.0,       # spec class: 600 kW motor
    "idle_kw": 90.0,             # approx.: no-load power ~15 % of installed
    "vol_cap_tph": 3500.0,       # site: volumetric limit at the chosen CSS
    "kwh_per_t_per_bwi": 0.012,  # approx.: 0.17 kWh/t at BWi 14 (primary crushing 0.1-0.4 kWh/t)
    "pocket_t": 460.0,           # site: dump pocket, two truck loads
    "tip_s": 45.0,               # approx.
}

# --- ROM stockpile and front-end loader reclaim -----------------------------
ROM = {
    "capacity_t": 150000.0,      # site
    "initial_t": 25000.0,        # site
    "initial_grade": 0.42,       # site, % Cu
    "initial_bwi": 13.5,
    "tip_spots": 2,
    "tip_s": 50.0,
    "loader_model": "Cat 994K class wheel loader",
    "reclaim_tph": 1400.0,       # approx.: 994-class loader into the crusher pocket
    "loader_l_per_h": 140.0,     # approx.: loader fuel burn while reclaiming
}

# --- Coarse ore stockpile (crusher product) and concentrator feed ------------
COS = {
    "capacity_t": 6000.0,        # site: live capacity, ~3 h of plant feed (small on purpose, see DATA.md)
    "initial_t": 3000.0,
    "initial_grade": 0.52,
    "initial_bwi": 14.0,
}

PLANT = {
    "target_tph": 2000.0,        # site: ~16 Mt/a at 92 % availability
    "grade_min": 0.45,           # site: feed grade band, % Cu
    "grade_max": 0.65,
    "base_kw": 2500.0,           # approx.: pumps, flotation, auxiliaries
    "kwh_per_t_per_bwi": 0.95,   # approx.: SAG+ball grinding ~13 kWh/t at BWi 14
}

WASTE_DUMP = {"tip_spots": 3, "tip_s": 50.0}

FUEL_BAY = {"spots": 2}

# --- Block model (synthetic, see blocks.py) ----------------------------------
BLOCK = {"dx": 10.0, "dy": 10.0, "dz": 15.0, "density": 2.6}   # m, m, m, t/m3 in situ

CLUSTERS = [
    {"id": "blocks-north", "bench_z": -120, "excavator": "ex-01", "nx": 12, "ny": 6,
     "grade_mean": 0.60, "grade_sd": 0.16, "bwi_mean": 15.0, "bwi_sd": 1.6,
     "rock": "quartz monzonite porphyry (hypogene)"},
    {"id": "blocks-east", "bench_z": -60, "excavator": "ex-02", "nx": 12, "ny": 6,
     "grade_mean": 0.46, "grade_sd": 0.14, "bwi_mean": 13.0, "bwi_sd": 1.4,
     "rock": "andesite-hosted porphyry (transitional)"},
    {"id": "blocks-south", "bench_z": -15, "excavator": "ex-03", "nx": 12, "ny": 6,
     "grade_mean": 0.08, "grade_sd": 0.04, "bwi_mean": 11.0, "bwi_sd": 1.2,
     "rock": "leached cap andesite (waste)"},
]

# --- Dispatcher ----------------------------------------------------------------
DISPATCH = {
    "target_tph": {"ex-01": 1250.0, "ex-02": 1050.0, "ex-03": 4500.0},  # site plan, strip ratio ~2
    "deficit_weight_s": 900.0,   # s of expected-start-time a 100 % target deficit is worth
    "cos_high_frac": 0.65,       # above this COS level ore goes to the ROM pad
    "cos_reclaim_frac": 0.45,    # below this the loader reclaims ROM into the crusher
    "crusher_wait_max_s": 600.0,  # expected wait at the crusher above which ore goes to ROM
}

KPI_TAU_S = 1800.0             # s, time constant of the rolling KPI averages
