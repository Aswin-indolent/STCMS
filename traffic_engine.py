
from dataclasses import dataclass
import random
import os

DIRS = ("N","E","S","W")
KINDS = ("Car","Taxi","Bus","Truck","Van","Bike")

# Passenger Car Equivalents: how much "road capacity" each kind consumes,
# so a bus/truck rightly pulls more green-time weight than a bike/car.
PCE = {"Car":1.0,"Taxi":1.0,"Van":1.3,"Bus":2.5,"Truck":2.2,"Bike":0.5,"Emergency":1.0}

# Short-horizon arrival forecast: how many seconds of future demand (at the
# current smoothed arrival rate) get folded into the green-time decision, so
# the controller anticipates a building queue instead of only reacting to it.
PREDICT_HORIZON = 6.0
ARRIVAL_EWMA_ALPHA = 0.2

# HCM-style Level-of-Service bands, keyed by average approach delay (seconds).
LOS_BANDS = [(10.0,"A"), (20.0,"B"), (35.0,"C"), (55.0,"D"), (80.0,"E")]

YELLOW = 3.0
ALL_RED = 2.0
FIXED_GREEN = 30.0
ADAPTIVE_BASE = 10.0
ADAPTIVE_FACTOR = 2.5
ADAPTIVE_MAX = 60.0
EMERGENCY_GREEN = 20.0
SERVICE_SPEED = 105.0
EMERGENCY_SPEED = 185.0
CAR_GAP = 44.0
MANUAL_SPAWN_INTERVAL = 0.45
FLUID_SPAWN_INTERVAL = 0.10
FLUID_DATA_FILE = "fluid_traffic.csv"

@dataclass
class Vehicle:
    vid: int
    direction: str
    kind: str = "Car"
    emergency: bool = False
    distance: float = 0.0
    waiting: float = 0.0
    entered_intersection: bool = False
    exited: bool = False
    lane_switched: bool = False

class SignalController:
    def __init__(self):
        self.mode = "adaptive"
        self.phase = 0
        self.state = "green"  # green, yellow, all_red, idle
        self.remaining = ADAPTIVE_BASE
        self.emergency_active = False
        self.emergency_dir = None
        self.emergency_return_phase = 0
        self.emergency_return = False

    def group(self):
        return ("N","S") if self.phase == 0 else ("E","W")

    def duration(self, demand):
        if self.mode == "fixed":
            return FIXED_GREEN
        group_demand = sum(demand[d] for d in self.group())
        return min(ADAPTIVE_MAX, ADAPTIVE_BASE + ADAPTIVE_FACTOR*group_demand)

    def set_mode(self, mode):
        self.mode = mode
        self.phase = 0
        self.state = "green"
        self.remaining = self.duration({"N":0,"E":0,"S":0,"W":0})

    def is_green(self, d):
        if self.emergency_active:
            return d == self.emergency_dir and self.state == "green"
        return self.state == "green" and d in self.group()

    def start_emergency(self, d):
        self.emergency_active = True
        self.emergency_dir = d
        self.emergency_return_phase = self.phase
        self.state = "green"
        self.remaining = EMERGENCY_GREEN
        self.emergency_return = False

    def finish_emergency(self):
        self.emergency_active = False
        self.emergency_dir = None
        self.phase = self.emergency_return_phase
        # Return safely through yellow -> all-red -> green; never jump
        # directly from the emergency green to a normal green.
        self.emergency_return = True
        self.state = "yellow"
        self.remaining = YELLOW

    def _adaptive_choice(self, presence):
        ns = presence["N"] + presence["S"]
        ew = presence["E"] + presence["W"]
        if ns == 0 and ew == 0:
            return None
        return 0 if ns >= ew else 1

    def update(self, dt, presence, demand):
        if self.emergency_active:
            self.remaining -= dt
            return

        if self.state == "idle":
            chosen = self._adaptive_choice(presence)
            if chosen is not None:
                self.phase = chosen
                self.state = "green"
                self.remaining = self.duration(demand)
            return

        current = self.group()
        current_presence = sum(presence[d] for d in current)

        # No vehicles left on the current green approach: don't sit on a
        # green nobody needs while the other road waits — cut straight to
        # yellow regardless of how much of the timed/adaptive duration is
        # left. This check must use exact real-time presence (never a
        # decaying forecast), so it reliably fires the instant a queue empties.
        if self.mode == "adaptive" and self.state == "green" and current_presence == 0:
            self.state = "yellow"
            self.remaining = YELLOW
            return

        self.remaining -= dt
        if self.remaining > 0:
            return

        if self.state == "green":
            self.state = "yellow"
            self.remaining = YELLOW
        elif self.state == "yellow":
            self.state = "all_red"
            self.remaining = ALL_RED
        elif self.state == "all_red":
            # Normal operation always alternates the green phase:
            # N/S -> E/W -> N/S -> ...
            # Demand affects the duration of the selected phase, not which
            # phase comes next. Emergency priority is handled separately.
            self.phase = 1 - self.phase
            self.state = "green"
            self.remaining = self.duration(demand)

