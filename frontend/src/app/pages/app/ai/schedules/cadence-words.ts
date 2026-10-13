/**
 * When a schedule runs, in words and as a form.
 *
 * A row keeps its cadence as the clock reads it: a cron, a period in
 * seconds, or nothing for a one-off. A person reads "Every weekday at
 * 09:00", and writes it by choosing days and a time. This is the
 * translation both ways, with the next few runs worked out here so a
 * form can show them before anything is saved. Times are read in the
 * schedule's own zone, whatever zone the browser is in.
 */

/** What of a schedule says when. */
export interface Clocked {
  cron: string;
  every_seconds: number | null;
  timezone: string;
  next_run_at: number;
  last_run_at?: number | null;
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const two = (value: number) => String(value).padStart(2, '0');

// ── A moment, in a zone ────────────────────────────────────────────────

/** The calendar parts of a moment as a zone's clock shows them. */
function partsIn(ms: number, zone: string): { y: number; mo: number; d: number; h: number; mi: number; dow: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone || undefined, hourCycle: 'h23', weekday: 'short',
    year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric',
  }).formatToParts(new Date(ms));
  const read = (type: string) => parts.find((part) => part.type === type)?.value || '';
  return {
    y: Number(read('year')), mo: Number(read('month')), d: Number(read('day')),
    h: Number(read('hour')), mi: Number(read('minute')), dow: DAYS.indexOf(read('weekday')),
  };
}

/** The moment a zone's clock shows a given date and time. */
export function momentIn(zone: string, y: number, mo: number, d: number, h: number, mi: number): number {
  const wall = Date.UTC(y, mo - 1, d, h, mi);
  let guess = wall;
  // The zone's offset at the guess, applied twice: once is off by an
  // hour on the day the clocks change.
  for (let pass = 0; pass < 2; pass += 1) {
    const shown = partsIn(guess, zone);
    guess -= Date.UTC(shown.y, shown.mo - 1, shown.d, shown.h, shown.mi) - wall;
  }
  return guess;
}

/** A moment as a person reads it: "Tue 14 Oct, 09:00". */
export function momentWords(seconds: number, zone = ''): string {
  if (!seconds) return '';
  try {
    return new Date(seconds * 1000).toLocaleString(undefined, {
      timeZone: zone || undefined, weekday: 'short', day: 'numeric', month: 'short',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    });
  } catch {
    return new Date(seconds * 1000).toLocaleString();
  }
}

/** How far off a moment is: "in 14 h", "due now". */
export function until(seconds: number, now = Date.now() / 1000): string {
  const left = Math.round(seconds - now);
  if (left <= 0) return 'due now';
  if (left < 60) return 'in under a minute';
  if (left < 3600) return `in ${Math.round(left / 60)} min`;
  if (left < 86400) return `in ${Math.round(left / 3600)} h`;
  return `in ${Math.round(left / 86400)} d`;
}

/** A period in words: "2 hours", "1 day". */
export function periodWords(seconds: number): string {
  for (const [size, word] of [[86400, 'day'], [3600, 'hour'], [60, 'minute']] as [number, string][]) {
    if (seconds % size === 0) return `${seconds / size} ${word}${seconds === size ? '' : 's'}`;
  }
  return `${seconds} seconds`;
}

// ── A cadence, in words ────────────────────────────────────────────────

/** The days a cron's fifth part names (0 is Sunday), or null when it
 *  is not a plain list of days. */
function daysOf(part: string): number[] | null {
  if (part === '*') return [0, 1, 2, 3, 4, 5, 6];
  const days = new Set<number>();
  for (const piece of part.split(',')) {
    const range = /^(\d)(?:-(\d))?$/.exec(piece);
    if (!range) return null;
    const from = Number(range[1]) % 7;
    const to = range[2] === undefined ? from : Number(range[2]) % 7;
    if (range[2] !== undefined && Number(range[1]) > Number(range[2])) return null;
    for (let day = Number(range[1]); day <= (range[2] === undefined ? Number(range[1]) : Number(range[2])); day += 1) {
      days.add(day % 7);
    }
    if (from > 6 || to > 6) return null;
  }
  return [...days].sort((a, b) => a - b);
}

