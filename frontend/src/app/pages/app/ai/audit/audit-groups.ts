/**
 * The audit trail as it is read: the same thing done several times in
 * a row is one row with a count. A page that asks the same question
 * every few seconds would otherwise bury everything else.
 */

/** Rows alike and next to each other, newest first. */
export interface AuditGroup {
  /** The newest of them: what the row shows. */
  first: any;
  events: any[];
}

/** How long after its newest event a group still takes another. */
const WITHIN_MS = 60_000;

/** What makes two events the same thing done again: the kind, the
 *  action, who did it, in which chat, and how it went. */
function sameness(event: any): string {
  const details = event?.details || {};
  return [
    event?.event_type, event?.function, event?.actor, event?.chat_id,
    details.outcome ?? details.status ?? details.decision,
  ].join('|');
}

/** Events newest first, in groups: an event joins the group before it
 *  when it is the same thing, at most a minute older than the group's
 *  newest. */
export function groupAudit(events: any[]): AuditGroup[] {
  const groups: AuditGroup[] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    const gap = last
      ? new Date(last.first.timestamp).getTime() - new Date(event.timestamp).getTime()
      : NaN;
    if (last && sameness(last.first) === sameness(event) && gap >= 0 && gap <= WITHIN_MS) {
      last.events.push(event);
    } else {
      groups.push({ first: event, events: [event] });
    }
  }
  return groups;
}