class World:
    def __init__(self, seed=42, auto=False, auto_level="medium"):
        self.rng = random.Random(seed)
        self.seed = seed
        self.controller = SignalController()
        self.vehicles = []
        self.pending = {d: 0 for d in DIRS}
        self.spawn_clock = {d: 0.0 for d in DIRS}
        self.next_vid = 0
        self.kind_index = 0
        self.total_passed = 0
        self.total_wait = 0.0
        self.max_queue = 0
        self.queue_sum = 0.0
        self.samples = 0
        self.elapsed = 0.0
        self.emergency_vehicle = None
        self.paused = False
        self.auto = auto
        self.auto_level = auto_level
        self.auto_clock = 0.0
        self.auto_next = 0.0
        self.fluid_enabled = False
        self.fluid_data = []
        self.fluid_index = 0
        self.fluid_clock = 0.0
        self.fluid_targets = {d: 0 for d in DIRS}
        self.csv_rows = []
        self.csv_clock = 0.0
        self.sim_speed = 1.0
        # --- congestion-management analytics ---
        self.arrival_rate = {d: 0.0 for d in DIRS}   # smoothed vehicles/sec, per direction
        self.dir_wait = {d: 0.0 for d in DIRS}       # cumulative wait time, per direction
        self.dir_passed = {d: 0 for d in DIRS}       # vehicles cleared, per direction

    def set_mode(self, mode):
        self.controller.set_mode(mode)

    def queue(self, d):
        return sum(1 for v in self.vehicles if v.direction == d and
                   not v.exited and not v.entered_intersection)

    def counts(self):
        return {d: self.queue(d) for d in DIRS}

    def demand(self):
        """PCE-weighted queue plus a short-horizon arrival forecast, per
        direction. This is what sizes adaptive green *duration* — a bus
        counts for more than a bike, and a fast-rising arrival rate earns
        extra green before its queue fully forms."""
        out = {}
        for d in DIRS:
            out[d] = self.weighted_counts()[d] + self.arrival_rate[d] * PREDICT_HORIZON
        return out

    def weighted_counts(self):
        """PCE-weighted queue length per direction, with NO predictive term —
        this is an exact snapshot of who is physically waiting right now, so
        it hits a clean 0.0 the instant a direction is empty. Used to decide
        *whether* to cut a green short, as opposed to demand() which decides
        how long a green should run."""
        out = {}
        for d in DIRS:
            out[d] = sum(PCE.get(v.kind, 1.0) for v in self.vehicles
                         if v.direction == d and not v.exited and not v.entered_intersection)
        return out

    def congestion_level(self):
        """HCM-style Level of Service (A-F) from average approach delay of
        vehicles currently stopped/queued, plus the raw average delay."""
        waiting = [v.waiting for v in self.vehicles
                   if not v.exited and not v.entered_intersection and v.waiting > 0]
        avg_delay = sum(waiting) / len(waiting) if waiting else 0.0
        for limit, grade in LOS_BANDS:
            if avg_delay <= limit:
                return grade, avg_delay
        return "F", avg_delay

    def analytics(self):
        grade, avg_delay = self.congestion_level()
        return {
            "congestion_grade": grade,
            "avg_delay": avg_delay,
            "dir_wait": dict(self.dir_wait),
            "dir_passed": dict(self.dir_passed),
            "arrival_rate": dict(self.arrival_rate),
        }

    def add_five(self, d):
        self.pending[d] += 5

    def add_emergency(self, d):
        if self.emergency_vehicle and not self.emergency_vehicle.exited:
            return False
        v = Vehicle(self.next_vid, d, "Emergency", True)
        self.next_vid += 1
        self.vehicles.append(v)
        self.emergency_vehicle = v
        self.controller.start_emergency(d)
        return True

    def set_auto(self, enabled, level=None):
        self.auto = enabled
        if level:
            self.auto_level = level
        self.fluid_enabled = False
        self.auto_clock = 0.0
        self.auto_next = 0.0

    def load_fluid_data(self, filename=FLUID_DATA_FILE):
        """Load directional traffic-demand profile from the supplied FLUID CSV."""
        path = filename
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        try:
            import csv
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                required = ["time_s", "N", "S", "E", "W"]
                if not reader.fieldnames or not all(c in reader.fieldnames for c in required):
                    raise ValueError("FLUID CSV must contain time_s, N, S, E and W columns")
                self.fluid_data = [
                    (float(row["time_s"]),
                     {d: max(0, int(float(row[d]))) for d in DIRS})
                    for row in reader
                ]
            self.fluid_index = 0
            self.fluid_clock = 0.0
            self.fluid_targets = {d: 0 for d in DIRS}
            return True
        except Exception:
            self.fluid_data = []
            return False

    def set_fluid(self, enabled=True, filename=FLUID_DATA_FILE):
        self.auto = False
        self.fluid_enabled = enabled
        self.fluid_index = 0
        self.fluid_clock = 0.0
        self.fluid_targets = {d: 0 for d in DIRS}
        if enabled and not self.fluid_data:
            return self.load_fluid_data(filename)
        return True

    def _fluid_spawn(self, dt):
        if not self.fluid_data:
            return

        self.fluid_clock += dt

        while (self.fluid_index + 1 < len(self.fluid_data) and
               self.elapsed >= self.fluid_data[self.fluid_index + 1][0]):
            self.fluid_index += 1

        self.fluid_targets = dict(self.fluid_data[self.fluid_index][1])

        # The FLUID values are treated as the desired waiting/approaching
        # population for each direction, not as new arrivals every frame.
        if self.fluid_clock >= FLUID_SPAWN_INTERVAL:
            self.fluid_clock -= FLUID_SPAWN_INTERVAL
            for d in DIRS:
                current = self.queue(d)
                deficit = self.fluid_targets[d] - current
                if deficit > 0:
                    self.pending[d] += min(deficit, 3)

    def set_auto_level(self, level):
        self.auto_level = level
        self.auto = True

    def _auto_interval(self):
        return {"low": 2.2, "medium": 1.25, "high": 0.7}.get(self.auto_level, 1.25)

    def _auto_spawn(self):
        # Weighted demand patterns make automatic intensity visibly useful.
        level = self.auto_level
        weights = {
            "low": [0.25,0.25,0.25,0.25],
            "medium": [0.35,0.20,0.30,0.15],
            "high": [0.45,0.10,0.35,0.10],
        }[level]
        d = self.rng.choices(DIRS, weights=weights, k=1)[0]
        self.pending[d] += 1

    def _spawn_pending(self, dt):
        spawned = {d: 0 for d in DIRS}
        for d in DIRS:
            self.spawn_clock[d] -= dt
            if self.pending[d] <= 0 or self.spawn_clock[d] > 0:
                continue
            # Keep a physical gap at the entry.
            same = [v for v in self.vehicles if v.direction == d and
                    not v.exited and not v.emergency and not v.entered_intersection]
            if same and min(v.distance for v in same) < CAR_GAP + 14:
                continue
            kind = KINDS[self.kind_index % len(KINDS)]
            self.kind_index += 1
            self.vehicles.append(Vehicle(self.next_vid, d, kind))
            self.next_vid += 1
            self.pending[d] -= 1
            spawned[d] += 1
            self.spawn_clock[d] = FLUID_SPAWN_INTERVAL if self.fluid_enabled else MANUAL_SPAWN_INTERVAL
        return spawned

    def _ahead(self, v):
        candidates = [a for a in self.vehicles if a is not v and
                      a.direction == v.direction and not a.exited and
                      a.distance > v.distance]
        return min(candidates, key=lambda a: a.distance) if candidates else None

    def _update_vehicle(self, v, dt):
        if v.exited:
            return
        speed = EMERGENCY_SPEED if v.emergency else SERVICE_SPEED
        desired = v.distance + speed * dt

        # Once committed past the stop line, signal changes cannot stop it.
        if not v.entered_intersection:
            # During an emergency, vehicles in the emergency vehicle's
            # direction may proceed only if they are physically AHEAD of it.
            # Vehicles behind it remain governed by the normal signal/queue.
            emergency_release = False
            if self.controller.emergency_active and not v.emergency:
                ev = self.emergency_vehicle
                if ev and not ev.exited and v.direction == ev.direction:
                    emergency_release = v.distance > ev.distance

            if not self.controller.is_green(v.direction) and not v.emergency and not emergency_release:
                desired = min(desired, 215.0)

            ahead = self._ahead(v)
            if ahead and ahead.distance < 215.0 + CAR_GAP:
                desired = min(desired, ahead.distance - CAR_GAP)

            # If the vehicle is behind the emergency vehicle, do not let it
            # pass the emergency vehicle while emergency priority is active.
            if self.controller.emergency_active and not v.emergency:
                ev = self.emergency_vehicle
                if ev and not ev.exited and v.direction == ev.direction and v.distance < ev.distance:
                    desired = min(desired, ev.distance - CAR_GAP)

            desired = max(v.distance, desired)

            if desired <= v.distance + 0.05:
                v.waiting += dt
                self.total_wait += dt
                self.dir_wait[v.direction] += dt

            if desired >= 215.0 and self.controller.is_green(v.direction):
                v.distance = desired
                v.entered_intersection = True
            elif v.emergency and desired >= 215.0:
                v.distance = desired
                v.entered_intersection = True
            else:
                v.distance = min(desired, 215.0)
                return
        else:
            # Committed vehicles never re-check the signal.
            v.distance = desired

        if v.distance >= 330.0:
            v.lane_switched = True
        if v.distance >= 650.0:
            v.exited = True
            if v.emergency:
                self.controller.finish_emergency()
                self.emergency_vehicle = None
            else:
                self.total_passed += 1
                self.dir_passed[v.direction] += 1

    def update(self, dt):
        if self.paused:
            return
        self.elapsed += dt

        if self.fluid_enabled:
            self._fluid_spawn(dt)
        elif self.auto:
            self.auto_clock -= dt
            if self.auto_clock <= 0:
                self._auto_spawn()
                self.auto_clock = self._auto_interval()

        spawned = self._spawn_pending(dt)
        for d in DIRS:
            inst_rate = spawned[d] / dt if dt > 0 else 0.0
            self.arrival_rate[d] = ARRIVAL_EWMA_ALPHA*inst_rate + (1-ARRIVAL_EWMA_ALPHA)*self.arrival_rate[d]
        self.controller.update(dt, self.weighted_counts(), self.demand())

        for v in list(self.vehicles):
            self._update_vehicle(v, dt)
        self.vehicles = [v for v in self.vehicles if not v.exited]

        q = sum(self.queue(d) for d in DIRS)
        self.max_queue = max(self.max_queue, q)
        self.queue_sum += q
        self.samples += 1

        self.csv_clock += dt
        if self.csv_clock >= 1.0:
            self.csv_clock -= 1.0
            c = self.controller
            demand = self.demand()
            grade, avg_delay = self.congestion_level()
            for d in DIRS:
                qd = self.queue(d)
                self.csv_rows.append([
                    round(self.elapsed,2), c.mode.upper(), d, qd, qd,
                    round(demand[d],2),
                    "NS" if c.phase == 0 else "EW", c.state,
                    round(max(0,c.remaining),2), self.total_passed,
                    round(self.total_wait,2), grade, round(avg_delay,2)
                ])

    def set_sim_speed(self, speed):
        """Set simulation playback speed. Supported values: 1x, 2x, 4x."""
        self.sim_speed = max(1.0, min(4.0, float(speed)))

    def reset(self):
        seed = self.seed
        self.__init__(seed, self.auto, self.auto_level)