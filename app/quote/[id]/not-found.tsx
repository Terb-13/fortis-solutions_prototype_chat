import Link from "next/link";

export default function QuoteNotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-16">
      <div className="max-w-md text-center">
        <p className="text-sm font-semibold uppercase tracking-wide text-stone-500">Fortis Edge</p>
        <h1 className="mt-2 text-2xl font-semibold text-stone-900">Quote not found</h1>
        <p className="mt-3 text-sm leading-relaxed text-stone-600">
          We couldn&apos;t find an estimate with this link. It may have been removed, or the ID may be incorrect.
        </p>
        <Link
          href="/"
          className="mt-8 inline-flex rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          Go back
        </Link>
      </div>
    </div>
  );
}
