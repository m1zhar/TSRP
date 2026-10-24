export type CsvValue = string | number | boolean | null | undefined;

function csvCell(value: CsvValue): string {
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  if (typeof value === "boolean") return String(value);
  let text = value == null ? "" : String(value);
  // Use the spreadsheet-oriented tab-prefix mitigation for formula-like text.
  // Numeric values return above and remain numeric, including negative amounts.
  if (typeof value === "string" && /^\s*[=+\-@＝＋－＠]/u.test(text)) {
    text = `\t${text}`;
  }
  return `"${text.replaceAll('"', '""')}"`;
}

export function toCsv(rows: CsvValue[][]): string {
  return `${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}\r\n`;
}
