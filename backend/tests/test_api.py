"""REST API tests: ingestion semantics, report endpoints, error shapes."""

from __future__ import annotations


def _row(**overrides):
    row = {
        "clinic_id": "CLN-KNP-014",
        "visit_id": "V-API-001",
        "timestamp": "2026-07-28T10:00:00Z",
        "doctor_id": "DOC-014-01",
        "line_items": [{"drug_name": "PARACETAMOL", "qty": 2, "unit_price_paise": 2000}],
        "payment_mode": "cash",
        "amount_paid_paise": 4000,
        "discount_paise": 0,
        "is_refund": False,
    }
    row.update(overrides)
    return row


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_ingest_partial_rejects_bad_rows_with_actionable_errors(client, sample_rows):
    rows = sample_rows("2026-07-27")
    response = client.post("/api/billing/ingest", json={"rows": rows})
    assert response.status_code == 201
    body = response.json()
    assert body["accepted"] == 18
    assert body["rejected"] == 1
    assert body["date"] == "2026-07-27"

    error = body["errors"][0]
    assert error["field"] == "payment_mode"
    assert error["visit_id"] == "V-20260727-019"
    assert error["index"] == 18
    assert "cash, card, upi" in error["error"]
    # never a generic 500 — explicit message present
    assert "rejected" in body["message"].lower() or "rejected" in body["message"]


def test_ingest_strict_is_all_or_nothing(client, sample_rows):
    rows = sample_rows("2026-07-27")
    response = client.post("/api/billing/ingest", json={"rows": rows, "strict": True})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["accepted"] == 0
    assert len(detail["errors"]) >= 1
    # nothing stored
    assert client.get("/api/reports/eod/2026-07-27").status_code == 404


def test_ingest_empty_day_needs_date(client):
    response = client.post("/api/billing/ingest", json={"rows": []})
    assert response.status_code == 400
    assert "date" in response.json()["detail"]["message"].lower()

    response = client.post(
        "/api/billing/ingest",
        json={"rows": [], "date": "2026-07-26", "clinic_id": "CLN-KNP-014"},
    )
    assert response.status_code == 201
    assert response.json()["accepted"] == 0


def test_ingest_all_rows_bad(client):
    bad = [_row(payment_mode="cheque")]
    response = client.post("/api/billing/ingest", json={"rows": bad})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["accepted"] == 0
    assert detail["errors"][0]["field"] == "payment_mode"


def test_ingest_mixed_clinics_rejected(client):
    rows = [_row(), _row(visit_id="V-API-002", clinic_id="CLN-OTHER")]
    response = client.post("/api/billing/ingest", json={"rows": rows})
    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any(e["field"] == "clinic_id" and e["index"] is None for e in errors)


def test_reingest_replaces_day_atomically(client):
    first = client.post("/api/billing/ingest", json={"rows": [_row()]})
    assert first.status_code == 201
    report = client.get("/api/reports/eod/2026-07-28").json()
    assert report["reconciliation"]["visits"] == 1

    second_row = _row(
        visit_id="V-API-002",
        timestamp="2026-07-28T11:00:00Z",
        line_items=[{"drug_name": "METFORMIN", "qty": 1, "unit_price_paise": 3000}],
        amount_paid_paise=3000,
    )
    second = client.post(
        "/api/billing/ingest",
        json={"date": "2026-07-28", "rows": [second_row]},  # replaces, not appends
    )
    assert second.status_code == 201
    report = client.get("/api/reports/eod/2026-07-28").json()
    assert report["reconciliation"]["visits"] == 1  # replaced, not 2
    assert report["reconciliation"]["total_billed_paise"] == 3000


