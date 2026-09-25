"""Pydantic models: validated billing rows and the deterministic EOD report."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PaymentMode = Literal["cash", "card", "upi"]


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    drug_name: str = Field(min_length=1)
    qty: int = Field(gt=0)
    unit_price_paise: int = Field(ge=0)


class BillingRow(BaseModel):
    """A fully validated visit record. All money is integer paise."""

    model_config = ConfigDict(extra="ignore")

    clinic_id: str = Field(min_length=1)
    visit_id: str = Field(min_length=1)
    timestamp: datetime
    doctor_id: str = Field(min_length=1)
    line_items: list[LineItem] = Field(min_length=1)
    payment_mode: PaymentMode
    amount_paid_paise: int
    discount_paise: int = Field(ge=0)
    is_refund: bool


class RowError(BaseModel):
    index: int | None = None
    visit_id: str | None = None
    field: str | None = None
    error: str


class IngestResponse(BaseModel):
    date: str
    clinic_id: str | None
    accepted: int
    rejected: int
    strict: bool
    errors: list[RowError] = []
    message: str


class ReportMeta(BaseModel):
    clinic_id: str
    clinic_name: str
    clinic_short_name: str
    clinic_subtitle: str
    date: str
    generated_at: datetime
    rows_ingested: int
    rows_rejected: int
    data_quality_warnings: list[str] = []


class ModeBreakdown(BaseModel):
    billed_paise: int
    collected_paise: int
    outstanding_paise: int
    discount_paise: int
    refunded_paise: int
    visits: int
    outstanding_visits: int


class Reconciliation(BaseModel):
    total_billed_paise: int
    total_collected_paise: int
    total_outstanding_paise: int
    total_discount_paise: int
    total_refunded_paise: int
    net_collected_paise: int
    collected_pct_of_billed: int | None
    visits: int
    outstanding_visits: int
    refund_visits: int
    by_mode: dict[str, ModeBreakdown]


class HourRevenue(BaseModel):
    hour: int
    hour_label: str
    revenue_paise: int
    visits: int


class MedicineQuantity(BaseModel):
    rank: int
    drug_name: str
    qty: int
    revenue_paise: int


class MedicineRevenue(BaseModel):
    rank: int
    drug_name: str
    revenue_paise: int
    qty: int


class Analytics(BaseModel):
    revenue_by_hour: list[HourRevenue]
    peak_hour: int | None
    peak_hour_label: str | None
    peak_hour_range: str | None
    peak_hour_revenue_paise: int
    top_medicines_by_quantity: list[MedicineQuantity]
    top_medicines_by_revenue: list[MedicineRevenue]


class EodReport(BaseModel):
    meta: ReportMeta
    reconciliation: Reconciliation
    analytics: Analytics


class TracedFigure(BaseModel):
    figure: str
    report_field: str
    report_field_label: str


class NarrativeResponse(BaseModel):
    date: str
    narrative: str
    traced_figures: list[TracedFigure]
    model: str
    source: Literal["openrouter", "ollama", "fallback"]
    grounded: bool
    generated_at: datetime
    llm_error: str | None = None
