"""SQLite persistence: contacts, attempts, outbox, counters and program versions.

Every write is keyed so that a retried workflow step converges on the same
row instead of creating a duplicate.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    profile         TEXT NOT NULL,           -- JSON
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|running|sent|flagged
    run_id          TEXT,
    program_version INTEGER,                 -- pinned when the workflow claims the contact
    attempts        INTEGER NOT NULL DEFAULT 0,
    final_score     REAL,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
    contact_id      TEXT NOT NULL REFERENCES contacts(id),
    attempt         INTEGER NOT NULL,
    program_version INTEGER NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    previous_feedback TEXT NOT NULL DEFAULT '',
    score           REAL,
    passed          INTEGER,
    feedback        TEXT,
    criteria        TEXT,                    -- JSON {criterion: probability}
    created_at      TEXT NOT NULL,
    PRIMARY KEY (contact_id, attempt)
);

CREATE TABLE IF NOT EXISTS outbox (
    contact_id      TEXT PRIMARY KEY REFERENCES contacts(id),
    to_email        TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    sent_at         TEXT NOT NULL
);

-- One row per finished contact; seq is the global completion order.
CREATE TABLE IF NOT EXISTS completions (
    contact_id      TEXT PRIMARY KEY REFERENCES contacts(id),
    seq             INTEGER NOT NULL UNIQUE,
    completed_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
    name            TEXT PRIMARY KEY,
    value           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS program_versions (
    version         INTEGER PRIMARY KEY,
    parent_version  INTEGER,
    location        TEXT NOT NULL,           -- Blob URL or local path
    instructions    TEXT NOT NULL,
    score           REAL,                    -- candidate score on the comparison set
    parent_score    REAL,                    -- parent score on the same set
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optimization_runs (
    batch           INTEGER PRIMARY KEY,     -- completion seq // OPTIMIZE_EVERY
    run_id          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|running|promoted|rejected|skipped|failed
    base_version    INTEGER,
    candidate_version INTEGER,
    base_score      REAL,
    candidate_score REAL,
    detail          TEXT,
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def _connect() -> sqlite3.Connection:
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATABASE_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """A write transaction that takes the write lock up front (BEGIN IMMEDIATE),
    so read-then-write sequences inside it are atomic across processes."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def init() -> None:
    with read() as conn:
        conn.executescript(SCHEMA)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# --- contacts ---------------------------------------------------------------


def upsert_contact(contact_id: str, email: str, profile: dict) -> tuple[dict, bool]:
    """Insert a contact if it is new. Returns (row, created)."""
    with tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO contacts (id, email, profile, created_at) VALUES (?, ?, ?, ?)",
            (contact_id, email, json.dumps(profile), now()),
        )
        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ? OR email = ?", (contact_id, email)
        ).fetchone()
        return dict(row), cur.rowcount == 1


def set_run_id(contact_id: str, run_id: str) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE contacts SET run_id = ? WHERE id = ? AND run_id IS NULL",
            (run_id, contact_id),
        )


def get_contact(contact_id: str) -> dict | None:
    with read() as conn:
        return _row(conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone())


def claim_contact(contact_id: str, run_id: str) -> dict:
    """Mark the contact running and pin the active program version.

    The version is only pinned once, so a retried claim, or a workflow that
    outlives a promotion, keeps generating with the version it started on.
    """
    with tx() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if row is None:
            raise LookupError(contact_id)
        if row["run_id"] not in (None, run_id):
            raise RuntimeError(f"contact {contact_id} is owned by run {row['run_id']}")
        if row["status"] == "pending":
            conn.execute(
                "UPDATE contacts SET status = 'running', run_id = ?, "
                "program_version = COALESCE(program_version, ?) WHERE id = ?",
                (run_id, _active_version(conn), contact_id),
            )
        return dict(conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone())


def finish_contact(contact_id: str, status: str, attempts: int, score: float | None) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE contacts SET status = ?, attempts = ?, final_score = ?, "
            "completed_at = COALESCE(completed_at, ?) WHERE id = ? AND status = 'running'",
            (status, attempts, score, now(), contact_id),
        )


# --- attempts ---------------------------------------------------------------


def get_attempt(contact_id: str, attempt: int) -> dict | None:
    with read() as conn:
        return _row(
            conn.execute(
                "SELECT * FROM attempts WHERE contact_id = ? AND attempt = ?", (contact_id, attempt)
            ).fetchone()
        )


def save_draft(
    contact_id: str, attempt: int, version: int, subject: str, body: str, previous_feedback: str
) -> dict:
    """Store a generated draft. If the attempt already exists (step retry after
    the write succeeded), the stored draft wins."""
    with tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO attempts "
            "(contact_id, attempt, program_version, subject, body, previous_feedback, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (contact_id, attempt, version, subject, body, previous_feedback, now()),
        )
        return dict(
            conn.execute(
                "SELECT * FROM attempts WHERE contact_id = ? AND attempt = ?", (contact_id, attempt)
            ).fetchone()
        )


