"""Akses database: SQLite (default) atau PostgreSQL via ``DATABASE_URL``.

SQLite dipakai untuk pengembangan lokal dan test karena tidak butuh service
tambahan. Docker Compose mengisi ``DATABASE_URL`` sehingga platform memakai
PostgreSQL. Keduanya memakai query yang sama; hanya DDL dan placeholder yang
berbeda.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .config import settings

POSTGRES_PREFIXES = ("postgres://", "postgresql://")
IS_POSTGRES = settings.database_url.startswith(POSTGRES_PREFIXES)

if IS_POSTGRES:
    import psycopg
    from psycopg.errors import IntegrityError as _IntegrityError
    from psycopg.rows import dict_row
else:
    _IntegrityError = sqlite3.IntegrityError


_SQLITE_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT,
        created_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attempts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        challenge   TEXT    NOT NULL,
        started_at  TEXT    NOT NULL,
        finished_at TEXT,
        status      TEXT    NOT NULL DEFAULT 'active',
        score       INTEGER NOT NULL DEFAULT 0,
        tries       INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_attempt_active
        ON attempts(user_id, challenge) WHERE status = 'active'
    """,
    "CREATE INDEX IF NOT EXISTS ix_attempt_challenge ON attempts(challenge, status)",
]

_POSTGRES_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id            BIGSERIAL PRIMARY KEY,
        name          TEXT NOT NULL,
        password_hash TEXT,
        created_at    TEXT NOT NULL
    )
    """,
    # Postgres case-sensitive; samakan perilaku dengan SQLite (NOCASE).
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_name_lower ON users (LOWER(name))",
    """
    CREATE TABLE IF NOT EXISTS attempts (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        challenge   TEXT   NOT NULL,
        started_at  TEXT   NOT NULL,
        finished_at TEXT,
        status      TEXT   NOT NULL DEFAULT 'active',
        score       INTEGER NOT NULL DEFAULT 0,
        tries       INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_attempt_active
        ON attempts(user_id, challenge) WHERE status = 'active'
    """,
    "CREATE INDEX IF NOT EXISTS ix_attempt_challenge ON attempts(challenge, status)",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _q(sql: str) -> str:
    """SQLite memakai '?', psycopg memakai '%s'."""
    return sql.replace("?", "%s") if IS_POSTGRES else sql


def connect() -> Any:
    if IS_POSTGRES:
        return psycopg.connect(settings.database_url, row_factory=dict_row)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@contextmanager
def get_db() -> Iterator[Any]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    if not IS_POSTGRES:
        _init_sqlite()
        return

    # PostgreSQL mungkin belum siap saat container platform start lebih dulu.
    delay = 1.0
    for attempt in range(10):
        try:
            with get_db() as db:
                for statement in _POSTGRES_SCHEMA:
                    db.execute(statement)
            return
        except psycopg.OperationalError:
            if attempt == 9:
                raise
            time.sleep(delay)
            delay = min(delay * 1.5, 5.0)


def _init_sqlite() -> None:
    with get_db() as db:
        for statement in _SQLITE_SCHEMA:
            db.execute(statement)
        # Database lama dibuat sebelum login memakai password. Tambahkan kolom
        # tanpa menghapus user/attempt yang sudah ada.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if "password_hash" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")


# ---------------------------------------------------------------- users

def normalize_name(name: str) -> str:
    return " ".join(name.split())[:40]


def get_user_by_name(name: str) -> Any | None:
    with get_db() as db:
        return db.execute(
            _q("SELECT * FROM users WHERE LOWER(name) = LOWER(?)"),
            (normalize_name(name),),
        ).fetchone()


def create_user(name: str, password_hash: str) -> Any | None:
    """Return the new row, or None when the username is already taken."""
    try:
        with get_db() as db:
            db.execute(
                _q(
                    "INSERT INTO users(name, password_hash, created_at) "
                    "VALUES(?, ?, ?)"
                ),
                (normalize_name(name), password_hash, iso(utcnow())),
            )
            return db.execute(
                _q("SELECT * FROM users WHERE LOWER(name) = LOWER(?)"),
                (normalize_name(name),),
            ).fetchone()
    except _IntegrityError:
        return None


def set_password(user_id: int, password_hash: str) -> None:
    with get_db() as db:
        db.execute(
            _q("UPDATE users SET password_hash = ? WHERE id = ?"),
            (password_hash, user_id),
        )


def get_user(user_id: int) -> Any | None:
    with get_db() as db:
        return db.execute(
            _q("SELECT * FROM users WHERE id = ?"), (user_id,)
        ).fetchone()


# ------------------------------------------------------------- attempts

def active_attempt(user_id: int, challenge: str) -> Any | None:
    with get_db() as db:
        return db.execute(
            _q(
                "SELECT * FROM attempts WHERE user_id = ? AND challenge = ? "
                "AND status = 'active'"
            ),
            (user_id, challenge),
        ).fetchone()


def latest_attempt(user_id: int, challenge: str) -> Any | None:
    with get_db() as db:
        return db.execute(
            _q(
                """SELECT * FROM attempts WHERE user_id = ? AND challenge = ?
                   ORDER BY (status = 'active') DESC, id DESC LIMIT 1"""
            ),
            (user_id, challenge),
        ).fetchone()


def start_attempt(user_id: int, challenge: str) -> Any:
    existing = active_attempt(user_id, challenge)
    if existing:
        return existing
    with get_db() as db:
        db.execute(
            _q(
                """INSERT INTO attempts(user_id, challenge, started_at, status)
                   VALUES(?, ?, ?, 'active')"""
            ),
            (user_id, challenge, iso(utcnow())),
        )
        return db.execute(
            _q(
                "SELECT * FROM attempts WHERE user_id = ? AND challenge = ? "
                "AND status = 'active'"
            ),
            (user_id, challenge),
        ).fetchone()


def record_try(attempt_id: int) -> int:
    with get_db() as db:
        db.execute(
            _q("UPDATE attempts SET tries = tries + 1 WHERE id = ?"),
            (attempt_id,),
        )
        return db.execute(
            _q("SELECT tries FROM attempts WHERE id = ?"), (attempt_id,)
        ).fetchone()["tries"]


def finish_attempt(attempt_id: int, status: str, score: int) -> None:
    with get_db() as db:
        db.execute(
            _q(
                "UPDATE attempts SET status = ?, score = ?, finished_at = ? "
                "WHERE id = ?"
            ),
            (status, score, iso(utcnow()), attempt_id),
        )


def attempts_for_user(user_id: int) -> dict[str, Any]:
    """Attempt terbaru per challenge, dipakai di dashboard."""
    with get_db() as db:
        rows = db.execute(
            _q(
                """SELECT a.* FROM attempts a
                   JOIN (SELECT challenge, MAX(id) AS mid FROM attempts
                         WHERE user_id = ? GROUP BY challenge) latest
                     ON a.id = latest.mid"""
            ),
            (user_id,),
        ).fetchall()
    return {row["challenge"]: row for row in rows}


# ---------------------------------------------------------- leaderboard

def leaderboard(days: int) -> list[Any]:
    """Peringkat dihitung dari attempt 'passed' dalam N hari terakhir."""
    since = iso(utcnow() - timedelta(days=days))
    with get_db() as db:
        return db.execute(
            _q(
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
                """
            ),
            (since,),
        ).fetchall()
