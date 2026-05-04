import type { QuoteDisplay } from "@/lib/quote/map-estimate-row";
import { formatLongDate, formatQty, formatUsd } from "@/lib/quote/format";

import { PrintActions } from "./PrintActions";

export function QuoteDocument({ quote }: { quote: QuoteDisplay }) {
  return (
    <div className="px-4 py-8 sm:px-6 lg:px-8">
      <PrintActions />

      <article className="quote-sheet quote-break-inside-avoid overflow-hidden">
        <header className="border-b border-stone-200 bg-slate-900 px-8 py-8 text-white print:bg-white print:text-slate-900">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-300 print:text-slate-500">
                Fortis Edge
              </p>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">Quote</h1>
            </div>
            <div className="text-sm sm:text-right">
              <p className="text-slate-300 print:text-slate-600">Quote #</p>
              <p className="font-mono text-lg font-medium text-white print:text-slate-900">{quote.id}</p>
              {quote.createdAt ? (
                <p className="mt-2 text-slate-300 print:text-slate-600">
                  Issued{" "}
                  <span className="text-white print:text-slate-900">{formatLongDate(quote.createdAt)}</span>
                </p>
              ) : null}
            </div>
          </div>
        </header>

        <div className="px-8 py-8">
          <section className="quote-break-inside-avoid grid gap-8 sm:grid-cols-2">
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-500">Bill to</h2>
              <p className="mt-2 text-lg font-semibold text-stone-900">{quote.businessName}</p>
              <p className="mt-1 text-stone-800">{quote.contactName}</p>
              <dl className="mt-4 space-y-1 text-sm text-stone-600">
                <div className="flex gap-2">
                  <dt className="w-14 shrink-0 font-medium text-stone-500">Email</dt>
                  <dd>
                    {quote.email.includes("@") ? (
                      <a
                        href={`mailto:${quote.email}`}
                        className="text-slate-800 underline decoration-stone-300 underline-offset-2 hover:decoration-slate-600"
                      >
                        {quote.email}
                      </a>
                    ) : (
                      quote.email
                    )}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-14 shrink-0 font-medium text-stone-500">Phone</dt>
                  <dd>{quote.phone}</dd>
                </div>
              </dl>
            </div>
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-500">Ship / address</h2>
              <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-stone-800">{quote.address}</p>
              <div className="mt-6 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950 print:border-stone-300 print:bg-stone-50 print:text-stone-800">
                <p className="font-medium text-amber-900 print:text-stone-900">Valid until</p>
                <p className="mt-1 font-semibold">{formatLongDate(quote.validUntil)}</p>
              </div>
            </div>
          </section>

          <section className="mt-10">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-500">Line items</h2>
            <div className="mt-4 overflow-x-auto rounded-lg border border-stone-200">
              <table className="min-w-full divide-y divide-stone-200 text-sm">
                <thead className="bg-stone-50 print:bg-white">
                  <tr>
                    <th
                      scope="col"
                      className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-stone-600"
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
                      className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-stone-600"
                    >
                      Qty
                    </th>
                    <th
                      scope="col"
                      className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-stone-600"
                    >
                      Unit price
                    </th>
                    <th
                      scope="col"
                      className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-stone-600"
                    >
                      Total
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100 bg-white">
                  {quote.lines.map((line, i) => (
                    <tr key={`${line.sku}-${i}`} className="hover:bg-stone-50/80">
                      <td className="whitespace-nowrap px-4 py-3 font-mono text-stone-900">{line.sku}</td>
                      <td className="max-w-xs px-4 py-3 text-stone-700">{line.description}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-stone-800">
                        {formatQty(line.quantity)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-stone-800">
                        {formatUsd(line.unitPrice)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right font-medium tabular-nums text-stone-900">
                        {formatUsd(line.total)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="mt-8 flex flex-col items-end gap-4 quote-break-inside-avoid">
            <div className="w-full max-w-sm rounded-lg border border-stone-200 bg-stone-50 px-6 py-4 print:bg-white">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium text-stone-600">Subtotal</span>
                <span className="text-lg font-semibold tabular-nums text-stone-900">
                  {quote.subtotal != null ? formatUsd(quote.subtotal) : "See PDF / formal proposal"}
                </span>
              </div>
            </div>
          </section>

          <section className="mt-10 border-t border-stone-200 pt-8">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-500">Notes</h2>
            <div className="mt-3 space-y-3 text-sm leading-relaxed text-stone-700">
              {quote.notes.split(/\n+/).map((para, idx) => (
                <p key={idx}>{para}</p>
              ))}
            </div>
          </section>

          <footer className="mt-10 border-t border-stone-100 pt-6 text-center text-xs text-stone-500">
            Fortis Edge · Quote reference only · Pricing subject to final confirmation.
          </footer>
        </div>
      </article>
    </div>
  );
}
