"""SQLite database: schema, connection helper and demo data."""
import os
import random
import sqlite3
from datetime import datetime, timedelta

from flask import g
from werkzeug.security import generate_password_hash

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance", "fleetflow.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff',
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_login TEXT);

CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, description TEXT);

CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, contact TEXT, phone TEXT, email TEXT,
  lead_days INTEGER DEFAULT 3, website TEXT, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
  unit TEXT DEFAULT 'pcs', zone_id INTEGER REFERENCES zones(id), bin TEXT,
  qty INTEGER NOT NULL DEFAULT 0, reorder_level INTEGER NOT NULL DEFAULT 20,
  reorder_qty INTEGER NOT NULL DEFAULT 50, unit_cost REAL NOT NULL DEFAULT 0,
  unit_price REAL NOT NULL DEFAULT 0, supplier_id INTEGER REFERENCES suppliers(id),
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT);

CREATE TABLE IF NOT EXISTS shops (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, area TEXT,
  manager TEXT, phone TEXT, address TEXT, zone_id INTEGER REFERENCES zones(id), active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS shop_stock (
  shop_id INTEGER REFERENCES shops(id), product_id INTEGER REFERENCES products(id),
  qty INTEGER NOT NULL DEFAULT 0, min_qty INTEGER NOT NULL DEFAULT 10,
  PRIMARY KEY (shop_id, product_id));

CREATE TABLE IF NOT EXISTS transfers (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL,
  from_shop_id INTEGER REFERENCES shops(id),      -- NULL = main warehouse
  to_shop_id INTEGER NOT NULL REFERENCES shops(id),
  status TEXT NOT NULL DEFAULT 'pending',         -- pending, picking, in_transit, delivered, cancelled
  priority TEXT NOT NULL DEFAULT 'normal',        -- low, normal, urgent
  robot_id INTEGER REFERENCES robots(id), note TEXT,
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
  picked_at TEXT, dispatched_at TEXT, delivered_at TEXT);

CREATE TABLE IF NOT EXISTS transfer_items (
  id INTEGER PRIMARY KEY, transfer_id INTEGER REFERENCES transfers(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id), qty INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS purchase_orders (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, supplier_id INTEGER REFERENCES suppliers(id),
  status TEXT NOT NULL DEFAULT 'draft',           -- draft, ordered, received, cancelled
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
  expected_at TEXT, received_at TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS po_items (
  id INTEGER PRIMARY KEY, po_id INTEGER REFERENCES purchase_orders(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id), qty INTEGER NOT NULL, unit_cost REAL NOT NULL);

CREATE TABLE IF NOT EXISTS movements (
  id INTEGER PRIMARY KEY, product_id INTEGER REFERENCES products(id),
  location TEXT NOT NULL DEFAULT 'warehouse',     -- 'warehouse' or shop code
  change INTEGER NOT NULL, balance INTEGER, reason TEXT NOT NULL, ref TEXT,
  user_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS robots (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, zone_id INTEGER REFERENCES zones(id),
  home_zone_id INTEGER REFERENCES zones(id), status TEXT NOT NULL DEFAULT 'idle',  -- idle, busy, charging, maintenance
  battery REAL NOT NULL DEFAULT 100, tasks_done INTEGER NOT NULL DEFAULT 0, last_service TEXT);

CREATE TABLE IF NOT EXISTS robot_moves (
  id INTEGER PRIMARY KEY, robot_id INTEGER REFERENCES robots(id), from_zone TEXT, to_zone TEXT,
  reason TEXT, source TEXT, user_id INTEGER, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, zone_id INTEGER REFERENCES zones(id), source TEXT NOT NULL,
  title TEXT NOT NULL, detail TEXT, url TEXT, multiplier REAL NOT NULL DEFAULT 1.0,
  starts_at TEXT, expires_at TEXT, created_at TEXT NOT NULL, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def now(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def q(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def log_activity(con, user_id, action, detail=""):
    con.execute("INSERT INTO activity (user_id, action, detail, created_at) VALUES (?,?,?,?)",
                (user_id, action, detail, now()))


def setting(con, key, default=None):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# --------------------------------------------------------------------------- demo data
ZONES = [
    ("A", "Zone A · Fast movers", "Snacks, drinks and daily items near the dock"),
    ("B", "Zone B · Household", "Cleaning, kitchen and home care"),
    ("C", "Zone C · Office & tech", "Stationery and electronics accessories"),
    ("D", "Zone D · Bulk staples", "Rice, atta, oil and other heavy bags"),
]
SUPPLIERS = [
    ("Gupta Wholesale Traders", "Rakesh Gupta", "98110 23456", "orders@guptawholesale.in", 2),
    ("FreshFlow FMCG Distributors", "Neha Arora", "98733 11220", "sales@freshflow.in", 3),
    ("Delhi Paper & Stationery Co.", "Imran Khan", "99100 55678", "imran@dpsco.in", 4),
    ("TechKart Accessories", "Pooja Mehta", "98180 77001", "b2b@techkart.in", 5),
    ("Annapurna Grains", "Suresh Yadav", "97111 30304", "supply@annapurnagrains.in", 3),
]
# sku, name, category, zone, unit, cost, price, supplier index, reorder level
PRODUCTS = [
    ("SNK-001", "Classic Salted Chips 52g", "Snacks", "A", "pcs", 14, 20, 1, 120),
    ("SNK-002", "Masala Peanuts 200g", "Snacks", "A", "pcs", 38, 55, 1, 60),
    ("SNK-003", "Glucose Biscuits 250g", "Snacks", "A", "pcs", 22, 30, 1, 100),
    ("SNK-004", "Dark Chocolate Bar 80g", "Snacks", "A", "pcs", 65, 99, 1, 40),
    ("BEV-001", "Mineral Water 1L", "Beverages", "A", "bottle", 12, 20, 1, 200),
    ("BEV-002", "Cola 750ml", "Beverages", "A", "bottle", 28, 40, 1, 90),
    ("BEV-003", "Mango Drink 600ml", "Beverages", "A", "bottle", 24, 35, 1, 80),
    ("BEV-004", "Green Tea 25 bags", "Beverages", "A", "box", 95, 145, 1, 30),
    ("DAI-001", "Toned Milk 500ml", "Dairy", "A", "packet", 25, 28, 1, 150),
    ("DAI-002", "Curd 400g", "Dairy", "A", "cup", 30, 40, 1, 60),
    ("HHC-001", "Dishwash Liquid 500ml", "Household", "B", "bottle", 72, 99, 0, 40),
    ("HHC-002", "Detergent Powder 1kg", "Household", "B", "pack", 85, 120, 0, 50),
    ("HHC-003", "Floor Cleaner 1L", "Household", "B", "bottle", 110, 159, 0, 30),
    ("HHC-004", "Garbage Bags (30)", "Household", "B", "roll", 55, 80, 0, 40),
    ("HHC-005", "Kitchen Towel Roll", "Household", "B", "roll", 45, 65, 0, 40),
    ("PER-001", "Bath Soap (pack of 4)", "Personal care", "B", "pack", 120, 165, 0, 40),
    ("PER-002", "Toothpaste 150g", "Personal care", "B", "tube", 70, 95, 0, 50),
    ("PER-003", "Shampoo 340ml", "Personal care", "B", "bottle", 180, 249, 0, 25),
    ("PER-004", "Hand Sanitiser 200ml", "Personal care", "B", "bottle", 60, 90, 0, 30),
    ("STA-001", "A4 Paper Ream (500)", "Stationery", "C", "ream", 240, 320, 2, 30),
    ("STA-002", "Ball Pens (pack of 10)", "Stationery", "C", "pack", 45, 70, 2, 40),
    ("STA-003", "Spiral Notebook 200pg", "Stationery", "C", "pcs", 48, 75, 2, 50),
    ("STA-004", "Sticky Notes Pad", "Stationery", "C", "pcs", 30, 50, 2, 30),
    ("STA-005", "Stapler with Pins", "Stationery", "C", "pcs", 95, 150, 2, 15),
    ("ELE-001", "USB-C Cable 1m", "Electronics", "C", "pcs", 110, 249, 3, 25),
    ("ELE-002", "Power Bank 10000mAh", "Electronics", "C", "pcs", 650, 999, 3, 10),
    ("ELE-003", "Wired Earphones", "Electronics", "C", "pcs", 140, 299, 3, 20),
    ("ELE-004", "AA Batteries (pack of 4)", "Electronics", "C", "pack", 60, 99, 3, 30),
    ("ELE-005", "LED Bulb 9W", "Electronics", "C", "pcs", 55, 99, 3, 30),
    ("STP-001", "Basmati Rice 5kg", "Staples", "D", "bag", 420, 549, 4, 25),
    ("STP-002", "Whole Wheat Atta 10kg", "Staples", "D", "bag", 380, 469, 4, 25),
    ("STP-003", "Sunflower Oil 1L", "Staples", "D", "bottle", 135, 170, 4, 40),
    ("STP-004", "Toor Dal 1kg", "Staples", "D", "pack", 130, 165, 4, 35),
    ("STP-005", "Sugar 1kg", "Staples", "D", "pack", 42, 52, 4, 50),
    ("STP-006", "Iodised Salt 1kg", "Staples", "D", "pack", 20, 28, 4, 40),
    ("STP-007", "Tea Leaves 500g", "Staples", "D", "pack", 210, 280, 4, 25),
]
SHOPS = [
    ("SH-OKH", "Okhla Express Store", "Okhla Phase 3", "Ritu Sharma", "98100 11001", "C"),
    ("SH-SAK", "Saket Daily Mart", "Saket", "Aman Verma", "98100 11002", "A"),
    ("SH-KAL", "Kalkaji Corner Shop", "Kalkaji", "Farhan Ali", "98100 11003", "B"),
    ("SH-LAJ", "Lajpat Nagar Super", "Lajpat Nagar", "Simran Kaur", "98100 11004", "A"),
    ("SH-HKV", "Hauz Khas Pick-up Point", "Hauz Khas", "Vikram Rao", "98100 11005", "B"),
    ("SH-NEH", "Nehru Place Tech Hub", "Nehru Place", "Deepa Nair", "98100 11006", "C"),
]


def init_db(con, demo=True):
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM zones").fetchone()[0] == 0 and demo:
        seed(con)
    con.commit()
    from .tracking_db import migrate                # live GPS tracking tables (new tables only)
    migrate(con, demo)


def seed(con, rnd=None):
    rnd = rnd or random.Random(7)
    t0 = datetime.now()
    con.executemany("INSERT INTO zones (code, name, description) VALUES (?,?,?)", ZONES)
    zid = {r["code"]: r["id"] for r in con.execute("SELECT id, code FROM zones")}
    con.executemany("INSERT INTO suppliers (name, contact, phone, email, lead_days) VALUES (?,?,?,?,?)", SUPPLIERS)
    sids = [r["id"] for r in con.execute("SELECT id FROM suppliers ORDER BY id")]

    users = [("Anurag Singh", "admin@fleetflow.in", "admin123", "admin"),
             ("Saavya Verma", "manager@fleetflow.in", "manager123", "manager"),
             ("Akshat Mishra", "staff@fleetflow.in", "staff123", "staff")]
    for n, e, p, r in users:
        con.execute("INSERT OR IGNORE INTO users (name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                    (n, e, generate_password_hash(p), r, now(t0 - timedelta(days=40))))

    for i, (sku, name, cat, z, unit, cost, price, si, rl) in enumerate(PRODUCTS):
        qty = int(rl * rnd.uniform(0.9, 3.2))
        if sku in ("BEV-001", "STA-001", "ELE-002", "STP-003", "HHC-002"):
            qty = int(rl * rnd.uniform(0.3, 0.8))       # a few low-stock items for the demo
        bin_ = f"{z}-{(i % 6) + 1:02d}-{rnd.randint(1, 4)}"
        con.execute("""INSERT INTO products (sku, name, category, unit, zone_id, bin, qty, reorder_level, reorder_qty,
                       unit_cost, unit_price, supplier_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sku, name, cat, unit, zid[z], bin_, qty, rl, rl * 2, cost, price, sids[si], now(t0 - timedelta(days=40))))
    pids = [r["id"] for r in con.execute("SELECT id FROM products ORDER BY id")]
    plist = con.execute("SELECT * FROM products ORDER BY id").fetchall()

    for code, name, area, mgr, phone, z in SHOPS:
        con.execute("INSERT INTO shops (code, name, area, manager, phone, address, zone_id) VALUES (?,?,?,?,?,?,?)",
                    (code, name, area, mgr, phone, f"{area}, New Delhi", zid[z]))
    shops = con.execute("SELECT * FROM shops ORDER BY id").fetchall()
    for sh in shops:
        for p in plist:
            focus = (sh["code"] == "SH-NEH" and p["category"] in ("Electronics", "Stationery")) or \
                    (sh["code"] == "SH-OKH" and p["category"] in ("Stationery", "Snacks", "Beverages")) or \
                    (sh["code"] in ("SH-SAK", "SH-LAJ") and p["category"] in ("Snacks", "Beverages", "Dairy", "Staples")) or \
                    (sh["code"] in ("SH-KAL", "SH-HKV") and p["category"] in ("Household", "Personal care", "Staples", "Dairy"))
            if not focus and rnd.random() > 0.35:
                continue
            mn = max(4, int(p["reorder_level"] / (3 if focus else 6)))
            qty = int(mn * rnd.uniform(0.35, 3.0))
            con.execute("INSERT INTO shop_stock (shop_id, product_id, qty, min_qty) VALUES (?,?,?,?)", (sh["id"], p["id"], qty, mn))

    # robots: 3 / 3 / 2 / 2 home zones (same as the simulation)
    homes = ["A", "A", "A", "B", "B", "B", "C", "C", "D", "D"]
    for i, z in enumerate(homes):
        st = "idle"
        if i == 7:
            st = "charging"
        con.execute("""INSERT INTO robots (code, zone_id, home_zone_id, status, battery, tasks_done, last_service)
                       VALUES (?,?,?,?,?,?,?)""", (f"R{i + 1}", zid[z], zid[z], st, round(rnd.uniform(22, 98), 0),
                                                    rnd.randint(300, 1400), now(t0 - timedelta(days=rnd.randint(3, 40)))))
    rids = [r["id"] for r in con.execute("SELECT id FROM robots ORDER BY id")]

    # 30 days of history: purchase receipts and delivered transfers
    po_n, tr_n = 1, 1
    for d in range(30, 0, -1):
        day = t0 - timedelta(days=d)
        if d % 6 == 0:
            si = rnd.choice(sids)
            items = [p for p in plist if p["supplier_id"] == si][:5]
            if items:
                code = f"PO-{po_n:04d}"; po_n += 1
                con.execute("""INSERT INTO purchase_orders (code, supplier_id, status, created_by, created_at, expected_at, received_at)
                               VALUES (?,?,?,?,?,?,?)""", (code, si, "received", 2, now(day - timedelta(days=3)), now(day), now(day)))
                po = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                for p in items:
                    qn = p["reorder_level"] * 2
                    con.execute("INSERT INTO po_items (po_id, product_id, qty, unit_cost) VALUES (?,?,?,?)", (po, p["id"], qn, p["unit_cost"]))
                    con.execute("INSERT INTO movements (product_id, location, change, reason, ref, user_id, created_at) VALUES (?,?,?,?,?,?,?)",
                                (p["id"], "warehouse", qn, "receive", code, 2, now(day + timedelta(hours=10))))
        for _ in range(rnd.randint(2, 5)):
            sh = rnd.choice(shops)
            code = f"TR-{tr_n:04d}"; tr_n += 1
            created = day + timedelta(hours=rnd.randint(8, 18), minutes=rnd.randint(0, 59))
            con.execute("""INSERT INTO transfers (code, from_shop_id, to_shop_id, status, priority, robot_id, created_by, created_at,
                           picked_at, dispatched_at, delivered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (code, None, sh["id"], "delivered", rnd.choice(["normal", "normal", "urgent", "low"]), rnd.choice(rids), 3,
                         now(created), now(created + timedelta(minutes=20)), now(created + timedelta(minutes=45)),
                         now(created + timedelta(hours=2, minutes=rnd.randint(0, 90)))))
            tid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            for p in rnd.sample(plist, rnd.randint(1, 4)):
                qn = rnd.randint(4, 30)
                con.execute("INSERT INTO transfer_items (transfer_id, product_id, qty) VALUES (?,?,?)", (tid, p["id"], qn))
                con.execute("INSERT INTO movements (product_id, location, change, reason, ref, user_id, created_at) VALUES (?,?,?,?,?,?,?)",
                            (p["id"], "warehouse", -qn, "dispatch", code, 3, now(created + timedelta(minutes=45))))

    # open work for today, across every status
    open_tr = [("pending", "urgent", 1), ("pending", "normal", 3), ("pending", "normal", 5), ("picking", "urgent", 0),
               ("picking", "normal", 2), ("in_transit", "normal", 4), ("in_transit", "low", 1)]
    free = [r["id"] for r in con.execute("SELECT id FROM robots WHERE status <> 'charging' ORDER BY id")]
    rnd.shuffle(free)
    for st, pr, si in open_tr:
        sh = shops[si]
        code = f"TR-{tr_n:04d}"; tr_n += 1
        created = t0 - timedelta(minutes=rnd.randint(10, 240))
        rob = free.pop() if st == "picking" else None
        if rob:
            con.execute("UPDATE robots SET status='busy' WHERE id=?", (rob,))
        con.execute("""INSERT INTO transfers (code, from_shop_id, to_shop_id, status, priority, robot_id, created_by, created_at,
                       picked_at, dispatched_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (code, None, sh["id"], st, pr, rob, 3, now(created),
                     now(created + timedelta(minutes=15)) if st != "pending" else None,
                     now(created + timedelta(minutes=40)) if st == "in_transit" else None))
        tid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        for p in rnd.sample(plist, rnd.randint(2, 4)):
            con.execute("INSERT INTO transfer_items (transfer_id, product_id, qty) VALUES (?,?,?)", (tid, p["id"], rnd.randint(4, 20)))

    for status, si, days in [("ordered", sids[3], 2), ("draft", sids[4], 0)]:
        code = f"PO-{po_n:04d}"; po_n += 1
        con.execute("""INSERT INTO purchase_orders (code, supplier_id, status, created_by, created_at, expected_at)
                       VALUES (?,?,?,?,?,?)""", (code, si, status, 2, now(t0 - timedelta(days=1)), now(t0 + timedelta(days=days + 1))))
        po = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        for p in [p for p in plist if p["supplier_id"] == si][:3]:
            con.execute("INSERT INTO po_items (po_id, product_id, qty, unit_cost) VALUES (?,?,?,?)", (po, p["id"], p["reorder_qty"], p["unit_cost"]))

    # sample web signals (labelled as demo until a TinyFish key is added)
    demo_sig = [("A", "Weekend sale announced at Saket malls", 2.5, "Demo signal"),
                ("C", "Tech expo at Pragati Maidan this week", 1.6, "Demo signal")]
    for z, title, m, src in demo_sig:
        con.execute("""INSERT INTO signals (zone_id, source, title, detail, multiplier, starts_at, expires_at, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""", (zid[z], src, title, "Sample signal so the page is not empty. Add a TinyFish key to fetch live ones.",
                                                     m, now(t0), now(t0 + timedelta(days=2)), now(t0)))
    set_setting(con, "city", "New Delhi")
    set_setting(con, "company", "Marlbros FC Logistics")
    log_activity(con, 1, "Demo data loaded", "36 products, 6 shops, 10 robots")
