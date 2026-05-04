-- Align legacy fortis_conversations rows with fortis_cs_agent/api.py expectations.
--
-- Run in Supabase → SQL Editor if PostgREST returns:
--   PGRST204 Could not find the 'channel' column of 'fortis_conversations'
--
-- Safe to run more than once (IF NOT EXISTS / guarded constraint).

ALTER TABLE fortis_conversations ADD COLUMN IF NOT EXISTS channel text;
ALTER TABLE fortis_conversations ADD COLUMN IF NOT EXISTS channel_ref text;
ALTER TABLE fortis_conversations ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
ALTER TABLE fortis_conversations ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

UPDATE fortis_conversations SET channel = 'web' WHERE channel IS NULL;
UPDATE fortis_conversations SET channel_ref = coalesce(channel_ref, '') WHERE channel_ref IS NULL;

ALTER TABLE fortis_conversations ALTER COLUMN channel SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fortis_conversations_channel_check'
  ) THEN
    ALTER TABLE fortis_conversations
      ADD CONSTRAINT fortis_conversations_channel_check
      CHECK (channel IN ('sms', 'web', 'api'));
  END IF;
END $$;

-- PostgREST often picks up new columns within seconds. If stale, reload the project or wait briefly.
