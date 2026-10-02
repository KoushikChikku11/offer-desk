"""SQLite storage. One file, no server, easy to back up or inspect."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from .config import DEFAULT_DB
from .models import ALL_STAGES, Posting, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    uid           TEXT PRIMARY KEY,
    firm          TEXT NOT NULL,
    ats           TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT,
    url           TEXT,
    description   TEXT,
    published_at  TEXT,
    comp_text     TEXT,
    meta          TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    family        TEXT,
    score         REAL,
    score_detail  TEXT,
    status        TEXT NOT NULL DEFAULT 'new'   -- new | skipped | applied
);

CREATE TABLE IF NOT EXISTS contacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    firm          TEXT NOT NULL,
    role          TEXT,
    relationship  TEXT,            -- alum | club | recruiter | friend | cold
    status        TEXT NOT NULL DEFAULT 'reached_out',  -- reached_out | replied | chatted | referred | dormant
    last_contact  TEXT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_uid         TEXT,
    firm                TEXT NOT NULL,
    title               TEXT NOT NULL,
    family              TEXT,
    resume_version      TEXT NOT NULL,
    routed_version      TEXT,
    route_confidence    REAL,
    referral_contact_id INTEGER REFERENCES contacts(id),
    applied_at          TEXT NOT NULL,
    days_after_posting  REAL,
    letter              TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    stage          TEXT NOT NULL,
    at             TEXT NOT NULL,
    note           TEXT
);

CREATE INDEX IF NOT EXISTS idx_postings_score ON postings(active, status, score DESC);
CREATE INDEX IF NOT EXISTS idx_events_app ON events(application_id);
"""


@contextmanager
def connect(path: Path | str | None = None):
    path = Path(path or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


# Postings

def upsert_posting(conn, p: Posting, family: str, score: float, detail: dict) -> bool:
    """Insert or refresh a posting. Returns True when the posting is new."""
    now = utcnow().isoformat()
    existing = conn.execute("SELECT uid FROM postings WHERE uid = ?", (p.uid,)).fetchone()
    if existing:
        conn.execute(
            """UPDATE postings SET title=?, location=?, url=?, description=?, comp_text=?, meta=?,
               last_seen=?, active=1, family=?, score=?, score_detail=? WHERE uid=?""",
            (p.title, p.location, p.url, p.description, p.comp_text, json.dumps(p.raw_meta), now,
             family, score, json.dumps(detail), p.uid),
        )
        return False
    conn.execute(
        """INSERT INTO postings (uid, firm, ats, external_id, title, location, url, description,
           published_at, comp_text, meta, first_seen, last_seen, family, score, score_detail)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (p.uid, p.firm, p.ats, p.external_id, p.title, p.location, p.url, p.description,
         _iso(p.published_at), p.comp_text, json.dumps(p.raw_meta), now, now, family, score, json.dumps(detail)),
    )
    return True


def mark_closed(conn, firm: str, ats: str, seen_uids: set[str]) -> int:
    """Postings from this firm that vanished from the board are marked inactive."""
    rows = conn.execute(
        "SELECT uid FROM postings WHERE firm=? AND ats=? AND active=1", (firm, ats)
    ).fetchall()
    gone = [r["uid"] for r in rows if r["uid"] not in seen_uids]
    conn.executemany("UPDATE postings SET active=0 WHERE uid=?", [(u,) for u in gone])
    return len(gone)


def queue(conn, limit: int = 20, family: str | None = None, min_score: float = 0) -> list[sqlite3.Row]:
    sql = """SELECT * FROM postings WHERE active=1 AND status='new' AND score >= ?"""
    args: list = [min_score]
    if family:
        sql += " AND family = ?"
        args.append(family)
    sql += " ORDER BY score DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def get_posting(conn, uid_or_prefix: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM postings WHERE uid = ?", (uid_or_prefix,)).fetchone()
    if row:
        return row
    rows = conn.execute("SELECT * FROM postings WHERE external_id = ?", (uid_or_prefix,)).fetchall()
    if len(rows) == 1:
        return rows[0]
    rows = conn.execute(
        "SELECT * FROM postings WHERE uid LIKE ?", (f"%{uid_or_prefix}%",)
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def set_posting_status(conn, uid: str, status: str) -> None:
    conn.execute("UPDATE postings SET status=? WHERE uid=?", (status, uid))


# Applications and events

def create_application(conn, **fields) -> int:
    fields.setdefault("applied_at", utcnow().isoformat())
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO applications ({cols}) VALUES ({marks})", list(fields.values()))
    app_id = cur.lastrowid
    add_event(conn, app_id, "applied", fields["applied_at"])
    if fields.get("posting_uid"):
        set_posting_status(conn, fields["posting_uid"], "applied")
    return app_id


def add_event(conn, application_id: int, stage: str, at: str | None = None, note: str = "") -> None:
    if stage not in ALL_STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Use one of {ALL_STAGES}")
    conn.execute(
        "INSERT INTO events (application_id, stage, at, note) VALUES (?,?,?,?)",
        (application_id, stage, at or utcnow().isoformat(), note),
    )


def applications_frame(conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    apps = pd.read_sql_query("SELECT * FROM applications", conn)
    events = pd.read_sql_query("SELECT * FROM events", conn)
    return apps, events


# Contacts

def add_contact(conn, **fields) -> int:
    fields.setdefault("last_contact", utcnow().isoformat())
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    return conn.execute(f"INSERT INTO contacts ({cols}) VALUES ({marks})", list(fields.values())).lastrowid


def update_contact(conn, contact_id: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE contacts SET {sets} WHERE id=?", [*fields.values(), contact_id])


def contacts(conn, firm: str | None = None) -> list[sqlite3.Row]:
    if firm:
        return conn.execute("SELECT * FROM contacts WHERE firm LIKE ? ORDER BY last_contact", (f"%{firm}%",)).fetchall()
    return conn.execute("SELECT * FROM contacts ORDER BY firm, name").fetchall()
