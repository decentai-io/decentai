import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';

import { byDay, clockTime, fullDate, localDay, relativeDate } from 'src/app/components/ui/dates';
import { FilterGroup, FilterItem } from 'src/app/components/ui/filter-column.component';
import { Choice } from 'src/app/components/ui/radio-keys';
import { RecordTone } from 'src/app/components/ui/record-row.component';
import { AuthService } from 'src/app/services/auth.service';
import {
  AgentFiles, AgentUsage, MonitorEvent, MonitorOutcome, MonitorQuery, MonitorTally,
  MonitoringService, ServiceUsage, UsageNow,
} from 'src/app/services/monitoring.service';
import {
  MONITOR_KINDS, MonitorGroup, bytesLabel, groupMonitor, monitorChip, monitorFields, monitorKind,
  monitorLabel, monitorSummary, monitorTone,
} from './monitor-words';

type MonitoringView = 'overview' | 'tokens' | 'agents' | 'events' | 'connections';

/** What the events and the connections are narrowed to, each under the
 *  name the address gives it; '' is not narrowed. */
export interface MonitorFilters {
  kind: string;
  agent: string;
  outcome: MonitorOutcome | '';
  host: string;
  /** The words looked for. */
  q: string;
  /** The first and the last day, as a date box gives them. */
  from: string;
  to: string;
}

const NO_FILTERS: MonitorFilters = { kind: '', agent: '', outcome: '', host: '', q: '', from: '', to: '' };
const OUTCOMES: MonitorOutcome[] = ['ok', 'trouble', 'refused', 'unreached', 'failed'];

/**
 * Monitoring: what agents did, use now, and what was seen of them
 * (docs/system/monitoring.md). Read-only: nothing here changes an
 * agent or what it is given.
 *
 * Four views, each with its own address. "Overview" (/monitoring) is
 * what the agents did in a window, added up from the audit trail
 * (activity-overview.component); for whoever may not see it, /monitoring
 * is "Agents". "Tokens" (/monitoring/tokens) is what the chats and agents
 * had models use, and its estimated cost (token-usage.component), for
 * whoever holds agents:monitor:tokens. "Agents" (/monitoring/agents) is the running agents with
 * what each holds, against what they are given together, and what each
 * keeps on disk. "Events" (/monitoring/events) is everything written
 * down; "Connections" (/monitoring/connections) is the connections
 * alone, made and refused. Both are read a page at a time, narrowed by
 * a column of choices — how it went, the kind, the agent, the host —
 * each with what choosing it would show, and by words and days; the
 * address holds the filters. Alike rows next to each other are one.
 */
@Component({
  selector: 'app-monitoring',
  standalone: false,
  templateUrl: './monitoring.component.html',
  styleUrl: './monitoring.component.css',
})
export class MonitoringComponent implements OnInit, OnDestroy {
  view: MonitoringView = 'agents';

  /** The views this person may open, in the order they are offered:
   *  worked out once, when the page opens. */
  views: Choice<MonitoringView>[] = [];

  private offered(): Choice<MonitoringView>[] {
    return [
      ...(this.canSeeSummary ? [{ value: 'overview' as const, name: 'Overview' }] : []),
      ...(this.canSeeTokens ? [{ value: 'tokens' as const, name: 'Tokens' }] : []),
      { value: 'agents', name: 'Agents' },
      ...(this.canSeeEvents
        ? [{ value: 'events' as const, name: 'Events' }, { value: 'connections' as const, name: 'Connections' }]
        : []),
    ];
  }
  error = '';
  private address = new Subscription();

  /** How often what the agents use is asked for again, while it is
   *  being looked at. */
  static readonly EVERY_MS = 5000;

  // ── What they use now ───────────────────────────────────────────
  usage: UsageNow | null = null;
  loadingUsage = true;
  /** How old the numbers are, in words: "Updated 3 s ago". */
  updated = '';
  private updatedAt = 0;
  private timer: any = null;
  private seconds = 0;
  /** The agent whose files are shown, and what it keeps. */
  filesOf = '';
  files: AgentFiles | null = null;
  loadingFiles = false;

