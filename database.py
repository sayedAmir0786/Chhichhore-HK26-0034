"""SQLite helpers for EcoShield AI.

The database file is created automatically on first start.
Every query uses '?' placeholders (parameterized) to prevent SQL injection.
"""
import os
import sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "database", "ecoshield.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS forest_analyses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    location       TEXT NOT NULL,
    date_before    TEXT NOT NULL,
    date_after     TEXT NOT NULL,
    before_image   TEXT,
    after_image    TEXT,
    diff_image     TEXT,
    change_percent REAL NOT NULL,
    is_sample      INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_text    TEXT NOT NULL,
    category      TEXT NOT NULL,
    assessment    TEXT NOT NULL,
    explanation   TEXT NOT NULL,
    matched_claim TEXT,
    source_title  TEXT,
    source_url    TEXT,
    match_score   REAL NOT NULL DEFAULT 0,
    is_sample     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def db_path():
    # ECOSHIELD_DB is only used by the automated tests.
    return os.getenv("ECOSHIELD_DB", DEFAULT_DB)


def get_connection():
    os.makedirs(os.path.dirname(db_path()), exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def query_all(sql, params=()):
    conn = get_connection()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def query_one(sql, params=()):
    conn = get_connection()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def execute(sql, params=()):
    """Run INSERT/UPDATE/DELETE. Returns (last inserted id, rows changed)."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid, cur.rowcount
    finally:
        conn.close()


def init_db():
    """Create tables and insert demo records (only once, ever)."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        seed_sample_data(conn)
        conn.commit()
    finally:
        conn.close()


def seed_sample_data(conn):
    # The 'meta' table remembers that seeding happened, so restarting the app
    # (or deleting a sample record) never re-inserts or duplicates samples.
    if conn.execute("SELECT 1 FROM meta WHERE key = 'sample_seeded'").fetchone():
        return
    now = datetime.now()

    def stamp(days_ago):
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")

    forest = [
        ("Bandipur buffer zone (sample)", "2025-01-15", "2026-01-15", 3.4, 75),
        ("Simlipal fringe (sample)", "2025-03-02", "2026-03-02", 12.8, 48),
        ("Kaziranga corridor (sample)", "2025-06-10", "2026-06-10", 27.5, 20),
        ("Periyar foothills (sample)", "2025-08-21", "2026-08-21", 6.1, 6),
    ]
    for loc, d1, d2, pct, days in forest:
        conn.execute(
            "INSERT INTO forest_analyses (location, date_before, date_after, change_percent, is_sample, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)", (loc, d1, d2, pct, stamp(days)))

    claims = [
        ("Deforestation releases stored carbon into the atmosphere.", "deforestation", "supported",
         "Trees store carbon; clearing and burning forests releases it as carbon dioxide.",
         "Deforestation contributes to climate change by releasing stored carbon dioxide",
         "IPCC Sixth Assessment Report", "https://www.ipcc.ch/report/ar6/syr/", 0.91, 30),
        ("Global warming is purely a natural cycle.", "climate_change", "contradicted",
         "Observed warming since the 1800s is mainly driven by human greenhouse gas emissions.",
         "Global warming is just a natural cycle and humans have no role",
         "NASA: Causes of Climate Change", "https://climate.nasa.gov/causes/", 0.78, 18),
        ("A new satellite will stop all wildfires.", "forest_fires", "unverified",
         "Unverified — additional evidence required. No entry in the evidence collection matched.",
         None, None, None, 0.12, 9),
    ]
    for c in claims:
        conn.execute(
            "INSERT INTO claim_analyses (claim_text, category, assessment, explanation, matched_claim, "
            "source_title, source_url, match_score, is_sample, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (*c[:8], stamp(c[8])))
    conn.execute("INSERT INTO meta (key, value) VALUES ('sample_seeded', '1')")


if __name__ == "__main__":
    init_db()
    print("Database ready at:", db_path())
