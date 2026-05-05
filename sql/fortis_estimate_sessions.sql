-- Quick Ship estimate wizard: durable session per conversation (Supabase).
-- Run in Supabase SQL Editor after fortis_conversations exists.
--
-- PostgREST: grant service_role access; enable RLS with no policies for anon if you only use service key.

CREATE TABLE IF NOT EXISTS fortis_estimate_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES fortis_conversations (id) ON DELETE CASCADE,
  current_step smallint NOT NULL DEFAULT 1
    CHECK (current_step >= 1 AND current_step <= 5),
  collected_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'in_progress'
    CHECK (status IN ('in_progress', 'paused', 'completed', 'abandoned')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fortis_estimate_sessions_conversation_id_key UNIQUE (conversation_id)
);

CREATE INDEX IF NOT EXISTS fortis_estimate_sessions_status_idx
  ON fortis_estimate_sessions (status);

CREATE INDEX IF NOT EXISTS fortis_estimate_sessions_updated_at_idx
  ON fortis_estimate_sessions (updated_at DESC);

-- Application updates ``updated_at`` on each upsert. Add a DB trigger later if desired.
