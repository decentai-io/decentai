import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import {
  ActivityKind, ApprovalCard, ChatJob, ChatMessage, ChatSnapshot, ChatTurn,
  MessagePart, PlanStep, Source,
} from 'src/app/models/chat-protocol';

/** One line of the work, as the page shows it: a call and what came
 *  of it — its agent, the agent's own latest line, how it ended and how
 *  long it took — or a single note. */
export interface ActivityEntry {
  id: string;
  text: string;
  agent?: string;
  detail?: string;
  status?: string;
  durationMs?: number;
  helper?: boolean;
  /** The plan item that was active when this step began — how the panel
   *  keeps each task's steps with it once the work has moved on. The
   *  runtime attributes evidence the same way. */
  item?: string;
}

export interface InlineChatError {
  id: number;
  scope: 'connection' | 'composer' | 'assistant' | 'approval';
  code: string;
  message: string;
  retryable: boolean;
}

/** The page's view of one conversation. There are no turns in the
 *  assistant model — one mind, working or idle — so what the page
 *  tracks is whether it is working, what it is doing, and what it is
 *  waiting on. */
export interface ChatViewState {
  loading: boolean;
  messages: ChatMessage[];
  turns: ChatTurn[];
  approvals: ApprovalCard[];
  jobs: ChatJob[];
  /** The mind is advancing — bracketed by the door's working/idle. */
  working: boolean;
  activity: ActivityEntry[];
  liveText: string | null;
  /** The work in hand, as the runtime last recorded it. */
  plan: PlanStep[];
  /** The assistant paused in the middle of work, and when it carries
   *  on by itself (milliseconds, as Date counts). */
  sleeping: { until: number; why: string } | null;
  latestEventSeq: number | null;
  errors: InlineChatError[];
}

const initialState = (): ChatViewState => ({
  loading: true,
  messages: [],
  turns: [],
  approvals: [],
  jobs: [],
  working: false,
  activity: [],
  liveText: null,
  plan: [],
  sleeping: null,
  latestEventSeq: null,
  errors: [],
});

@Injectable({ providedIn: 'root' })
export class ChatStateService {
  readonly state$ = new BehaviorSubject<ChatViewState>(initialState());
  private errorId = 0;
  private noteId = 0;
  private sleepTimer: ReturnType<typeof setTimeout> | null = null;

  get value(): ChatViewState { return this.state$.value; }

  reset(): void {
    this.setSleeping(null);
    this.state$.next(initialState());
  }

  load(snapshot: ChatSnapshot, approvals: ApprovalCard[]): void {
    this.patch({
      loading: false,
      messages: snapshot.messages.messages,
      turns: this.buildTurns(snapshot.messages.messages),
      approvals,
      latestEventSeq: snapshot.latest_event_seq,
      activity: [],
      liveText: null,
      errors: [],
    });
  }

  acceptSequence(seq: unknown): boolean {
    if (typeof seq !== 'number') return true;
    const current = this.value.latestEventSeq;
    if (current !== null && seq <= current) return false;
    this.patch({ latestEventSeq: seq });
    return true;
  }

  replaceMessages(messages: ChatMessage[]): void {
    this.patch({
      messages,
      turns: this.buildTurns(messages),
    });
  }

  replaceApprovals(approvals: ApprovalCard[]): void {
    this.patch({ approvals });
  }

  /** The door's present tense, on attach: what runs, which cards wait,
   *  and whether the mind is advancing right now. The working/idle
   *  events that say so happened before this page was listening, so a
   *  chat opened in the middle of a turn learns it here. A door that
   *  does not say leaves what the page already believed. */
  hello(jobs: ChatJob[], cards: ApprovalCard[], plan: PlanStep[],
        working?: boolean): void {
    const known = new Set(this.value.approvals.map((card) => card.approval_id));
    const approvals = [
      ...this.value.approvals,
      ...cards.filter((card) => !known.has(card.approval_id)),
    ];
    const live = typeof working === 'boolean' ? working : this.value.working;
    this.patch({
      jobs,
      approvals,
      working: live,
      plan: Array.isArray(plan) ? plan : this.value.plan,
      liveText: jobs.length ? 'Working in the background…'
        : live ? this.value.liveText || 'Thinking…'
        : working === false ? null : this.value.liveText,
    });
  }

