"""Figure formatting, extraction and grounding.

Every monetary/numeric figure that may appear in a narrative is pre-formatted
from the deterministic report into an index of allowed figures. During
grounding we extract every number-like token from the model output and require
each one to resolve to a report field. Anything unresolvable fails grounding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .reconcile import hour_range
from .schemas import EodReport


def indian_number(n: int) -> str:
    """1234567 -> '12,34,567' (Indian digit grouping)."""
    neg = n < 0
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        out = ",".join(parts) + "," + last3
    return f"-{out}" if neg else out


def format_paise(paise: int) -> str:
    """500 -> '₹5', 350 -> '₹3.50', -49000 -> '-₹490'."""
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    rupees, rem = divmod(paise, 100)
    if rem:
        return f"{sign}₹{indian_number(rupees)}.{rem:02d}"
    return f"{sign}₹{indian_number(rupees)}"


def format_pct(pct: int) -> str:
    return f"{pct}%"


@dataclass(frozen=True)
class FigureRef:
    display: str  # exact token expected in the narrative
    field: str  # dotted path into the report
    label: str  # human label for the Traced Figures panel
    keywords: tuple[str, ...] = ()


def build_figure_index(report: EodReport) -> list[FigureRef]:
    """All figures the narrative is allowed to mention, priority-ordered.

    Earlier entries win when the same display string maps to multiple fields
    and no keyword disambiguates.
    """
    rec = report.reconciliation
    an = report.analytics
    meta = report.meta
    refs: list[FigureRef] = []

    def add(display: str, field: str, label: str, *keywords: str) -> None:
        refs.append(FigureRef(display=display, field=field, label=label, keywords=keywords))

    # Top-level reconciliation money (most cited)
    add(
        format_paise(rec.total_billed_paise),
        "reconciliation.total_billed_paise",
        "total billed",
        "billed",
        "invoice",
    )
    add(
        format_paise(rec.total_collected_paise),
        "reconciliation.total_collected_paise",
        "total collected",
        "collected",
        "cash",
        "taken",
    )
    add(
        format_paise(rec.total_outstanding_paise),
        "reconciliation.total_outstanding_paise",
        "outstanding",
        "outstanding",
        "pending",
        "owed",
        "due",
    )
    add(
        format_paise(rec.total_refunded_paise),
        "reconciliation.total_refunded_paise",
        "refunds",
        "refund",
        "returned",
        "back",
    )
    add(
        format_paise(rec.net_collected_paise),
        "reconciliation.net_collected_paise",
        "net collected",
        "net",
        "after refund",
    )
    add(
        format_paise(rec.total_discount_paise),
        "reconciliation.total_discount_paise",
        "discounts given",
        "discount",
    )

    if rec.collected_pct_of_billed is not None:
        add(
            format_pct(rec.collected_pct_of_billed),
            "reconciliation.collected_pct_of_billed",
            "% of billed collected",
            "percent",
            "of billed",
            "%",
        )

    # Visit counts
    add(str(rec.visits), "reconciliation.visits", "visits", "visit", "transaction")
    add(
        str(rec.outstanding_visits),
        "reconciliation.outstanding_visits",
        "visits with outstanding balance",
        "outstanding",
        "pending",
        "owe",
        "still",
    )
    add(
        str(rec.refund_visits),
        "reconciliation.refund_visits",
        "visits with refunds",
        "refund",
        "refunded",
        "returned",
    )

    # Per-mode breakdowns (after totals so totals win on ties)
    for mode, breakdown in rec.by_mode.items():
        add(
            format_paise(breakdown.billed_paise),
            f"reconciliation.by_mode.{mode}.billed_paise",
            f"{mode} billed",
            mode,
            "billed",
        )
        add(
            format_paise(breakdown.collected_paise),
            f"reconciliation.by_mode.{mode}.collected_paise",
            f"{mode} collected",
            mode,
            "collected",
        )
        add(
            format_paise(breakdown.outstanding_paise),
            f"reconciliation.by_mode.{mode}.outstanding_paise",
            f"{mode} outstanding",
            mode,
            "outstanding",
        )
        add(
            format_paise(breakdown.refunded_paise),
            f"reconciliation.by_mode.{mode}.refunded_paise",
            f"{mode} refunds",
            mode,
            "refund",
        )
        add(
            str(breakdown.visits),
            f"reconciliation.by_mode.{mode}.visits",
            f"{mode} visits",
            mode,
            "visit",
        )
        add(
            str(breakdown.outstanding_visits),
            f"reconciliation.by_mode.{mode}.outstanding_visits",
            f"{mode} pending visits",
            mode,
            "pending",
            "outstanding",
        )

    # Peak hour
    if an.peak_hour is not None and an.peak_hour_range:
        add(
            an.peak_hour_range,
            "analytics.peak_hour_range",
            "peak hour for revenue",
            "peak",
            "busiest",
            "hour",
            "top hour",
        )
        add(an.peak_hour_label, "analytics.peak_hour_label", "peak hour", "peak", "busiest", "hour")
        add(str(an.peak_hour), "analytics.peak_hour", "peak hour (24h)", "peak", "busiest", "hour")
        add(
            format_paise(an.peak_hour_revenue_paise),
            "analytics.peak_hour_revenue_paise",
            "peak hour revenue",
            "peak",
            "busiest",
            "hour",
            "revenue that hour",
        )

    # Per-hour revenue + hour numbers (only hours present in the data)
    for entry in an.revenue_by_hour:
        add(
            format_paise(entry.revenue_paise),
            f"analytics.revenue_by_hour[{entry.hour}].revenue_paise",
            f"revenue at {entry.hour_label}",
            entry.hour_label,
            "hour",
            entry.hour_range if hasattr(entry, "hour_range") else hour_range(entry.hour),
        )
        add(
            entry.hour_label,
            f"analytics.revenue_by_hour[{entry.hour}].hour_label",
            f"hour {entry.hour_label}",
            "hour",
            entry.hour_label,
        )
        add(
            str(entry.visits),
            f"analytics.revenue_by_hour[{entry.hour}].visits",
            f"visits at {entry.hour_label}",
            entry.hour_label,
            "hour",
            "visit",
        )

    # Medicine rankings — revenue ranking registered first so money displays
    # prefer analytics.top_medicines_by_revenue[*].revenue_paise.
    for med in an.top_medicines_by_revenue:
        add(
            format_paise(med.revenue_paise),
            f"analytics.top_medicines_by_revenue[{med.rank}].revenue_paise",
            f"{med.drug_name} revenue",
            med.drug_name.lower(),
            "revenue",
        )
        add(
            str(med.qty),
            f"analytics.top_medicines_by_revenue[{med.rank}].qty",
            f"{med.drug_name} quantity",
            med.drug_name.lower(),
            "units",
            "quantity",
        )
    for med in an.top_medicines_by_quantity:
        add(
            str(med.qty),
            f"analytics.top_medicines_by_quantity[{med.rank}].qty",
            f"{med.drug_name} quantity",
            med.drug_name.lower(),
            "units",
            "unit",
            "qty",
            "quantity",
            "boxes",
            "strips",
        )
        add(
            format_paise(med.revenue_paise),
            f"analytics.top_medicines_by_quantity[{med.rank}].revenue_paise",
            f"{med.drug_name} revenue",
            med.drug_name.lower(),
            "revenue",
        )

    # Meta: date parts and rejected rows
    # Malformed date strings fall through with no day/month/year figures —
    # the rest of the index still builds from reconciliation/analytics/meta.
    try:
        dt = datetime.strptime(meta.date, "%Y-%m-%d")
        add(str(dt.day), "meta.date", "day of month")
        add(str(dt.month), "meta.date", "month")
        add(str(dt.year), "meta.date", "year")
    except ValueError:
        pass
    add(
        str(meta.rows_rejected),
        "meta.rows_rejected",
        "rows rejected at ingest",
        "rejected",
        "malformed",
        "dropped",
        "skipped",
    )
    add(str(meta.rows_ingested), "meta.rows_ingested", "rows ingested", "row", "ingested", "record")

    return refs


# --- token extraction -------------------------------------------------------
MONEY_RE = re.compile(r"-?₹\s?\d[\d,]*(?:\.\d{1,2})?")
PCT_RE = re.compile(r"\d+(?:\.\d+)?%")
HOUR_RANGE_RE = re.compile(
    # en dash, em dash and the word "to" are all valid separators (RUF001 intentional)
    r"\b(\d{1,2})(?::00)?\s?(am|pm)\s*[-–—to]+\s*(\d{1,2})(?::00)?\s?(am|pm)\b",  # noqa: RUF001
    re.IGNORECASE,
)
HOUR_RE = re.compile(r"\b(\d{1,2})\s?(am|pm)\b", re.IGNORECASE)
INT_RE = re.compile(r"(?<![\w.,%])(\d{1,3}(?:,\d{2,3})+|\d+)(?![\w.%])")


@dataclass
class ExtractedToken:
    raw: str
    normalized: str  # canonical lookup key ("₹3,190", "99%", "18", "1pm-2pm")
    start: int
    end: int


def extract_tokens(text: str) -> list[ExtractedToken]:
    tokens: list[ExtractedToken] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in occupied)

    for rx, norm in (
        (PCT_RE, lambda m: m.group(0)),
        (MONEY_RE, lambda m: m.group(0).replace(" ", "")),
        (
            HOUR_RANGE_RE,
            lambda m: f"{m.group(1)}{m.group(2).lower()}-{m.group(3)}{m.group(4).lower()}",
        ),
        (HOUR_RE, lambda m: f"{m.group(1)}{m.group(2).lower()}"),
    ):
        for m in rx.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            occupied.append((m.start(), m.end()))
            tokens.append(ExtractedToken(m.group(0), norm(m), m.start(), m.end()))

    for m in INT_RE.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        occupied.append((m.start(), m.end()))
        raw = m.group(1)
        canonical = str(int(raw.replace(",", "")))
        tokens.append(ExtractedToken(raw, canonical, m.start(), m.end()))

    tokens.sort(key=lambda t: t.start)
    return tokens


def _context(text: str, token: ExtractedToken, radius: int = 48) -> str:
    return text[max(0, token.start - radius) : min(len(text), token.end + radius)].lower()


def trace_tokens(text: str, refs: list[FigureRef]) -> tuple[list[dict], list[str]]:
    """Map every extracted token to a report field.

    Returns (traced, ungrounded). Ungrounded = invented figures.
    """
    # display -> refs (priority order preserved)
    by_display: dict[str, list[FigureRef]] = {}
    for ref in refs:
        by_display.setdefault(ref.display, []).append(ref)
        # money tokens may be written with/without the rupee sign spacing
        if ref.display.startswith("₹"):
            by_display.setdefault(ref.display.replace("₹", "₹ "), []).append(ref)
        # canonical integer form of money: "₹3,190" also matches bare 3190? no —
        # bare paise ints are intentionally NOT auto-allowed (narrative uses ₹).

    traced: list[dict] = []
    ungrounded: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()

    for token in extract_tokens(text):
        candidates = by_display.get(token.normalized, [])
        if not candidates:
            # allow bare integer matches for plain-int displays already handled;
            # also try stripping commas from money-like displays' numeric part
            ungrounded.append(token.raw)
            continue

        ctx = _context(text, token)
        chosen = None
        # 1) keyword match against surrounding context
        for ref in candidates:
            if any(k in ctx for k in ref.keywords):
                chosen = ref
                break
        # 2) single candidate
        if chosen is None and len(candidates) == 1:
            chosen = candidates[0]
        # 3) deterministic priority (first registered)
        if chosen is None:
            chosen = candidates[0]

        key = (token.normalized, chosen.field)
        if key not in seen_pairs:
            seen_pairs.add(key)
            traced.append(
                {
                    "figure": token.raw.strip(),
                    "report_field": chosen.field,
                    "report_field_label": chosen.label,
                }
            )

    return traced, ungrounded


# --- label ↔ amount consistency ---------------------------------------------
# A figure may exist in the report yet be bound to the wrong word ("Billed
# ₹3,172" when billed is ₹3,190 and ₹3,172 is collected). After token
# grounding, scan for money/pct tokens whose *nearest* label word in the same
# sentence disagrees with the FACTS value for that label.

LABELLED_FIELDS: dict[str, str] = {
    "billed": "billed",
    "collected": "collected",
    "outstanding": "outstanding",
    "refunded": "refunded",
    "refunds": "refunded",
    "net": "net_collected",
    "discount": "discounts",
}

_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]")
_LABEL_WORD_RE = re.compile(
    r"\b(billed|collected|outstanding|refunded|refunds|net|discount)\b",
    re.IGNORECASE,
)
_PCT_OF_BILLED_RE = re.compile(r"(\d+(?:\.\d+)?%)\s+of\s+billed\b", re.IGNORECASE)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_SPLIT_RE.finditer(text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def check_label_bindings(text: str, facts: dict) -> list[str]:
    """Return errors for money/pct figures bound to the wrong FACTS label.

    Only flags tokens whose display equals a known FACTS money/pct value —
    mode-level or other derived figures are left to token grounding.
    """
    errors: list[str] = []
    money_facts = {
        key: facts[key]
        for key in (
            "billed",
            "collected",
            "outstanding",
            "refunded",
            "net_collected",
        )
        if key in facts
    }
    # display -> canonical fact key (first wins; values are unique in practice)
    by_display: dict[str, str] = {}
    for key, display in money_facts.items():
        by_display.setdefault(display, key)

    for token in extract_tokens(text):
        fact_key = by_display.get(token.normalized)
        if fact_key is None:
            continue
        # locate the sentence containing this token
        containing = next(
            (s for s in _sentence_spans(text) if s[0] <= token.start < s[1]),
            None,
        )
        if containing is None:
            continue
        sent_start, sent_end = containing
        sent = text[sent_start:sent_end]
        # nearest labelled word to this token within the sentence
        best_label: str | None = None
        best_dist = len(sent) + 1
        for m in _LABEL_WORD_RE.finditer(sent):
            abs_start = sent_start + m.start()
            abs_end = sent_start + m.end()
            if abs_end <= token.start:
                dist = token.start - abs_end
            elif abs_start >= token.end:
                dist = abs_start - token.end
            else:
                continue
            if dist < best_dist:
                best_dist = dist
                best_label = m.group(1).lower()
        if best_label is None:
            continue
        expected_key = LABELLED_FIELDS.get(best_label)
        if expected_key is None or expected_key == fact_key:
            continue
        expected_display = money_facts.get(expected_key)
        if expected_display is None:
            continue
        errors.append(
            f'figure {token.raw.strip()} is labelled "{best_label}" '
            f"but {best_label} is {expected_display}; relabel it or use {expected_display}"
        )

    # "99% of billed" without a nearby "collected" — pct must attach to collected
    collected_pct = facts.get("collected_pct")
    if collected_pct:
        for m in _PCT_OF_BILLED_RE.finditer(text):
            if m.group(1) != collected_pct:
                continue
            window = text[max(0, m.start() - 40) : m.end() + 40].lower()
            if "collect" not in window:
                errors.append(
                    f'{collected_pct} is written as "of billed" without "collected"; '
                    f"the percentage belongs next to collected"
                )
    return errors
