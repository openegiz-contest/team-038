r"""Time-stepped simulation of the TwinVision Copper Pit.

One MineSim is one copy of the mine. The service runs two in lockstep with
the same seed: "live" (what-if scenarios applied) and "baseline" (never
touched). Every entity draws from its own seeded random streams, so the two
copies only diverge through the consequences of a scenario.

Material flow (tonnes are conserved and audited every step):

  block cluster --excavator--> truck --> crusher pocket --crusher--> COS --> plant
                                    +--> ROM pad --loader reclaim--> crusher pocket
                                    +--> waste dump

Linked processes: dispatcher (truck allocation, ore routing, grade blending,
fuel rationing), condition-based maintenance (wear, hazard, failures,
planned maintenance), diesel supply at the fuel bay, electrical demand and
the grid cap.
"""

import copy
import math
import random
from collections import deque

from . import params as P
from .blocks import BlockCluster
from .roads import RoadNetwork, truck_power_kw, truck_speed_ms


MODULES = ("dispatcher", "maintenance", "energy")


def record(log, t, text):
    """Append to a module decision log (newest last)."""
    log.append((round(t), text))


class Ema:
    """Exponentially weighted rate: add() amounts, read .v as amount per hour.

    Bias-corrected for the start-up (divided by the weight accumulated so far),
    so the rate is right after a few minutes instead of ramping up over tau.
    The weight is floored at 0.25 to keep the first minutes from jumping with
    every single truck load."""

    def __init__(self, tau):
        self.tau = tau
        self.raw = 0.0
        self.w = 0.0

    def add(self, amount):
        self.raw += amount * 3600.0 / self.tau

    def decay(self, factor):
        self.raw *= factor
        self.w = self.w * factor + (1.0 - factor)

    @property
    def v(self):
        return self.raw / max(self.w, 0.25)


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
        return t, self.grade, self.bwi

    @property
    def free(self):
        return max(0.0, self.capacity - self.t)


class Health:
    """Condition of one machine: wear, failure hazard, breakdowns and planned maintenance.

    A failure happens when the cumulative hazard reaches an Exp(1) budget drawn
    from the machine's own random stream (so identical use gives identical
    failures in the live and baseline copies).
    """

    def __init__(self, kind, key, seed):
        self.kind = kind
        self.p = P.MAINT[kind]
        self.rng = random.Random(f"{seed}:health:{key}")
        self.h = self.rng.uniform(45.0, 95.0)
        self.cum = 0.0
        self.budget = self.rng.expovariate(1.0)
        self.broken_until = -1.0
        self.pm_until = -1.0
        self.pm_pending = False
        self.failures = 0
        self.pms = 0

    def hazard(self):
        return self.p["lambda0_ph"] * math.exp(self.p["k"] * (1.0 - self.h / 100.0))

    def p_fail(self, hours):
        return 1.0 - math.exp(-self.hazard() * hours)

    def broken(self, t):
        return t < self.broken_until

    def in_pm(self, t):
        return t < self.pm_until

    def use(self, dt, intensity):
        """Wear and hazard for dt seconds of work. True if the machine fails now."""
        if intensity <= 0:
            return False
        self.h = max(0.0, self.h - self.p["wear_ph"] * intensity * dt / 3600.0)
        self.cum += self.hazard() * intensity * dt / 3600.0
        if self.cum >= self.budget:
            self.cum = 0.0
            self.budget = self.rng.expovariate(1.0)
            return True
        return False

    def fail(self, t):
        self.failures += 1
        self.pm_pending = False
        self.broken_until = t + self.p["mttr_h"] * 3600.0 * self.rng.lognormvariate(0.0, 0.3)

    def start_pm(self, t):
        self.pm_pending = False
        self.pms += 1
        self.pm_until = t + self.p["pm_h"] * 3600.0

    def update(self, t):
        """Finish repairs and maintenance. Returns 'repaired', 'pm_done' or None."""
        if self.broken_until > 0 and t >= self.broken_until:
            self.broken_until = -1.0
            self.h = max(self.h, 70.0)        # the failed component is replaced
            return "repaired"
        if self.pm_until > 0 and t >= self.pm_until:
            self.pm_until = -1.0
            self.h = 98.0
            return "pm_done"
        return None


class Truck:
    def __init__(self, idx, seed):
        self.id = f"truck-{idx:02d}"
        self.rng = random.Random(f"{seed}:truck:{self.id}")
        self.health = Health("truck", self.id, seed)
        self.state = "parked"   # parked travel queued loading tipping refueling waiting broken maintenance
        self.node = "WS"
        self.seg = None
        self.fwd = True
        self.pos = 0.0
        self.route = []
        self.dest = None        # (kind, node/id): excavator crusher rom dump fuel workshop park
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
        self.wait_cycle = 0.0
        self.last_wait = 0.0
        self.trips = 0
        self.tonnes = 0.0
        self.fuel_burned = 0.0
        self.retry_at = 0.0
        self.why = ""            # reason for waiting/parking
        self.resume = None       # state to return to after a breakdown

    @property
    def mass(self):
        return P.TRUCK["empty_t"] + self.payload

    def from_node(self):
        return self.seg.end(self.fwd) if self.seg is not None else self.node

    @property
    def available(self):
        return self.state not in ("broken", "maintenance")


class Machine:
    """Common down-state logic of excavators and the crusher."""

    forced = False           # what-if scenario
    health = None
    why_down = ""

    def is_down(self, t):
        return self.forced or self.health.broken(t) or self.health.in_pm(t)

    def why(self, t):
        if self.forced:
            return "failed"
        if self.health.broken(t):
            return "breakdown"
        if self.health.in_pm(t):
            return "maintenance"
        return ""


