# fortis-chatbot — Working Context

The FastAPI backend for Fortis Edge. All AI logic, conversation persistence, estimate creation, pricing lookup, and Twilio integration live here.

For project-wide context, see `../CLAUDE.md`. For full architecture, see `../docs/02_technical-architecture.md`.

## What This Repo Does

- Serves `/chat`, `/test-chat`, `/twilio-webhook`, `/create-estimate`, `/health` endpoints
- Houses the conversational AI agent (`fortis_cs_agent/`) — knowledge retrieval, tool use, prompt management
- Implements the 5-step estimate wizard as a deterministic state machine (`estimate_flow.py`)
- Performs closest-match pricing lookup against `fortis_pricing` in Supabase
- Persists conversations, messages, sessions, and estimates to Supabase
- Generates PDF estimates via `fpdf2`

## Tech Stack

| Layer | Tech |
|-------|------|
| Framework | FastAPI 0.115 |
| Language | Python 3.11+ |
| HTTP | `httpx` |
| Validation | `pydantic` |
| DB client | `supabase` Python SDK |
| AI client | OpenAI-compatible client pointed at xAI endpoint |
| Telephony | `twilio` SDK |
| PDF | `fpdf2` |
| Tests | pytest |
| Hosting | Vercel (migrating to Railway) |

## Repo Map

```
main.py                          # FastAPI entry point. Registers fortis_cs_agent.api router.
fortis_cs_agent/                 # ~5,200 LoC — the agent core
  api.py                         # Route definitions
  estimate_flow.py               # ~1,200 LoC — wizard state machine
  ...                            # knowledge retrieval, tools, prompts, Grok client
sql/                             # Supabase schema files (canonical status TBD — see Known Issues)
scripts/                         # Utility scripts
tests/                           # 7 pytest files
.github/workflows/               # CI

# Legacy — slated for removal (see §6.2 of architecture doc)
app/                             # ⚠️ Legacy Next.js code — do not modify
components/                      # ⚠️ Legacy
lib/                             # ⚠️ Legacy
package.json, package-lock.json  # ⚠️ Legacy
next.config.*, next-env.d.ts     # ⚠️ Legacy
```

## Build / Dev / Test

```bash
# Setup (use venv)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Local dev server (port 8000)
uvicorn main:app --reload --port 8000

# Run tests
pytest

# Run a specific test file
pytest tests/test_<name>.py -v
```

When testing, the chat backend can be exercised independently via `/chat`, but most useful end-to-end testing requires the frontend running too (typically port 3000) with `FORTIS_CHAT_BACKEND_URL=http://localhost:8000`.

## Environment Variables

Required for local dev (in `.env`) and prod (Vercel/Railway):

