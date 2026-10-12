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