  // ── What happened ───────────────────────────────────────────────
  /** How many events a page holds. */
  static readonly PAGE = 100;
  /** The filters, as the address says them. */
  filters: MonitorFilters = { ...NO_FILTERS };
  /** What the toolbar's boxes hold until they are applied: the words
   *  looked for, and the days, as the date boxes give them. */
  text = '';
  since = '';
  until = '';
  events: MonitorEvent[] = [];
  /** What is listed, alike rows together, under the day it happened on. */
  days: { day: string; items: MonitorGroup[] }[] = [];
  /** What the filters leave, added up with the first page. */
  tally: MonitorTally | null = null;
  /** The column of choices, with what each would show. */
  columns: FilterGroup[] = [];
  /** Where each page read so far began, and how many came before it. */
  private pages: { before: number | null; offset: number }[] = [{ before: null, offset: 0 }];
  page = 0;
  nextBefore: number | null = null;
  loading = false;
  turning = false;
  /** What is open: an event by `key`, a group by `groupKey`. */
  expanded = new Set<string>();

  constructor(
    private monitoring: MonitoringService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
  ) {}

  get canSeeEvents(): boolean { return this.auth.can('agents:monitor:events'); }
  get canSeeFiles(): boolean { return this.auth.can('agents:monitor:files'); }
  get canSeeSummary(): boolean { return this.auth.can('agents:monitor:summary'); }
  get canSeeTokens(): boolean { return this.auth.can('agents:monitor:tokens'); }

