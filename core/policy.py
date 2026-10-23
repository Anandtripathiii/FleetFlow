"""Run the trained PPO policy with numpy only (no torch needed in the web app)."""
import os
from functools import lru_cache

import numpy as np

from .sim import greedy_policy

HERE = os.path.dirname(__file__)


class NumpyPolicy:
    def __init__(self, w):
        self.w = w

    @classmethod
    def load(cls, path=None):
        path = path or os.path.join(HERE, "policy_weights.npz")
        if not os.path.exists(path):
            return None
        d = np.load(path)
        return cls({k: d[k] for k in d.files})

    def logits(self, obs):
        w = self.w
        h = np.tanh(w["w0"] @ obs + w["b0"])
        h = np.tanh(w["w1"] @ h + w["b1"])
        return w["w2"] @ h + w["b2"]

    def __call__(self, obs, sim=None):
        return int(np.argmax(self.logits(np.asarray(obs, dtype=np.float32))))


@lru_cache(maxsize=1)
def get_policy():
    """Trained agent if weights exist, otherwise the simple heuristic. Loaded once per process."""
    p = NumpyPolicy.load()
    return (p, "ppo") if p is not None else (greedy_policy, "heuristic")
