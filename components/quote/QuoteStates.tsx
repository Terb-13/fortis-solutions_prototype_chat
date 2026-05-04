import Link from "next/link";

export function QuoteConfigurationError() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-16">
      <div className="max-w-md rounded-lg border border-amber-200 bg-amber-50 px-6 py-8 text-center shadow-sm">
        <h1 className="text-lg font-semibold text-amber-950">Quote pages not configured</h1>
        <p className="mt-2 text-sm text-amber-900">
          Set <code className="rounded bg-amber-100 px-1 py-0.5 font-mono text-xs">NEXT_PUBLIC_SUPABASE_URL</code>{" "}
          and{" "}
          <code className="rounded bg-amber-100 px-1 py-0.5 font-mono text-xs">
            NEXT_PUBLIC_SUPABASE_ANON_KEY
          </code>{" "}
          for this app, and add a Supabase policy that allows anonymous read of estimates by{" "}
          <code className="font-mono text-xs">id</code>.
        </p>
      </div>
    </div>
  );
}

export function QuoteFetchError({ message }: { message: string }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-16">
      <div className="max-w-lg rounded-lg border border-red-200 bg-red-50 px-6 py-8 shadow-sm">
        <h1 className="text-lg font-semibold text-red-950">Could not load quote</h1>
        <p className="mt-2 text-sm text-red-900">
          This may be a temporary network issue, or Row Level Security may be blocking public reads. Ask your
          admin to confirm an anon SELECT policy on <span className="font-mono">fortis_estimates</span>.
        </p>
        <p className="mt-4 rounded-md bg-white/80 px-3 py-2 font-mono text-xs text-red-800">{message}</p>
        <p className="mt-6">
          <Link
            href="/"
            className="text-sm font-medium text-slate-800 underline decoration-slate-300 underline-offset-2 hover:decoration-slate-600"
          >
            Back home
          </Link>
        </p>
      </div>
    </div>
  );
}
