"""Postgres access. The schema lives in schema.sql."""

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import config


async def _query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    async with await psycopg.AsyncConnection.connect(
        config.DATABASE_URL, row_factory=dict_row, autocommit=True
    ) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall() if cur.description else []


async def _one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = await _query(sql, params)
    return rows[0] if rows else None


# --- contacts ---------------------------------------------------------------


async def create_contact(email: str, profile: dict) -> int:
    row = await _one(
        "INSERT INTO contacts (email, profile) VALUES (%s, %s) RETURNING id",
        (email, Jsonb(profile)),
    )
    return row["id"]


async def get_contact(contact_id: int) -> dict | None:
    return await _one("SELECT * FROM contacts WHERE id = %s", (contact_id,))


async def list_contacts(status: str | None = None, limit: int = 200) -> list[dict]:
    if status:
        return await _query(
            "SELECT * FROM contacts WHERE status = %s ORDER BY id DESC LIMIT %s", (status, limit)
        )
    return await _query("SELECT * FROM contacts ORDER BY id DESC LIMIT %s", (limit,))


async def add_attempt(contact_id: int, attempt: dict) -> None:
    await _query(
        "UPDATE contacts SET attempts = attempts || %s WHERE id = %s",
        (Jsonb([attempt]), contact_id),
    )


async def finish_contact(contact_id: int, status: str, version: int) -> int:
    """Mark the contact done and return its completion number."""
    row = await _one(
        "UPDATE contacts SET status = %s, program_version = %s, completed_at = now() "
        "WHERE id = %s RETURNING nextval('completions') AS seq",
        (status, version, contact_id),
    )
    return row["seq"]


async def training_rows(recent: int, history: int) -> list[dict]:
    """First attempts of finished contacts: the latest `recent`, plus up to
    `history` older ones split evenly between passes and failures."""
    base = """
        SELECT profile, attempts->0 AS first
        FROM contacts WHERE completed_at IS NOT NULL
    """
    latest = await _query(base + " ORDER BY completed_at DESC LIMIT %s", (recent,))
    older = await _query(
        """
        WITH older AS (
            SELECT profile, attempts->0 AS first, completed_at
            FROM contacts WHERE completed_at IS NOT NULL
            ORDER BY completed_at DESC OFFSET %s
        )
        SELECT profile, first FROM (
            SELECT *, row_number() OVER (
                PARTITION BY (first->>'passed')::boolean ORDER BY completed_at DESC
            ) AS rank
            FROM older
        ) t WHERE rank <= %s
        """,
        (recent, history // 2),
    )
    return latest + older


# --- program versions -------------------------------------------------------


async def active_version() -> int:
    row = await _one("SELECT COALESCE(MAX(version), 0) AS v FROM program_versions")
    return row["v"]


async def get_version(version: int) -> dict | None:
    return await _one("SELECT * FROM program_versions WHERE version = %s", (version,))


async def list_versions() -> list[dict]:
    return await _query("SELECT * FROM program_versions ORDER BY version DESC")


async def add_version(location: str, instructions: str, scores: dict) -> int:
    row = await _one(
        "INSERT INTO program_versions (location, instructions, scores) "
        "VALUES (%s, %s, %s) RETURNING version",
        (location, instructions, Jsonb(scores)),
    )
    return row["version"]


# --- optimization runs ------------------------------------------------------


async def record_optimization(batch: int, status: str, result: dict) -> None:
    await _query(
        "INSERT INTO optimization_runs (batch, status, result) VALUES (%s, %s, %s)",
        (batch, status, Jsonb(result)),
    )


async def list_optimizations(limit: int = 20) -> list[dict]:
    return await _query("SELECT * FROM optimization_runs ORDER BY id DESC LIMIT %s", (limit,))


async def stats() -> dict:
    by_status = await _query("SELECT status, COUNT(*) AS n FROM contacts GROUP BY status")
    first_attempt = await _query(
        """
        SELECT program_version, COUNT(*) AS n,
               AVG((attempts->0->>'score')::float) AS avg_score,
               AVG(((attempts->0->>'passed')::boolean)::int)::float AS pass_rate
        FROM contacts WHERE completed_at IS NOT NULL
        GROUP BY program_version ORDER BY program_version
        """
    )
    return {
        "contacts": {r["status"]: r["n"] for r in by_status},
        "active_version": await active_version(),
        "first_attempt_by_version": first_attempt,
    }
