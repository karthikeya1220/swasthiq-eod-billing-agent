"""Row-level and payload-level validation with specific, actionable errors.

Every rejected row reports the index, visit_id (when known), offending field,
and a human-readable reason. No generic failures.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .config import PAYMENT_MODES
from .schemas import BillingRow, LineItem, RowError

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REQUIRED_FIELDS = (
    "clinic_id",
    "visit_id",
    "timestamp",
    "doctor_id",
    "line_items",
    "payment_mode",
    "amount_paid_paise",
    "discount_paise",
    "is_refund",
)

INT_FIELDS = ("amount_paid_paise", "discount_paise")


def _is_int(value: Any) -> bool:
    # bool is a subclass of int in Python — reject it explicitly.
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be an ISO 8601 string, e.g. 2026-07-27T09:10:00Z")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"must be a valid ISO 8601 timestamp (got {value!r})") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            "must include a UTC offset (e.g. trailing 'Z') — naive timestamps "
            "are ambiguous for hour-of-day bucketing"
        )
    # Normalize to UTC so date-chunking and hour-of-day bucketing are consistent
    # regardless of the offset the client sent (e.g. +05:30 vs Z).
    return parsed.astimezone(UTC)


def validate_date_string(value: Any) -> str:
    """Return value if it is a valid YYYY-MM-DD calendar date, else raise ValueError."""
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValueError(f"must be a calendar date in YYYY-MM-DD format (got {value!r})")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"must be a real calendar date in YYYY-MM-DD format (got {value!r})"
        ) from exc
    return value


def validate_row(raw: Any, index: int) -> tuple[BillingRow | None, list[RowError]]:
    """Validate one raw row. Returns (row, []) on success, (None, errors) otherwise."""
    errors: list[RowError] = []

    def err(field: str | None, message: str, visit_id: str | None = None) -> None:
        errors.append(RowError(index=index, visit_id=visit_id, field=field, error=message))

    if not isinstance(raw, dict):
        err(None, f"row must be a JSON object (got {type(raw).__name__})")
        return None, errors

    visit_id = raw.get("visit_id") if isinstance(raw.get("visit_id"), str) else None

    for field in REQUIRED_FIELDS:
        if field not in raw or raw[field] is None:
            if field == "payment_mode":
                message = "field is required — expected one of: cash, card, upi"
            else:
                message = "field is required"
            err(field, message, visit_id)

    if errors:
        return None, errors

    for field in ("clinic_id", "visit_id", "doctor_id"):
        value = raw[field]
        if not isinstance(value, str) or not value.strip():
            err(field, "must be a non-empty string", visit_id)

    timestamp: datetime | None = None
    try:
        timestamp = _parse_timestamp(raw["timestamp"])
    except ValueError as exc:
        err("timestamp", str(exc), visit_id)

    if not isinstance(raw["is_refund"], bool):
        err("is_refund", "must be a boolean (true or false)", visit_id)

    for field in INT_FIELDS:
        if not _is_int(raw[field]):
            err(
                field,
                f"must be an integer number of paise (got {type(raw[field]).__name__})",
                visit_id,
            )
        elif field == "discount_paise" and raw[field] < 0:
            err(field, "must be >= 0", visit_id)

    payment_mode = raw["payment_mode"]
    if payment_mode not in PAYMENT_MODES:
        if isinstance(payment_mode, str):
            err(
                "payment_mode",
                f"must be one of: {', '.join(PAYMENT_MODES)} (got {payment_mode!r})",
                visit_id,
            )
        else:
            err(
                "payment_mode",
                f"must be one of: {', '.join(PAYMENT_MODES)}",
                visit_id,
            )

    line_items: list[LineItem] = []
    raw_items = raw["line_items"]
    if not isinstance(raw_items, list):
        err("line_items", "must be an array of {drug_name, qty, unit_price_paise}", visit_id)
    elif len(raw_items) == 0:
        err("line_items", "must contain at least one line item", visit_id)
    else:
        for item_index, item in enumerate(raw_items):
            prefix = f"line_items[{item_index}]"
            if not isinstance(item, dict):
                err(prefix, "must be an object with drug_name, qty, unit_price_paise", visit_id)
                continue
            drug = item.get("drug_name")
            if not isinstance(drug, str) or not drug.strip():
                err(f"{prefix}.drug_name", "must be a non-empty string", visit_id)
            qty = item.get("qty")
            if not _is_int(qty):
                err(f"{prefix}.qty", "must be a positive integer", visit_id)
            elif qty <= 0:
                err(f"{prefix}.qty", "must be > 0", visit_id)
            price = item.get("unit_price_paise")
            if not _is_int(price):
                err(f"{prefix}.unit_price_paise", "must be an integer >= 0 paise", visit_id)
            elif price < 0:
                err(f"{prefix}.unit_price_paise", "must be >= 0", visit_id)
            if (
                isinstance(drug, str)
                and drug.strip()
                and _is_int(qty)
                and qty > 0
                and _is_int(price)
                and price >= 0
            ):
                line_items.append(LineItem(drug_name=drug.strip(), qty=qty, unit_price_paise=price))

    amount = raw["amount_paid_paise"]
    discount = raw["discount_paise"]
    is_refund = raw["is_refund"]
    if _is_int(amount) and _is_int(discount) and isinstance(is_refund, bool):
        if is_refund and amount >= 0:
            err(
                "amount_paid_paise",
                "must be negative on a refund (is_refund=true) — "
                "refunds are money going back out (got "
                f"{amount})",
                visit_id,
            )
        elif not is_refund and amount < 0:
            err(
                "amount_paid_paise",
                "must be >= 0 on a sale (is_refund=false); "
                "set is_refund=true for negative adjustments",
                visit_id,
            )

    # Discount must not exceed the gross line-item total (sales only — refunds
    # have no meaningful "billed" amount to discount against).
    if (
        line_items
        and _is_int(discount)
        and discount >= 0
        and isinstance(is_refund, bool)
        and not is_refund
    ):
        gross = sum(item.qty * item.unit_price_paise for item in line_items)
        if discount > gross:
            err(
                "discount_paise",
                f"must be <= gross line-item total ({gross} paise); got {discount}",
                visit_id,
            )

    if errors:
        return None, errors

    try:
        row = BillingRow(
            clinic_id=raw["clinic_id"].strip(),
            visit_id=visit_id.strip() if visit_id else raw["visit_id"].strip(),
            timestamp=timestamp,
            doctor_id=raw["doctor_id"].strip(),
            line_items=line_items,
            payment_mode=payment_mode,
            amount_paid_paise=amount,
            discount_paise=discount,
            is_refund=is_refund,
        )
    except Exception as exc:  # defensive: should not happen after checks above
        err(None, f"failed to build row: {exc}", visit_id)
        return None, errors

    return row, []


def validate_payload(
    raw_rows: Any,
    expected_date: str | None = None,
) -> tuple[list[BillingRow], list[RowError], str | None, str | None]:
    """Validate an entire payload.

    Returns (valid_rows, errors, date, clinic_id).
    Payload-level problems (mixed clinics, mixed dates) are returned as errors
    with index=None and apply to the whole request.
    """
    if not isinstance(raw_rows, list):
        return (
            [],
            [
                RowError(
                    index=None, field="rows", error="rows must be a JSON array of visit records"
                )
            ],
            None,
            None,
        )

    valid: list[BillingRow] = []
    errors: list[RowError] = []
    seen_visit_ids: dict[str, int] = {}

    for index, raw in enumerate(raw_rows):
        row, row_errors = validate_row(raw, index)
        if row is None:
            errors.extend(row_errors)
            continue
        first_index = seen_visit_ids.get(row.visit_id)
        if first_index is not None:
            errors.append(
                RowError(
                    index=index,
                    visit_id=row.visit_id,
                    field="visit_id",
                    error=f"duplicate visit_id — already used at row {first_index}",
                )
            )
            continue
        seen_visit_ids[row.visit_id] = index
        valid.append(row)

    # single clinic per payload
    clinic_ids = sorted({r.clinic_id for r in valid})
    if len(clinic_ids) > 1:
        errors.append(
            RowError(
                index=None,
                field="clinic_id",
                error="mixed clinics in one payload: "
                + ", ".join(clinic_ids)
                + " — submit one clinic per request",
            )
        )

    # date consistency
    dates = sorted({r.timestamp.date().isoformat() for r in valid})
    date: str | None = expected_date
    if expected_date:
        mismatched = [d for d in dates if d != expected_date]
        if mismatched:
            errors.append(
                RowError(
                    index=None,
                    field="timestamp",
                    error=(
                        f"rows for {', '.join(mismatched)} do not match "
                        f"requested date {expected_date}"
                    ),
                )
            )
    elif dates:
        if len(dates) > 1:
            errors.append(
                RowError(
                    index=None,
                    field="timestamp",
                    error="rows span multiple dates: "
                    + ", ".join(dates)
                    + " — pass an explicit 'date' or submit one day per request",
                )
            )
        else:
            date = dates[0]

    # If payload-level errors exist, reject the payload entirely.
    payload_level = [e for e in errors if e.index is None]
    if payload_level:
        return [], errors, expected_date, clinic_ids[0] if len(clinic_ids) == 1 else None

    clinic_id = clinic_ids[0] if clinic_ids else None
    if expected_date and not dates and not raw_rows:
        date = expected_date
    return valid, errors, date, clinic_id
