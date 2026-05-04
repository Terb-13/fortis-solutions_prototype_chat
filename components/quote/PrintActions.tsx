"use client";

export function PrintActions() {
  const handlePrint = () => window.print();

  return (
    <div className="quote-no-print mb-8 flex flex-col gap-3 sm:mb-6 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
      <p className="text-sm text-stone-600 sm:max-w-md">
        Need a PDF? Use your browser&rsquo;s print dialog — choose <strong>Save as PDF</strong> where available.
      </p>
      <button
        type="button"
        onClick={handlePrint}
        className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 active:bg-slate-950 sm:min-h-[44px] sm:w-auto print:hidden"
      >
        Print / Download PDF
      </button>
    </div>
  );
}
