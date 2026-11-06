"""Akses PostgreSQL untuk akun, attempt, dan leaderboard."""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import psycopg
from psycopg.errors import IntegrityError
from psycopg.rows import dict_row

from .config import settings

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id            BIGSERIAL PRIMARY KEY,
        name          TEXT NOT NULL,
        password_hash TEXT,
        created_at    TEXT NOT NULL,
        last_seen     TEXT
    )
    """,
    # Database lama dibuat sebelum ada pencatatan aktivitas.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TEXT",
    # Username tidak membedakan huruf besar dan kecil.
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
        tries       INTEGER NOT NULL DEFAULT 0,
        applied_manifest_hash TEXT
    )
    """,
    "ALTER TABLE attempts ADD COLUMN IF NOT EXISTS applied_manifest_hash TEXT",
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


def connect() -> Any:
    if not settings.database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    return psycopg.connect(settings.database_url, row_factory=dict_row)


@contextmanager
def get_db() -> Iterator[Any]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    # PostgreSQL mungkin belum siap saat container platform start lebih dulu.
    delay = 1.0
    for attempt in range(10):
        try:
            with get_db() as db:
                for statement in _SCHEMA:
                    db.execute(statement)
            return
        except psycopg.OperationalError:
            if attempt == 9:
                raise
            time.sleep(delay)
            delay = min(delay * 1.5, 5.0)


# ---------------------------------------------------------------- users

def normalize_name(name: str) -> str:
    return " ".join(name.split())[:40]


def get_user_by_name(name: str) -> Any | None:
    with get_db() as db:
        return db.execute(
            "SELECT * FROM users WHERE LOWER(name) = LOWER(%s)",
            (normalize_name(name),),
        ).fetchone()


def create_user(name: str, password_hash: str) -> Any | None:
    """Return the new row, or None when the username is already taken."""
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users(name, password_hash, created_at) "
                "VALUES(%s, %s, %s)",
                (normalize_name(name), password_hash, iso(utcnow())),
            )
            return db.execute(
                "SELECT * FROM users WHERE LOWER(name) = LOWER(%s)",
                (normalize_name(name),),
            ).fetchone()
    except IntegrityError:
        return None


def set_password(user_id: int, password_hash: str) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (password_hash, user_id),
        )


def get_user(user_id: int) -> Any | None:
    with get_db() as db:
        return db.execute(
            "SELECT * FROM users WHERE id = %s", (user_id,)
        ).fetchone()


def touch_user(user_id: int, min_interval_seconds: int = 300) -> None:
    """Catat aktivitas terakhir, tapi jangan menulis di setiap request.

    ``last_seen`` dipakai oleh ``app.cleanup`` untuk menentukan sandbox mana
    yang sudah boleh dihapus.
    """
    now = utcnow()
    with get_db() as db:
        db.execute(
            "UPDATE users SET last_seen = %s "
            "WHERE id = %s AND (last_seen IS NULL OR last_seen < %s)",
            (
                iso(now),
                user_id,
                iso(now - timedelta(seconds=min_interval_seconds)),
            ),
        )


def all_users() -> list[Any]:
    with get_db() as db:
        return db.execute("SELECT * FROM users ORDER BY id").fetchall()


def idle_users(days: int) -> list[Any]:
    """User yang aktivitas terakhirnya lebih lama dari ``days`` hari.

    Baris lama mungkin belum punya ``last_seen``; pakai attempt terakhir atau
    ``created_at`` sebagai gantinya.
    """
    cutoff = iso(utcnow() - timedelta(days=days))
    with get_db() as db:
        return db.execute(
            """
                SELECT u.*,
                       COALESCE(
                           u.last_seen,
                           (SELECT MAX(COALESCE(a.finished_at, a.started_at))
                              FROM attempts a WHERE a.user_id = u.id),
                           u.created_at
                       ) AS activity
                  FROM users u
                 WHERE COALESCE(
                           u.last_seen,
                           (SELECT MAX(COALESCE(a.finished_at, a.started_at))
                              FROM attempts a WHERE a.user_id = u.id),
                           u.created_at
                       ) < %s
                 ORDER BY activity ASC
                """,
            (cutoff,),
        ).fetchall()


# ------------------------------------------------------------- attempts

def active_attempt(user_id: int, challenge: str) -> Any | None:
    with get_db() as db:
        return db.execute(
            "SELECT * FROM attempts WHERE user_id = %s AND challenge = %s "
            "AND status = 'active'",
            (user_id, challenge),
        ).fetchone()


def latest_attempt(user_id: int, challenge: str) -> Any | None:
    with get_db() as db:
        return db.execute(
            """SELECT * FROM attempts WHERE user_id = %s AND challenge = %s
                   ORDER BY (status = 'active') DESC, id DESC LIMIT 1""",
            (user_id, challenge),
        ).fetchone()


def start_attempt(user_id: int, challenge: str) -> Any:
    existing = active_attempt(user_id, challenge)
    if existing:
        return existing
    with get_db() as db:
        db.execute(
            """INSERT INTO attempts(user_id, challenge, started_at, status)
                   VALUES(%s, %s, %s, 'active')""",
            (user_id, challenge, iso(utcnow())),
        )
        return db.execute(
            "SELECT * FROM attempts WHERE user_id = %s AND challenge = %s "
            "AND status = 'active'",
            (user_id, challenge),
        ).fetchone()


def record_try(attempt_id: int) -> int:
    with get_db() as db:
        db.execute(
            "UPDATE attempts SET tries = tries + 1 WHERE id = %s",
            (attempt_id,),
        )
        return db.execute(
            "SELECT tries FROM attempts WHERE id = %s", (attempt_id,)
        ).fetchone()["tries"]


def set_applied_manifest_hash(attempt_id: int, value: str | None) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE attempts SET applied_manifest_hash = %s WHERE id = %s AND status = 'active'",
            (value, attempt_id),
        )


def finish_attempt(attempt_id: int, status: str, score: int) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE attempts SET status = %s, score = %s, finished_at = %s "
            "WHERE id = %s",
            (status, score, iso(utcnow()), attempt_id),
        )


def attempts_for_user(user_id: int) -> dict[str, Any]:
    """Attempt terbaru per challenge, dipakai di dashboard."""
    with get_db() as db:
        rows = db.execute(
            """SELECT a.* FROM attempts a
                   JOIN (SELECT challenge, MAX(id) AS mid FROM attempts
                         WHERE user_id = %s GROUP BY challenge) latest
                     ON a.id = latest.mid""",
            (user_id,),
        ).fetchall()
    return {row["challenge"]: row for row in rows}


def completed_sandboxes() -> set[tuple[int, str]]:
    """Latest completed attempts whose old namespaces may still exist."""
    with get_db() as db:
        rows = db.execute(
            """SELECT a.user_id, a.challenge FROM attempts a
               JOIN (SELECT user_id, challenge, MAX(id) AS mid FROM attempts
                     GROUP BY user_id, challenge) latest ON a.id = latest.mid
              WHERE a.status IN ('passed', 'expired')"""
        ).fetchall()
    return {(row["user_id"], row["challenge"]) for row in rows}


# ---------------------------------------------------------- leaderboard

def leaderboard(days: int) -> list[Any]:
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
                 WHERE a.status = 'passed' AND a.finished_at >= %s
                 GROUP BY u.id
                 ORDER BY total_score DESC, last_solved ASC
                 LIMIT 50
                """,
            (since,),
        ).fetchall()
