/**
 * Placeholder brand mark until a production logo asset ships.
 */
export function FortisLogoMark() {
  return (
    <div className="flex items-center gap-3">
      <div
        className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-slate-700/40 bg-white text-lg font-semibold uppercase tracking-tight text-slate-900 shadow-inner print:border-stone-300 print:text-stone-900"
        aria-hidden
      >
        F
      </div>
      <div className="leading-tight">
        <p className="text-sm font-semibold tracking-wide text-white print:text-slate-900">Fortis</p>
        <p className="text-xs font-medium text-slate-300 print:text-slate-600">Solutions · Quick Ship</p>
      </div>
    </div>
  );
}
