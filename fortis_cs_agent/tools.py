"""
Agent tools — estimates, portal/SBU lookups, product discovery.
"""

from __future__ import annotations

import logging
import os
import re
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from fortis_cs_agent.estimate_models import (
    CreateEstimateResult,
    EstimateRequest,
    EstimatedPricing,
    ProductType,
    normalize_estimate_payload,
)
from fortis_cs_agent.estimate_pdf import build_estimate_pdf_bytes, pdf_to_base64
from fortis_cs_agent.store import EST_TABLE, save_estimate_snapshot, supabase as _supabase_client

logger = logging.getLogger(__name__)


def insert_fortis_estimate(
    conversation_id: str,
    business_name: str,
    contact_name: str,
    email: str,
    phone: str,
    address: str,
    items: list[dict],
    notes: str = "This quote does not include shipping or taxes. Prices are valid for 30 days.",
) -> dict:
    """
    Insert a structured estimate row into Supabase (`fortis_estimates` by default).

    Requires a table schema that supports these columns (expand DDL if you currently only store JSON snapshots).

    items should be a list of dicts like:
    [
        {"sku": "STICKER_3x8_WBGP_G", "description": "3x8 White BOPP Gloss", "quantity": 5000, "unit_price": 0.0839, "total": 419.5},
        ...
    ]
    """
    if _supabase_client is None:
        raise RuntimeError(
            "Supabase is not configured; set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to insert estimates."
        )

    subtotal = sum(item.get("total", 0) for item in items)

    data = {
        "conversation_id": conversation_id,
        "business_name": business_name,
        "contact_name": contact_name,
        "email": email,
        "phone": phone,
        "address": address,
        "items": items,
        "subtotal": subtotal,
        "notes": notes,
        "status": "draft",
    }

    result = _supabase_client.table(EST_TABLE).insert(data).execute()
    if not result.data:
        raise RuntimeError("Supabase insert returned no rows; check table schema and RLS policies.")
    estimate_id = result.data[0]["id"]

    return {
        "estimate_id": estimate_id,
        "message": f"Estimate created successfully. Share this link with the customer: /quote/{estimate_id}",
    }


