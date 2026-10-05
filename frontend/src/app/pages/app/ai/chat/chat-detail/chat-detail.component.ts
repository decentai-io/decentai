// chat-detail.component.ts — the chat page, on the platform protocol
// (docs/system/chat-session.md).
//
// AI:Chat:Open loads the durable state and the session contract; the
// WebSocket carries the conversation: AI:Chat:Input out, AI:Chat:Event in
// (the runtime door's frames, relayed). A decision or a stop goes through
// the HTTP gateway, where the backend records it before the runtime hears.
import { Component, NgZone, OnDestroy, OnInit } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { ActivatedRoute, Router } from '@angular/router';
import { AlertComponent } from 'src/app/components/alert/alert.component';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { ProfileService } from 'src/app/services/profile.service';
import { CHAT_PROTOCOL_VERSION, ChatInputCommand, FilePart, parseChatEvent, SessionContract, toApprovalCard, ChatJob } from 'src/app/models/chat-protocol';
import { Subscription } from 'rxjs';
import { ChatTurn, FileChoice, PlanStep } from 'src/app/models/chat-protocol';
import { ScreenInputEvent, ScreenTab, ScreenView } from '../chat-screen/chat-screen.component';
import { ViewportService } from 'src/app/services/viewport.service';
import {
  ChatConnectionService, ChatConnectionState,
} from 'src/app/services/chat-connection.service';
import {
  ActivityEntry, ChatStateService, InlineChatError,
} from 'src/app/services/chat-state.service';

import { ChatActivityDialogComponent } from '../chat-activity-dialog/chat-activity-dialog.component';
import { ChatAgentsDialogComponent } from '../chat-agents-dialog/chat-agents-dialog.component';
import { ChatSkillsDialogComponent } from '../chat-skills-dialog/chat-skills-dialog.component';
import { ChatModel } from '../chat-model-picker/chat-model-picker.component';

@Component({
  selector: 'app-chat-detail',
  standalone: false,
  templateUrl: './chat-detail.component.html',
  styleUrls: ['./chat-detail.component.css'],
})
export class ChatDetailComponent implements OnInit, OnDestroy {
  title: string = '';
  chat_id: string | null = null;
  chat: any = null;

  /** Inline header rename — Enter/blur saves, Escape cancels. */
  isEditingTitle = false;
  titleDraft = '';

  messages: any[] = [];
  turns: ChatTurn[] = [];
  hasOlderMessages = false;
  olderMessagesBefore: number | null = null;
  loadingOlderMessages = false;
  liveSystemText: string | null = null;
  activity: ActivityEntry[] = [];

  /** The cards waiting on this person (approval_requested, hello). */
  pendingApprovals: any[] = [];

  /** A screen an agent is showing right now (call.screen): the latest
   *  frame, drawn as it comes and kept nowhere. */
  screen: ScreenView | null = null;
  /** The person closed the panel. It stays closed for the call that
   *  was showing — its next frame must not undo the click — and opens
   *  again for a new call's first frame, or when asked. */
  screenHidden = false;
  private hiddenCallId = '';

  /** A phone: the header keeps only what a thumb needs, the rest goes
   *  into the menu. */
  isNarrow = false;
  /** Room for two columns: a live view stands beside the thread instead
   *  of between the thread and the composer. */
  isWide = false;

  get showSideScreen(): boolean {
    return this.isWide && !!this.screen && !this.screenHidden;
  }

  /** The picture's width over its height: the side column is as wide
   *  as the picture is at the column's full height, so a large window
   *  shows a large browser. */
  get screenRatio(): number {
    const width = Number(this.screen?.width) || 0;
    const height = Number(this.screen?.height) || 0;
    return width > 0 && height > 0 ? width / height : 1.6;
  }
  /** The live view folded to its head: beside the thread, the column
   *  gives the room back. */
  screenMinimized = false;
  /** Whether the person holds the browser. Kept here, not in the view:
   *  the view is built again when it moves beside the thread or back,
   *  and the hand on the browser stays where it was. */
  screenTaken = false;

  /** The tabs a frame tells of, as the live view shows them. The same
   *  list object is kept while nothing in it changed: a frame arrives
   *  ten times a second, and a strip rebuilt each time would take a
   *  click away from under the pointer. */
  private screenTabs(told: unknown): ScreenTab[] {
    const tabs: ScreenTab[] = (Array.isArray(told) ? told : [])
      .filter((tab) => tab && Number(tab.index) >= 1)
      .map((tab) => ({
        index: Number(tab.index), title: String(tab.title || tab.address || 'New tab'),
        address: String(tab.address || ''), active: !!tab.active,
      }));
    const before = this.screen?.tabs || [];
    const same = before.length === tabs.length && before.every((tab, i) =>
      tab.index === tabs[i].index && tab.title === tabs[i].title
      && tab.address === tabs[i].address && tab.active === tabs[i].active);
    return same ? before : tabs;
  }

  /** The cards still waiting on the person — shown in one strip above
   *  the composer, wherever the thread is scrolled. Decided cards leave
   *  the strip; the audit trail keeps them. */
  get waitingApprovals(): any[] {
    return this.pendingApprovals.filter((card) =>
      !['approved', 'denied', 'answered', 'expired'].includes(card?.status));
  }
  /** Last event sequence this page has processed — replay/live dedupe. */
  lastEventSeq: number | null = null;

  /** The session contract AI:Chat:Open answered: the model, the agents
   *  and functions this person may call, their powers here. */
  contract: SessionContract | null = null;

  /** Chat trust level (0-3) — lives in the chat's config; changing it
   *  updates the config and reconnects the chat. */
  trustLevel = 1;
  /** A function runs without asking when its level is at or below the
   *  chat's; each hint says what that leaves to ask about. The same
   *  four, in fewer words, as Settings → Chat configuration offers. */
  readonly trustOptions = [
    { level: 0, label: 'Ask for every change', hint: 'Reads run; every change asks' },
    { level: 1, label: 'Standard', hint: 'Ordinary changes run; wider ones ask' },
    { level: 2, label: 'Trusted', hint: 'Wider changes run; outside actions ask' },
    { level: 3, label: 'Autonomous', hint: 'Everything runs without asking' },
  ];

