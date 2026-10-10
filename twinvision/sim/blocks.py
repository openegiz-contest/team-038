"""Block model: one cluster of blocks in front of every excavator face.

Values come from real data, the spatial pattern is synthetic:
- a seeded sum of Gaussian kernels gives a smooth standard-normal field per
  cluster (one for grade, one partly correlated with it for hardness);
- each field value is mapped through the normal CDF onto the cluster's
  quantile range of the empirical GeoMet distributions (Cu % from
  drillholes.csv, Bond work index computed from the grindability tests in
  comminution.csv; CC BY 4.0, see DATA_LICENSES.md);
- an affine support correction shrinks 2 m assay variability to block scale.
The cluster is dug block by block in a serpentine order away from the face.
"""

import csv
import math
import os
import random
from functools import lru_cache

from . import params as P
from .layout import NODES

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "geomet")


@lru_cache(maxsize=None)
def geomet():
    """Sorted Cu (%) assays and Bond work indices (kWh/t) from the GeoMet dataset."""
    with open(os.path.join(DATA, "drillholes.csv"), newline="") as fh:
        cu = sorted(float(r["Cu ppm"]) / 1e4 for r in csv.DictReader(fh) if r["Cu ppm"])
    bwi = []
    with open(os.path.join(DATA, "comminution.csv"), newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                a, gbp, p80, f80 = float(r["A"]), float(r["M"]), float(r["P80"]), float(r["F80"])
            except (KeyError, ValueError):
                continue
            # Bond ball mill work index (metric): A = closing screen um, M = grindability g/rev
            bwi.append(44.5 / (a ** 0.23 * gbp ** 0.82 * (10 / math.sqrt(p80) - 10 / math.sqrt(f80))) * 1.1023)
    return cu, sorted(bwi)


def quantile(sorted_vals, q):
    pos = min(max(q, 0.0), 1.0) * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


class Block:
    __slots__ = ("i", "j", "x", "y", "grade", "bwi", "tonnes", "remaining")

    def __init__(self, i, j, x, y, grade, bwi, tonnes):
        self.i, self.j, self.x, self.y = i, j, x, y
        self.grade, self.bwi, self.tonnes, self.remaining = grade, bwi, tonnes, tonnes


def _field(rng, nx, ny, kernels=8):
    ks = [(rng.uniform(-1, nx + 1), rng.uniform(-1, ny + 1), rng.uniform(1.5, 4.0), rng.gauss(0, 1))
          for _ in range(kernels)]
    vals = {}
    for i in range(nx):
        for j in range(ny):
            v = sum(w * math.exp(-((i - cx) ** 2 + (j - cy) ** 2) / (2 * s * s)) for cx, cy, s, w in ks)
            vals[(i, j)] = v + 0.25 * rng.gauss(0, 1)
    mean = sum(vals.values()) / len(vals)
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals.values()) / len(vals)) or 1.0
    return {k: (v - mean) / sd for k, v in vals.items()}


class BlockCluster:
    def __init__(self, spec, seed):
        self.id = spec["id"]
        self.spec = spec
        self.rock = spec["rock"]
        rng = random.Random(f"{seed}:blocks:{self.id}")
        nx, ny = spec["nx"], spec["ny"]
        dx, dy, dz, rho = P.BLOCK["dx"], P.BLOCK["dy"], P.BLOCK["dz"], P.BLOCK["density"]
        tonnes = dx * dy * dz * rho

        node = next(e["node"] for e in P.EXCAVATORS if e["id"] == spec["excavator"])
        fx, fy, _ = NODES[node][0]
        norm = math.hypot(fx, fy) or 1.0
        ux, uy = -fx / norm, -fy / norm          # into the pit, away from the wall
        vx, vy = -uy, ux
        self.axes = (ux, uy, vx, vy)
        self.origin = (fx, fy)

        zg = _field(rng, nx, ny)
        zh = _field(rng, nx, ny)
        cu_all, bwi_all = geomet()
        (q0, q1), (h0, h1) = spec["cu_q"], spec["bwi_q"]

        def cu_at(z):
            return quantile(cu_all, q0 + (q1 - q0) * phi(z))

        def bwi_at(z):
            return quantile(bwi_all, h0 + (h1 - h0) * phi(z))

        grid = [(i, j) for j in range(ny) for i in range(nx)]
        raw = {ij: cu_at(zg[ij]) for ij in grid}
        mean = sum(raw.values()) / len(raw)
        self.blocks = []
        for j in range(ny):
            cols = range(nx) if j % 2 == 0 else range(nx - 1, -1, -1)
            for i in cols:
                cx = fx + ux * (15 + (j + 0.5) * dy) + vx * ((i - nx / 2 + 0.5) * dx)
                cy = fy + uy * (15 + (j + 0.5) * dy) + vy * ((i - nx / 2 + 0.5) * dx)
                grade = mean + P.SUPPORT_FACTOR * (raw[(i, j)] - mean)
                bwi = bwi_at(0.5 * zg[(i, j)] + 0.866 * zh[(i, j)])
                self.blocks.append(Block(i, j, round(cx, 1), round(cy, 1), round(max(0.01, grade), 3),
                                         round(bwi, 2), tonnes))
        self.k = 0
        self.cut = 1
        self.mined_t = 0.0
        self.grade_mult = 1.0      # what-if: low-grade zone
        self.bwi_mult = 1.0        # what-if: hardness spike

    @property
    def current(self):
        return self.blocks[self.k]

    @property
    def face_grade(self):
        return self.current.grade * self.grade_mult

    @property
    def face_bwi(self):
        return self.current.bwi * self.bwi_mult

    def remaining_t(self):
        return sum(b.remaining for b in self.blocks[self.k:])

    def dig(self, tonnes):
        """Remove `tonnes` from the face. Returns (tonnes, grade, bwi) of what was dug."""
        got, g_acc, h_acc = 0.0, 0.0, 0.0
        while got < tonnes - 1e-9:
            b = self.blocks[self.k]
            take = min(b.remaining, tonnes - got)
            b.remaining -= take
            got += take
            g_acc += take * b.grade * self.grade_mult
            h_acc += take * b.bwi * self.bwi_mult
            if b.remaining <= 1e-9:
                self.k += 1
                if self.k >= len(self.blocks):     # cluster exhausted: next cut below, same grades
                    self.k = 0
                    self.cut += 1
                    for blk in self.blocks:
                        blk.remaining = blk.tonnes
        self.mined_t += got
        return got, g_acc / got, h_acc / got

    def state(self):
        return {"k": self.k, "cut": self.cut, "mined": self.mined_t,
                "rem": [round(b.remaining, 6) for b in self.blocks]}

    def geometry(self):
        """Static description for the twin's attributes (map drawing)."""
        return {
            "block_size_m": [P.BLOCK["dx"], P.BLOCK["dy"], P.BLOCK["dz"]],
            "density_t_m3": P.BLOCK["density"],
            "axes": [round(a, 4) for a in self.axes],
            "blocks": [[b.x, b.y, b.grade, b.bwi, round(b.tonnes)] for b in self.blocks],
        }