class Excavator(Machine):
    def __init__(self, spec, cluster, seed):
        self.spec = spec
        self.id = spec["id"]
        self.node = spec["node"]
        self.role = spec["role"]
        self.cluster = cluster
        self.rng = random.Random(f"{seed}:excavator:{self.id}")
        self.health = Health("excavator", self.id, seed)
        self.queue = deque()
        self.current = None
        self.timer = 0.0
        self.down = False
        self.tonnes = 0.0
        self.passes = 0
        self.rate = Ema(P.KPI_TAU_S)
        self.util = Level(P.KPI_TAU_S)

    @property
    def state(self):
        if self.down:
            return self.why_down or "failed"
        return "loading" if self.current else "waiting_for_truck"

    def pass_time(self):
        return self.spec["pass_s"] * (1 + 0.03 * (self.cluster.face_bwi - P.REF_BWI))

    def bucket_t(self):
        return self.spec["bucket_t"] * (1 - 0.015 * (self.cluster.face_bwi - P.REF_BWI))

    def load_time_est(self):
        passes = max(1, round(P.TRUCK["payload_t"] / self.bucket_t()))
        return self.spec["spot_s"] + passes * self.pass_time()


class Crusher(Machine):
    id = "crusher-01"

    def __init__(self, seed):
        self.health = Health("crusher", self.id, seed)
        self.pocket = Pile(capacity=P.CRUSHER["pocket_t"])
        self.queue = deque()
        self.tipping = None
        self.timer = 0.0
        self.down = False
        self.state = "starved"
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
    """ROM pad, waste dump, fuel bay or workshop: several spots, FIFO queue."""

    def __init__(self, spots):
        self.spots = spots
        self.active = []          # [truck, remaining_s]
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
        self.truck_by_id = {t.id: t for t in self.trucks}
        self.crusher = Crusher(seed)
        self.rom = Pile(P.ROM["initial_t"], P.ROM["initial_grade"], P.ROM["initial_bwi"], P.ROM["capacity_t"])
        self.rom_pad = TipPoint(P.ROM["tip_spots"])
        self.cos = Pile(P.COS["initial_t"], P.COS["initial_grade"], P.COS["initial_bwi"], P.COS["capacity_t"])
        self.dump = TipPoint(P.WASTE_DUMP["tip_spots"])
        self.fuel_bay = TipPoint(P.FUEL_BAY["spots"])
        self.workshop = TipPoint(P.WORKSHOP["bays"])
        # process state
        self.loader_tph = 0.0
        self.plant_tph = 0.0
        self.plant_grade = 0.0
        self.plant_kw = 0.0
        self.plant_cap_tph = P.PLANT["target_tph"]
        self.plant_t = 0.0
        self.plant_cu_t = 0.0
        self.copper_t = 0.0
        self.waste_t = 0.0
        self.rom_in_t = 0.0
        self.rom_out_t = 0.0
        # diesel
        self.bay_l = P.FUEL_BAY["initial_l"]
        self.fuel_dispensed = 0.0
        self.fuel_resupplied = 0.0
        self.fuel_written_off = 0.0
        self.fuel_starved_h = 0.0
        self.loader_fuel = 0.0
        self.fuel_initial = sum(t.fuel for t in self.trucks) + self.bay_l
        # electricity
        self.site_kw = 0.0
        self.site_kwh = 0.0
        self.win_kwh = 0.0
        self.win_s = 0.0
        self.demand_kw = 0.0
        self.peak_kw = 0.0
        # audit
        self.inventory0 = self.rom.t + self.cos.t
        self.crusher_fixed_kwh = 0.0     # energy audit: fixed loads ...
        self.crusher_bond_kwh = 0.0      # ... and specific energy x tonnes processed
        self.plant_fixed_kwh = 0.0
        self.plant_bond_kwh = 0.0
        self.plant_kwh = 0.0
        self.modules = {m: True for m in MODULES}
        self.cap_since = 0.0
        self.dispatcher = Dispatcher(self)
        self.maintenance = MaintenancePlanner(self)
        self.energy = EnergyManager(self)
        self.scenarios = {}
        self.events = deque(maxlen=40)
        self._topo = None
        tau = P.KPI_TAU_S
        self.k = {name: Ema(tau) for name in (
            "dug", "dug_ore", "dug_waste", "to_crusher", "crushed", "plant", "plant_cu", "copper", "hauled",
            "fuel", "crusher_kwh", "site_kwh", "queue_s", "cycles", "rom_in", "rom_out", "waste")}
        self.avail = Level(tau, 1.0)
        for tr in self.trucks:
            tr.x, tr.y = self.net.nodes["WS"][:2]

    # ------------------------------------------------------------------ helpers
    def log(self, text):
        self.events.append((round(self.t), text))

    def burn(self, tr, kw, dt):
        litres = min(kw * dt / 3600.0 * P.TRUCK["bsfc_l_per_kwh"], tr.fuel)
        tr.fuel -= litres
        tr.fuel_burned += litres
        self.k["fuel"].add(litres)

    def send(self, tr, dest, route, eta_s):
        tr.dest = dest
        tr.route = route
        tr.eta = self.t + eta_s
        tr.why = ""
        if route:
            tr.state = "travel"
        else:
            self.arrive(tr)

    def resupply_lph(self):
        fs = self.scenarios.get("fuel_shortage")
        return fs["resupply_lph"] if fs else P.FUEL_BAY["resupply_lph"]

    def energy_cap_kw(self):
        ec = self.scenarios.get("energy_cap")
        return ec["cap_kw"] if ec else math.inf

    def topo_key(self):
        return (self.net.version, self.crusher.down, tuple(e.down for e in self.excavators))

    # ------------------------------------------------------------------ scenarios
    def set_scenarios(self, sc):
        """Apply a normalised scenario dict (see twinvision/sim/scenarios.py)."""
        old = self.scenarios
        self.scenarios = sc
        forced = bool(sc.get("crusher_failure"))
        if forced != self.crusher.forced:
            self.crusher.forced = forced
            self.log("Crusher CR-01 failed (what-if)" if forced else "Crusher CR-01 repaired (what-if undone)")
        closed = set(sc.get("road_closure", []))
        for sid, seg in self.net.segments.items():
            if seg.closed != (sid in closed):
                self.net.set_closed(sid, sid in closed)
                self.log(f"Road {sid} ({seg.name}) {'closed' if sid in closed else 'reopened'}")
        broken = set(sc.get("excavator_breakdown", []))
        for ex in self.excavators:
            if (ex.id in broken) != ex.forced:
                ex.forced = ex.id in broken
                self.log(f"Excavator {ex.id.upper()} {'broke down (what-if)' if ex.forced else 'back in service'}")
        hs = sc.get("hardness_spike") or {}
        lg = sc.get("low_grade_zone") or {}
        for cid, cl in self.clusters.items():
            cl.bwi_mult = hs.get("factor", 1.0) if cid in hs.get("clusters", []) else 1.0
            cl.grade_mult = lg.get("factor", 1.0) if cid in lg.get("clusters", []) else 1.0
        if bool(hs) != bool(old.get("hardness_spike")) or hs != old.get("hardness_spike", {}):
            self.log(f"Ore hardness x{hs['factor']:.2f} in {', '.join(hs['clusters'])}" if hs else "Ore hardness back to normal")
        if lg != old.get("low_grade_zone", {}):
            self.log(f"Low-grade zone x{lg['factor']:.2f} in {', '.join(lg['clusters'])}" if lg else "Low-grade zone passed")
        fs, ofs = sc.get("fuel_shortage"), old.get("fuel_shortage")
        if fs and not ofs:
            lost = max(0.0, self.bay_l - fs["bay_stock_l"])
            self.bay_l -= lost
            self.fuel_written_off += lost
            self.log(f"Diesel supply cut to {fs['resupply_lph']:.0f} L/h, fuel bay stock {self.bay_l:,.0f} L")
        elif ofs and not fs:
            self.log("Diesel supply restored")
        off = set(sc.get("modules_off", []))
        for m in MODULES:
            on = m not in off
            if on != self.modules[m]:
                self.modules[m] = on
                self.log(f"{m.capitalize()} module switched {'on' if on else 'off'}")
                if m == "maintenance" and not on:
                    self.maintenance.crusher_prep = False
                    for u in [self.crusher, *self.excavators, *self.trucks]:
                        u.health.pm_pending = False
        ec, oec = sc.get("energy_cap"), old.get("energy_cap")
        if ec != oec:
            self.cap_since = self.t
            self.log(f"Grid cap {ec['cap_kw']:,.0f} kW (peak tariff window)" if ec else "Grid cap lifted")

    def resync_from(self, other):
        """Make this copy an exact copy of `other` (state, queues, wear, random streams); keep the name."""
        suffix = self.suffix
        self.__dict__.update(copy.deepcopy(other).__dict__)
        for part in (self.dispatcher, self.maintenance, self.energy):
            part.sim = self
        self.suffix = suffix

    # ------------------------------------------------------------------ step
    def step(self, dt):
        self.t += dt
        self.update_machines()
        key = self.topo_key()
        if key != self._topo:
            if self._topo is not None:
                self.dispatcher.replan()
            self._topo = key
        self.maintenance.step()
        self.dispatcher.step()
        for ex in self.excavators:
            self.step_excavator(ex, dt)
        for tr in self.trucks:
            self.step_truck(tr, dt)
        self.step_crusher(dt)
        self.step_tip_points(dt)
        self.step_rom_reclaim(dt)
        self.step_plant(dt)
        self.step_fuel_bay(dt)
        self.step_energy(dt)
        self.step_kpis(dt)

    def update_machines(self):
        t = self.t
        for m in [*self.excavators, self.crusher]:
            ev = m.health.update(t)
            if ev:
                self.log(f"{m.id.upper()} {'repaired' if ev == 'repaired' else 'maintenance done'}")
            m.down = m.is_down(t)
            m.why_down = m.why(t)

    def step_excavator(self, ex, dt):
        ex.util.update(1.0 if ex.current else 0.0, dt)
        if ex.down:
            if ex.current is not None:
                tr, ex.current = ex.current, None
                self.finish_loading(ex, tr, partial=True)
            return
        if ex.health.pm_pending and ex.current is None:
            ex.health.start_pm(self.t)
            ex.down = True
            self.log(f"{ex.id.upper()} planned maintenance started ({ex.health.p['pm_h']:.0f} h)")
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
        if ex.health.use(dt, ex.cluster.face_bwi / P.REF_BWI):
            ex.health.fail(self.t)
            ex.down = True
            self.log(f"{ex.id.upper()} breakdown (health {ex.health.h:.0f}%)")
            return
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
        if tr.payload <= 0:
            tr.state = "waiting"
            self.dispatcher.assign_empty(tr)
            return
        self.k["dug_ore" if tr.material == "ore" else "dug_waste"].add(tr.payload)
        if partial:
            self.log(f"{tr.id} leaves {ex.id.upper()} part-loaded ({tr.payload:.0f} t)")
        self.dispatcher.route_loaded(tr)

    def step_truck(self, tr, dt):
        st = tr.state
        if st == "broken":
            tr.speed = 0.0
            if tr.health.update(self.t) == "repaired":
                self.log(f"{tr.id} repaired")
                tr.state = tr.resume or "travel"
                tr.resume = None
            return
        if st == "parked":
            tr.speed = 0.0
            if self.t >= tr.release_at and self.t >= tr.retry_at:
                tr.retry_at = self.t + 300.0
                d = self.dispatcher
                if tr.why == "fuel rationing" and d.rationed() <= d.ration:
                    return
                tr.why = ""
                self.dispatcher.assign_empty(tr)
            return
        if st == "travel":
            self.move(tr, dt)
            return
        tr.speed = 0.0
        if st in ("queued", "loading", "tipping", "waiting"):
            self.burn(tr, P.TRUCK["idle_kw"], dt)
        if st in ("queued", "waiting"):
            if tr in self.fuel_bay.queue and self.bay_out():
                self.fuel_starved_h += dt / 3600.0      # stock-out, not haul congestion
            else:
                tr.wait_cycle += dt
                self.k["queue_s"].add(dt)
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
                    self.burn(tr, T["idle_kw"], remaining)
                    if seg.closed and self.t >= tr.retry_at:
                        tr.retry_at = self.t + 60.0
                        self.dispatcher.reroute(tr)
                    return
                tr.why = ""
                seg.occupants.add(tr.id)
                tr.seg, tr.fwd, tr.pos = seg, fwd, 0.0
            if tr.fuel <= 0:
                tr.speed = 0.0
                tr.why = "out of fuel"
                return
            grade = seg.grade if fwd else -seg.grade
            v = truck_speed_ms(tr.mass, grade, seg.limit_kmh, tr.payload > 0) * tr.driver
            used = min(remaining, (seg.length - tr.pos) / v)
            tr.pos += v * used
            remaining -= used
            tr.speed = v * 3.6
            self.burn(tr, truck_power_kw(tr.mass, grade, v), used)
            if tr.health.use(used, 1.0 + 0.5 * tr.payload / T["payload_t"]):
                tr.health.fail(self.t)
                tr.resume = "travel"
                tr.state = "broken"
                tr.speed = 0.0
                tr.x, tr.y = seg.point(tr.pos, fwd)
                self.log(f"{tr.id} breakdown on road {seg.id} (health {tr.health.h:.0f}%)")
                return
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
        tr.state = "queued"
        if kind == "excavator":
            ex = self.ex_by_id[tr.dest[1]]
            if ex.down:
                tr.state = "waiting"
                self.dispatcher.assign_empty(tr)
                return
            ex.queue.append(tr)
        elif kind == "crusher":
            if self.crusher.down:
                tr.state = "waiting"
                self.dispatcher.route_loaded(tr)
                return
            self.crusher.queue.append(tr)
        elif kind == "rom":
            self.rom_pad.queue.append(tr)
        elif kind == "dump":
            self.dump.queue.append(tr)
        elif kind == "fuel":
            self.fuel_bay.queue.append(tr)
        elif kind == "workshop":
            self.workshop.queue.append(tr)
        else:
            tr.state = "parked"
            tr.retry_at = self.t + 300.0

    def after_dump(self, tr):
        t = tr.payload
        tr.trips += 1
        tr.tonnes += t
        self.k["hauled"].add(t)
        self.k["cycles"].add(1)
        tr.last_wait = tr.wait_cycle
        tr.wait_cycle = 0.0
        tr.payload, tr.grade, tr.bwi, tr.material = 0.0, 0.0, 0.0, ""
        tr.state = "waiting"
        self.next_job(tr)

    def next_job(self, tr):
        """Empty truck: maintenance, then fuel, then the dispatcher."""
        if tr.health.pm_pending:
            route, eta = self.net.route(tr.from_node(), "WS", False)
            if route is not None:
                self.send(tr, ("workshop", "WS"), route, eta)
                return
        if tr.fuel < P.TRUCK["tank_l"] * P.TRUCK["refuel_below_frac"]:
            route, eta = self.net.route(tr.from_node(), "WS", False)
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
                self.after_dump(tr)
        if c.health.pm_pending and not c.down and c.tipping is None:
            c.health.start_pm(self.t)
            c.down = True
            self.log(f"CRUSHER-01 planned maintenance started ({c.health.p['pm_h']:.0f} h, COS "
                     f"{self.cos.t / P.PLANT['target_tph']:.1f} h of feed)")
        if c.down:
            c.state, c.power_kw, c.rate_tph, c.cap_tph = c.why(self.t) or "down", 0.0, 0.0, 0.0
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
            self.crusher_bond_kwh += C["kwh_per_t_per_bwi"] * h * t
            c.state = "crushing"
            self.k["crushed"].add(t)
            if c.health.use(dt, (c.rate_tph / P.PLANT["target_tph"]) * (h / P.REF_BWI)):
                c.health.fail(self.t)
                c.down = True
                self.log(f"CRUSHER-01 breakdown (health {c.health.h:.0f}%)")
        else:
            c.rate_tph = 0.0
            c.power_kw = C["idle_kw"]
            c.state = "blocked_cos_full" if self.cos.free <= 1e-6 and c.pocket.t > 0 else "starved"
        self.crusher_fixed_kwh += C["idle_kw"] * dt / 3600.0
        kwh = c.power_kw * dt / 3600.0
        c.kwh += kwh
        self.k["crusher_kwh"].add(kwh)

    def step_tip_points(self, dt):
        for tp, where in ((self.rom_pad, "rom"), (self.dump, "dump"), (self.fuel_bay, "fuel"),
                          (self.workshop, "workshop")):
            while tp.queue and len(tp.active) < tp.spots:
                tr = tp.queue[0]
                if where == "fuel":
                    give = min(P.TRUCK["tank_l"] - tr.fuel, self.bay_l)
                    if give < min(P.FUEL_BAY["min_dispense_l"], P.TRUCK["tank_l"] - tr.fuel):
                        tr.why = "fuel bay empty"
                        break
                    self.bay_l -= give
                    tr.fuel += give
                    self.fuel_dispensed += give
                    dur = P.TRUCK["refuel_fixed_s"] + give / P.TRUCK["refuel_lpm"] * 60.0
                    tr.state, tr.why = "refueling", ""
                elif where == "workshop":
                    tr.health.start_pm(self.t)
                    dur = tr.health.p["pm_h"] * 3600.0
                    tr.state = "maintenance"
                    self.log(f"{tr.id} planned maintenance started")
                else:
                    tip_s = P.ROM["tip_s"] if where == "rom" else P.WASTE_DUMP["tip_s"]
                    dur = tip_s * tr.rng.uniform(0.9, 1.15)
                    tr.state = "tipping"
                tp.queue.popleft()
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
                    self.after_dump(tr)
                elif where == "dump":
                    self.waste_t += tr.payload
                    self.k["waste"].add(tr.payload)
                    self.after_dump(tr)
                else:
                    if where == "workshop":
                        tr.health.pm_until = -1.0
                        tr.health.h = 98.0
                        self.log(f"{tr.id} maintenance done")
                    tr.state = "waiting"
                    self.next_job(tr)

    def step_rom_reclaim(self, dt):
        """Front-end loader feeds the crusher from the ROM pad when it would starve or to blend."""
        c = self.crusher
        D = P.DISPATCH
        L = P.PLANT
        self.loader_tph = 0.0
        if c.down or self.rom.t <= 0 or c.tipping is not None:
            return
        target = (L["grade_min"] + L["grade_max"]) / 2
        starving = not c.queue and c.pocket.t < 0.35 * c.pocket.capacity and \
            self.cos.t < D["cos_reclaim_frac"] * self.cos.capacity
        off = self.cos.grade - target
        building = self.maintenance.crusher_prep and c.pocket.t < 0.5 * c.pocket.capacity and \
            not c.queue and self.cos.free > 0
        blending = self.modules["dispatcher"] and abs(off) > 0.08 and (self.rom.grade - target) * off < 0 and \
            c.pocket.t < 0.5 * c.pocket.capacity and self.cos.t < D["cos_high_frac"] * self.cos.capacity
        if not (starving or blending or building):
            return
        litres = P.ROM["loader_l_per_h"] * dt / 3600.0
        if self.bay_l < litres:
            return
        t, g, h = self.rom.take(min(P.ROM["reclaim_tph"] * dt / 3600.0, c.pocket.free))
        if t <= 0:
            return
        c.pocket.add(t, g, h)
        self.rom_out_t += t
        self.k["rom_out"].add(t)
        self.loader_tph = t * 3600.0 / dt
        self.bay_l -= litres
        self.loader_fuel += litres
        self.k["fuel"].add(litres)

    def step_plant(self, dt):
        L = P.PLANT
        kwh_t = L["kwh_per_t_per_bwi"] * max(self.cos.bwi, 1.0)
        self.plant_cap_tph = self.energy.plant_limit(kwh_t)
        t, g, h = self.cos.take(self.plant_cap_tph * dt / 3600.0)
        self.plant_tph = t * 3600.0 / dt
        self.plant_grade = g if t > 0 else 0.0
        self.plant_t += t
        self.plant_cu_t += t * g / 100.0
        cu = t * g / 100.0 * P.CU_RECOVERY
        self.copper_t += cu
        if self.energy.tripped():
            self.plant_kw = 0.0
        else:
            self.plant_kw = L["base_kw"] + kwh_t * self.plant_tph
            self.plant_fixed_kwh += L["base_kw"] * dt / 3600.0
            self.plant_bond_kwh += kwh_t * t
        self.plant_kwh += self.plant_kw * dt / 3600.0
        self.k["plant"].add(t)
        self.k["plant_cu"].add(t * g)
        self.k["copper"].add(cu)

    def step_fuel_bay(self, dt):
        add = min(self.resupply_lph() * dt / 3600.0, P.FUEL_BAY["capacity_l"] - self.bay_l)
        if add > 0:
            self.bay_l += add
            self.fuel_resupplied += add

    def step_energy(self, dt):
        self.site_kw = self.crusher.power_kw + self.plant_kw
        kwh = self.site_kw * dt / 3600.0
        self.site_kwh += kwh
        self.k["site_kwh"].add(kwh)
        self.win_kwh += kwh
        self.win_s += dt
        if self.win_s >= P.GRID["demand_window_s"] - 1e-9:
            self.demand_kw = self.win_kwh * 3600.0 / self.win_s
            self.peak_kw = max(self.peak_kw, self.demand_kw)
            self.win_kwh = self.win_s = 0.0
            self.energy.window_closed(self.demand_kw, self.t - P.GRID["demand_window_s"])

    def step_kpis(self, dt):
        f = math.exp(-dt / P.KPI_TAU_S)
        for e in self.k.values():
            e.decay(f)
        for ex in self.excavators:
            ex.rate.decay(f)
        for e in self.dispatcher.assigned.values():
            e.decay(f)
        units = len(self.excavators) + len(self.trucks) + 1
        up = sum(not e.down for e in self.excavators) + sum(t.available for t in self.trucks) + \
            (not self.crusher.down)
        self.avail.update(up / units, dt)

    # ------------------------------------------------------------------ audits
    def bay_out(self):
        return self.bay_l < P.FUEL_BAY["min_dispense_l"]

    def mass_balance(self):
        """Tonnes dug + opening stocks - (stocks now + in trucks + waste dumped + plant feed)."""
        dug = sum(ex.tonnes for ex in self.excavators)
        in_trucks = sum(tr.payload for tr in self.trucks)
        stocks = self.rom.t + self.cos.t + self.crusher.pocket.t
        return dug + self.inventory0 - (in_trucks + stocks + self.waste_t + self.plant_t)

    def fuel_balance(self):
        """Opening diesel + deliveries - (burned + written off + in tanks + in the bay)."""
        tanks = sum(tr.fuel for tr in self.trucks)
        burned = sum(tr.fuel_burned for tr in self.trucks) + self.loader_fuel
        return self.fuel_initial + self.fuel_resupplied - (burned + self.fuel_written_off + tanks + self.bay_l)

    def energy_balance(self):
        """Grid-metered energy - (fixed loads + specific energy x tonnes crushed and milled)."""
        return self.site_kwh - (self.crusher_fixed_kwh + self.crusher_bond_kwh +
                                self.plant_fixed_kwh + self.plant_bond_kwh)

    def kpis(self):
        k = self.k
        return {
            "tph_mined": k["dug"].v,
            "tph_ore": k["dug_ore"].v,
            "tph_waste": k["dug_waste"].v,
            "tph_crushed": k["crushed"].v,
            "plant_feed_tph": k["plant"].v,
            "plant_feed_grade": k["plant_cu"].v / k["plant"].v if k["plant"].v > 1e-6 else 0.0,
            "copper_tph": k["copper"].v,
            "mean_queue_min": k["queue_s"].v / k["cycles"].v / 60.0 if k["cycles"].v > 1e-6 else 0.0,
            "availability_pct": 100.0 * self.avail.v,
            "crusher_kwh_t": k["crusher_kwh"].v / k["crushed"].v if k["crushed"].v > 1e-6 else 0.0,
            "site_kwh_t": k["site_kwh"].v / k["plant"].v if k["plant"].v > 100 else 0.0,
            "diesel_l_t": k["fuel"].v / k["hauled"].v if k["hauled"].v > 1e-6 else 0.0,
            "diesel_lph": k["fuel"].v,
        }