  /** How many steps one message may take before the chat stops and
    *  asks to continue. It lives in the chat's config as `max_turns`;
    *  the backend's standard is 20, which a chain of agents can spend
    *  on one file each. */
  turnBudget = 20;
  readonly turnBudgetOptions = [
    { turns: 20, label: 'Standard', hint: '20 steps' },
    { turns: 40, label: 'Long', hint: '40 steps' },
    { turns: 60, label: 'Very long', hint: '60 steps' },
    { turns: 100, label: 'Longest', hint: '100 steps' },
    { turns: 0, label: 'Unlimited', hint: 'Runs until done, or until you stop it' },
  ];

  /** How many skills the assistant is shown each turn. It lives in the
   *  chat's config as `max_skills`; the platform's standard is 40, and
   *  0 lists every skill the person can see. */
  skillsCap = 40;
  readonly skillsCapOptions = [
    { rows: 40, label: 'Standard', hint: '40 skills listed' },
    { rows: 80, label: 'More', hint: '80 skills listed' },
    { rows: 160, label: 'Many', hint: '160 skills listed' },
    { rows: 0, label: 'All', hint: 'Every skill you can see' },
  ];

  isSocketReady = false;
  /** The work in hand, as the runtime records it moving. */
  livePlan: PlanStep[] = [];

  isReconnecting = false;
  /** The FIRST connect of a chat, which has not been interrupted by
   *  anything and should not be announced as though it had. */
  isConnecting = false;
  connectionState: ChatConnectionState = 'disconnected';
  inlineErrors: InlineChatError[] = [];

  isLoading = false;
  /** Background jobs the chat holds — the strip at the top, and the
   *  header's word on it. */
  jobs: ChatJob[] = [];
  activeJobs = 0;

  jobLabel(job: ChatJob): string {
    if (job.kind === 'assistant') return 'A helper assistant on its own goal';
    return job.function || 'A function';
  }

  /** The header's one word on what this chat needs: cards first,
   *  since they wait on the person; else whether work goes on. A card
   *  already decided waits on nobody and is not counted. */
  get attentionLabel(): string {
    const cards = this.waitingApprovals.length;
    if (cards) return cards === 1 ? '1 waiting for you' : `${cards} waiting for you`;
    if (this.isLoading || this.activeJobs) return 'Working';
    if (this.sleeping) {
      const until = new Date(this.sleeping.until).toLocaleTimeString(
        [], { hour: '2-digit', minute: '2-digit' });
      return `Sleeping until ${until}`;
    }
    return '';
  }

  /** What the assistant said it is waiting for, shown on the pill. */
  get attentionHint(): string {
    return this.attentionLabel.startsWith('Sleeping') ? this.sleeping?.why || '' : '';
  }
  isChatLoading = true;
  sleeping: { until: number; why: string } | null = null;

  private messageLookup: Map<any, number> = new Map();
  /** True from "the person hit send" to "their message is a turn" —
   *  including the queued first message of a brand-new chat, which
   *  waits for the socket before it can even be sent. The empty-state
   *  must never show inside that window. */
  get sendingFirstMessage(): boolean {
    return this.pendingInitialMessage !== null;
  }

  private pendingInitialMessage: { query: string; uploads?: any[] } | null =
    null;
  /** Files staged into the composer by whoever opened this chat (the
   *  Files page's "Use in a chat"): chips, not a message — the person
   *  says what to do with them. */
  stagedFiles: FileChoice[] | null = null;
  /** Decision submission is idempotent in the UI as well as the backend.
   *  `resolving` blocks double-clicks while HTTP is in flight; `resolved`
   *  is a tombstone so a delayed/replayed request cannot recreate a card
   *  the user has already answered. */
  private resolvingApprovalIds = new Set<string>();
  private resolvedApprovalIds = new Set<string>();
  private subscriptions = new Subscription();

  /** The state and the connection are shared by every chat page, and
   *  they outlive this one: a request still in flight when the person
   *  leaves must not write into them afterwards. `opening` counts the
   *  reads of the chat, so an older read that answers late is dropped. */
  private destroyed = false;
  private opening = 0;
  /** The chat was just read from its records, so the connection that
   *  follows has nothing to catch up on; any later one does. */
  private snapshotFresh = false;

  constructor(
    private aiSession: AiSessionService,
    private profileService: ProfileService,
    private route: ActivatedRoute,
    private router: Router,
    private dialog: MatDialog,
    private ngZone: NgZone,
    private connection: ChatConnectionService,
    private state: ChatStateService,
    private viewport: ViewportService,
  ) {}

