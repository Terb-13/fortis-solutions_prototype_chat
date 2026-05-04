"""
Pydantic models for packaging estimates (labels, sleeves, corrugated, RFID, flexible, etc.).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class ProductType(str, Enum):
    PRESSURE_SENSITIVE_LABELS = "pressure_sensitive_labels"
    RFID_LABELS_OR_INLAYS = "rfid_labels_or_inlays"
    SHRINK_SLEEVES = "shrink_sleeves"
    FLEXIBLE_PACKAGING = "flexible_packaging"
    FOLDING_CARTON = "folding_carton"
    RSC = "rsc_corrugated_box"
    TELESCOPING = "telescoping_box"
    MAILER = "mailer"
    PARTITION = "partition"
    TRAY_SLEEVE_COMBO = "tray_sleeve_shrink_combo"
    BULK_OTHER = "bulk_other"


class EstimateLineItem(BaseModel):
    """One SKU / product line (normalized internal shape)."""

    name: str = Field(..., min_length=1, description='Line or SKU name')
    product_type: ProductType
    quantity: int = Field(..., ge=1, le=50_000_000, description="Units to quote")
    length_in: Decimal | None = Field(
        None,
        description="Primary depth / label height / open dimension (in). Maps from tool 'height' when applicable.",
    )
    width_in: Decimal | None = Field(
        None,
        description="Width across web / panel (in). Maps from tool 'width'.",
    )
    depth_in: Decimal | None = Field(None, description="Third dimension where applicable (in)")
    board: str | None = Field(None, description="Substrate / material (e.g. BOPP, 32ECT kraft)")
    finish: str | None = Field(None, description="Varnish, lamination, emboss, etc.")
    colors: str | None = Field(None, description="Ink stations / color count / brand specs")
    print_specs: str | None = Field(
        None,
        description="Combined converting notes (auto-built from finish/colors if empty)",
    )
    target_turnaround_days: int | None = Field(
        None,
        ge=1,
        le=365,
        description="Customer-requested production window in days (planning)",
    )
    notes: str | None = Field(None, description="Line-specific notes")

    @field_validator("length_in", "width_in", "depth_in", mode="before")
    @classmethod
    def _non_negative_dim(cls, v: object) -> object:
        if v is None:
            return v
        d = Decimal(str(v))
        if d < 0:
            raise ValueError("Dimensions must be non-negative")
        return d

    @model_validator(mode="after")
    def _merge_print_specs(self) -> EstimateLineItem:
        parts: list[str] = []
        if self.finish:
            parts.append(f"Finish: {self.finish}")
        if self.colors:
            parts.append(f"Colors: {self.colors}")
        if self.target_turnaround_days is not None:
            parts.append(f"Target ~{self.target_turnaround_days}d production")
        merged = "; ".join(parts)
        if merged and not (self.print_specs or "").strip():
            self.print_specs = merged
        elif merged and self.print_specs and merged not in self.print_specs:
            self.print_specs = f"{self.print_specs}; {merged}"
        return self


class EstimateCustomer(BaseModel):
    company: str | None = None
    contact_name: str
    email: str | None = None
    phone: str | None = None


class EstimateRequest(BaseModel):
    estimate_id: UUID = Field(default_factory=uuid4)
    estimate_date: date = Field(default_factory=date.today)
    quote_valid_days: int = Field(30, ge=7, le=90, description="Quote validity window for PDF")
    customer: EstimateCustomer
    line_items: list[EstimateLineItem] = Field(..., min_length=1)

    turnaround: Literal["rush", "standard", "economy"] = Field(
        "standard",
        description="Fulfillment urgency band derived from tool urgency / lead times",
    )
    ship_to_region: str | None = None
    customer_notes: str | None = Field(
        None,
        description="Customer-visible notes (not restricted internal block)",
    )
    internal_notes: str | None = Field(
        None,
        description="Internal-only on PDF when marked internal",
    )

    def model_summary(self) -> str:
        kinds = ", ".join(li.product_type.value for li in self.line_items)
        return (
            f"Estimate {self.estimate_id} for {self.customer.contact_name}: "
            f"{len(self.line_items)} line(s) [{kinds}], turnaround={self.turnaround}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def valid_until(self) -> date:
        return self.estimate_date + timedelta(days=self.quote_valid_days)


class EstimatedPricing(BaseModel):
    subtotal: Decimal
    setup_fees: Decimal = Decimal("0")
    freight_estimate: Decimal = Decimal("0")
    rush_multiplier: Decimal = Decimal("1.0")
    total: Decimal
    currency: Literal["USD"] = "USD"
    assumptions: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def explanation(self) -> str:
        lines = [
            f"Subtotal (extended): ${self.subtotal:.2f}",
            f"Setup / tooling / program: ${self.setup_fees:.2f}",
            f"Freight (indicative): ${self.freight_estimate:.2f}",
            f"Turnaround multiplier: x{self.rush_multiplier}",
            f"Total (indicative): ${self.total:.2f} {self.currency}",
        ]
        if self.assumptions:
            lines.append("Assumptions: " + "; ".join(self.assumptions))
        return "\n".join(lines)


class CreateEstimateResult(BaseModel):
    estimate: EstimateRequest
    pricing: EstimatedPricing
    pdf_base64: str | None = None
    pdf_link: str | None = None
    message: str = Field(...)

    def to_tool_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "estimate_id": str(self.estimate.estimate_id),
            "summary": self.estimate.model_summary(),
            "pricing_explanation": self.pricing.explanation,
            "message": self.message,
            "valid_until": self.estimate.valid_until.isoformat(),
        }
        if self.pdf_link:
            out["pdf_link"] = self.pdf_link
        return out


# --- Tool-facing input (v2) ----------------------------------------------------------------


class EstimateProductLineInput(BaseModel):
    """One line as passed from the generate_estimate_pdf tool (LLM-friendly)."""

    product_type: ProductType
    quantity: int = Field(..., ge=1, le=50_000_000)
    width: Decimal | None = Field(
        None,
        ge=0,
        description="Width in inches (label web / carton panel). Optional; defaults inferred for planning.",
    )
    height: Decimal | None = Field(
        None,
        ge=0,
        description="Height or second primary dimension in inches.",
    )
    material: str | None = Field(None, max_length=500, description="Face stock, film, board, flute spec")
    finish: str | None = Field(None, max_length=500)
    colors: str | None = Field(None, max_length=500, description="e.g. 4/C process + PMS 186")
    turnaround_days: int | None = Field(
        None,
        ge=1,
        le=365,
        description="Requested production-ready days for this line",
    )
    name: str | None = Field(None, max_length=200, description="Optional SKU / description label")

    @field_validator("width", "height", mode="before")
    @classmethod
    def _coerce_dim(cls, v: object) -> object:
        if v is None or v == "":
            return None
        return v


class CreateEstimateToolPayload(BaseModel):
    """
    Payload for the Grok tool generate_estimate_pdf (customer_name + products[]).

    Legacy REST/chat may still send { customer, line_items, turnaround }.
    """

    customer_name: str = Field(..., min_length=1, max_length=240)
    company: str | None = Field(None, max_length=240)
    email: str | None = Field(None, max_length=240)
    phone: str | None = Field(None, max_length=64)
    products: list[EstimateProductLineInput] = Field(..., min_length=1, max_length=100)
    notes: str | None = Field(None, max_length=8000)
    urgency: Literal["low", "standard", "high", "critical"] = "standard"
    ship_to_region: str | None = Field(None, max_length=240)
    internal_notes: str | None = Field(None, max_length=8000)

    def to_estimate_request(self) -> EstimateRequest:
        lines: list[EstimateLineItem] = []
        for i, p in enumerate(self.products):
            nm = p.name or f"Line {i + 1} ({p.product_type.value.replace('_', ' ')})"
            lines.append(
                EstimateLineItem(
                    name=nm,
                    product_type=p.product_type,
                    quantity=p.quantity,
                    width_in=p.width,
                    length_in=p.height,
                    depth_in=None,
                    board=(p.material or "").strip() or None,
                    finish=(p.finish or "").strip() or None,
                    colors=(p.colors or "").strip() or None,
                    print_specs=None,
                    target_turnaround_days=p.turnaround_days,
                    notes=None,
                )
            )

        turnaround: Literal["rush", "standard", "economy"] = "standard"
        if self.urgency in ("high", "critical"):
            turnaround = "rush"
        elif self.urgency == "low":
            turnaround = "economy"

        min_days = min((x.turnaround_days for x in self.products if x.turnaround_days is not None), default=None)
        if min_days is not None and min_days <= 7:
            turnaround = "rush"
        if min_days is not None and min_days >= 45 and self.urgency not in ("high", "critical"):
            turnaround = "economy"

        return EstimateRequest(
            customer=EstimateCustomer(
                contact_name=self.customer_name.strip(),
                company=self.company.strip() if self.company else None,
                email=self.email.strip() if self.email else None,
                phone=self.phone.strip() if self.phone else None,
            ),
            line_items=lines,
            turnaround=turnaround,
            ship_to_region=self.ship_to_region.strip() if self.ship_to_region else None,
            customer_notes=self.notes.strip() if self.notes else None,
            internal_notes=self.internal_notes.strip() if self.internal_notes else None,
        )


def normalize_estimate_payload(payload: dict[str, Any]) -> EstimateRequest:
    """
    Normalize REST / tool payloads.

    - New shape: customer_name + products
    - Legacy: customer + line_items (+ optional customer_notes via notes at root)
    """
    if "customer_name" in payload and "products" in payload:
        return CreateEstimateToolPayload.model_validate(payload).to_estimate_request()
    return EstimateRequest.model_validate(payload)
