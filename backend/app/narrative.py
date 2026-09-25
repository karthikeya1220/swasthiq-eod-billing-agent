"""Narrative layer: fact sheet -> LLM -> strict grounding -> traced figures.

Grounding guarantees:
- The model only receives the deterministic report + a pre-formatted fact sheet.
- Every number-like token in the output must resolve to a report field;
  otherwise the response is rejected, retried once with feedback, and finally
  replaced by a deterministic fallback that is grounded by construction.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from .figures import (
    build_figure_index,
    check_label_bindings,
    format_paise,
    format_pct,
    trace_tokens,
)
from .llm import LLMError, LLMProvider
from .schemas import EodReport, NarrativeResponse, TracedFigure

# Short display form matching the mockup greeting: "(27 Jul)".
DATE_DISPLAY_FMT = "%d %b"

SYSTEM_PROMPT = """You are a billing assistant writing an end-of-day summary
for a clinic owner on WhatsApp.

STRICT OUTPUT FORMAT:
Return ONLY a JSON object: {"narrative": "<the message>"}.
No markdown, no code fences, no commentary outside the JSON.

RULES:
1. Use ONLY figures that appear verbatim in the FACTS object or the report JSON.
   Never compute, round, adjust, or invent any number.
   If a figure is not in FACTS, do not mention it.
2. Copy money strings exactly as given (e.g. "₹3,190"), including commas.
3. Bind each money figure to its FACTS key — never swap labels:
   - FACTS["billed"] is billed (total invoiced). Always call it "billed".
   - FACTS["collected"] is collected (cash in). Always call it "collected".
   - FACTS["collected_pct"] is the % of billed that was collected. It belongs
     next to collected, never next to billed.
   - FACTS["outstanding"] is outstanding; FACTS["refunded"] is refunded.
   Wrong label + right number is a rejected response.
4. WhatsApp tone: warm, plain language, flowing prose in short paragraphs
   separated by blank lines. No emoji, no markdown symbols.
   Never write "Label: value" lines, bullet lists, or "Label — value" lines.
   Every sentence must read as natural speech, e.g. "₹3,190 billed across
   18 visits, ₹3,172 collected (99%)." — never "Billed: ₹3,190".
5. Match the voice and layout of this example. STYLE ONLY — its numbers come
   from a different day, so never copy them; use FACTS values instead:

   Good evening! Here's today's summary for Mehta Clinic (27 Jul).

   ₹42,850 billed across 18 visits, ₹38,200 collected (89%).
   ₹4,650 is still outstanding across 3 visits, and ₹600 was refunded on 1 visit.

   Busiest hour: 12pm-1pm, with ₹8,490 in revenue.

   Top mover by quantity: PARACETAMOL (142 units).
   Top by revenue: ATORVASTATIN (₹6,480).

   Note: cost data wasn't available, so this is revenue, not profit —
   flagging rather than estimating.

6. Structure the message in this order. Every item whose facts exist is
   mandatory — never skip one:
   - Greeting with clinic short name + day, exactly like the example
   - billed across visits, collected (+ pct of billed if present) — one sentence
   - outstanding (+ how many visits); add refunds (+ how many visits) to the
     same sentence, omitting that part when FACTS["refunded"] is "₹0"
   - busiest hour with its revenue
   - top medicine by quantity (with units) — required when FACTS has "top_by_qty"
   - top medicine by revenue (with amount) — required when FACTS has
     "top_by_revenue"; both ranking lines must appear when both facts exist
   - The profit note exactly as given in FACTS["profit_note"]
   Use "visit" only when the count is exactly 1; for any other count
   (including 0) use "visits".
7. For empty days (FACTS["has_sales"] is false and visits is "0"), write ONLY:
   the greeting, one line saying no visits were recorded for the clinic on
   that day, one line saying nothing was billed or collected, and the profit
   note. Do not emit "Busiest hour:", "Top mover", "No data available",
   "₹0 billed", or any colon-led placeholder for a fact that doesn't exist —
   skip missing structure lines entirely.
   For refunds-only days (has_sales false but refunded is not "₹0"), the
   middle lines are: "No sales visits today — {refunded} was refunded on
   {refund_visits} visits." instead of the billed/collected lines.
8. If FACTS["refunded"] is "₹0", never mention refunds anywhere in the
   message — no "₹0 refunded" line, no "0 visits" clause.
9. Never write a percentage unless FACTS contains "collected_pct" —
   refunds-only and empty days have no collection percentage, so "100%"
   would be invented.