def test_report_404_and_seeded_reports(seeded_client):
    assert seeded_client.get("/api/reports/eod/2026-01-01").status_code == 404

    report = seeded_client.get("/api/reports/eod/2026-07-27")
    assert report.status_code == 200
    body = report.json()
    assert body["reconciliation"]["total_billed_paise"] == 319000
    assert body["reconciliation"]["total_collected_paise"] == 317200
    assert body["meta"]["rows_rejected"] == 1

    empty = seeded_client.get("/api/reports/eod/2026-07-26").json()
    assert empty["reconciliation"]["visits"] == 0
    assert empty["meta"]["clinic_id"] == "CLN-KNP-014"
    assert "unknown" not in empty["meta"]["clinic_subtitle"]

    refunds = seeded_client.get("/api/reports/eod/2026-07-25").json()
    assert refunds["reconciliation"]["total_refunded_paise"] == 49000


def test_stateless_compute_endpoint(client, sample_rows):
    # strict by design: malformed rows block computation with actionable errors
    rows = [r for r in sample_rows("2026-07-27") if r.get("payment_mode")]
    response = client.post("/api/reports/compute", json={"rows": rows})
    assert response.status_code == 200
    assert response.json()["reconciliation"]["total_billed_paise"] == 319000


def test_stateless_compute_rejects_bad_rows(client):
    response = client.post("/api/reports/compute", json={"rows": [_row(payment_mode=None)]})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["errors"][0]["field"] == "payment_mode"


def test_list_days(seeded_client):
    days = seeded_client.get("/api/billing/days").json()["days"]
    dates = {d["date"] for d in days}
    assert dates == {"2026-07-25", "2026-07-26", "2026-07-27"}
    rejected = {d["date"]: d["rows_rejected"] for d in days}
    assert rejected["2026-07-27"] == 1


def test_get_day_returns_rows_and_errors(seeded_client):
    body = seeded_client.get("/api/billing/day/2026-07-27").json()
    assert len(body["rows"]) == 18
    assert body["rows_rejected"] == 1
    assert body["errors"][0]["field"] == "payment_mode"


def test_ingest_bad_date_format_rejected(client):
    resp = client.post("/api/billing/ingest", json={"rows": [], "date": "banana"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "YYYY-MM-DD" in detail["message"]


def test_ingest_impossible_date_rejected(client):
    resp = client.post("/api/billing/ingest", json={"rows": [], "date": "2026-13-45"})
    assert resp.status_code == 400
    assert "YYYY-MM-DD" in resp.json()["detail"]["message"]


def test_ingest_string_false_is_not_truthy(client, sample_rows):
    rows = [r for r in sample_rows("2026-07-27") if r.get("payment_mode")][:1]
    bad = [dict(rows[0], payment_mode="cheque")]
    resp = client.post(
        "/api/billing/ingest",
        json={"rows": bad + rows, "strict": "false"},
    )
    # non-strict: the one bad row is rejected, good row accepted
    assert resp.status_code == 201
    body = resp.json()
    assert body["strict"] is False
    assert body["accepted"] == 1
    assert body["rejected"] == 1


def test_ingest_string_true_is_strict(client, sample_rows):
    rows = sample_rows("2026-07-27")
    resp = client.post("/api/billing/ingest", json={"rows": rows, "strict": "true"})
    assert resp.status_code == 400
    assert "strict mode" in resp.json()["detail"]["message"]


def test_ingest_invalid_strict_type_rejected(client, sample_rows):
    resp = client.post(
        "/api/billing/ingest",
        json={"rows": sample_rows("2026-07-27"), "strict": 42},
    )
    assert resp.status_code == 400
    assert "strict" in resp.json()["detail"]["message"]


def test_ingest_discount_over_gross_rejected(client):
    row = _row(discount_paise=99999, amount_paid_paise=0)
    resp = client.post("/api/billing/ingest", json={"rows": [row]})
    assert resp.status_code == 400
    errors = resp.json()["detail"]["errors"]
    assert errors[0]["field"] == "discount_paise"


def test_compute_report_bad_date_rejected(client):
    resp = client.post("/api/reports/compute", json={"rows": [], "date": "banana"})
    assert resp.status_code == 400
    assert "YYYY-MM-DD" in resp.json()["detail"]["message"]
