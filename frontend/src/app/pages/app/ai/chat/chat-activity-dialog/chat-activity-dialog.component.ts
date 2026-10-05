import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

import {
  AiSessionService, AssistantTranscript, TranscriptEntry,
} from 'src/app/services/ai-session.service';
import { auditLabel, auditSummary, auditTone, durationLabel } from './audit-words';

/** A stored result opened from an execution's row. */
interface ResultView {
  state: 'loading' | 'rows' | 'value' | 'missing';
  rows: any[];
  columns: string[];
  value: any;
}

/**
 * What the platform recorded while this chat happened.
 *
 * The trail is written by the platform, never by the assistant: every
 * function the runtime ran — the agent, the function, its level, the
 * inputs in outline, the outcome, how long it took — every approval
 * asked for and answered, every credential read by name, every install.
 * This is where a person reads it, oldest first, each row opening to
 * what it holds and, for a function that returned something, the
 * result itself.
 *
 * Beside it, "The assistant": what the model of this chat was shown and
 * what it answered, in order — its instructions, each thing that
 * arrived, each action it chose. The person's own, read from the
 * assistant's kept state; nothing in it can be changed here.
 */
@Component({
  selector: 'app-chat-activity-dialog',
  standalone: false,
  templateUrl: './chat-activity-dialog.component.html',
  styleUrls: [
    './chat-activity-dialog.shared.css',
    './chat-activity-dialog.component.css',
  ],
})
export class ChatActivityDialogComponent implements OnInit {
  loading = true;
  view: 'record' | 'assistant' = 'record';
  /** What the assistant was shown: read the first time it is asked for. */
  transcript: AssistantTranscript | null = null;
  loadingTranscript = false;
  openTurns = new Set<number>();
  events: any[] = [];
  expanded = new Set<string>();
  results: Record<string, ResultView> = {};

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: any,
    private dialogRef: MatDialogRef<ChatActivityDialogComponent>,
    private aiSession: AiSessionService,
  ) {}

  async ngOnInit(): Promise<void> {
    const chatId = this.data?.chat_id;
    if (chatId) {
      // Newest first from the server; a conversation's trail reads in
      // the order it happened.
      this.events = (await this.aiSession.listAudit(chatId)).slice().reverse();
    }
    this.loading = false;
  }

  // ── The assistant ────────────────────────────────────────────────────

  async show(view: 'record' | 'assistant'): Promise<void> {
    this.view = view;
    if (view !== 'assistant' || this.transcript || this.loadingTranscript) return;
    const chatId = this.data?.chat_id;
    if (!chatId) return;
    this.loadingTranscript = true;
    try {
      this.transcript = await this.aiSession.transcript(chatId);
    } finally {
      this.loadingTranscript = false;
    }
  }

  /** The action the model chose, where an entry is one. */
  private action(entry: TranscriptEntry): any | null {
    if (entry.role !== 'assistant') return null;
    try {
      const parsed = JSON.parse(entry.content);
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch {
      return null;
    }
  }

  turnChip(entry: TranscriptEntry): string {
    return entry.role === 'system' ? 'instructions'
      : entry.role === 'assistant' ? 'decided' : 'told';
  }

  turnTone(entry: TranscriptEntry): string {
    return entry.role === 'assistant' ? 'ok'
      : entry.role === 'system' ? 'live' : 'muted';
  }

  /** One line for the row: the action and what it was aimed at, or the
   *  first line of what the model was told. */
  turnSummary(entry: TranscriptEntry): string {
    const action = this.action(entry);
    if (action) {
      const kind = String(action.action || 'answered');
      const aimed = action.function || action.agent || action.skill
        || action.text || action.goal || '';
      const rest = String(aimed).replace(/\s+/g, ' ').trim();
      return rest ? `${kind}: ${rest.slice(0, 140)}` : kind;
    }
    if (entry.role === 'system') return 'What the assistant is and how it works';
    const first = entry.content.split('\n').find((line) => line.trim()) || '';
    return first.trim().slice(0, 160) || '(nothing)';
  }

  turnText(entry: TranscriptEntry): string {
    const action = this.action(entry);
    return action ? JSON.stringify(action, null, 2) : entry.content;
  }

  toggleTurn(entry: TranscriptEntry): void {
    if (this.openTurns.has(entry.index)) this.openTurns.delete(entry.index);
    else this.openTurns.add(entry.index);
  }

  isTurnOpen(entry: TranscriptEntry): boolean {
    return this.openTurns.has(entry.index);
  }

  // ── Counts for the header ────────────────────────────────────────────

  get ran(): number {
    return this.events.filter((e) => e?.event_type === 'execution').length;
  }

  get failed(): number {
    return this.events.filter((e) => e?.event_type === 'execution' && e?.details?.status !== 'success').length;
  }

  get approvals(): number {
    return this.events.filter((e) => e?.event_type === 'approval.requested').length;
  }

  // ── Rows ─────────────────────────────────────────────────────────────

  label(event: any): string { return auditLabel(event); }
  summary(event: any): string { return auditSummary(event); }
  tone(event: any): string { return auditTone(event); }

  chip(event: any): string {
    if (event?.event_type === 'execution') return String(event?.details?.status || 'ran');
    if (event?.event_type === 'approval.resolved') return String(event?.details?.decision || 'answered');
    return 'record';
  }

  duration(event: any): string {
    const ms = event?.details?.duration_ms;
    return ms == null ? '' : durationLabel(ms);
  }

  hasBody(event: any): boolean {
    const details = event?.details || {};
    return event?.event_type === 'execution'
      || Object.keys(details).length > 0
      || (event?.resource_refs || []).length > 0;
  }

  toggle(event: any): void {
    const id = event?.event_id;
    if (!id || !this.hasBody(event)) return;
    if (this.expanded.has(id)) this.expanded.delete(id);
    else this.expanded.add(id);
  }

  isOpen(event: any): boolean {
    return this.expanded.has(event?.event_id);
  }

  inputs(event: any): string {
    const inputs = event?.details?.inputs;
    if (inputs == null) return '';
    return typeof inputs === 'string' ? inputs : JSON.stringify(inputs, null, 2);
  }

  otherDetails(event: any): { key: string; value: string }[] {
    const details = event?.details || {};
    const hidden = new Set(['inputs', 'status', 'duration_ms', 'permission_level',
      'chat_level', 'agent_name', 'agent_id', 'error', 'resumed']);
    return Object.entries(details)
      .filter(([key]) => !hidden.has(key))
      .map(([key, value]) => ({
        key, value: typeof value === 'object' ? JSON.stringify(value) : String(value),
      }));
  }

  resultRef(event: any): string {
    const ref = (event?.resource_refs || []).find((r: string) => String(r).startsWith('stg_'));
    return event?.event_type === 'execution' && ref ? ref : '';
  }

  /** The stored result behind an execution, the way a table part is
   *  filled: rows into the informative table, anything else as text. */
  async openResult(event: any): Promise<void> {
    const ref = this.resultRef(event);
    if (!ref) return;
    if (this.results[ref]) {
      delete this.results[ref];
      return;
    }
    this.results[ref] = { state: 'loading', rows: [], columns: [], value: null };
    const value = await this.aiSession.getStorage(ref);
    if (value == null) {
      this.results[ref] = { state: 'missing', rows: [], columns: [], value: null };
      return;
    }
    const list = Array.isArray(value) ? value
      : Object.values(value).find((v) => Array.isArray(v) && v.length && typeof v[0] === 'object');
    if (Array.isArray(list) && list.length) {
      const columns = Object.keys(list[0] || {});
      this.results[ref] = { state: 'rows', rows: list, columns, value };
    } else {
      this.results[ref] = { state: 'value', rows: [], columns: [], value };
    }
  }

  resultText(view: ResultView): string {
    return JSON.stringify(view.value, null, 2);
  }

  // ── Words ────────────────────────────────────────────────────────────

  dateLabel(value?: string | null): string {
    if (!value) return '';
    const date = new Date(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
  }

  timeLabel(value?: string | null): string {
    if (!value) return '';
    const date = new Date(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleTimeString();
  }

  close(): void {
    this.dialogRef.close();
  }
}
