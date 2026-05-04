const DEFAULT_NOTES =
  "This quote does not include shipping or taxes. Prices are valid for 30 days.";

export type QuoteLine = {
  sku: string;
  description: string;
  quantity: number;
  unitPrice: number | null;
  total: number | null;
};

export type QuoteDisplay = {
  id: string;
  businessName: string;
  contactName: string;
  email: string;
  phone: string;
  address: string;
  lines: QuoteLine[];
  subtotal: number | null;
  notes: string;
  validUntil: Date;
  createdAt: Date | null;
};

function parseDate(value: unknown): Date | null {
  if (value == null) return null;
  if (value instanceof Date && !Number.isNaN(value.valueOf())) return value;
  const s = String(value);
  const d = new Date(s);
  return Number.isNaN(d.valueOf()) ? null : d;
}

function addDays(d: Date, days: number): Date {
  const x = new Date(d);
  x.setUTCDate(x.getUTCDate() + days);
  return x;
}

function isStructuredRow(row: Record<string, unknown>): boolean {
  return (
    typeof row.business_name === "string" ||
    (Array.isArray(row.items) &&
      row.items.length > 0 &&
      typeof (row.items as Record<string, unknown>[])[0]?.sku === "string")
  );
}

function mapStructured(row: Record<string, unknown>, id: string): QuoteDisplay {
  const items = (Array.isArray(row.items) ? row.items : []) as Record<string, unknown>[];
  const lines: QuoteLine[] = items.map((it) => ({
    sku: String(it.sku ?? "—"),
    description: String(it.description ?? "—"),
    quantity: Number(it.quantity ?? 0),
    unitPrice: it.unit_price != null ? Number(it.unit_price) : null,
    total: it.total != null ? Number(it.total) : null,
  }));

  const rawSub = row.subtotal;
  const subtotal =
    rawSub != null && rawSub !== ""
      ? Number(rawSub)
      : lines.reduce((sum, li) => sum + (li.total ?? 0), 0);

  const notes =
    typeof row.notes === "string" && row.notes.trim()
      ? row.notes.trim()
      : DEFAULT_NOTES;

  const createdAt = parseDate(row.created_at);
  const explicitValid = parseDate(row.valid_until);
  const validUntil =
    explicitValid ??
    (createdAt ? addDays(createdAt, 30) : addDays(new Date(), 30));

  return {
    id,
    businessName: String(row.business_name ?? "—"),
    contactName: String(row.contact_name ?? "—"),
    email: String(row.email ?? "—"),
    phone: String(row.phone ?? "").trim() || "—",
    address: String(row.address ?? "").trim() || "—",
    lines,
    subtotal: Number.isFinite(subtotal) ? subtotal : null,
    notes,
    validUntil,
    createdAt,
  };
}

function legacyLineDescription(li: Record<string, unknown>): string {
  const parts = [
    li.board,
    li.finish,
    li.colors,
    li.product_type,
    li.width_in != null ? `W ${li.width_in}"` : null,
    li.length_in != null ? `H ${li.length_in}"` : null,
  ].filter(Boolean);
  const joined = parts.map(String).join(" · ");
  return joined || String(li.name ?? "Line item");
}

function mapLegacyPayload(payload: Record<string, unknown>, rowId: string): QuoteDisplay {
  const customer = (payload.customer ?? {}) as Record<string, unknown>;
  const lineItems = (Array.isArray(payload.line_items) ? payload.line_items : []) as Record<
    string,
    unknown
  >[];

  const lines: QuoteLine[] = lineItems.map((li) => ({
    sku: String(li.name ?? "—"),
    description: legacyLineDescription(li),
    quantity: Number(li.quantity ?? 0),
    unitPrice: null,
    total: null,
  }));

  const estimateDate = parseDate(payload.estimate_date);
  const quoteValidDays = Number(payload.quote_valid_days ?? 30);
  const validUntilRaw = parseDate(payload.valid_until);
  const validUntil =
    validUntilRaw ??
    (estimateDate && Number.isFinite(quoteValidDays)
      ? addDays(estimateDate, quoteValidDays)
      : addDays(new Date(), 30));

  const notesRaw = payload.customer_notes;
  const notes =
    typeof notesRaw === "string" && notesRaw.trim()
      ? notesRaw.trim()
      : DEFAULT_NOTES;

  const shipTo = payload.ship_to_region;
  const address =
    typeof shipTo === "string" && shipTo.trim() ? shipTo.trim() : "—";

  return {
    id: String(payload.estimate_id ?? rowId),
    businessName: String(customer.company ?? "").trim() || "—",
    contactName: String(customer.contact_name ?? "—"),
    email: String(customer.email ?? "").trim() || "—",
    phone: String(customer.phone ?? "").trim() || "—",
    address,
    lines,
    subtotal: null,
    notes,
    validUntil,
    createdAt: estimateDate,
  };
}

/**
 * Maps a `fortis_estimates` row to a printable quote view.
 * Supports structured columns (from `insert_fortis_estimate`) and legacy `{ id, payload }` snapshots.
 */
export function mapEstimateRow(row: Record<string, unknown> | null): QuoteDisplay | null {
  if (!row || typeof row !== "object") return null;

  const id = String(row.id ?? "");
  if (!id) return null;

  if (isStructuredRow(row)) {
    return mapStructured(row, id);
  }

  const payload = row.payload;
  if (payload && typeof payload === "object") {
    return mapLegacyPayload(payload as Record<string, unknown>, id);
  }

  return null;
}
