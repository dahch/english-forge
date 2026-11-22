// The API historically serialized naive UTC datetimes (SQLite stores no
// timezone), which `new Date()` interprets as local time — every timestamp was
// displayed shifted by the browser's offset. Treat zone-less values as UTC.
export function parseServerDate(value: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  return new Date(hasZone ? value : `${value}Z`)
}

export function formatServerDateTime(value: string): string {
  return parseServerDate(value).toLocaleString()
}
