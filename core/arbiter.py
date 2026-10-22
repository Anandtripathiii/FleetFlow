"""Policy Arbiter: every 10 s pick the RL action, the rules action, or no move.

How one decision works:
  1. Ask both controllers what they would do now: RL (PPO) and rules (greedy_policy).
  2. For each candidate (rl, rules, none) copy the digital twin and play the next `horizon` seconds.
  3. Pick the candidate with the lowest predicted wait, but stay with RL unless another option
     is clearly better (by `margin`). Do nothing when acting is not worth it.
  4. Log the choice with a one-line reason.

Used exactly like the RL policy: action = arbiter(obs, sim).
If anything goes wrong (error, too slow) it falls back to the rules action, so a run never crashes.
"""
from __future__ import annotations

import copy
import os
import time

import numpy as np

from .sim import DECISION_S, MOVES, ZONES, greedy_policy

SOURCES = ("rl", "rules", "none")


def describe(a: int) -> str:
    if a <= 0:
        return "no move"
    s, d = MOVES[a - 1]
    return f"move {ZONES[s]}->{ZONES[d]}"


class Arbiter:
    def __init__(self, rl, rules=greedy_policy, horizon=180, margin=0.05, rollouts=3, min_gain=0.01,
                 budget_s=1.0, seed=0, force_fail=None):
        self.rl, self.rules = rl, rules
        self.horizon = horizon          # seconds played in the twin for each candidate
        self.margin = margin            # another option must beat RL by this share to replace it
        self.rollouts = rollouts        # demand samples averaged per candidate
        self.min_gain = min_gain        # acting must beat "no move" by this share, else do nothing
        self.budget_s = budget_s        # time allowed per decision before falling back to rules
        self.seed = seed
        self.force_fail = os.environ.get("ARBITER_FORCE_FAIL") == "1" if force_fail is None else force_fail
        self.decisions: list[dict] = []

    # ------------------------------------------------------------ interface
    def __call__(self, obs, sim) -> int:
        t0 = time.perf_counter()
        try:
            a, source, pred, reason = self._decide(obs, sim, t0)
        except Exception as e:                       # includes TimeoutError: never crash the real run
            try:
                a = int(self.rules(obs, sim))
            except Exception:
                a = 0
            source, pred, reason = "rules", {}, f"fallback: {type(e).__name__}: {e}"
        self.decisions.append({"t": int(sim.t), "source": source, "action": a, "move": describe(a), "pred": pred,
                               "reason": reason, "ms": round(1000 * (time.perf_counter() - t0), 1)})
        return a

    def counts(self) -> dict:
        return {s: sum(d["source"] == s for d in self.decisions) for s in SOURCES}

    # ------------------------------------------------------------ decision
    def _decide(self, obs, sim, t0):
        if self.force_fail:
            raise RuntimeError("forced failure (ARBITER_FORCE_FAIL)")
        acts = {"rl": int(self.rl(obs, sim)), "rules": int(self.rules(obs, sim)), "none": 0}
        pols = {"rl": self.rl, "rules": self.rules, "none": None}
        demand = self._sample_demand(sim)
        pred, moved = {}, {}
        for src in SOURCES:
            res = [self._rollout(sim, acts[src], pols[src], arr, t0) for arr in demand]
            pred[src] = round(float(np.mean([r[0] for r in res])), 1)
            moved[src] = res[0][1]
        pred = {k: pred[k] for k in SOURCES}

        pick = "rl"
        alt = min(("rules", "none"), key=pred.get)
        if pred[alt] < pred["rl"] * (1 - self.margin):
            pick = alt
        if pick != "none" and pred["none"] <= pred[pick] * (1 + self.min_gain):
            pick = "none"                            # acting is predicted to help less than min_gain
        a = acts[pick]

        vs = " vs ".join(f"{'RL' if k == 'rl' else k} {pred[k]:.0f}s" for k in SOURCES if k != pick)
        lead = f"Chose {'RL' if pick == 'rl' else pick} ({describe(a)}): predicted wait {pred[pick]:.0f}s vs {vs}"
        if pick == "rl" and pred[alt] < pred["rl"]:
            lead += f" (gap under {self.margin:.0%} margin, keep RL)"
        elif pick == "none" and pred["none"] > min(pred.values()):
            lead += f" (acting gains under {self.min_gain:.0%})"
        if acts["rl"] > 0 and not moved["rl"]:
            lead += f"; RL's {describe(acts['rl'])} is not possible"
        return a, pick, pred, lead

    # ------------------------------------------------------------ lookahead
    def _sample_demand(self, sim) -> list:
        """Orders for the next `horizon` seconds, drawn from what the twin knows NOW.

        The real sim has the whole day's orders drawn up front; using them would be peeking at the
        future. So we draw new orders from the 60 s forecast x web signal (same idea as greedy_policy).
        One separate RNG, seeded by (seed, t): the real sim's RNG is never touched, and every candidate
        sees the same orders (common random numbers), so differences come from the action, not luck.
        """
        h = min(self.horizon, sim.plan.length - sim.t)
        sig = sim.signal[min(sim.t, sim.plan.length - 1)]
        rate = sim.forecast() * np.maximum(1.0, sig) / 60.0           # orders per second per zone
        rng = np.random.default_rng([self.seed, int(sim.t)])
        return [rng.poisson(rate, size=(h, len(rate))) for _ in range(self.rollouts)]

    def _rollout(self, sim, first_action, policy, arrivals, t0):
        """Play the candidate in a copy of the twin. Returns (predicted avg wait, first move happened).

        After the first step, the same controller keeps driving (rl -> PPO, rules -> greedy, none -> no
        moves) for the whole horizon. This scores "who should be in charge for the next 2 minutes" in a
        fair way: it does not assume RL takes over afterwards, which would tilt every score toward RL.
        """
        # deep copy (RNG included). Read-only tables are shared; past waits, UI history and log start empty.
        keep = {id(x): x for x in (sim.plan, sim.rates, sim.signal)}
        c = copy.deepcopy(sim, {**keep, id(sim.waits): [], id(sim.history): [], id(sim.log): []})
        start, h = c.t, len(arrivals)
        c.arrivals[start:start + h] = arrivals
        moved = c.apply_action(first_action) is not None
        while c.t < start + h:
            c.step_second()
            if (c.t - start) % DECISION_S == 0 and c.t < start + h:
                if time.perf_counter() - t0 > self.budget_s:
                    raise TimeoutError(f"lookahead over {self.budget_s}s budget")
                if policy is not None:
                    c.apply_action(policy(c.observation(), c))
        w = c.waits + [c.t - b for q in c.queues for b in q]
        return (float(np.mean(w)) if w else 0.0), moved