class Dispatcher:
    """Truck dispatching and ore routing.

    Empty truck: pick the excavator with the lowest score
        expected loading start - deficit_weight x (target - actual rate) / target
                               - grade_weight x (blend benefit of its face grade)
    where the expected start accounts for travel and for the trucks already
    queued at or driving to that excavator, and the blend benefit is positive
    when the face grade pulls the coarse-ore (plant feed) grade toward the
    middle of the band. Loaded truck: waste to the dump; ore to the crusher
    unless the crusher is down, its expected wait is too long, the coarse ore
    stockpile is near full, or the load would push the feed grade further out
    of band; then to the ROM pad (blend stock). Under a diesel shortage the
    dispatcher parks trucks so that fuel lasts, suspending waste stripping
    first to protect ore production.
    """

    def __init__(self, sim):
        self.sim = sim
        self.assigned = {ex.id: Ema(P.KPI_TAU_S) for ex in sim.excavators}
        self.decisions = 0
        self.reroutes = 0
        self.to_rom = 0
        self.blend_diverts = 0
        self.last = ""
        self.ration = 0
        self.next_fuel_review = 0.0
        self.fuel_note = ""
        self.log = deque(maxlen=10)

    @property
    def on(self):
        return self.sim.modules["dispatcher"]

    def decide(self, text):
        """A notable decision: counted, shown as last decision and kept in the decision log."""
        self.note(text)
        record(self.log, self.sim.t, text)

    def note(self, text):
        self.decisions += 1
        self.last = text

    @staticmethod
    def target_grade():
        return (P.PLANT["grade_min"] + P.PLANT["grade_max"]) / 2

    # ---------------------------------------------------------- fuel rationing
    def step(self):
        sim = self.sim
        if not self.on:
            if self.ration:
                self.ration = 0
                self.fuel_note = "Fuel rationing lifted (dispatcher off)"
                sim.log(self.fuel_note)
            return
        if sim.t < self.next_fuel_review:
            return
        self.next_fuel_review = sim.t + 600.0
        active = [t for t in sim.trucks if t.state not in ("parked", "broken", "maintenance")]
        per_truck = sim.k["fuel"].v / max(1, len(active)) if sim.k["fuel"].v > 1 else 190.0
        horizon_h = 12.0
        need = per_truck * len(sim.trucks) - sim.resupply_lph() - sim.bay_l / horizon_h
        n = 0 if need <= 0 else min(len(sim.trucks) - 3, math.ceil(need / max(per_truck, 1.0)))
        if n != self.ration:
            cover = sim.bay_l / max(1.0, per_truck * (len(sim.trucks) - n) - sim.resupply_lph()) \
                if per_truck * (len(sim.trucks) - n) > sim.resupply_lph() else math.inf
            if n > 0:
                self.fuel_note = (f"Fuel rationing: park {n} trucks, waste stripping suspended "
                                  f"(bay {sim.bay_l:,.0f} L, supply {sim.resupply_lph():,.0f} L/h, "
                                  f"cover {'%.1f h' % cover if cover < 1e6 else 'ok'})")
            else:
                self.fuel_note = "Fuel rationing lifted"
            sim.log(self.fuel_note)
            self.decide(self.fuel_note)
            self.ration = n

    def cos_high_limit(self):
        return 0.98 if self.sim.maintenance.crusher_prep else P.DISPATCH["cos_high_frac"]

    def rationed(self):
        return sum(1 for t in self.sim.trucks if t.dest == ("park", "WS") and t.why == "fuel rationing"
                   and t.state in ("parked", "travel"))

    # ---------------------------------------------------------- empty trucks
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

    def blend_benefit(self, ex):
        """+1 when this face pulls the feed grade toward target as much as possible, -1 the opposite."""
        if ex.role != "ore":
            return 0.0
        half = (P.PLANT["grade_max"] - P.PLANT["grade_min"]) / 2
        target = self.target_grade()
        need = max(-1.0, min(1.0, (target - self.sim.cos.grade) / half))
        pull = max(-1.0, min(1.0, (ex.cluster.face_grade - target) / half))
        return need * pull

    FIXED = {"ex-01": (1, 2), "ex-02": (3, 4), "ex-03": (5, 6, 7, 8)}

    def assign_fixed(self, tr):
        """Dispatcher off: fixed truck-to-shovel allocation, nearest working shovel as fallback."""
        sim = self.sim
        src = tr.from_node()
        idx = int(tr.id.split("-")[1])
        home = next((e for e, ts in self.FIXED.items() if idx in ts), "ex-03")
        options = []
        for ex in sim.excavators:
            if ex.down:
                continue
            route, t = sim.net.route(src, ex.node, False)
            if route is not None:
                options.append((ex.id != home, t, ex, route))
        if not options:
            tr.state = "parked" if tr.state == "parked" else "waiting"
            tr.why = "no reachable excavator"
            tr.retry_at = sim.t + 60.0
            return
        _, t, ex, route = min(options, key=lambda o: (o[0], o[1]))
        self.assigned[ex.id].add(P.TRUCK["payload_t"])
        self.note(f"{tr.id} -> {ex.id} (fixed allocation)")
        self._go(tr, ("excavator", ex.id), route, t)

    def assign_empty(self, tr):
        sim = self.sim
        src = tr.from_node()
        if not self.on:
            self.assign_fixed(tr)
            return
        if self.ration > self.rationed() and tr.payload <= 0:
            route, t = sim.net.route(src, "WS", False)
            if route is not None:
                self.note(f"{tr.id} parked (fuel rationing)")
                self._go(tr, ("park", "WS"), route, t)
                tr.why = "fuel rationing"
                return
        best = fastest = None
        terms = {}
        for ex in sim.excavators:
            if ex.down or (self.ration > 0 and ex.role == "waste"):
                continue
            route, t_travel = sim.net.route(src, ex.node, False)
            if route is None:
                continue
            arrival = sim.t + t_travel
            wait = max(arrival, self.ex_free_at(ex, arrival, tr)) - sim.t
            target = P.DISPATCH["target_tph"][ex.id]
            deficit = (target - self.assigned[ex.id].v) / target
            d_term = P.DISPATCH["deficit_weight_s"] * deficit
            g_term = P.DISPATCH["grade_weight_s"] * self.blend_benefit(ex)
            terms[ex.id] = (wait, d_term, g_term)
            score = wait - d_term - g_term
            if best is None or score < best[0] - 1e-9:
                best = (score, ex, route, t_travel)
            if fastest is None or wait < fastest[0] - 1e-9:
                fastest = (wait, ex)
        if best is None:
            tr.state = "parked" if tr.state == "parked" else "waiting"
            tr.why = "no reachable excavator"
            tr.retry_at = sim.t + 60.0
            return
        _, ex, route, t_travel = best
        fast = fastest[1]
        if fast is not ex:
            w, d, g = terms[ex.id]
            wf, df, gf = terms[fast.id]
            if g - gf > d - df:
                if g > 0:
                    why = (f"blend: {ex.id} face {ex.cluster.face_grade:.2f}% Cu pulls feed "
                           f"{sim.cos.grade:.2f}% toward {self.target_grade():.2f}%")
                else:
                    why = (f"blend: {fast.id} face {fast.cluster.face_grade:.2f}% Cu would push feed "
                           f"{sim.cos.grade:.2f}% away from {self.target_grade():.2f}%")
            else:
                T = P.DISPATCH["target_tph"]
                why = (f"rate balance: {ex.id} at {self.assigned[ex.id].v / T[ex.id]:.0%} of target, "
                       f"{fast.id} at {self.assigned[fast.id].v / T[fast.id]:.0%}")
            self.decide(f"{tr.id} -> {ex.id}, not {fast.id} ({(w - wf) / 60:+.1f} min wait): {why}")
        else:
            self.note(f"{tr.id} -> {ex.id}")
        self.assigned[ex.id].add(P.TRUCK["payload_t"])
        self._go(tr, ("excavator", ex.id), route, t_travel)

    # ---------------------------------------------------------- loaded trucks
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
        L = P.PLANT
        if tr.material == "waste":
            options, reason = [("dump", "DUMP")], "waste"
        else:
            r_cr, t_cr = sim.net.route(src, "CR", True)
            crusher_ok = not sim.crusher.down and r_cr is not None
            cos_high = sim.cos.t > self.cos_high_limit() * sim.cos.capacity
            wait = self.crusher_wait_est(sim.t + t_cr) if crusher_ok else math.inf
            target = self.target_grade()
            off_band = (tr.grade < L["grade_min"] and sim.cos.grade < target) or \
                       (tr.grade > L["grade_max"] and sim.cos.grade > target)
            if not self.on:
                cos_high, wait, off_band = False, 0.0, False
            if crusher_ok and not cos_high and wait <= D["crusher_wait_max_s"] and not off_band:
                options, reason = [("crusher", "CR"), ("rom", "ROM")], f"ore {tr.grade:.2f}% to crusher"
            else:
                if sim.crusher.down:
                    why = f"crusher {sim.crusher.why(sim.t)}"
                elif r_cr is None:
                    why = "crusher cut off"
                elif cos_high:
                    why = "COS high"
                elif off_band:
                    why = f"blend: {tr.grade:.2f}% would push feed {sim.cos.grade:.2f}% out of band"
                    self.blend_diverts += 1
                else:
                    why = f"crusher wait {wait / 60:.0f} min"
                options, reason = [("rom", "ROM"), ("crusher", "CR")], f"ore to ROM ({why})"
        for kind, node in options:
            route, t = sim.net.route(src, node, True)
            if route is not None:
                if kind == "rom":
                    self.to_rom += 1
                text = f"{tr.id} {reason}" if kind == options[0][0] else f"{tr.id} {kind} (fallback)"
                if kind == "rom" and self.on:
                    self.decide(text)
                else:
                    self.note(text)
                self._go(tr, (kind, node), route, t)
                return
        tr.state = "waiting"
        tr.why = "no route for loaded truck"
        tr.retry_at = sim.t + 60.0

    def _go(self, tr, dest, route, t):
        # Keep the segment the truck is driving on; the new route starts at its end.
        if tr.seg is not None and tr.route and tr.route[0][0] is tr.seg:
            route = [tr.route[0]] + route
        self.sim.send(tr, dest, route, t)

    def reroute(self, tr):
        """Re-plan one truck after a road closure or an equipment change."""
        if tr.payload > 0:
            self.route_loaded(tr)
        else:
            self.sim.next_job(tr)
        self.reroutes += 1
        if tr.seg is not None or tr.route:
            record(self.log, self.sim.t, f"{tr.id} re-routed to {tr.dest[1] if tr.dest else '?'}")

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
            elif tr.state == "broken" and tr.resume == "travel":
                upcoming = tr.route[1:] if tr.seg is not None else tr.route
                if any(seg.closed for seg, _ in upcoming) or \
                        (kind == "excavator" and sim.ex_by_id[tr.dest[1]].down) or \
                        (kind == "crusher" and sim.crusher.down):
                    self.reroute(tr)
                    tr.resume, tr.state = "travel", "broken"


