/**
 * Plain English for what the runtime writes down of agents
 * (docs/system/monitoring.md): one line a person reads, a second that
 * says the rest, and the colour the outcome deserves.
 */
import { Choice } from 'src/app/components/ui/radio-keys';
import { RecordTone } from 'src/app/components/ui/record-row.component';
import { MonitorEvent } from 'src/app/services/monitoring.service';

/** The kinds a person can ask for, in the words they read. */
export const MONITOR_KINDS: Choice<string>[] = [
  { value: '', name: 'Every kind' },
  { value: 'worker.started', name: 'Agent started' },
  { value: 'worker.ended', name: 'Agent ended' },
  { value: 'worker.failed', name: 'Agent could not start' },
  { value: 'connection', name: 'Connection' },
  { value: 'log', name: 'Log line' },
  { value: 'process', name: 'Process started' },
  { value: 'memory', name: 'Ended for memory' },
  { value: 'helper', name: 'Helper job' },
  { value: 'program', name: 'Program run' },
];

/** What each job of the spawn helper does, for somebody who never
 *  read its source. */
const HELPER_JOBS: Record<string, string> = {
  check: 'checked that users can be switched',
  own: "handed a folder to the agent's user",
  clear: "emptied one of the agent's folders",
  stop: "ended every process of the agent's user",
  sweep: 'removed what the agent left in shared temporary folders',
};

/** The kind of an event in those words, for its chip. */
export function monitorKind(event: MonitorEvent): string {
  return MONITOR_KINDS.find((kind) => kind.value === event.kind)?.name || event.kind;
}

export function bytesLabel(value: any): string {
  const amount = Number(value) || 0;
  if (amount >= 1024 ** 3) return `${(amount / 1024 ** 3).toFixed(1)} GB`;
  if (amount >= 1024 ** 2) return `${(amount / 1024 ** 2).toFixed(1)} MB`;
  if (amount >= 1024) return `${(amount / 1024).toFixed(1)} KB`;
  return `${amount} bytes`;
}

function seconds(value: any): string {
  const amount = Number(value) || 0;
  return amount < 1 ? `${Math.round(amount * 1000)} ms` : `${amount.toFixed(1)} s`;
}

function who(event: MonitorEvent): string {
  return event.name || event.agent || 'The platform';
}

export function monitorLabel(event: MonitorEvent): string {
  switch (event.kind) {
    case 'worker.started': return `${who(event)} started`;
    case 'worker.ended': return `${who(event)} ended`;
    case 'worker.failed': return `${who(event)} could not start`;
    case 'connection':
      return `${who(event)} → ${event['host'] || 'an unknown host'}`
        + (event['port'] ? `:${event['port']}` : '');
    case 'log': return `${who(event)} wrote to its log`;
    case 'process': return `${who(event)} started a process`;
    case 'memory':
      return event.name ? `${event.name} was ended for memory`
        : 'The system ended a process for memory';
    case 'helper': return `Helper ${HELPER_JOBS[event['job']] || event['job'] || 'ran'}`;
    case 'program': return `A program was run as ${who(event)}`;
    default: return event.kind;
  }
}

export function monitorSummary(event: MonitorEvent): string {
  switch (event.kind) {
    case 'worker.started':
      return (event['confined'] ? 'As a user of its own' : 'Not confined')
        + (event['where'] ? `, in ${event['where']}` : '');
    case 'worker.ended':
    case 'worker.failed':
      return String(event['why'] || '');
    case 'connection':
      if (event['allowed'] === false) return `Refused: ${event['why'] || 'not allowed'}`;
      if (event['reached'] === false) return `Allowed, and not reached: ${event['why'] || ''}`;
      return [
        `${bytesLabel(event['sent'])} sent`,
        `${bytesLabel(event['received'])} received`,
        seconds(event['seconds']),
      ].join(' · ');
    case 'log': return String(event['line'] || '');
    case 'process': return String(event['command'] || '');
    case 'memory': return String(event['why'] || '');
    case 'helper':
      return event['refused'] ? `Refused: ${event['refused']}`
        : [event.name, seconds(event['seconds'])].filter(Boolean).join(' · ');
    case 'program':
      return `${event['program'] || ''} · exit ${event['code'] ?? '?'}`;
    default: return '';
  }
}

/** How it went, in a word, for someone who cannot see the dot. */
export function monitorChip(event: MonitorEvent): string {
  if (event.kind === 'connection') {
    return event['allowed'] === false ? 'refused'
      : event['reached'] === false ? 'unreached' : 'made';
  }
  if (event.kind === 'helper' || event.kind === 'program') {
    return event['code'] === 0 ? 'done' : 'failed';
  }
  return ({
    'worker.started': 'started', 'worker.ended': 'ended',
    'worker.failed': 'failed', log: 'log', process: 'process',
    memory: 'ended',
  } as Record<string, string>)[event.kind] || event.kind;
}

/** The colour an outcome deserves. */
export function monitorTone(event: MonitorEvent): RecordTone {
  switch (event.kind) {
    case 'worker.started': return 'ok';
    case 'worker.failed':
    case 'memory': return 'bad';
    case 'worker.ended':
      return event['why'] === 'it was asked to leave' ? 'muted' : 'live';
    case 'connection':
      return event['allowed'] === false ? 'bad'
        : event['reached'] === false ? 'live' : 'ok';
    case 'helper':
    case 'program':
      return event['code'] === 0 ? 'muted' : 'bad';
    default: return 'muted';
  }
}

/** Rows alike and next to each other, newest first: what the row
 *  shows is the newest of them. */
export interface MonitorGroup {
  first: MonitorEvent;
  events: MonitorEvent[];
}

/** How long after its newest event a group still takes another, in
 *  seconds. */
const WITHIN_S = 60;

/** What makes two events the same thing again: one agent's lines of
 *  log, the same connection made or refused, the same job or program
 *  ending the same way. */
function sameness(event: MonitorEvent): string {
  return [
    event.kind, event.agent, monitorChip(event),
    event['host'], event['port'], event['job'], event['program'],
  ].join('|');
}

/** Events newest first, in groups: one joins the group before it when
 *  it is the same thing, at most a minute older than the group's
 *  newest. A burst of log lines is one row, opening to each line. */
export function groupMonitor(events: MonitorEvent[]): MonitorGroup[] {
  const groups: MonitorGroup[] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    const gap = last ? last.first.at - event.at : NaN;
    if (last && sameness(last.first) === sameness(event) && gap >= 0 && gap <= WITHIN_S) {
      last.events.push(event);
    } else {
      groups.push({ first: event, events: [event] });
    }
  }
  return groups;
}

/** What a line holds, field by field, as it was written down: for its
 *  details, under the time in full. */
export function monitorFields(event: MonitorEvent): { name: string; value: string }[] {
  return Object.entries(event)
    .filter(([name]) => name !== 'at')
    .map(([name, value]) => ({
      name,
      value: value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value),
    }));
}
