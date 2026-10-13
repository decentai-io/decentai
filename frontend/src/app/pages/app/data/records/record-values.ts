/**
 * How a record's value is shown: decided from the value itself and,
 * where the agent's manifest declared the field, from its type. A
 * record is read as what it holds (a note as prose, tags as chips, a
 * list of rows as a table), never as JSON unless that is asked for.
 */
export type Shown =
  | { kind: 'empty' }
  /** One line of text; a reference (a 32-character id); a select's choice. */
  | { kind: 'text' | 'ref' | 'chip'; text: string }
  /** Text that is read: long, or with line breaks. */
  | { kind: 'prose'; paragraphs: string[] }
  | { kind: 'number'; text: string }
  | { kind: 'flag'; on: boolean }
  /** A list of plain values. */
  | { kind: 'chips'; items: string[] }
  /** A list of objects, as rows under the keys they share. */
  | { kind: 'table'; columns: string[]; rows: string[][] }
  /** An object, as its own names and values. */
  | { kind: 'object'; entries: { name: string; value: Shown }[] }
  /** A field the manifest marks secret: masked until it is revealed. */
  | { kind: 'secret'; text: string };

/** Past this many characters, or with a line break, a string is prose. */
const PROSE_FROM = 120;
/** A table shows this many columns; an object is unfolded this deep. */
const TABLE_COLUMNS = 8;
const OBJECT_DEPTH = 2;

const isPlain = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isSimple = (value: unknown): boolean =>
  ['string', 'number', 'boolean'].includes(typeof value);

/** A number as it reads: whole as it is, otherwise to two places. */
export function numberLabel(value: number): string {
  return Number.isInteger(value) ? String(value)
    : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** A value inside a table's cell or a chip: one short line. */
function inline(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return numberLabel(value);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

export function showValue(value: unknown, type = '', depth = 0): Shown {
  if (value === null || value === undefined || value === '') return { kind: 'empty' };
  if (type === 'secret') return { kind: 'secret', text: inline(value) };
  if (typeof value === 'boolean') return { kind: 'flag', on: value };
  if (typeof value === 'number') return { kind: 'number', text: numberLabel(value) };

  if (typeof value === 'string') {
    if (type === 'select') return { kind: 'chip', text: value };
    if (/^[0-9a-f]{32}$/.test(value)) return { kind: 'ref', text: value };
    if (value.length > PROSE_FROM || value.includes('\n')) {
      return { kind: 'prose', paragraphs: value.split(/\n+/).filter((line) => line.trim()) };
    }
    return { kind: 'text', text: value };
  }

  if (Array.isArray(value)) {
    if (!value.length) return { kind: 'empty' };
    if (value.every(isSimple)) return { kind: 'chips', items: value.map(inline) };
    if (value.every(isPlain)) {
      const columns = [...new Set(value.flatMap((row) => Object.keys(row)))].slice(0, TABLE_COLUMNS);
      return { kind: 'table', columns, rows: value.map((row) => columns.map((column) => inline(row[column]))) };
    }
    return { kind: 'text', text: JSON.stringify(value) };
  }

  if (isPlain(value) && depth < OBJECT_DEPTH) {
    const entries = Object.entries(value);
    if (!entries.length) return { kind: 'empty' };
    // An object that only wraps one piece of text (a note's
    // `{ text }`) reads as that text, not as a list of one name.
    if (entries.length === 1 && typeof entries[0][1] === 'string') {
      return showValue(entries[0][1], '', depth + 1);
    }
    return {
      kind: 'object',
      entries: entries.map(([name, inner]) => ({ name, value: showValue(inner, '', depth + 1) })),
    };
  }
  return { kind: 'text', text: JSON.stringify(value) };
}
