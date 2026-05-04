export default function QuoteLoading() {
  return (
    <div className="min-h-screen px-4 py-10 sm:px-8">
      <div className="mx-auto max-w-4xl animate-pulse">
        <div className="mb-6 flex justify-end">
          <div className="h-10 w-40 rounded-md bg-stone-200" />
        </div>
        <div className="overflow-hidden rounded-lg border border-stone-200 bg-white shadow-sm">
          <div className="h-36 bg-stone-300" />
          <div className="space-y-6 px-8 py-8">
            <div className="grid gap-8 sm:grid-cols-2">
              <div className="space-y-3">
                <div className="h-3 w-24 rounded bg-stone-200" />
                <div className="h-6 w-48 rounded bg-stone-200" />
                <div className="h-4 w-full max-w-xs rounded bg-stone-100" />
              </div>
              <div className="space-y-3">
                <div className="h-3 w-28 rounded bg-stone-200" />
                <div className="h-20 w-full rounded bg-stone-100" />
              </div>
            </div>
            <div className="h-48 rounded-lg bg-stone-100" />
            <div className="ml-auto h-16 max-w-sm rounded-lg bg-stone-100" />
          </div>
        </div>
      </div>
    </div>
  );
}
