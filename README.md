# FleetFlow

**Warehouse stock, shop restocking and AI robot rebalancing — in one web app.**

FleetFlow is a working prototype for Problem 7: *Distribute resources intelligently*.
A delivery company keeps stock in **one warehouse (4 zones, 10 picking robots)** and supplies **6 shops**.
The app tracks every unit of stock, tells each shop what it needs and where to send it from,
and uses a **reinforcement-learning (RL) agent** to move robots to the zones where orders are about to pile up.
Live demand hints (sales, cricket matches, festivals, weather) come from the web through the **TinyFish API**.

🔗 **Live demo:** [https://fleetflow-8x7h.onrender.com/](https://fleetflow-8x7h.onrender.com/) *(free tier — first open takes ~1 minute to wake up)*

---

## Table of contents

1. [What the app does](#what-the-app-does)
2. [Tech stack](#tech-stack)
3. [Quick start (5 minutes)](#quick-start-5-minutes)
4. [Demo logins](#demo-logins)
5. [Connect TinyFish](#connect-tinyfish)
6. [Project layout](#project-layout)
7. [How the AI works](#how-the-ai-works)
8. [Training results](#training-results)
9. [Database schema](#database-schema)
10. [Deploy on Render](#deploy-on-render)
11. [Common problems](#common-problems)

---

## What the app does

| Area | What it does |
|---|---|
| **Login & roles** | Email + password login (hashed with Werkzeug), sign-up, three roles — **staff, manager, admin** — each with their own permissions |
| **Dashboard** | Stock value, items below reorder level, open transfers, robots working, open alerts, 14-day stock-flow chart, live zone pressure |
| **Inventory** | Every product with zone, bin, stock, reorder level, cost and value. Filters, search, stock in / stock out, shelf counts, movement history, CSV import / export |
| **Shops** | Per-shop shelf stock, minimum levels, record sales and counts, shelf-health view (which shelves are below minimum) |
| **Transfers** | Warehouse → shop or shop → shop. Lifecycle: *Pending → Picking (robot assigned) → On the road → Delivered*. Printable pick list sorted by zone and bin. Cancelling a transfer puts the stock back |
| **Restock planner** | Every shelf below minimum, with the best source (warehouse zone, or another shop with spare stock) and a human reason. One click turns the plan into real transfers |
| **Purchasing** | Suppliers, purchase orders (*draft → ordered → received*), automatic reorder suggestions, real vs promised lead time, past-order history |
| **Robot fleet** | Live floor map, battery level, current job, maintenance, manual moves, and **AI advice** checked in a digital twin before it is shown to the user |
| **Simulation lab** | Rules vs RL agent side by side on the same demand shock. Animated. Every robot move has a reason |
| **Demand signals** | TinyFish **Search** turns web news into a demand multiplier per zone. TinyFish **Web Agent** scrapes supplier prices from a product URL |
| **Reports** | Top and slow movers, shop fill rate, stock by category, robot workload — CSV downloads for everything |
| **Alerts & activity log** | Out-of-stock items, urgent transfers, low battery, late suppliers, big demand signals. Every action a user takes is logged |

---

## Tech stack

| Layer | What we used | Why |
|---|---|---|
| Backend | **Flask 3** + Blueprints | Light, fast, one file per section |
| Auth | **Flask-Login** + Werkzeug password hashing | Standard, battle-tested |
| Database | **SQLite** (one file, `fleetflow.db`) | No DB server to run, perfect for a prototype |
| Frontend | Server-rendered Jinja templates + vanilla JS | Zero build step, deploys as-is |
| Charts | **Chart.js** (bundled locally in `static/vendor/`) | Works offline, no CDN risk |
| Typography | **Figtree** font (bundled in `static/fonts/`) | Clean, free, no network calls |
| ML training | **PyTorch + Stable-Baselines3 + Gymnasium** | PPO out of the box |
| ML inference | **NumPy only** (weights exported to `.npz`) | 50 MB smaller image, no PyTorch in production |
| Web signals | **TinyFish Search + Web Agent** APIs | Real demand news without writing a scraper |
| Production server | **Gunicorn** + 2 workers, 4 threads | Simple, enough for a free tier |
| Hosting | **Render** (free plan) via `render.yaml` Blueprint | Push to GitHub → one click to deploy |

---

## Quick start (5 minutes)

Requires **Python 3.10 or newer**.

```bash
# 1. Clone or download the folder
cd fleetflow

# 2. Make a virtual environment
python -m venv venv

# Windows
venv\Scripts\activate
# Mac / Linux
source venv/bin/activate

# 3. Install the four runtime packages
pip install -r requirements.txt

# 4. Run it
python app.py
```

Open **http://127.0.0.1:5000** in a browser. On the very first run the app creates `fleetflow.db` and seeds it with demo data (6 shops, ~40 products, 10 robots, a few open transfers and 2 weeks of movement history).

To start fresh, delete `fleetflow.db` and run again. To skip the demo seed, set `NO_DEMO_DATA=1` before starting.

### Demo logins

| Role | Email | Password | What they can do |
|---|---|---|---|
| **Admin** | `admin@fleetflow.in` | `admin123` | Everything, plus users and settings |
| **Manager** | `manager@fleetflow.in` | `manager123` | Approve transfers, apply AI advice, run purchases |
| **Staff** | `staff@fleetflow.in` | `staff123` | Record stock in / out, view everything read-only |

---

## Connect TinyFish

TinyFish gives the app real demand news (sales, matches, weather, festivals) and can scrape supplier product pages for price and stock.

1. Get a key at **[agent.tinyfish.ai → API keys](https://agent.tinyfish.ai)**.
2. Either set it before starting the app:

   ```bash
   # Windows
   set TINYFISH_API_KEY=sk-tinyfish-xxx
   # Mac / Linux
   export TINYFISH_API_KEY=sk-tinyfish-xxx
   ```

   …or sign in as **admin** and paste the key in **Settings**.
3. Open **Demand signals → Fetch live signals**.

**What we call:**

- **Search API** — `GET https://api.search.tinyfish.ai?query=...`
  Queries like *"Delhi sale this week"*, *"IPL match Mumbai today"*, *"heavy rain Bengaluru"*.
  Each returned headline is matched against simple keyword rules (e.g. *sale* → Zone A ×2.5, *rain* → Zone B ×1.4), so every signal can **explain itself** on the dashboard.
- **Web Agent** — `POST https://agent.tinyfish.ai/v1/automation/run`
  Give it a supplier product URL and a plain-English goal ("get price and whether it is in stock"); it returns JSON.
  Used in **Purchasing** to compare the real supplier price against our purchase order.

**If TinyFish is down or no key is set**, the app keeps working with the last saved signals and any manual ones a manager added. Nothing breaks.

---

## Project layout

```
fleetflow/
├── app.py                      Flask app factory, login manager, Jinja filters, /healthz
├── requirements.txt            4 runtime packages (Flask, Flask-Login, NumPy, Gunicorn)
├── requirements-train.txt      Extra packages for training (torch, SB3, gymnasium)
├── render.yaml                 Render Blueprint — one-click deploy
├── Dockerfile                  Alternative deploy for any Docker host
├── train_rl.py                 PPO training script + evaluation on 6 scenarios
│
├── core/                       Non-web building blocks
│   ├── db.py                   SQLite schema (16 tables) + demo data seed
│   ├── authz.py                User class + @role_required decorator
│   ├── ops.py                  Business rules — stock in/out, restock plan, PO suggestions,
│   │                           alerts, zone pressure, digital-twin advice
│   ├── sim.py                  Warehouse digital twin: zones, robots, Poisson orders,
│   │                           6 scenarios (normal, spike, moving, rush, failure, mixed)
│   ├── policy.py               NumpyPolicy — runs the trained PPO network with numpy only
│   ├── policy_weights.npz      Trained PPO weights (export from train_rl.py)
│   ├── tinyfish.py             TinyFish Search + Web Agent client, keyword→multiplier rules
│   ├── eval.json               Benchmark results shown in the Simulation Lab
│   └── train_log_v1.txt        Training log for the shipped checkpoint
│
├── routes/                     One Flask Blueprint per section
│   ├── auth.py                 Login, sign-up, logout, profile
│   ├── main.py                 Dashboard, reports, alerts, activity log, settings
│   ├── inventory.py            Products, movements, CSV in/out
│   ├── shops.py                Shops, shelf stock, sales
│   ├── transfers.py            Transfers + restock planner
│   ├── purchasing.py           Suppliers + purchase orders + reorder suggestions
│   ├── fleet.py                Robot floor, AI advice, simulation API
│   └── signals.py              TinyFish fetch + demand signals page
│
├── templates/                  Jinja HTML
│   ├── base.html, _macros.html Shared layout + reusable components
│   ├── auth/                   Login + sign-up, bold grid-paper design
│   ├── inventory/, shops/, …   One folder per section
│   └── fleet/                  Floor map + simulation lab (animated)
│
└── static/
    ├── css/app.css             Light-green theme, Figtree font-face
    ├── fonts/                  Figtree WOFF2 files (bundled, no CDN)
    ├── img/                    Logo + icons
    └── vendor/chart.min.js     Chart.js (bundled, works offline)
```

---

## How the AI works

The hard problem on this floor is **which zone each robot should be in right now**. 10 robots, 4 zones, demand that keeps shifting through the day.

### 1. The digital twin (`core/sim.py`)

A fast simulator of the warehouse floor used both for training *and* for live advice.

- **4 zones** (A market, B homes, C/D offices) with their own order patterns
- **10 robots**, each with a home zone, speed and battery
- **1 tick = 1 second** of simulated time
- Orders arrive from a **Poisson process** per zone, with a multiplier that depends on time of day and the current scenario
- A robot handles ~2 orders a minute and takes **40 s to drive** from one zone to another
- 6 built-in **scenarios**: `normal`, `spike` (Zone A flash sale), `moving` (hotspot shifts hour by hour), `rush` (evening peak), `failure` (2 robots break down), `mixed` (random busy zones)

### 2. The baseline (rules)

Every robot stays in its home zone and takes the next order there. Simple, honest, and the yardstick to beat.

### 3. The RL agent (PPO)

- **Observation (16 numbers):** per zone — current queue length, robots there, orders in the last minute, web-signal multiplier
- **Action space (17 actions):** `move robot from Zone X to Zone Y` for each X≠Y pair (12 options), plus `send one robot home` for each zone (4), plus `do nothing` (1)
- **Decision frequency:** every **10 s** of warehouse time (one env step)
- **Reward:** `-sum(queue_waits) − 0.5 × move_cost`
- **Hard rule (safety):** every zone always keeps at least 1 robot, no matter what the agent asks for
- **Algorithm:** PPO from Stable-Baselines3, default MLP policy, 2× `[64, 64]` layers, `tanh`
- **Training:** 1.5 million env steps on random days (75% random scenario, 25% picked from the 6 named ones), with `VecNormalize` on observations. About **30 minutes on a laptop CPU**.
- We keep the checkpoint with the best average score on 20 held-out seeds across all scenarios.

### 4. Live advice — the digital-twin planner

The RL agent alone can be fooled by situations it never saw in training. So on the live Fleet page the app runs a **two-step planner** every time the page loads:

1. **Try every possible single move** — for each robot currently in a non-essential spot, simulate moving it to every other zone. Play the next 15 minutes in the twin. Keep the 1–3 moves whose predicted wait time is best.
2. **Ask the RL agent** in the same twin for comparison.
3. Show **only moves that beat doing nothing by a clear margin.** No suggestion is ever shown that would make waits worse — this is the safety net around the RL agent.

### 5. Production inference (no PyTorch)

Training uses PyTorch, but running the agent in the live web app does not. `train_rl.py` exports the trained PPO network to `core/policy_weights.npz` — just the weight matrices. `core/policy.py` has a 40-line `NumpyPolicy` class that re-implements the forward pass with numpy. This saves ~500 MB on the Render image and makes cold starts much faster.

---

## Training results

Benchmarked on **10 unseen random seeds** per scenario (seeds never used in training). Metric: **average waiting time in seconds per order** — lower is better.

| Scenario | Basic rules | **RL agent** | Better than rules | Simple heuristic |
|---|---:|---:|---:|---:|
| Normal day | 47 | **25** | **47%** | 23 |
| Flash sale (Zone A ×5) | 335 | **256** | **23%** | 253 |
| Moving hotspot | 319 | **264** | **17%** | 256 |
| Evening rush | 232 | **154** | **33%** | 142 |
| 2 robots break down | 377 | **322** | **15%** | 321 |
| Mixed day (random busy zones) | 236 | 253 | **−7%** | 89 |

**Honest notes:**

- The agent **clearly beats the basic rules** on every standard scenario and roughly matches a hand-tuned heuristic.
- It **struggles when the busy zones are shuffled** (it never saw that during training).
- That is exactly why live advice uses the **digital-twin planner** instead of the raw agent — the planner tests every move and never suggests one that makes waits worse.
- **Next step:** train with `MIX_BASE_PROB=0.6` (random busy zones) for 3M+ steps. Our short runs with a bigger network collapsed, so the shipped checkpoint is the smaller v1.

### Retrain the agent

```bash
pip install -r requirements-train.txt     # torch + stable-baselines3 + gymnasium
python train_rl.py                        # ~30 min on a laptop CPU
```

Output: a fresh `core/policy_weights.npz`. The web app picks it up on the next restart.

---

## Database schema

16 SQLite tables. The important ones:

| Table | What it holds |
|---|---|
| `users` | id, name, email, password_hash, role, created_at |
| `zones` | 4 warehouse zones with code + name |
| `products` | SKU, name, category, zone, bin, stock, reorder_level, cost, supplier |
| `shops` | 6 shop names, city, region |
| `shop_stock` | per shop × product: on-shelf stock + minimum level |
| `transfers` | header: code, from, to, status, robot, created_by |
| `transfer_items` | lines inside a transfer: product × quantity |
| `purchase_orders` + `po_items` | supplier POs, draft→ordered→received |
| `movements` | every stock in/out event (audit trail) |
| `robots` | 10 robots: code, zone, home_zone, status, battery |
| `robot_moves` | every rebalancing move with reason + source (manual, twin planner, RL) |
| `signals` | demand signals per zone, with headline + multiplier + source |
| `activity` | every user action for the audit log |
| `settings` | key/value — e.g. TinyFish key saved from the admin UI |

The schema and demo seed are in `core/db.py` — one file to read if you want to understand the data model.

---

## Deploy on Render

### Option A — Blueprint (reads `render.yaml`)

1. Push this folder to a GitHub repo.
2. [render.com](https://render.com) → **New → Blueprint** → pick the repo → **Apply**.
3. When asked, paste your `TINYFISH_API_KEY`. `SECRET_KEY` is generated for you.

### Option B — Web Service (by hand)

- **New → Web Service** → pick the repo, Runtime **Python 3**, Instance **Free**
- **Build command:** `pip install -r requirements.txt`
- **Start command:**
  ```
  gunicorn app:app --preload --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT
  ```
- **Environment variables:**
  | Key | Value |
  |---|---|
  | `TINYFISH_API_KEY` | your TinyFish key |
  | `SECRET_KEY` | any long random string |
  | `PYTHON_VERSION` | `3.12.8` |

### Notes on the free tier

- The service **sleeps after 15 minutes** of no visitors. The first open after a sleep takes about a minute to wake up.
- The SQLite file is **ephemeral** — it resets on every deploy or restart. The demo seed re-runs, so the three demo logins always work.
- For real persistence, add a Render **Disk** and point `FLEETFLOW_DB` at a path on it.

---

## Common problems

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: flask` | Virtual environment is not active. Re-run `venv\Scripts\activate` (Windows) or `source venv/bin/activate`. |
| `fleetflow.db is locked` | Close any other Python process using the DB and refresh. SQLite allows only one writer. |
| TinyFish fetch does nothing | Key is missing or wrong. Check **Settings → TinyFish** (admin only) or the `TINYFISH_API_KEY` env var. |
| Robots never move on their own | Rebalancing is **operator-approved**. Open **Fleet** and click **Apply** on the suggested moves. |
| Login page loops back to login | The session cookie is being dropped. On HTTPS set `SESSION_COOKIE_SECURE=True` (already handled on Render + HF Spaces). |
| First open on Render is slow | Free tier is waking up from sleep — takes about 1 minute. Opens after that are instant. |

---

## Credits

Built by **Doppler Effect** — Anand Tripathi, Anurag Singh, Tanishq Tripathi, Saavya Verma, Akshat Mishra.
TinyFish Search + Web Agent, Chart.js, Figtree font, Stable-Baselines3, Flask.
