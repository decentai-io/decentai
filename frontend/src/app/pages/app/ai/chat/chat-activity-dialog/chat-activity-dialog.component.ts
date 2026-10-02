import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

import { AiSessionService } from 'src/app/services/ai-session.service';
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
