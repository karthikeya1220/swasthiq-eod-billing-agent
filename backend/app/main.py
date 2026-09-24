"""FastAPI application entrypoint.

Run: uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .llm import OllamaProvider
from .routes import router
from .storage import Storage
from .validation import validate_payload

log = logging.getLogger("swasthiq.seed")


def _seed_sample_data(storage: Storage) -> None:
    """Load the provided sample days (partial ingest: skip malformed rows)."""
    sample_dir: Path = config.sample_data_dir()
    if not sample_dir.exists():
        log.warning("Sample data dir %s does not exist — skipping seed", sample_dir)
        return
    for path in sorted(sample_dir.glob("billing_log_*.json")):
        match = re.search(r"billing_log_(\d{4}-\d{2}-\d{2})\.json$", path.name)
        if not match:
            continue
        date = match.group(1)
        if storage.get_day(date) is not None:
            continue
        try:
            raw_rows = json.loads(path.read_text())
        except OSError as exc:
            log.error("Could not read %s: %s", path, exc)
            continue
        except json.JSONDecodeError as exc:
            log.error("Invalid JSON in %s: %s", path, exc)
            continue
        if not isinstance(raw_rows, list):
            log.error("%s: top level must be a JSON array", path)
            continue
        valid, errors, resolved_date, clinic_id = validate_payload(raw_rows, expected_date=date)
        if resolved_date is None:
            log.error("%s: could not resolve a date", path)
            continue
        clinic_id = clinic_id or "CLN-KNP-014"  # sample set is one clinic; empty day has no rows
        storage.upsert_day(
            date=resolved_date,
            clinic_id=clinic_id,
            rows=valid,
            rows_rejected=len(errors),
            errors=[e.model_dump() for e in errors],
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = Storage(config.database_path())
    if config.seed_sample_data() and not storage.list_days():
        _seed_sample_data(storage)
    app.state.storage = storage
    app.state.llm_provider = OllamaProvider(
        base_url=config.ollama_base_url(),
        model=config.ollama_model(),
        timeout=config.llm_timeout_seconds(),
    )
    app.state.llm_max_retries = config.llm_max_retries()
    yield


app = FastAPI(
    title="SwasthiQ EOD Billing & Analytics API",
    version="1.0.0",
    description=(
        "Deterministic EOD reconciliation + analytics for clinic billing logs, "
        "with an LLM-grounded WhatsApp narrative layer."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
