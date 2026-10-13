import { Component, OnDestroy, OnInit } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';

import { FilterGroup, FilterItem } from 'src/app/components/ui/filter-column.component';
import {
  ActivitySchedule, ActivityService, SchedulableFunction,
} from 'src/app/services/activity.service';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { WorkSwitchService } from 'src/app/services/work-switch.service';
import { DataPageBase } from '../../data-page-base';
import { cadenceWords, momentWords, until } from './cadence-words';
import {
  ScheduleChat, ScheduleFormComponent, ScheduleFormData, ScheduleFormResult,
} from './schedule-form.component';
import {
  STATE_WORDS, ScheduleState, alsoOf, failuresOf, lastRun, runLabel, stateOf, took, whatOf,
} from './schedule-shared';

const STATES: ScheduleState[] = ['active', 'paused', 'done', 'failing'];

/**
 * Schedules: everything the person's chats keep on the clock, read from
 * every chat at once. A row reads as an appointment: when it comes
 * round, in words; what it does and in which chat; when it runs next
 * and how the last run went.
 *
 * One list, with a column at its left that narrows it by how a schedule
 * stands and by chat. A row's name opens the schedule at
 * /ai/schedules/<id>, beside the list on a wide screen, with every run
 * it has had. Making one and changing one are the same dialog
 * (schedule-form.component); saying it in plain words goes to a chat,
 * where the assistant sets the clock.
 */
