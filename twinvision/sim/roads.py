"""Haul road graph: segments with length, grade, speed limit and capacity."""

import heapq
import math

from . import params as P
from .layout import NODES, SEGMENTS


class Segment:
    def __init__(self, sid, name, a, b, pts, limit_kmh, capacity):
        self.id, self.name, self.a, self.b = sid, name, a, b
        self.pts = pts
        self.cum = [0.0]
        for p, q in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
        self.length = self.cum[-1]
        self.grade = (pts[-1][2] - pts[0][2]) / self.length     # rise from a to b
        self.limit_kmh = float(limit_kmh)
        self.capacity = capacity
        self.closed = False
        self.occupants = set()
        self.passes = 0
        self.speed_sum = 0.0       # for mean speed of trucks that drove it

    def end(self, forward):
        return self.b if forward else self.a

    def point(self, dist, forward):
        """(x, y) at `dist` metres from the start of travel."""
        d = dist if forward else self.length - dist
        d = min(max(d, 0.0), self.length)
        for k in range(1, len(self.cum)):
            if self.cum[k] >= d:
                p, q = self.pts[k - 1], self.pts[k]
                seg = self.cum[k] - self.cum[k - 1] or 1.0
                t = (d - self.cum[k - 1]) / seg
                return p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        return self.pts[-1][0], self.pts[-1][1]


def truck_speed_ms(mass_t, grade, limit_kmh, loaded):
    """Steady speed of a Cat 793F-class truck on a grade (rimpull/power limited)."""
    T = P.TRUCK
    eff = grade + T["rolling_res"]
    vmax = (T["vmax_loaded_kmh"] if loaded else T["vmax_empty_kmh"]) / 3.6
    if eff > 0.004:
        v = T["net_power_kw"] * 1000 * T["drive_eff"] / (mass_t * 1000 * P.G * eff)
    else:
        v = vmax
    if grade < -0.03:      # retarder-limited descent
        v = min(v, (T["downhill_loaded_kmh"] if loaded else T["downhill_empty_kmh"]) / 3.6)
    return max(1.0, min(v, vmax, limit_kmh / 3.6))


def truck_power_kw(mass_t, grade, v_ms):
    """Engine power drawn to hold speed v on a grade (idle power when retarding)."""
    T = P.TRUCK
    force = mass_t * 1000 * P.G * (grade + T["rolling_res"])
    if force <= 0:
        return T["idle_kw"]
    return min(T["net_power_kw"], force * v_ms / 1000 / T["drive_eff"] + T["idle_kw"])


class RoadNetwork:
    def __init__(self):
        self.nodes = {k: v[0] for k, v in NODES.items()}
        self.segments = {}
        self.adj = {k: [] for k in self.nodes}
        for sid, name, a, b, pts, limit, cap in SEGMENTS:
            seg = Segment(sid, name, a, b, pts, limit, cap)
            self.segments[sid] = seg
            self.adj[a].append((seg, True))
            self.adj[b].append((seg, False))
        self.version = 0
        self._cache = {}

    def set_closed(self, sid, closed):
        seg = self.segments[sid]
        if seg.closed != closed:
            seg.closed = closed
            self.version += 1
            self._cache.clear()

    def travel_time(self, seg, forward, loaded):
        mass = P.TRUCK["empty_t"] + (P.TRUCK["payload_t"] if loaded else 0.0)
        g = seg.grade if forward else -seg.grade
        return seg.length / truck_speed_ms(mass, g, seg.limit_kmh, loaded)

    def route(self, src, dst, loaded):
        """Fastest open route as [(segment, forward), ...] and its time in s; (None, inf) if cut off."""
        key = (src, dst, loaded)
        if key in self._cache:
            r, t = self._cache[key]
            return list(r) if r is not None else None, t
        dist = {src: 0.0}
        prev = {}
        heap = [(0.0, src)]
        while heap:
            d, n = heapq.heappop(heap)
            if n == dst:
                break
            if d > dist.get(n, math.inf):
                continue
            for seg, fwd in sorted(self.adj[n], key=lambda sf: sf[0].id):
                if seg.closed:
                    continue
                m = seg.end(fwd)
                nd = d + self.travel_time(seg, fwd, loaded)
                if nd < dist.get(m, math.inf) - 1e-9:
                    dist[m] = nd
                    prev[m] = (n, seg, fwd)
                    heapq.heappush(heap, (nd, m))
        if dst not in dist:
            self._cache[key] = (None, math.inf)
            return None, math.inf
        path = []
        n = dst
        while n != src:
            p, seg, fwd = prev[n]
            path.append((seg, fwd))
            n = p
        path.reverse()
        self._cache[key] = (tuple(path), dist[dst])
        return path, dist[dst]
