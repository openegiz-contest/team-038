"""Static geometry of the TwinVision Copper Pit.

Local metric grid: x east, y north, z relative to the pit rim (m). The pit is
an elliptical cone: every bench is an ellipse that shrinks with depth by the
overall wall slope. Roads are polylines of (x, y, z) points; ramps spiral
along the wall. The site is fictional; its proportions follow a mid-size
porphyry copper pit (about 1.4 x 0.9 km at the rim, 150 m deep, 15 m benches).
"""

import math

RIM_RX = 700.0          # m, semi-axis east-west at the rim
RIM_RY = 450.0          # m, semi-axis north-south at the rim
WALL_X = 3.2            # horizontal m per m of depth (overall slope ~17 deg on x)
WALL_Y = 2.0            # horizontal m per m of depth (overall slope ~27 deg on y)
BENCH_HEIGHT = 15.0     # m
PIT_FLOOR_Z = -150.0    # m


def ellipse_xy(z, angle_deg, offset=0.0):
    depth = max(0.0, -z)
    rx = RIM_RX - WALL_X * depth + offset
    ry = RIM_RY - WALL_Y * depth + offset
    a = math.radians(angle_deg)
    return rx * math.cos(a), ry * math.sin(a)


def _p(z, angle_deg, offset=0.0):
    x, y = ellipse_xy(z, angle_deg, offset)
    return (round(x, 1), round(y, 1), round(z, 1))


def spiral(z0, z1, a0, a1, n=24, offset=0.0):
    """Points along the pit wall from (z0, angle a0) to (z1, angle a1)."""
    pts = []
    for i in range(n + 1):
        t = i / n
        pts.append(_p(z0 + (z1 - z0) * t, a0 + (a1 - a0) * t, offset))
    return pts


# Road graph nodes: (x, y, z) and a human-readable label.
NODES = {
    "EX1": (_p(-120, 120), "Face north, bench -120 (EX-01)"),
    "J1": (_p(-120, 243), "Main ramp foot, bench -120"),
    "EX2": (_p(-60, 40), "Face east, bench -60 (EX-02)"),
    "J2": (_p(-60, -4), "Ramp junction, bench -60"),
    "EX3": (_p(-15, 215), "Waste face south-west, bench -15 (EX-03)"),
    "RIM_N": (_p(0, 84), "Pit exit north"),
    "RIM_W": (_p(0, 180), "Pit exit west"),
    "JC": ((1050.0, 520.0, 5.0), "Crusher junction"),
    "CR": ((1250.0, 620.0, 10.0), "Primary crusher tipping bay"),
    "ROM": ((1250.0, 380.0, 5.0), "ROM stockpile pad"),
    "DUMP": ((-2150.0, -650.0, 45.0), "Waste dump crest"),
    "WS": ((350.0, 950.0, 0.0), "Workshop and fuel bay"),
}


def _line(*names_or_pts):
    pts = []
    for item in names_or_pts:
        pts.append(NODES[item][0] if isinstance(item, str) else item)
    return pts


# id, name, from-node, to-node, polyline, speed limit km/h, capacity (trucks on
# the segment at once), closable in what-if scenarios.
SEGMENTS = [
    ("s01", "Pit floor road (bench -120)", "EX1", "J1", spiral(-120, -120, 120, 243, 12), 25, 4),
    ("s02", "Main ramp, lower", "J1", "J2", spiral(-120, -60, 243, 356), 30, 6),
    ("s03", "Bench -60 road", "EX2", "J2", spiral(-60, -60, 40, -4, 8), 25, 4),
    ("s04", "Main ramp, upper", "J2", "RIM_N", spiral(-60, 0, -4, 84), 30, 6),
    ("s05", "Bench -15 access ramp", "EX3", "RIM_W", spiral(-15, 0, 215, 180, 8), 30, 4),
    ("s06", "North haul road", "RIM_N", "JC", _line("RIM_N", (500.0, 520.0, 3.0), "JC"), 50, 10),
    ("s07", "Crusher approach", "JC", "CR", _line("JC", "CR"), 25, 4),
    ("s08", "ROM pad approach", "JC", "ROM", _line("JC", "ROM"), 25, 4),
    ("s09", "Waste haul road", "RIM_W", "DUMP", _line("RIM_W", (-1150.0, -120.0, 15.0), (-1700.0, -480.0, 32.0), "DUMP"), 45, 10),
    ("s10", "West ramp (long, 4%)", "J2", "RIM_W", spiral(-60, 0, -4, -180, 36, offset=-15.0), 30, 6),
    ("s11", "Rim road west-north", "RIM_W", "RIM_N", spiral(0, 0, 180, 84, 16, offset=40.0), 45, 10),
    ("s12", "Workshop road", "RIM_N", "WS", _line("RIM_N", (250.0, 700.0, 0.0), "WS"), 40, 10),
]

# Fix polyline endpoints exactly onto their nodes (spirals with an offset
# start a few metres away from the node).
SEGMENTS = [
    (sid, name, a, b, [NODES[a][0]] + pts[1:-1] + [NODES[b][0]], limit, cap)
    for sid, name, a, b, pts, limit, cap in SEGMENTS
]

# Surface facilities drawn on the map (not road nodes).
FACILITIES = {
    "crusher": {"x": 1250.0, "y": 620.0, "label": "Primary gyratory crusher"},
    "stockpile-rom": {"x": 1300.0, "y": 340.0, "label": "ROM stockpile"},
    "stockpile-cos": {"x": 1520.0, "y": 640.0, "label": "Coarse ore stockpile"},
    "plant-feed": {"x": 1760.0, "y": 640.0, "label": "Concentrator feed (SAG mill)"},
    "waste-dump": {"x": -2250.0, "y": -720.0, "label": "Waste dump"},
    "workshop": {"x": 350.0, "y": 950.0, "label": "Workshop and fuel bay"},
}


def bench_contours():
    """Bench crest ellipses for drawing: list of (z, rx, ry)."""
    out = []
    z = 0.0
    while z >= PIT_FLOOR_Z:
        depth = -z
        out.append((z, round(RIM_RX - WALL_X * depth, 1), round(RIM_RY - WALL_Y * depth, 1)))
        z -= BENCH_HEIGHT
    return out
