# FleetFlow Technical Disclosure Log

**Project:** FleetFlow, a warehouse brain that uses live web signals and a reinforcement learning agent to move picking robots across zones before demand hits.

**Team:** Doppler Effect (Anand Tripathi, Anurag Singh, Tanishq Tripathi, Saavya Verma, Akshat Mishra)

**Repository:** https://github.com/Anandtripathiii/FleetFlow

**Live site:** https://fleetflow-8x7h.onrender.com

This log lists every tool, library, service, API and AI model that went into building FleetFlow. It is grouped so the judges can skim it quickly. Where something was built by us, we say so. Where something is open source and reused, we name it and link to it.


## 1. What we built ourselves

These are the main pieces of FleetFlow that are our own original code. Nothing here was downloaded or copied from an existing project.

**The warehouse digital twin (`core/sim.py`).** A small simulator of a warehouse floor with 4 zones, 10 picking robots and orders that arrive from a Poisson process. One tick is one second of warehouse time. It also ships with 6 scenarios we wrote (normal, spike, moving, rush, failure, mixed) so the agent can be trained and tested on different kinds of days. The twin is the heart of the project. We use it for training, for the live advice shown on the Fleet page, and for the side by side race in the Simulation Lab.

**The reinforcement learning environment (`train_rl.py`).** A custom Gymnasium environment called `WarehouseEnv`. The observation is 16 numbers (per zone queue length, robots present, demand in the last minute, and the web signal multiplier). The action space has 17 choices (12 robot moves between zones, 4 send home options, and 1 do nothing). We wrote the reward function too (negative total waiting time, minus a small cost per move so the agent does not fidget).

**The trained PPO agent.** We trained a PPO network from scratch on 1.5 million warehouse decisions using Stable Baselines 3. The weights that ship with the app (`core/policy_weights.npz`) are our own. The training log is in `core/train_log_v1.txt`.

**NumPy inference layer (`core/policy.py`).** A small class called `NumpyPolicy` that re implements the forward pass of the PPO network using only NumPy. The live web app never has to import PyTorch, which saves around 500 MB on the deploy image and keeps cold starts fast.

**Policy Arbiter (`core/arbiter.py`).** The main new piece added for Round 2. At every decision the arbiter asks three brains (the RL agent, a simple greedy rule, and no move), deep copies the simulator, plays the next 3 minutes forward for each option using common random numbers, and picks the one with the lowest predicted wait. Every decision is logged with a plain English reason. Our own code, 176 lines. It fixed the one honest weakness in Round 1, where the agent lost by 7 percent on the mixed scenario. With the arbiter, mixed day waits drop from 252 seconds to 91 seconds.

**Evaluation harness (`evaluate.py`).** A command line tool that runs rules, RL and the arbiter on 6 scenarios times 10 unseen seeds, writes `results/eval.json` and `results/eval.md`, and prints PASS or FAIL for each row. It also supports a horizon sweep (`--sweep 60,120,180`), a forced failure mode (`--force-fail`) that proves the fallback works, and parallel runs across CPU cores.

**TinyFish keyword rules (`core/tinyfish.py`).** The code that turns a headline from TinyFish Search into a demand multiplier per zone. The rule table (sale, match, rain, launch, festival and so on) is ours. The client that calls TinyFish is ours too.

**Business rules (`core/ops.py`).** The restock planner, purchase order suggestions, alerts engine, zone pressure calculator, and the digital twin advice wrapper. All original.

**The full web app.** Every page template in `templates/`, every route in `routes/`, the CSS theme in `static/css/app.css`, and the database schema and demo seed in `core/db.py`. All written by us.

**The deck and documentation.** The pitch deck, the README, this disclosure log and the Round 2 brief are all written by the team.


## 2. Programming language and web framework

**Python 3.12.** Used for the entire backend, the training script, the simulator, and the inference layer. We picked Python because the whole ML ecosystem lives here (PyTorch, Stable Baselines 3, Gymnasium, NumPy) and Flask works beautifully for the web side, so we only have to work in one language.

**Flask 3.** The web framework. Chosen because it is small, has almost no boilerplate, and lets us ship a prototype fast. Big frameworks like Django would have been overkill for a 10 route app. We use Flask Blueprints so each section of the site (inventory, shops, transfers, purchasing, fleet, signals, auth) is its own file in `routes/`, cleanly separated.

**Flask Login 0.6.** Handles sessions and the "who is logged in" check across every request. Combined with Werkzeug's password hashing (which comes with Flask), it gives us secure logins without any custom auth code. Three roles (staff, manager, admin) are enforced by a `@role_required` decorator in `core/authz.py`.

**Jinja2 (bundled with Flask).** The HTML templating engine. We use it for every page. No React, no build step, no bundler. The HTML is rendered on the server and sent straight to the browser.

