import { relativeDate } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';
import { McpCredential, McpServer } from 'src/app/services/mcp.service';
import { LEVEL_MEANINGS, PERMISSION_LEVELS } from '../../agents/agent-shared';

/** What the list and a server's own page both say about a server, so
 *  that they cannot say two things. */

export const BLOCKED_WHY = 'MCP servers are off for this deployment';

/** How a server stands: one word, its chip's variant, and why. */
export interface ServerStatus {
  word: string;
  tone: '' | 'accent' | 'warn' | 'danger';
  why: string;
}

export function serverStatus(server: McpServer, blocked: boolean): ServerStatus {
  if (server.unreadable) {
    return { word: 'Unreadable', tone: 'warn', why: 'Its details can no longer be read. Remove it and add it again.' };
  }
  if (blocked) return { word: 'Off for this deployment', tone: '', why: BLOCKED_WHY };
  if (server.keys.last_read_error) {
    return { word: 'Cannot be reached', tone: 'danger', why: server.keys.last_read_error };
  }
  return server.keys.enabled
    ? { word: 'On', tone: 'accent', why: 'Your chats can call the tools you kept on' }
    : { word: 'Off', tone: '', why: 'No chat can call it' };
}

/** What a server is reached with, as the backend words it: 'token',
 *  'header X-Api-Key', 'no credential'; '' for a server added before
 *  rows carried it. */
export function credentialWord(server: McpServer): string {
  const kind = server.keys.credential || '';
  return kind === 'none' ? 'no credential' : kind;
}

/** When its tools were last read, mid-sentence: "2 h ago", "just now". */
export function lastRead(server: McpServer): string {
  return relativeDate(readAt(server)).replace(/^J/, 'j');
}

export function readAt(server: McpServer): string {
  return server.keys.last_read_at || server.updated_at || '';
}

/** What a tool may cost to call: the agents' four words, each with
 *  what it means for a chat. */
export const TOOL_LEVELS: Choice<number>[] = PERMISSION_LEVELS.map((name, value) => ({
  value,
  name,
  hint: LEVEL_MEANINGS[name].replace(/^Level \d\. /, ''),
}));

export type CredentialKind = 'none' | 'token' | 'header';

/** A credential as it is typed, when a server is added and when its
 *  credential is replaced: one of three kinds, and what each needs. */
export class CredentialDraft {
  kind: CredentialKind = 'none';
  token = '';
  header = '';
  value = '';

  readonly kinds: Choice<CredentialKind>[] = [
    { value: 'none', name: 'None' },
    { value: 'token', name: 'A token' },
    { value: 'header', name: 'A header' },
  ];

  reset(kind: CredentialKind = 'none'): void {
    this.kind = kind;
    this.token = this.header = this.value = '';
  }

  /** What is missing from a part of it; '' when nothing is. */
  missing(part: 'token' | 'header' | 'value'): string {
    if (part === 'token') return this.kind === 'token' && !this.token.trim() ? 'Enter the token.' : '';
    if (this.kind !== 'header') return '';
    if (part === 'header') return this.header.trim() ? '' : 'Enter the header\'s name.';
    return this.value.trim() ? '' : 'Enter the header\'s value.';
  }

  get ready(): boolean {
    return !this.missing('token') && !this.missing('header') && !this.missing('value');
  }

  /** As the backend takes it: `{}` is none. */
  given(): McpCredential {
    if (this.kind === 'token') return { token: this.token.trim() };
    if (this.kind === 'header') return { header: this.header.trim(), value: this.value.trim() };
    return {};
  }
}