  ngOnInit(): void {
    this.state.reset();
    this.subscriptions.add(this.viewport.narrow$.subscribe((narrow) => (this.isNarrow = narrow)));
    this.subscriptions.add(this.viewport.wide$.subscribe((wide) => (this.isWide = wide)));
    this.subscriptions.add(this.state.state$.subscribe((state) => {
      this.messages = state.messages as any[];
      this.turns = state.turns;
      this.pendingApprovals = state.approvals;
      this.jobs = state.jobs;
      this.activeJobs = state.jobs.length;
      this.activity = state.activity;
      this.liveSystemText = state.liveText;
      this.isLoading = state.working;
      this.isChatLoading = state.loading;
      this.lastEventSeq = state.latestEventSeq;
      this.inlineErrors = state.errors;
      this.livePlan = state.plan;
      this.sleeping = state.sleeping;
    }));
    this.subscriptions.add(this.connection.state$.subscribe((connection) => {
      this.connectionState = connection;
      this.isSocketReady = connection === 'connected';
      // A new chat opens on 'connecting'; only an established
      // connection that dropped is 'reconnecting'. Telling the user the
      // connection is being restored while it is being made for the
      // first time reports a fault that has not happened.
      this.isConnecting = connection === 'connecting';
      this.isReconnecting = connection === 'reconnecting';
      // Pictures travel on the socket and are never replayed, and so is
      // the word that they stopped: a view left as it was across a lost
      // socket would go on saying "watching" over a browser that may be
      // gone. It comes back with the next frame.
      if (connection !== 'connected') this.settleScreen();
      if (connection === 'connected') {
        this.state.clearErrors('connection');
        if (this.snapshotFresh) this.snapshotFresh = false;
        else void this.readNewestMessages();
      } else if (connection === 'reconnecting') {
        this.state.addError('connection', 'reconnecting',
          'The connection was interrupted. Reconnecting…', true);
      }
      if (connection === 'connected' && this.pendingInitialMessage) {
        const pending = this.pendingInitialMessage;
        this.pendingInitialMessage = null;
        this.onPrompt(pending);
      }
      if (connection === 'archived') {
        this.state.clearProgress();
        this.state.addError('connection', 'chat_archived',
          'This chat is archived. Restore it to continue.');
      } else if (connection === 'unauthorized') {
        this.state.addError('connection', 'connection_unauthorized',
          'You no longer have access to this conversation.');
      } else if (connection === 'replaced') {
        this.state.clearProgress();
        this.state.addError('connection', 'connection_replaced',
          'This chat is open in another tab or window. Refresh and '
          + 'reconnect to continue here.');
      } else if (connection === 'missing') {
        this.state.clearProgress();
        this.state.addError('connection', 'chat_missing',
          'This chat no longer exists, or is no longer yours to open.');
      } else if (connection === 'refused') {
        this.state.clearProgress();
        this.state.addError('connection', 'connection_refused',
          'The connection was closed over a message it could not read. '
          + 'Reconnect to continue.', true);
      }
    }));
    // The event log had moved past where this page stopped: what was
    // missed is read back from the records instead.
    this.subscriptions.add(this.connection.gap$.subscribe(() => {
      void this.initChat();
    }));
    this.subscriptions.add(this.connection.events$.subscribe((raw) =>
      this.handleSocketMessage(raw),
    ));
    this.pendingInitialMessage = history.state?.initialMessage || null;
    this.stagedFiles = Array.isArray(history.state?.stagedFiles)
      ? history.state.stagedFiles : null;
    if (this.pendingInitialMessage || this.stagedFiles) {
      // Taken once. A reload keeps the history entry's state, and the
      // first message must not be sent again every time the page is.
      const { initialMessage, stagedFiles, ...rest } = history.state || {};
      history.replaceState(rest, '');
    }
    this.chat_id = this.route.snapshot.paramMap.get('chat_id');
    if (!this.chat_id) {
      this.onError('No chat ID provided in the URL.');
      this.router.navigate(['ai/chats']);
      return;
    }

    // The chats page is a likely next destination from an open
    // conversation. Warm its tiny summary list while the user is chatting
    // so opening it never starts with an empty loading state.
    if (!this.aiSession.peekChats()) {
      void this.aiSession.listChats().catch(() => undefined);
    }

    this.initChat();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.clearKillTimer();
    // Leaving with the browser in hand gives it back: the agent would
    // otherwise wait on a person who is no longer there.
    if (this.screenTaken && this.screen && !this.screen.idle) {
      this.connection.send('AI:Chat:Screen', {
        call_id: this.screen.call_id,
        events: [{ type: 'control', action: 'release' }],
      });
    }
    this.subscriptions.unsubscribe();
    this.connection.disconnect();
  }

  // =========================
  // Init
  // =========================
  private async initChat(): Promise<void> {
    const opening = ++this.opening;
    this.isChatLoading = true;
    try {
      const snapshotRes = await this.aiSession.openChat(this.chat_id!);
      if (this.destroyed || opening !== this.opening) return;
      if (snapshotRes.error || !snapshotRes.data) {
        this.onError(snapshotRes.error || 'Unable to load the conversation.');
        this.router.navigate(['ai/chats']);
        return;
      }
      const snapshot = snapshotRes.data;
      this.hasOlderMessages = snapshot.messages.has_more;
      this.olderMessagesBefore = snapshot.messages.next_before;
      this.chat = snapshot.chat || {};
      this.title = this.chat.title || 'New Chat';
      const config = this.chat.config || {};
      this.trustLevel = Number.isInteger(config.trust_level)
        ? config.trust_level
        : 1;
      this.turnBudget = Number.isInteger(config.max_turns)
        ? config.max_turns
        : 20;
      this.skillsCap = Number.isInteger(config.max_skills)
        ? config.max_skills
        : 40;
      this.messageLookup.clear();

      // The cards still waiting survive disconnects: rebuilt from the
      // record, and again from the door's hello on every connect.
      this.contract = snapshot.contract || null;
      const approvals = snapshot.approvals.map((card) =>
        toApprovalCard(card, this.agentLabels));

      // Adopt the event log's high-water mark: records rehydration
      // just rebuilt the UI, so replay only matters for later
      // reconnects within this page.
      this.state.load(snapshot, approvals);
      this.messages = [];
      for (const message of snapshot.messages.messages) {
        this.upsertMessage(message, false);
      }
      this.state.replaceMessages(this.messages as any);

      // No model resolves for this person: the contract says so at the
      // door, in a sentence — steer them to settings up front.
      if (!this.contract?.llm) {
        this.state.addError('composer', 'llm_not_configured',
          this.contract?.llm_missing
          || 'Choose a language model from LLM settings to continue.');
      }

      this.snapshotFresh = true;
      this.connection.connect(this.chat_id!, () => this.state.value.latestEventSeq);
    } catch (err) {
      if (this.destroyed || opening !== this.opening) return;
      this.state.addError('connection', 'snapshot_failed',
        'Unable to load the conversation.');
      this.alert(err);
      this.router.navigate(['ai/chats']);
    }
  }

