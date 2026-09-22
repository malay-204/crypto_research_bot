"""Additive schema upgrades with verified, consistent SQLite backups."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 3

def prepare(db, path, mode):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
    if not exists:
        return
    meta = dict(db.execute("SELECT key,value FROM meta"))
    if meta.get("mode") != mode:
        raise ValueError("Demo and live data must use separate databases")
    version = int(meta.get("schema_version", "1"))
    if version > SCHEMA_VERSION:
        raise ValueError("Database schema is newer than this application")
    if version < SCHEMA_VERSION:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = Path(path).parent / "backups" / (Path(path).name + f".v{version}." + stamp + ".bak")
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target)) as backup:
            db.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Pre-migration backup failed integrity check")
        # Additive DDL below is transactional. A failed upgrade leaves v1 recoverable.

def upgrade(db):
    db.executescript("""
    BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS candle_batches(
      id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id),
      pair TEXT NOT NULL, granularity INTEGER NOT NULL,
      start INTEGER NOT NULL, end INTEGER NOT NULL, started REAL NOT NULL,
      finished REAL, status TEXT NOT NULL, expected INTEGER NOT NULL,
      received_count INTEGER NOT NULL DEFAULT 0, missing_count INTEGER,
      excluded_count INTEGER NOT NULL DEFAULT 0, error TEXT);
    CREATE TABLE IF NOT EXISTS candles(
      id INTEGER PRIMARY KEY, fetch_id INTEGER REFERENCES fetches(id),
      pair TEXT NOT NULL, granularity INTEGER NOT NULL, start INTEGER NOT NULL,
      low REAL NOT NULL, high REAL NOT NULL, open REAL NOT NULL,
      close REAL NOT NULL, volume REAL NOT NULL, observed REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS candle_lookup ON candles(pair,granularity,start,observed);
    CREATE INDEX IF NOT EXISTS candle_batch_lookup ON candle_batches(pair,granularity,started);
    CREATE TABLE IF NOT EXISTS scanner_events(
      id INTEGER PRIMARY KEY, observed REAL NOT NULL, pair TEXT,
      kind TEXT NOT NULL, message TEXT NOT NULL, detail TEXT NOT NULL);
    INSERT OR REPLACE INTO meta VALUES ('schema_version','3');
    COMMIT;
    """)
