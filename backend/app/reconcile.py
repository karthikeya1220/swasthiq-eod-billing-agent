"""Deterministic EOD reconciliation and analytics. Pure functions, integer paise.

Definitions (documented in README):
- billed      = sum(qty * unit_price_paise - discount_paise) over sale rows
- collected   = sum(amount_paid_paise) over sale rows (gross collections)
- outstanding = billed - collected  (equals sum of row-level arrears)
- refunded    = sum(abs(amount_paid_paise)) over refund rows
- net         = collected - refunded
- hour revenue & medicine rankings consider sale rows only (never refunds)
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from datetime import UTC, datetime

from .config import PAYMENT_MODES, clinic_display
from .schemas import (
    Analytics,
    BillingRow,
    EodReport,
    HourRevenue,
    MedicineQuantity,
    MedicineRevenue,
    ModeBreakdown,
    Reconciliation,
    ReportMeta,
)


def hour_label(hour: int) -> str:
    """0 -> '12am', 13 -> '1pm', 12 -> '12pm'."""
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12
    if display == 0:
        display = 12
    return f"{display}{suffix}"


def hour_range(hour: int) -> str:
    return f"{hour_label(hour)}-{hour_label((hour + 1) % 24)}"


def row_gross_total(row: BillingRow) -> int:
    return sum(item.qty * item.unit_price_paise for item in row.line_items)


def row_billed(row: BillingRow) -> int:
    """Invoiced amount after discount (sale rows only)."""
    return row_gross_total(row) - row.discount_paise


def row_arrears(row: BillingRow) -> int:
    """Still-owed amount for a sale row after discount."""
    return max(0, row_billed(row) - row.amount_paid_paise)


def _similar_drug_names(names: list[str]) -> list[str]:
    """Flag probable data-entry typos (e.g. PARACETMOL vs PARACETAMOL)."""
    warnings: list[str] = []
    unique = sorted(set(names))
    for i, a in enumerate(unique):
        for b in unique[i + 1 :]:
            ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
            if ratio >= 0.9:
                warnings.append(
                    f"Drug names {a!r} and {b!r} look like variants of the same medicine "
                    f"— ranked separately (exact-match, deterministic). Verify spelling."
                )
    return warnings


def build_report(
    rows: list[BillingRow],
    date: str,
    *,
    rows_rejected: int = 0,
    clinic_id: str | None = None,
    generated_at: datetime | None = None,
) -> EodReport:
    sales = [r for r in rows if not r.is_refund]
    refunds = [r for r in rows if r.is_refund]

    # Prefer explicit clinic (e.g. day record for an empty log), then rows.
    if clinic_id is None:
        clinic_id = next((r.clinic_id for r in rows), None)
    if clinic_id is None:
        clinic_id = "unknown"

    mode_acc = {
        mode: {
            "billed": 0,
            "collected": 0,
            "discount": 0,
            "refunded": 0,
            "visits": 0,
            "outstanding_visits": 0,
        }
        for mode in PAYMENT_MODES
    }

    total_billed = total_collected = total_discount = total_refunded = 0

    for row in sales:
        billed = row_billed(row)
        acc = mode_acc[row.payment_mode]
        acc["billed"] += billed
        acc["collected"] += row.amount_paid_paise
        acc["discount"] += row.discount_paise
        acc["visits"] += 1
        if row_arrears(row) > 0:
            acc["outstanding_visits"] += 1
        total_billed += billed
        total_collected += row.amount_paid_paise
        total_discount += row.discount_paise

    for row in refunds:
        refunded = abs(row.amount_paid_paise)
        mode_acc[row.payment_mode]["refunded"] += refunded
        total_refunded += refunded

    by_mode: dict[str, ModeBreakdown] = {}
    for mode in PAYMENT_MODES:
        acc = mode_acc[mode]
        by_mode[mode] = ModeBreakdown(
            billed_paise=acc["billed"],
            collected_paise=acc["collected"],
            outstanding_paise=acc["billed"] - acc["collected"],
            discount_paise=acc["discount"],
            refunded_paise=acc["refunded"],
            visits=acc["visits"],
            outstanding_visits=acc["outstanding_visits"],
        )

    outstanding = total_billed - total_collected
    pct = round(total_collected * 100 / total_billed) if total_billed > 0 else None

    reconciliation = Reconciliation(
        total_billed_paise=total_billed,
        total_collected_paise=total_collected,
        total_outstanding_paise=outstanding,
        total_discount_paise=total_discount,
        total_refunded_paise=total_refunded,
        net_collected_paise=total_collected - total_refunded,
        collected_pct_of_billed=pct,
        visits=len(sales),
        outstanding_visits=sum(1 for r in sales if row_arrears(r) > 0),
        refund_visits=len(refunds),
        by_mode=by_mode,
    )

    # --- revenue by hour-of-day (sale rows, invoiced amount after discount) ---
    hour_billed: dict[int, int] = defaultdict(int)
    hour_visits: dict[int, int] = defaultdict(int)
    for row in sales:
        h = row.timestamp.hour
        hour_billed[h] += row_billed(row)
        hour_visits[h] += 1

    revenue_by_hour = [
        HourRevenue(
            hour=h,
            hour_label=hour_label(h),
            revenue_paise=hour_billed[h],
            visits=hour_visits[h],
        )
        for h in sorted(hour_billed)
    ]

    if revenue_by_hour:
        # Deterministic tie-break: highest revenue, then earliest hour.
        peak = sorted(revenue_by_hour, key=lambda e: (-e.revenue_paise, e.hour))[0]
        peak_hour = peak.hour
        peak_label = peak.hour_label
        peak_range = hour_range(peak.hour)
        peak_revenue = peak.revenue_paise
    else:
        peak_hour = None
        peak_label = None
        peak_range = None
        peak_revenue = 0

    # --- medicine rankings (sale rows only; two distinct orderings) ---
    qty_agg: dict[str, dict[str, int]] = defaultdict(lambda: {"qty": 0, "revenue": 0})
    for row in sales:
        for item in row.line_items:
            agg = qty_agg[item.drug_name]
            agg["qty"] += item.qty
            agg["revenue"] += item.qty * item.unit_price_paise

    by_quantity = sorted(qty_agg.items(), key=lambda kv: (-kv[1]["qty"], kv[0]))
    by_revenue = sorted(qty_agg.items(), key=lambda kv: (-kv[1]["revenue"], kv[0]))

    top_qty = [
        MedicineQuantity(
            rank=i,
            drug_name=name,
            qty=stats["qty"],
            revenue_paise=stats["revenue"],
        )
        for i, (name, stats) in enumerate(by_quantity, start=1)
    ]
    top_rev = [
        MedicineRevenue(
            rank=i,
            drug_name=name,
            revenue_paise=stats["revenue"],
            qty=stats["qty"],
        )
        for i, (name, stats) in enumerate(by_revenue, start=1)
    ]

    analytics = Analytics(
        revenue_by_hour=revenue_by_hour,
        peak_hour=peak_hour,
        peak_hour_label=peak_label,
        peak_hour_range=peak_range,
        peak_hour_revenue_paise=peak_revenue,
        top_medicines_by_quantity=top_qty,
        top_medicines_by_revenue=top_rev,
    )

    warnings = _similar_drug_names([i.drug_name for r in sales for i in r.line_items])
    if rows_rejected:
        warnings.append(
            f"{rows_rejected} malformed row(s) were rejected at ingest and "
            "are excluded from this report."
        )
    if not rows:
        warnings.append("No visits recorded for this date.")
    elif not sales and refunds:
        warnings.append(
            "Refunds-only day: no sale visits, so billed/collected are ₹0 and "
            "all money movement is outgoing refunds."
        )

    meta = ReportMeta(
        **clinic_display(clinic_id),
        date=date,
        generated_at=generated_at or datetime.now(UTC),
        rows_ingested=len(rows),
        rows_rejected=rows_rejected,
        data_quality_warnings=warnings,
    )

    return EodReport(meta=meta, reconciliation=reconciliation, analytics=analytics)