  private handleSocketMessage(raw: string): void {
    const data = parseChatEvent(raw);
    if (!data) return;

    // Sequenced events dedupe across replay and live delivery; events
    // without a sequence are connection-transient and always process.
    if (!this.state.acceptSequence(data.seq)) return;

    switch (data.event) {
      case 'hello':
        // The door's present tense on attach: what runs in the
        // background, which cards wait, where the plan stands, and
        // whether the mind is advancing right now.
        this.state.hello(
          data.active_jobs || [],
          (data.pending_approvals || []).map((card) =>
            toApprovalCard(card, this.agentLabels)),
          data.plan || [],
          data.working,
        );
        this.state.setSleeping(data.sleeping?.until, data.sleeping?.why);
        return;

      case 'sleeping':
        this.state.setSleeping(data.until, data.why);
        return;

      case 'working':
        this.state.setWorking(true);
        return;

      case 'idle':
        // A reply ends nothing — a say does not finish — so this is
        // the one frame that stops the indicator.
        this.state.setWorking(false);
        return;

      case 'error':
        this.state.fail('runtime_error',
          data.detail || 'The assistant reported an error.', true);
        return;

      case 'runtime_unavailable':
      case 'runtime_disconnected':
        this.state.setWorking(false);
        this.settleScreen();
        this.state.addError('connection', data.event,
          data.event === 'runtime_unavailable'
            ? 'The AI runtime is unavailable. History remains readable.'
            : 'The AI runtime disconnected. Your next message reconnects it.',
          true);
        return;

      case 'work_stopped':
        // Reconnecting would only be told the same thing: the way on
        // is to resume, so no reconnect is offered beside the words.
        this.state.setWorking(false);
        this.settleScreen();
        this.state.addError('connection', data.event,
          'You stopped everything. Nothing runs until you resume, from the '
          + 'bar at the top of the page.', false);
        return;

      case 'invalid_input':
        this.state.fail('invalid_input', data.detail || 'Invalid input.', false);
        return;

      case 'activity':
        // The work as it happens, each line saying whose it is; the
        // lines of one call gather under it (contracts/chat.py).
        this.state.addActivity(data);
        // The screen's own call speaking: its words caption the picture,
        // so the person knows what the still frame is waiting on.
        if (data.kind === 'agent_progress' && this.screen
            && data.source?.call_id === this.screen.call_id && data.text) {
          this.screen = { ...this.screen, caption: String(data.text) };
        }
        return;

      case 'progress': {
        // Retired: only a replay of older events still carries it.
        const description = String(data.description || '').trim();
        this.state.addProgress(data.child ? `Helper: ${description}` : description);
        return;
      }

      case 'agent_status':
        // A first open waits on a package fetch and a pip install; the
        // door says so, and a wait with words is not a fault.
        this.state.agentStatus(data.phase || 'installing', String(data.text || ''));
        return;

      case 'plan_updated':
        // What the runtime recorded, not what the model claimed: a step
        // is done because the work behind it came back.
        this.state.setPlan(data.steps || []);
        return;

      case 'memory_saved':
        // The visible half of explicit memory: the user watches the
        // assistant learn something, rather than discovering it later.
        this.state.addProgress(`Remembered: ${data.text}`);
        return;

      case 'schedule_set': {
        const schedule = data.schedule || {};
        const what = schedule.note || schedule.function || 'a check';
        this.state.addProgress(
          schedule.every_seconds ? `Scheduled: ${what}, repeating` : `Scheduled: ${what}`,
        );
        return;
      }

      case 'schedule_removed':
        this.state.addProgress('Schedule removed.');
        return;

      case 'approval_requested':
        this.addApproval(data);
        return;

      case 'question_asked':
        // An agent asking the person mid-call (call.ask): a card like an
        // approval's, answered by a choice or in the person's words.
        this.addApproval({ ...data, kind: 'question' });
        return;

      case 'question_closed':
        this.closeQuestion(String(data.approval_id || ''), data.status);
        return;

      case 'stopped': {
        // The kill switch answered, from here or from another tab.
        const jobs = Number(data.jobs || 0);
        const cards = Number(data.cards || 0);
        const parts = [
          jobs ? `${jobs} background job${jobs === 1 ? '' : 's'} cancelled` : '',
          cards ? `${cards} card${cards === 1 ? '' : 's'} expired` : '',
        ].filter(Boolean);
        this.clearKillTimer();
        this.killing = false;
        // The browser went with the rest, and nothing says so on its own.
        this.settleScreen();
        this.state.setSleeping(null);
        this.state.stopped(parts.length
          ? `Stopped everything: ${parts.join(', ')}.`
          : 'Stopped everything in this chat.');
        return;
      }

      case 'screen_frame': {
        // The present tense of what an agent drives: the newest frame
        // replaces the last, and nothing is kept. The type is one of the
        // contract's two, never whatever the frame says.
        const mime = data.mime === 'image/png' ? 'image/png' : 'image/jpeg';
        // The first picture this page sees says whose hand is on the
        // browser — a page reloaded while the person held it still
        // holds it. After that the page's own word stands: the frames
        // just behind a take or a hand-back still tell the old state.
        if (!this.screen || this.screen.idle) this.screenTaken = !!data.taken;
        this.screen = {
          call_id: String(data.call_id || ''),
          src: `data:${mime};base64,${data.image_base64}`,
          width: Number(data.width) || 1, height: Number(data.height) || 1,
          frame: Number(data.frame) || 0,
          agent_name: data.source?.agent_name || this.agentLabels[data.source?.agent || ''] || '',
          caption: this.screen?.call_id === String(data.call_id || '') ? this.screen.caption : '',
          at: Date.now(),
          tabs: this.screenTabs(data.tabs),
        };
        if (this.screenHidden && this.hiddenCallId !== String(data.call_id || '')) {
          this.screenHidden = false;
        }
        return;
      }

      case 'chat_titled':
        // The runtime named the chat from its content: the header and
        // the list follow, unless a rename is being typed right now.
        if (data.title && !this.isEditingTitle) {
          this.title = String(data.title);
          if (this.chat) this.chat.title = this.title;
        }
        return;

      case 'screen_unavailable':
        this.state.addError('assistant', 'screen_unavailable',
          String(data.detail || 'No agent in this chat can show a browser.'), false);
        return;

      case 'screen_closed':
        // The call ended; the browser behind the picture may stay open
        // for the next run, so the last frame stays, marked idle, until
        // a new frame replaces it.
        if (this.screen?.call_id === String(data.call_id || '')) this.settleScreen();
        return;

      case 'message_created':
        this.onMessageCreated(data.message);
        return;
    }
  }

  // =========================
  // Messages
  // =========================
  private onMessageCreated(message: any): void {
    if (!message) return;
    this.upsertMessage(message);
    if (message.actor === 'ai') this.state.answered();
  }

  /** Every agent this chat can reach, by the ref the platform routes
   *  with — so anything that leaks a ref into text can be read back
   *  as the name a person knows. */
  get agentLabels(): Record<string, string> {
    const labels: Record<string, string> = {};
    for (const [ref, agent] of Object.entries(this.contract?.agents || {})) {
      if ((agent as any)?.name) labels[ref] = String((agent as any).name);
    }
    return labels;
  }

  /** What this chat can reach, named — the contract's join of installed,
   *  granted and enabled, as a person reads it. */
  get enabledAgentNames(): string[] {
    return Object.values(this.contract?.agents || {})
      .map((agent) => agent.name || '')
      .filter(Boolean);
  }

  /** The live view marked idle: its last picture stays, nothing behind
   *  it is claimed to be running, and the header's button opens the
   *  browser again instead of showing a picture that has stopped. */
  private settleScreen(): void {
    if (this.screen && !this.screen.idle) this.screen = { ...this.screen, idle: true };
    this.screenTaken = false;
  }