class MaintenancePlanner:
    """Condition-based maintenance: pull a machine for planned maintenance when
    the expected cost of an unplanned failure within the horizon exceeds the
    cost of stopping it now. Costs are in tonnes of lost production:

        E[failure] = P(fail within H) x MTTR x loss rate x unplanned factor
        E[planned] = PM duration x loss rate x opportunity factor

    The crusher's loss rate accounts for the coarse ore stockpile: while the
    stockpile can feed the plant, a crusher stop costs only re-handling, so
    the planner prefers to service the crusher when the stockpile is full.
    """

    def __init__(self, sim):
        self.sim = sim
        self.next_review = P.MAINT["review_s"]
        self.decisions = 0
        self.last = ""
        self.avoided_t = 0.0
        self.crusher_prep = False       # building the COS before a crusher stop
        self.prep_since = 0.0
        self.log = deque(maxlen=10)

    def decide(self, text):
        self.last = text
        record(self.log, self.sim.t, text)
        self.sim.log(text)

    def loss(self, kind, obj, duration_h):
        sim = self.sim
        if kind == "truck":
            return P.MAINT["truck"]["loss_tph"] * duration_h
        if kind == "excavator":
            value = 1.5 if obj.role == "ore" else 1.0
            return P.DISPATCH["target_tph"][obj.id] * value * duration_h
        cos_h = sim.cos.t / P.PLANT["target_tph"]
        return P.PLANT["target_tph"] * max(0.0, duration_h - cos_h) + 300.0 * duration_h

    def step(self):
        sim = self.sim
        c = sim.crusher
        if not sim.modules["maintenance"]:
            return
        if self.crusher_prep:
            cover_h = sim.cos.t / P.PLANT["target_tph"]
            if c.down or c.health.pm_pending:
                self.crusher_prep = False
            elif cover_h >= c.health.p["pm_h"] * 0.95 or sim.cos.t >= 0.95 * sim.cos.capacity or \
                    sim.t - self.prep_since > 6 * 3600:
                self.crusher_prep = False
                c.health.pm_pending = True
                self.decide(f"CRUSHER-01 PM released: COS {sim.cos.t:,.0f} t = {cover_h:.1f} h of plant feed")
        if sim.t < self.next_review:
            return
        self.next_review = sim.t + P.MAINT["review_s"]
        M = P.MAINT
        in_shop = sum(1 for t in sim.trucks if t.health.pm_pending or t.state == "maintenance")
        machines = [("crusher", sim.crusher)] + [("excavator", e) for e in sim.excavators] + \
                   [("truck", t) for t in sorted(sim.trucks, key=lambda t: t.health.h)]
        for kind, m in machines:
            hl = m.health
            if hl.pm_pending or hl.broken(sim.t) or hl.in_pm(sim.t) or getattr(m, "forced", False):
                continue
            if kind == "crusher" and self.crusher_prep:
                continue
            if kind == "truck" and (in_shop >= P.WORKSHOP["bays"] or not m.available):
                continue
            if kind == "excavator" and sum(e.down or e.health.pm_pending for e in sim.excavators) >= 1:
                continue
            p = hl.p_fail(M["horizon_h"])
            e_fail = p * self.loss(kind, m, hl.p["mttr_h"]) * M["unplanned_factor"]
            e_pm = self.loss(kind, m, hl.p["pm_h"])
            if kind == "crusher" and e_fail <= e_pm:
                full_h = sim.cos.capacity * 0.95 / P.PLANT["target_tph"]
                e_pm_full = P.PLANT["target_tph"] * max(0.0, hl.p["pm_h"] - full_h) + 300.0 * hl.p["pm_h"]
                if e_fail > e_pm_full:
                    self.crusher_prep = True
                    self.prep_since = sim.t
                    self.decisions += 1
                    self.avoided_t += e_fail - e_pm_full
                    self.decide(f"CRUSHER-01 PM scheduled: health {hl.h:.0f}%, P(fail in {M['horizon_h']:.0f} h) "
                                f"{p:.0%}, expected loss {e_fail:,.0f} t; building COS to cover the "
                                f"{hl.p['pm_h']:.0f} h stop (planned cost {e_pm_full:,.0f} t)")
                continue
            if e_fail > e_pm:
                hl.pm_pending = True
                if kind == "truck":
                    in_shop += 1
                self.decisions += 1
                self.avoided_t += e_fail - e_pm
                name = m.id.upper() if kind != "truck" else m.id
                self.decide(f"PM {name}: health {hl.h:.0f}%, P(fail in {M['horizon_h']:.0f} h) {p:.0%}, "
                            f"expected loss {e_fail:,.0f} t > planned stop {e_pm:,.0f} t")