  async ngOnInit(): Promise<void> {
    this.views = this.offered();
    // The view is a child route with no component, so this page is not
    // told when it changes: it reads the address after each navigation.
    this.address.add(this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => void this.readView()));
    await this.readView();
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
    this.stopWatching();
  }

  /** Each view has its own address; going there shows it. */
  go(view: MonitoringView): void {
    void this.router.navigate(view === this.landing ? ['/monitoring'] : ['/monitoring', view]);
  }

  /** What /monitoring itself shows: the Overview, for whoever may see it. */
  private get landing(): MonitoringView {
    return this.canSeeSummary ? 'overview' : 'agents';
  }

  private async readView(): Promise<void> {
    const named = this.route.snapshot.firstChild?.url[0]?.path;
    const view: MonitoringView =
      named === 'events' || named === 'connections' || named === 'agents' || named === 'tokens'
        ? named : this.landing;
    const listed = view === 'events' || view === 'connections';
    if ((listed && !this.canSeeEvents) || (view === 'tokens' && !this.canSeeTokens)) {
      void this.router.navigate(['/monitoring'], { replaceUrl: true });
      return;
    }
    // The filters are in the address too, so a narrowed list can be
    // sent to somebody or read again.
    let refiltered = false;
    if (listed) {
      const filters = this.filtersOf(view);
      refiltered = JSON.stringify(filters) !== JSON.stringify(this.filters);
      this.filters = filters;
      this.text = filters.q;
      this.since = filters.from;
      this.until = filters.to;
    }
    if (view !== this.view || refiltered || !this.timer && view === 'agents') await this.select(view);
  }

  /** The filters the address names, of those the view has. */
  private filtersOf(view: MonitoringView): MonitorFilters {
    const params = this.route.snapshot.queryParamMap;
    const word = (key: string) => params?.get(key) ?? '';
    const day = (key: string) => (/^\d{4}-\d{2}-\d{2}$/.test(word(key)) ? word(key) : '');
    const kind = word('kind');
    const outcome = word('outcome') as MonitorOutcome;
    return {
      kind: view === 'events' && MONITOR_KINDS.some((known) => known.value === kind) ? kind : '',
      agent: word('agent'),
      outcome: OUTCOMES.includes(outcome) ? outcome : '',
      host: view === 'connections' ? word('host') : '',
      q: word('q'),
      from: day('from'),
      to: day('to'),
    };
  }

  async select(view: MonitoringView): Promise<void> {
    this.view = view;
    this.error = '';
    this.stopWatching();
    if (view === 'overview' || view === 'tokens') return;
    if (view === 'agents') {
      // One clock, ticking each second: it says how old the numbers
      // are, and asks for new ones when they are old enough.
      this.timer = setInterval(() => this.tick(), 1000);
      await this.readUsage();
    } else {
      await this.reload();
    }
  }

  private stopWatching(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.seconds = 0;
  }

  private tick(): void {
    this.seconds += 1;
    if (this.seconds % (MonitoringComponent.EVERY_MS / 1000) === 0) void this.readUsage();
    this.sayUpdated();
  }

  private sayUpdated(): void {
    if (!this.updatedAt) return;
    const ago = Math.round((Date.now() - this.updatedAt) / 1000);
    this.updated = ago < 2 ? 'Updated just now' : `Updated ${ago} s ago`;
  }

  // ── Agents ──────────────────────────────────────────────────────

  async readUsage(): Promise<void> {
    const found = await this.monitoring.usage();
    this.loadingUsage = false;
    if (found.error) { this.error = found.error; return; }
    this.error = '';
    this.usage = found;
    this.updatedAt = Date.now();
    this.sayUpdated();
  }

  /** How full the agents' container is, 0 to 100, or null where no
   *  limit is set. */
  get fullness(): number | null {
    const given = this.usage?.limits?.memory;
    if (!given || this.usage?.memory == null) return null;
    return Math.min(100, Math.round((this.usage.memory / given) * 100));
  }

  /** One agent's share of what they are given, for its bar. */
  share(agent: AgentUsage): number | null {
    const given = this.usage?.limits?.memory;
    return given ? Math.min(100, Math.round((agent.memory / given) * 100)) : null;
  }

  /** An agent's row is the same row from one asking to the next: the
   *  numbers change in place, and its open file list stays open. */
  byAgent(_: number, agent: AgentUsage): string { return agent.agent; }
  byService(_: number, service: ServiceUsage): string { return service.id; }

  /** How full one part of the platform is of what it was given, or
   *  null where nothing was set or it could not read. */
  fullnessOf(service: ServiceUsage): number | null {
    if (!service.memory_limit || service.memory == null) return null;
    return Math.min(100, Math.round((service.memory / service.memory_limit) * 100));
  }

  /** What is not held here, in words: empty when all three are. */
  get notHeld(): string[] {
    const held = this.usage?.confined || {};
    const missing: string[] = [];
    if (held.user === false) missing.push('Agents do not run as users of their own.');
    if (held.files === false) missing.push("Agents' files are not fenced.");
    if (held.network === false) missing.push("Agents' connections are not held to the hosts they declared.");
    return missing;
  }

  async toggleFiles(agent: AgentUsage): Promise<void> {
    if (this.filesOf === agent.agent) { this.filesOf = ''; this.files = null; return; }
    this.filesOf = agent.agent;
    this.files = null;
    this.loadingFiles = true;
    try {
      const found = await this.monitoring.files(agent.agent);
      if (this.filesOf !== agent.agent) return;
      if (found.error) this.error = found.error;
      else this.files = found;
    } finally {
      this.loadingFiles = false;
    }
  }

  // ── Events and connections ──────────────────────────────────────

  private query(before: number | null, tally: boolean): MonitorQuery {
    const filters = this.filters;
    // The server reads `until` as the first moment not asked for.
    const since = localDay(filters.from, false);
    const until = localDay(filters.to, true);
    return {
      kinds: this.view === 'connections' ? ['connection'] : [],
      kind: filters.kind, agent: filters.agent, outcome: filters.outcome,
      host: filters.host, text: filters.q,
      before, limit: MonitoringComponent.PAGE, tally,
      since: since ? since.getTime() / 1000 : null,
      until: until ? (until.getTime() + 1) / 1000 : null,
    };
  }

  /** The first page, and what the filters leave added up. */
  async reload(): Promise<void> {
    this.loading = true;
    this.error = '';
    try {
      const page = await this.monitoring.events(this.query(null, true));
      if (page.error) { this.error = page.error; return; }
      this.tally = page.tally ?? null;
      this.pages = [{ before: null, offset: 0 }];
      this.take(0, page.events, page.next_before);
    } finally {
      this.loading = false;
    }
  }

  /** The page after this one, read from where this one ended. */
  async older(top?: HTMLElement): Promise<void> {
    if (this.nextBefore == null) return;
    this.pages[this.page + 1] = {
      before: this.nextBefore, offset: this.pages[this.page].offset + this.events.length,
    };
    await this.turnTo(this.page + 1, top);
  }

  async newer(top?: HTMLElement): Promise<void> {
    if (this.page > 0) await this.turnTo(this.page - 1, top);
  }

  private async turnTo(page: number, top?: HTMLElement): Promise<void> {
    if (this.turning) return;
    this.turning = true;
    try {
      const read = await this.monitoring.events(this.query(this.pages[page].before, false));
      if (read.error) { this.error = read.error; return; }
      this.take(page, read.events, read.next_before);
      top?.scrollIntoView?.({ block: 'nearest' });
    } finally {
      this.turning = false;
    }
  }

  private take(page: number, events: MonitorEvent[], nextBefore: number | null): void {
    this.page = page;
    this.events = events;
    this.nextBefore = nextBefore;
    this.expanded.clear();
    this.days = byDay(groupMonitor(events), (group) => group.first.at * 1000);
    this.columns = this.columnsOf();
  }

  /** Which events of how many this page is: "101–200 of 2,345". Where
   *  only the latest were added up, the count is at least that. */
  get range(): string {
    if (!this.events.length) return '';
    const first = this.pages[this.page].offset + 1;
    const last = first + this.events.length - 1;
    const shown = `${first.toLocaleString()}–${last.toLocaleString()}`;
    if (!this.tally) return shown;
    const counted = Math.max(this.tally.matching, last);
    return `${shown} of ${counted.toLocaleString()}${this.tally.complete ? '' : '+'}`;
  }

  // ── The filters ─────────────────────────────────────────────────

  /** Narrow the list: the address changes, and the page follows it. */
  apply(changes: Partial<MonitorFilters>): void {
    const filters = { ...this.filters, ...changes };
    const queryParams: Record<string, string> = {};
    for (const [key, value] of Object.entries(filters)) if (value) queryParams[key] = value;
    void this.router.navigate(['/monitoring', this.view], { queryParams, replaceUrl: true });
  }

  /** What the toolbar's boxes hold, applied. */
  applyTyped(): void {
    this.apply({ q: this.text.trim(), from: this.since, to: this.until });
  }

  clearFilters(): void { this.apply({ ...NO_FILTERS }); }

  get filtered(): boolean {
    return Object.values(this.filters).some(Boolean);
  }

  /** The column's choices, each with what choosing it would show. */
  get chosen(): Record<string, string> {
    const { outcome, kind, agent, host } = this.filters;
    return { outcome, kind, agent, host };
  }

  choose(choice: { key: string; value: string }): void {
    this.apply({ [choice.key]: choice.value });
  }

  /** How it went, the kinds, the agents and, of connections, the hosts:
   *  each with its count from the tally. A choice with none is left out
   *  unless it is the one chosen. Worked out with each page read, so the
   *  column is the same column between two. */
  private columnsOf(): FilterGroup[] {
    const tally = this.tally;
    const outcomes = tally?.outcomes || {};
    const count = (value?: number) => (tally ? value || 0 : undefined);
    const connections = this.view === 'connections';
    const kept = (item: FilterItem, key: keyof MonitorFilters) =>
      !tally || !!item.count || this.filters[key] === item.value;

    const wrong: FilterItem[] = [
      { value: 'refused', name: 'Refused', count: count(outcomes.refused) },
      { value: 'unreached', name: 'Not reached', count: count(outcomes.unreached) },
      ...(connections ? [] : [{ value: 'failed', name: 'Failed', count: count(outcomes.failed) }]),
    ].filter((item) => kept(item, 'outcome'));
    const trouble = (outcomes.refused || 0) + (outcomes.unreached || 0) + (outcomes.failed || 0);
    const groups: FilterGroup[] = [{
      key: 'outcome',
      required: true,
      items: [
        { value: '', name: connections ? 'Every connection' : 'Every event', count: count((outcomes.ok || 0) + trouble) },
        { value: 'ok', name: connections ? 'Made' : 'Went fine', count: count(outcomes.ok) },
        { value: 'trouble', name: connections ? 'Refused or not reached' : 'Went wrong', count: count(trouble), children: wrong },
      ],
    }];

    if (!connections) {
      groups.push({
        key: 'kind', label: 'By kind',
        items: MONITOR_KINDS.filter((kind) => kind.value)
          .map((kind) => ({ value: kind.value, name: kind.name, count: count(tally?.kinds[kind.value]) }))
          .filter((item) => kept(item, 'kind')),
      });
    }

    const agents: FilterItem[] = (tally?.agents || []).map((agent) => ({
      value: agent.agent, name: agent.name, count: agent.count, agent: { name: agent.name },
    }));
    const chosenAgent = this.filters.agent;
    if (chosenAgent && !agents.some((item) => item.value === chosenAgent)) {
      const name = this.events.find((event) => event.agent === chosenAgent)?.name || chosenAgent;
      agents.push({ value: chosenAgent, name, count: count(0), agent: { name } });
    }
    if (agents.length) groups.push({ key: 'agent', label: 'By agent', items: agents });

    if (connections) {
      const hosts: FilterItem[] = (tally?.hosts || []).map((host) => ({
        value: host.host, name: host.host, count: host.count,
      }));
      if (this.filters.host && !hosts.some((item) => item.value === this.filters.host)) {
        hosts.push({ value: this.filters.host, name: this.filters.host, count: count(0) });
      }
      if (hosts.length) groups.push({ key: 'host', label: 'By destination', items: hosts });
    }
    return groups;
  }

  /** Of what is listed, how many went wrong, when the outcome is not
   *  already chosen: "41 went wrong (1.7%)". */
  get troubleLine(): string {
    const tally = this.tally;
    if (!tally || this.filters.outcome || !tally.matching) return '';
    const outcomes = tally.outcomes;
    const trouble = (outcomes.refused || 0) + (outcomes.unreached || 0) + (outcomes.failed || 0);
    if (!trouble) return this.view === 'connections' ? 'none refused' : 'none went wrong';
    const share = (trouble / tally.matching) * 100;
    const percent = share < 1 ? share.toFixed(1) : Math.round(share).toString();
    return `${trouble.toLocaleString()} ${this.view === 'connections' ? 'refused or not reached' : 'went wrong'} (${percent}%)`;
  }

  // ── Rows ────────────────────────────────────────────────────────

  /** An event's own key: its time, which the runtime keeps apart for
   *  each writer, and its kind. */
  key(event: MonitorEvent): string { return `${event.at}|${event.kind}`; }

  /** A group of several opens to its events; a group of one is that
   *  event, and opens to its details. */
  groupKey(group: MonitorGroup): string {
    return group.events.length > 1 ? `group:${this.key(group.first)}` : this.key(group.first);
  }

  toggle(key: string): void {
    if (this.expanded.has(key)) this.expanded.delete(key);
    else this.expanded.add(key);
  }

  isOpen(key: string): boolean { return this.expanded.has(key); }

  // ── Words ───────────────────────────────────────────────────────

  label(event: MonitorEvent): string { return monitorLabel(event); }
  summary(event: MonitorEvent): string { return monitorSummary(event); }
  kindOf(event: MonitorEvent): string { return monitorKind(event); }
  outcome(event: MonitorEvent): string { return monitorChip(event); }
  tone(event: MonitorEvent): RecordTone { return monitorTone(event); }
  bytes(value: any): string { return bytesLabel(value); }
  time(event: MonitorEvent): string { return clockTime(event.at * 1000); }

  /** When a file was last written: how long ago, and in full. */
  when(at?: number | null): string { return at ? relativeDate(at * 1000) : ''; }
  fullWhen(at?: number | null): string { return at ? fullDate(at * 1000) : ''; }

  processors(value: any): string {
    return `${Math.round((Number(value) || 0) * 100)}%`;
  }

  fields(event: MonitorEvent): { name: string; value: string }[] { return monitorFields(event); }
}