  /** A socket that came back replays the event log, and the log does
   *  not hold everything: an answer too large for one event was never
   *  recorded there. So the newest messages are read from their own
   *  record too, and any the page does not have yet are added. */
  private async readNewestMessages(): Promise<void> {
    if (!this.chat_id) return;
    const opening = this.opening;
    const result = await this.aiSession.listMessagePage(this.chat_id, 50);
    if (this.destroyed || opening !== this.opening || !result.data) return;
    for (const message of result.data.messages || []) {
      if (!this.messageLookup.has(message.message_id)) this.upsertMessage(message);
    }
  }

  async loadOlderMessages(): Promise<void> {
    if (!this.chat_id || !this.hasOlderMessages || this.loadingOlderMessages
        || this.olderMessagesBefore === null) return;
    this.loadingOlderMessages = true;
    try {
      const opening = this.opening;
      const result = await this.aiSession.listMessagePage(
        this.chat_id, 50, this.olderMessagesBefore,
      );
      if (this.destroyed || opening !== this.opening) return;
      if (result.error || !result.data) {
        this.state.addError('assistant', 'history_failed',
          result.error || 'Unable to load earlier messages.', true);
        return;
      }
      // An older page is adapted like every other message — markdown text,
      // file refs, stored tables and charts fetched — and the index is
      // rebuilt, since prepending moved every position. Raw, its text
      // rendered blank and its tables empty.
      const existing = new Set(this.messages.map((message) => message.message_id));
      const older = result.data.messages
        .filter((message) => !existing.has(message.message_id))
        .map((message) => this.normalizeMessage(message));
      this.messages = [...older, ...this.messages];
      this.reindexMessages();
      this.state.replaceMessages(this.messages as any);
      this.hasOlderMessages = result.data.has_more;
      this.olderMessagesBefore = result.data.next_before;
    } finally {
      this.loadingOlderMessages = false;
    }
  }

  private normalizeMessage(message: any): any {
    return {
      ...message,
      parts: (message.parts || []).map((part: any) => this.normalizePart(part)),
    };
  }

  private reindexMessages(): void {
    this.messageLookup.clear();
    this.messages.forEach((message, index) =>
      this.messageLookup.set(message.message_id, index));
  }

  /** Adapt protocol parts to what the renderer expects and index by id. */
  private upsertMessage(message: any, publish = true): void {
    const normalized = this.normalizeMessage(message);
    const id = normalized.message_id;
    if (id == null) return;

    // The page may already be showing these words under its own name
    // for the submission. The server is the author of record, so its
    // message takes that place rather than landing beside it — the
    // index is keyed on message_id and would otherwise hold both.
    const named = String(normalized.client_message_id || '');
    if (named) {
      const optimistic = this.messages.findIndex((held) =>
        held.message_id === `pending:${named}`);
      if (optimistic >= 0) {
        this.messages = this.messages.filter((_, at) => at !== optimistic);
        this.reindexMessages();
      }
    }

    if (!this.messageLookup.has(id)) {
      const messages = [...this.messages, normalized];
      this.messageLookup.set(id, messages.length - 1);
      if (publish) this.state.replaceMessages(messages as any);
      else this.messages = messages;
    } else {
      const messages = [...this.messages];
      messages[this.messageLookup.get(id)!] = normalized;
      if (publish) this.state.replaceMessages(messages as any);
      else this.messages = messages;
    }
  }

  private normalizePart(part: any): any {
    if (part?.type === 'markdown') {
      // Protocol: {type, content}. Renderer: part.text.
      return { ...part, text: part.content ?? part.text ?? '' };
    }
    if (part?.type === 'table' || part?.type === 'graph') {
      // Verified data lives in storage; fetch and attach it.
      const adapted = { ...part, data: part.data || null, loading: !part.data, hydrationError: '' };
      this.hydrateStoragePart(adapted);
      return adapted;
    }
    return part;
  }

  /** Fill a table/graph part's data from chat storage (in place — the
   *  template re-renders when the fetch lands). */
  private async hydrateStoragePart(part: any): Promise<void> {
    if (!part.storage_ref || part.data) return;

    // A path names a slice from INSIDE the stored result, so "data" —
    // the result itself — resolves to nothing and the part stays blank.
    // The prompt no longer suggests it, but messages written while it did
    // are already in the transcript and should still render.
    const path = part.path === 'data' ? '' : part.path;

    const value = await this.aiSession.getStorage(part.storage_ref, path);
    if (this.destroyed) return;
    if (value == null) {
      part.loading = false;
      part.hydrationError = 'This content is unavailable or has expired.';
      this.ngZone.run(() => this.state.replaceMessages([...this.messages] as any));
      return;
    }

    this.ngZone.run(() => {
      if (part.type === 'graph') {
        // Chart spec: {chartData, chartType, title}.
        part.data = value;
        part.text = part.text || value.title;
      } else {
        // Rows into the informative table's {rows, columns}. The part
        // may name the columns to show, in reading order — the model's
        // choice of presentation; the rows themselves are storage's.
        const rows = Array.isArray(value) ? value : value.rows || [value];
        const present = new Set<string>(
          rows.flatMap((row: any) => (row && typeof row === 'object' ? Object.keys(row) : [])),
        );
        const chosen = Array.isArray(part.columns)
          ? part.columns.filter((c: any) => typeof c === 'string' && present.has(c))
          : [];
        const columns = chosen.length
          ? chosen
          : (!Array.isArray(value) && value.columns) ||
            (rows.length ? Object.keys(rows[0]) : []);
        part.data = { rows, columns };
      }
      part.loading = false;
      this.state.replaceMessages([...this.messages] as any);
    });
  }

  // =========================
  // Composer
  // =========================
  async onPrompt(message: { query: string; uploads?: any[] }) {
    if (!this.isSocketReady) {
      this.pendingInitialMessage = message;
      this.state.addError('connection', 'connection_closed',
        'The connection was interrupted. Your message is waiting to send.', true);
      this.connection.reconnectNow();
      return;
    }

    const attachments: FilePart[] = (message.uploads || [])
      .filter((upload) => upload?.file_id)
      .map((upload) => ({
        type: 'file',
        resource_ref: upload.file_id,
        filename: upload.filename,
        file_size: upload.file_size,
        file_type: upload.file_type,
      }));
    const command: ChatInputCommand = {
      protocol_version: CHAT_PROTOCOL_VERSION,
      client_message_id: crypto.randomUUID(),
      text: message.query,
      attachments,
    };
    this.state.startSubmission(command.client_message_id, command.text,
                               attachments as any);
    if (!this.connection.send('AI:Chat:Input', command)) {
      // Take the words back: leaving them on screen beside an error
      // saying they were not sent is worse than never showing them.
      this.state.dropSubmission(command.client_message_id);
      this.state.fail('send_failed',
        'The message could not be sent. Reconnect and try again.', true);
    }
  }

