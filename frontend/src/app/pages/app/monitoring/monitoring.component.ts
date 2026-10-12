import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';

import { byDay, clockTime, fullDate, localDay, relativeDate } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';
import { RecordTone } from 'src/app/components/ui/record-row.component';
import { AuthService } from 'src/app/services/auth.service';
import {
  AgentFiles, AgentUsage, MonitorEvent, MonitoringService, ServiceUsage, UsageNow,
} from 'src/app/services/monitoring.service';
import {
  MONITOR_KINDS, bytesLabel, monitorChip, monitorKind, monitorLabel, monitorSummary, monitorTone,
} from './monitor-words';

type MonitoringView = 'agents' | 'events' | 'connections';

/**
 * Monitoring: what agents use now, and what was seen of them
 * (docs/system/monitoring.md). Read-only: nothing here changes an
 * agent or what it is given.
 *
 * Three views, each with its own address. "Agents" (/monitoring) is
 * the running agents with what each holds, against what they are given
 * together, and what each keeps on disk. "Events" (/monitoring/events)
 * is everything written down, by kind and by agent. "Connections"
 * (/monitoring/connections) is the connections alone, made and refused.
 */
@Component({
  selector: 'app-monitoring',
  standalone: false,
  templateUrl: './monitoring.component.html',
  styleUrl: './monitoring.component.css',
})
export class MonitoringComponent implements OnInit, OnDestroy {
  view: MonitoringView = 'agents';
  readonly views: Choice<MonitoringView>[] = [
    { value: 'agents', name: 'Agents' },
    { value: 'events', name: 'Events' },
    { value: 'connections', name: 'Connections' },
  ];
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
  readonly kinds = MONITOR_KINDS;
  events: MonitorEvent[] = [];
  /** What is listed, under the day it happened on. */
  days: { day: string; items: MonitorEvent[] }[] = [];
  nextBefore: number | null = null;
  loading = false;
  loadingMore = false;
  kind = '';
  agent = '';
  /** The days asked for, as the date boxes give them. */
  since = '';
  until = '';
  refusedOnly = false;
  expanded = new Set<MonitorEvent>();
  /** The agents the log has named, for the filter: ref -> name. */
  named = new Map<string, string>();

  constructor(
    private monitoring: MonitoringService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
  ) {}

  get canSeeEvents(): boolean { return this.auth.can('agents:monitor:events'); }
  get canSeeFiles(): boolean { return this.auth.can('agents:monitor:files'); }

  async ngOnInit(): Promise<void> {
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
    void this.router.navigate(view === 'agents' ? ['/monitoring'] : ['/monitoring', view]);
  }

  private async readView(): Promise<void> {
    const named = this.route.snapshot.firstChild?.url[0]?.path;
    const view: MonitoringView = named === 'events' || named === 'connections' ? named : 'agents';
    if (view !== 'agents' && !this.canSeeEvents) {
      void this.router.navigate(['/monitoring'], { replaceUrl: true });
      return;
    }
    if (view !== this.view || !this.timer && view === 'agents') await this.select(view);
  }

  async select(view: MonitoringView): Promise<void> {
    this.view = view;
    this.error = '';
    this.stopWatching();
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
    for (const agent of found.agents) this.named.set(agent.agent, agent.name);
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

  private query(before?: number | null) {
    const kinds = this.view === 'connections' ? ['connection']
      : this.kind ? [this.kind] : [];
    // The server reads `until` as the first moment not asked for.
    const since = localDay(this.since, false);
    const until = localDay(this.until, true);
    return {
      kinds, agent: this.agent, before, limit: 100,
      since: since ? since.getTime() / 1000 : null,
      until: until ? (until.getTime() + 1) / 1000 : null,
    };
  }

  async reload(): Promise<void> {
    this.loading = true;
    this.error = '';
    try {
      const page = await this.monitoring.events(this.query());
      if (page.error) { this.error = page.error; return; }
      this.expanded.clear();
      this.take(page.events, page.next_before);
    } finally {
      this.loading = false;
    }
  }

  async loadMore(): Promise<void> {
    if (this.nextBefore == null || this.loadingMore) return;
    this.loadingMore = true;
    try {
      const page = await this.monitoring.events(this.query(this.nextBefore));
      if (page.error) { this.error = page.error; return; }
      this.take([...this.events, ...page.events], page.next_before);
    } finally {
      this.loadingMore = false;
    }
  }

  private take(events: MonitorEvent[], nextBefore: number | null): void {
    this.events = events;
    this.nextBefore = nextBefore;
    for (const event of events) {
      if (event.agent?.startsWith('agt_')) {
        this.named.set(event.agent, event.name || event.agent);
      }
    }
    this.arrange();
  }

  private arrange(): void {
    this.days = byDay(this.shown, (event) => event.at * 1000);
  }

  get agentChoices(): Choice<string>[] {
    const agents = [...this.named.entries()]
      .map(([value, name]) => ({ value, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
    return [{ value: '', name: 'Every agent' }, ...agents];
  }

  /** What is listed: all that was read, or of the connections only
   *  the ones refused or not reached. */
  get shown(): MonitorEvent[] {
    if (this.view !== 'connections' || !this.refusedOnly) return this.events;
    return this.events.filter(e => e['allowed'] === false || e['reached'] === false);
  }

  /** Narrows what was already read, so it needs no asking again. */
  showRefusedOnly(only: boolean): void {
    this.refusedOnly = only;
    this.arrange();
  }

  get filtered(): boolean {
    return !!(this.kind || this.agent || this.since || this.until || this.refusedOnly);
  }

  clearFilters(): void {
    this.kind = '';
    this.agent = '';
    this.since = '';
    this.until = '';
    this.refusedOnly = false;
    void this.reload();
  }

  toggle(event: MonitorEvent): void {
    if (this.expanded.has(event)) this.expanded.delete(event);
    else this.expanded.add(event);
  }

  isOpen(event: MonitorEvent): boolean { return this.expanded.has(event); }

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

  details(event: MonitorEvent): string { return JSON.stringify(event, null, 2); }
}
