"""Block model: one cluster of blocks in front of every excavator face.

Grades and hardness are synthetic but spatially correlated: a seeded sum of
Gaussian kernels gives a smooth field, mapped to a lognormal grade
distribution (typical of porphyry copper) with the cluster's mean and
standard deviation. Bond work index is partly correlated with grade. The
cluster is dug block by block in a serpentine order away from the face.
"""

import math
import random

from . import params as P
from .layout import NODES


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
        cv = spec["grade_sd"] / spec["grade_mean"]
        s_log = math.sqrt(math.log(1 + cv * cv))
        self.blocks = []
        for j in range(ny):
            cols = range(nx) if j % 2 == 0 else range(nx - 1, -1, -1)
            for i in cols:
                cx = fx + ux * (15 + (j + 0.5) * dy) + vx * ((i - nx / 2 + 0.5) * dx)
                cy = fy + uy * (15 + (j + 0.5) * dy) + vy * ((i - nx / 2 + 0.5) * dx)
                grade = spec["grade_mean"] * math.exp(s_log * zg[(i, j)] - s_log * s_log / 2)
                bwi = spec["bwi_mean"] + spec["bwi_sd"] * (0.5 * zg[(i, j)] + 0.866 * zh[(i, j)])
                self.blocks.append(Block(i, j, round(cx, 1), round(cy, 1), round(grade, 3),
                                         round(max(8.0, bwi), 2), tonnes))
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
