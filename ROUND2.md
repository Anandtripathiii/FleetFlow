# Round 2: Policy Arbiter

## What it does
Every 10 seconds the warehouse can make one robot move. Round 1 had two ways to decide:

* **RL**: our trained PPO agent. It is good on normal days but worse than the rules on the "mixed" day (it never saw shuffled busy zones during training).
* **Rules**: a simple hand-written rule (`greedy_policy`: send a robot from the quietest zone to the busiest one).

The **Policy Arbiter** (`core/arbiter.py`) does not replace either one. At each decision it asks both,
then tries three options in the digital twin: **the RL move, the rules move, and no move**.
It plays the next 3 minutes for each option, picks the one with the lowest predicted waiting time,
and writes down why, for example:

```
Chose rules (move B->D): predicted wait 19s vs RL 30s vs none 23s
```

It is used exactly like the RL policy (`action = arbiter(obs, sim)`), so it plugs in anywhere a policy is used.
We did **not** retrain PPO and did not change `policy_weights.npz` (a test checks its SHA256).

## Results (6 scenarios x 10 seeds, average wait in seconds, lower is better)

|Scenario|Rules|RL|**Arbiter**|Check|Result|
|-|-:|-:|-:|-|-|
|normal|47.1|25.1|**21.8**|within 5% of RL|PASS|
|spike|334.5|256.3|**256.4**|within 5% of RL|PASS|
|moving|319.2|263.9|**251.9**|within 5% of RL|PASS|
|rush|231.9|154.4|**134.6**|within 5% of RL|PASS|
|failure|376.7|321.8|**321.6**|within 5% of RL|PASS|
|**mixed**|235.7|252.5|**91.2**|arbiter <= rules|PASS|

* **Mixed day:** waits drop from 252.5 s (RL) and 235.7 s (rules) to **91.2 s**.
* **The other days:** the arbiter is as good as RL or better.
* **Decisions over all 60 runs:** rl 502, rules 1032, none 7466. Most of the time the best choice is not to move, because one move costs 40 s of driving.
* **Runtime:** the full eval (60 runs x rules + RL + arbiter) takes **39-52 s** on a 16-core laptop (39 s in the final run). A single episode takes about 3 s.
* **Speed per decision:** the slowest decision took 192 ms, against a 1 s budget. No fallbacks happened in the normal run.

The "before" numbers from the Round 1 code are in `results/round1_baseline.json`. The full table is in
`results/eval.md` and `results/eval.json`, and a sample decision log is `results/sample_log_mixed_1.jsonl`.

## Design choices

**Lookahead in the digital twin.**
* We deep-copy the simulator, its random generator included, so the real run is never changed. A test checks this.
* The real simulator draws the whole day's orders at the start. Copying those would let the arbiter see the future, which is cheating.
* So the copy gets **new** orders for the next 3 minutes, drawn from what we know right now: the last 60 s of demand multiplied by the web signal.

**Common random numbers.**
* For one decision, all three options play against the **same** sampled orders (3 samples, averaged).
* The difference between the options then comes from the action, not from luck.
* A separate RNG, seeded by (seed, time), keeps runs repeatable.

**What happens after the first step.**
* Each option keeps its own controller for the whole horizon: RL keeps driving with PPO, rules with the greedy rule, and "none" makes no moves.
* This asks the fair question "who should be in charge for the next 3 minutes?".
* If RL took over after the first step in every option, the scores would lean toward RL.
* Side effect: sometimes the log says `Chose rules (no move)`. That means the rules make no move right now, but letting the rules drive the next 3 minutes is predicted to beat the other options.

**Horizon: 180 s.** We tested 60, 120 and 180 s (`results/sweep.json`):

|Horizon|normal|spike|moving|rush|failure|mixed|All checks|
|-|-:|-:|-:|-:|-:|-:|-|
|60 s|24.9|282.5|256.1|156.1|332.8|107.0|FAIL (spike)|
|120 s|23.0|260.9|261.4|134.7|324.9|91.6|PASS|
|**180 s**|**21.8**|**256.4**|**251.9**|**134.6**|**321.6**|**91.2**|PASS|

60 s is too short: a move takes 40 s, so the twin barely sees the benefit before the horizon ends.

**Bias toward RL (margin 5%).** RL stays in charge unless rules or "no move" is predicted to be at least 5% better.
This avoids flipping on noise.

**No move when acting does not pay.** If the chosen move is predicted to help less than 1% compared with no move,
we do nothing. Moves cost driving time and battery.

