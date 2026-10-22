"""Evaluate rules, RL and the Policy Arbiter on the 6 scenarios x 10 seeds.

Run:   py evaluate.py                     full eval -> results/eval.json, results/eval.md, results/logs/
       py evaluate.py --sweep 60,120,180  try several horizons (rules/RL run once) -> results/sweep.json
       py evaluate.py --force-fail        lookahead always fails -> every decision uses the rules fallback

Same logic as train_rl.evaluate() (same plans, seeds and metric), without needing torch.
"""
import argparse
import json
import os
import time
from multiprocessing import Pool

import numpy as np

from core.arbiter import SOURCES, Arbiter, DecisionLog
from core.policy import NumpyPolicy
from core.sim import SCENARIOS, make_plan, run_episode

TOL = 0.05      # other scenarios: arbiter must be within 5% of RL


def run_one(job):
    sc, sd, horizons, cfg = job
    plan = make_plan(sc, rng=np.random.default_rng(sd))
    pol = NumpyPolicy.load()
    rules, rl = DecisionLog(None, "rules"), DecisionLog(pol, "rl")     # only records; actions pass through
    out = {"scenario": sc, "seed": sd, "rules": run_episode(plan, sd, rules).metrics()["avg_wait_s"],
           "rl": run_episode(plan, sd, rl).metrics()["avg_wait_s"], "arbiter": {},
           "rules_decisions": rules.decisions, "rl_decisions": rl.decisions}
    for h in horizons:
        arb = Arbiter(pol, horizon=h, **cfg)
        out["arbiter"][h] = {"wait": run_episode(plan, sd, arb).metrics()["avg_wait_s"], "decisions": arb.decisions}
    return out


def write_jsonl(path, rows):
    with open(path, "w") as f:
        for d in rows:
            f.write(json.dumps(d) + "\n")


def table(runs, h):
    res = {}
    for sc in SCENARIOS:
        rs = [r for r in runs if r["scenario"] == sc]
        if not rs:
            continue
        row = {k: round(float(np.mean([r[k] for r in rs])), 1) for k in ("rules", "rl")}
        row["arbiter"] = round(float(np.mean([r["arbiter"][h]["wait"] for r in rs])), 1)
        if sc == "mixed":
            row["check"] = "arbiter <= rules"
            row["pass"] = row["arbiter"] <= row["rules"]
        else:
            row["check"] = f"arbiter within {TOL:.0%} of RL"
            row["pass"] = row["arbiter"] <= row["rl"] * (1 + TOL)
        row["decisions"] = {s: sum(d["source"] == s for r in rs for d in r["arbiter"][h]["decisions"]) for s in SOURCES}
        res[sc] = row
    return res


def show(res, h):
    print(f"\nhorizon {h}s   (average wait in seconds over the seeds, lower is better)")
    print(f"{'scenario':10}{'rules':>8}{'RL':>8}{'arbiter':>9}   check                      result   rl/rules/none")
    for sc, r in res.items():
        d = r["decisions"]
        print(f"{sc:10}{r['rules']:8}{r['rl']:8}{r['arbiter']:9}   {r['check']:27}{'PASS' if r['pass'] else 'FAIL':9}"
              f"{d['rl']}/{d['rules']}/{d['none']}")


def markdown(res, h, cfg, secs):
    lines = [f"# Policy Arbiter evaluation", "",
             f"Average queue wait in seconds over seeds {cfg['seeds']} (lower is better). "
             f"Horizon {h}s, margin {cfg['margin']:.0%}, rollouts {cfg['rollouts']}, runtime {secs:.0f}s.", "",
             "|Scenario|Rules|RL|Arbiter|Check|Result|Decisions rl / rules / none|", "|-|-:|-:|-:|-|-|-|"]
    for sc, r in res.items():
        d = r["decisions"]
        lines.append(f"|{sc}|{r['rules']}|{r['rl']}|**{r['arbiter']}**|{r['check']}|{'PASS' if r['pass'] else 'FAIL'}|"
                     f"{d['rl']} / {d['rules']} / {d['none']}|")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=180)
    ap.add_argument("--sweep", help="comma list of horizons to compare, e.g. 60,120,180")
    ap.add_argument("--margin", type=float, default=0.05)
    ap.add_argument("--rollouts", type=int, default=3)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--seeds", default="0-9", help="e.g. 0-9 or 0,3")
    ap.add_argument("--force-fail", action="store_true", help="make every lookahead raise (tests the fallback)")
    ap.add_argument("--out", default=None, help="results folder (default results/, or results/force_fail/)")
    a = ap.parse_args()

    seeds = list(range(int(a.seeds.split("-")[0]), int(a.seeds.split("-")[1]) + 1)) if "-" in a.seeds \
        else [int(x) for x in a.seeds.split(",")]
    horizons = [int(x) for x in a.sweep.split(",")] if a.sweep else [a.horizon]
    cfg = {"margin": a.margin, "rollouts": a.rollouts, "force_fail": a.force_fail or None}
    jobs = [(sc, sd, horizons, cfg) for sc in a.scenarios.split(",") for sd in seeds]

    t0 = time.time()
    with Pool(min(len(jobs), os.cpu_count() or 1)) as pool:
        runs = pool.map(run_one, jobs)
    secs = time.time() - t0

    results = {h: table(runs, h) for h in horizons}
    for h in horizons:
        show(results[h], h)
    print(f"\nruntime {secs:.1f}s for {len(jobs)} scenario/seed runs x (rules + RL + {len(horizons)} arbiter)")

    out = a.out or ("results/force_fail" if a.force_fail else "results")
    os.makedirs(out, exist_ok=True)
    meta = {"seeds": seeds, "margin": a.margin, "rollouts": a.rollouts, "force_fail": a.force_fail}
    if a.sweep:
        with open(os.path.join(out, "sweep.json"), "w") as f:
            json.dump({**meta, "runtime_s": round(secs, 1), "results": results}, f, indent=1)
        return

    h = horizons[0]
    res = results[h]
    os.makedirs(os.path.join(out, "logs"), exist_ok=True)
    for r in runs:
        name = f"{r['scenario']}_{r['seed']}.jsonl"
        write_jsonl(os.path.join(out, "logs", name), r["arbiter"][h]["decisions"])
        write_jsonl(os.path.join(out, "logs", "rl_" + name), r["rl_decisions"])
        write_jsonl(os.path.join(out, "logs", "rules_" + name), r["rules_decisions"])
    total = {s: sum(r["decisions"][s] for r in res.values()) for s in SOURCES}
    with open(os.path.join(out, "eval.json"), "w") as f:
        json.dump({**meta, "horizon": h, "runtime_s": round(secs, 1), "decisions": total, "results": res}, f, indent=1)
    with open(os.path.join(out, "eval.md"), "w") as f:
        f.write(markdown(res, h, meta, secs) + f"\nAll decisions: rl {total['rl']}, rules {total['rules']}, none {total['none']}.\n")
    print(f"decisions: {total}   saved {out}/eval.json, eval.md, logs/")
    print("ALL CHECKS PASS" if all(r["pass"] for r in res.values()) else "SOME CHECKS FAIL")


if __name__ == "__main__":
    main()
