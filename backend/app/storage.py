"""SQLite storage: ingested days and generated narratives.

Consistency model:
- One row per clinic-day; re-ingesting the same date atomically replaces it
  (single UPSERT statement inside an implicit transaction).
- Reports are never stored — they are recomputed by pure functions from the
  stored rows, so a report can never drift from its source data.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .schemas import BillingRow, NarrativeResponse

SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
    date TEXT PRIMARY KEY,
    clinic_id TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_narratives_date ON narratives(date, id);
"""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Storage:
    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # persistent DB setting — once at init, not per call
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection; commit/rollback transaction, then close it."""
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    def upsert_day(
        self,
        date: str,
        clinic_id: str,
        rows: list[BillingRow],
        rows_rejected: int = 0,
        errors: list[dict] | None = None,
    ) -> None:
        payload = json.dumps([r.model_dump(mode="json") for r in rows])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO days (
                    date, clinic_id, rows_json, rows_rejected, errors_json, ingested_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    clinic_id=excluded.clinic_id,
                    rows_json=excluded.rows_json,
                    rows_rejected=excluded.rows_rejected,
                    errors_json=excluded.errors_json,
                    ingested_at=excluded.ingested_at
                """,
                (
                    date,
                    clinic_id,
                    payload,
                    rows_rejected,
                    json.dumps(errors or []),
                    _utcnow(),
                ),
            )

    def list_days(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT date, clinic_id, rows_rejected, ingested_at, rows_json "
                "FROM days ORDER BY date DESC"
            ).fetchall()
        return [
            {
                "date": r["date"],
                "clinic_id": r["clinic_id"],
                "rows": len(json.loads(r["rows_json"])),
                "rows_rejected": r["rows_rejected"],
                "ingested_at": r["ingested_at"],
            }
            for r in rows
        ]

    def get_day(self, date: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM days WHERE date = ?", (date,)).fetchone()
        if row is None:
            return None
        raw_rows = json.loads(row["rows_json"])
        return {
            "date": row["date"],
            "clinic_id": row["clinic_id"],
            "rows": [BillingRow.model_validate(r) for r in raw_rows],
            "rows_rejected": row["rows_rejected"],
            "errors": json.loads(row["errors_json"]),
            "ingested_at": row["ingested_at"],
        }

    def save_narrative(self, narrative: NarrativeResponse) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO narratives (date, payload_json, model, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    narrative.date,
                    narrative.model_dump_json(),
                    narrative.model,
                    narrative.source,
                    narrative.generated_at.isoformat(),
                ),
            )

    def latest_narrative(self, date: str) -> NarrativeResponse | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM narratives WHERE date = ? ORDER BY id DESC LIMIT 1",
                (date,),
            ).fetchone()
        if row is None:
            return None
        return NarrativeResponse.model_validate_json(row["payload_json"])