@Component({
  selector: 'app-schedules',
  standalone: false,
  templateUrl: './schedules.component.html',
  styleUrl: './schedules.component.css',
})
export class SchedulesComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  schedules: ActivitySchedule[] = [];
  functions: SchedulableFunction[] = [];
  private chats: ScheduleChat[] = [];
  /** The person stopped everything: the rows stay on the clock and
   *  none runs until they resume. */
  stopped = false;
  resuming = false;

  // ── What the address says ───────────────────────────────────────────
  /** How a schedule stands, or '' for all of them. */
  state: ScheduleState | '' = '';
  chat = '';
  query = '';
  page = 1;
  readonly pageSize = 20;
  /** The schedule opened beside the list; '' for none. */
  openId = '';
  private address = new Subscription();

  /** The schedule an action is on its way for. */
  busyId = '';
  intent = '';
  sending = false;

  /** The clock "in 14 h" is read against, in seconds: it moves every
   *  minute, and the rows are read again while any is live. */
  now = Date.now() / 1000;
  private ticker: ReturnType<typeof setInterval> | null = null;

  constructor(
    private service: ActivityService,
    private session: AiSessionService,
    private work: WorkSwitchService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    this.address.add(this.route.queryParamMap.subscribe((params) => {
      const state = params.get('state') as ScheduleState;
      this.state = STATES.includes(state) ? state : '';
      this.chat = params.get('chat') ?? '';
      this.query = params.get('q') ?? '';
      this.page = Math.max(1, Number(params.get('page')) || 1);
    }));
    this.address.add(this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) this.readOpened();
    }));
    // Resumed from the bar at the top of the app: the page follows.
    this.address.add(this.work.stopped$.subscribe((stopped) => {
      if (!this.loading) this.stopped = stopped;
    }));

    await this.reload();
    this.loading = false;
    this.readOpened();
    this.ticker = setInterval(() => {
      this.now = Date.now() / 1000;
      if (this.schedules.some((schedule) => schedule.enabled)) void this.reload();
    }, 60000);
    // A function's own names, for the rows that run one.
    this.functions = await this.service.schedulableFunctions().catch(() => []);
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
    if (this.ticker) clearInterval(this.ticker);
  }

  private async reload(): Promise<void> {
    const read = await this.service.list();
    // A read that failed says so and leaves what was on screen: an
    // empty list would read as "nothing scheduled".
    if (read.error) return this.fail(`Could not read your schedules: ${read.error}`);
    this.schedules = read.schedules;
    this.stopped = read.stopped;
    this.now = Date.now() / 1000;
  }

  get canCreate(): boolean { return this.auth.can('ai:schedule:create'); }
  get canUpdate(): boolean { return this.auth.can('ai:schedule:update'); }
  get canDelete(): boolean { return this.auth.can('ai:schedule:delete'); }

  // ── The address ─────────────────────────────────────────────────────

  go(changes: Record<string, string>): void {
    const queryParams: Record<string, string | null> = { page: null };
    for (const [key, value] of Object.entries(changes)) queryParams[key] = value || null;
    void this.router.navigate([], {
      relativeTo: this.route.firstChild ?? this.route,
      queryParams,
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  filter(key: string, value: string): void {
    this.go({ [key]: key === 'state' && value === 'all' ? '' : value });
  }

  private readOpened(): void {
    this.openId = this.route.snapshot.firstChild?.paramMap.get('id') || '';
    if (this.openId && !this.loading && !this.opened) {
      this.fail('That schedule is no longer on the clock.');
      this.closeOpened();
    }
  }

  get opened(): ActivitySchedule | null {
    return this.schedules.find((schedule) => schedule.schedule_id === this.openId) || null;
  }

  closeOpened(): void {
    void this.router.navigate(['/ai/schedules'], { queryParamsHandling: 'preserve' });
  }

  // ── The list ────────────────────────────────────────────────────────

  get chosen(): Record<string, string> {
    return { state: this.state || 'all', chat: this.chat };
  }

  get filters(): FilterGroup[] {
    const counts: Record<string, number> = {};
    const byChat = new Map<string, FilterItem>();
    for (const schedule of this.schedules) {
      const state = stateOf(schedule);
      counts[state] = (counts[state] || 0) + 1;
      const item = byChat.get(schedule.chat_id)
        ?? { value: schedule.chat_id, name: schedule.chat_title || 'Untitled chat', count: 0 };
      item.count = (item.count ?? 0) + 1;
      byChat.set(schedule.chat_id, item);
    }
    const groups: FilterGroup[] = [{
      key: 'state',
      required: true,
      items: [
        { value: 'all', name: 'All', count: this.schedules.length },
        // Done and Failing only once there is one: most people have none.
        ...STATES.filter((state) => counts[state] || state === 'active' || state === 'paused')
          .map((state) => ({ value: state, name: STATE_WORDS[state], count: counts[state] || 0 })),
      ],
    }];
    if (byChat.size > 1 || this.chat) {
      groups.push({
        key: 'chat', label: 'By chat',
        items: [...byChat.values()].sort((a, b) => a.name.localeCompare(b.name)),
      });
    }
    return groups;
  }

  /** What the column and the search leave, the next to run first; what
   *  is not running comes after, the last to have run first. */
  get shownSchedules(): ActivitySchedule[] {
    const query = this.query.trim().toLowerCase();
    return this.schedules.filter((schedule) => {
      if (this.state && stateOf(schedule) !== this.state) return false;
      if (this.chat && schedule.chat_id !== this.chat) return false;
      return !query || [this.what(schedule), this.cadence(schedule), schedule.chat_title, schedule.timezone]
        .join(' ').toLowerCase().includes(query);
    }).sort((a, b) => {
      if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
      return a.enabled ? a.next_run_at - b.next_run_at : (b.last_run_at || 0) - (a.last_run_at || 0);
    });
  }

  get pagedSchedules(): ActivitySchedule[] {
    return this.shownSchedules.slice((this.page - 1) * this.pageSize, this.page * this.pageSize);
  }

  get filtered(): boolean {
    return !!(this.query || this.state || this.chat);
  }

  toPage(page: number): void {
    void this.router.navigate([], {
      relativeTo: this.route.firstChild ?? this.route,
      queryParams: { page: page > 1 ? page : null },
      queryParamsHandling: 'merge',
    });
  }

  // ── A schedule, in words ────────────────────────────────────────────

  cadence(schedule: ActivitySchedule): string { return cadenceWords(schedule); }
  what(schedule: ActivitySchedule): string { return whatOf(schedule, this.functions); }
  also(schedule: ActivitySchedule): string { return alsoOf(schedule, this.functions); }
  stateOf(schedule: ActivitySchedule): ScheduleState { return stateOf(schedule); }
  stateWord(schedule: ActivitySchedule): string { return STATE_WORDS[stateOf(schedule)]; }
  failures(schedule: ActivitySchedule): string { return failuresOf(schedule); }

  /** "Next Tue 14 Oct, 09:00 (in 14 h)"; '' when it is not running. */
  next(schedule: ActivitySchedule): string {
    if (!schedule.enabled || !schedule.next_run_at) return '';
    const moment = momentWords(schedule.next_run_at, schedule.timezone);
    return this.stopped ? `Next ${moment}, once you resume` : `Next ${moment} (${until(schedule.next_run_at, this.now)})`;
  }

  /** "last ran Mon 13 Oct, 09:00 · Ran quietly · 3.0 s". */
  last(schedule: ActivitySchedule): string {
    const run = lastRun(schedule);
    if (!run) return schedule.enabled ? 'has not run yet' : '';
    return [`last ran ${momentWords(run.at, schedule.timezone)}`, runLabel(run).toLowerCase(), took(run)]
      .filter(Boolean).join(' · ');
  }

  // ── The person's own hand on the clock ──────────────────────────────

  async setEnabled(schedule: ActivitySchedule, enabled: boolean): Promise<void> {
    this.busyId = schedule.schedule_id;
    try {
      const result = await this.service.setEnabled(schedule.chat_id, schedule.schedule_id, enabled);
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(enabled ? 'Resumed. The next run is counted from now.' : 'Paused.');
    } finally {
      this.busyId = '';
    }
  }

  async askDelete(schedule: ActivitySchedule): Promise<void> {
    const sure = await this.confirm.ask({
      title: 'Delete this schedule?',
      body: `“${this.what(schedule)}”, ${this.cadence(schedule).replace(/^./, (first) => first.toLowerCase())}. `
        + 'It stops running. What it already did stays in its chat.',
      confirm: 'Delete',
      danger: true,
    });
    if (!sure) return;
    this.busyId = schedule.schedule_id;
    try {
      const result = await this.service.remove(schedule.chat_id, schedule.schedule_id);
      if (result.error) return this.fail(result.error);
      if (this.openId === schedule.schedule_id) this.closeOpened();
      await this.reload();
      this.flash('Deleted.');
    } finally {
      this.busyId = '';
    }
  }

  /** Lifts the stop on everything, as the bar at the top does. */
  async resumeAll(): Promise<void> {
    this.resuming = true;
    try {
      const result = await this.work.resume();
      if (result.error) return this.fail(result.error);
      await this.reload();
    } finally {
      this.resuming = false;
    }
  }

  // ── Making and changing: the one dialog ─────────────────────────────

  private async openForm(schedule?: ActivitySchedule): Promise<void> {
    if (!schedule && !this.chats.length) {
      // Without the list the form still works: a new chat is offered.
      const chats = await this.session.listChats().catch(() => []);
      this.chats = chats.map((chat: any) => ({ chat_id: chat.chat_id, title: chat.title || 'Untitled chat' }));
    }
    const ref = this.dialog.open<ScheduleFormComponent, ScheduleFormData, ScheduleFormResult | null>(
      ScheduleFormComponent, {
        width: '640px', maxWidth: '100vw',
        data: { chats: this.chats, functions: this.functions, schedule, chatId: this.chat },
      });
    const done = await firstValueFrom(ref.afterClosed());
    if (!done) return;
    // A chat the form made is one to offer by its name the next time.
    if (!schedule) this.chats = [];
    await this.reload();
    this.flash(done.notice);
    if (done.id && !schedule) void this.router.navigate(['/ai/schedules', done.id], { queryParamsHandling: 'preserve' });
  }

  add(): Promise<void> { return this.openForm(); }
  edit(schedule: ActivitySchedule): Promise<void> { return this.openForm(schedule); }

  /** Plain words: the sentence goes to a chat, and the assistant, which
   *  already turns intent into schedules with evidence and audit, sets
   *  the clock. No second translator. */
  async describe(): Promise<void> {
    const text = this.intent.trim();
    if (!text || this.sending) return;
    this.sending = true;
    try {
      const created = await this.session.createChat(text.substring(0, 80), {});
      const chatId = created.data?.chat?.chat_id;
      if (!chatId) return this.fail(created.error || 'Could not open a chat for it.');
      void this.router.navigate(['ai/chats', chatId], {
        state: { initialMessage: { query: text, uploads: [] } },
      });
    } finally {
      this.sending = false;
    }
  }
}
