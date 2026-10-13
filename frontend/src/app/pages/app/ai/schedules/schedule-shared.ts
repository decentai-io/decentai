import { ActivitySchedule, ScheduleRun, SchedulableFunction } from 'src/app/services/activity.service';

/**
 * A schedule in the words the list, its page and the form all use: how
 * it stands, what it runs, and what a run of it did.
 */

export type ScheduleState = 'active' | 'paused' | 'done' | 'failing';

export const STATE_WORDS: Record<ScheduleState, string> = {
  active: 'Active', paused: 'Paused', done: 'Done', failing: 'Failing',
};

/** A one-off has no cadence to come round again. */
export function isOnce(schedule: ActivitySchedule): boolean {
  return !schedule.cron && !schedule.every_seconds;
}

/** Newest last, as the runtime keeps them. */
export function lastRun(schedule: ActivitySchedule): ScheduleRun | null {
  const runs = schedule.runs || [];
  return runs.length ? runs[runs.length - 1] : null;
}

/** How a schedule stands: a one-off that ran is done, not paused; one
 *  whose last run failed is failing for as long as it is still on. */
export function stateOf(schedule: ActivitySchedule): ScheduleState {
  if (!schedule.enabled) return isOnce(schedule) && schedule.last_run_at ? 'done' : 'paused';
  return lastRun(schedule)?.status === 'error' ? 'failing' : 'active';
}

/** The function a schedule runs, by the names its agent gave it; the
 *  id without its agent when the agent is no longer offered. */
export function functionLabel(id: string, functions: SchedulableFunction[]): string {
  return functions.find((fn) => fn.value === id)?.label || id.split('.').slice(1).join('.') || id;
}

/** What a schedule does, in the person's words where there are any. */
export function whatOf(schedule: ActivitySchedule, functions: SchedulableFunction[]): string {
  if (schedule.note) return schedule.note;
  if (schedule.function) return `Run ${functionLabel(schedule.function, functions)}`;
  return 'Wake the assistant';
}

/** What a function's schedule does beside running: '' for a note. */
export function alsoOf(schedule: ActivitySchedule, functions: SchedulableFunction[]): string {
  if (!schedule.function) return '';
  const ran = schedule.note ? `runs ${functionLabel(schedule.function, functions)}` : '';
  const wakes = schedule.wake_field ? `wakes the assistant when ${schedule.wake_field} comes back` : '';
  return [ran, wakes].filter(Boolean).join(', ');
}

export function runLabel(run: ScheduleRun): string {
  if (run.status === 'error') return 'Failed';
  if (run.status === 'woke') return 'Woke the assistant';
  if (run.status === 'success') return run.woke ? 'Ran, woke the assistant' : 'Ran quietly';
  return run.status;
}

/** How long a run took: "3.2 s"; '' when the runtime did not measure. */
export function took(run: ScheduleRun): string {
  const ms = Number(run.took_ms || 0);
  if (!ms) return '';
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms / 60000)} min`;
}

/** "1 of 10 failed"; '' when none did. */
export function failuresOf(schedule: ActivitySchedule): string {
  const runs = schedule.runs || [];
  const failed = runs.filter((run) => run.status === 'error').length;
  return failed ? `${failed} of ${runs.length} failed` : '';
}