**Gunicorn 22.** A production grade Python web server. Flask's own development server is not meant for real traffic. In production we run `gunicorn app:app --preload --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`, which is what the `render.yaml` tells Render to start.

**Werkzeug (bundled with Flask).** Used for password hashing and for a `ProxyFix` middleware when the app runs behind a proxy on Render.


## 3. Database

**SQLite.** A single file database (`fleetflow.db`) that holds everything. 16 tables defined in `core/db.py` including users, products, shops, shop stock, transfers, purchase orders, movements, robots, robot moves, signals, activity and settings. We chose SQLite because there is no server to run, it deploys in one click on Render's free tier, and we can swap to PostgreSQL later by changing one line in `core/db.py`. The schema is ours. The first run auto fills it with demo data so a judge can log in and see a realistic warehouse right away.


## 4. Machine learning stack

**PyTorch 2.** Used only during training. We never ship PyTorch in production. Why we picked it: Stable Baselines 3 is built on top of it, and the Python ML community lives on PyTorch.

**Stable Baselines 3 (SB3).** The RL library we used to train the PPO agent. SB3 gives us a battle tested PPO implementation so we could focus on the warehouse environment, not the algorithm plumbing. We use its `PPO("MlpPolicy", env)` with a 2 by [64, 64] MLP policy, plus `VecNormalize` for observation scaling.

**Gymnasium (formerly OpenAI Gym).** The standard way to describe an RL environment. SB3 expects it, and every RL researcher reads it fluently. Our `WarehouseEnv` inherits `gym.Env`.

**NumPy 1.26.** The only ML library the live web app actually imports. `core/policy.py` uses NumPy to run the PPO forward pass from `core/policy_weights.npz`. NumPy is also used in `core/sim.py` for order generation, and in `core/arbiter.py` for the lookahead rollouts.


## 5. External APIs and services

**TinyFish Search API.** A real time web search API. We call it from `core/tinyfish.py` with queries like "Delhi sale this week", "IPL match tonight" and "heavy rain Delhi". Each returned headline passes through our keyword rules and becomes a demand multiplier per zone, which the AI agent uses as part of its observation. Shown on the Demand Signals page with the original headline so every signal explains itself. Endpoint: `GET https://api.search.tinyfish.ai`.

**TinyFish Web Agent API.** A browser automation API that reads any public web page and returns clean JSON. We use it on the Purchasing page to scrape supplier product pages for live price and stock, which lets us compare the real market price against our purchase order. Endpoint: `POST https://agent.tinyfish.ai/v1/automation/run`.

Both TinyFish keys live in Render environment variables and the local `.env`, never in git. If TinyFish is down or the key is missing, the app keeps working with the last saved signals plus any manual ones a manager added.


## 6. Frontend libraries

**Chart.js 4.** A lightweight charting library. Bundled locally in `static/vendor/chart.min.js` so the site works even if a CDN is blocked. We use it on the dashboard (14 day stock flow), in the Simulation Lab (the Rules vs RL vs Arbiter race chart) and in Reports (robot workload bar chart).

**Figtree font.** A clean modern open source typeface by Erik Kennedy. WOFF2 files sit in `static/fonts/` and are loaded through `@font-face` in `static/css/app.css`. Bundled locally for the same offline reason.

**Hand written CSS.** No framework like Tailwind or Bootstrap. We wanted a specific brand look (light theme, bold grid paper login page, Doppler Effect wordmark) and a framework would have fought us. Single stylesheet in `static/css/app.css`. Light vanilla JS where needed (modals, confirm dialogs, chart updates), no React, no bundler.


## 7. Testing and quality

**pytest.** Used to write unit and integration tests under `tests/`. We have tests for the arbiter (`test_arbiter.py`), the app smoke tests (`test_app.py`), the team account creation flow (`test_team_accounts.py`), and a performance test (`test_perf.py`). One test checks the SHA256 of `policy_weights.npz` so we can prove to a judge that we did not quietly retrain the PPO weights for Round 2. Run with `python -m pytest -q`.


## 8. Hosting and deployment

**Render (free tier).** Hosts the live site. We picked Render because it has a free plan, auto SSL, a one click Blueprint deploy from GitHub (reads `render.yaml`), and the Python runtime does not need a Dockerfile. Deploy pipeline: push to GitHub, Render auto builds with `pip install -r requirements.txt`, then runs the Gunicorn command.

**GitHub.** Source control and remote repository. The entire project is public at https://github.com/Anandtripathiii/FleetFlow. Judges can read every commit.

**Git.** Local version control.


## 9. AI models used

**One trained from scratch:**

