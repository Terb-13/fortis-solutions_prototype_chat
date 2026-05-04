export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-4">
      <div className="max-w-lg text-center">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-stone-500">Fortis Edge</p>
        <h1 className="mt-3 text-2xl font-semibold text-stone-900">Customer success agent</h1>
        <p className="mt-4 text-sm leading-relaxed text-stone-600">
          Chat and APIs are served by the FastAPI backend. Saved quotes open under{" "}
          <code className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-xs">/quote/&lt;id&gt;</code>.
        </p>
        <p className="mt-8 text-xs text-stone-500">
          Use the UUID from your Supabase <span className="font-mono">fortis_estimates</span> row in the URL.
        </p>
      </div>
    </main>
  );
}
