/**
 * Dates as every list says them: how long ago for what is recent, the
 * day for what is not, and the whole date and time for a tooltip.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function parse(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null;
  const date = value instanceof Date ? value : new Date(value);
  return isNaN(date.getTime()) ? null : date;
}

/** "Just now", "12 min ago", "3 h ago", "Yesterday", "6 Oct", "6 Oct
 *  2025"; '' for no date. */
export function relativeDate(
  value: string | number | Date | null | undefined,
  now: Date = new Date(),
): string {
  const date = parse(value);
  if (!date) return '';
  const ago = now.getTime() - date.getTime();
  if (ago < MINUTE) return 'Just now';
  if (ago < HOUR) return `${Math.floor(ago / MINUTE)} min ago`;
  if (ago < DAY) return `${Math.floor(ago / HOUR)} h ago`;
  if (ago < 2 * DAY) return 'Yesterday';
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    ...(sameYear ? {} : { year: 'numeric' }),
  });
}

/** The whole date and time, for the tooltip of a relative one. */
export function fullDate(value: string | number | Date | null | undefined): string {
  return parse(value)?.toLocaleString() ?? '';
}

/** The time of day to the second, "11:34:08", for a row of a record. */
export function clockTime(value: string | number | Date | null | undefined): string {
  return parse(value)?.toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }) ?? '';
}

/** The day a record's rows stand under: "Today", "Yesterday",
 *  "Mon 6 Oct", "Mon 6 Oct 2025". */
export function dayHeading(
  value: string | number | Date | null | undefined,
  now: Date = new Date(),
): string {
  const date = parse(value);
  if (!date) return '';
  const midnight = (of: Date) => new Date(of.getFullYear(), of.getMonth(), of.getDate()).getTime();
  const daysAgo = Math.round((midnight(now) - midnight(date)) / DAY);
  if (daysAgo === 0) return 'Today';
  if (daysAgo === 1) return 'Yesterday';
  return date.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  });
}

/** What was read newest first, cut into its days: each day's heading
 *  and the items under it, in the order they came. */
export function byDay<T>(
  items: T[],
  when: (item: T) => string | number | Date | null | undefined,
  now: Date = new Date(),
): { day: string; items: T[] }[] {
  const days: { day: string; items: T[] }[] = [];
  for (const item of items) {
    const day = dayHeading(when(item), now);
    const last = days[days.length - 1];
    if (last && last.day === day) last.items.push(item);
    else days.push({ day, items: [item] });
  }
  return days;
}

/** The first or the last moment of a day picked in a date box, in the
 *  person's own zone. A date input gives `YYYY-MM-DD`, which
 *  `new Date()` reads as midnight UTC: hours off the day they chose,
 *  and on the day before it west of UTC. So the parts are read as
 *  local ones. Null for a box left empty. */
export function localDay(value: string, end: boolean): Date | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
  if (!parts) return null;
  const [year, month, day] = [Number(parts[1]), Number(parts[2]), Number(parts[3])];
  return end
    ? new Date(year, month - 1, day, 23, 59, 59, 999)
    : new Date(year, month - 1, day, 0, 0, 0, 0);
}

/** A day, with no time of day: "6 Oct 2026"; '' for no date. */
export function dayDate(value: string | number | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  return isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}
