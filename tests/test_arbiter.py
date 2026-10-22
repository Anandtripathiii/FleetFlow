"""Round 2 Policy Arbiter tests. Run: py -m pytest -q"""
import copy
import hashlib
import os

import numpy as np
import pytest

from core.arbiter import Arbiter
from core.policy import NumpyPolicy
from core.sim import N_ACTIONS, WarehouseSim, make_plan, run_episode

ROOT = os.path.dirname(os.path.dirname(__file__))
WEIGHTS_SHA256 = "236323d9deb077274a652126a450fff5666bafe060c2b5020862037d6058f153"   # Round 1 PPO, never retrained


@pytest.fixture(scope="module")
def pol():
    return NumpyPolicy.load()


def busy_sim(steps=300):
    sim = WarehouseSim(make_plan("mixed", rng=np.random.default_rng(1)), 1)
    for _ in range(steps):
        sim.step_second()
    return sim


def test_same_interface_and_valid_actions(pol):
    arb, sim = Arbiter(pol, horizon=60), busy_sim()
    for _ in range(5):
        a = arb(sim.observation(), sim)               # same call as the RL policy: (obs, sim) -> int
        assert isinstance(a, int) and 0 <= a < N_ACTIONS
        sim.apply_action(a)
        for _ in range(10):
            sim.step_second()
    assert {"t", "source", "pred", "reason"} <= set(arb.decisions[0])


def test_lookahead_does_not_touch_real_sim(pol):
    sim = busy_sim()
    before = (copy.deepcopy(sim.snapshot()), copy.deepcopy(sim.queues), sim.arrivals.copy(), len(sim.waits),
              len(sim.log), sim.recent.copy(), copy.deepcopy(sim.rng.bit_generator.state))
    Arbiter(pol)(sim.observation(), sim)
    after = (sim.snapshot(), sim.queues, sim.arrivals, len(sim.waits), len(sim.log), sim.recent, sim.rng.bit_generator.state)
    assert before[0] == after[0] and before[1] == after[1] and before[3:5] == after[3:5] and before[6] == after[6]
    assert np.array_equal(before[2], after[2]) and np.array_equal(before[5], after[5])


@pytest.mark.parametrize("how", ["flag", "env"])
def test_force_fail_uses_rules_fallback(pol, monkeypatch, how):
    if how == "env":
        monkeypatch.setenv("ARBITER_FORCE_FAIL", "1")
    arb = Arbiter(pol, force_fail=True if how == "flag" else None)
    plan = make_plan("spike", rng=np.random.default_rng(0))
    sim = run_episode(plan, 0, arb)
    assert sim.t == plan.length                       # the run finished
    assert arb.decisions and all(d["source"] == "rules" and d["reason"].startswith("fallback: ") for d in arb.decisions)


def test_timeout_falls_back(pol):
    arb, sim = Arbiter(pol, budget_s=0.0), busy_sim()
    arb(sim.observation(), sim)
    assert arb.decisions[-1]["reason"].startswith("fallback: TimeoutError")


def test_log_explains_rl_rules_and_none(pol):
    arb = Arbiter(pol)
    run_episode(make_plan("mixed", rng=np.random.default_rng(1)), 1, arb)
    for src in ("rl", "rules", "none"):
        rows = [d for d in arb.decisions if d["source"] == src]
        assert rows, f"no {src} decision"
        assert all(d["reason"].startswith("Chose ") and set(d["pred"]) == {"rl", "rules", "none"} for d in rows)


def test_policy_weights_unchanged():
    with open(os.path.join(ROOT, "core", "policy_weights.npz"), "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == WEIGHTS_SHA256