function daysWords(days: number[]): string {
  const key = days.join();
  if (days.length === 7) return 'Every day';
  if (key === '1,2,3,4,5') return 'Every weekday';
  if (key === '0,6') return 'Every weekend';
  const names = days.map((day) => DAYS[day]);
  return `Every ${names.length > 1 ? names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1] : names[0]}`;
}

/** A cron in words where it is one of the common shapes, else as it is
 *  written: a translation that gets one case wrong is worse than the
 *  expression. */
export function cronWords(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return `Custom: ${cron}`;
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
  const plain = (part: string) => /^\d+$/.test(part);
  const everyN = (part: string) => /^\*\/(\d+)$/.exec(part)?.[1];
  const rest = dayOfMonth === '*' && month === '*' && dayOfWeek === '*';

  if (rest && hour === '*' && everyN(minute)) return `Every ${everyN(minute)} minutes`;
  if (rest && minute === '0' && everyN(hour)) return `Every ${everyN(hour)} hours`;
  if (rest && hour === '*' && plain(minute)) {
    return minute === '0' ? 'Every hour' : `Every hour at :${two(Number(minute))}`;
  }
  if (plain(minute) && plain(hour) && month === '*') {
    const time = `${two(Number(hour))}:${two(Number(minute))}`;
    const days = daysOf(dayOfWeek);
    if (dayOfMonth === '*' && days) return `${daysWords(days)} at ${time}`;
    if (plain(dayOfMonth) && dayOfWeek === '*') return `On day ${dayOfMonth} of every month at ${time}`;
  }
  return `Custom: ${cron}`;
}

/** When a schedule runs, as its row says it. */
export function cadenceWords(schedule: Clocked): string {
  if (schedule.cron) return cronWords(schedule.cron);
  if (schedule.every_seconds) return `Every ${periodWords(schedule.every_seconds).replace(/^1 /, '')}`;
  const moment = schedule.next_run_at || schedule.last_run_at || 0;
  return moment ? `Once, ${momentWords(moment, schedule.timezone)}` : 'Once';
}

// ── A cadence, as a form ───────────────────────────────────────────────

export type CadenceKind = 'every' | 'days' | 'once' | 'custom';
export type PeriodUnit = 'minutes' | 'hours' | 'days';
const UNIT_SECONDS: Record<PeriodUnit, number> = { minutes: 60, hours: 3600, days: 86400 };

/** The four ways a person says when, each with its own boxes. */
export interface CadenceDraft {
  kind: CadenceKind;
  count: number;
  unit: PeriodUnit;
  /** Days of the week, 0 for Sunday. */
  days: number[];
  /** HH:MM, for the days and for the one-off. */
  time: string;
  /** YYYY-MM-DD, for the one-off. */
  date: string;
  cron: string;
}

export function blankCadence(): CadenceDraft {
  return { kind: 'days', count: 1, unit: 'hours', days: [1, 2, 3, 4, 5], time: '09:00', date: '', cron: '0 9 * * 1-5' };
}

/** A schedule's cadence as the form shows it, to be changed. */
export function cadenceOf(schedule: Clocked): CadenceDraft {
  const draft = blankCadence();
  if (schedule.every_seconds) {
    const unit = (['days', 'hours', 'minutes'] as PeriodUnit[])
      .find((each) => schedule.every_seconds! % UNIT_SECONDS[each] === 0) || 'minutes';
    return { ...draft, kind: 'every', unit, count: Math.max(1, Math.round(schedule.every_seconds / UNIT_SECONDS[unit])) };
  }
  if (schedule.cron) {
    const [minute, hour, dayOfMonth, month, dayOfWeek] = schedule.cron.trim().split(/\s+/);
    const days = dayOfWeek ? daysOf(dayOfWeek) : null;
    if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && dayOfMonth === '*' && month === '*' && days) {
      return { ...draft, kind: 'days', days, time: `${two(Number(hour))}:${two(Number(minute))}`, cron: schedule.cron };
    }
    return { ...draft, kind: 'custom', cron: schedule.cron };
  }
  const moment = partsIn((schedule.next_run_at || Date.now() / 1000) * 1000, schedule.timezone);
  return { ...draft, kind: 'once', date: `${moment.y}-${two(moment.mo)}-${two(moment.d)}`, time: `${two(moment.h)}:${two(moment.mi)}` };
}

