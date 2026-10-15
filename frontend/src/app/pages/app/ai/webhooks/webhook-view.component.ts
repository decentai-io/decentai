import {
  Component, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges,
} from '@angular/core';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';
import { AuthService } from 'src/app/services/auth.service';
import { Webhook, WebhookExecution, WebhooksService } from 'src/app/services/webhooks.service';
import { TRUST_CHOICES } from '../chat/chat-defaults';
import { FIELD_TYPES, cardWord, copyText, curlExample, postedLine, resultWord } from './webhooks-shared';

type Tab = 'executions' | 'settings';

/** How long after an event its chat may still be working on it: while
 *  one that recent is, the list is read again every few seconds. */
const STILL_WORKING_MS = 5 * 60_000;
const POLL_MS = 5_000;

/**
 * One webhook, opened: every event it heard, newest first, beside what
 * its chat did with it — the reply, the cards and how they stand — and,
 * for its owner, its settings.
 *
 * Someone it was shared with sees the events alone. Nothing here
 * changes anything itself: the page owns the doors, and this says
 * which one was asked for. The events are this view's own read.
 */
@Component({
  selector: 'app-webhook-view',
  standalone: false,
  templateUrl: './webhook-view.component.html',
  styleUrl: './webhook-view.component.css',
})
export class WebhookViewComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) webhook!: Webhook;
  /** Whose it is, for one shared with the reader. */
  @Input() ownerName = '';
  @Input() busy = false;

  @Output() closed = new EventEmitter<void>();
  @Output() edit = new EventEmitter<void>();
  @Output() setEnabled = new EventEmitter<boolean>();
  @Output() test = new EventEmitter<void>();
  @Output() share = new EventEmitter<void>();
  @Output() handOver = new EventEmitter<void>();
  @Output() rotate = new EventEmitter<void>();
  @Output() remove = new EventEmitter<void>();

  readonly tabs: Choice<Tab>[] = [
    { value: 'executions', name: 'Executions' },
    { value: 'settings', name: 'Settings' },
  ];
  tab: Tab = 'executions';

  loading = true;
  problem = '';
  executions: WebhookExecution[] = [];
  private poll: ReturnType<typeof setTimeout> | null = null;

  copied: '' | 'url' | 'curl' = '';

  constructor(private service: WebhooksService, private auth: AuthService) {}

  ngOnChanges(changes: SimpleChanges): void {
    // Another webhook starts on its events, read afresh.
    const before = changes['webhook']?.previousValue as Webhook | undefined;
    if (before?.webhook_id !== this.webhook.webhook_id) {
      this.tab = 'executions';
      this.executions = [];
      this.copied = '';
      void this.load();
    }
  }

  ngOnDestroy(): void {
    if (this.poll) clearTimeout(this.poll);
  }

  async load(): Promise<void> {
    if (this.poll) clearTimeout(this.poll);
    const id = this.webhook.webhook_id;
    this.loading = true;
    this.problem = '';
    try {
      const page = await this.service.executions(id);
      if (id !== this.webhook.webhook_id) return;
      if (page.error) this.problem = page.error;
      else this.executions = page.executions;
    } catch {
      this.problem = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
    if (this.stillWorking) this.poll = setTimeout(() => void this.load(), POLL_MS);
  }

  /** A recent event whose chat has not answered yet. */
  private get stillWorking(): boolean {
    const since = Date.now() - STILL_WORKING_MS;
    return this.executions.some((run) =>
      run.result === 'working' && new Date(run.received_at || 0).getTime() > since);
  }

  // ── Who may do what ─────────────────────────────────────────────────

  get isOwner(): boolean { return this.webhook.role === 'owner'; }
  private may(action: string): boolean { return this.isOwner && this.auth.can(`settings:webhook:${action}`); }
  get mayUpdate(): boolean { return this.may('update'); }
  get mayTest(): boolean { return this.may('test'); }
  get mayShare(): boolean { return this.may('share'); }
  get mayTransfer(): boolean { return this.may('transfer'); }
  get mayRotate(): boolean { return this.may('rotate'); }
  get mayDelete(): boolean { return this.may('delete'); }
  get chatExists(): boolean { return this.isOwner && !!this.webhook.chat_id && this.webhook.chat?.exists !== false; }

  // ── Words ───────────────────────────────────────────────────────────

  when(run: WebhookExecution): string { return relativeDate(run.received_at); }
  whenInFull(run: WebhookExecution): string { return fullDate(run.received_at); }
  result(run: WebhookExecution): { word: string; tone: string } { return resultWord(run); }
  cardState(status: string): string { return cardWord(status); }
  posted(run: WebhookExecution): string { return postedLine(run.data); }
  postedInFull(run: WebhookExecution): string { return JSON.stringify(run.data, null, 2); }

  /** "Answered in 3.2 s"; '' before an answer. */
  took(run: WebhookExecution): string {
    return run.seconds !== null && run.seconds !== undefined ? `answered in ${run.seconds} s` : '';
  }

  /** The reply's first line, for the fold's summary. */
  replyLine(run: WebhookExecution): string {
    return (run.reply || '').split('\n').find((line) => line.trim())?.trim() || '';
  }

  /** The whole address a caller posts to. */
  get url(): string {
    return `${window.location.origin}${this.webhook.url_path}`;
  }

  get trustWord(): string {
    const level = this.webhook.chat?.trust_level;
    return TRUST_CHOICES.find((choice) => choice.value === level)?.name || 'Your chat default';
  }

  get modelWord(): string {
    return this.webhook.chat?.llm?.model || 'Your default model';
  }

  typeWord(type: string): string {
    return FIELD_TYPES.find((choice) => choice.value === type)?.name || type;
  }

  /** The whole call, as shown when the webhook was made, the secret
   *  aside: that is shown once, so the command reads it from the shell. */
  get curl(): string {
    return curlExample(this.url, '$WEBHOOK_SECRET', this.webhook.fields || [], this.webhook.require_signature);
  }

  async copy(what: 'url' | 'curl'): Promise<void> {
    this.copied = (await copyText(what === 'url' ? this.url : this.curl)) ? what : '';
  }
}
