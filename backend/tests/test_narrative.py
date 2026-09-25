"""Narrative grounding tests: zero invented numbers, malformed-response handling."""

from __future__ import annotations

import json

from app.figures import (
    build_figure_index,
    check_label_bindings,
    format_paise,
    indian_number,
    trace_tokens,
)
from app.llm import LLMError, MockProvider
from app.narrative import (
    fallback_narrative,
    generate_narrative,
    parse_model_response,
)
from app.reconcile import build_report
from app.validation import validate_payload


def _report_27(sample_rows, rejected: int = 1):
    rows = [r for r in sample_rows("2026-07-27") if r.get("payment_mode")]
    valid, errors, date, _ = validate_payload(rows, expected_date="2026-07-27")
    assert not errors
    return build_report(valid, date, rows_rejected=rejected)


def _provider(narrative_text: str) -> MockProvider:
    return MockProvider(json.dumps({"narrative": narrative_text}))


GOOD_NARRATIVE = (
    "Good evening! Here's today's summary for Mehta Clinic (27 Jul).\n"
    "₹3,190 billed across 18 visits, ₹3,172 collected (99%).\n"
    "₹18 is still outstanding across 3 visits.\n"
    "Busiest hour: 1pm-2pm, with ₹760 in revenue.\n"
    "Top mover by quantity: OMEPRAZOLE (18 units).\n"
    "Top by revenue: ATORVASTATIN (₹1,200).\n"
    "Note: cost data wasn't available, so this is revenue, not profit — "
    "flagging rather than estimating."
)


# --- formatting --------------------------------------------------------------


def test_indian_number_formatting():
    assert indian_number(0) == "0"
    assert indian_number(42850) == "42,850"
    assert indian_number(120000) == "1,20,000"
    assert indian_number(-49000) == "-49,000"


def test_format_paise():
    assert format_paise(0) == "₹0"
    assert format_paise(500) == "₹5"
    assert format_paise(350) == "₹3.50"
    assert format_paise(319000) == "₹3,190"
    assert format_paise(-49000) == "-₹490"


# --- tracing -----------------------------------------------------------------


def test_good_narrative_is_fully_grounded(sample_rows):
    report = _report_27(sample_rows)
    traced, ungrounded = trace_tokens(GOOD_NARRATIVE, build_figure_index(report))
    assert ungrounded == []
    fields = {t["report_field"] for t in traced}
    assert "reconciliation.total_billed_paise" in fields
    assert "reconciliation.total_collected_paise" in fields
    assert "reconciliation.total_outstanding_paise" in fields
    assert "reconciliation.collected_pct_of_billed" in fields
    assert "analytics.peak_hour_range" in fields
    figures = {t["figure"] for t in traced}
    assert "₹3,190" in figures and "₹1,200" in figures and "99%" in figures


def test_invented_number_is_ungrounded(sample_rows):
    report = _report_27(sample_rows)
    refs = build_figure_index(report)
    bad = "We billed ₹99,999 across 42 visits today."
    _traced, ungrounded = trace_tokens(bad, refs)
    assert "₹99,999" in ungrounded
    assert "42" in ungrounded


def test_invented_percentage_rejected(sample_rows):
    report = _report_27(sample_rows)
    _traced, ungrounded = trace_tokens("Collected (87%) of billed.", build_figure_index(report))
    assert "87%" in ungrounded


# --- label ↔ amount consistency ----------------------------------------------


def _facts_27(sample_rows) -> dict:
    from app.narrative import build_fact_sheet

    return build_fact_sheet(_report_27(sample_rows))


def test_correct_labels_pass(sample_rows):
    facts = _facts_27(sample_rows)
    assert check_label_bindings(GOOD_NARRATIVE, facts) == []


def test_billed_label_on_collected_amount_rejected(sample_rows):
    facts = _facts_27(sample_rows)
    bad = "Billed ₹3,172 (99%) across visits today."
    errors = check_label_bindings(bad, facts)
    assert errors, "should reject collected amount under billed label"
    assert "billed" in errors[0]
    assert "₹3,190" in errors[0]