/** What is wrong with a cadence as it stands; '' when nothing is. */
export function cadenceProblem(draft: CadenceDraft, zone: string, now = Date.now()): string {
  const timed = /^\d{2}:\d{2}$/.test(draft.time);
  switch (draft.kind) {
    case 'every':
      if (!(draft.count >= 1) || !Number.isInteger(Number(draft.count))) return 'Enter a whole number, 1 or more.';
      return '';
    case 'days':
      if (!draft.days.length) return 'Choose at least one day.';
      return timed ? '' : 'Enter a time.';
    case 'once': {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.date) || !timed) return 'Enter a date and a time.';
      const [moment] = upcomingOf(draft, zone, 1, 0);
      return moment * 1000 <= now ? 'That time has passed.' : '';
    }
    default:
      return draft.cron.trim().split(/\s+/).length === 5 ? ''
        : 'A cron has five parts: minute, hour, day of the month, month, day of the week.';
  }
}

/** A cadence as the backend takes it: one way of saying when. */
export function whenOf(draft: CadenceDraft): { cron?: string; every_seconds?: number; at?: string } {
  const [hour, minute] = draft.time.split(':').map(Number);
  switch (draft.kind) {
    case 'every': return { every_seconds: Math.round(draft.count) * UNIT_SECONDS[draft.unit] };
    case 'days': {
      const days = [...draft.days].sort((a, b) => a - b);
      return { cron: `${minute} ${hour} * * ${days.length === 7 ? '*' : days.join(',')}` };
    }
    // Without a zone of its own: the backend reads it in the schedule's.
    case 'once': return { at: `${draft.date}T${draft.time}:00` };
    default: return { cron: draft.cron.trim() };
  }
}

/** The next runs of a cadence, in seconds, soonest first. A custom
 *  cron has none here: the backend counts those once it is saved. */
export function upcomingOf(draft: CadenceDraft, zone: string, count = 3, now = Date.now()): number[] {
  const [hour, minute] = draft.time.split(':').map(Number);
  if (draft.kind === 'every') {
    const period = Math.max(1, Math.round(draft.count)) * UNIT_SECONDS[draft.unit];
    return Array.from({ length: count }, (_, index) => Math.round(now / 1000) + period * (index + 1));
  }
  if (draft.kind === 'once') {
    const [y, mo, d] = draft.date.split('-').map(Number);
    return y && !Number.isNaN(hour) ? [Math.round(momentIn(zone, y, mo, d, hour, minute) / 1000)] : [];
  }
  if (draft.kind !== 'days' || !draft.days.length || Number.isNaN(hour)) return [];
  const found: number[] = [];
  const today = partsIn(now, zone);
  // Four weeks ahead: three runs of a schedule that comes once a week.
  for (let ahead = 0; ahead < 29 && found.length < count; ahead += 1) {
    // Noon keeps the date right across a change of the clocks.
    const day = partsIn(momentIn(zone, today.y, today.mo, today.d, 12, 0) + ahead * 86400000, zone);
    if (!draft.days.includes(day.dow)) continue;
    const moment = momentIn(zone, day.y, day.mo, day.d, hour, minute);
    if (moment > now) found.push(Math.round(moment / 1000));
  }
  return found;
}

/** The names of the days, for the form: Monday first. */
export const WEEK: { day: number; name: string }[] =
  [1, 2, 3, 4, 5, 6, 0].map((day) => ({ day, name: DAYS[day] }));
