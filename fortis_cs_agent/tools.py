"""
Agent tools — packaging estimates and integrations.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from fortis_cs_agent.estimate_models import (
    EstimateLineItem,
    EstimateRequest,
    EstimatedPricing,
    ProductType,
    CreateEstimateResult,
)
from fortis_cs_agent.estimate_pdf import build_estimate_pdf_bytes, pdf_to_base64

logger = logging.getLogger(__name__)

# OpenAI-compatible tool schema exposed to Grok
CREATE_ESTIMATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_estimate",
        "description": (
            "Generate a structured corrugated / retail packaging estimate with indicative pricing "
            "and PDF output. Call when the user asks for pricing, quotes, BOM-style packaging scopes, "
            "or downloadable estimates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string"},
                        "contact_name": {"type": "string"},
                        "email": {"type": "string"},
                        "phone": {"type": "string"},
                    },
                    "required": ["contact_name"],
                },
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "product_type": {
                                "type": "string",
                                "enum": [e.value for e in ProductType],
                            },
                            "quantity": {"type": "integer", "minimum": 1},
                            "length_in": {"type": "number"},
                            "width_in": {"type": "number"},
                            "depth_in": {"type": "number"},
                            "board": {"type": "string"},
                            "print_specs": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                        "required": ["name", "product_type", "quantity"],
                    },
                    "minItems": 1,
                },
                "turnaround": {
                    "type": "string",
                    "enum": ["rush", "standard", "economy"],
                },
                "ship_to_region": {"type": "string"},
                "internal_notes": {"type": "string"},
            },
            "required": ["customer", "line_items"],
        },
    },
}


def _line_area_sf(li: EstimateLineItem) -> Decimal:
    """Rough surface area in sq ft for placeholder pricing (6 faces of a box)."""
    if li.length_in and li.width_in and li.depth_in:
        l, w, d = li.length_in, li.width_in, li.depth_in
        # Sum of face areas (in^2) / 144
        sides = Decimal("2") * (l * w + w * d + l * d)
        return (sides / Decimal("144")).quantize(Decimal("0.01"))
    return Decimal("2.5")  # default small box assumption


def _type_multiplier(pt: ProductType) -> Decimal:
    return {
        ProductType.RSC: Decimal("1.0"),
        ProductType.TELESCOPING: Decimal("1.12"),
        ProductType.MAILER: Decimal("0.95"),
        ProductType.FOLDING_CARTON: Decimal("0.88"),
        ProductType.PARTITION: Decimal("0.75"),
        ProductType.TRAY_SLEEVE: Decimal("1.2"),
        ProductType.BULK_OTHER: Decimal("1.05"),
    }.get(pt, Decimal("1.0"))


def _turnaround_multiplier(turnaround: str) -> Decimal:
    return {
        "rush": Decimal("1.25"),
        "standard": Decimal("1.0"),
        "economy": Decimal("0.95"),
    }.get(turnaround, Decimal("1.0"))


def compute_indicative_pricing(req: EstimateRequest) -> EstimatedPricing:
    """
    Deterministic placeholder math so PDFs and APIs stay consistent.

    Replace with ERP / rules engine in production.
    """
    base_rate = Decimal("4.85")  # blended $/sq ft planning rate
    subtotal = Decimal("0")
    assumptions: list[str] = [
        "Indicative blended rate for planning; final board, MOQ, and freight may differ.",
        "Tooling may apply for new die / print cylinders.",
    ]

    for li in req.line_items:
        sf = _line_area_sf(li)
        line_total = sf * base_rate * Decimal(li.quantity) * _type_multiplier(li.product_type)
        subtotal += line_total.quantize(Decimal("0.01"))

    setup = Decimal("250") if any(li.print_specs for li in req.line_items) else Decimal("125")
    freight = Decimal("180") if req.ship_to_region else Decimal("95")
    rush = _turnaround_multiplier(req.turnaround)

    total = (subtotal + setup + freight) * rush
    total = total.quantize(Decimal("0.01"))
    subtotal = subtotal.quantize(Decimal("0.01"))

    return EstimatedPricing(
        subtotal=subtotal,
        setup_fees=setup,
        freight_estimate=freight,
        rush_multiplier=rush,
        total=total,
        assumptions=assumptions,
    )


def _money(amount: Decimal) -> str:
    return f"${amount.quantize(Decimal('0.01')):,.2f}"


def create_estimate(
    *,
    payload: dict[str, Any],
    include_pdf_base64: bool = False,
) -> CreateEstimateResult:
    """
    Parse tool arguments, compute pricing, and render PDF.

    `payload` matches CREATE_ESTIMATE_TOOL JSON (nested dicts / lists).
    """
    est = EstimateRequest.model_validate(payload)
    pricing = compute_indicative_pricing(est)
    msg = (
        f"Prepared estimate {est.estimate_id} for {est.customer.contact_name}: "
        f"{len(est.line_items)} line(s), indicative total {_money(pricing.total)} {pricing.currency}. "
        "Share files and track approvals in the Fortis Edge Portal."
    )
    result = CreateEstimateResult(estimate=est, pricing=pricing, pdf_base64=None, message=msg)
    pdf_bytes = build_estimate_pdf_bytes(result)
    result = result.model_copy(
        update={
            "pdf_base64": pdf_to_base64(pdf_bytes) if include_pdf_base64 else None,
        }
    )
    logger.info("create_estimate ok: estimate_id=%s lines=%s", est.estimate_id, len(est.line_items))
    return result

