"""
HTTP routes for the Fortis CS agent: Grok chat, Twilio SMS, estimates, health.

Logging and error handling mirror a typical production FastAPI service.
"""

from __future__ import annotations

import json
import logging
import os
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
from fortis_cs_agent.knowledge import retrieve_knowledge
from fortis_cs_agent.prompts import SYSTEM_PROMPT
from fortis_cs_agent.store import load_estimate_snapshot
from fortis_cs_agent.tools import AGENT_TOOLS, assemble_estimate_result, create_estimate, execute_agent_tool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["fortis"])

XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_CHAT_MODEL = (os.getenv("XAI_CHAT_MODEL") or "").strip() or "grok-4"
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


def ensure_conversation(
    *,
    channel: Literal["sms", "web", "api"],
    channel_ref: str | None,
    existing_id: str | None,
) -> str | None:
    """Create or reuse a conversation row; returns UUID string or None when DB unavailable."""
    client = _sb()
    if client is None:
        return existing_id or str(uuid.uuid4())

    conv_id = existing_id or str(uuid.uuid4())
    try:
        if existing_id:
            return existing_id

        row = {
            "id": conv_id,
            "channel": channel,
            "channel_ref": channel_ref or "",
        }
        client.table(CONV_TABLE).upsert(row).execute()
        return conv_id
    except Exception:
        logger.exception("ensure_conversation failed; using ephemeral id.")
        return conv_id


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
    except Exception:
        logger.exception("append_message skipped for conversation_id=%s", conversation_id)


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
    body: dict[str, Any] = {
        "model": XAI_CHAT_MODEL,
        "messages": messages,
        "temperature": 0.35,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            upstream = _parse_grok_error_body(r)
            logger.error(
                "Grok API HTTP %s model=%s upstream=%s",
                r.status_code,
                XAI_CHAT_MODEL,
                upstream.replace("\n", " ")[:1200],
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Grok API error ({r.status_code}, model={XAI_CHAT_MODEL}): {upstream}. "
                    "Check XAI_API_KEY and model access; set XAI_CHAT_MODEL to a model your key supports."
                ),
            )
        return r.json()


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
        msgs.append({"role": role, "content": content})
    return msgs


async def run_agent_turn(
    user_text: str,
    *,
    conversation_id: str,
    augment_knowledge: bool = True,
) -> str:
    """Single user message → assistant reply with tool loop."""
    msgs: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    prev = load_recent_messages(conversation_id, limit=30)
    msgs.extend(_sanitize_history(prev))
    knowledge_context = ""
    if augment_knowledge:
        knowledge_results = retrieve_knowledge(user_text, limit=4)
        knowledge_context = "\n\n".join([r["content"] for r in knowledge_results])

    augmented = user_text
    if knowledge_context:
        augmented = "Internal knowledge:\n" + knowledge_context + "\n\nCustomer message:\n" + user_text
    msgs.append({"role": "user", "content": augmented})

    tools_list = AGENT_TOOLS

    grok_msgs = [*msgs]

    while True:
        try:
            data = await _grok_chat(grok_msgs, tools=tools_list)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Grok invocation failed in run_agent_turn (conversation_id=%s).", conversation_id)
            raise HTTPException(status_code=502, detail="Upstream model unavailable.") from None

        choice = data["choices"][0]["message"]
        tc = choice.get("tool_calls") or []
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        assistant_msg.update(choice)

        grok_msgs.append(assistant_msg)

        if tc:
            for call in tc:
                fname = call.get("function", {}).get("name")
                args_raw = call.get("function", {}).get("arguments") or "{}"
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    logger.warning("tool_call_invalid_json tool=%s raw=%s", fname, args_raw[:200])
                    args = {}

                payload = execute_agent_tool(fname or "", args, persist_estimate=True)
                grok_msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", "call"),
                        "content": json.dumps(payload),
                    }
                )
            continue

        content = choice.get("content") or ""
        return content.strip() or "(No response text from model.)"


# --- Request models -------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str


# --- Routes ---------------------------------------------------------------------------

@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "fortis-cs-agent",
        "grok_configured": bool(XAI_API_KEY),
        "grok_model": XAI_CHAT_MODEL,
        "supabase_configured": supabase is not None,
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
    cid = ensure_conversation(
        channel="web",
        channel_ref=None,
        existing_id=req.conversation_id,
    )
    if not cid:
        raise HTTPException(status_code=500, detail="Could not allocate conversation.")

    try:
        reply = await run_agent_turn(req.message, conversation_id=cid)
    except HTTPException:
        raise
    except Exception:
        logger.exception("chat pipeline failed (conversation_id=%s)", cid)
        raise HTTPException(status_code=500, detail="Chat failed.") from None

    append_message(cid, role="user", content=req.message)
    append_message(cid, role="assistant", content=reply)
    return ChatResponse(reply=reply, conversation_id=cid)


@router.get("/test-chat", response_model=ChatResponse)
async def test_chat(
    message: str = Query(
        ...,
        min_length=1,
        description="Ephemeral Grok probe (same tools as POST /chat, no persistence).",
    ),
) -> ChatResponse:
    """GET convenience for probes and scripted checks — no Supabase writes."""
    if not XAI_API_KEY:
        raise HTTPException(status_code=503, detail="XAI_API_KEY is not configured.")

    cid = str(uuid.uuid4())
    msgs: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
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
                payload = execute_agent_tool(fname or "", args, persist_estimate=False)
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
    cid = existing or ensure_conversation(
        channel="sms",
        channel_ref=str(from_num),
        existing_id=None,
    )
    if not cid:
        cid = str(uuid.uuid4())

    try:
        reply_text = await run_agent_turn(body_text, conversation_id=cid, augment_knowledge=True)
    except Exception:
        logger.exception("Twilio agent failed — sending fallback SMS.")
        reply_text = (
            "We couldn’t reply automatically right now. Please email your Fortis rep or "
            "use the Fortis Edge Portal for status and file uploads."
        )

    append_message(cid, role="user", content=body_text)
    append_message(cid, role="assistant", content=reply_text)

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
