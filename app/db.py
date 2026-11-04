"""Akses SQLite: skema dan query yang dipakai platform."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    challenge   TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    status      TEXT    NOT NULL DEFAULT 'active',   -- active | passed | expired
    score       INTEGER NOT NULL DEFAULT 0,
    tries       INTEGER NOT NULL DEFAULT 0
);

-- Satu attempt aktif per user per challenge.
CREATE UNIQUE INDEX IF NOT EXISTS ux_attempt_active
    ON attempts(user_id, challenge) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_attempt_challenge ON attempts(challenge, status);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with get_db() as db:
        db.executescript(SCHEMA)


# ---------------------------------------------------------------- users

def get_or_create_user(name: str) -> sqlite3.Row:
    clean = " ".join(name.split())[:40]
    with get_db() as db:
        db.execute(
            "INSERT INTO users(name, created_at) VALUES(?, ?) ON CONFLICT(name) DO NOTHING",
            (clean, iso(utcnow())),
        )
        return db.execute("SELECT * FROM users WHERE name = ?", (clean,)).fetchone()


def get_user(user_id: int) -> sqlite3.Row | None:
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# ------------------------------------------------------------- attempts

def active_attempt(user_id: int, challenge: str) -> sqlite3.Row | None:
    with get_db() as db:
        return db.execute(
            "SELECT * FROM attempts WHERE user_id = ? AND challenge = ? AND status = 'active'",
            (user_id, challenge),
        ).fetchone()


def latest_attempt(user_id: int, challenge: str) -> sqlite3.Row | None:
    with get_db() as db:
        return db.execute(
            """SELECT * FROM attempts WHERE user_id = ? AND challenge = ?
               ORDER BY (status = 'active') DESC, id DESC LIMIT 1""",
            (user_id, challenge),
        ).fetchone()


def start_attempt(user_id: int, challenge: str) -> sqlite3.Row:
    existing = active_attempt(user_id, challenge)
    if existing:
        return existing
    with get_db() as db:
        db.execute(
            """INSERT INTO attempts(user_id, challenge, started_at, status)
               VALUES(?, ?, ?, 'active')""",
            (user_id, challenge, iso(utcnow())),
        )
        return db.execute(
            "SELECT * FROM attempts WHERE user_id = ? AND challenge = ? AND status = 'active'",
            (user_id, challenge),
        ).fetchone()


def record_try(attempt_id: int) -> int:
    with get_db() as db:
        db.execute("UPDATE attempts SET tries = tries + 1 WHERE id = ?", (attempt_id,))
        return db.execute("SELECT tries FROM attempts WHERE id = ?", (attempt_id,)).fetchone()["tries"]


def finish_attempt(attempt_id: int, status: str, score: int) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE attempts SET status = ?, score = ?, finished_at = ? WHERE id = ?",
            (status, score, iso(utcnow()), attempt_id),
        )


def attempts_for_user(user_id: int) -> dict[str, sqlite3.Row]:
    """Attempt terbaru per challenge, dipakai di dashboard."""
    with get_db() as db:
        rows = db.execute(
            """SELECT a.* FROM attempts a
               JOIN (SELECT challenge, MAX(id) AS mid FROM attempts
                     WHERE user_id = ? GROUP BY challenge) latest
                 ON a.id = latest.mid""",
            (user_id,),
        ).fetchall()
    return {row["challenge"]: row for row in rows}


# ---------------------------------------------------------- leaderboard

def leaderboard(days: int) -> list[sqlite3.Row]:
    """Peringkat dihitung dari attempt 'passed' dalam N hari terakhir."""
    since = iso(utcnow() - timedelta(days=days))
    with get_db() as db:
        return db.execute(
            """
            SELECT u.name,
                   SUM(a.score)  AS total_score,
                   COUNT(*)      AS solved,
                   MAX(a.finished_at) AS last_solved
              FROM attempts a
              JOIN users u ON u.id = a.user_id
             WHERE a.status = 'passed' AND a.finished_at >= ?
             GROUP BY u.id
             ORDER BY total_score DESC, last_solved ASC
             LIMIT 50
            """,
            (since,),
        ).fetchall()