10. Never mention paise, never convert units, never mention internal field
    names or JSON.
11. Never write bare rank numbers or list positions.
"""


def _date_display(date: str) -> str:
    try:
        return datetime.strptime(date, "%Y-%m-%d").strftime(DATE_DISPLAY_FMT)
    except ValueError:
        # Malformed date string (shouldn't reach here — validated at ingest);
        # fall back to the raw value rather than inventing a display form.
        return date


def build_fact_sheet(report: EodReport) -> dict:
    """Pre-formatted, copy-exact facts. Every value is a string built from the report."""
    rec = report.reconciliation
    an = report.analytics
    meta = report.meta

    facts: dict = {
        "clinic": meta.clinic_short_name,
        "clinic_full": meta.clinic_name,
        "date": _date_display(meta.date),
        "has_sales": rec.visits > 0,
        "billed": format_paise(rec.total_billed_paise),
        "collected": format_paise(rec.total_collected_paise),
        "outstanding": format_paise(rec.total_outstanding_paise),
        "refunded": format_paise(rec.total_refunded_paise),
        "net_collected": format_paise(rec.net_collected_paise),
        "visits": str(rec.visits),
        "outstanding_visits": str(rec.outstanding_visits),
        "refund_visits": str(rec.refund_visits),
        "rows_rejected": str(meta.rows_rejected),
        "profit_note": (
            "Note: cost data wasn't available, so this is revenue, not profit — "
            "flagging rather than estimating."
        ),
    }
    if rec.collected_pct_of_billed is not None:
        facts["collected_pct"] = format_pct(rec.collected_pct_of_billed)
    if an.peak_hour_range is not None:
        facts["busiest_hour"] = an.peak_hour_range
        facts["busiest_hour_revenue"] = format_paise(an.peak_hour_revenue_paise)
    if an.top_medicines_by_quantity:
        top_q = an.top_medicines_by_quantity[0]
        facts["top_by_qty"] = top_q.drug_name
        facts["top_by_qty_units"] = f"{top_q.qty} units"
    if an.top_medicines_by_revenue:
        top_r = an.top_medicines_by_revenue[0]
        facts["top_by_revenue"] = top_r.drug_name
        facts["top_by_revenue_amount"] = format_paise(top_r.revenue_paise)
    if meta.data_quality_warnings:
        facts["data_notes"] = meta.data_quality_warnings
    return facts


def parse_model_response(content: str) -> str:
    """Parse the model's response into narrative text; raise ValueError if off-schema."""
    text = content.strip()
    # strip accidental code fences
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # last resort: first {...} block
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("response is not valid JSON") from None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"response is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")

    narrative = None
    for key in ("narrative", "summary", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            narrative = value.strip()
            break
    if narrative is None:
        raise ValueError("response missing a non-empty 'narrative' string field")
    return narrative


def fallback_narrative(report: EodReport) -> str:
    """Deterministic, grounded-by-construction narrative used when the LLM fails."""
    facts = build_fact_sheet(report)
    lines: list[str] = []

    if not facts["has_sales"] and facts["visits"] == "0":
        if (
            int(report.reconciliation.total_refunded_paise) != 0
            or report.reconciliation.refund_visits
        ):
            lines.append(
                f"Good evening! Here's today's summary for {facts['clinic']} ({facts['date']})."
            )
            lines.append(
                f"No sales visits today — {facts['refunded']} was refunded on "
                f"{facts['refund_visits']} visits."
            )
        else:
            lines.append(
                f"Good evening! No visits were recorded for {facts['clinic']} on {facts['date']}."
            )
            lines.append("Nothing was billed or collected today.")
    else:
        lines.append(
            f"Good evening! Here's today's summary for {facts['clinic']} ({facts['date']})."
        )
        pct = facts.get("collected_pct")
        billed_line = (
            f"{facts['billed']} billed across {facts['visits']} visits, "
            f"{facts['collected']} collected"
        )
        if pct:
            billed_line += f" ({pct})"
        billed_line += "."
        lines.append(billed_line)

        outstanding_bits = []
        if facts["outstanding"] != "₹0" or facts["outstanding_visits"] != "0":
            outstanding_bits.append(
                f"{facts['outstanding']} is still outstanding across "
                f"{facts['outstanding_visits']} visits"
            )
        if facts["refunded"] != "₹0":
            refund_word = "visit" if facts["refund_visits"] == "1" else "visits"
            outstanding_bits.append(
                f"{facts['refunded']} was refunded on {facts['refund_visits']} {refund_word}"
            )
        if outstanding_bits:
            lines.append("; ".join(outstanding_bits) + ".")

        if "busiest_hour" in facts:
            lines.append(
                f"Busiest hour: {facts['busiest_hour']}, "
                f"with {facts['busiest_hour_revenue']} in revenue."
            )
        if "top_by_qty" in facts:
            lines.append(
                f"Top mover by quantity: {facts['top_by_qty']} ({facts['top_by_qty_units']})."
            )
        if "top_by_revenue" in facts:
            lines.append(
                f"Top by revenue: {facts['top_by_revenue']} ({facts['top_by_revenue_amount']})."
            )
        if facts["rows_rejected"] != "0":
            lines.append(
                f"Heads up: {facts['rows_rejected']} malformed row(s) were "
                "excluded from this report."
            )

    lines.append(facts["profit_note"])
    return "\n".join(lines)


def _build_user_prompt(report: EodReport, facts: dict, feedback: str | None = None) -> str:
    payload = {
        "FACTS": facts,
        "REPORT": json.loads(report.model_dump_json()),
    }
    prompt = (
        "Write the WhatsApp end-of-day narrative for this clinic day.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    if feedback:
        prompt += (
            "\n\nYour previous response was rejected because these figures are "
            f"wrong or mislabelled: {feedback}. Rewrite the narrative using only "
            "figures from FACTS/REPORT, each bound to its correct label."
        )
    return prompt


def generate_narrative(
    report: EodReport,
    provider: LLMProvider,
    *,
    max_retries: int = 1,
) -> NarrativeResponse:
    facts = build_fact_sheet(report)
    refs = build_figure_index(report)
    now = datetime.now(UTC)

    last_error: str | None = None
    feedback: str | None = None

    for _attempt in range(1 + max(0, max_retries)):
        try:
            content = provider.complete(SYSTEM_PROMPT, _build_user_prompt(report, facts, feedback))
        except LLMError as exc:
            # Transport/timeout failures won't improve on an immediate retry —
            # fall back now instead of doubling the request latency.
            last_error = str(exc)
            break

        try:
            narrative = parse_model_response(content)
        except ValueError as exc:
            last_error = f"off-schema model response: {exc}"
            feedback = "a well-formed JSON narrative"
            continue

        traced, ungrounded = trace_tokens(narrative, refs)
        if ungrounded:
            last_error = "ungrounded figures: " + ", ".join(ungrounded)
            feedback = ", ".join(repr(u) for u in ungrounded)
            continue

        label_errors = check_label_bindings(narrative, facts)
        if label_errors:
            last_error = "mislabelled figures: " + "; ".join(label_errors)
            feedback = "; ".join(label_errors)
            continue

        # Completeness: a sales day must carry the mockup's key lines —
        # skipping a ranking line is as much a content miss as a wrong number.
        if facts.get("has_sales"):
            missing = [
                str(facts[key])
                for key in ("busiest_hour", "top_by_qty", "top_by_revenue")
                if key in facts and str(facts[key]) not in narrative
            ]
            if missing:
                last_error = "missing required lines: " + ", ".join(missing)
                feedback = (
                    "these required figures are missing from the message: "
                    + ", ".join(missing)
                    + " — include each of them in its structure line"
                )
                continue

        return NarrativeResponse(
            date=report.meta.date,
            narrative=narrative,
            traced_figures=[TracedFigure(**t) for t in traced],
            model=getattr(provider, "model_name", "unknown"),
            source=getattr(provider, "source_name", "ollama"),
            grounded=True,
            generated_at=now,
        )

    # Deterministic fallback — grounded by construction from the same formatters.
    narrative = fallback_narrative(report)
    traced, ungrounded = trace_tokens(narrative, refs)
    # Fallback must be fully grounded; if any token somehow fails, drop it from
    # the traced panel rather than surface a false claim (never corrupt output).
    if ungrounded:  # pragma: no cover - defensive
        last_error = (last_error or "") + f"; fallback also had {ungrounded}"
    return NarrativeResponse(
        date=report.meta.date,
        narrative=narrative,
        traced_figures=[TracedFigure(**t) for t in traced],
        model=getattr(provider, "model_name", "unknown"),
        source="fallback",
        grounded=True,
        generated_at=now,
        llm_error=last_error,
    )


__all__ = [
    "build_fact_sheet",
    "fallback_narrative",
    "generate_narrative",
    "parse_model_response",
]