def _structured_estimate_validation_failed(
    hint: str,
    *,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {"error": "validation_failed", "hint": hint, "details": details or []}


def _coerce_positive_number(value: Any, *, field: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, field
    if isinstance(value, bool):
        return None, field
    if isinstance(value, (int, float)):
        if value < 0:
            return None, field
        return float(value), None
    if isinstance(value, str) and value.strip():
        try:
            n = float(value.strip().replace(",", ""))
            if n < 0:
                return None, field
            return n, None
        except ValueError:
            return None, field
    return None, field


def _parse_structured_create_estimate_args(
    raw: dict[str, Any],
    *,
    conversation_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Returns (kwargs for insert_fortis_estimate, error_response)."""
    cid = (conversation_id or "").strip() or str(raw.get("conversation_id") or "").strip()
    if not cid:
        return None, _structured_estimate_validation_failed(
            "conversation_id is missing; the server should attach the active thread id—retry once."
        )

    business_name = str(raw.get("business_name") or "").strip()
    contact_name = str(raw.get("contact_name") or "").strip()
    email = str(raw.get("email") or "").strip()
    phone = str(raw.get("phone") or "").strip()
    address = str(raw.get("address") or "").strip()
    notes_raw = raw.get("notes")
    notes = (
        "This quote does not include shipping or taxes. Prices are valid for 30 days."
        if notes_raw is None
        else str(notes_raw).strip()
    )

    missing = [
        label
        for label, ok in (
            ("business_name", bool(business_name)),
            ("contact_name", bool(contact_name)),
            ("email", bool(email)),
        )
        if not ok
    ]
    if missing:
        return None, _structured_estimate_validation_failed(
            "Required fields missing or empty.",
            details=[{"field": m, "message": "required non-empty string"} for m in missing],
        )

    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or len(items_raw) < 1:
        return None, _structured_estimate_validation_failed(
            "items must be a non-empty array of line objects.",
            details=[{"field": "items", "message": "expected at least one line"}],
        )

    normalized_items: list[dict[str, Any]] = []
    for idx, row in enumerate(items_raw):
        if not isinstance(row, dict):
            return None, _structured_estimate_validation_failed(
                f"items[{idx}] must be an object.",
                details=[{"field": f"items[{idx}]", "message": "invalid line"}],
            )
        sku = str(row.get("sku") or "").strip()
        description = str(row.get("description") or "").strip()
        qty, qerr = _coerce_positive_number(row.get("quantity"), field="quantity")
        unit_price, uperr = _coerce_positive_number(row.get("unit_price"), field="unit_price")
        total, terr = _coerce_positive_number(row.get("total"), field="total")
        line_issues = []
        if not sku:
            line_issues.append("sku")
        if not description:
            line_issues.append("description")
        if qerr or qty is None or qty <= 0:
            line_issues.append("quantity")
        if uperr or unit_price is None:
            line_issues.append("unit_price")
        if terr or total is None:
            line_issues.append("total")
        if line_issues:
            return None, _structured_estimate_validation_failed(
                f"Invalid line items[{idx}]: check sku, description, quantity, unit_price, total.",
                details=[{"field": f"items[{idx}]", "message": ", ".join(line_issues)}],
            )
        normalized_items.append(
            {
                "sku": sku,
                "description": description,
                "quantity": qty,
                "unit_price": unit_price,
                "total": total,
            }
        )

    return (
        {
            "conversation_id": cid,
            "business_name": business_name,
            "contact_name": contact_name,
            "email": email,
            "phone": phone,
            "address": address,
            "items": normalized_items,
            "notes": notes or "This quote does not include shipping or taxes. Prices are valid for 30 days.",
        },
        None,
    )


def _tool_validation_error_payload(exc: ValidationError) -> dict[str, Any]:
    """Structured, model-friendly errors when generate_estimate_pdf payload is wrong."""
    items = []
    for err in exc.errors()[:12]:
        loc = [str(x) for x in err.get("loc", ())]
        items.append({"field": ".".join(loc) if loc else "body", "message": err.get("msg", "invalid")})
    return {
        "error": "validation_failed",
        "hint": "Required: customer_name, products[{product_type, quantity, ...}]. "
        "Use search_products if unsure of product_type.",
        "details": items,
    }


# --- OpenAI-compatible tool schemas ----------------------------------------------------

GENERATE_ESTIMATE_PDF_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_estimate_pdf",
        "description": (
            "Generate a Fortis Edge indicative estimate PDF link + totals (downloadable PDF workflow). "
            "Use when the customer wants a formal PDF quote / indicative packaging estimate. "
            "Call as soon as you have: customer_name (contact person), company (business name — ask if missing), "
            "email (required for routing), product_type (usually pressure_sensitive_labels for Quick Ship labels), "
            "quantity, width/height in inches when known (use catalog nearest match from pricing context if needed), "
            "material/finish, colors (e.g. CMYK). "
            "After calling: include the tool's pdf_link verbatim in your reply so the user can open the PDF. "
            "Do not invent alternate URLs. "
            "If PUBLIC_BASE_URL is unset server-side, pdf_link may be a relative path — still pass it through. "
            "For saving a line-item quote with SKU-level pricing to the database instead, use create_estimate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Primary contact full name"},
                "company": {
                    "type": "string",
                    "description": "Customer company / organization name (ask explicitly before formal PDF)",
                },
                "email": {"type": "string", "description": "Work email for PDF + Portal routing"},
                "phone": {"type": "string"},
                "products": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "SKU or friendly label"},
                            "product_type": {
                                "type": "string",
                                "enum": [e.value for e in ProductType],
                                "description": "Canonical product category",
                            },
                            "quantity": {"type": "integer", "minimum": 1},
                            "width": {
                                "type": "number",
                                "description": "Width in inches (optional; improves accuracy)",
                            },
                            "height": {
                                "type": "number",
                                "description": "Height / second primary dimension in inches (optional)",
                            },
                            "material": {
                                "type": "string",
                                "description": "Substrate: BOPP, PET, paper, flute/board grade, film structure…",
                            },
                            "finish": {"type": "string", "description": "Matte, gloss, soft-touch, cold foil…"},
                            "colors": {"type": "string", "description": "Process / spot inks / brand colors"},
                            "turnaround_days": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 365,
                                "description": "Requested ready-to-ship horizon for this line",
                            },
                        },
                        "required": ["product_type", "quantity"],
                    },
                },
                "notes": {"type": "string", "description": "Customer-visible scope notes"},
                "urgency": {
                    "type": "string",
                    "enum": ["low", "standard", "high", "critical"],
                    "description": "Commercial urgency band (drives rush factor)",
                },
                "ship_to_region": {"type": "string"},
                "internal_notes": {"type": "string", "description": "Fortis-only context for PDF internal block"},
            },
            "required": ["customer_name", "products"],
        },
    },
}


CREATE_ESTIMATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_estimate",
        "description": (
            "Save a formal structured estimate / quote to Fortis (Supabase) with SKU line items and computed totals. "
            "Use when the customer wants an official saved quote, a record they can share via link, or line-level SKU pricing "
            "grounded in catalog/pricing context (not the PDF generator). "
            "Collect business details and build items[] from confirmed SKUs, descriptions, quantities, unit_price, and line totals. "
            "conversation_id is normally injected by the server from the active chat thread—you may omit it or leave it empty."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "description": "Active chat thread id; server fills this automatically when omitted.",
                },
                "business_name": {"type": "string", "description": "Customer company / organization"},
                "contact_name": {"type": "string", "description": "Primary contact full name"},
                "email": {"type": "string", "description": "Contact email for the quote"},
                "phone": {"type": "string", "description": "Phone (optional; use empty string if unknown)"},
                "address": {"type": "string", "description": "Ship-to or billing address (optional; use empty string if unknown)"},
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Quote lines with SKU-level pricing from catalog context",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "total": {"type": "number"},
                        },
                        "required": ["sku", "description", "quantity", "unit_price", "total"],
                    },
                },
                "notes": {
                    "type": "string",
                    "description": "Customer-visible notes (defaults server-side if omitted)",
                },
            },
            "required": ["business_name", "contact_name", "email", "items"],
        },
    },
}


GET_PORTAL_STATUS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_portal_status",
        "description": (
            "Return Fortis Edge Portal rollout status and milestones for internal teams. "
            "Call when users ask about portal launch, adoption, SSO, training waves, or regional rollout."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


GET_SBU_METRICS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_sbu_metrics",
        "description": (
            "Return indicative SBU / operations metrics (Tier 3/4 volume, top categories, utilization). "
            "Planning reference only — confirm with finance / BI for exec reporting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sbu_hint": {
                    "type": "string",
                    "description": "Optional SBU or region hint (labels, corrugated, etc.)",
                },
            },
        },
    },
}


SEARCH_PRODUCTS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_products",
        "description": (
            "Suggest product_type enum values from natural language (e.g. wrap labels, RFID tags, e-com shipper). "
            "Always call before generate_estimate_pdf when category is ambiguous."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What the stakeholder called the item (2+ words recommended)",
                },
            },
            "required": ["query"],
        },
    },
}


AGENT_TOOLS: list[dict[str, Any]] = [
    GENERATE_ESTIMATE_PDF_TOOL,
    CREATE_ESTIMATE_TOOL,
    GET_PORTAL_STATUS_TOOL,
    GET_SBU_METRICS_TOOL,
    SEARCH_PRODUCTS_TOOL,
]


def _public_base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")


def pdf_link_for(estimate_id: str) -> str:
    prefix = _public_base_url()
    tail = f"/estimate-pdf/{estimate_id}"
    return f"{prefix}{tail}" if prefix else tail


def _line_area_sf(li: Any) -> Decimal:
    from fortis_cs_agent.estimate_models import EstimateLineItem

    assert isinstance(li, EstimateLineItem)
    if li.length_in and li.width_in and li.depth_in:
        l, w, d = li.length_in, li.width_in, li.depth_in
        sides = Decimal("2") * (l * w + w * d + l * d)
        return (sides / Decimal("144")).quantize(Decimal("0.01"))
    if li.length_in and li.width_in:
        return (li.length_in * li.width_in / Decimal("144")).quantize(Decimal("0.01"))
    return Decimal("2.25")


def _line_monetary(li: Any) -> Decimal:
    from fortis_cs_agent.estimate_models import EstimateLineItem

    assert isinstance(li, EstimateLineItem)
    qty = Decimal(li.quantity)
    pt = li.product_type
    sf = _line_area_sf(li)

    if pt in (ProductType.PRESSURE_SENSITIVE_LABELS, ProductType.RFID_LABELS_OR_INLAYS):
        thousand = qty / Decimal("1000") if qty > 0 else Decimal("0")
        unit = Decimal("48") if pt == ProductType.PRESSURE_SENSITIVE_LABELS else Decimal("165")
        return (thousand * unit).quantize(Decimal("0.01"))

    if pt == ProductType.SHRINK_SLEEVES:
        return (sf * Decimal("8.95") * qty).quantize(Decimal("0.01"))

    if pt == ProductType.FLEXIBLE_PACKAGING:
        thousand = qty / Decimal("1000")
        film_index = Decimal("1.15") + (qty / Decimal("250000"))
        return (thousand * Decimal("420") * film_index).quantize(Decimal("0.01"))

    if pt == ProductType.FOLDING_CARTON:
        return (sf * Decimal("6.20") * qty).quantize(Decimal("0.01"))

    corrugated_rate = Decimal("5.05")
    multiplier = _type_multiplier(pt)
    return (sf * corrugated_rate * qty * multiplier).quantize(Decimal("0.01"))


def _type_multiplier(pt: ProductType) -> Decimal:
    return {
        ProductType.RSC: Decimal("1.0"),
        ProductType.TELESCOPING: Decimal("1.12"),
        ProductType.MAILER: Decimal("0.94"),
        ProductType.PARTITION: Decimal("0.78"),
        ProductType.TRAY_SLEEVE_COMBO: Decimal("1.18"),
        ProductType.BULK_OTHER: Decimal("1.06"),
        ProductType.PRESSURE_SENSITIVE_LABELS: Decimal("1.0"),
        ProductType.RFID_LABELS_OR_INLAYS: Decimal("1.0"),
        ProductType.SHRINK_SLEEVES: Decimal("1.0"),
        ProductType.FLEXIBLE_PACKAGING: Decimal("1.0"),
        ProductType.FOLDING_CARTON: Decimal("1.0"),
    }.get(pt, Decimal("1.0"))


def _turnaround_multiplier(turnaround: str) -> Decimal:
    return {
        "rush": Decimal("1.28"),
        "standard": Decimal("1.0"),
        "economy": Decimal("0.94"),
    }.get(turnaround, Decimal("1.0"))


def compute_indicative_pricing(req: EstimateRequest) -> EstimatedPricing:
    subtotal = sum((_line_monetary(li) for li in req.line_items), start=Decimal("0"))
    assumptions = [
        "Indicative extended pricing for planning; ERP / CS leaders must bless before customer-facing commitment.",
        "Portal-submitted artwork accelerates SBU cycle time versus offline routing — final dates follow Ops load.",
        "Tooling, cylinder, RFID encode, freight class, and MOQ can materially move totals.",
    ]

    has_rf = any(li.product_type == ProductType.RFID_LABELS_OR_INLAYS for li in req.line_items)
    has_print = any(
        (li.print_specs or li.finish or li.colors) for li in req.line_items
    )
    setup = Decimal("395") if (has_rf or has_print) else Decimal("155")
    if any(li.product_type == ProductType.SHRINK_SLEEVES for li in req.line_items):
        setup += Decimal("120")

    freight = Decimal("210") if req.ship_to_region else Decimal("110")
    rush = _turnaround_multiplier(req.turnaround)

    total = (subtotal + setup + freight) * rush

    return EstimatedPricing(
        subtotal=subtotal.quantize(Decimal("0.01")),
        setup_fees=setup,
        freight_estimate=freight,
        rush_multiplier=rush,
        total=total.quantize(Decimal("0.01")),
        assumptions=assumptions,
    )


def _money(amount: Decimal) -> str:
    return f"${amount.quantize(Decimal('0.01')):,.2f}"


def assemble_estimate_result(
    est: EstimateRequest,
    *,
    pricing: EstimatedPricing | None = None,
    include_pdf_base64: bool = False,
) -> CreateEstimateResult:
    pr = pricing or compute_indicative_pricing(est)
    msg = (
        f"Estimate {est.estimate_id} | {est.customer.contact_name} | "
        f"{len(est.line_items)} line(s) | indicative {_money(pr.total)} {pr.currency} | "
        f"valid through {est.valid_until.isoformat()} | Portal upload next."
    )
    interim = CreateEstimateResult(estimate=est, pricing=pr, pdf_base64=None, pdf_link=None, message=msg)
    pdf_bytes = build_estimate_pdf_bytes(interim)
    link = pdf_link_for(str(est.estimate_id))
    return interim.model_copy(
        update={
            "pdf_base64": pdf_to_base64(pdf_bytes) if include_pdf_base64 else None,
            "pdf_link": link,
            "message": msg,
        }
    )


def create_estimate(
    *,
    payload: dict[str, Any],
    include_pdf_base64: bool = False,
    persist_snapshot: bool = True,
) -> CreateEstimateResult:
    try:
        est = normalize_estimate_payload(payload)
    except ValidationError as exc:
        logger.info(
            "create_estimate.validation_failed estimate_parse count=%s",
            len(exc.errors()),
        )
        raise

    pricing = compute_indicative_pricing(est)
    result = assemble_estimate_result(est, pricing=pricing, include_pdf_base64=include_pdf_base64)

    if persist_snapshot:
        try:
            save_estimate_snapshot(est)
        except Exception:
            logger.exception("save_estimate_snapshot failed estimate_id=%s", est.estimate_id)

    logger.info(
        "create_estimate ok total=%s estimate_id=%s lines=%s pdf_link=%s",
        result.pricing.total,
        est.estimate_id,
        len(est.line_items),
        result.pdf_link,
    )
    return result


# --- Product search ----------------------------------------------------------------------

_PRODUCT_CATALOG: list[dict[str, Any]] = [
    {
        "product_type": ProductType.PRESSURE_SENSITIVE_LABELS.value,
        "title": "Pressure-sensitive labels",
        "blurb": "Roll-fed or sheeted PS labels — flexo, digital, cold foil",
        "kw": "pressure sensitive label sticker prime roll flexo digital psa beverage prime wrap",
    },
    {
        "product_type": ProductType.RFID_LABELS_OR_INLAYS.value,
        "title": "RFID labels & inlays",
        "blurb": "UHF/HF inlays, encoding, tag performance validation",
        "kw": "rfid rainbow inlay encode uhf hf nfc smart label",
    },
    {
        "product_type": ProductType.SHRINK_SLEEVES.value,
        "title": "Shrink sleeves",
        "blurb": "Full-body or partial sleeves, PET/PVC/OPS films",
        "kw": "shrink sleeve label bottle full body tamper neck band",
    },
    {
        "product_type": ProductType.FLEXIBLE_PACKAGING.value,
        "title": "Flexible packaging",
        "blurb": "Pouches, films, sachets — barrier & sustainable options",
        "kw": "pouch film sachet stand up flexible llpd",
    },
    {
        "product_type": ProductType.FOLDING_CARTON.value,
        "title": "Folding cartons",
        "blurb": "Paperboard cartons — SR, CR, litho lam",
        "kw": "folding carton paperboard cereal pharma tuck end",
    },
    {
        "product_type": ProductType.RSC.value,
        "title": "Corrugated RSC",
        "blurb": "Regular slotted container shippers — BCT / ECT specs",
        "kw": "corrugated box rsc shipper case master carton ect bct",
    },
    {
        "product_type": ProductType.TELESCOPING.value,
        "title": "Telescoping boxes",
        "blurb": "Two-piece rigid-style presentation shippers",
        "kw": "telescope box setup two piece gift",
    },
    {
        "product_type": ProductType.MAILER.value,
        "title": "Mailers",
        "blurb": "E-com mailers — kraft, poly, padded",
        "kw": "mailer poly bubble ecommerce shipper bag",
    },
    {
        "product_type": ProductType.PARTITION.value,
        "title": "Partitions & inserts",
        "blurb": "Interior protection pieces, bottle partitions",
        "kw": "partition insert divider bottle layer pad",
    },
    {
        "product_type": ProductType.TRAY_SLEEVE_COMBO.value,
        "title": "Tray + sleeve kits",
        "blurb": "Multi-component retail-ready packaging",
        "kw": "tray sleeve combo club display",
    },
    {
        "product_type": ProductType.BULK_OTHER.value,
        "title": "Bulk / specialty",
        "blurb": "Catch-all for non-standard SKUs — engage packaging engineering",
        "kw": "bulk pallet specialty industrial custom",
    },
]


def search_products(*, query: str, limit: int = 5) -> dict[str, Any]:
    q = (query or "").strip().lower()
    if len(q) < 2:
        return {
            "matches": [],
            "hint": "Provide at least 2 characters (e.g. shrink sleeve for nutraceutical bottle).",
        }
    toks = re.findall(r"[a-z0-9]+", q)
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in _PRODUCT_CATALOG:
        blob = f"{row['title']} {row['blurb']} {row['kw']}".lower()
        score = sum(3 for t in toks if t in blob)
        if score == 0:
            score = sum(1 for t in toks if any(t in w for w in blob.split()))
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    top = [dict(row) for _, row in scored[:limit]]
    logger.info("search_products query=%r hits=%s", query, len(top))
    return {
        "query": query,
        "matches": top,
        "hint": "Pick product_type from matches and pass to generate_estimate_pdf.",
    }


def get_portal_status() -> dict[str, Any]:
    """Static reference data; replace with Supabase/feature-flag API when wired."""
    return {
        "rollout_percent": int(os.getenv("FORTIS_PORTAL_ROLLOUT_PCT", "73")),
        "phase": os.getenv("FORTIS_PORTAL_PHASE", "Wave 3 — SBU self-service hardening"),
        "milestones": [
            {"name": "SSO + MFA for all SBU logins", "eta": "Jun 2026", "status": "on_track"},
            {"name": "Label artwork diffing in Portal", "eta": "Jul 2026", "status": "in_progress"},
            {"name": "Corrugated structural approvals digitized", "eta": "Aug 2026", "status": "planned"},
        ],
        "self_service_wins": [
            "24–48h faster CS acknowledgement vs email-only routing when files hit Portal.",
            "Versioned artwork reduces plant remakes tied to ambiguous approvals.",
        ],
        "disclaimer": "Snapshot for internal Fortis Edge Advisor — sync with PMO before external statements.",
        "last_refreshed": os.getenv("FORTIS_KNOWLEDGE_AS_OF", "2026-04-01"),
    }


def get_sbu_metrics(*, sbu_hint: str | None = None) -> dict[str, Any]:
    hint = (sbu_hint or "").strip().lower()
    base = {
        "tier_3_4_volume_mm_units": float(os.getenv("FORTIS_SBU_T34_VOLUME_MM", "128.4")),
        "tier_3_4_yoy_growth_pct": float(os.getenv("FORTIS_SBU_T34_YOY", "6.2")),
        "plant_utilization_pct": int(os.getenv("FORTIS_PLANT_UTIL", "81")),
        "top_product_mix": [
            {"category": "PS labels — beverage", "share_pct": 24},
            {"category": "Shrink sleeves — wellness", "share_pct": 19},
            {"category": "RSC e-commerce shippers", "share_pct": 17},
            {"category": "Folding cartons — OTC", "share_pct": 14},
        ],
        "speed_note": "Portal-first SBUs average faster proof-to-release cadence by cutting ad-hoc file chase.",
        "disclaimer": "Illustrative composite for CS planning — validate against enterprise BI.",
        "sbu_filter": hint or "all",
    }
    logger.info("get_sbu_metrics hint=%r", sbu_hint)
    return base


def execute_agent_tool(
    function_name: str,
    raw_args: dict[str, Any],
    *,
    persist_estimate: bool = True,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Dispatch tool by name; always returns a JSON-serializable dict (for Grok)."""
    fn = (function_name or "").strip()
    args = raw_args or {}
    logger.debug(
        "execute_agent_tool name=%s persist_estimate=%s conversation_id=%s",
        fn,
        persist_estimate,
        conversation_id,
    )

    try:
        if fn == "generate_estimate_pdf":
            try:
                result = create_estimate(
                    payload=args,
                    include_pdf_base64=False,
                    persist_snapshot=persist_estimate,
                )
                return result.to_tool_dict()
            except ValidationError as exc:
                return _tool_validation_error_payload(exc)

        if fn == "create_estimate":
            products = args.get("products")
            if isinstance(products, list) and len(products) > 0 and args.get("customer_name"):
                # Back-compat: older prompts named the PDF tool `create_estimate`.
                try:
                    result = create_estimate(
                        payload=args,
                        include_pdf_base64=False,
                        persist_snapshot=persist_estimate,
                    )
                    return result.to_tool_dict()
                except ValidationError as exc:
                    return _tool_validation_error_payload(exc)

            kwargs, err = _parse_structured_create_estimate_args(args, conversation_id=conversation_id)
            if err:
                return err
            assert kwargs is not None
            subtotal = sum(float(item.get("total", 0)) for item in kwargs["items"])
            if not persist_estimate:
                logger.info(
                    "create_estimate structured dry_run conversation_id=%s lines=%s subtotal=%s",
                    kwargs["conversation_id"],
                    len(kwargs["items"]),
                    subtotal,
                )
                return {
                    "dry_run": True,
                    "conversation_id": kwargs["conversation_id"],
                    "subtotal": subtotal,
                    "items": kwargs["items"],
                    "message": (
                        "Test mode: structured estimate was not saved. "
                        "In production this would create a database record and return estimate_id + customer link."
                    ),
                }
            try:
                out = insert_fortis_estimate(**kwargs)
                logger.info(
                    "create_estimate structured ok estimate_id=%s conversation_id=%s",
                    out.get("estimate_id"),
                    kwargs["conversation_id"],
                )
                return out
            except RuntimeError as exc:
                return {"error": "configuration_error", "message": str(exc)[:600]}
            except Exception as exc:
                logger.exception("create_estimate structured insert failed.")
                return {"error": "tool_failed", "message": str(exc)[:600]}

        if fn == "get_portal_status":
            return get_portal_status()

        if fn == "get_sbu_metrics":
            return get_sbu_metrics(sbu_hint=args.get("sbu_hint"))

        if fn == "search_products":
            q = args.get("query") or ""
            return search_products(query=str(q))

        logger.warning("unknown_tool name=%s", fn)
        return {"error": "unknown_tool", "message": f"No handler for `{fn}`."}
    except Exception as exc:
        logger.exception("execute_agent_tool failed name=%s", fn)
        return {"error": "tool_failed", "message": str(exc)[:600]}
