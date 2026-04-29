# Fortis Edge CS Agent

Production-style FastAPI backend for the **Fortis Edge** customer success agent: **xAI Grok** chat with tool calling, **Twilio** SMS webhook, structured **`create_estimate`** packaging quotes with **PDF** output, and **Supabase** conversation persistence plus optional knowledge retrieval.

## Features

| Area | Endpoint / module | Notes |
|------|-------------------|--------|
| Chat | `POST /chat` | Persistent thread via `conversation_id`; Grok + `create_estimate` tool |
| Dry run | `POST /test-chat` | Same model/tools; no Supabase writes |
| SMS | `POST /twilio-webhook` | Form-encoded webhook; binds thread by caller `From` |
| Estimate API | `POST /create-estimate` | PDF download or JSON + `pdf_base64` |
| Health | `GET /health` | Config flags (does not ping external APIs) |

Default Supabase URL is `https://vapnbelrpaxeafospalc.supabase.co` (override with env).

SMS support line referenced in prompts: **(801) 459-0886**.

## Prerequisites

- Python 3.11+
- Accounts: xAI API key, Supabase project, Twilio **(optional)** for SMS

## Local setup

```bash
cd fortis-chat
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: XAI_API_KEY, SUPABASE_SERVICE_ROLE_KEY, Twilio vars as needed.
```

Run the API:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## Environment variables

See `.env.example`. Important keys:

| Variable | Purpose |
|---------|---------|
| `XAI_API_KEY` | **Required** for `/chat`, `/test-chat`, SMS agent turns |
| `XAI_CHAT_MODEL` | Defaults to `grok-2-latest` |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Persistence + knowledge table (optional if unset) |
| `TWILIO_*` | SMS; webhook still returns XML without Twilio if only chat is used |
| `TWILIO_VALIDATE_SIGNATURE` | Set `true` in production with correct public URL for signature validation |

## Supabase schema

Create tables (SQL editor or migration) so chat persistence works when `SUPABASE_SERVICE_ROLE_KEY` is set:

```sql
-- Conversations (web, SMS, API)
create table if not exists fortis_conversations (
  id uuid primary key,
  channel text not null check (channel in ('sms', 'web', 'api')),
  channel_ref text default '',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists fortis_conversations_sms
  on fortis_conversations (channel, channel_ref)
  where channel = 'sms';

-- Messages
create table if not exists fortis_messages (
  id bigserial primary key,
  conversation_id uuid not null references fortis_conversations(id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system', 'tool')),
  content text default '',
  tool_name text,
  meta jsonb,
  created_at timestamptz default now()
);

create index if not exists fortis_messages_conv_time
  on fortis_messages (conversation_id, created_at);

-- Optional knowledge for retrieve_knowledge_snippets()
create table if not exists fortis_knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  title text,
  body text not null,
  tags text[],
  updated_at timestamptz default now()
);
```

Row Level Security: lock these down to service role or add policies for your app; the backend uses the **service role** key server-side only.

## API quick reference

**Chat**

```http
POST /chat
Content-Type: application/json

{"message": "Need 500 RSC shippers 12x10x8", "conversation_id": null}
```

**Test (no DB)**

```http
POST /test-chat
Content-Type: application/json

{"message": "Hello"}
```

**Create estimate (PDF)**

```http
POST /create-estimate?response_mode=pdf
Content-Type: application/json
```

Body: JSON matching `EstimateRequest` (customer, line_items with `product_type` enum, optional dimensions, turnaround, etc.).

**JSON + base64 PDF**

```http
POST /create-estimate?response_mode=json
```

**Twilio**

Configure the Twilio number’s **A Message Comes In** webhook to `POST https://<your-host>/twilio-webhook` (HTTP POST, form body).

## Deploy (Vercel)

`vercel.json` builds `main.py` with `@vercel/python`. Set environment variables in the Vercel project. For Twilio signature validation, use the **public** HTTPS URL Twilio calls.

## Project layout

```
fortis-chat/
├── main.py
├── vercel.json
├── requirements.txt
├── .env.example
├── README.md
└── fortis_cs_agent/
    ├── __init__.py
    ├── api.py          # Routes, Grok client, Supabase helpers
    ├── prompts.py
    ├── tools.py        # create_estimate + tool schema
    ├── estimate_models.py
    ├── estimate_pdf.py
    └── knowledge.py
```

## License

Proprietary — Fortis Edge internal use unless otherwise specified.
