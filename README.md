# FleetFlow

**Warehouse stock, shop restocking and AI robot rebalancing in one app.**
Built by Marlbros FC for Problem 7: *Distribute resources intelligently*.

A delivery company keeps stock in one warehouse (4 zones, 10 picking robots) and supplies 6 shops.
FleetFlow tracks every unit, tells you what each shop needs and where to send it from,
and uses a reinforcement-learning agent to move robots to the zones where orders are about to pile up.
Live demand hints (sales, matches, festivals, weather) come from the web through **TinyFish**.

---

## What you can do

| Area | What it does |
|---|---|
| **Login & roles** | Email + password login (hashed), sign-up, three roles: staff, manager, admin |
| **Dashboard** | Stock value, low stock, open transfers, robots working, alerts, 14-day stock flow, zone pressure |
| **Inventory** | Every product with zone, bin, stock, reorder level, cost and value. Filters, search, stock in/out, shelf counts, full history, CSV import/export |
| **Shops** | Shelf stock per shop, minimum levels, record sales and counts, shelf health |
| **Transfers** | Warehouse → shop or shop → shop. Pending → Picking (robot assigned) → On the road → Delivered. Printable pick list sorted by zone and bin. Cancel puts stock back |
| **Restock planner** | Every shelf below minimum, with where to send from (warehouse, or a shop with spare stock) and why. One click creates the transfers |
| **Purchasing** | Suppliers, purchase orders (draft → ordered → received), automatic reorder suggestions, real vs promised lead time |
| **Robot fleet** | Live floor map, battery, jobs, maintenance, manual moves, and **AI advice** checked in a digital twin before it is shown |
| **Simulation lab** | Rules vs RL agent side by side on the same demand shock, animated, with a reason for every robot move |
| **Demand signals** | TinyFish Search turns web news into a demand multiplier per zone. TinyFish Web Agent checks supplier prices from a product page |
| **Reports** | Top and slow movers, shop fill rate, stock by category, robot workload, CSV downloads |
| **Alerts & activity log** | Out of stock, urgent work, low battery, late suppliers, big demand signals. Every action is logged |

## Run it (5 minutes)

```bash
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
python app.py
```
Open **http://127.0.0.1:5000**. Demo data loads on the first run.

| Role | Email | Password |
|---|---|---|
| Admin | admin@fleetflow.in | admin123 |
| Manager | manager@fleetflow.in | manager123 |
| Staff | staff@fleetflow.in | staff123 |

## Connect TinyFish

1. Get a key at **agent.tinyfish.ai → API keys**.
2. Either set it before starting: `set TINYFISH_API_KEY=your_key` (Windows) / `export TINYFISH_API_KEY=your_key`,
   or log in as admin and paste it in **Settings**.
3. Open **Demand signals → Fetch live signals**.

What we call:
- **Search API** `GET https://api.search.tinyfish.ai?query=...` for "{city} sale this week", events, weather and matches.
  Each headline is matched against simple keyword rules (e.g. *sale* → Zone A ×2.5, *rain* → Zone B ×1.4), so every signal can explain itself.
- **Web Agent** `POST https://agent.tinyfish.ai/v1/automation/run` with a product URL and a plain-English goal; it returns price and stock as JSON.

If TinyFish is down or no key is set, the app keeps working with the saved signals and manual ones.

## How the AI works (simple version)

1. **Digital twin** (`core/sim.py`): a simulator of the warehouse floor. 4 zones, 10 robots, 1 tick = 1 second, orders arrive at random (Poisson).
   A robot handles 2 orders a minute and needs 40 s to drive to another zone.
2. **Baseline rules**: each robot stays in its home zone and takes the next order there.
3. **RL agent** (PPO, Stable-Baselines3): every 10 s it sees 16 numbers (queue, robots, last-minute demand, web signal for each zone)
   and picks one action: move one robot from zone X to zone Y, or do nothing.
   Reward = minus the total waiting time, minus a small cost per trip. Safety rule: every zone keeps at least one robot.
4. **Training**: 1.5 million decisions on random days (surprise spikes, robot failures, rush hours). We keep the checkpoint that does best on held-out days.
5. **Live advice (planning)**: the fleet page copies today's floor into the twin, tries every possible robot move, plays the next 15 minutes for each,
   and keeps the best 1–3 moves. The RL agent is played in the same twin for comparison. Only moves that cut predicted waiting time are shown.
6. The web app runs the trained network with **numpy only** (`core/policy_weights.npz`), so it deploys without PyTorch.

### Results (10 unseen seeds per scenario, average wait in seconds, lower is better)

| Scenario | Basic rules | RL agent | Better than rules | Simple heuristic |
|---|---|---|---|---|
| Normal day | 47 | 25 | **47%** | 23 |
| Flash sale (Zone A ×5) | 335 | 256 | **23%** | 253 |
| Moving hotspot | 319 | 264 | **17%** | 256 |
| Evening rush | 232 | 154 | **33%** | 142 |
| 2 robots break down | 377 | 322 | **15%** | 321 |
| Mixed day (random busy zones) | 236 | 253 | −7% | 89 |

Honest notes: the agent clearly beats the basic rules on every standard scenario, and roughly matches a hand-tuned heuristic.
It struggles when the busy zones are shuffled (it never saw that during training). That is why live advice uses the
digital-twin planner, which tests every move and never suggests one that makes things worse.
Next step: train with `MIX_BASE_PROB=0.6` (random busy zones) for longer; our short runs were unstable.

Retrain: `pip install -r requirements-train.txt` then `python train_rl.py` (about 30 minutes on a laptop CPU).

## Project layout

```
app.py                  Flask app, login manager, filters
core/db.py              SQLite schema + demo data
core/ops.py             stock rules, transfers, POs, planner, alerts, digital-twin advice
core/sim.py             warehouse simulator + scenarios
core/policy.py          runs the trained agent with numpy
core/tinyfish.py        TinyFish Search + Web Agent client, keyword rules
routes/                 one file per section (inventory, shops, transfers, purchasing, fleet, signals, ...)
templates/, static/     pages, light-green theme, Chart.js and font bundled (works offline)
train_rl.py             PPO training + evaluation
```

## Deploy on Render

**Option A: Blueprint (reads `render.yaml`)**
1. Push this folder to a GitHub repo.
2. render.com → **New → Blueprint** → pick the repo → **Apply**.
3. When asked, paste your `TINYFISH_API_KEY`. `SECRET_KEY` is made for you.

**Option B: Web Service (by hand)**
- New → Web Service → pick the repo, Runtime **Python 3**, Instance **Free**
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --preload --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
- Environment: `TINYFISH_API_KEY` = your key, `SECRET_KEY` = any long text, `PYTHON_VERSION` = `3.12.8`

Notes: the free plan sleeps after 15 minutes with no visitors (first open takes about a minute), and the SQLite file
resets on every deploy/restart, so the demo data reloads. Demo logins always work.
