"""Warehouse digital twin.

4 zones, 10 robots, 1 tick = 1 second. Orders arrive at random (Poisson) in each
zone. A robot standing in a zone picks the oldest waiting order there. Moving to
another zone takes TRAVEL_S seconds, during which the robot cannot work.

Two kinds of controller can drive the fleet:
  * "rules"  - robots stay in their home zone (nearest free robot takes the order)
  * "rl"     - every DECISION_S seconds a policy may move one robot between zones

The same seed gives the same orders, so both controllers face exactly the same day.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

ZONES = ["A", "B", "C", "D"]
ZONE_NAMES = {"A": "Market", "B": "Homes", "C": "Offices North", "D": "Offices South"}
N_ZONES = 4
N_ROBOTS = 10
HOME = [0, 0, 0, 1, 1, 1, 2, 2, 3, 3]          # 3 / 3 / 2 / 2 robots per zone
BASE_RATE = np.array([6.0, 6.0, 2.0, 2.0])      # orders per minute per zone
SERVICE_S = 30                                  # one order takes 30 s -> 2 orders/min/robot
TRAVEL_S = 40                                   # driving between zones
DECISION_S = 10                                 # agent decides every 10 s
BATTERY_DRAIN_WORK = 0.035                      # % per second while working
BATTERY_DRAIN_MOVE = 0.05                       # % per second while driving
BATTERY_LOW = 18.0
CHARGE_S = 90
FORECAST_WINDOW = 60                            # moving average over last 60 s

# share of training days where the busy zones are shuffled (0 reproduces the shipped agent)
MIX_BASE_PROB = float(__import__("os").environ.get("MIX_BASE_PROB", 0))

# action 0 = do nothing, actions 1..12 = move one robot from zone i to zone j
MOVES = [(i, j) for i in range(N_ZONES) for j in range(N_ZONES) if i != j]
N_ACTIONS = 1 + len(MOVES)

SCENARIOS = {
    "normal":  "Normal day: steady orders, no surprises",
    "spike":   "Flash sale: Zone A orders jump 5x for 8 minutes",
    "moving":  "Moving hotspot: the busy zone shifts A -> B -> C",
    "rush":    "Evening rush: Zone A doubles, Zone B up 20%, all evening",
    "failure": "Robot breakdown: 2 robots fail during a Zone A spike",
    "mixed":   "Mixed day: random busy zones and surprise spikes",
}


@dataclass
class Event:
    zone: int
    start: int          # second
    end: int
    mult: float
    signal_lead: int = 120   # web signal appears this many seconds before start (-1 = none)
    signal_noise: float = 0.0


@dataclass
class Plan:
    """Everything random about a day, fixed up front so both controllers share it."""
    length: int
    events: list = field(default_factory=list)
    failures: list = field(default_factory=list)   # (second, robot index)
    global_mult: np.ndarray | None = None          # per-zone multiplier for whole day
    base: np.ndarray | None = None                 # per-zone orders/min (default BASE_RATE)


def make_plan(scenario: str, length: int = 1500, rng: np.random.Generator | None = None) -> Plan:
    rng = rng or np.random.default_rng(0)
    p = Plan(length=length)
    if scenario == "normal":
        pass
    elif scenario == "spike":
        p.events.append(Event(0, 300, 780, 5.0, signal_lead=150))
    elif scenario == "moving":
        p.events += [Event(0, 240, 540, 4.0, 120), Event(1, 540, 840, 4.0, 120), Event(2, 840, 1140, 5.0, 120)]
    elif scenario == "rush":
        p.global_mult = np.array([2.0, 1.2, 1.0, 1.0])
        p.events.append(Event(0, 0, length, 1.0, -1))
    elif scenario == "failure":
        p.events.append(Event(0, 300, 780, 5.0, signal_lead=150))
        p.failures += [(360, 0), (420, 6)]
    elif scenario in ("random", "mixed"):
        if scenario == "mixed" or (MIX_BASE_PROB and rng.random() < MIX_BASE_PROB):   # any zone can be the busy one
            p.base = rng.dirichlet([1.2] * N_ZONES) * rng.uniform(10, 22)
        n = int(rng.integers(0, 4))
        for _ in range(n):
            z = int(rng.integers(0, N_ZONES))
            start = int(rng.integers(60, length - 240))
            dur = int(rng.integers(180, 600))
            mult = float(rng.uniform(2.0, 5.5))
            lead = int(rng.integers(60, 200)) if rng.random() < 0.75 else -1
            p.events.append(Event(z, start, min(length, start + dur), mult, lead, float(rng.uniform(0, 0.25))))
        if rng.random() < 0.25:
            p.global_mult = rng.uniform(0.7, 1.6, N_ZONES)
        if rng.random() < 0.2:
            p.failures.append((int(rng.integers(200, length - 200)), int(rng.integers(0, N_ROBOTS))))
    else:
        raise ValueError(scenario)
    return p


class WarehouseSim:
    def __init__(self, plan: Plan, seed: int = 0, init: dict | None = None, rates: np.ndarray | None = None,
                 signal: np.ndarray | None = None):
        self.plan = plan
        self.rng = np.random.default_rng(seed)
        # pre-draw every arrival so both controllers see identical orders
        self.rates = self._rate_table() if rates is None else np.tile(np.asarray(rates, float), (plan.length, 1))
        self.arrivals = self.rng.poisson(self.rates / 60.0)          # [length, zones]
        self.signal = self._signal_table() if signal is None else np.tile(np.asarray(signal, float), (plan.length, 1))
        self.reset_state()
        if init:                                 # start from a real warehouse state (digital twin)
            self.zone = np.array(init["zone"], dtype=int)
            self.status = np.array(init["status"], dtype=int)
            self.battery = np.array(init["battery"], dtype=float)
            self.queues = [[0] * int(n) for n in init["queues"]]

    # ------------------------------------------------------------------ tables
    def _rate_table(self) -> np.ndarray:
        L = self.plan.length
        r = np.tile(BASE_RATE if self.plan.base is None else self.plan.base, (L, 1))
        if self.plan.global_mult is not None:
            r *= self.plan.global_mult
        for e in self.plan.events:
            r[e.start:e.end, e.zone] *= e.mult
        return r

    def _signal_table(self) -> np.ndarray:
        """What the web (TinyFish) tells us: expected demand multiplier per zone."""
        L = self.plan.length
        s = np.ones((L, N_ZONES))
        for e in self.plan.events:
            if e.signal_lead < 0 or e.mult <= 1.01:
                continue
            a = max(0, e.start - e.signal_lead)
            noisy = e.mult * (1 + self.rng.uniform(-e.signal_noise, e.signal_noise))
            s[a:e.end, e.zone] = np.maximum(s[a:e.end, e.zone], round(min(5.0, noisy), 1))
        return s

    # ------------------------------------------------------------------- state
    def reset_state(self):
        self.t = 0
        self.zone = np.array(HOME, dtype=int)          # current or destination zone
        self.status = np.zeros(N_ROBOTS, dtype=int)     # 0 idle 1 busy 2 moving 3 charging 4 broken
        self.timer = np.zeros(N_ROBOTS, dtype=int)
        self.battery = 100.0 - self.rng.uniform(0, 30, N_ROBOTS)
        self.queues = [[] for _ in range(N_ZONES)]      # arrival times of waiting orders
        self.waits: list[int] = []
        self.served = np.zeros(N_ZONES, dtype=int)
        self.moves = 0
        self.idle_robot_seconds = 0
        # pretend the shift has been running for a minute so the forecast is not empty at t=0
        self.recent = self.rng.poisson(np.tile(self.rates[0] / 60.0, (FORECAST_WINDOW, 1))).astype(float)
        self.history = []                                # snapshots for the UI
        self.log = []                                    # plain-English decision log

    def robots_in(self, z: int) -> int:
        return int(np.sum((self.zone == z) & (self.status != 4)))

    def forecast(self) -> np.ndarray:
        """Orders per minute seen in the last 60 s (moving-average forecast for the next minute)."""
        return self.recent.sum(axis=0)

    def observation(self) -> np.ndarray:
        q = np.array([len(x) for x in self.queues], dtype=float)
        rob = np.array([self.robots_in(z) for z in range(N_ZONES)], dtype=float)
        fc = self.recent.sum(axis=0)
        sig = self.signal[min(self.t, self.plan.length - 1)]
        return np.concatenate([np.minimum(q / 40.0, 3.0), rob / N_ROBOTS, np.minimum(fc / 30.0, 3.0), sig / 5.0]).astype(np.float32)

    # ----------------------------------------------------------------- actions
    def move_robot(self, src: int, dst: int) -> int | None:
        """Send one robot from src to dst. Prefer idle robots with good battery."""
        cand = [i for i in range(N_ROBOTS) if self.zone[i] == src and self.status[i] in (0, 1)]
        if not cand or self.robots_in(src) <= 1:      # safety rule: every zone keeps at least one robot
            return None
        cand.sort(key=lambda i: (self.status[i] != 0, self.timer[i], -self.battery[i]))
        r = cand[0]
        if self.status[r] == 1:          # finish current job first: count it as served now
            self.timer[r] = 0
        self.zone[r] = dst
        self.status[r] = 2
        self.timer[r] = TRAVEL_S
        self.moves += 1
        return r

    def apply_action(self, a: int):
        if a <= 0:
            return None
        src, dst = MOVES[a - 1]
        r = self.move_robot(src, dst)
        if r is not None:
            q = [len(x) for x in self.queues]
            fc = self.recent.sum(axis=0)
            sig = self.signal[min(self.t, self.plan.length - 1)]
            rob = [self.robots_in(z) for z in range(N_ZONES)]
            rob[dst] -= 1
            rob[src] += 1                        # counts before this move
            why = f"Zone {ZONES[dst]}: {q[dst]} waiting, {fc[dst]:.0f} orders/min for {rob[dst]} robot(s)"
            if sig[dst] > 1.05:
                why += f", web signal x{sig[dst]:.1f}"
            why += f". Zone {ZONES[src]}: {q[src]} waiting, {fc[src]:.0f} orders/min for {rob[src]} robots"
            self.log.append({"t": self.t, "robot": f"R{r + 1}", "from": ZONES[src], "to": ZONES[dst], "why": why})
        return r

    # -------------------------------------------------------------------- tick
    def step_second(self):
        t = self.t
        for (ft, ri) in self.plan.failures:
            if ft == t:
                self.status[ri] = 4
                self.log.append({"t": t, "robot": f"R{ri + 1}", "from": ZONES[self.zone[ri]], "to": "-", "why": "Robot broke down and left the floor"})
        arr = self.arrivals[t]
        for z in range(N_ZONES):
            self.queues[z].extend([t] * int(arr[z]))
        self.recent[t % FORECAST_WINDOW] = arr

        for i in range(N_ROBOTS):
            st = self.status[i]
            if st == 4:
                continue
            if st in (1, 2, 3):
                self.timer[i] -= 1
                self.battery[i] -= BATTERY_DRAIN_WORK if st == 1 else BATTERY_DRAIN_MOVE if st == 2 else -0.8
                if self.timer[i] <= 0:
                    self.status[i] = 0
                    self.battery[i] = min(100.0, self.battery[i])
            if self.status[i] == 0:
                if self.battery[i] < BATTERY_LOW:
                    self.status[i] = 3
                    self.timer[i] = CHARGE_S
                    continue
                z = self.zone[i]
                if self.queues[z]:
                    born = self.queues[z].pop(0)
                    self.waits.append(t - born)
                    self.served[z] += 1
                    self.status[i] = 1
                    self.timer[i] = SERVICE_S
                else:
                    self.idle_robot_seconds += 1
        self.t += 1

    def snapshot(self):
        return {
            "t": self.t,
            "queues": [len(x) for x in self.queues],
            "zone": self.zone.tolist(),
            "status": self.status.tolist(),
            "battery": [round(float(b), 1) for b in self.battery],
            "timer": self.timer.tolist(),
            "avg_wait": round(float(np.mean(self.waits + [self.t - b for q in self.queues for b in q])), 1) if (self.waits or any(self.queues)) else 0.0,
            "moves": self.moves,
            "served": int(self.served.sum()),
            "rate": [round(float(x), 1) for x in self.rates[min(self.t, self.plan.length - 1)]],
            "signal": [round(float(x), 1) for x in self.signal[min(self.t, self.plan.length - 1)]],
        }

    def metrics(self) -> dict:
        waiting_now = [self.t - b for q in self.queues for b in q]
        all_w = np.array(self.waits + waiting_now, dtype=float) if (self.waits or waiting_now) else np.zeros(1)
        alive = int(np.sum(self.status != 4))
        return {
            "avg_wait_s": round(float(all_w.mean()), 1),
            "p90_wait_s": round(float(np.percentile(all_w, 90)), 1),
            "served": int(self.served.sum()),
            "left_waiting": len(waiting_now),
            "idle_pct": round(100.0 * self.idle_robot_seconds / max(1, self.t * max(alive, 1)), 1),
            "moves": self.moves,
            "throughput_per_hr": round(float(self.served.sum()) * 3600.0 / max(1, self.t), 0),
        }


def run_episode(plan: Plan, seed: int, policy=None, record_every: int = 10, **kw) -> WarehouseSim:
    """policy: None for rules, or callable(obs, sim) -> action."""
    sim = WarehouseSim(plan, seed, **kw)
    while sim.t < plan.length:
        if policy is not None and sim.t % DECISION_S == 0:
            sim.apply_action(policy(sim.observation(), sim))
        sim.step_second()
        if sim.t % record_every == 0:
            sim.history.append(sim.snapshot())
    return sim


def greedy_policy(obs, sim: WarehouseSim) -> int:
    """Simple pressure heuristic (used only if no trained model is present)."""
    q = np.array([len(x) for x in sim.queues], dtype=float)
    fc = sim.recent.sum(axis=0) * np.maximum(1.0, sim.signal[min(sim.t, sim.plan.length - 1)])
    rob = np.array([max(sim.robots_in(z), 0.5) for z in range(N_ZONES)])
    need = (q / 4.0 + fc / 2.0) / rob
    dst, src = int(np.argmax(need)), int(np.argmin(need))
    if need[dst] - need[src] < 1.0 or sim.robots_in(src) <= 1:
        return 0
    return 1 + MOVES.index((src, dst))
