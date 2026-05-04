import type { QuoteDisplay } from "@/lib/quote/map-estimate-row";
import { formatLongDate, formatQty, formatUsd } from "@/lib/quote/format";

import { FortisLogoMark } from "./FortisLogoMark";
import { PrintActions } from "./PrintActions";

const STANDARD_DISCLAIMERS = (
  <>
    <p className="font-medium text-stone-900">Quote valid for 30 days</p>
    <p className="mt-1 text-stone-700">
      This quote does not include shipping or taxes.
    </p>
  </>
);

export function QuoteDocument({ quote }: { quote: QuoteDisplay }) {
  return (
    <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 sm:py-10 lg:px-8 lg:py-12 print:max-w-none print:px-0 print:py-0">
      <PrintActions />

      <article className="quote-sheet quote-break-inside-avoid overflow-hidden print:rounded-none">
        <header className="border-b border-slate-800/40 bg-gradient-to-br from-slate-900 via-slate-900 to-slate-800 px-5 py-7 text-white shadow-inner sm:px-8 sm:py-8 print:bg-white print:text-slate-900 print:shadow-none">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
            <FortisLogoMark />
            <div className="text-left sm:text-right">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-300 print:text-slate-500">
                Estimate
              </p>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight text-white print:text-slate-900 sm:text-3xl">
                Quote
              </h1>
              <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-300 print:text-slate-500">
                Quote #
              </p>
              <p className="font-mono text-base font-semibold text-white tabular-nums print:text-slate-900 sm:text-lg">
                {quote.id}
              </p>
              {quote.createdAt ? (
                <p className="mt-3 text-xs text-slate-300 print:text-slate-600 sm:text-sm">
                  Issued{" "}
                  <span className="font-medium text-white print:text-slate-900">{formatLongDate(quote.createdAt)}</span>
                </p>
              ) : null}
            </div>
          </div>
        </header>

        <div className="border-b border-amber-200/70 bg-amber-50 px-5 py-4 sm:px-8 print:border-stone-200 print:bg-stone-50">
          <div className="text-center text-sm sm:text-base">{STANDARD_DISCLAIMERS}</div>
        </div>

        <div className="px-5 py-8 sm:px-8">
          <section className="quote-break-inside-avoid grid gap-8 sm:grid-cols-2">
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-stone-500">Bill to</h2>
              <p className="mt-2 text-lg font-semibold text-stone-900">{quote.businessName}</p>
              <p className="mt-1 text-stone-700">{quote.contactName}</p>
              <dl className="mt-4 space-y-2 text-sm text-stone-600">
                <div className="flex flex-wrap gap-x-2 gap-y-1">
                  <dt className="min-w-[3.25rem] font-medium text-stone-500">Email</dt>
                  <dd className="min-w-0 break-all">
                    {quote.email.includes("@") ? (
                      <a
                        href={`mailto:${quote.email}`}
                        className="text-slate-800 underline decoration-stone-300 underline-offset-[3px] hover:decoration-slate-700"
                      >
                        {quote.email}
                      </a>
                    ) : (
                      quote.email
                    )}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt className="min-w-[3.25rem] font-medium text-stone-500">Phone</dt>
                  <dd>{quote.phone}</dd>
                </div>
              </dl>
            </div>
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-stone-500">Ship / billing address</h2>
              <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-stone-800">{quote.address}</p>
              <div className="mt-6 rounded-xl border border-slate-200 bg-slate-50 px-4 py-4 text-sm shadow-sm print:border-stone-200 print:bg-white">
                <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">Pricing valid until</p>
                <p className="mt-1 text-base font-semibold tabular-nums text-stone-900">{formatLongDate(quote.validUntil)}</p>
              </div>
            </div>
          </section>

          <section className="mt-10 quote-break-inside-avoid">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-stone-500">
                Pricing breakdown
              </h2>
              <p className="text-xs text-stone-500 sm:text-right">All figures USD unless noted</p>
            </div>

            {/* Mobile-friendly cards */}
            <div className="mt-4 space-y-3 sm:hidden">
              {quote.lines.map((line, i) => (
                <div
                  key={`${line.sku}-${i}-m`}
                  className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm print:border-stone-300 print:shadow-none"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-mono text-sm font-semibold text-stone-900">{line.sku}</p>
                      <p className="mt-1 text-sm leading-snug text-stone-700">{line.description}</p>
                    </div>
                  </div>
                  <dl className="mt-4 grid grid-cols-3 gap-2 border-t border-stone-100 pt-3 text-sm">
                    <div>
                      <dt className="text-xs font-medium text-stone-500">Qty</dt>
                      <dd className="tabular-nums text-stone-900">{formatQty(line.quantity)}</dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium text-stone-500">Unit</dt>
                      <dd className="tabular-nums text-stone-900">{formatUsd(line.unitPrice)}</dd>
                    </div>
                    <div className="text-right">
                      <dt className="text-xs font-medium text-stone-500">Line total</dt>
                      <dd className="font-semibold tabular-nums text-stone-900">{formatUsd(line.total)}</dd>
                    </div>
                  </dl>
                </div>
              ))}
            </div>

            {/* Desktop table */}
            <div className="quote-table-wrap mt-4 hidden overflow-x-auto rounded-xl border border-stone-200 sm:block">
              <table className="min-w-[640px] w-full divide-y divide-stone-200 text-sm lg:min-w-0">
                <thead className="bg-stone-50 print:bg-white">
                  <tr>
                    <th
                      scope="col"
                      className="whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-stone-600"
                    >
                      SKU
                    </th>
                    <th
                      scope="col"
                      className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-stone-600"
                    >
                      Description
                    </th>
                    <th
                      scope="col"
                      className="whitespace-nowrap px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-stone-600"
                    >
                      Quantity
                    </th>
                    <th
                      scope="col"
                      className="whitespace-nowrap px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-stone-600"
                    >
                      Unit price
                    </th>
                    <th
                      scope="col"
                      className="whitespace-nowrap px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-stone-600"
                    >
                      Line total
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100 bg-white">
                  {quote.lines.map((line, i) => (
                    <tr key={`${line.sku}-${i}`} className="print:border-stone-200">
                      <td className="whitespace-nowrap px-4 py-3 align-top font-mono text-xs text-stone-900 sm:text-sm">
                        {line.sku}
                      </td>
                      <td className="max-w-[18rem] px-4 py-3 align-top leading-snug text-stone-700 lg:max-w-none">
                        {line.description}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right align-top tabular-nums text-stone-800">
                        {formatQty(line.quantity)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right align-top tabular-nums text-stone-800">
                        {formatUsd(line.unitPrice)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right align-top text-sm font-semibold tabular-nums text-stone-900">
                        {formatUsd(line.total)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="mt-8 quote-break-inside-avoid flex flex-col items-stretch gap-4 sm:items-end">
            <div className="sm:ml-auto sm:w-full sm:max-w-sm">
              <div className="rounded-xl border border-slate-900/15 bg-gradient-to-b from-white to-stone-50 px-6 py-5 shadow-sm print:border-stone-200 print:bg-white print:shadow-none">
                <div className="flex items-center justify-between gap-6">
                  <span className="text-sm font-semibold text-stone-600">Subtotal</span>
                  <span className="text-xl font-semibold tabular-nums tracking-tight text-slate-900">
                    {quote.subtotal != null ? formatUsd(quote.subtotal) : "Contact your rep"}
                  </span>
                </div>
              </div>
            </div>
          </section>

          <section className="mt-10 border-t border-stone-200 pt-8 quote-break-inside-avoid">
            <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-stone-500">Notes &amp; policy</h2>
            <div className="mt-3 rounded-lg bg-stone-50 px-4 py-4 text-sm leading-relaxed text-stone-800 print:bg-white print:px-0">
              {quote.notes.split(/\n+/).filter(Boolean).map((para, idx) => (
                <p key={idx} className={idx === 0 ? "" : "mt-3"}>
                  {para}
                </p>
              ))}
              <div className="mt-4 border-t border-stone-200 pt-4 text-xs text-stone-600">{STANDARD_DISCLAIMERS}</div>
            </div>
          </section>

          <footer className="mt-10 border-t border-stone-100 pt-6 text-center text-xs leading-relaxed text-stone-500">
            Fortis Solutions — Quick Ship label quote · Reference only; final invoicing confirmed at order acceptance.
          </footer>
        </div>
      </article>
    </div>
  );
}