  /** Sent: the person's own words go up at once, under the id the page
   *  gave the submission. The server is the author of record and its
   *  message replaces this one when it lands (matched on that id), but
   *  nobody should watch their sentence vanish into a composer and wait
   *  on a round trip — least of all one that may be installing an agent
   *  — to learn it was heard. */
  startSubmission(clientMessageId: string, text = '',
                  parts: MessagePart[] = []): void {
    // No words, nothing to show: a caller that names only the
    // submission (a test, a resend) gets the old behaviour exactly.
    if (!text.trim() && !parts.length) {
      this.patch({ working: true, activity: [], liveText: 'Sending…' });
      this.clearErrors('composer');
      return;
    }
    const pending = {
      message_id: `pending:${clientMessageId}`,
      client_message_id: clientMessageId,
      chat_id: '',
      actor: 'user',
      parts: [{ type: 'markdown', content: text, text }, ...parts],
      sequence: Number.MAX_SAFE_INTEGER,
      pending: true,
    } as unknown as ChatMessage;
    const messages = [...this.value.messages, pending];
    if (this.value.working) {
      // Words mid-turn steer the work in progress: the steps so far
      // and the live line stay, since the turn they narrate goes on.
      this.patch({ messages, turns: this.buildTurns(messages) });
      this.clearErrors('composer');
      return;
    }
    this.patch({
      messages,
      turns: this.buildTurns(messages),
      working: true,
      activity: [], liveText: 'Sending…',
    });
    this.clearErrors('composer');
  }

  /** The send never left. Take the words back rather than leave them
   *  sitting there as though they had been heard, with an error beside
   *  them saying they had not. */
  dropSubmission(clientMessageId: string): void {
    const messages = this.value.messages.filter((message) =>
      message.client_message_id !== clientMessageId);
    if (messages.length === this.value.messages.length) return;
    this.patch({ messages, turns: this.buildTurns(messages) });
  }

  /** The assistant went to sleep until a time (epoch seconds, as the
   *  runtime counts), or a stop ended the sleep. Nothing announces the
   *  waking itself, so the page lets go of it when the time comes. */
  setSleeping(until: number | null | undefined, why = ''): void {
    if (this.sleepTimer) {
      clearTimeout(this.sleepTimer);
      this.sleepTimer = null;
    }
    const at = typeof until === 'number' ? until * 1000 : 0;
    const left = at - Date.now();
    if (left <= 0) {
      if (this.value.sleeping) this.patch({ sleeping: null });
      return;
    }
    this.patch({ sleeping: { until: at, why } });
    this.sleepTimer = setTimeout(() => this.setSleeping(null), left);
  }

  /** The mind started advancing (working) or came to rest (idle). A
   *  reply ends nothing — a say does not finish — so only idle clears
   *  the indicator. */
  setWorking(working: boolean): void {
    if (working) {
      this.patch({
        working: true,
        liveText: this.value.liveText || 'Thinking…',
      });
      return;
    }
    // The steps stay. A turn that has finished is exactly when somebody
    // wants to read what it did, and throwing the list away the instant
    // it settled left the activity panel with nothing to show. The next
    // message clears them, not the end of this one.
    this.patch({ working: false, liveText: null });
  }

  /** The assistant could not go on: the indicator stops and the person
   *  is told. */
  fail(code: string, message: string, retryable: boolean): void {
    this.patch({ working: false, activity: [], liveText: null });
    this.addError('assistant', code, message, retryable);
  }

  /** A note in the timeline — something the page itself says, or a
   *  retired `progress` line from a replay. */
  addProgress(description: string): void {
    if (!description) return;
    const activity = this.value.activity;
    if (activity[activity.length - 1]?.text === description) {
      this.patch({ liveText: description });
      return;
    }
    this.patch({
      activity: [...activity.slice(-199), { id: `note_${++this.noteId}`, text: description }],
      liveText: description,
    });
  }

