import { AgentRecord, RecordShape } from 'src/app/services/records.service';

/**
 * The records of one kind, written out: a CSV with a column for each
 * field the kind declares, or the same as JSON. Both are made from
 * what the page already holds. A field the manifest marks secret is
 * left out of both: a file on a disk is not where a secret is kept.
 */

/** One field as a column: where it is stored, and what heads it. */
interface ExportColumn {
  name: string;
  label: string;
  storage: 'keys' | 'values';
}

/** The kind's declared fields without its secrets; for a kind no
 *  installed agent declares, the keys the records themselves carry. */
function columnsOf(records: AgentRecord[], shape: RecordShape | null): ExportColumn[] {
  if (shape) {
    return shape.fields
      .filter((field) => field.type !== 'secret')
      .map((field) => ({ name: field.name, label: field.label || field.name, storage: field.storage }));
  }
  const names = [...new Set(records.flatMap((record) => Object.keys(record.keys ?? {})))];
  return names.map((name) => ({ name, label: name, storage: 'keys' as const }));
}

/** One CSV field: quoted when it holds a comma, a quote or a line break. */
function csvField(value: unknown): string {
  const text = value === null || value === undefined ? ''
    : typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function recordsAsCsv(records: AgentRecord[], shape: RecordShape | null): string {
  const columns = columnsOf(records, shape);
  const head = [...columns.map((column) => column.label), 'Created', 'Changed', 'Reference'];
  const rows = records.map((record) => [
    ...columns.map((column) => record[column.storage]?.[column.name]),
    record.created_at ?? '',
    record.updated_at ?? '',
    record.resource_ref,
  ]);
  return [head, ...rows].map((row) => row.map(csvField).join(',')).join('\r\n') + '\r\n';
}

export function recordsAsJson(records: AgentRecord[], shape: RecordShape | null): string {
  const columns = columnsOf(records, shape);
  return JSON.stringify(records.map((record) => ({
    reference: record.resource_ref,
    ...Object.fromEntries(columns.map((column) => [column.name, record[column.storage]?.[column.name] ?? null])),
    created_at: record.created_at ?? null,
    updated_at: record.updated_at ?? null,
  })), null, 2) + '\n';
}
