export function formatUsd(amount: number | null | undefined): string {
  if (amount == null || Number.isNaN(amount)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(amount);
}

export function formatQty(qty: number): string {
  if (!Number.isFinite(qty)) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(qty);
}

export function formatLongDate(d: Date): string {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "long",
    timeZone: "UTC",
  }).format(d);
}