  /** The kill switch: asked once, then everything in this chat ends
   *  where it stands — the run, its background jobs, its browser — and
   *  every card still waiting is expired. Offered whenever anything
   *  could be running, since work goes on after the tab is closed. */
  killAsking = false;
  killing = false;

  /** How long the runtime's `stopped` is waited for before the button
   *  is given back — a little past the backend's own grace. The word
   *  can be lost (the socket was down as it was said), and a button
   *  disabled for good is worse than a stop asked for twice. */
  private static readonly KILL_WAIT_MS = 12000;
  private killTimer: ReturnType<typeof setTimeout> | null = null;

  private clearKillTimer(): void {
    if (this.killTimer) clearTimeout(this.killTimer);
    this.killTimer = null;
  }

  askKill(): void {
    this.killAsking = true;
  }

  async confirmKill(): Promise<void> {
    if (!this.chat_id || this.killing) return;
    this.killAsking = false;
    this.killing = true;
    this.state.addProgress('Stopping everything…');
    const res = await this.aiSession.stop(this.chat_id, true);
    if (res.error) {
      this.killing = false;
      this.state.addError('assistant', 'stop_failed', res.error, true);
      return;
    }
    if (this.destroyed) return;
    if (!res.data?.delivered) {
      // No runtime is serving this chat: nothing runs, and the cards
      // are already expired on the record.
      this.killing = false;
      this.settleScreen();
      this.state.stopped('Stopped everything in this chat.');
      return;
    }
    this.clearKillTimer();
    this.killTimer = setTimeout(() => {
      this.killTimer = null;
      this.killing = false;
    }, ChatDetailComponent.KILL_WAIT_MS);
  }

  /** The stop button: recorded by the backend, carried to the runtime as
   *  the door's own frame, honored by the assistant between beats. The
   *  indicator clears when the door says idle. */
  async onStopGenerating(): Promise<void> {
    if (!this.chat_id || !this.isLoading) return;
    this.state.addProgress('Stopping…');
    const res = await this.aiSession.stop(this.chat_id);
    if (res.error) {
      this.state.addError('assistant', 'stop_failed', res.error, true);
    }
  }

  // =========================
  // Approvals & trust
  // =========================
  private addApproval(data: any): void {
    if (!data?.approval_id) return;
    if (
      this.resolvingApprovalIds.has(data.approval_id) ||
      this.resolvedApprovalIds.has(data.approval_id)
    ) return;
    if (this.pendingApprovals.some((a) => a.approval_id === data.approval_id))
      return;

    this.state.replaceApprovals([
      ...this.pendingApprovals, toApprovalCard(data, this.agentLabels),
    ]);
  }

  /** The runtime closed a question: answered (perhaps on another page),
   *  or expired — nobody answered in time, or the call that asked it had
   *  already ended, and an answer given to it went nowhere. */
  private closeQuestion(approvalId: string, status: string | undefined): void {
    const card = this.pendingApprovals.find((a) => a.approval_id === approvalId);
    if (!card) return;
    if (status === 'expired') card.status = 'expired';
    else if (card.status !== 'answered') card.status = 'answered';
    this.state.replaceApprovals([...this.pendingApprovals]);
  }

  /** The decision goes through the gateway: validated, recorded with the
   *  resolver's identity, then delivered to the runtime. */
  async onApprovalDecision(event: {
    approval_id: string;
    decision: 'approve' | 'deny' | 'answer';
    answer?: string;
    file?: File;
    /** A files question: the chosen refs. */
    files?: string[];
    /** A credential card: typed fields or a word. */
    credential?: { mode: 'entry' | 'consent' | 'choose' | 'once'; fields?: Record<string, string>; answer?: string };
    /** A code card: allow or deny. */
    code?: 'allow' | 'deny';
  }): Promise<void> {
    const approval = this.pendingApprovals.find(
      (a) => a.approval_id === event.approval_id,
    );
    if (
      !approval ||
      this.resolvingApprovalIds.has(event.approval_id) ||
      this.resolvedApprovalIds.has(event.approval_id)
    ) {
      return;
    }

    // Lock before awaiting the gateway. Previously the buttons stayed live
    // during the request, allowing two resolves, and the resolved card then
    // lingered for three seconds looking like a repeated request.
    this.resolvingApprovalIds.add(event.approval_id);
    approval.status = event.decision === 'answer' ? 'answering'
      : event.decision === 'approve' ? 'approving' : 'denying';
    this.state.replaceApprovals([...this.pendingApprovals]);

    try {
      let answer = event.answer || '';
      if (event.decision === 'answer' && event.file) {
        // A file question: the document becomes an attachment of this
        // chat first, and its ref is the answer the agent reads.
        const uploaded = await this.aiSession.uploadChatFile(this.chat_id || '', event.file);
        const ref = uploaded.data?.resource?.resource_ref || '';
        if (uploaded.error || !ref) {
          approval.status = 'pending';
          this.state.replaceApprovals([...this.pendingApprovals]);
          this.state.addError('approval', 'upload_failed',
            uploaded.error || `Could not attach ${event.file.name}`, true);
          return;
        }
        answer = ref;
      }
      let res: { data?: any; error?: string; code?: string };
      if (event.decision === 'answer' && event.credential) {
        // Typed values and the yes go to the vault's own doors; a
        // decline, an update request or a chosen row are words.
        const c = event.credential;
        if (c.mode === 'entry') res = await this.aiSession.saveCredential(event.approval_id, c.fields || {});
        else if (c.mode === 'consent' && c.answer === 'allow') res = await this.aiSession.allowCredential(event.approval_id);
        else if (c.mode === 'once') res = await this.aiSession.answerQuestion(event.approval_id, c.fields || {});
        else res = await this.aiSession.answerQuestion(event.approval_id, c.answer || '');
      } else if (event.decision === 'answer' && event.code) {
        res = await this.aiSession.answerQuestion(event.approval_id, event.code);
      } else {
        res = event.decision === 'answer'
          ? await this.aiSession.answerQuestion(event.approval_id, event.files ?? answer)
          : await this.aiSession.resolveApproval(event.approval_id, event.decision);
      }
      if (res.error && res.code === 'not_pending') {
        // Already decided — on another device — or expired. Asking
        // again can only be refused again, so the card leaves the strip
        // and the backend's sentence says what became of it.
        this.resolvedApprovalIds.add(event.approval_id);
        approval.status = 'expired';
        this.state.replaceApprovals([...this.pendingApprovals]);
        this.state.addError('approval', 'approval_closed', res.error, false);
        return;
      }
      if (res.error) {
        approval.status = 'pending';
        this.state.replaceApprovals([...this.pendingApprovals]);
        this.state.addError('approval', 'approval_failed', res.error, true);
        return;
      }
      this.resolvedApprovalIds.add(event.approval_id);
      approval.status = event.decision === 'answer' ? 'answered'
        : event.decision === 'approve' ? 'approved' : 'denied';
      this.state.replaceApprovals([...this.pendingApprovals]);
    } catch {
      // The file could not be read, or the request itself threw: the
      // card goes back to waiting rather than staying mid-answer for good.
      approval.status = 'pending';
      this.state.replaceApprovals([...this.pendingApprovals]);
      this.state.addError('approval', 'approval_failed',
        'Your answer could not be sent. Try again.', true);
    } finally {
      this.resolvingApprovalIds.delete(event.approval_id);
    }
  }

