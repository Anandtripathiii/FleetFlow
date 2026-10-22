# 3-minute demo script

Note: the official brief PDF (`docs/round2_brief.pdf`) was not in the repo when this was written. This is
our own script, built from the acceptance criteria in `docs/round2_brief.md`. If the brief has its own
script, follow that one.

Before the demo:
* Run `git checkout round2-arbiter` and `pip install -r requirements.txt pytest`.
* Run `python evaluate.py` once, so `results/` is fresh.
* Start the app in a second terminal with `python app.py`. Open the Simulation Lab once, so the first run is already cached.

## 0:00 - Small, additive change (20 s)
```bash
git diff round1-baseline..round2-arbiter --stat
```
(also saved in `results/diff_vs_round1.txt`)

Say: "Almost everything is new files: the arbiter, an eval script, tests and docs. Four existing files changed:
2 lines in .gitignore, one extra field in the Lab API, and the Lab page for the third lane. The PPO weights are
untouched; a test checks their SHA256."

## 0:20 - Run the evaluation (60 s)
```bash
python evaluate.py
```
Say while it runs (about 40-50 s): "Every 10 seconds the arbiter tries the RL move, the rules move and no move
in our digital twin, plays 3 minutes ahead for each, and keeps the best one."

Point at the **mixed** row: rules 235.7 s, RL 252.5 s, **arbiter 91.2 s**, PASS. The other five rows show the arbiter
within 5% of RL or better. The last line reads `ALL CHECKS PASS`.

## 1:20 - Why did it choose that? (25 s)
```bash
head -3 results/sample_log_mixed_1.jsonl
```
On Windows PowerShell: `Get-Content results/sample_log_mixed_1.jsonl -TotalCount 3`.

Read one line out loud, for example `Chose rules (move B->D): predicted wait 19s vs RL 30s vs none 23s`.
Say: "Every decision has a source and a plain-English reason."

## 1:45 - Make it fail on purpose (25 s)
```bash
python evaluate.py --force-fail --scenarios mixed
head -1 results/force_fail/logs/mixed_0.jsonl
```
Say: "The lookahead now crashes on every decision. The run still finishes on the rules, and the log says
`fallback: RuntimeError: forced failure`. The warehouse never stops."

## 2:10 - See it live (50 s)
Open http://127.0.0.1:5000 and log in as `manager@fleetflow.in` / `manager123`. The team accounts work too: `anand@` (owner), `akshat@` and `saavya@` (managers), `anurag@` (staff), with passwords from the team lead.

1. Open **Robot fleet**: it works as in Round 1.
2. Open **Simulation lab**, pick **Mixed day**, then press **Run all**.
3. Point at the three lanes (rules, RL, arbiter) and the blue arbiter line in the chart, which ends lowest.
4. Scroll the **Arbiter decisions** card: rl / rules / none badges, each with its reason.
5. The Lab now shows decision logs for all three policies: point at **Rules decisions** and **RL decisions** below it. Note that most RL entries are NONE because RL asks for moves that can't happen.

If time is left: `python -m pytest -q` shows 11 passed.