- `SUPABASE_URL` — project URL
- `SUPABASE_SERVICE_ROLE_KEY` — service role for backend writes (NOT the anon key)
- `XAI_API_KEY` — xAI Grok API key
- `GROK_MODEL` — model name (env-configurable, no hard-coded names anywhere in code)
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` — for SMS/voice webhooks
- See `.env.example` for the full list (~40 keys)

The `.env.example` file in this repo is comprehensive — start there.

## Critical Files

These are the highest-stakes files in the repo. Treat changes here with extra care.

### `fortis_cs_agent/estimate_flow.py` (~1,200 LoC)
The wizard state machine. Steps: size → material → color → finish → quantity. State persists in `fortis_estimate_sessions` (JSONB `collected_data`). Topic-switching detection lives here too.

**Known issue:** wizard occasionally loses place after topic switches. Inconsistent reproduction. This is the #2 priority on the punch list.

### Closest-match pricing logic (in `fortis_cs_agent/`)
**This is the highest-value business logic in the codebase.** When the customer's exact spec isn't in `fortis_pricing`, the system finds the nearest available combination and recommends it (e.g., 2x3 white BOPP CMYK 5,000 → 2.5x3 vinyl CMYK 5,000).

This logic must:
- Be reliable (it's what makes the demo land)
- Be explainable (the bot must communicate *why* the substitution)
- Be conservative (when no good match exists, fall back gracefully — don't recommend something materially different)
- Have dedicated tests for known-good and known-bad cases

**Known status:** assumed working but needs full verification. This is the #1 priority on the punch list.

### `fortis_cs_agent/api.py`
Route definitions. Don't put business logic here — it should be thin, calling into the agent modules.

### `sql/`
Contains schema files for Supabase. **Important caveat:** `fortis-frontend/supabase/` also contains migration-style files. Whether `sql/` is the canonical source of truth or whether the two have drifted has not been verified. Before making any schema changes, check both folders and confirm the current state with Brett. See architecture doc §11 item 13.

## Conventions

- **Type everything.** Pydantic models for inputs/outputs. No bare dicts crossing function boundaries.
- **Async I/O.** FastAPI is async; use `httpx.AsyncClient`, async Supabase calls, await the AI client.
- **Tools are explicit.** Agent tools (pricing lookup, estimate creation, etc.) are defined as functions with clear schemas, not embedded prompts.
- **Prompts versioned.** Don't edit prompts inline in handlers — they belong in `fortis_cs_agent/` prompt modules.
- **Logging over prints.** Use the standard `logging` module with a logger per module (`logger = logging.getLogger(__name__)`).
- **Idempotency.** Webhook handlers (Twilio) and chat handlers should tolerate duplicate calls. Twilio retries on timeout.
- **Service role key is server-only.** Never returned in responses, never logged in full, never exposed to the frontend.

## Known Issues (current, prioritized)

1. **Closest-match pricing** — needs explicit verification and test coverage. Priority: highest.
2. **Wizard state inconsistency** — bot loses place after topic switches. Priority: high.
3. **Duplicate messages** — bot occasionally responds twice. Cause unclear; investigate post-Railway migration. Priority: high.
4. **Vercel hosting limitations** — driving the Railway migration. Voice will require Railway anyway.
5. **Legacy Next.js code** — `app/`, `components/`, `lib/`, `package.json`, etc. are all dead and should be removed.
6. **Two PDF libraries in the project** — keep `fpdf2`, drop `@react-pdf/renderer` from frontend.

## Things Not To Touch

- **Don't enable Supabase RLS** without explicit instruction. It's currently disabled because the backend is the only writer; turning it on now will break things until policies are written. RLS is on the Phase 2 punch list, not now.
- **Don't hardcode the Grok model name.** It's `GROK_MODEL` env var. Several places in code rely on this — keep it that way.
- **Don't write new code in the legacy Next.js folders** (`app/`, `components/`, `lib/`, etc.). They're being deleted.
- **Don't commit secrets.** `.env` is gitignored; `.env.example` is the template.
- **Don't introduce new AI providers without discussion.** xAI Grok (chat + voice) is the standard.

## Phase 1 Voice Port (Stretch Goal)

The architecture for voice is proven — there's a working standalone voice prototype using the same FastAPI + Twilio Media Streams + xAI Grok pattern. When porting to Fortis:

- Replace existing prompts with Fortis prompts
- Point Supabase calls at the Fortis project
- Confirm xAI Grok voice access via the OpenAI-compatible client
- Deploy to Railway
- Configure Twilio voice number webhook
- Add the email-the-estimate enhancement (planned post-call action)

The voice prototype's monorepo structure (`packages/core` + `apps/voice-sms`) is the long-term target architecture for this repo too — see §8 of the architecture doc.

## Voice / SMS Notes

- SMS endpoint exists (`/twilio-webhook`) but the phone number is awaiting Twilio approval. Code is ready; deployment is blocked on procurement.
- Voice port is a Phase 0/1 stretch goal. Make a clear go/no-go decision before any ELT-facing demo.
- Both will eventually run on the same Railway-hosted backend (or its monorepo successor).

## When To Reach for the Frontend Repo

If a task involves any of the following, you're probably in the wrong repo:

- React components, Tailwind styling, Next.js routes, or page layouts
- The chat widget UI or admin dashboard UI
- Public quote rendering at `/quote/[id]`
- Marketing pages or copy

Switch to `../fortis-frontend/` and read its `CLAUDE.md`.
