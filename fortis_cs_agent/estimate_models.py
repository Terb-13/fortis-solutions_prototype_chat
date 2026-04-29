"""
Pydantic models for packaging estimates.

Designed for realistic corrugated / retail packaging quoting fields.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, field_validator


class ProductType(str, Enum):
    RSC = "rsc_corrugated_box"
    TELESCOPING = "telescoping_box"
    MAILER = "mailer"
    FOLDING_CARTON = "folding_carton"
    PARTITION = "partition"
    TRAY_SLEEVE = "tray_sleeve_shrink_combo"
    BULK_OTHER = "bulk_other"


class EstimateLineItem(BaseModel):
    """One SKU / product line on the estimate."""

    name: str = Field(..., min_length=1, description='Marketing or internal SKU name, e.g. "12oz bottle shipper"')
    product_type: ProductType
    quantity: int = Field(..., ge=1, description="Units for this line (e.g. boxes or cartons)")
    length_in: Decimal | None = Field(None, description="Interior L in inches")
    width_in: Decimal | None = Field(None, description="Interior W in inches")
    depth_in: Decimal | None = Field(None, description="Interior D in inches")
    board: str | None = Field(
        None,
        description='e.g. "32ECT C-flute white kraft"',
    )
    print_specs: str | None = Field(
        None,
        description="Flexo litho UV, spot colors, flood coat, varnish, etc.",
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


class EstimateCustomer(BaseModel):
    """Bill-to / ship attention block."""

    company: str | None = None
    contact_name: str
    email: str | None = Field(None, description="Email for revisions and approvals")
    phone: str | None = None


class EstimateRequest(BaseModel):
    """
    Full payload for generating a packaging estimate (tool + REST).

    Stored fields support both chat tool calls and POST /create-estimate.
    """

    estimate_id: UUID = Field(default_factory=uuid4)
    estimate_date: date = Field(default_factory=date.today)
    customer: EstimateCustomer
    line_items: list[EstimateLineItem] = Field(..., min_length=1)

    turnaround: Literal["rush", "standard", "economy"] = Field(
        "standard",
        description="Fulfillment urgency for planning and buffer",
    )
    ship_to_region: str | None = Field(
        None,
        description="Region or city/state for freight assumptions",
    )
    internal_notes: str | None = Field(
        None,
        description="Ops / CS notes visible on internal copy only when printed.",
    )

    def model_summary(self) -> str:
        """Short human-readable summary for chat logs."""
        kinds = ", ".join(li.product_type.value for li in self.line_items)
        return (
            f"Estimate {self.estimate_id} for {self.customer.contact_name}: "
            f"{len(self.line_items)} line(s) [{kinds}], turnaround={self.turnaround}"
        )


class EstimatedPricing(BaseModel):
    """
    Lightweight pricing output (transparent breakdown for PDF + API).

    In production these values would come from Fortis quoting rules / ERP integration.
    For this backend we derive structured placeholders from inputs so PDFs remain useful.
    """

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
            f"Subtotal (material + run): ${self.subtotal:.2f}",
            f"Setup / tooling: ${self.setup_fees:.2f}",
            f"Freight (indicative): ${self.freight_estimate:.2f}",
            f"Turnaround multiplier: x{self.rush_multiplier}",
            f"Total: ${self.total:.2f} {self.currency}",
        ]
        if self.assumptions:
            lines.append("Assumptions: " + "; ".join(self.assumptions))
        return "\n".join(lines)


class CreateEstimateResult(BaseModel):
    """Return value from create_estimate tool and /create-estimate."""

    estimate: EstimateRequest
    pricing: EstimatedPricing
    pdf_base64: str | None = Field(
        None,
        description="Optional base64 PDF when caller requests inline.",
    )
    message: str = Field(..., description="End-user facing summary sentence(s).")

    def to_tool_dict(self) -> dict[str, Any]:
        """Safe dict for Grok tool result (omit large blobs if needed)."""
        return {
            "estimate_id": str(self.estimate.estimate_id),
            "summary": self.estimate.model_summary(),
            "pricing_explanation": self.pricing.explanation,
            "message": self.message,
        }
