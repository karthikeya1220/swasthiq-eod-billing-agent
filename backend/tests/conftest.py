"""Shared fixtures: sample-day loaders and an isolated app/client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


def load_day(date: str) -> list:
    path = SAMPLE_DIR / f"billing_log_{date}.json"
    return json.loads(path.read_text())


@pytest.fixture()
def sample_rows():
    return load_day


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_billing.db"))
    monkeypatch.setenv("SEED_SAMPLE_DATA", "0")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def seeded_client(client, sample_rows):
    """Client with all three sample days ingested (partial: skips bad rows)."""
    for date in ("2026-07-25", "2026-07-26", "2026-07-27"):
        rows = sample_rows(date)
        response = client.post(
            "/api/billing/ingest",
            # empty day has no rows to infer clinic from — pass it explicitly
            json={"date": date, "rows": rows, "clinic_id": "CLN-KNP-014"},
        )
        if not rows:
            assert response.status_code == 201
        else:
            # 25th is all valid; 27th has one malformed row (partial ingest)
            assert response.status_code == 201, response.text
    return client
