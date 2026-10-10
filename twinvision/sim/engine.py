r"""Time-stepped simulation of the TwinVision Copper Pit.

One MineSim is one copy of the mine. The service runs two in lockstep with
the same seed: "live" (what-if scenarios applied) and "baseline" (never
touched). Every entity draws from its own seeded random stream, so the two
copies only diverge through the consequences of a scenario.

Material flow (tonnes are conserved and audited every step):

  block cluster --excavator--> truck --> crusher pocket --crusher--> COS --> plant
                                    \--> ROM pad --loader reclaim--> crusher pocket
                                    \--> waste dump
"""

import math
import random
from collections import deque

from . import params as P
from .blocks import BlockCluster
from .roads import RoadNetwork, truck_power_kw, truck_speed_ms


class Ema:
    """Exponentially weighted rate: add() amounts, read .v as amount per hour."""

    def __init__(self, tau):
        self.tau = tau
        self.v = 0.0

    def add(self, amount):
        self.v += amount * 3600.0 / self.tau

    def decay(self, factor):
        self.v *= factor


class Level:
    """Exponentially weighted average of a level (fraction, kW...)."""

    def __init__(self, tau, v=0.0):
        self.tau = tau
        self.v = v

    def update(self, x, dt):
        self.v += (x - self.v) * min(1.0, dt / self.tau)


class Pile:
    """A well-mixed body of broken rock with tonnage-weighted grade and hardness."""

    def __init__(self, tonnes=0.0, grade=0.0, bwi=0.0, capacity=math.inf):
        self.t, self.grade, self.bwi, self.capacity = tonnes, grade, bwi, capacity

    def add(self, t, grade, bwi):
        if t <= 0:
            return
        tot = self.t + t
        self.grade = (self.grade * self.t + grade * t) / tot
        self.bwi = (self.bwi * self.t + bwi * t) / tot
        self.t = tot

    def take(self, t):
        t = min(t, self.t)
        self.t -= t
        if self.t < 1e-9:
            self.t = 0.0
        return t, self.grade, self.bwi

    @property
    def free(self):
        return max(0.0, self.capacity - self.t)


class Truck:
    def __init__(self, idx, seed):
        self.id = f"truck-{idx:02d}"
        self.rng = random.Random(f"{seed}:truck:{self.id}")
        self.state = "parked"            # parked travel queued loading tipping refueling waiting
        self.node = "WS"
        self.seg = None
        self.fwd = True
        self.pos = 0.0
        self.route = []
        self.dest = None                 # (kind, id): excavator/crusher/rom/dump/fuel
        self.eta = 0.0
        self.payload = 0.0
        self.grade = 0.0
        self.bwi = 0.0
        self.material = ""
        self.source = ""
        self.fuel = P.TRUCK["tank_l"] * self.rng.uniform(0.45, 0.95)
        self.driver = self.rng.uniform(0.95, 1.03)   # operator speed factor
        self.release_at = 30.0 + 45.0 * (idx - 1)    # staggered shift start
        self.speed = 0.0
        self.x, self.y = 0.0, 0.0
        self.timer = 0.0
        self.wait_cycle = 0.0
        self.last_wait = 0.0
        self.trips = 0
        self.tonnes = 0.0
        self.fuel_burned = 0.0
        self.retry_at = 0.0
        self.why = ""                     # reason for waiting/parking

    @property
    def mass(self):
        return P.TRUCK["empty_t"] + self.payload

    def from_node(self):
        return self.seg.end(self.fwd) if self.seg is not None else self.node


class Excavator:
    def __init__(self, spec, cluster, seed):
        self.spec = spec
        self.id = spec["id"]
        self.node = spec["node"]
        self.role = spec["role"]
        self.cluster = cluster
        self.rng = random.Random(f"{seed}:excavator:{self.id}")
        self.queue = deque()
        self.current = None
        self.timer = 0.0
        self.down = False
        self.why_down = ""
        self.tonnes = 0.0
        self.passes = 0
        self.rate = Ema(P.KPI_TAU_S)
        self.util = Level(P.KPI_TAU_S)

    def pass_time(self):
        return self.spec["pass_s"] * (1 + 0.03 * (self.cluster.face_bwi - P.REF_BWI))

    def bucket_t(self):
        return self.spec["bucket_t"] * (1 - 0.015 * (self.cluster.face_bwi - P.REF_BWI))

    def load_time_est(self):
        passes = max(1, round(P.TRUCK["payload_t"] / self.bucket_t()))
        return self.spec["spot_s"] + passes * self.pass_time()

    @property
    def state(self):
        if self.down:
            return self.why_down or "down"
        return "loading" if self.current else "waiting_for_truck"


