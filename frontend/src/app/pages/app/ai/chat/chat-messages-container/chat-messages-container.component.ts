import {
  Component,
  EventEmitter,
  Input,
  ElementRef,
  ViewChild,
  OnChanges,
  SimpleChanges,
  NgZone,
  AfterViewInit,
  OnDestroy,
  Output,
} from '@angular/core';
import { ChatTurn, PlanStep } from 'src/app/models/chat-protocol';
import { ActivityEntry } from 'src/app/services/chat-state.service';

@Component({
  selector: 'app-chat-messages-container',
  standalone: false,
  templateUrl: './chat-messages-container.component.html',
  styleUrl: './chat-messages-container.component.css',
})
export class ChatMessagesContainerComponent
  implements OnChanges, AfterViewInit, OnDestroy
{
  @Input() loading = false;
  @Input() isProcessing = false;
  @Input() systemText: string | null = null;
  /** The work as it happened: calls under their agents, and notes. */
  @Input() activity: ActivityEntry[] = [];
  /** The work in hand, as the runtime keeps it: each item with its
   *  status and what the trace proved for it. It is shown in the docked
   *  panel — the plan is state, not a second message. */
  @Input() livePlan: PlanStep[] = [];
  @Input() turns: ChatTurn[] = [];
  @Input() hasMore = false;
  @Input() loadingOlder = false;

  /** What this chat can reach, by name. An empty conversation that lists
   *  its own capabilities answers "what can I ask?" — which is the
   *  question somebody staring at a blank chat actually has. */
  @Input() agentNames: string[] = [];
  /** ref → name, for reading a leaked ref as the agent a person knows. */
  @Input() agentLabels: Record<string, string> = {};

  /** What the indicator says: the latest step, or the state of the
   *  mind when no step has been narrated yet. Never "processing your
   *  request" over an answer that already arrived. */
  get liveLabel(): string {
    return this.humanize(this.systemText || 'Thinking…');
  }

  /** Text as a person reads it: a minted agent ref, alone or as the
   *  prefix of a canonical function name, becomes the agent's name. */
  humanize(text: string): string {
    if (!text) return '';
    return text.replace(/agt_[0-9a-f]{6,}(?:__[a-z0-9_]+)?(?:\.([a-z0-9_]+)\.([a-z0-9_]+))?/g,
      (whole, tool, fn) => {
        const ref = whole.split('.')[0].split('__')[0];
        const name = this.agentLabels[ref];
        if (!name) return whole;
        return tool && fn ? `${name} · ${tool}.${fn}` : name;
      });
  }

  /** The whole answer of a turn, for the one copy control it has. */
  turnText(turn: ChatTurn): string {
    return (turn.assistantMessages || [])
      .flatMap((message: any) => (message.parts || []))
      .filter((part: any) => part?.text)
      .map((part: any) => part.text)
      .join('\n\n');
  }
  @Output() loadOlder = new EventEmitter<void>();
  @Output() opener = new EventEmitter<string>();

  /** Openers worth offering: enough to show the shape of a request,
   *  never so many that the page becomes a menu. */
  readonly openers = [
    'What can you help me with?',
    'Which agents can you use in this chat?',
  ];

  get capabilityLine(): string {
    if (!this.agentNames.length) {
      return 'No agents are enabled for this chat — it can talk, but it '
        + 'cannot act in another system yet.';
    }
    const names = this.agentNames.slice(0, 3).join(', ');
    const more = this.agentNames.length - 3;
    return more > 0
      ? `${names} and ${more} more are ready to work in this chat.`
      : `${names} ${this.agentNames.length === 1 ? 'is' : 'are'} ready to work in this chat.`;
  }

  @ViewChild('messagesContainer', { static: false })
  private messagesContainer!: ElementRef<HTMLDivElement>;
  @ViewChild('messagesInner', { static: false })
  private messagesInner?: ElementRef<HTMLDivElement>;

  /** The docked line, opened: the plan as it stands and the steps of
   *  this turn. */
  panelOpen = false;

  togglePanel(): void {
    this.panelOpen = !this.panelOpen;
    if (this.panelOpen) this.openedByPerson = true;
  }

  /** The dock stands wherever there is something to say OR something to
   *  look back on, which in a chat with any history is always. */
  get showDock(): boolean {
    return this.showActivity || this.turns.length > 0 || this.panelOpen;
  }

  /** One line: what is happening, what it came to, or simply the way in. */
  get dockLabel(): string {
    if (this.isProcessing || this.activity.length || this.livePlan.length) {
      return this.activityLine;
    }
    // A turn that needed no agent: only the time it took to answer.
    if (this.lastTurnSeconds) return `Answered · ${this.lastTurnSeconds}s`;
    return 'This turn';
  }

  /** How long the last turn took, once it is over; 0 before any turn. */
  lastTurnSeconds = 0;

  /** How long the current turn has been running, ticking once a second
   *  while the assistant works. */
  turnStartedAt = 0;
  elapsedSeconds = 0;
  private elapsedTimer: any = null;

  private startElapsedClock(): void {
    this.stopElapsedClock();
    this.elapsedTimer = setInterval(() => {
      this.elapsedSeconds = Math.floor((Date.now() - this.turnStartedAt) / 1000);
    }, 1000);
  }

  private stopElapsedClock(): void {
    if (this.elapsedTimer) { clearInterval(this.elapsedTimer); this.elapsedTimer = null; }
  }

  get failedSteps(): number {
    return this.activity.filter((entry) => this.isFailed(entry)).length;
  }

  /** The panel folds on its own once, when the turn ends; after that
   *  it is the person's to open and close. */
  private foldedByUs = false;

  get planDone(): number {
    return this.livePlan.filter((i) => i.status === 'done').length;
  }

  get planWaiting(): boolean {
    return this.livePlan.some((i) => i.status === 'blocked');
  }

  get planSummary(): string {
    const total = this.livePlan.length;
    if (!total) {
      return this.activity.length > 0
        ? this.activity[this.activity.length - 1].text : '';
    }
    const done = this.planDone;
    if (done === total) return `Completed ${total} ${total === 1 ? 'step' : 'steps'}`;
    const blocked = this.livePlan.filter((i) => i.status === 'blocked').length;
    const active = this.livePlan.filter((i) => i.status === 'active').length;
    const parts = [`${done} of ${total} complete`];
    if (active) parts.push(`${active} in progress`);
    if (blocked) parts.push(`${blocked} waiting on you`);
    return parts.join(' · ');
  }

  /** Whether there is anything to say about the work at all. */
  get showActivity(): boolean {
    return this.isProcessing || this.activity.length > 0 || this.livePlan.length > 0;
  }

  /** The one line the answer carries: live while the work goes on,
   *  settled to what it came to — how many steps, how long the calls
   *  took — when it is done. */
  get activityLine(): string {
    if (this.isProcessing) {
      const live = this.livePlan.length ? this.activitySummary : `${this.activitySummary}…`;
      const step = this.activity.length ? `Step ${this.activity.length} · ` : '';
      const clock = this.elapsedSeconds >= 3 ? ` · ${this.elapsedSeconds}s` : '';
      return `${step}${live}${clock}`;
    }
    if (this.planWaiting) return 'Waiting for your input';
    if (this.livePlan.length) return this.planSummary;
    const steps = this.activity.length;
    const ms = this.activity.reduce((total, entry) => total + (entry.durationMs || 0), 0);
    const failed = this.failedSteps;
    return `Done · ${steps} ${steps === 1 ? 'step' : 'steps'}`
      + (failed ? ` · ${failed} failed` : '')
      + (ms ? ` · ${this.durationLabel(ms)}` : '');
  }

  /** A calm, human summary for the collapsed state. The expanded panel
   * owns function names and arguments, so the same technical line is never
   * repeated twice. */
  get activitySummary(): string {
    if (!this.isProcessing) return this.humanize(this.planSummary) || 'Details from this response';
    if (this.livePlan.length) return this.planSummary;

    const detail = this.liveLabel;
    if (!detail || detail === 'Thinking…') return 'Preparing the next step';
    const match = detail.match(/^(.+?)\s*·\s*([a-z0-9_.-]+)/i);
    if (!match) return 'Working through the next step';

    const [, agent, action] = match;
    if (/search|find|list|query/.test(action)) return `Searching with ${agent}`;
    if (/read|get|fetch|open/.test(action)) return `Reviewing information from ${agent}`;
    if (/save|create|update|edit|write|send/.test(action)) return `Preparing an action with ${agent}`;
    return `Using ${agent}`;
  }

  /** Someone opened the steps themselves during this turn, so the end of
   *  it does not close them again. Cleared when the next turn starts. */
  private openedByPerson = false;

  private userNearBottom = true;
  private resizeObs?: ResizeObserver;
  newUpdateCount = 0;

  /** When a scroll we started is expected to have settled. Its own frames
   *  raise scroll events like any other, and reading the thread's position
   *  from one of those says "they have scrolled away" about a movement
   *  nobody made — which switches off the very correction below that
   *  copes with a table arriving late. */
  private autoScrollUntil = 0;

  private scrollingOurselves(): boolean {
    return Date.now() < this.autoScrollUntil;
  }

  constructor(private ngZone: NgZone) {}

  /** How long a call took, as a person reads a stopwatch. */
  durationLabel(ms: number): string {
    const s = ms / 1000;
    if (s < 10) return `${s.toFixed(1)}s`;
    if (s < 60) return `${Math.round(s)}s`;
    return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  }

  isFailed(entry: ActivityEntry): boolean {
    return entry.status === 'error' || entry.status === 'failed';
  }

  ngOnChanges(changes: SimpleChanges): void {
    // A new message arrived → scroll (always for the user's own message; for AI
    // replies only when the user is already following along near the bottom).
    if (changes['turns']) {
      requestAnimationFrame(() => {
        if (this.resizeObs && this.messagesInner) {
          this.resizeObs.observe(this.messagesInner.nativeElement);
        }
      });
      const curr: ChatTurn[] = changes['turns'].currentValue || [];
      const prev: ChatTurn[] = changes['turns'].previousValue || [];
      const additions = this.activityCount(curr) - this.activityCount(prev);
      if (additions > 0) {
        const isPrepend = !!prev.length && curr.some((turn, index) =>
          turn?.id === prev[0]?.id && index > 0);
        if (isPrepend && this.messagesContainer) {
          const el = this.messagesContainer.nativeElement;
          const oldHeight = el.scrollHeight;
          requestAnimationFrame(() => el.scrollTop += el.scrollHeight - oldHeight);
          return;
        }
        const actor = curr[curr.length - 1]?.userMessage ? 'user' : 'ai';
        if (actor === 'user' || this.userNearBottom) {
          this.deferScrollToBottom(actor !== 'user');
          this.newUpdateCount = 0;
        } else {
          this.newUpdateCount += additions;
        }
      }
    }

    // A turn started or ended: the elapsed clock on the live line runs
    // from the moment the person sent, so a slow first thought is seen
    // to be taking time rather than doing nothing.
    if (changes['isProcessing']) {
      if (this.isProcessing) {
        this.turnStartedAt = Date.now();
        this.elapsedSeconds = 0;
        this.lastTurnSeconds = 0;
        this.startElapsedClock();
      } else {
        this.stopElapsedClock();
        if (this.turnStartedAt) {
          this.lastTurnSeconds = Math.round((Date.now() - this.turnStartedAt) / 1000);
        }
      }
    }

    // The processing/system indicator renders below the messages, so reveal it
    // as it appears or updates — otherwise the live status gets buried offscreen.
    const startedProcessing =
      changes['isProcessing']?.currentValue && !changes['isProcessing']?.previousValue;
    if ((startedProcessing || changes['activity']) && this.userNearBottom) {
      this.deferScrollToBottom(true);
    }

    // The steps open while the work is running and fold back to one line
    // when it stops. Those are the two moments: during, the steps ARE
    // the content and there is nothing else to look at; at the end, the
    // answer is, and the steps that produced it should not be standing
    // in front of it.
    //
    // Both halves had to be done by hand before. Opening, because the
    // turn began folded; closing, because the fold waited on a plan with
    // every item done — and a turn with no plan had none to settle,
    // while a turn that ended by ASKING something left the item open for
    // an answer that had not come yet. So the steps sat over the very
    // words the person was waiting to read, often unnoticed.
    if (changes['livePlan'] || changes['isProcessing']) {
      if (startedProcessing) {
        this.panelOpen = true;
        this.foldedByUs = false;
        this.openedByPerson = false;
      } else if (!this.isProcessing && !this.foldedByUs
                 && !this.openedByPerson) {
        this.panelOpen = false;
        this.foldedByUs = true;
      }
    }

  }

  ngAfterViewInit() {
    if (!this.messagesContainer) return;
    this.deferScrollToBottom(false);

    const el = this.messagesContainer.nativeElement;
    this.ngZone.runOutsideAngular(() => {
      // Scroll when container itself resizes (window resize, sidebar toggle, etc.)
      this.resizeObs = new ResizeObserver(() => {
        if (this.userNearBottom) this.scrollToBottom(false);
      });
      this.resizeObs.observe(el);
      if (this.messagesInner) this.resizeObs.observe(this.messagesInner.nativeElement);
    });
  }

  /** How many messages the turns hold between them. */
  private activityCount(turns: ChatTurn[]): number {
    return turns.reduce((count, turn) =>
      count + (turn.userMessage ? 1 : 0) + turn.assistantMessages.length, 0);
  }

  ngOnDestroy() {
    this.resizeObs?.disconnect();
    this.stopElapsedClock();
  }

  showJumpToLatest = false;

  onScroll() {
    if (!this.messagesContainer) return;
    const el = this.messagesContainer.nativeElement;
    const threshold = 100;
    // Only a person's own scroll decides whether to keep following the
    // foot of the thread. Ours does not get a vote.
    if (!this.scrollingOurselves()) {
      this.userNearBottom =
        el.scrollTop + el.clientHeight >= el.scrollHeight - threshold;
    }
    // Reveal the "jump to latest" affordance once the user scrolls up a bit.
    this.showJumpToLatest = el.scrollHeight - (el.scrollTop + el.clientHeight) > 240;
    if (this.userNearBottom) this.newUpdateCount = 0;
    if (el.scrollTop < 100 && this.hasMore && !this.loadingOlder) this.loadOlder.emit();
  }

  jumpToLatest(): void {
    this.showJumpToLatest = false;
    this.newUpdateCount = 0;
    this.scrollToBottom(true);
  }

  /** Wait for DOM layout before scrolling. Two frames, not one: the
   *  thread and its "loading" flag flip in the same change-detection
   *  pass, so after a single frame the messages are in the DOM but the
   *  browser has not laid them out yet and scrollHeight still answers
   *  for the skeleton. */
  private deferScrollToBottom(smooth = false) {
    this.ngZone.runOutsideAngular(() => {
      requestAnimationFrame(() =>
        requestAnimationFrame(() => this.scrollToBottom(smooth)));
    });
  }

  private scrollToBottom(smooth = false) {
    if (!this.messagesContainer) return;
    const el = this.messagesContainer.nativeElement;
    // Going to the foot of the thread IS following it — say so before the
    // move, so the frames it raises cannot argue otherwise, and so that
    // whatever grows afterwards is still chased down.
    this.userNearBottom = true;
    this.autoScrollUntil = Date.now() + (smooth ? 700 : 150);
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }
}
