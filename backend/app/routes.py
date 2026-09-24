"""REST routes for ingestion, reports and narrative generation."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .narrative import generate_narrative
from .reconcile import build_report
from .schemas import EodReport, IngestResponse, NarrativeResponse, RowError
from .storage import Storage
from .validation import validate_date_string, validate_payload

router = APIRouter(prefix="/api")


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_provider(request: Request):
    return request.app.state.llm_provider


def _bad_date(value: object, field: str = "date") -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "message": f"'{field}' must be a calendar date in YYYY-MM-DD format",
            "errors": [RowError(field=field, error=f"got {value!r}").model_dump()],
        },
    )


def _coerce_strict(value: object) -> bool:
    """Parse the optional 'strict' flag.

    Accepts a real JSON boolean, or the case-insensitive strings
    "true"/"false" (so a form-encoded "false" is not silently truthy).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no", ""):
            return False
    if value is None:
        return False
    raise HTTPException(
        status_code=400,
        detail={
            "message": "'strict' must be a boolean (true/false) or the strings 'true'/'false'",
            "errors": [RowError(field="strict", error=f"got {value!r}").model_dump()],
        },
    )


def _report_for_day(storage: Storage, date: str) -> EodReport:
    day = storage.get_day(date)
    if day is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"No billing log ingested for {date}",
                "hint": "POST /api/billing/ingest with this date's rows first",
            },
        )
    return build_report(
        day["rows"],
        date,
        rows_rejected=day["rows_rejected"],
        clinic_id=day["clinic_id"],
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --- billing ingestion ------------------------------------------------------


def _parse_ingest_request(payload: dict[str, Any]) -> tuple[list, str | None, bool, str | None]:
    """Validate the ingest envelope. Returns (rows, date, strict, clinic_id)."""
    if "rows" not in payload:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Request body must be an object with a 'rows' array",
                "errors": [RowError(field="rows", error="missing 'rows' array").model_dump()],
            },
        )
    raw_rows = payload["rows"]
    if not isinstance(raw_rows, list):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "'rows' must be a JSON array of visit records",
                "errors": [RowError(field="rows", error="must be a JSON array").model_dump()],
            },
        )

    date = payload.get("date")
    if date is not None:
        if not isinstance(date, str) or not date:
            raise _bad_date(date)
        try:
            validate_date_string(date)
        except ValueError as exc:
            raise _bad_date(date) from exc
    if not raw_rows and not date:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "'date' is required when 'rows' is empty",
                "errors": [RowError(field="date", error="required for an empty log").model_dump()],
            },
        )

    strict = _coerce_strict(payload.get("strict", False))
    payload_clinic = payload.get("clinic_id")
    if payload_clinic is not None and (
        not isinstance(payload_clinic, str) or not payload_clinic.strip()
    ):
        payload_clinic = None
    clinic = payload_clinic.strip() if isinstance(payload_clinic, str) else None
    return raw_rows, date, strict, clinic


def _raise_ingest_rejected(message: str, errors: list) -> None:
    raise HTTPException(
        status_code=400,
        detail={
            "message": message,
            "errors": [e.model_dump() for e in errors],
            "accepted": 0,
            "rejected": len(errors),
        },
    )


