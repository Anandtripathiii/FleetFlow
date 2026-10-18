"""Train the PPO rebalancing agent and export it to plain numpy weights.

Run:  python train_rl.py            (about 10 minutes on a laptop CPU)
Output: core/policy_weights.npz  (the web app only needs numpy to use it)

Needs: pip install stable-baselines3 gymnasium torch
"""
import json
import os
import sys

import gymnasium as gym
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

from core.sim import (DECISION_S, N_ACTIONS, SCENARIOS, WarehouseSim, make_plan, run_episode)
from core.policy import NumpyPolicy

EP_LEN = 1200


class WarehouseEnv(gym.Env):
    """One step = one decision every 10 s of warehouse time."""

    def __init__(self, seed=0):
        super().__init__()
        self.observation_space = gym.spaces.Box(0.0, 3.0, shape=(16,), dtype=np.float32)
        self.action_space = gym.spaces.Discrete(N_ACTIONS)
        self.rng = np.random.default_rng(seed)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        sc = "random" if self.rng.random() < 0.75 else str(self.rng.choice(list(SCENARIOS)))
        plan = make_plan(sc, EP_LEN, self.rng)
        self.sim = WarehouseSim(plan, int(self.rng.integers(1_000_000)))
        return self.sim.observation(), {}

    def step(self, action):
        s = self.sim
        moved = s.apply_action(int(action))
        penalty = 0.0
        if int(action) > 0:
            penalty += 0.15 if moved is not None else 0.05     # empty driving / impossible move
        queue_seconds = 0
        for _ in range(DECISION_S):
            if s.t >= s.plan.length:
                break
            s.step_second()
            queue_seconds += sum(len(q) for q in s.queues)
        reward = -queue_seconds / 600.0 - penalty
        done = s.t >= s.plan.length
        return s.observation(), float(reward), done, False, {}


def export(model, path="core/policy_weights.npz"):
    sd = {k: v.detach().cpu().numpy() for k, v in model.policy.state_dict().items()}
    np.savez(path,
             w0=sd["mlp_extractor.policy_net.0.weight"], b0=sd["mlp_extractor.policy_net.0.bias"],
             w1=sd["mlp_extractor.policy_net.2.weight"], b1=sd["mlp_extractor.policy_net.2.bias"],
             w2=sd["action_net.weight"], b2=sd["action_net.bias"])
    print("saved", path)


def evaluate(policy, seeds=range(5)):
    out = {}
    for sc in SCENARIOS:
        r, a = [], []
        for sd in seeds:
            plan = make_plan(sc, rng=np.random.default_rng(sd))
            r.append(run_episode(plan, sd).metrics()["avg_wait_s"])
            a.append(run_episode(plan, sd, policy).metrics()["avg_wait_s"])
        out[sc] = {"rules": round(float(np.mean(r)), 1), "rl": round(float(np.mean(a)), 1),
                   "better_pct": round(100 * (1 - np.mean(a) / np.mean(r)), 1)}
    return out


def score(res):
    """Average improvement, but a scenario where we lose to the rules counts double."""
    vals = [r["better_pct"] for r in res.values()]
    return float(np.mean([v if v >= 0 else 2 * v for v in vals]))


if __name__ == "__main__":
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 1_500_000
    env = VecNormalize(DummyVecEnv([lambda i=i: WarehouseEnv(seed=i) for i in range(8)]),
                       norm_obs=False, norm_reward=True, gamma=0.99)
    model = PPO("MlpPolicy", env, n_steps=1024, batch_size=512, n_epochs=10,
                learning_rate=lambda p: float(os.environ.get("LR", 3e-4)) * max(p, 0.1), clip_range=0.15, 
                gamma=0.99, gae_lambda=0.95, ent_coef=0.005, verbose=0, seed=0)
    import os, torch
    torch.set_num_threads(1)
    init = os.environ.get("INIT_FROM")
    if init:                                     # continue from an exported agent (keeps what it learned)
        w = np.load(init)
        sd = model.policy.state_dict()
        for k, key in [("w0", "mlp_extractor.policy_net.0.weight"), ("b0", "mlp_extractor.policy_net.0.bias"),
                       ("w1", "mlp_extractor.policy_net.2.weight"), ("b1", "mlp_extractor.policy_net.2.bias"),
                       ("w2", "action_net.weight"), ("b2", "action_net.bias")]:
            sd[key] = torch.tensor(w[k])
        model.policy.load_state_dict(sd)
        print("started from", init, flush=True)
    chunk, done, best = 100_000, 0, -1e9
    while done < steps:
        model.learn(chunk, reset_num_timesteps=False)
        done += chunk
        export(model, "core/_candidate.npz")
        res = evaluate(NumpyPolicy.load("core/_candidate.npz"), seeds=range(100, 106))
        sc = score(res)
        tag = ""
        if sc > best:
            best = sc
            export(model)
            model.save("core/ppo_model")
            tag = "  <- best so far, saved"
        print(done, round(sc, 1), json.dumps({k: v["better_pct"] for k, v in res.items()}), tag, flush=True)
    final = evaluate(NumpyPolicy.load(), seeds=range(10))
    with open("core/eval.json", "w") as f:
        json.dump(final, f, indent=1)
    print(json.dumps(final, indent=1))
