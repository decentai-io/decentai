import { Choice } from 'src/app/components/ui/radio-keys';
import {
  OutboundFormat, SendState, WebhookExecution, WebhookField, WebhookFieldType,
} from 'src/app/services/webhooks.service';

/**
 * What the Webhooks page, its dialogs and Settings › Notifications out
 * say, kept in one place: the field types, what an event came to, the
 * events and formats out in words, and the examples a caller copies.
 */

/** A field's name, as the backend takes it. */
export const FIELD_NAME = /^[a-z][a-z0-9_]{0,63}$/;

export const FIELD_TYPES: Choice<WebhookFieldType>[] = [
  { value: 'text', name: 'Text' },
  { value: 'number', name: 'Number' },
  { value: 'boolean', name: 'Yes or no' },
  { value: 'date', name: 'Date' },
  { value: 'list', name: 'List' },
  { value: 'object', name: 'Object' },
];

/** Why a field's name is refused, or ''. */
export function fieldNameProblem(name: string, fields: WebhookField[]): string {
  const trimmed = name.trim();
  if (!trimmed) return 'Give the field a name.';
  if (!FIELD_NAME.test(trimmed)) return 'Lowercase letters, digits and _, starting with a letter.';
  if (fields.filter((field) => field.name.trim() === trimmed).length > 1) return 'Named twice.';
  return '';
}

/** What an event's body could hold for a field of this type. */
function exampleValue(type: WebhookFieldType): unknown {
  switch (type) {
    case 'number': return 1;
    case 'boolean': return true;
    case 'date': return new Date().toISOString().slice(0, 10);
    case 'list': return [];
    case 'object': return {};
    default: return '…';
  }
}

/** A body that fits the fields: every one of them, with a value of its
 *  type; laid out over lines when `indent` is given. */
export function exampleBody(fields: WebhookField[], indent?: number): string {
  const body: Record<string, unknown> = {};
  for (const field of fields) body[field.name] = exampleValue(field.type);
  return JSON.stringify(body, null, indent);
}

// ── What an event came to ────────────────────────────────────────────

/** What the chat did with an event, in words and the chip's tone. */
export function resultWord(execution: WebhookExecution): { word: string; tone: string } {
  switch (execution.result) {
    case 'answered': return { word: 'Answered', tone: 'accent' };
    case 'waiting': return { word: 'Waiting for approval', tone: 'warn' };
    case 'working': return { word: 'Working', tone: '' };
    case 'not delivered': return { word: 'Not delivered', tone: 'warn' };
    default: return { word: 'Refused', tone: 'danger' };
  }
}

/** A card's state in a word: approved, denied, answered, expired, or
 *  waiting while nobody has decided it. */
export function cardWord(status: string): string {
  return status === 'pending' ? 'waiting' : status || 'unknown';
}

/** What was posted, one line: "ticket_id: 42 · title: Printer down". */
export function postedLine(data: Record<string, unknown> | null | undefined): string {
  return Object.entries(data || {})
    .map(([name, value]) => `${name}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(' · ');
}

/** A call a person can paste into a terminal. A webhook that requires
 *  a signature takes two more headers, made as the HMAC line says. */
export function curlExample(url: string, secret: string, fields: WebhookField[], signed: boolean): string {
  const lines = [
    `curl -X POST '${url}'`,
    `  -H 'Authorization: Bearer ${secret}'`,
    `  -H 'Content-Type: application/json'`,
  ];
  if (signed) {
    lines.push(`  -H 'X-DecentAI-Timestamp: <unix seconds>'`);
    lines.push(`  -H 'X-DecentAI-Signature: sha256=<hex>'`);
  }
  lines.push(`  -d '${exampleBody(fields).replace(/'/g, `'\\''`)}'`);
  return lines.join(' \\\n');
}

/** How a signature is made, inbound and out. */
export const HMAC_LINE =
  'signature = HMAC-SHA256(secret, "<timestamp>.<body>"), sent as "sha256=<hex>" in X-DecentAI-Signature, '
  + 'with the timestamp (unix seconds) in X-DecentAI-Timestamp.';

// ── Out ──────────────────────────────────────────────────────────────

/** The events a notification is sent for, in plain words. */
export const EVENT_WORDS: Record<string, { name: string; hint: string }> = {
  'approval.waiting': { name: 'An approval is waiting for you', hint: 'A chat stopped to ask before doing something.' },
  'reply.ready': { name: 'A chat answered while you were away', hint: 'Sent when you were not looking at the chat.' },
  'run.finished': { name: 'A schedule’s run finished', hint: '' },
  'run.failed': { name: 'A schedule’s run failed', hint: '' },
};

export function eventName(event: string): string {
  return EVENT_WORDS[event]?.name ?? event;
}

export const FORMAT_WORDS: Record<OutboundFormat, { name: string; hint: string }> = {
  json: { name: 'JSON', hint: 'For your own service: a JSON body, signed with the secret shown once.' },
  slack: { name: 'Slack', hint: 'A Slack incoming webhook address: the message is posted to its channel.' },
  teams: { name: 'Microsoft Teams', hint: 'A Teams incoming webhook or workflow address: posted as a card.' },
};

export function formatName(format: string): string {
  return FORMAT_WORDS[format as OutboundFormat]?.name ?? format;
}

/** A send out, in words and the chip's tone. */
export function stateWord(state: SendState): { word: string; tone: string } {
  switch (state) {
    case 'delivered': return { word: 'Delivered', tone: 'accent' };
    case 'pending': return { word: 'Waiting', tone: '' };
    case 'sending': return { word: 'Sending', tone: '' };
    case 'dropped': return { word: 'Dropped', tone: 'warn' };
    default: return { word: 'Failed', tone: 'danger' };
  }
}

/** Copies, and says whether it could. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