@router.post("/billing/ingest", response_model=IngestResponse, status_code=201)
def ingest(payload: dict[str, Any], request: Request) -> IngestResponse:
    """Ingest a day's billing log.

    Body: {"rows": [...], "date"?: "YYYY-MM-DD", "strict"?: bool}
    Default: valid rows are accepted, malformed rows are rejected with
    field-level actionable errors. strict=true → all-or-nothing (400 on any error).
    """
    storage = get_storage(request)
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Request body must be an object with a 'rows' array",
                "errors": [RowError(field="rows", error="missing 'rows' array").model_dump()],
            },
        )

    raw_rows, date, strict, payload_clinic = _parse_ingest_request(payload)
    valid, errors, resolved_date, clinic_id = validate_payload(raw_rows, expected_date=date)
    if clinic_id is None and payload_clinic:
        clinic_id = payload_clinic

    has_payload_error = any(e.index is None for e in errors)
    if errors and (strict or has_payload_error):
        if has_payload_error:
            message = "Billing log rejected: payload-level validation failed."
        elif len(valid) == 0:
            message = f"Billing log rejected: all {len(raw_rows)} rows are malformed."
        else:
            message = (
                f"Billing log rejected (strict mode): {len(errors)} malformed row(s) "
                f"— nothing was ingested."
            )
        _raise_ingest_rejected(message, errors)

    if not valid and errors:
        _raise_ingest_rejected(
            f"Billing log rejected: all {len(raw_rows)} rows are malformed.",
            errors,
        )

    if not valid and not errors and clinic_id is None:
        clinic_id = "UNKNOWN"

    if resolved_date is None:
        # Unreachable after validate_payload with either an explicit date or
        # at least one valid row — but never let an assert become a 500.
        raise HTTPException(
            status_code=400,
            detail={"message": "Could not resolve a date for these rows"},
        )
    storage.upsert_day(
        date=resolved_date,
        clinic_id=clinic_id or "UNKNOWN",
        rows=valid,
        rows_rejected=len(errors),
        errors=[e.model_dump() for e in errors],
    )

    if errors:
        message = (
            f"Ingested {len(valid)} row(s) for {resolved_date}; "
            f"rejected {len(errors)} malformed row(s) with actionable errors."
        )
    else:
        message = f"Ingested {len(valid)} row(s) for {resolved_date}."

    return IngestResponse(
        date=resolved_date,
        clinic_id=clinic_id,
        accepted=len(valid),
        rejected=len(errors),
        strict=strict,
        errors=errors,
        message=message,
    )


@router.get("/billing/days")
def list_days(request: Request) -> dict[str, Any]:
    return {"days": get_storage(request).list_days()}


@router.get("/billing/day/{date}")
def get_day(date: str, request: Request) -> dict[str, Any]:
    day = get_storage(request).get_day(date)
    if day is None:
        raise HTTPException(status_code=404, detail={"message": f"No log for {date}"})
    return {
        "date": day["date"],
        "clinic_id": day["clinic_id"],
        "rows_rejected": day["rows_rejected"],
        "errors": day["errors"],
        "rows": [json.loads(r.model_dump_json()) for r in day["rows"]],
    }


# --- deterministic reports ---------------------------------------------------


@router.get("/reports/eod/{date}", response_model=EodReport)
def get_report(date: str, request: Request) -> EodReport:
    return _report_for_day(get_storage(request), date)


@router.post("/reports/compute", response_model=EodReport, status_code=200)
def compute_report(payload: dict[str, Any]) -> EodReport:
    """Stateless: compute a report directly from posted rows (no storage)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise HTTPException(
            status_code=400,
            detail={"message": "Body must be an object with a 'rows' array"},
        )
    date = payload.get("date")
    if date is not None:
        if not isinstance(date, str):
            raise _bad_date(date)
        try:
            validate_date_string(date)
        except ValueError as exc:
            raise _bad_date(date) from exc
    valid, errors, resolved_date, _clinic = validate_payload(
        payload["rows"], expected_date=date if isinstance(date, str) else None
    )
    if errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Rows failed validation; no report computed.",
                "errors": [e.model_dump() for e in errors],
            },
        )
    if not resolved_date:
        raise HTTPException(
            status_code=400, detail={"message": "Could not resolve a date for these rows"}
        )
    return build_report(valid, resolved_date, rows_rejected=0)


# --- narrative ---------------------------------------------------------------


@router.post("/narrative/{date}", response_model=NarrativeResponse)
def create_narrative(date: str, request: Request) -> NarrativeResponse:
    report = _report_for_day(get_storage(request), date)
    provider = get_provider(request)
    narrative = generate_narrative(
        report,
        provider,
        max_retries=request.app.state.llm_max_retries,
    )
    get_storage(request).save_narrative(narrative)
    return narrative


@router.get("/narrative/{date}", response_model=NarrativeResponse)
def get_narrative(date: str, request: Request) -> NarrativeResponse:
    cached = get_storage(request).latest_narrative(date)
    if cached is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"No narrative generated for {date} yet"},
        )
    return cached


@router.get("/narrative/{date}/report", response_model=EodReport)
def narrative_context(date: str, request: Request) -> EodReport:
    """Convenience: the deterministic report backing a narrative."""
    return _report_for_day(get_storage(request), date)