  /** The person's hand on the screen: to the runtime over the chat
   *  socket, as the door's own frame, never recorded. */
  onScreenInput(events: ScreenInputEvent[]): void {
    if (!this.screen || !events.length) return;
    this.connection.send('AI:Chat:Screen', { call_id: this.screen.call_id, events });
  }

  /** Whether this chat holds an agent that can open a browser on
   *  request — a function declaring `watch` in the contract. */
  get canWatch(): boolean {
    return Object.values(this.contract?.agents || {}).some((agent) => !!(agent as any)?.watch);
  }

  /** The header's button: a hidden panel comes back; otherwise the
   *  runtime is asked to open the browser and stream it. */
  openScreen(): void {
    // A screen still streaming is the browser: shown again if it was
    // hidden, left alone if it is on screen. An idle one has nothing
    // live behind it, so the browser is opened again.
    if (this.screen && !this.screen.idle) {
      this.screenHidden = false;
      this.hiddenCallId = '';
      return;
    }
    if (!this.connection.send('AI:Chat:Watch', {})) {
      this.state.addError('connection', 'connection_closed',
        'The connection was interrupted. Reconnect to open the browser.', true);
    }
  }

  /** The person closed the browser itself: the runtime is asked to
   *  quit it, remembering where it was; the panel goes with it. */
  onScreenQuit(): void {
    this.connection.send('AI:Chat:Watch', { action: 'quit' });
    this.settleScreen();
    this.screenHidden = true;
    this.hiddenCallId = this.screen?.call_id || '';
  }

  /** The person closed the panel. A call that only shows (a watch)
   *  hears it and ends; a run goes on and the next frame reopens it. */
  onScreenClosed(): void {
    if (this.screen && !this.screen.idle) {
      this.connection.send('AI:Chat:Screen', {
        call_id: this.screen.call_id,
        events: [{ type: 'control', action: 'close' }],
      });
    }
    this.screenHidden = true;
    this.hiddenCallId = this.screen?.call_id || '';
  }

  /** The contract's word on whether this person may speak here. Only a
   *  plain no closes the composer: a contract that does not say leaves
   *  it to the relay, which refuses a message that is not allowed. */
  get maySend(): boolean {
    return this.contract?.powers?.['send'] !== false;
  }

  get composerDisabled(): boolean {
    return this.isChatLoading || !this.isSocketReady || !this.chat?.config?.llm
      || !this.maySend;
  }

  get composerPlaceholder(): string {
    if (this.isChatLoading) return 'Loading conversation…';
    if (!this.maySend) return 'You may read this chat, not write in it';
    if (!this.chat?.config?.llm) return 'Choose a model to continue';
    if (this.isConnecting) return 'Connecting…';
    if (!this.isSocketReady) return 'Reconnecting…';
    if (this.screen && !this.screen.idle) return `Tell ${this.screen.agent_name || 'the agent'} something…`;
    if (this.isLoading) return 'Add to what it is doing…';
    return 'Message DecentAI';
  }

  get composerHint(): string {
    if (!this.maySend) return 'Sending messages is not among your permissions.';
    if (!this.chat?.config?.llm) return 'Choose a model, on the left.';
    // Nothing to say about a connection being made for the first time:
    // it takes a moment and then it is there.
    if (this.isConnecting) return '';
    if (!this.isSocketReady) return 'Restoring the chat connection…';
    if (this.screen && !this.screen.idle) return `${this.screen.agent_name || 'The agent'} hears this as it works.`;
    if (this.isLoading) return 'Sent now, it steers the turn in progress.';
    return '';
  }

  /** What the status pill reads. */
  get connectionLabel(): string {
    if (this.isSocketReady) return 'Connected';
    if (this.isConnecting) return 'Connecting';
    return this.isReconnecting ? 'Reconnecting' : 'Reconnect';
  }

  /** Trust lives in the chat's config; a change reconnects the chat. */
  async onTrustLevelChange(level: number): Promise<void> {
    const config = { ...(this.chat?.config || {}), trust_level: level };
    const res = await this.aiSession.updateChatConfig(this.chat_id!, config);
    if (res.error) {
      this.onError(res.error);
      return;
    }
    this.chat = res.data?.chat || this.chat;
    this.trustLevel = level;
    this.connection.connect(this.chat_id!, () => this.state.value.latestEventSeq);
  }

  /** The budget lives in the chat's config beside trust, and the
   *  runtime re-reads the contract each turn, so a chat part-way through
   *  a long piece of work takes the new number without reconnecting —
   *  which matters, because that is exactly when someone raises it. */
  async onTurnBudgetChange(turns: number): Promise<void> {
    const config = { ...(this.chat?.config || {}), max_turns: turns };
    const res = await this.aiSession.updateChatConfig(this.chat_id!, config);
    if (res.error) {
      this.onError(res.error);
      return;
    }
    this.chat = res.data?.chat || this.chat;
    this.turnBudget = turns;
  }

  turnBudgetLabel(turns: number): string {
    return (
      this.turnBudgetOptions.find((o) => o.turns === turns)?.label ??
      `${turns} steps`
    );
  }