def save_verdict(
    contact_id: str, attempt: int, score: float, passed: bool, feedback: str, criteria: dict
) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE attempts SET score = ?, passed = ?, feedback = ?, criteria = ? "
            "WHERE contact_id = ? AND attempt = ?",
            (score, int(passed), feedback, json.dumps(criteria), contact_id, attempt),
        )


# --- outbox -----------------------------------------------------------------


def record_send(contact_id: str, to_email: str, subject: str, body: str) -> bool:
    """Returns True only for the first send of this contact."""
    with tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO outbox (contact_id, to_email, subject, body, sent_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (contact_id, to_email, subject, body, now()),
        )
        return cur.rowcount == 1


# --- completion counter -----------------------------------------------------


def record_completion(contact_id: str) -> int:
    """Atomically assign this contact the next completion sequence number.

    Idempotent: a retry returns the seq already assigned to the contact.
    """
    with tx() as conn:
        row = conn.execute(
            "SELECT seq FROM completions WHERE contact_id = ?", (contact_id,)
        ).fetchone()
        if row is not None:
            return row["seq"]
        seq = conn.execute(
            "INSERT INTO counters (name, value) VALUES ('completions', 1) "
            "ON CONFLICT(name) DO UPDATE SET value = value + 1 RETURNING value"
        ).fetchone()["value"]
        conn.execute(
            "INSERT INTO completions (contact_id, seq, completed_at) VALUES (?, ?, ?)",
            (contact_id, seq, now()),
        )
        return seq


def claim_optimization(batch: int) -> dict:
    with tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO optimization_runs (batch, created_at) VALUES (?, ?)",
            (batch, now()),
        )
        return dict(
            conn.execute("SELECT * FROM optimization_runs WHERE batch = ?", (batch,)).fetchone()
        )


def update_optimization(batch: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as conn:
        conn.execute(
            f"UPDATE optimization_runs SET {cols} WHERE batch = ?", (*fields.values(), batch)
        )


def list_optimizations(limit: int = 20) -> list[dict]:
    with read() as conn:
        rows = conn.execute(
            "SELECT * FROM optimization_runs ORDER BY batch DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- program versions -------------------------------------------------------


def _active_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM app_state WHERE key = 'active_version'").fetchone()
    return int(row["value"]) if row else 0


def active_version() -> int:
    with read() as conn:
        return _active_version(conn)


def get_version(version: int) -> dict | None:
    with read() as conn:
        return _row(
            conn.execute("SELECT * FROM program_versions WHERE version = ?", (version,)).fetchone()
        )


def list_versions() -> list[dict]:
    with read() as conn:
        rows = conn.execute("SELECT * FROM program_versions ORDER BY version DESC").fetchall()
        return [dict(r) for r in rows]


def next_version() -> int:
    with read() as conn:
        row = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM program_versions")
        return row.fetchone()["v"]


def promote_version(
    *,
    version: int,
    parent_version: int,
    location: str,
    instructions: str,
    score: float,
    parent_score: float,
) -> bool:
    """Record the version and make it active, but only if `parent_version` is
    still active (compare-and-swap), so concurrent optimizations can't
    overwrite a newer winner."""
    with tx() as conn:
        if _active_version(conn) != parent_version:
            return False
        conn.execute(
            "INSERT OR IGNORE INTO program_versions "
            "(version, parent_version, location, instructions, score, parent_score, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (version, parent_version, location, instructions, score, parent_score, now()),
        )
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('active_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
        return True


# --- training data ----------------------------------------------------------


def training_rows(recent: int, history: int) -> list[dict]:
    """First attempts for finished contacts: the most recent `recent` plus up
    to `history` older ones, balanced between passes and failures."""
    query = """
        SELECT c.id, c.profile, a.subject, a.body, a.score, a.passed, a.feedback, cp.seq
        FROM completions cp
        JOIN contacts c ON c.id = cp.contact_id
        JOIN attempts a ON a.contact_id = c.id AND a.attempt = 1
        WHERE a.score IS NOT NULL
    """
    with read() as conn:
        latest = conn.execute(query + " ORDER BY cp.seq DESC LIMIT ?", (recent,)).fetchall()
        cutoff = min((r["seq"] for r in latest), default=0)
        half = history // 2
        older = []
        for passed in (1, 0):
            older += conn.execute(
                query + " AND cp.seq < ? AND a.passed = ? ORDER BY cp.seq DESC LIMIT ?",
                (cutoff, passed, half),
            ).fetchall()
    return [dict(r) for r in latest + older]


def stats() -> dict:
    with read() as conn:
        by_status = dict(
            conn.execute("SELECT status, COUNT(*) FROM contacts GROUP BY status").fetchall()
        )
        first_attempt = [
            dict(r)
            for r in conn.execute(
                "SELECT program_version, COUNT(*) AS n, AVG(score) AS avg_score, "
                "AVG(passed) AS pass_rate FROM attempts WHERE attempt = 1 AND score IS NOT NULL "
                "GROUP BY program_version ORDER BY program_version"
            ).fetchall()
        ]
        completions = conn.execute(
            "SELECT value FROM counters WHERE name = 'completions'"
        ).fetchone()
    return {
        "contacts": by_status,
        "completions": completions["value"] if completions else 0,
        "active_version": active_version(),
        "first_attempt_by_version": first_attempt,
    }
