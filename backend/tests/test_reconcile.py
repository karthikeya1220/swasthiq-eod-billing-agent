"""Deterministic reconciliation + analytics against hand-computed expectations."""

from __future__ import annotations

from app.reconcile import build_report, hour_label, hour_range
from app.validation import validate_payload


def _report(rows, date, rows_rejected=0):
    valid, errors, _, _ = validate_payload(rows, expected_date=date)
    assert not errors, errors
    return build_report(valid, date, rows_rejected=rows_rejected)


def test_hour_labels():
    assert hour_label(0) == "12am"
    assert hour_label(9) == "9am"
    assert hour_label(12) == "12pm"
    assert hour_label(13) == "1pm"
    assert hour_range(13) == "1pm-2pm"


# --- 27 Jul 2026: happy path + one malformed row -----------------------------


def test_full_day_reconciliation(sample_rows):
    rows = sample_rows("2026-07-27")
    # V-019 is malformed (missing payment_mode); drop it like ingest does
    valid_raw = [r for r in rows if r.get("payment_mode")]
    rejected = len(rows) - len(valid_raw)
    report = _report(valid_raw, "2026-07-27", rows_rejected=rejected)

    rec = report.reconciliation
    assert rec.total_billed_paise == 319000  # ₹3,190 (after ₹70 discounts)
    assert rec.total_collected_paise == 317200  # ₹3,172
    assert rec.total_outstanding_paise == 1800  # ₹18
    assert rec.total_discount_paise == 7000
    assert rec.total_refunded_paise == 0
    assert rec.net_collected_paise == 317200
    assert rec.collected_pct_of_billed == 99
    assert rec.visits == 18
    assert rec.outstanding_visits == 3  # V-004, V-011, V-016
    assert rec.refund_visits == 0

    cash = rec.by_mode["cash"]
    assert (cash.billed_paise, cash.collected_paise, cash.outstanding_paise) == (
        127500,
        127000,
        500,
    )
    assert cash.visits == 5 and cash.outstanding_visits == 1

    card = rec.by_mode["card"]
    assert (card.billed_paise, card.collected_paise, card.outstanding_paise) == (83500, 82700, 800)
    assert card.visits == 7 and card.outstanding_visits == 1

    upi = rec.by_mode["upi"]
    assert (upi.billed_paise, upi.collected_paise, upi.outstanding_paise) == (108000, 107500, 500)
    assert upi.visits == 6 and upi.outstanding_visits == 1

    # identity holds: outstanding == billed - collected, both total and per mode
    assert rec.total_outstanding_paise == rec.total_billed_paise - rec.total_collected_paise
    for breakdown in rec.by_mode.values():
        assert breakdown.outstanding_paise == breakdown.billed_paise - breakdown.collected_paise


def test_full_day_analytics(sample_rows):
    rows = [r for r in sample_rows("2026-07-27") if r.get("payment_mode")]
    report = _report(rows, "2026-07-27", rows_rejected=1)
    an = report.analytics

    hours = {e.hour: e.revenue_paise for e in an.revenue_by_hour}
    assert hours == {
        9: 9000,
        10: 57000,
        11: 33500,
        12: 9500,
        13: 76000,
        14: 3500,
        15: 41500,
        16: 61000,
        17: 22000,
        18: 6000,
    }
    assert sum(hours.values()) == 319000  # hours sum to total billed

    assert an.peak_hour == 13
    assert an.peak_hour_label == "1pm"
    assert an.peak_hour_range == "1pm-2pm"
    assert an.peak_hour_revenue_paise == 76000

    by_qty = [(m.drug_name, m.qty) for m in an.top_medicines_by_quantity]
    assert by_qty == [
        ("OMEPRAZOLE", 18),
        ("METFORMIN", 14),
        ("AMOXICILLIN", 11),
        ("PARACETAMOL", 11),
        ("ATORVASTATIN", 10),
        ("PARACETMOL", 2),
    ]

    by_rev = [(m.drug_name, m.revenue_paise) for m in an.top_medicines_by_revenue]
    assert by_rev == [
        ("ATORVASTATIN", 120000),
        ("OMEPRAZOLE", 72000),
        ("AMOXICILLIN", 66000),
        ("METFORMIN", 42000),
        ("PARACETAMOL", 22000),
        ("PARACETMOL", 4000),
    ]

    # the two rankings are genuinely distinct orderings
    assert [d for d, _ in by_qty] != [d for d, _ in by_rev]

    # typo flag surfaced, not silently merged
    assert any("PARACETMOL" in w and "PARACETAMOL" in w for w in report.meta.data_quality_warnings)
    assert report.meta.rows_rejected == 1


# --- 25 Jul 2026: refunds-only day (non-happy-path) ---------------------------


def test_refunds_only_day(sample_rows):
    rows = sample_rows("2026-07-25")
    report = _report(rows, "2026-07-25")
    rec = report.reconciliation

    assert rec.visits == 0
    assert rec.total_billed_paise == 0
    assert rec.total_collected_paise == 0
    assert rec.total_outstanding_paise == 0
    assert rec.total_refunded_paise == 49000  # 24000 + 22000 + 3000
    assert rec.net_collected_paise == -49000
    assert rec.collected_pct_of_billed is None
    assert rec.refund_visits == 3
    assert rec.by_mode["card"].refunded_paise == 24000
    assert rec.by_mode["upi"].refunded_paise == 25000

    an = report.analytics
    assert an.revenue_by_hour == []  # refunds never bucket into hours
    assert an.peak_hour is None
    assert an.top_medicines_by_quantity == []  # no medicine rankings
    assert an.top_medicines_by_revenue == []
    assert any("Refunds-only" in w for w in report.meta.data_quality_warnings)


# --- 26 Jul 2026: empty day ---------------------------------------------------


def test_empty_day(sample_rows):
    rows = sample_rows("2026-07-26")
    assert rows == []
    report = _report(rows, "2026-07-26")
    rec = report.reconciliation

    assert rec.total_billed_paise == 0
    assert rec.total_collected_paise == 0
    assert rec.total_outstanding_paise == 0
    assert rec.total_refunded_paise == 0
    assert rec.visits == 0
    assert rec.collected_pct_of_billed is None
    assert report.analytics.peak_hour is None
    assert report.analytics.top_medicines_by_quantity == []
    assert report.meta.rows_ingested == 0
    assert any("No visits recorded" in w for w in report.meta.data_quality_warnings)
    # clinic comes from the day record / explicit arg, not from rows
    assert report.meta.clinic_id == "unknown"


def test_empty_day_with_explicit_clinic():
    report = build_report([], "2026-07-26", clinic_id="CLN-KNP-014")
    assert report.meta.clinic_id == "CLN-KNP-014"
    assert report.meta.clinic_name == "Mehta Multi-Specialty Clinic"


def test_determinism(sample_rows):
    rows = [r for r in sample_rows("2026-07-27") if r.get("payment_mode")]
    a = _report(rows, "2026-07-27")
    b = _report(rows, "2026-07-27")
    # identical inputs → identical numbers (timestamps of generation may differ)
    assert a.reconciliation.model_dump() == b.reconciliation.model_dump()
    assert a.analytics.model_dump() == b.analytics.model_dump()
