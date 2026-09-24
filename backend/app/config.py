"""Application configuration via environment variables.

Env vars are re-read on each access via the *() helpers so tests can monkeypatch
them without reimporting the module. Static values (payment modes, clinic
directory, CORS) are constants.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def sample_data_dir() -> Path:
    return Path(os.getenv("SAMPLE_DATA_DIR", str(BASE_DIR / "sample_data")))


def database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "billing.db")))


def ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def ollama_model() -> str:
    return os.getenv("OLLAMA_MODEL", "llama3.2:3b")


def llm_timeout_seconds() -> float:
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))


def llm_max_retries() -> int:
    return int(os.getenv("LLM_MAX_RETRIES", "1"))


def seed_sample_data() -> bool:
    return os.getenv("SEED_SAMPLE_DATA", "1") == "1"


ALLOWED_ORIGINS = [
    origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",") if origin.strip()
]

PAYMENT_MODES = ("cash", "card", "upi")

CLINICS = {
    "CLN-KNP-014": {
        "name": "Mehta Multi-Specialty Clinic",
        "short_name": "Mehta Clinic",
        "address": "Kanpur, Uttar Pradesh",
    },
}


def clinic_display(clinic_id: str) -> dict[str, str]:
    info = CLINICS.get(clinic_id, {})
    name = info.get("name", clinic_id)
    address = info.get("address", "")
    subtitle = f"{name} — {address}" if address else name
    return {
        "clinic_id": clinic_id,
        "clinic_name": name,
        "clinic_short_name": info.get("short_name", name),
        "clinic_subtitle": subtitle,
    }