def test_collected_label_on_billed_amount_rejected(sample_rows):
    facts = _facts_27(sample_rows)
    bad = "Collected ₹3,190 from patients today."
    errors = check_label_bindings(bad, facts)
    assert errors
    assert "collected" in errors[0]
    assert "₹3,172" in errors[0]


def test_outstanding_label_on_collected_amount_rejected(sample_rows):
    facts = _facts_27(sample_rows)
    bad = "Outstanding ₹3,172 is what we collected."
    errors = check_label_bindings(bad, facts)
    assert errors
    assert "outstanding" in errors[0]
    assert "₹18" in errors[0]


def test_pct_of_billed_without_collected_rejected(sample_rows):
    facts = _facts_27(sample_rows)
    bad = "Billed ₹3,190 across visits — that's 99% of billed."
    errors = check_label_bindings(bad, facts)
    assert any("99%" in e and "collected" in e for e in errors)


def test_pct_of_billed_with_collected_nearby_passes(sample_rows):
    facts = _facts_27(sample_rows)
    ok = "₹3,190 billed across visits, ₹3,172 collected — 99% of billed collected."
    assert check_label_bindings(ok, facts) == []


def test_zero_valued_label_not_flagged_on_empty_day(sample_rows):
    # Empty days have "₹0" in several money facts — the token resolves to the
    # first match, so "billed or collected ₹0" must not read as a swapped label.
    from app.narrative import build_fact_sheet

    valid, errors, date, _ = validate_payload(sample_rows("2026-07-26"), expected_date="2026-07-26")
    assert not errors
    facts = build_fact_sheet(build_report(valid, date))
    text = "Nothing was billed or collected today — ₹0 moved at all."
    assert check_label_bindings(text, facts) == []


def test_label_error_retries_then_succeeds(sample_rows):
    report = _report_27(sample_rows)
    bad = json.dumps({"narrative": "Billed ₹3,172 (99%) across visits."})
    good = json.dumps({"narrative": GOOD_NARRATIVE})
    responses = [bad, good]
    calls = {"n": 0}

    def chained(system, user):
        out = responses[calls["n"]]
        calls["n"] += 1
        return out

    provider = MockProvider(bad)
    provider.complete = chained  # type: ignore[method-assign]
    result = generate_narrative(report, provider, max_retries=1)
    assert calls["n"] == 2
    assert result.source == "ollama"
    assert result.narrative == GOOD_NARRATIVE


def test_label_error_exhausts_retries_then_falls_back(sample_rows):
    report = _report_27(sample_rows)
    bad = json.dumps({"narrative": "Billed ₹3,172 (99%) across visits."})
    provider = MockProvider(bad)
    result = generate_narrative(report, provider, max_retries=1)
    assert result.source == "fallback"
    assert "mislabelled" in (result.llm_error or "")
    assert len(provider.calls) == 2
    # fallback itself must pass label check
    from app.narrative import build_fact_sheet

    assert check_label_bindings(result.narrative, build_fact_sheet(report)) == []


def _narrative_without_revenue_line() -> str:
    return GOOD_NARRATIVE.replace("Top by revenue: ATORVASTATIN (₹1,200).\n", "")


def test_missing_ranking_line_retries_then_succeeds(sample_rows):
    report = _report_27(sample_rows)
    incomplete = json.dumps({"narrative": _narrative_without_revenue_line()})
    good = json.dumps({"narrative": GOOD_NARRATIVE})
    responses = [incomplete, good]
    calls = {"n": 0}

    def chained(system, user):
        out = responses[calls["n"]]
        calls["n"] += 1
        return out

    provider = MockProvider(incomplete)
    provider.complete = chained  # type: ignore[method-assign]
    result = generate_narrative(report, provider, max_retries=1)
    assert calls["n"] == 2
    assert result.source == "ollama"
    assert "Top by revenue" in result.narrative