class Crusher:
    def __init__(self):
        self.pocket = Pile(capacity=P.CRUSHER["pocket_t"])
        self.queue = deque()
        self.tipping = None
        self.timer = 0.0
        self.down = False
        self.why_down = ""
        self.state = "idle"
        self.power_kw = 0.0
        self.rate_tph = 0.0
        self.cap_tph = 0.0
        self.tonnes = 0.0
        self.kwh = 0.0
        self.received = 0.0

    def capacity_tph(self, bwi):
        c = P.CRUSHER
        power_cap = (c["installed_kw"] - c["idle_kw"]) / (c["kwh_per_t_per_bwi"] * max(bwi, 1.0))
        return min(c["vol_cap_tph"], power_cap)


class TipPoint:
    """ROM pad, waste dump or fuel bay: several spots, FIFO queue."""

    def __init__(self, spots):
        self.spots = spots
        self.active = []                 # [truck, remaining_s]
        self.queue = deque()


class MineSim:
    def __init__(self, seed=42, suffix=""):
        self.seed = seed
        self.suffix = suffix
        self.t = 0.0
        self.net = RoadNetwork()
        self.clusters = {c["id"]: BlockCluster(c, seed) for c in P.CLUSTERS}
        self.excavators = [Excavator(e, self.clusters[e["cluster"]], seed) for e in P.EXCAVATORS]
        self.ex_by_id = {e.id: e for e in self.excavators}
        self.trucks = [Truck(i + 1, seed) for i in range(P.TRUCK_COUNT)]
        self.crusher = Crusher()
        self.rom = Pile(P.ROM["initial_t"], P.ROM["initial_grade"], P.ROM["initial_bwi"], P.ROM["capacity_t"])
        self.rom_pad = TipPoint(P.ROM["tip_spots"])
        self.cos = Pile(P.COS["initial_t"], P.COS["initial_grade"], P.COS["initial_bwi"], P.COS["capacity_t"])
        self.dump = TipPoint(P.WASTE_DUMP["tip_spots"])
        self.fuel_bay = TipPoint(P.FUEL_BAY["spots"])
        self.loader_tph = 0.0
        self.loader_fuel = 0.0
        self.plant_tph = 0.0
        self.plant_grade = 0.0
        self.plant_kw = 0.0
        self.plant_t = 0.0
        self.plant_cu_t = 0.0
        self.plant_kwh = 0.0
        self.waste_t = 0.0
        self.rom_in_t = 0.0
        self.rom_out_t = 0.0
        self.fuel_dispensed = 0.0
        self.fuel_initial = sum(t.fuel for t in self.trucks)
        self.inventory0 = self.rom.t + self.cos.t
        self.dispatcher = Dispatcher(self)
        self.scenarios = {}
        self.events = deque(maxlen=50)
        self._topo = None
        tau = P.KPI_TAU_S
        self.k = {name: Ema(tau) for name in (
            "dug", "dug_ore", "dug_waste", "to_crusher", "crushed", "plant", "plant_cu", "hauled",
            "fuel", "crusher_kwh", "queue_s", "cycles", "rom_in", "rom_out", "waste")}
        self.avail = Level(tau, 1.0)
        self.queue_now = 0
        for tr in self.trucks:
            tr.x, tr.y = self.net.nodes["WS"][:2]

    # ------------------------------------------------------------------ helpers
    def log(self, text):
        self.events.append((round(self.t), text))

    def burn(self, tr, kw, dt):
        litres = kw * dt / 3600.0 * P.TRUCK["bsfc_l_per_kwh"]
        litres = min(litres, tr.fuel)
        tr.fuel -= litres
        tr.fuel_burned += litres
        self.k["fuel"].add(litres)

    def send(self, tr, dest, route, eta_s):
        tr.dest = dest
        tr.route = route
        tr.eta = self.t + eta_s
        tr.state = "travel" if route else tr.state
        tr.why = ""
        if not route:
            self.arrive(tr)

    def topo_key(self):
        return (self.net.version, self.crusher.down, tuple(e.down for e in self.excavators))

    # ------------------------------------------------------------------ scenarios
    def set_scenarios(self, sc):
        """Apply a normalised scenario dict (see twinvision/sim/scenarios.py)."""
        self.scenarios = sc
        crusher_down = bool(sc.get("crusher_failure"))
        if crusher_down != (self.crusher.down and self.crusher.why_down == "failed"):
            self.crusher.down = crusher_down
            self.crusher.why_down = "failed" if crusher_down else ""
            self.log("Crusher CR-01 failed" if crusher_down else "Crusher CR-01 repaired")
        closed = set(sc.get("road_closure", []))
        for sid, seg in self.net.segments.items():
            if seg.closed != (sid in closed):
                self.net.set_closed(sid, sid in closed)
                self.log(f"Road {sid} ({seg.name}) {'closed' if sid in closed else 'reopened'}")
        broken = set(sc.get("excavator_breakdown", []))
        for ex in self.excavators:
            down = ex.id in broken
            if down != (ex.down and ex.why_down == "failed"):
                ex.down = down
                ex.why_down = "failed" if down else ""
                self.log(f"Excavator {ex.id.upper()} {'broke down' if down else 'back in service'}")
                if down and ex.current is not None:
                    tr, ex.current = ex.current, None
                    self.finish_loading(ex, tr, partial=True)
        for cid, cl in self.clusters.items():
            hs = sc.get("hardness_spike") or {}
            lg = sc.get("low_grade_zone") or {}
            cl.bwi_mult = hs.get("factor", 1.0) if cid in hs.get("clusters", []) else 1.0
            cl.grade_mult = lg.get("factor", 1.0) if cid in lg.get("clusters", []) else 1.0

    # ------------------------------------------------------------------ step
    def step(self, dt):
        self.t += dt
        key = self.topo_key()
        if key != self._topo:
            if self._topo is not None:
                self.dispatcher.replan()
            self._topo = key
        for ex in self.excavators:
            self.step_excavator(ex, dt)
        self.queue_now = 0
        for tr in self.trucks:
            self.step_truck(tr, dt)
        self.step_crusher(dt)
        self.step_tip_points(dt)
        self.step_rom_reclaim(dt)
        self.step_plant(dt)
        self.step_kpis(dt)

    def step_excavator(self, ex, dt):
        ex.util.update(1.0 if ex.current else 0.0, dt)
        if ex.down:
            return
        if ex.current is None and ex.queue:
            tr = ex.queue.popleft()
            ex.current = tr
            tr.state = "loading"
            ex.timer = -ex.spec["spot_s"]
        tr = ex.current
        if tr is None:
            return
        ex.timer += dt
        cap = P.TRUCK["payload_t"]
        while ex.timer >= 0:
            pt = ex.pass_time() * ex.rng.uniform(0.92, 1.10)
            if ex.timer < pt:
                break
            ex.timer -= pt
            want = min(ex.bucket_t() * ex.rng.uniform(0.90, 1.05), cap * 1.05 - tr.payload)
            got, g, h = ex.cluster.dig(want)
            tot = tr.payload + got
            tr.grade = (tr.grade * tr.payload + g * got) / tot
            tr.bwi = (tr.bwi * tr.payload + h * got) / tot
            tr.payload = tot
            ex.tonnes += got
            ex.passes += 1
            ex.rate.add(got)
            self.k["dug"].add(got)
            if cap - tr.payload < 0.5 * ex.bucket_t():
                ex.current = None
                self.finish_loading(ex, tr)
                break

    def finish_loading(self, ex, tr, partial=False):
        tr.material = "ore" if tr.grade >= P.CUTOFF_GRADE else "waste"
        tr.source = ex.id
        self.k["dug_ore" if tr.material == "ore" else "dug_waste"].add(tr.payload)
        if tr.payload <= 0:
            tr.state = "waiting"
            self.dispatcher.assign_empty(tr)
            return
        if partial:
            self.log(f"{tr.id} leaves {ex.id.upper()} part-loaded ({tr.payload:.0f} t)")
        self.dispatcher.route_loaded(tr)

    def step_truck(self, tr, dt):
        st = tr.state
        if st == "parked":
            tr.speed = 0.0
            if self.t >= tr.release_at and self.t >= tr.retry_at:
                self.dispatcher.assign_empty(tr)
            return
        if st == "travel":
            self.move(tr, dt)
            return
        tr.speed = 0.0
        if st in ("queued", "loading", "tipping", "waiting"):
            self.burn(tr, P.TRUCK["idle_kw"], dt)
        if st in ("queued", "waiting"):
            tr.wait_cycle += dt
            self.k["queue_s"].add(dt)
            self.queue_now += 1
        if st == "waiting" and self.t >= tr.retry_at:
            tr.retry_at = self.t + 60.0
            if tr.payload > 0:
                self.dispatcher.route_loaded(tr)
            else:
                self.dispatcher.assign_empty(tr)

    def move(self, tr, dt):
        remaining = dt
        T = P.TRUCK
        while remaining > 1e-9 and tr.route:
            seg, fwd = tr.route[0]
            if tr.seg is not seg:
                if seg.closed or len(seg.occupants) >= seg.capacity:
                    tr.speed = 0.0
                    tr.why = "road closed" if seg.closed else f"road {seg.id} full"
                    tr.wait_cycle += remaining
                    self.k["queue_s"].add(remaining)
                    self.queue_now += 1
                    self.burn(tr, T["idle_kw"], remaining)
                    if seg.closed and self.t >= tr.retry_at:
                        tr.retry_at = self.t + 60.0
                        self.dispatcher.reroute(tr)
                    return
                tr.why = ""
                seg.occupants.add(tr.id)
                tr.seg, tr.fwd, tr.pos = seg, fwd, 0.0
            grade = seg.grade if fwd else -seg.grade
            loaded = tr.payload > 0
            v = truck_speed_ms(tr.mass, grade, seg.limit_kmh, loaded) * tr.driver
            left = seg.length - tr.pos
            used = min(remaining, left / v)
            tr.pos += v * used
            remaining -= used
            tr.speed = v * 3.6
            self.burn(tr, truck_power_kw(tr.mass, grade, v), used)
            if tr.pos >= seg.length - 1e-6:
                seg.occupants.discard(tr.id)
                seg.passes += 1
                seg.speed_sum += v * 3.6
                tr.node = seg.end(fwd)
                tr.seg = None
                tr.route.pop(0)
        if tr.seg is not None:
            tr.x, tr.y = tr.seg.point(tr.pos, tr.fwd)
        else:
            tr.x, tr.y = self.net.nodes[tr.node][:2]
        if not tr.route and tr.state == "travel":
            self.arrive(tr)

    def arrive(self, tr):
        kind = tr.dest[0] if tr.dest else None
        tr.speed = 0.0
        tr.x, tr.y = self.net.nodes[tr.node][:2]
        if kind == "excavator":
            ex = self.ex_by_id[tr.dest[1]]
            if ex.down:
                tr.state = "waiting"
                self.dispatcher.assign_empty(tr)
                return
            ex.queue.append(tr)
            tr.state = "queued"
        elif kind == "crusher":
            if self.crusher.down:
                tr.state = "waiting"
                self.dispatcher.route_loaded(tr)
                return
            self.crusher.queue.append(tr)
            tr.state = "queued"
        elif kind == "rom":
            self.rom_pad.queue.append(tr)
            tr.state = "queued"
        elif kind == "dump":
            self.dump.queue.append(tr)
            tr.state = "queued"
        elif kind == "fuel":
            self.fuel_bay.queue.append(tr)
            tr.state = "queued"
        else:
            tr.state = "parked"

    def after_dump(self, tr, where):
        t = tr.payload
        tr.trips += 1
        tr.tonnes += t
        self.k["hauled"].add(t)
        self.k["cycles"].add(1)
        tr.last_wait = tr.wait_cycle
        tr.wait_cycle = 0.0
        tr.payload, tr.grade, tr.bwi, tr.material = 0.0, 0.0, 0.0, ""
        tr.state = "waiting"
        if tr.fuel < P.TRUCK["tank_l"] * P.TRUCK["refuel_below_frac"]:
            route, eta = self.net.route(tr.node, "WS", False)
            if route is not None:
                self.send(tr, ("fuel", "WS"), route, eta)
                return
        self.dispatcher.assign_empty(tr)

    def step_crusher(self, dt):
        c = self.crusher
        C = P.CRUSHER
        if c.tipping is None and c.queue and not c.down:
            head = c.queue[0]
            if c.pocket.t + head.payload <= c.pocket.capacity + 1e-6:
                c.queue.popleft()
                c.tipping = head
                c.timer = C["tip_s"] * head.rng.uniform(0.9, 1.15)
                head.state = "tipping"
        if c.tipping is not None:
            c.timer -= dt
            if c.timer <= 0:
                tr = c.tipping
                c.tipping = None
                c.pocket.add(tr.payload, tr.grade, tr.bwi)
                c.received += tr.payload
                self.k["to_crusher"].add(tr.payload)
                self.after_dump(tr, "crusher")
        if c.down:
            c.state, c.power_kw, c.rate_tph = c.why_down or "down", 0.0, 0.0
            c.cap_tph = 0.0
            return
        bwi = c.pocket.bwi if c.pocket.t > 0 else P.REF_BWI
        c.cap_tph = c.capacity_tph(bwi)
        feed = min(c.cap_tph * dt / 3600.0, c.pocket.t, self.cos.free)
        if feed > 1e-9:
            t, g, h = c.pocket.take(feed)
            self.cos.add(t, g, h)
            c.tonnes += t
            c.rate_tph = t * 3600.0 / dt
            c.power_kw = C["idle_kw"] + C["kwh_per_t_per_bwi"] * h * c.rate_tph
            c.state = "crushing"
            self.k["crushed"].add(t)
        else:
            c.rate_tph = 0.0
            c.power_kw = C["idle_kw"]
            c.state = "blocked_cos_full" if self.cos.free <= 1e-6 and c.pocket.t > 0 else "starved"
        kwh = c.power_kw * dt / 3600.0
        c.kwh += kwh
        self.k["crusher_kwh"].add(kwh)

    def step_tip_points(self, dt):
        for tp, tip_s, where in ((self.rom_pad, P.ROM["tip_s"], "rom"),
                                 (self.dump, P.WASTE_DUMP["tip_s"], "dump"),
                                 (self.fuel_bay, None, "fuel")):
            while tp.queue and len(tp.active) < tp.spots:
                tr = tp.queue.popleft()
                if where == "fuel":
                    need = P.TRUCK["tank_l"] - tr.fuel
                    dur = P.TRUCK["refuel_fixed_s"] + need / P.TRUCK["refuel_lpm"] * 60.0
                    tr.state = "refueling"
                else:
                    dur = tip_s * tr.rng.uniform(0.9, 1.15)
                    tr.state = "tipping"
                tp.active.append([tr, dur])
            done = []
            for item in tp.active:
                item[1] -= dt
                if item[1] <= 0:
                    done.append(item)
            for item in done:
                tp.active.remove(item)
                tr = item[0]
                if where == "rom":
                    self.rom.add(tr.payload, tr.grade, tr.bwi)
                    self.rom_in_t += tr.payload
                    self.k["rom_in"].add(tr.payload)
                    self.after_dump(tr, "rom")
                elif where == "dump":
                    self.waste_t += tr.payload
                    self.k["waste"].add(tr.payload)
                    self.after_dump(tr, "dump")
                else:
                    litres = P.TRUCK["tank_l"] - tr.fuel
                    tr.fuel += litres
                    self.fuel_dispensed += litres
                    tr.state = "waiting"
                    self.dispatcher.assign_empty(tr)

    def step_rom_reclaim(self, dt):
        c = self.crusher
        D = P.DISPATCH
        self.loader_tph = 0.0
        busy = c.tipping is not None or bool(c.queue)
        if (c.down or busy or self.rom.t <= 0
                or c.pocket.t > 0.35 * c.pocket.capacity
                or self.cos.t > D["cos_reclaim_frac"] * self.cos.capacity):
            return
        t, g, h = self.rom.take(min(P.ROM["reclaim_tph"] * dt / 3600.0, c.pocket.free))
        if t <= 0:
            return
        c.pocket.add(t, g, h)
        self.rom_out_t += t
        self.k["rom_out"].add(t)
        self.loader_tph = t * 3600.0 / dt
        self.loader_fuel += P.ROM["loader_l_per_h"] * dt / 3600.0

    def step_plant(self, dt):
        L = P.PLANT
        t, g, h = self.cos.take(L["target_tph"] * dt / 3600.0)
        self.plant_tph = t * 3600.0 / dt
        self.plant_grade = g if t > 0 else 0.0
        self.plant_t += t
        self.plant_cu_t += t * g / 100.0
        self.plant_kw = L["base_kw"] + L["kwh_per_t_per_bwi"] * h * self.plant_tph
        self.plant_kwh += self.plant_kw * dt / 3600.0
        self.k["plant"].add(t)
        self.k["plant_cu"].add(t * g)

    def step_kpis(self, dt):
        f = math.exp(-dt / P.KPI_TAU_S)
        for e in self.k.values():
            e.decay(f)
        for ex in self.excavators:
            ex.rate.decay(f)
        for e in self.dispatcher.assigned.values():
            e.decay(f)
        units = len(self.excavators) + len(self.trucks) + 1
        up = sum(not e.down for e in self.excavators) + len(self.trucks) + (not self.crusher.down)
        self.avail.update(up / units, dt)

    # ------------------------------------------------------------------ audits
    def mass_balance(self):
        """Tonnes dug + opening stocks - (stocks now + in trucks + waste dumped + plant feed)."""
        dug = sum(ex.tonnes for ex in self.excavators)
        in_trucks = sum(tr.payload for tr in self.trucks)
        stocks = self.rom.t + self.cos.t + self.crusher.pocket.t
        return dug + self.inventory0 - (in_trucks + stocks + self.waste_t + self.plant_t)

    def fuel_balance(self):
        tanks = sum(tr.fuel for tr in self.trucks)
        burned = sum(tr.fuel_burned for tr in self.trucks)
        return self.fuel_initial + self.fuel_dispensed - burned - tanks

    def kpis(self):
        k = self.k
        return {
            "tph_mined": k["dug"].v,
            "tph_ore": k["dug_ore"].v,
            "tph_waste": k["dug_waste"].v,
            "tph_crushed": k["crushed"].v,
            "plant_feed_tph": k["plant"].v,
            "plant_feed_grade": k["plant_cu"].v / k["plant"].v if k["plant"].v > 1e-6 else 0.0,
            "mean_queue_min": k["queue_s"].v / k["cycles"].v / 60.0 if k["cycles"].v > 1e-6 else 0.0,
            "availability_pct": 100.0 * self.avail.v,
            "crusher_kwh_t": k["crusher_kwh"].v / k["crushed"].v if k["crushed"].v > 1e-6 else 0.0,
            "diesel_l_t": k["fuel"].v / k["hauled"].v if k["hauled"].v > 1e-6 else 0.0,
            "diesel_lph": k["fuel"].v,
        }


