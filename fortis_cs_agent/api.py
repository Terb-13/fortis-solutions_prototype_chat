"""
HTTP routes for the Fortis CS agent: Grok chat, Twilio SMS, estimates, health.

Logging and error handling mirror a typical production FastAPI service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from fortis_cs_agent.estimate_models import EstimateRequest
from fortis_cs_agent.estimate_pdf import write_estimate_pdf_binary
from fortis_cs_agent.estimate_json import compact_history_hint, parse_assistant_estimate_json
from fortis_cs_agent.estimate_detector import is_estimate_request

# Bind estimate_flow only via _estimate_flow (avoid a second import from the estimate_flow submodule).
from fortis_cs_agent import estimate_flow as _estimate_flow
from fortis_cs_agent.knowledge import format_pricing_context, pricing_health_probe, retrieve_knowledge, retrieve_pricing
from fortis_cs_agent.prompts import render_system_prompt
from fortis_cs_agent.store import load_estimate_snapshot
from fortis_cs_agent.tools import AGENT_TOOLS, assemble_estimate_result, create_estimate, execute_agent_tool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["fortis"])

XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_CHAT_MODEL = (os.getenv("XAI_CHAT_MODEL") or "").strip() or "grok-4"
XAI_CHAT_MODEL_FALLBACK = (os.getenv("XAI_CHAT_MODEL_FALLBACK") or "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
_SUPABASE_RESOLVED_URL = SUPABASE_URL or "https://vapnbelrpaxeafospalc.supabase.co"

if SUPABASE_KEY:
    from supabase import create_client

    supabase = create_client(_SUPABASE_RESOLVED_URL, SUPABASE_KEY)
else:
    supabase = None

TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_VALIDATE = os.getenv("TWILIO_VALIDATE_SIGNATURE", "false").lower() in ("1", "true", "yes")

CONV_TABLE = os.getenv("FORTIS_CONVERSATIONS_TABLE", "fortis_conversations")
MSG_TABLE = os.getenv("FORTIS_MESSAGES_TABLE", "fortis_messages")


def estimate_pdf_absolute_url(request: Request, estimate_id: str) -> str:
    """Build an absolute `/estimate-pdf/{id}` URL for emailed links and JSON consumers."""
    pub = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if pub:
        return f"{pub}/estimate-pdf/{estimate_id}"
    return str(request.url_for("estimate_pdf_download", estimate_id=estimate_id))


# --- Supabase persistence (graceful degrade when unset) --------------------------------


def _sb() -> Any:
    return supabase


def _normalize_conversation_id(existing_id: str | None) -> str:
    """Return a UUID string for this thread: reuse client id when valid, else allocate new."""
    raw = (existing_id or "").strip()
    if not raw:
        return str(uuid.uuid4())
    try:
        uuid.UUID(raw)
        return raw
    except ValueError:
        logger.warning(
            "Ignoring invalid conversation_id from client %r; allocating a new UUID.",
            raw[:80],
        )
        return str(uuid.uuid4())


def _supabase_rest_error_hint(exc: BaseException, *, limit: int = 420) -> str:
    parts: list[str] = []
    for attr in ("code", "message", "details", "hint"):
        val = getattr(exc, attr, None)
        if val:
            parts.append(str(val))
    return (" ".join(parts) if parts else str(exc)).strip()[:limit]


def _upsert_conversation_row(
    *,
    conversation_id: str,
    channel: Literal["sms", "web", "api"],
    channel_ref: str,
) -> None:
    """Insert-or-update fortis_conversations with PostgREST-friendly upsert shape.

    postgrest-py only adds ``columns`` when upsert receives a non-empty **list**.
    Omitting ``on_conflict`` breaks some deployments (HTTP 400 on merge-duplicates).

    ``default_to_null=False`` emits ``Prefer: missing=default`` so ``created_at`` /
    ``updated_at`` table defaults remain valid on INSERT.
    """

    client = _sb()
    if client is None:
        raise RuntimeError("_upsert_conversation_row requires a configured Supabase client")

    row: dict[str, Any] = {
        "id": conversation_id,
        "channel": channel,
        "channel_ref": channel_ref or "",
    }
    client.table(CONV_TABLE).upsert(
        [row],
        on_conflict="id",
        default_to_null=False,
    ).execute()


def ensure_conversation(
    *,
    channel: Literal["sms", "web", "api"],
    channel_ref: str | None,
    existing_id: str | None,
) -> str | None:
    """Ensure a row exists in ``fortis_conversations`` for the returned id.

    When Supabase is configured, **always upserts** (including when the client sends an
    ``existing_id``) so ``fortis_messages`` foreign keys succeed.

    Returns:
        - UUID string when persistence is skipped (no Supabase client) or upsert succeeds.
        - ``None`` only when Supabase is configured but upsert fails (caller should treat as fatal for DB-backed chat).
    """
    conv_id = _normalize_conversation_id(existing_id)
    client = _sb()
    if client is None:
        return conv_id

    try:
        _upsert_conversation_row(
            conversation_id=conv_id,
            channel=channel,
            channel_ref=channel_ref or "",
        )
        logger.info(
            "ensure_conversation upsert ok table=%s conversation_id=%s channel=%s channel_ref=%r",
            CONV_TABLE,
            conv_id,
            channel,
            channel_ref or "",
        )
        return conv_id
    except Exception as exc:
        hint = _supabase_rest_error_hint(exc)
        logger.exception(
            "ensure_conversation UPSERT FAILED table=%s conversation_id=%s channel=%s "
            "channel_ref=%r postgrest=%s exc_type=%s",
            CONV_TABLE,
            conv_id,
            channel,
            channel_ref or "",
            hint or "(none)",
            type(exc).__name__,
        )
        return None


def allocate_web_chat_conversation(existing_id: str | None) -> tuple[str, bool]:
    """Return (conversation_uuid, persist_thread).

    Persist is ``False`` when Supabase isn't configured.

    Upsert failures raise ``HTTPException`` (503) with an actionable hint, unless env
    ``FORTIS_CHAT_RELAX_CONVERSATION_UPSERT`` is true — then persistence is skipped and Grok runs
    with an empty server-side history.
    """

    cid = _normalize_conversation_id(existing_id)
    client = _sb()
    if client is None:
        return cid, False

    try:
        _upsert_conversation_row(
            conversation_id=cid,
            channel="web",
            channel_ref="",
        )
        logger.info(
            "allocate_web_chat_conversation upsert ok conversation_id=%s table=%s",
            cid,
            CONV_TABLE,
        )
        return cid, True
    except Exception as exc:
        hint = _supabase_rest_error_hint(exc)
        logger.exception(
            "allocate_web_chat_conversation UPSERT FAILED conversation_id=%s table=%s postgrest=%s",
            cid,
            CONV_TABLE,
            hint or "(none)",
        )
        relaxed = os.getenv(
            "FORTIS_CHAT_RELAX_CONVERSATION_UPSERT", ""
        ).lower() in ("1", "true", "yes")
        if relaxed:
            logger.warning(
                "FORTIS_CHAT_RELAX_CONVERSATION_UPSERT enabled — /chat proceeding without persisted threads"
            )
            return cid, False

        base = (
            "Conversation store unreachable: fortis_conversations upsert failed (see server logs). "
            "PostgREST PGRST204 on column 'channel' usually means your table predates README — "
            "run sql/fix_fortis_conversations_chat_columns.sql in Supabase. "
            "Otherwise confirm schema (id uuid PK, channel, channel_ref, timestamps). "
            "Temporary: FORTIS_CHAT_RELAX_CONVERSATION_UPSERT=1 (no saved threads)."
        )
        extra = f" PostgREST detail: {hint}" if hint else ""
        raise HTTPException(
            status_code=503,
            detail=(base + extra)[:1600],
        )


def find_sms_conversation(sender: str) -> str | None:
    """Return existing SMS thread id keyed by caller number."""
    client = _sb()
    if client is None or not sender:
        return None
    try:
        res = (
            client.table(CONV_TABLE)
            .select("id")
            .eq("channel", "sms")
            .eq("channel_ref", sender)
            .limit(1)
            .execute()
        )
        if res.data:
            return str(res.data[0]["id"])
    except Exception:
        logger.exception("find_sms_conversation failed.")
    return None


def append_message(
    conversation_id: str,
    *,
    role: Literal["user", "assistant", "system", "tool"],
    content: str | None,
    tool_name: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    client = _sb()
    if client is None:
        return
    try:
        payload: dict[str, Any] = {
            "conversation_id": conversation_id,
            "role": role,
            "content": content or "",
        }
        if tool_name:
            payload["tool_name"] = tool_name
        if meta is not None:
            payload["meta"] = meta
        client.table(MSG_TABLE).insert(payload).execute()
    except Exception as exc:
        logger.exception(
            "append_message FAILED table=%s conversation_id=%s role=%s tool_name=%s "
            "exc_type=%s message=%s payload_preview_keys=%s",
            MSG_TABLE,
            conversation_id,
            role,
            tool_name,
            type(exc).__name__,
            getattr(exc, "message", None) or str(exc),
            list(payload.keys()),
        )


def load_recent_messages(conversation_id: str, limit: int = 40) -> list[dict[str, Any]]:
    client = _sb()
    if client is None:
        return []
    try:
        res = (
            client.table(MSG_TABLE)
            .select("role,content,tool_name,meta,created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = list(reversed(res.data or []))
        return rows
    except Exception:
        logger.exception("load_recent_messages failed for %s", conversation_id)
        return []


# --- xAI Grok -------------------------------------------------------------------------


def _parse_grok_error_body(r: httpx.Response) -> str:
    """Short summary of xAI / OpenAI-style error JSON for logs and HTTP detail."""
    raw = (r.text or "").strip()
    if not raw:
        return "(empty response body)"
    try:
        data = r.json()
        err = data.get("error")
        if isinstance(err, dict):
            parts = [str(x) for x in (err.get("message"), err.get("code")) if x]
            if err.get("type"):
                parts.append(str(err["type"]))
            return " | ".join(parts) if parts else raw[:400]
        if isinstance(err, str):
            return err[:400]
        msg = data.get("message")
        if isinstance(msg, str):
            return msg[:400]
    except Exception:
        pass
    return raw[:500]


async def _grok_chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not XAI_API_KEY:
        raise HTTPException(status_code=503, detail="XAI_API_KEY is not configured.")

    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    candidates: list[str] = []
    prim = XAI_CHAT_MODEL.strip()
    if prim:
        candidates.append(prim)
    if XAI_CHAT_MODEL_FALLBACK and XAI_CHAT_MODEL_FALLBACK.lower() not in {
        c.lower() for c in candidates
    }:
        candidates.append(XAI_CHAT_MODEL_FALLBACK)

    max_retries = max(1, min(5, int(os.getenv("XAI_CHAT_MAX_RETRIES") or "3")))
    base_delay_sec = float(os.getenv("XAI_CHAT_RETRY_BASE_SEC") or "1.25")

    transient = frozenset({408, 425, 429, 500, 502, 503, 504})
    last_status: int | None = None
    last_upstream = ""

    async with httpx.AsyncClient(timeout=120.0) as client:
        for model_slug in candidates:
            body: dict[str, Any] = {
                "model": model_slug,
                "messages": messages,
                "temperature": 0.35,
            }
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"

            for attempt in range(max_retries):
                r = await client.post(url, headers=headers, json=body)
                if r.status_code < 400:
                    if model_slug != prim:
                        logger.info("Grok request succeeded via fallback model %s", model_slug)
                    return r.json()

                upstream = _parse_grok_error_body(r)
                last_status = r.status_code
                last_upstream = upstream
                transient_like = (
                    r.status_code in transient
                    or ("capacity" in upstream.lower())
                    or ("overload" in upstream.lower())
                    or ("try again later" in upstream.lower())
                    or ("temporarily unavailable" in upstream.lower())
                )
                will_retry_same_model = transient_like and attempt < max_retries - 1

                logger.error(
                    "Grok API HTTP %s model=%s attempt=%s/%s upstream=%s",
                    r.status_code,
                    model_slug,
                    attempt + 1,
                    max_retries,
                    upstream.replace("\n", " ")[:800],
                )
                if will_retry_same_model:
                    delay = base_delay_sec * (2**attempt)
                    jitter = random.uniform(0, min(4.0, base_delay_sec))
                    await asyncio.sleep(delay + jitter)
                    continue

                break

    assert last_status is not None

    outbound = (
        f"Grok API error ({last_status}, tried models={','.join(candidates)}): {last_upstream}. "
        "Upstream may be overloaded—please retry shortly. "
        "You can set **XAI_CHAT_MODEL_FALLBACK** to another slug your key supports. "
        "See GET /health (`grok_model`)."
    )
    http_status = 503 if last_status in (429, 503) else 502
    raise HTTPException(status_code=http_status, detail=outbound)


def _should_skip_knowledge_retrieval(user_text: str) -> bool:
    """Avoid RAG for meta/capability questions — training snippets look like real tickets and confuse the model."""
    t = (user_text or "").strip().lower()
    if not t:
        return False
    # Order #s, quantities, dates → shopper is being specific; allow retrieval.
    if any(ch.isdigit() for ch in t):
        return False
    if len(t) > 72:
        return False
    if " about " in t:
        return False
    phrases = (
        "what can you do",
        "what can u do",
        "what do you do",
        "how can you help",
        "what are you",
        "who are you",
        "what are your capabilities",
        "your capabilities",
        "how does this work",
        "how do you work",
        "what is this",
    )
    if any(p in t for p in phrases):
        return True
    if t in {"hi", "hello", "hey", "help"}:
        return True
    return False


def _recent_user_text_for_pricing(prev: list[dict[str, Any]], user_text: str, *, max_prior: int = 6) -> str:
    """Merge recent user turns so size/qty/material from earlier messages still inform pricing lookup."""
    chunks: list[str] = []
    for row in prev:
        if row.get("role") != "user":
            continue
        c = (row.get("content") or "").strip()
        if c:
            chunks.append(c)
    tail = chunks[-max_prior:] if chunks else []
    return "\n".join([*tail, user_text])


def _sanitize_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for row in rows:
        role = row.get("role")
        content = row.get("content") or ""
        if role not in ("user", "assistant", "system", "tool"):
            continue
        if role == "tool":
            # Skip replaying opaque tool payloads into Grok unless you store canonical tool messages.
            continue
        if role == "assistant" and parse_assistant_estimate_json(content):
            # Keeps transcripts readable and prevents confusing follow-up turns ("reset" greetings).
            content = compact_history_hint()

        msgs.append({"role": role, "content": content})
    return msgs


def _quote_view_absolute_url(estimate_id: str) -> str:
    pub = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    tail = f"/quote/{estimate_id}"
    return f"{pub}{tail}" if pub else tail


async def persist_estimate_from_assistant_json(
    raw_reply: str,
    *,
    conversation_id: str,
) -> tuple[str, str | None]:
    """Parse Grok-authored estimate JSON, persist row, replace reply with shopper-friendly copy."""
    parsed = parse_assistant_estimate_json(raw_reply)
    if parsed is None:
        return raw_reply, None

    from fortis_cs_agent.tools import insert_fortis_estimate

    def _insert() -> dict[str, Any]:
        return insert_fortis_estimate(
            conversation_id,
            parsed["business_name"],
            parsed["contact_name"],
            parsed["email"],
            parsed.get("phone") or "",
            parsed.get("address") or "",
            parsed["items"],
            parsed["notes"],
        )

    try:
        out = await asyncio.to_thread(_insert)
    except Exception:
        logger.exception(
            "estimate_json persistence failed conversation_id=%s business=%s",
            conversation_id,
            parsed.get("business_name"),
        )
        excerpt = raw_reply.strip()[:800]
        return (
            "We captured structured estimate JSON from the assistant, but saving to Supabase failed "
            "(check server logs / `fortis_estimates` schema + `SUPABASE_SERVICE_ROLE_KEY`).\n\n"
            f"Draft excerpt:\n{excerpt}",
            None,
        )

    qid = str(out.get("estimate_id", "")).strip()
    link = _quote_view_absolute_url(qid)
    friendly = (
        f"Estimate saved.\n\n"
        f"View your quote: {link}\n"
        f"Quote reference: `{qid}`"
    )

    logger.info(
        "estimate_json persisted estimate_id=%s conversation_id=%s",
        qid,
        conversation_id,
    )

    return friendly, qid or None


async def run_agent_turn(
    user_text: str,
    *,
    conversation_id: str,
    augment_knowledge: bool = True,
) -> str:
    """Resolve a single conversational turn (/chat disables Grok tool calls — quotes serialize as JSON)."""
    msgs: list[dict[str, Any]] = [{"role": "system", "content": render_system_prompt()}]
    prev = load_recent_messages(conversation_id, limit=30)
    msgs.extend(_sanitize_history(prev))
    knowledge_context = ""
    if augment_knowledge:
        pricing_blob = _recent_user_text_for_pricing(prev, user_text)
        blob_lower = pricing_blob.lower()
        pricing_intent_tokens = (
            "price",
            "cost",
            "how much",
            "pricing",
            "quote",
            "estimate",
            "material",
            "finish",
            "quantity",
            "qty",
            "buy",
            "order",
            "purchase",
            "placing an order",
            "cheapest",
            "sticker",
            "label",
            "size",
            "dimension",
            "dimensions",
            "width",
            "height",
            "cmyk",
            "printing",
            "pdf",
            "formal",
        )
        pricing_intent = any(word in blob_lower for word in pricing_intent_tokens) or is_estimate_request(
            pricing_blob
        )

        pricing_results: list[dict[str, Any]] = []
        if pricing_intent:
            try:
                pricing_results = retrieve_pricing(pricing_blob, limit=5)
            except Exception:
                logger.exception("retrieve_pricing failed; continuing without pricing rows.")
                pricing_results = []

        # When label pricing rows exist, DO NOT mix in fortis_knowledge (training transcripts can mention pouches, etc.).
        if pricing_results:
            knowledge_context = format_pricing_context(pricing_results, pricing_blob)
        elif pricing_intent:
            knowledge_context = (
                "Quick Ship label pricing: No fortis_pricing rows matched this thread yet. "
                "Stay on pressure-sensitive labels; do not cite pouch / flexible film examples. "
                "Collect missing customer + address fields normally. Never invent dollars in prose."
            )
        else:
            if _should_skip_knowledge_retrieval(user_text):
                knowledge_context = ""
            else:
                try:
                    knowledge_results = retrieve_knowledge(user_text, limit=4)
                except Exception:
                    logger.exception("retrieve_knowledge failed; continuing without snippets.")
                    knowledge_results = []
                knowledge_context = "\n\n".join([r["content"] for r in knowledge_results])

    augmented = user_text
    if knowledge_context:
        augmented = (
            "Internal knowledge below is CONFIDENTIAL reference for you only. It may contain other "
            "customers’ or sample scenarios. Do NOT quote PO/lot/order numbers, names, companies, emails, "
            "phones, or addresses from it. Use it only for generic Fortis guidance. The shopper’s words "
            "are only in “Customer message”.\n\n"
            "--- internal reference ---\n"
            + knowledge_context
            + "\n--- end internal reference ---\n\nCustomer message:\n"
            + user_text
        )
    msgs.append({"role": "user", "content": augmented})

    grok_msgs = [*msgs]

    try:
        data = await _grok_chat(grok_msgs, tools=None)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Grok invocation failed in run_agent_turn (conversation_id=%s).", conversation_id)
        raise HTTPException(status_code=502, detail="Upstream model unavailable.") from None

    try:
        choice = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        logger.exception("Grok returned unexpected response shape (conversation_id=%s).", conversation_id)
        return "(Something went wrong parsing the assistant response—please try again.)"

    tc = choice.get("tool_calls") or []
    if tc:
        logger.warning(
            "Unexpected tool_calls from Grok despite tools disabled (conversation_id=%s)",
            conversation_id,
        )

    content = choice.get("content") or ""
    return content.strip() or "(No response text from model.)"


# --- Request models -------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    estimate_id: str | None = None


# --- Routes ---------------------------------------------------------------------------

@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "fortis-cs-agent",
        "grok_configured": bool(XAI_API_KEY),
        "grok_model": XAI_CHAT_MODEL,
        "grok_fallback_model": XAI_CHAT_MODEL_FALLBACK or None,
        "supabase_configured": supabase is not None,
        "pricing_health": pricing_health_probe(),
        "estimate_flow_build": _estimate_flow.ESTIMATE_FLOW_BUILD,
        "vercel_git_commit_sha": (os.getenv("VERCEL_GIT_COMMIT_SHA") or "").strip() or None,
        "vercel_git_commit_ref": (os.getenv("VERCEL_GIT_COMMIT_REF") or "").strip() or None,
    }


@router.get("/estimate-pdf/{estimate_id}", name="estimate_pdf_download")
async def estimate_pdf_download(estimate_id: str) -> StreamingResponse:
    """Regenerate the PDF for a previously stored estimate (Supabase or in-process cache)."""
    snap = load_estimate_snapshot(estimate_id)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail="Estimate not found. Create one via POST /create-estimate or the chat tool first.",
        )
    result = assemble_estimate_result(snap, include_pdf_base64=False)
    bio = write_estimate_pdf_binary(result)
    filename = f"fortis_estimate_{estimate_id}.pdf"
    return StreamingResponse(
        bio,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    cid, persist = allocate_web_chat_conversation(req.conversation_id)

    history = load_recent_messages(cid) if persist else []

    estimate_flow_result = _estimate_flow.handle_estimate_flow(
        user_message=req.message,
        conversation_history=history,
        conversation_id=cid,
    )

    reply: str
    estimate_id: str | None
    assistant_meta: dict[str, Any] | None

    try:
        if estimate_flow_result.handled:
            reply = estimate_flow_result.reply
            estimate_id = estimate_flow_result.estimate_id
            assistant_meta = estimate_flow_result.assistant_meta
        else:
            reply = await run_agent_turn(req.message, conversation_id=cid)
            reply, estimate_id = await persist_estimate_from_assistant_json(reply, conversation_id=cid)
            assistant_meta = None
    except HTTPException:
        raise
    except Exception:
        logger.exception("chat pipeline failed (conversation_id=%s)", cid)
        raise HTTPException(status_code=500, detail="Chat failed.") from None

    if persist:
        append_message(cid, role="user", content=req.message)
        append_message(cid, role="assistant", content=reply, meta=assistant_meta)
    return ChatResponse(reply=reply, conversation_id=cid, estimate_id=estimate_id)


@router.get("/test-chat", response_model=ChatResponse)
async def test_chat(
    message: str = Query(
        ...,
        min_length=1,
        description="Ephemeral Grok probe (still enables legacy tool stack; differs from POST /chat JSON quotes).",
    ),
) -> ChatResponse:
    """GET probe for Grok + tools (does not mirror production /chat JSON-quote mode)."""
    if not XAI_API_KEY:
        raise HTTPException(status_code=503, detail="XAI_API_KEY is not configured.")

    cid = str(uuid.uuid4())
    msgs: list[dict[str, Any]] = [{"role": "system", "content": render_system_prompt()}]
    msgs.append({"role": "user", "content": message})
    grok_msgs = [*msgs]

    while True:
        try:
            data = await _grok_chat(grok_msgs, tools=AGENT_TOOLS)
        except HTTPException:
            raise
        except Exception:
            logger.exception("test-chat Grok invocation failed (session=%s).", cid)
            raise HTTPException(
                status_code=502,
                detail="Model request failed. Verify Grok configuration, then retry.",
            ) from None

        choice = data["choices"][0]["message"]
        assistant_msg = {"role": "assistant", **choice}
        grok_msgs.append(assistant_msg)
        tc = choice.get("tool_calls") or []

        if tc:
            for call in tc:
                fname = call.get("function", {}).get("name")
                args_raw = call.get("function", {}).get("arguments") or "{}"
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    logger.warning("test_chat bad_tool_json tool=%s", fname)
                    args = {}
                payload = execute_agent_tool(
                    fname or "",
                    args,
                    persist_estimate=False,
                    conversation_id=cid,
                )
                grok_msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", "call"),
                        "content": json.dumps(payload),
                    }
                )
            continue

        content = choice.get("content") or ""
        reply = content.strip() or "(No response text from model.)"
        break

    return ChatResponse(reply=reply, conversation_id=f"test-{cid}")


@router.post("/twilio-webhook")
async def twilio_webhook(request: Request) -> Response:
    form = await request.form()
    signature = request.headers.get("X-Twilio-Signature", "")
    if TWILIO_VALIDATE and TWILIO_AUTH_TOKEN:
        # Twilio signs the callback URL configured on the webhook; set TWILIO_WEBHOOK_URL in prod when
        # the forwarded host differs from the URL Twilio used (tunnel, reverse proxy).
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        url_for_signature = os.getenv("TWILIO_WEBHOOK_URL") or str(request.url)
        if not validator.validate(url_for_signature, dict(form), signature):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")

    body_text = form.get("Body") or ""
    from_num = form.get("From") or ""

    if not isinstance(body_text, str) or not body_text.strip():
        twiml = MessagingResponse()
        twiml.message("Send your packaging question and we’ll help. For uploads, please use the Fortis Edge Portal.")
        return Response(content=str(twiml), media_type="application/xml")

    existing = find_sms_conversation(str(from_num))
    persist_messages = True
    if existing:
        cid = existing
    else:
        cid = ensure_conversation(
            channel="sms",
            channel_ref=str(from_num),
            existing_id=None,
        )
        if not cid:
            logger.error(
                "Twilio webhook: fortis_conversations upsert failed for From=%s; "
                "replying without storing messages (check Supabase logs / schema).",
                from_num,
            )
            cid = str(uuid.uuid4())
            persist_messages = False

    try:
        sms_history = load_recent_messages(cid) if persist_messages else []
        sms_flow = _estimate_flow.handle_estimate_flow(
            user_message=body_text,
            conversation_history=sms_history,
            conversation_id=cid,
        )
        if sms_flow.handled:
            reply_text = sms_flow.reply
            assistant_meta_twilio = sms_flow.assistant_meta
        else:
            reply_text = await run_agent_turn(body_text, conversation_id=cid, augment_knowledge=True)
            reply_text, _estimate_id_saved = await persist_estimate_from_assistant_json(
                reply_text, conversation_id=cid
            )
            assistant_meta_twilio = None
    except Exception:
        logger.exception("Twilio agent failed — sending fallback SMS.")
        reply_text = (
            "We couldn’t reply automatically right now. Please email your Fortis rep or "
            "use the Fortis Edge Portal for status and file uploads."
        )
        assistant_meta_twilio = None

    if persist_messages:
        append_message(cid, role="user", content=body_text)
        append_message(cid, role="assistant", content=reply_text, meta=assistant_meta_twilio)

    sms_body = reply_text
    if len(sms_body) > 1450:
        sms_body = sms_body[:1420].rstrip() + " … See Fortis Edge Portal."

    twiml = MessagingResponse()
    twiml.message(sms_body)

    resp = Response(content=str(twiml), media_type="application/xml")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@router.post("/create-estimate", response_model=None)
async def create_estimate_route(
    request: Request,
    body: EstimateRequest,
    response_mode: Literal["pdf", "json"] = Query(
        "pdf",
        description="pdf = download PDF; json = JSON with base64 + absolute pdf_link",
    ),
) -> StreamingResponse | dict[str, Any]:
    try:
        result = create_estimate(
            payload=body.model_dump(mode="python"),
            include_pdf_base64=(response_mode == "json"),
        )
    except ValidationError as exc:
        logger.info(
            "create_estimate REST validation failed estimate_id_hint=%s errors=%s",
            getattr(body, "estimate_id", None),
            len(exc.errors()),
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Estimate payload failed validation. Use customer + line_items for API, or match create_estimate tool shape.",
                "errors": exc.errors(),
            },
        ) from exc
    except Exception:
        logger.exception("create_estimate unexpected failure.")
        raise HTTPException(
            status_code=500,
            detail="Could not build estimate. Check server logs and payload shape.",
        ) from None

    pdf_url = estimate_pdf_absolute_url(request, str(body.estimate_id))
    result = result.model_copy(update={"pdf_link": pdf_url})

    if response_mode == "json":
        return {
            "estimate": body.model_dump(mode="json"),
            "pricing": result.pricing.model_dump(mode="json"),
            "message": result.message,
            "pdf_base64": result.pdf_base64,
            "pdf_link": result.pdf_link,
        }

    bio = write_estimate_pdf_binary(result)
    filename = f"fortis_estimate_{body.estimate_id}.pdf"
    return StreamingResponse(
        bio,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