  /** One `activity` event into the timeline. The lines of one call (or
   *  job) share its id: the start opens the entry, the agent's own lines
   *  become its detail, the finish settles it with status and time. */
  addActivity(event: {
    kind: ActivityKind; text: string; status?: string; duration_ms?: number;
    source?: Source; child?: string;
  }): void {
    const text = String(event.text || '').trim();
    if (!text) return;
    const source: Source = event.source || { kind: 'assistant' };
    const id = source.call_id || source.job_id || '';
    const helper = source.kind === 'helper' || !!event.child;
    const activity = [...this.value.activity];
    const index = id ? activity.findIndex((entry) => entry.id === id) : -1;
    const finished = event.kind === 'call_finished' || event.kind === 'job_finished';

    if (index >= 0 && (event.kind === 'agent_progress' || finished)) {
      activity[index] = finished
        ? { ...activity[index], status: event.status, durationMs: event.duration_ms }
        : { ...activity[index], detail: text };
      this.patch({ activity });
      return;
    }
    const entry: ActivityEntry = {
      id: id && index < 0 ? id : `note_${++this.noteId}`,
      text, agent: source.agent_name, helper,
      status: finished ? event.status : undefined,
      durationMs: finished ? event.duration_ms : undefined,
      item: this.value.plan.find((step) => step.status === 'active')?.id,
    };
    this.patch({
      activity: [...activity.slice(-199), entry],
      liveText: helper ? `Helper: ${text}` : text,
    });
  }

  clearProgress(): void { this.patch({ activity: [], liveText: null }); }

  /** The kill switch answered: nothing runs, nothing waits, and the
   *  page says so in one line. */
  stopped(summary: string): void {
    this.patch({ working: false, jobs: [], approvals: [], activity: [], liveText: null });
    this.addProgress(summary);
  }

  /** An assistant message landed while the mind is still working: what
   *  remains is the wrap-up — recording the plan, deciding it is done
   *  — not another answer. The indicator says so instead of claiming
   *  to be processing a request that was already answered. */
  answered(): void {
    if (this.value.working) this.patch({ liveText: 'Finishing up…' });
  }

  /** The door preparing an agent before the hello: the wait is shown
   *  as a step while it lasts, cleared when the agent is ready, and
   *  told as an error when it is not — an agent missing from the
   *  roster must never be a silent absence. */
  agentStatus(phase: string, text: string): void {
    if (phase === 'ready') {
      this.clearProgress();
      return;
    }
    if (phase === 'failed') {
      this.clearProgress();
      this.addError('assistant', 'agent_failed', text || 'An agent could not be prepared.', false);
      return;
    }
    this.addProgress(text);
  }

  /** The work in hand, as the runtime last recorded it. Empty when the
   *  plan is finished — a completed plan is not the work in hand. */
  setPlan(steps: PlanStep[]): void {
    this.patch({ plan: Array.isArray(steps) ? steps : [] });
  }

  addError(
    scope: InlineChatError['scope'], code: string, message: string,
    retryable = false,
  ): void {
    const duplicate = this.value.errors.some(
      (error) => error.scope === scope && error.code === code
        && error.message === message,
    );
    if (duplicate) return;
    this.patch({ errors: [...this.value.errors, {
      id: ++this.errorId, scope, code, message, retryable,
    }] });
  }

  dismissError(id: number): void {
    this.patch({ errors: this.value.errors.filter((error) => error.id !== id) });
  }

  clearErrors(scope?: InlineChatError['scope']): void {
    this.patch({ errors: scope
      ? this.value.errors.filter((error) => error.scope !== scope) : [] });
  }

  private patch(changes: Partial<ChatViewState>): void {
    this.state$.next({ ...this.value, ...changes });
  }

  /** Turns are a reading aid, not a record: a user message and what
   *  the assistant said after it. What the assistant said before anyone
   *  spoke — a scheduled run, a background job reporting in — is a turn
   *  of its own with no user message, rather than nothing at all. Cards
   *  are not part of any turn: what waits for the person sits in one
   *  strip above the composer (chat-detail), so a card never rides up
   *  the thread with the answer it interrupted. */
  private buildTurns(messages: ChatMessage[]): ChatTurn[] {
    const turns: ChatTurn[] = [];
    for (const message of messages) {
      if (message.actor === 'user') {
        turns.push({ id: message.message_id, userMessage: message,
          assistantMessages: [] });
      } else if (message.actor === 'ai') {
        if (!turns.length) {
          turns.push({ id: message.message_id, userMessage: null,
            assistantMessages: [] });
        }
        turns[turns.length - 1].assistantMessages.push(message);
      }
    }
    return turns;
  }
}