class Dispatcher:
    """Assigns empty trucks to excavators and loaded trucks to destinations.

    Empty truck: pick the excavator with the lowest score
        expected loading start time - deficit_weight x (target - actual rate) / target
    where the expected start accounts for travel time and for the trucks
    already queued at or driving to that excavator. Loaded truck: waste goes to
    the dump; ore goes to the crusher unless it is down, its expected wait is
    too long, or the coarse ore stockpile is near full; then to the ROM pad.
    """

    def __init__(self, sim):
        self.sim = sim
        self.assigned = {ex.id: Ema(P.KPI_TAU_S) for ex in sim.excavators}
        self.decisions = 0
        self.reroutes = 0
        self.to_rom = 0
        self.last = ""

    def note(self, text):
        self.decisions += 1
        self.last = text

    def ex_free_at(self, ex, before, exclude):
        sim = self.sim
        free = sim.t
        if ex.current is not None:
            done = ex.current.payload / P.TRUCK["payload_t"]
            free += max(0.0, (1 - done) * ex.load_time_est())
        free += len(ex.queue) * ex.load_time_est()
        coming = sorted(tr.eta for tr in sim.trucks
                        if tr is not exclude and tr.state == "travel" and tr.dest == ("excavator", ex.id)
                        and tr.eta <= before)
        for eta in coming:
            free = max(free, eta) + ex.load_time_est()
        return free

    def assign_empty(self, tr):
        sim = self.sim
        src = tr.from_node()
        best = None
        for ex in sim.excavators:
            if ex.down:
                continue
            route, t_travel = sim.net.route(src, ex.node, False)
            if route is None:
                continue
            arrival = sim.t + t_travel
            start = max(arrival, self.ex_free_at(ex, arrival, tr))
            target = P.DISPATCH["target_tph"][ex.id]
            deficit = (target - self.assigned[ex.id].v) / target
            score = start - sim.t - P.DISPATCH["deficit_weight_s"] * deficit
            if best is None or score < best[0] - 1e-9:
                best = (score, ex, route, t_travel)
        if best is None:
            tr.state = "waiting" if tr.state != "parked" else "parked"
            tr.why = "no reachable excavator"
            tr.retry_at = sim.t + 60.0
            return
        _, ex, route, t_travel = best
        self.assigned[ex.id].add(P.TRUCK["payload_t"])
        self.note(f"{tr.id} -> {ex.id}")
        self._go(tr, ("excavator", ex.id), route, t_travel)

    def crusher_wait_est(self, arrival):
        sim = self.sim
        c = sim.crusher
        ahead = len(c.queue) + (1 if c.tipping else 0)
        ahead += sum(1 for t in sim.trucks if t.state == "travel" and t.dest == ("crusher", "CR") and t.eta <= arrival)
        rate = max(c.cap_tph, 1.0)
        backlog_t = c.pocket.t + ahead * P.TRUCK["payload_t"] - c.pocket.capacity
        wait = ahead * P.CRUSHER["tip_s"] + max(0.0, backlog_t) / rate * 3600.0
        return max(0.0, wait - (arrival - sim.t))

    def route_loaded(self, tr):
        sim = self.sim
        src = tr.from_node()
        D = P.DISPATCH
        if tr.material == "waste":
            options = [("dump", "DUMP")]
            reason = "waste"
        else:
            r_cr, t_cr = sim.net.route(src, "CR", True)
            crusher_ok = not sim.crusher.down and r_cr is not None
            cos_high = sim.cos.t > D["cos_high_frac"] * sim.cos.capacity
            wait = self.crusher_wait_est(sim.t + t_cr) if crusher_ok else math.inf
            if crusher_ok and not cos_high and wait <= D["crusher_wait_max_s"]:
                options, reason = [("crusher", "CR"), ("rom", "ROM")], "ore to crusher"
            else:
                why = "crusher down" if sim.crusher.down else ("COS high" if cos_high else
                      ("crusher cut off" if r_cr is None else f"crusher wait {wait / 60:.0f} min"))
                options, reason = [("rom", "ROM"), ("crusher", "CR")], f"ore to ROM ({why})"
        for kind, node in options:
            route, t = sim.net.route(src, node, True)
            if route is not None:
                if kind == "rom":
                    self.to_rom += 1
                self.note(f"{tr.id} {reason}" if kind == options[0][0] else f"{tr.id} {kind} (fallback)")
                self._go(tr, (kind, node), route, t)
                return
        tr.state = "waiting"
        tr.why = "no route for loaded truck"
        tr.retry_at = sim.t + 60.0

    def _go(self, tr, dest, route, t):
        sim = self.sim
        # Keep the segment the truck is driving on; the new route starts at its end.
        if tr.seg is not None and tr.route and tr.route[0][0] is tr.seg:
            route = [tr.route[0]] + route
        sim.send(tr, dest, route, t)

    def reroute(self, tr):
        """Re-plan one travelling truck after a road closure or equipment change."""
        if tr.payload > 0:
            self.route_loaded(tr)
        else:
            self.assign_empty(tr)
        self.reroutes += 1

    def replan(self):
        sim = self.sim
        for tr in sim.trucks:
            kind = tr.dest[0] if tr.dest else None
            if tr.state == "travel":
                upcoming = tr.route[1:] if tr.seg is not None else tr.route
                blocked = any(seg.closed for seg, _ in upcoming)
                bad_dest = (kind == "excavator" and sim.ex_by_id[tr.dest[1]].down) or \
                           (kind == "crusher" and sim.crusher.down)
                if blocked or bad_dest:
                    self.reroute(tr)
            elif tr.state == "queued":
                if kind == "excavator" and sim.ex_by_id[tr.dest[1]].down:
                    sim.ex_by_id[tr.dest[1]].queue.remove(tr)
                    tr.state = "waiting"
                    self.reroute(tr)
                elif kind == "crusher" and sim.crusher.down:
                    sim.crusher.queue.remove(tr)
                    tr.state = "waiting"
                    self.reroute(tr)
            elif tr.state == "waiting":
                tr.retry_at = sim.t
