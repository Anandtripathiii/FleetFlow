"""Live GPS tracking: tables and demo data (drivers, vehicles, trips, locations, alerts, geofences, issues).

migrate(con) is the one migration for tracking. It only adds new tables, so it is safe to run on every start.
Existing tables are not changed: a trip points at an existing transfer, and shop coordinates live in geofences.
"""
from werkzeug.security import generate_password_hash

from .db import now

TRACKING_SCHEMA = """
CREATE TABLE IF NOT EXISTS drivers (
  id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
  phone TEXT, license_no TEXT, active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS vehicles (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, plate TEXT, kind TEXT DEFAULT 'van',
  active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS geofences (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'shop',   -- shop, warehouse
  shop_id INTEGER UNIQUE REFERENCES shops(id),                                  -- NULL = main warehouse
  lat REAL NOT NULL, lng REAL NOT NULL, radius_m REAL NOT NULL DEFAULT 150);

CREATE TABLE IF NOT EXISTS trips (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL,
  transfer_id INTEGER NOT NULL REFERENCES transfers(id),
  driver_id INTEGER NOT NULL REFERENCES drivers(id), vehicle_id INTEGER REFERENCES vehicles(id),
  status TEXT NOT NULL DEFAULT 'active',          -- active, arrived, completed, cancelled
  dest_lat REAL, dest_lng REAL,
  eta_at TEXT, eta_source TEXT,                   -- osrm or approximate (straight line)
  gps_state TEXT NOT NULL DEFAULT 'waiting',      -- waiting, ok, denied, unavailable
  last_lat REAL, last_lng REAL, last_speed REAL, last_seen_at TEXT, last_moved_at TEXT,
  started_at TEXT NOT NULL, arrived_at TEXT, completed_at TEXT, confirmed_by INTEGER REFERENCES users(id));

CREATE TABLE IF NOT EXISTS trip_locations (
  id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
  lat REAL NOT NULL, lng REAL NOT NULL, accuracy_m REAL, speed_mps REAL, heading REAL,
  recorded_at TEXT NOT NULL,                      -- time on the device
  received_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'browser');  -- browser, native, device

CREATE TABLE IF NOT EXISTS trip_events (
  id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, detail TEXT, user_id INTEGER, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS driver_issues (
  id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
  driver_id INTEGER NOT NULL REFERENCES drivers(id), kind TEXT NOT NULL, note TEXT,
  lat REAL, lng REAL, created_at TEXT NOT NULL, resolved_at TEXT);

CREATE TABLE IF NOT EXISTS tracking_alerts (
  id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL,
  created_at TEXT NOT NULL, resolved_at TEXT,
  UNIQUE (trip_id, kind, level));                 -- one alert per trip, kind and step: no repeats

CREATE INDEX IF NOT EXISTS ix_trip_locations_trip ON trip_locations(trip_id, id);
CREATE INDEX IF NOT EXISTS ix_trips_status ON trips(status);
"""

# demo coordinates (South Delhi); the main warehouse is in Okhla Industrial Area
WAREHOUSE = ("Main warehouse (Okhla)", 28.5355, 77.2732)
SHOP_COORDS = {"SH-OKH": (28.5440, 77.2700), "SH-SAK": (28.5245, 77.2066), "SH-KAL": (28.5494, 77.2588),
               "SH-LAJ": (28.5677, 77.2433), "SH-HKV": (28.5494, 77.2001), "SH-NEH": (28.5491, 77.2533)}
DEMO_DRIVER = ("Ravi Kumar", "driver@fleetflow.in", "driver123")
DEMO_VEHICLES = [("VAN-01", "DL 1L AB 1234", "van"), ("VAN-02", "DL 3C CD 5678", "van")]


def migrate(con, demo=True):
    con.executescript(TRACKING_SCHEMA)
    if demo and con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 0 \
            and con.execute("SELECT COUNT(*) FROM shops").fetchone()[0] > 0:
        seed(con)
    con.commit()


def seed(con):
    con.executemany("INSERT INTO vehicles (code, plate, kind) VALUES (?,?,?)", DEMO_VEHICLES)
    name, lat, lng = WAREHOUSE
    con.execute("INSERT INTO geofences (name, kind, lat, lng, radius_m) VALUES (?, 'warehouse', ?, ?, 200)", (name, lat, lng))
    for s in con.execute("SELECT id, code, name FROM shops"):
        if s["code"] in SHOP_COORDS:
            con.execute("INSERT INTO geofences (name, kind, shop_id, lat, lng) VALUES (?, 'shop', ?, ?, ?)",
                        (s["name"], s["id"], *SHOP_COORDS[s["code"]]))
    n, e, p = DEMO_DRIVER       # drivers are normal staff users, so roles and auth stay unchanged
    con.execute("INSERT OR IGNORE INTO users (name, email, password_hash, role, created_at) VALUES (?,?,?,'staff',?)",
                (n, e, generate_password_hash(p), now()))
    uid = con.execute("SELECT id FROM users WHERE email=?", (e,)).fetchone()["id"]
    con.execute("INSERT OR IGNORE INTO drivers (user_id, phone, license_no) VALUES (?, '98100 22001', 'DL-0420190012345')", (uid,))
