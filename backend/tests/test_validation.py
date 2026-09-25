"""Validation tests: field-level, actionable errors for malformed rows."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.validation import validate_payload, validate_row


def make_row(**overrides):
    row = {
        "clinic_id": "CLN-KNP-014",
        "visit_id": "V-TEST-001",
        "timestamp": "2026-07-27T09:10:00Z",
        "doctor_id": "DOC-014-01",
        "line_items": [{"drug_name": "PARACETAMOL", "qty": 2, "unit_price_paise": 2000}],
        "payment_mode": "cash",
        "amount_paid_paise": 4000,
        "discount_paise": 0,
        "is_refund": False,
    }
    row.update(overrides)
    return row


def test_valid_row_passes():
    row, errors = validate_row(make_row(), 0)
    assert errors == []
    assert row is not None
    assert row.amount_paid_paise == 4000


def test_missing_payment_mode_is_actionable():
    raw = make_row()
    del raw["payment_mode"]
    row, errors = validate_row(raw, 7)
    assert row is None
    assert len(errors) == 1
    error = errors[0]
    assert error.index == 7
    assert error.visit_id == "V-TEST-001"
    assert error.field == "payment_mode"
    assert "cash, card, upi" in error.error


def test_invalid_payment_mode_value():
    row, errors = validate_row(make_row(payment_mode="cheque"), 0)
    assert row is None
    assert errors[0].field == "payment_mode"
    assert "cheque" in errors[0].error


def test_naive_timestamp_rejected():
    row, errors = validate_row(make_row(timestamp="2026-07-27T09:10:00"), 0)
    assert row is None
    assert errors[0].field == "timestamp"
    assert "UTC" in errors[0].error or "offset" in errors[0].error


def test_unparseable_timestamp_rejected():
    row, errors = validate_row(make_row(timestamp="yesterday"), 0)
    assert row is None
    assert errors[0].field == "timestamp"


def test_refund_must_be_negative():
    row, errors = validate_row(make_row(is_refund=True, amount_paid_paise=4000), 0)
    assert row is None
    assert errors[0].field == "amount_paid_paise"
    assert "negative" in errors[0].error


def test_sale_must_not_be_negative():
    row, errors = validate_row(make_row(is_refund=False, amount_paid_paise=-100), 0)
    assert row is None
    assert errors[0].field == "amount_paid_paise"


def test_bool_rejected_for_money_field():
    row, errors = validate_row(make_row(amount_paid_paise=True), 0)
    assert row is None
    assert errors[0].field == "amount_paid_paise"
    assert "integer" in errors[0].error


def test_bad_line_item_paths():
    raw = make_row()
    raw["line_items"] = [
        {"drug_name": "", "qty": 1, "unit_price_paise": 100},
        {"drug_name": "X", "qty": 0, "unit_price_paise": 100},
        {"drug_name": "Y", "qty": 1, "unit_price_paise": -5},
    ]
    row, errors = validate_row(raw, 0)
    assert row is None
    fields = {e.field for e in errors}
    assert "line_items[0].drug_name" in fields
    assert "line_items[1].qty" in fields
    assert "line_items[2].unit_price_paise" in fields


def test_empty_line_items_rejected():
    row, errors = validate_row(make_row(line_items=[]), 0)
    assert row is None
    assert errors[0].field == "line_items"


def test_duplicate_visit_id_rejected():
    rows = [make_row(), deepcopy(make_row())]
    valid, errors, date, _ = validate_payload(rows)
    assert len(valid) == 1
    assert any(e.field == "visit_id" and "duplicate" in e.error for e in errors)
    assert date == "2026-07-27"


def test_mixed_clinics_rejected_payload_level():
    rows = [make_row(), make_row(visit_id="V-TEST-002", clinic_id="CLN-OTHER")]
    valid, errors, _, _ = validate_payload(rows)
    assert valid == []
    assert any(e.index is None and e.field == "clinic_id" for e in errors)


def test_mixed_dates_rejected_payload_level():
    rows = [make_row(), make_row(visit_id="V-TEST-002", timestamp="2026-07-28T10:00:00Z")]
    valid, errors, _, _ = validate_payload(rows)
    assert valid == []
    assert any(e.index is None and "multiple dates" in e.error for e in errors)


def test_expected_date_mismatch():
    rows = [make_row()]
    valid, errors, _, _ = validate_payload(rows, expected_date="2026-07-26")
    assert valid == []
    assert any("do not match requested date" in e.error for e in errors)


def test_non_object_row():
    valid, errors, _, _ = validate_payload(["not a dict"])
    assert valid == []
    assert errors[0].index == 0
    assert "JSON object" in errors[0].error


def test_discount_exceeding_gross_rejected():
    raw = make_row(
        line_items=[{"drug_name": "X", "qty": 1, "unit_price_paise": 1000}],
        discount_paise=2000,
        amount_paid_paise=0,
    )
    row, errors = validate_row(raw, 0)
    assert row is None
    assert errors[0].field == "discount_paise"
    assert "gross" in errors[0].error


def test_discount_equal_to_gross_allowed():
    raw = make_row(
        line_items=[{"drug_name": "X", "qty": 1, "unit_price_paise": 1000}],
        discount_paise=1000,
        amount_paid_paise=0,
    )
    row, errors = validate_row(raw, 0)
    assert errors == []
    assert row is not None


def test_overpaid_sale_rejected():
    # billed = 2 x 2000 = 4000; paying 5000 would make outstanding negative.
    raw = make_row(amount_paid_paise=5000)
    row, errors = validate_row(raw, 0)
    assert row is None
    assert len(errors) == 1
    error = errors[0]
    assert error.field == "amount_paid_paise"
    assert "billed" in error.error
    assert "outstanding" in error.error
    assert "4000" in error.error


def test_overpayment_checked_against_discounted_billed():
    # billed = 5000 - 1000 discount = 4000; 4001 paise is one paise too much.
    raw = make_row(
        line_items=[{"drug_name": "X", "qty": 1, "unit_price_paise": 5000}],
        discount_paise=1000,
        amount_paid_paise=4001,
    )
    row, errors = validate_row(raw, 0)
    assert row is None
    assert errors[0].field == "amount_paid_paise"
    assert "(4000 paise" in errors[0].error


def test_exact_billed_amount_allowed():
    raw = make_row(
        line_items=[{"drug_name": "X", "qty": 1, "unit_price_paise": 5000}],
        discount_paise=1000,
        amount_paid_paise=4000,
    )
    row, errors = validate_row(raw, 0)
    assert errors == []
    assert row is not None


def test_partial_payment_allowed():
    raw = make_row(amount_paid_paise=2500)
    row, errors = validate_row(raw, 0)
    assert errors == []
    assert row is not None


def test_refund_skips_overpayment_check():
    raw = make_row(is_refund=True, amount_paid_paise=-99999)
    row, errors = validate_row(raw, 0)
    assert errors == []
    assert row is not None


def test_bad_date_string_rejected():
    from app.validation import validate_date_string

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        validate_date_string("banana")
    with pytest.raises(ValueError, match="real calendar date"):
        validate_date_string("2026-13-45")
    assert validate_date_string("2026-07-27") == "2026-07-27"
