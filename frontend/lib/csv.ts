export type CsvValue = string | number | boolean | null | undefined;

function csvCell(value: CsvValue): string {
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  if (typeof value === "boolean") return String(value);
  let text = value == null ? "" : String(value);
  if (typeof value === "string" && /^[\u0000-\u0020\u00a0]*[=+\-@]/u.test(text)) {
    text = `'${text}`;
  }
  return `"${text.replaceAll('"', '""')}"`;
}

export function toCsv(rows: CsvValue[][]): string {
  return `${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}\r\n`;
}
