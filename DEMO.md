# 3-minute demo script

Before the demo: `git checkout round2-arbiter`, `pip install -r requirements.txt pytest`, and one terminal open in the repo.

## 0:00 - Small, additive change (20 s)
```bash
git diff round1-baseline..round2-arbiter --stat
```
Say: "All new files (the arbiter, an eval script, tests and docs) plus 2 lines in .gitignore. No app code changed,
and the PPO weights are untouched. A test checks their SHA256."

## 0:20 - Run the evaluation (60 s)
```bash
python evaluate.py
```
Say while it runs (about 1 minute): "Every 10 seconds the arbiter tries the RL move, the rules move and no move
in our digital twin, plays 3 minutes ahead for each, and keeps the best one."
Point at the **mixed** row: rules 235.7 s, RL 252.5 s, **arbiter 91.2 s**, PASS. The other rows show the arbiter
within 5% of RL or better. The last line reads `ALL CHECKS PASS`.

## 1:20 - Why did it choose that? (30 s)
```bash
head -3 results/sample_log_mixed_1.jsonl
```
Read one line out loud, for example
`Chose rules (move B->D): predicted wait 19s vs RL 30s vs none 23s`.
Say: "Each decision has its source and a plain-English reason."
On Windows PowerShell use `Get-Content results/sample_log_mixed_1.jsonl -TotalCount 3`.

## 1:50 - Make it fail on purpose (30 s)
```bash
python evaluate.py --force-fail --scenarios mixed
head -1 results/force_fail/logs/mixed_0.jsonl
```
Say: "The lookahead now crashes on every decision. The run still finishes on the rules, and the log says
`fallback: RuntimeError: forced failure`. The warehouse never stops."

## 2:20 - The app still works (40 s)
```bash
python -m pytest -q
python app.py
```
11 tests pass. Open http://127.0.0.1:5000, log in as `manager@fleetflow.in` / `manager123`, show
**Fleet** and the **Simulation Lab** (mixed scenario). Both work exactly as in Round 1.
