"use client";

export function PrintActions() {
  return (
    <div className="quote-no-print mb-6 flex flex-wrap items-center justify-end gap-3">
      <button
        type="button"
        onClick={() => window.print()}
        className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2"
      >
        Print / Save PDF
      </button>
    </div>
  );
}