* **PPO (Proximal Policy Optimization) agent, trained by us.** Our own weights, built using Stable Baselines 3 and PyTorch on the warehouse environment we wrote. 1.5 million training steps on a laptop CPU, about 30 minutes. The checkpoint we ship (`core/policy_weights.npz`) is the one that scored best on 20 held out seeds across all scenarios.

**No pretrained models were used at inference time.** FleetFlow does not call any hosted LLM, image model or embedding model in the running app. All intelligence in production comes from our own PPO network, the digital twin planner, and the keyword rules that convert TinyFish headlines into demand multipliers.


## 10. AI assistance during development

**Claude (Anthropic) and Claude Code.** We used Claude as a coding and writing assistant throughout development. It helped with brainstorming the arbiter design, drafting documentation (including large parts of this log, which we then edited), writing boilerplate for the Flask routes, and spotting bugs in the Simulation Lab animation code. Every suggestion was reviewed, edited and tested before it went into the repository. No code written by Claude was committed without a human on the team reading it line by line. Model used: Claude Opus.

**GitHub (web interface).** For reading diffs, viewing pull requests and managing the repository.

**VS Code.** The editor the whole team used.

No other AI coding assistants (Copilot, Cursor, Codex, Replit AI etc.) were used.


## 11. MCP servers used during development

**None at runtime.** FleetFlow does not run any Model Context Protocol server in production.

**During development** we used the TinyFish MCP server inside Claude Code to prototype queries against the Search and Web Agent endpoints before copying the final call signatures into `core/tinyfish.py`. Only the HTTP clients ended up in the shipped app.


## 12. Open source libraries (full list from `requirements.txt`)

Production (installed on Render):

* Flask 3.0 or later, web framework
* Flask Login 0.6 or later, session management
* NumPy 1.26 or later, matrix math for the PPO inference layer and the simulator
* Gunicorn 22 or later, production web server

Training only (installed with `requirements-train.txt`, never shipped):

* PyTorch, deep learning backend
* Stable Baselines 3, PPO implementation
* Gymnasium, RL environment interface

Testing only:

* pytest

Bundled in the repo (not pip installed):

* Chart.js 4 at `static/vendor/chart.min.js`
* Figtree font files at `static/fonts/`

Every single one is open source and widely used. No proprietary paid library.


## 13. Code reused from other repositories

The only external code we reuse is the open source libraries listed above, installed via pip or bundled as a single file (Chart.js, Figtree). We did not copy, fork, or adapt code from any GitHub starter template, boilerplate repository, or other hackathon project. The Flask app layout, the database schema, the simulator, the PPO environment, the arbiter, the evaluation harness, the templates and the CSS theme are all original to this project.


## 14. Design and planning documents

We kept the paperwork light so most of our time went into building. Documents in the repository:

* `README.md`, the main project description with setup instructions, implementation notes and benchmark results
* `ROUND2.md`, the Round 2 brief answering what changed, why, with the arbiter design and results
* `DEMO.md`, a 3 minute demo script we practise with before judging rounds
* `docs/round2_brief.md`, the Round 2 problem statement we worked from
* `log.md` (this file)

A pitch deck (PPTX) is kept outside the repository and sent to judges separately. It covers the problem, the solution, the AI, the SDG alignment and the roadmap.


## 15. Datasets used

**No external datasets.** The warehouse data (products, shops, stock levels, two weeks of movement history) is generated by our own demo seed function in `core/db.py` on the first run. The PPO agent is trained purely on episodes generated by our digital twin, which draws orders from a Poisson distribution with scenario specific rates. No scraped data, no customer data, no third party dataset.


## 16. Where to find each thing in the repository

A quick map so a judge can jump straight to the proof:

| What | File or folder |
|---|---|
| Flask app entry point | `app.py` |
| Database schema and demo seed | `core/db.py` |
| Login, roles, permissions | `core/authz.py` |
| Digital twin simulator | `core/sim.py` |
| PPO inference (NumPy) | `core/policy.py` + `core/policy_weights.npz` |
| Policy Arbiter (Round 2) | `core/arbiter.py` |
| Evaluation harness | `evaluate.py` |
| TinyFish client and rules | `core/tinyfish.py` |
| Business rules, planner, alerts | `core/ops.py` |
| Flask routes | `routes/` |
| Jinja templates | `templates/` |
| CSS, fonts, Chart.js | `static/` |
| Training script | `train_rl.py` |
| Benchmark results | `results/` |
| Tests | `tests/` |
| Round 2 explanation | `ROUND2.md` |
| Demo script | `DEMO.md` |


## Honesty note

Everything above is a true account of how FleetFlow was built. If something changes before judging (a new library, a new API, a different model) we will update this file in the same commit. The purpose of this log is to let the judges see exactly what we did, what we used, what we built, and where every piece lives.

Anand Tripathi, on behalf of team Doppler Effect