class EnergyManager:
    """Keeps site demand under a grid cap by throttling the concentrator.

    With the module on, the concentrator rate is limited every step to
    (cap - crusher power - base load) / specific energy, so demand never
    exceeds the cap. With the module off the plant runs at target; when a
    15-minute demand block exceeds the cap the utility sheds the
    concentrator for 15 minutes (load shedding).
    """

    TRIP_S = 900.0

    def __init__(self, sim):
        self.sim = sim
        self.log = deque(maxlen=10)
        self.decisions = 0
        self.last = ""
        self.violations = 0
        self.trip_until = -1.0
        self.throttled = False
        self.logged_tph = 0.0

    def tripped(self):
        return self.sim.t < self.trip_until

    def decide(self, text):
        self.decisions += 1
        self.last = text
        record(self.log, self.sim.t, text)

    def plant_limit(self, kwh_t):
        sim = self.sim
        target = P.PLANT["target_tph"]
        if self.tripped():
            return 0.0
        cap = sim.energy_cap_kw()
        if cap == math.inf or not sim.modules["energy"]:
            if self.throttled:
                self.throttled = False
                self.decide("Concentrator throttle released" + ("" if cap == math.inf else " (energy module off)"))
            return target
        lim = max(0.0, min(target, (cap - sim.crusher.power_kw - P.PLANT["base_kw"]) / kwh_t))
        throttled = lim < target - 1.0
        if throttled != self.throttled or (throttled and abs(lim - self.logged_tph) > 0.05 * target):
            self.throttled = throttled
            self.logged_tph = lim
            if throttled:
                self.decide(f"Concentrator throttled to {lim:,.0f} t/h to hold the {cap:,.0f} kW grid cap "
                            f"(crusher {sim.crusher.power_kw:,.0f} kW, {kwh_t:.1f} kWh/t)")
            else:
                self.decide("Concentrator throttle released")
        return lim

    def window_closed(self, demand_kw, window_start):
        """A demand block counts against the cap only if the cap was already set when it began."""
        sim = self.sim
        cap = sim.energy_cap_kw()
        if cap < math.inf and window_start >= sim.cap_since - 1e-6 and demand_kw > cap + 1.0 and not self.tripped():
            self.violations += 1
            self.trip_until = sim.t + self.TRIP_S
            text = (f"Grid cap exceeded: 15-min demand {demand_kw:,.0f} kW > cap {cap:,.0f} kW; "
                    f"utility sheds the concentrator for 15 min")
            record(self.log, sim.t, text)
            sim.log(text)
