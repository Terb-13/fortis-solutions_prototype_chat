import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Shared Supabase client for the Next.js app (browser-safe anon key only).
 * Instantiate per request on the server to avoid stale config in dev.
 *
 * Backend Python services continue using SUPABASE_SERVICE_ROLE_KEY server-side only.
 */
export function createSupabaseClient(): SupabaseClient {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "Missing NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY for quote pages.",
    );
  }
  return createClient(url, anonKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
    },
  });
}
