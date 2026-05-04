import { notFound } from "next/navigation";

import { QuoteDocument } from "@/components/quote/QuoteDocument";
import { QuoteConfigurationError, QuoteFetchError } from "@/components/quote/QuoteStates";
import { mapEstimateRow } from "@/lib/quote/map-estimate-row";
import { createSupabaseClient } from "@/lib/supabase/client";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type PageProps = {
  params: { id: string };
};

export default async function QuotePage({ params }: PageProps) {
  const id = params.id?.trim() ?? "";
  if (!UUID_RE.test(id)) {
    notFound();
  }

  let supabase: ReturnType<typeof createSupabaseClient>;
  try {
    supabase = createSupabaseClient();
  } catch {
    return <QuoteConfigurationError />;
  }

  const { data, error } = await supabase.from("fortis_estimates").select("*").eq("id", id).maybeSingle();

  if (error) {
    return <QuoteFetchError message={error.message} />;
  }

  const quote = mapEstimateRow(data as Record<string, unknown> | null);
  if (!quote) {
    notFound();
  }

  return (
    <main className="min-h-dvh bg-stone-100 print:min-h-0 print:bg-white">
      <QuoteDocument quote={quote} />
    </main>
  );
}
