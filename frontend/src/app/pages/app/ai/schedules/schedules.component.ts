import { Component, OnDestroy, OnInit } from '@angular/core';
import { Router } from '@angular/router';

import {
  ActivitySchedule, ActivityService, ScheduleDraft, ScheduleRun, SchedulableFunction,
} from 'src/app/services/activity.service';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataPageBase } from '../../data-page-base';

type WhenKind = 'at' | 'every' | 'cron';
type EveryUnit = 'minutes' | 'hours' | 'days';

/** The new-schedule form, as the person fills it. */
interface ScheduleForm {
  chatId: string;          // '' = a new chat
  kind: 'note' | 'function';
  note: string;
  fn: string;
  inputsText: string;
  wakeField: string;
  when: WhenKind;
  at: string;
  everyCount: number;
  everyUnit: EveryUnit;
  cron: string;
}

/**
 * Schedules: everything the person's chats keep on the clock, read from
 * every chat at once — what each one does, when it fires next and the
 * few fires after that, and what every past fire did. Cards and
 * background work are not here: they live in their chats, where the
 * badges point.
 */
@Component({
  selector: 'app-schedules',
  standalone: false,
  templateUrl: './schedules.component.html',
  styleUrls: [
    '../../data-shared.css',
    '../../../admin/iam-shared.css',
    './schedules.component.css',
  ],
})
export class SchedulesComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  schedules: ActivitySchedule[] = [];
  /** The person stopped everything: the rows stay on the clock and
   *  none fires until they resume. */
  stopped = false;
  /** The schedule whose run history is unfolded. */
  openRuns: string | null = null;
  busyId = '';

  /** Self-service: delete asks; the form writes; plain words go to a chat. */
  deleteTarget: ActivitySchedule | null = null;
  formOpen = false;
  saving = false;
  form: ScheduleForm = this.blankForm();
  chats: { chat_id: string; title: string }[] = [];
  functions: SchedulableFunction[] = [];
  functionsLoaded = false;
  intent = '';
  sending = false;
  query = '';
  filter: 'all' | 'active' | 'paused' = 'all';
  page = 1;
  readonly pageSize = 10;

  /** A fire changes the row; the page reads again every minute while
   *  anything is live, so "next in 3 min" stays true. */
  private poller: any = null;
  private static readonly POLL_MS = 60000;

  constructor(
    private service: ActivityService,
    private session: AiSessionService,
    public auth: AuthService,
    private router: Router,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    await this.reload();
    this.loading = false;
    this.poller = setInterval(() => {
      if (this.liveSchedules.length) void this.reload();
    }, SchedulesComponent.POLL_MS);
  }

  ngOnDestroy(): void {
    if (this.poller) clearInterval(this.poller);
  }

  private async reload(): Promise<void> {
    const page = await this.service.list();
    // A read that failed says so and leaves what was on screen: an
    // empty list would read as "nothing scheduled".
    if (page.error) return this.fail(`Unable to load your schedules: ${page.error}`);
    this.schedules = page.schedules;
    this.stopped = page.stopped;
  }

  // ── The list ────────────────────────────────────────────────────────

  get liveSchedules(): ActivitySchedule[] {
    return this.schedules.filter((s) => s.enabled);
  }

  get pausedSchedules(): ActivitySchedule[] {
    return this.schedules.filter((s) => !s.enabled);
  }

  get shownSchedules(): ActivitySchedule[] {
    const query = this.query.trim().toLowerCase();
    return this.schedules.filter((schedule) => {
      if (this.filter === 'active' && !schedule.enabled) return false;
      if (this.filter === 'paused' && schedule.enabled) return false;
      return !query || `${this.what(schedule)} ${this.cadence(schedule)} ${schedule.chat_title || ''}`
        .toLowerCase().includes(query);
    });
  }

  get pagedSchedules(): ActivitySchedule[] {
    const start = (this.page - 1) * this.pageSize;
    return this.shownSchedules.slice(start, start + this.pageSize);
  }

  get pageCount(): number {
    return Math.max(1, Math.ceil(this.shownSchedules.length / this.pageSize));
  }

  setFilter(filter: 'all' | 'active' | 'paused'): void {
    this.filter = filter;
    this.page = 1;
  }

  search(value: string): void {
    this.query = value;
    this.page = 1;
  }

  // ── Words for a schedule ────────────────────────────────────────────

  /** What the schedule does, in the person's words where there are
   *  any, otherwise the function it runs. */
  what(schedule: ActivitySchedule): string {
    if (schedule.note) return schedule.note;
    if (schedule.function) return `Run ${schedule.function}`;
    return 'Wake the assistant';
  }

  /** How often, in words. Cron stays cron — with the zone it is read
   *  in — because a translation that gets one case wrong is worse than
   *  the expression; the upcoming times beside it say what it means. */
  cadence(schedule: ActivitySchedule): string {
    if (schedule.cron) {
      return `cron ${schedule.cron}${schedule.timezone ? ` · ${schedule.timezone}` : ''}`;
    }
    if (schedule.every_seconds) return `every ${this.duration(schedule.every_seconds)}`;
    return 'once';
  }

  statusLabel(schedule: ActivitySchedule): string {
    if (schedule.enabled) return schedule.mode === 'invoke' ? 'Function' : 'Assistant';
    if (schedule.last_run_at && !schedule.cron && !schedule.every_seconds) return 'Completed';
    return 'Paused';
  }

  duration(seconds: number): string {
    if (seconds % 86400 === 0) return `${seconds / 86400} day${seconds === 86400 ? '' : 's'}`;
    if (seconds % 3600 === 0) return `${seconds / 3600} hour${seconds === 3600 ? '' : 's'}`;
    if (seconds % 60 === 0) return `${seconds / 60} minute${seconds === 60 ? '' : 's'}`;
    return `${seconds} seconds`;
  }

  took(run: ScheduleRun): string {
    const ms = Number(run.took_ms || 0);
    if (!ms) return '';
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
    return `${Math.round(ms / 60000)} min`;
  }

  when(timestamp: number | null | undefined): string {
    if (!timestamp) return '';
    const date = new Date(timestamp * 1000);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
  }

  /** Relative to now, for the next run: the thing a person actually
   *  asks of a schedules page. */
  until(timestamp: number): string {
    const seconds = Math.round(timestamp - Date.now() / 1000);
    if (seconds <= 0) return 'due now';
    if (seconds < 60) return 'in under a minute';
    if (seconds < 3600) return `in ${Math.round(seconds / 60)} min`;
    if (seconds < 86400) return `in ${Math.round(seconds / 3600)} h`;
    return `in ${Math.round(seconds / 86400)} d`;
  }

  /** The fires after the next one, when the backend could say. */
  upcoming(schedule: ActivitySchedule): number[] {
    return (schedule.upcoming || []).slice(1, 4);
  }

  toggleRuns(schedule: ActivitySchedule): void {
    this.openRuns = this.openRuns === schedule.schedule_id ? null : schedule.schedule_id;
  }

  /** Newest first: the last fire is the one being asked about. */
  runsOf(schedule: ActivitySchedule): ScheduleRun[] {
    return [...(schedule.runs || [])].reverse();
  }

  runLabel(run: ScheduleRun): string {
    if (run.status === 'error') return 'Failed';
    if (run.status === 'woke') return 'Woke the assistant';
    if (run.status === 'success') return run.woke ? 'Ran, woke the assistant' : 'Ran quietly';
    return run.status;
  }

  lastRun(schedule: ActivitySchedule): ScheduleRun | null {
    const runs = schedule.runs || [];
    return runs.length ? runs[runs.length - 1] : null;
  }

  failures(schedule: ActivitySchedule): number {
    return (schedule.runs || []).filter((run) => run.status === 'error').length;
  }

  // ── Self-service: the person's own hand on the clock ────────────────

  get canCreate(): boolean { return this.auth.can('ai:schedule:create'); }
  get canPause(): boolean { return this.auth.can('ai:schedule:update'); }
  get canDelete(): boolean { return this.auth.can('ai:schedule:delete'); }

  async setEnabled(schedule: ActivitySchedule, enabled: boolean): Promise<void> {
    this.busyId = schedule.schedule_id;
    try {
      const result = await this.service.setEnabled(schedule.chat_id, schedule.schedule_id, enabled);
      if (result.error) return this.fail(result.error);
      this.flash(enabled ? 'Resumed — the next run is counted from now.' : 'Paused.');
      await this.reload();
    } finally {
      this.busyId = '';
    }
  }

  requestDelete(schedule: ActivitySchedule): void {
    this.deleteTarget = schedule;
    this.error = '';
  }

  closeDelete(): void {
    if (this.busyId) return;
    this.deleteTarget = null;
  }

  async remove(schedule: ActivitySchedule): Promise<void> {
    this.busyId = schedule.schedule_id;
    try {
      const result = await this.service.remove(schedule.chat_id, schedule.schedule_id);
      if (result.error) return this.fail(result.error);
      this.deleteTarget = null;
      this.flash('Taken off the clock.');
      await this.reload();
    } finally {
      this.busyId = '';
    }
  }

  private blankForm(): ScheduleForm {
    return {
      chatId: '', kind: 'note', note: '', fn: '', inputsText: '{}',
      wakeField: '', when: 'cron', at: '', everyCount: 1, everyUnit: 'hours',
      cron: '0 9 * * 1-5',
    };
  }

  async openForm(): Promise<void> {
    this.form = this.blankForm();
    this.formOpen = true;
    this.error = '';
    if (!this.chats.length) {
      // Without the list the form still works: a new chat is offered.
      const chats = await this.session.listChats().catch(() => []);
      this.chats = chats.map((chat: any) => ({
        chat_id: chat.chat_id, title: chat.title || 'Untitled chat',
      }));
    }
    if (!this.functionsLoaded) {
      this.functions = await this.service.schedulableFunctions().catch(() => []);
      this.functionsLoaded = true;
    }
  }

  closeForm(): void {
    if (this.saving) return;
    this.formOpen = false;
  }

  get chosenFunction(): SchedulableFunction | null {
    return this.functions.find((fn) => fn.value === this.form.fn) || null;
  }

  onFunctionChosen(): void {
    const fn = this.chosenFunction;
    if (!fn) return;
    // Seed the inputs with the declared fields so the shape is in view.
    const seed: Record<string, unknown> = {};
    for (const key of Object.keys(fn.inputs)) seed[key] = '';
    this.form.inputsText = JSON.stringify(seed, null, 2);
    this.form.wakeField = fn.outputs[0] || '';
  }

  private everySeconds(): number {
    const unit = { minutes: 60, hours: 3600, days: 86400 }[this.form.everyUnit];
    return Math.max(1, Math.round(this.form.everyCount)) * unit;
  }

  get canSubmit(): boolean {
    const form = this.form;
    if (form.kind === 'note' && !form.note.trim()) return false;
    if (form.kind === 'function' && !form.fn) return false;
    if (form.when === 'at' && !form.at) return false;
    if (form.when === 'cron' && form.cron.trim().split(/\s+/).length !== 5) return false;
    return !this.saving;
  }

  async submitForm(): Promise<void> {
    if (!this.canSubmit) return;
    // Everything the page can check is checked before a chat is made
    // for the schedule: a refused form must not leave a chat behind.
    let inputs: Record<string, unknown> = {};
    if (this.form.kind === 'function') {
      try {
        inputs = JSON.parse(this.form.inputsText || '{}');
      } catch {
        return this.fail('Inputs must be valid JSON.');
      }
    }

    this.saving = true;
    try {
      let chatId = this.form.chatId;
      if (!chatId) {
        const created = await this.session.createChat('Schedules', {});
        chatId = created.data?.chat?.chat_id || '';
        if (!chatId) return this.fail(created.error || 'Could not create a chat for it.');
        this.chats = [{ chat_id: chatId, title: 'Schedules' }, ...this.chats];
        // The chat exists now: a second try after a refusal goes into
        // it, not into another new one.
        this.form.chatId = chatId;
      }
      const draft: ScheduleDraft = { chat_id: chatId };
      if (this.form.kind === 'note') {
        draft.note = this.form.note.trim();
      } else {
        draft.function = this.form.fn;
        draft.inputs = inputs;
        if (this.form.wakeField.trim()) draft.wake_field = this.form.wakeField.trim();
      }
      if (this.form.when === 'at') draft.at = this.form.at;
      else if (this.form.when === 'every') draft.every_seconds = this.everySeconds();
      else draft.cron = this.form.cron.trim();

      const result = await this.service.create(draft);
      if (result.error) return this.fail(result.error);
      this.formOpen = false;
      this.flash('On the clock.');
      await this.reload();
    } finally {
      this.saving = false;
    }
  }

  /** Plain words: the sentence goes to a chat, and the assistant —
   *  which already turns intent into schedules, with evidence and
   *  audit — sets the clock. No second translator. */
  async describe(): Promise<void> {
    const text = this.intent.trim();
    if (!text || this.sending) return;
    this.sending = true;
    try {
      const created = await this.session.createChat(text.substring(0, 80), {});
      const chatId = created.data?.chat?.chat_id;
      if (!chatId) return this.fail(created.error || 'Could not open a chat for it.');
      this.router.navigate(['ai/chats', chatId], {
        state: { initialMessage: { query: text, uploads: [] } },
      });
    } finally {
      this.sending = false;
    }
  }
}