**Safety fallback.**
* Each decision has a 1 s time budget.
* On any error or timeout, the arbiter uses the rules action and logs `source: rules, reason: fallback: <error>`. The run never crashes.
* To test this on purpose, use `--force-fail` or `ARBITER_FORCE_FAIL=1`, which make the lookahead raise an error every time.

## Simulation Lab: third lane
The animated Simulation Lab (`/fleet/lab`) now plays three lanes on the same day and seed: **Basic rules**, **Our RL agent**
(both unchanged) and **Policy arbiter**. Next to the arbiter lane, an **Arbiter decisions** card lists every
10 s choice with its source (rl / rules / none) and reason. The wait-over-time chart has a third line.
The Lab now shows decision logs for all three policies: **Rules decisions** and **RL decisions** cards sit below the arbiter (also saved as `results/logs/rules_<scenario>_<seed>.jsonl` and `rl_<scenario>_<seed>.jsonl`), with reasons built from the live zone state.
It reuses the existing canvas, metric tiles, `.log` list and badges. Nothing was redesigned.

The first run of a scenario takes a few seconds longer, because the arbiter plays its lookahead.
After that the result is cached, as before.

## What changed in existing files
Everything else in Round 2 is a new file (see `results/diff_vs_round1.txt`). Only these existing files changed:
* `.gitignore`: 2 lines, so the generated logs are not committed.
* `routes/fleet.py`: `/fleet/api/sim` returns one extra `arbiter` field. The URL, parameters and the existing fields are the same.
* `templates/fleet/lab.html` and `static/js/lab.js`: the third lane (Phase 6, stretch goal).

No change to the SQLite schema, other routes, the PPO model, scenarios, TinyFish or deployment.

## How to run
```bash
pip install -r requirements.txt pytest
python evaluate.py                         # table + PASS/FAIL, writes results/eval.json, eval.md, logs/
python evaluate.py --sweep 60,120,180      # compare horizons -> results/sweep.json
python evaluate.py --force-fail            # every decision uses the rules fallback -> results/force_fail/
ARBITER_FORCE_FAIL=1 python evaluate.py    # same, with the env var
python evaluate.py --scenarios mixed --seeds 0-2 --margin 0.1 --rollouts 2   # quick custom run
python -m pytest -q                        # 11 tests: arbiter + app regression + weights hash
python app.py                              # the app -> http://127.0.0.1:5000, Simulation Lab at /fleet/lab
```
One decision log per run is saved as `results/logs/<scenario>_<seed>.jsonl`, one line per decision:
`t, source (rl|rules|none), action, move, pred {rl, rules, none}, reason, ms`.

## Accounts
The 3 demo logins (`admin@` / `manager@` / `staff@fleetflow.in`) are unchanged. There are also 4 team accounts:

|Name|Login|Role|
|-|-|-|
|Anand Tripathi|`anand@fleetflow.in`|Owner (everything an admin can do; only an owner can change an owner)|
|Akshat|`akshat@fleetflow.in`|Manager|
|Saavya|`saavya@fleetflow.in`|Manager|
|Anurag|`anurag@fleetflow.in`|Staff|

Their passwords are **not in git**. They come from the environment variables `TEAM_PW_ANAND`, `TEAM_PW_AKSHAT`,
`TEAM_PW_SAAVYA` and `TEAM_PW_ANURAG`: put them in `.env` locally, or in the Render dashboard. The app creates
any missing team account on every start. An account with no password set is skipped.

## Real-world use and scaling
* **Safe way to bring in AI.** A real warehouse can run a learned policy while the twin checks every move
  before a robot drives. When RL is wrong (like on the mixed day), the arbiter quietly uses the rules,
  and every choice has a reason a shift manager can read.
* **Failure is boring by design.** If the twin is slow or breaks, the floor keeps running on the rules.
* **Cost grows linearly.** One decision is 3 options x 3 samples x 180 s of simulation, about 30-80 ms on one CPU core.
  More robots or zones make each simulated second longer, roughly in proportion. The options and samples
  are independent, so they can run in parallel across cores. Fewer samples or a shorter horizon trade
  accuracy for speed (`--rollouts`, `--horizon`).
* **Better twin, better arbiter.** The lookahead only uses live data (recent demand, web signals, robot state).
  A better demand forecast plugs straight into `_sample_demand` without touching the rest.
* **More candidates.** Any controller with the `(obs, sim) -> action` interface can be added as another option,
  for example a newer RL model run safely next to the old one.