  /** Same path as the turn budget: the config, re-read by the runtime
   *  each turn, so the next turn's frame lists the new number. */
  async onSkillsCapChange(rows: number): Promise<void> {
    const config = { ...(this.chat?.config || {}), max_skills: rows };
    const res = await this.aiSession.updateChatConfig(this.chat_id!, config);
    if (res.error) {
      this.onError(res.error);
      return;
    }
    this.chat = res.data?.chat || this.chat;
    this.skillsCap = rows;
  }

  skillsCapLabel(rows: number): string {
    return (
      this.skillsCapOptions.find((o) => o.rows === rows)?.label ??
      `${rows} skills`
    );
  }

  trustLabel(level: number): string {
    return (
      this.trustOptions.find((o) => o.level === level)?.label ??
      `Level ${level}`
    );
  }

  // =========================
  // Header actions
  // =========================
  /** The amber dot doubles as the reconnect control — a full refresh,
   *  so history, approvals, and the socket all come back together. */
  onStatusDotClicked(): void {
    if (this.isSocketReady) return;
    this.connection.reconnectNow();
  }

  startTitleEdit(): void {
    this.titleDraft = this.title;
    this.isEditingTitle = true;
  }

  cancelTitleEdit(): void {
    this.isEditingTitle = false;
    this.titleDraft = '';
  }

  async saveTitle(): Promise<void> {
    if (!this.isEditingTitle) return;
    const next = this.titleDraft.trim();
    this.isEditingTitle = false;
    this.titleDraft = '';
    if (!next || next === this.title) return;

    const res = await this.aiSession.renameChat(this.chat_id!, next);
    if (res.error) {
      this.onError(res.error);
      return;
    }
    this.title = res.data?.chat?.title || next;
    this.chat = res.data?.chat || this.chat;
  }

  onMessagesActionClicked(event: any) {
    if (!this.chat_id) return;

    if (event.type === 'refresh') {
      this.initChat();
      return;
    }
    if (event.type === 'agents') {
      this.openAgentsDialog();
      return;
    }
    if (event.type === 'skills') {
      this.openSkillsDialog();
      return;
    }
    if (event.type === 'activity') {
      this.openActivityDialog();
      return;
    }
  }

  /** The record of what actually happened here — the audit trail, every
   *  function since the first message with its inputs, outcome and
   *  result. It is its own dialog: the line docked at the foot of the
   *  thread shows THIS turn only, so the current work is never buried
   *  under the chat's history. Read live, never cached — the point of a
   *  trail is that it is the source and not a copy. */
  private openActivityDialog(): void {
    this.dialog.open(ChatActivityDialogComponent, {
      width: '760px',
      maxWidth: '95vw',
      maxHeight: '85vh',
      data: { chat_id: this.chat_id, title: this.title },
    });
  }

  /** The model picked under the composer: this chat thinks with it
   *  from its next turn, and the person's next chat starts with it. */
  async onModelChosen(llm: ChatModel): Promise<void> {
    const config = { ...(this.chat?.config || {}), llm };
    const res = await this.aiSession.updateChatConfig(this.chat_id!, config);
    if (res.error) {
      this.onError(res.error);
      return;
    }
    this.chat = res.data?.chat || this.chat;
    const preference = await this.profileService.saveDefaultLlm(
      llm.secret_ref, llm.model, llm.reasoning_effort || '',
    );
    if (preference.error) {
      this.onError(
        `The model was changed for this chat, but could not be saved as your default: ${preference.error}`,
      );
    }
    // The contract names the model too: read the chat again, so the
    // page's word on whether one resolves is the backend's.
    await this.initChat();
  }

  private async openAgentsDialog(): Promise<void> {
    const profile = await this.profileService.get();
    const defaults = profile?.preferences?.chat?.enabled_agents;
    const ref = this.dialog.open(ChatAgentsDialogComponent, {
      width: '480px',
      maxWidth: '95vw',
      data: { enabled_agents: this.chat?.config?.enabled_agents,
        default_agents: Array.isArray(defaults) ? defaults : null },
    });
    ref.afterClosed().subscribe(async (result) => {
      if (!result) return;
      const config = { ...(this.chat?.config || {}) };
      // No list at all is how "every agent" is said. The page sends the
      // whole config back, so the key is dropped rather than emptied.
      if (result.enabled_agents === null) delete config.enabled_agents;
      else config.enabled_agents = result.enabled_agents;
      const res = await this.aiSession.updateChatConfig(this.chat_id!, config);
      if (res.error) {
        this.onError(res.error);
        return;
      }
      this.chat = res.data?.chat || this.chat;
      // What the chat can reach changed, and the contract is where the
      // page reads it: the agents' names, and whether one can show a
      // browser.
      await this.initChat();
    });
  }

  /** Which skills this chat puts in front of the assistant. Not a
   *  security boundary — the catalog is bounded, so this is how a
   *  conversation spends its attention on what it is actually about. */
  private async openSkillsDialog(): Promise<void> {
    const profile = await this.profileService.get();
    const defaults = profile?.preferences?.chat?.enabled_skills;
    const ref = this.dialog.open(ChatSkillsDialogComponent, {
      width: '520px',
      maxWidth: '95vw',
      data: { enabled_skills: this.chat?.config?.enabled_skills,
        default_skills: Array.isArray(defaults) ? defaults : null,
        max_skills: this.skillsCap },
    });
    ref.afterClosed().subscribe(async (result) => {
      if (!result) return;
      const config = { ...(this.chat?.config || {}) };
      // No list at all is how "every skill" is said, as with agents: a
      // chat that keeps none takes up skills written later.
      if (result.enabled_skills === null) delete config.enabled_skills;
      else config.enabled_skills = result.enabled_skills;
      const res = await this.aiSession.updateChatConfig(this.chat_id!, config);
      if (res.error) {
        this.onError(res.error);
        return;
      }
      this.chat = res.data?.chat || this.chat;
    });
  }

  // =========================
  // Alerts
  // =========================
  private onError(msg: string = '') {
    this.state.addError('assistant', 'chat_error',
      msg || 'Something went wrong. Refresh the conversation and try again.');
  }

  dismissError(id: number): void { this.state.dismissError(id); }

  async retryInlineError(error: InlineChatError): Promise<void> {
    this.state.dismissError(error.id);
    if (error.scope === 'connection') this.connection.reconnectNow();
  }

  private alert(message: any) {
    this.dialog.open(AlertComponent, { width: 'auto', data: message });
  }
}