def test_missing_ranking_line_exhausts_retries_then_falls_back(sample_rows):
    report = _report_27(sample_rows)
    incomplete = json.dumps({"narrative": _narrative_without_revenue_line()})
    provider = MockProvider(incomplete)
    result = generate_narrative(report, provider, max_retries=1)
    assert result.source == "fallback"
    assert "missing required lines" in (result.llm_error or "")
    assert len(provider.calls) == 2
    assert "ATORVASTATIN" in result.narrative


# --- parse -------------------------------------------------------------------


def test_parse_plain_json():
    assert parse_model_response('{"narrative": "hello"}') == "hello"


def test_parse_fenced_json():
    content = '```json\n{"narrative": "hello"}\n```'
    assert parse_model_response(content) == "hello"


def test_parse_rejects_missing_field():
    try:
        parse_model_response('{"foo": 1}')
    except ValueError as exc:
        assert "narrative" in str(exc)
    else:
        raise AssertionError("should have raised")


def test_parse_rejects_non_json():
    try:
        parse_model_response("Sure! Here's a summary...")
    except ValueError as exc:
        assert "JSON" in str(exc)
    else:
        raise AssertionError("should have raised")


# --- generation --------------------------------------------------------------


def test_generate_happy_path(sample_rows):
    report = _report_27(sample_rows)
    provider = _provider(GOOD_NARRATIVE)
    result = generate_narrative(report, provider)

    assert result.grounded is True
    assert result.source == "ollama"
    assert result.narrative == GOOD_NARRATIVE
    assert len(provider.calls) == 1
    assert all(t.figure for t in result.traced_figures)
    # every traced figure exists in the report index
    displays = {ref.display for ref in build_figure_index(report)}
    for traced in result.traced_figures:
        assert traced.figure in displays or traced.figure.strip() in displays


def test_retry_on_ungrounded_then_success(sample_rows):
    report = _report_27(sample_rows)
    bad = json.dumps({"narrative": "We made ₹1,00,000 today."})
    good = json.dumps({"narrative": GOOD_NARRATIVE})
    provider = MockProvider(bad)
    responses = [bad, good]

    calls = {"n": 0}

    def chained(system, user):
        out = responses[calls["n"]]
        calls["n"] += 1
        return out

    provider.complete = chained  # type: ignore[method-assign]
    result = generate_narrative(report, provider, max_retries=1)
    assert calls["n"] == 2
    assert result.source == "ollama"
    assert result.grounded is True
    assert result.narrative == GOOD_NARRATIVE


def test_off_schema_falls_back(sample_rows):
    report = _report_27(sample_rows)
    provider = MockProvider("not json at all")
    result = generate_narrative(report, provider, max_retries=1)

    assert result.source == "fallback"
    assert result.grounded is True
    assert result.llm_error is not None
    assert "off-schema" in result.llm_error
    assert len(provider.calls) == 2  # initial + one retry
    # fallback itself must be fully grounded
    _traced, ungrounded = trace_tokens(result.narrative, build_figure_index(report))
    assert ungrounded == []
    assert "revenue, not profit" in result.narrative


def test_llm_connection_error_falls_back(sample_rows):
    report = _report_27(sample_rows)
    provider = MockProvider(LLMError("connection refused"))
    result = generate_narrative(report, provider, max_retries=1)
    assert result.source == "fallback"
    assert "connection refused" in (result.llm_error or "")
    assert result.grounded is True


def test_missing_narrative_field_retries_then_falls_back(sample_rows):
    report = _report_27(sample_rows)
    provider = MockProvider('{"unexpected": true}')
    result = generate_narrative(report, provider, max_retries=1)
    assert result.source == "fallback"
    assert "off-schema" in (result.llm_error or "")


def test_persisted_numbers_only_in_fallback_for_edge_days(sample_rows):
    for date, rejected in (("2026-07-25", 0), ("2026-07-26", 0)):
        rows = sample_rows(date)
        valid, _errors, _, _ = validate_payload(rows, expected_date=date)
        report = build_report(valid, date, rows_rejected=rejected)
        narrative = fallback_narrative(report)
        _traced, ungrounded = trace_tokens(narrative, build_figure_index(report))
        assert ungrounded == [], (date, ungrounded, narrative)
        assert "not profit" in narrative
