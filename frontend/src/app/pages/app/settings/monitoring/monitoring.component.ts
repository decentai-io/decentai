import { Component, Input, OnDestroy, OnInit } from '@angular/core';

import {
  AgentFiles, AgentUsage, MonitorEvent, MonitoringService, ServiceUsage, UsageNow,
} from 'src/app/services/monitoring.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataPageBase } from '../../data-page-base';
import {
  MONITOR_KINDS, bytesLabel, monitorChip, monitorLabel, monitorSummary, monitorTone,
} from './monitor-words';

type MonitoringView = 'agents' | 'events' | 'connections';

/**
 * Monitoring: what agents use now, and what was seen of them
 * (docs/system/monitoring.md). Read-only: nothing here changes an
 * agent or what it is given.
 *
 * Three views. "Agents" is the running agents with what each holds,
 * against what they are given together, and what each keeps on disk.
 * "Events" is everything written down, by kind and by agent.
 * "Connections" is the connections alone, made and refused.
 */
@Component({
  selector: 'app-monitoring',
  standalone: false,
  templateUrl: './monitoring.component.html',
  styleUrls: [
    '../../data-shared.css', '../../../admin/iam-shared.css',
    '../../ai/audit/audit.component.css', './monitoring.component.css',
  ],
})
export class MonitoringComponent extends DataPageBase implements OnInit, OnDestroy {
  @Input() initialView: MonitoringView = 'agents';
  view: MonitoringView = 'agents';

  /** How often what the agents use is asked for again, while it is
   *  being looked at. */
  static readonly EVERY_MS = 5000;

  // ── What they use now ───────────────────────────────────────────
  usage: UsageNow | null = null;
  loadingUsage = true;
  private timer: any = null;
  /** The agent whose files are shown, and what it keeps. */
  filesOf = '';
  files: AgentFiles | null = null;
  loadingFiles = false;

  // ── What happened ───────────────────────────────────────────────
  readonly kinds = MONITOR_KINDS;
  events: MonitorEvent[] = [];
  nextBefore: number | null = null;
  loading = false;
  loadingMore = false;
  kind = '';
  agent = '';
  refusedOnly = false;
  expanded = new Set<MonitorEvent>();
  /** The agents the log has named, for the filter: ref -> name. */
  named = new Map<string, string>();

  constructor(private monitoring: MonitoringService, public auth: AuthService) {
    super();
  }

  get canSeeEvents(): boolean { return this.auth.can('agents:monitor:events'); }
  get canSeeFiles(): boolean { return this.auth.can('agents:monitor:files'); }

  async ngOnInit(): Promise<void> {
    await this.select(this.initialView === 'agents' || !this.canSeeEvents
      ? 'agents' : this.initialView);
  }

  ngOnDestroy(): void { this.stopWatching(); }

  async select(view: MonitoringView): Promise<void> {
    this.view = view;
    this.error = '';
    this.stopWatching();
    if (view === 'agents') {
      await this.readUsage();
      this.timer = setInterval(() => void this.readUsage(), MonitoringComponent.EVERY_MS);
    } else {
      await this.reload();
    }
  }

  private stopWatching(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  // ── Agents ──────────────────────────────────────────────────────

  async readUsage(): Promise<void> {
    const found = await this.monitoring.usage();
    this.loadingUsage = false;
    if (found.error) { this.fail(found.error); return; }
    this.error = '';
    this.usage = found;
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
      if (found.error) this.fail(found.error);
      else this.files = found;
    } finally {
      this.loadingFiles = false;
    }
  }

  // ── Events and connections ──────────────────────────────────────

  private query(before?: number | null) {
    const kinds = this.view === 'connections' ? ['connection']
      : this.kind ? [this.kind] : [];
    return { kinds, agent: this.agent, before, limit: 100 };
  }

  async reload(): Promise<void> {
    this.loading = true;
    this.error = '';
    try {
      const page = await this.monitoring.events(this.query());
      if (page.error) { this.fail(page.error); return; }
      this.events = page.events;
      this.nextBefore = page.next_before;
      this.expanded.clear();
      this.remember(page.events);
    } finally {
      this.loading = false;
    }
  }

  async loadMore(): Promise<void> {
    if (this.nextBefore == null || this.loadingMore) return;
    this.loadingMore = true;
    try {
      const page = await this.monitoring.events(this.query(this.nextBefore));
      if (page.error) { this.fail(page.error); return; }
      this.events = [...this.events, ...page.events];
      this.nextBefore = page.next_before;
      this.remember(page.events);
    } finally {
      this.loadingMore = false;
    }
  }

  private remember(events: MonitorEvent[]): void {
    for (const event of events) {
      if (event.agent?.startsWith('agt_')) {
        this.named.set(event.agent, event.name || event.agent);
      }
    }
  }

  get agentChoices(): { value: string; label: string }[] {
    return [...this.named.entries()]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }

  /** What is listed: all that was read, or of the connections only
   *  the ones refused or not reached. */
  get shown(): MonitorEvent[] {
    if (this.view !== 'connections' || !this.refusedOnly) return this.events;
    return this.events.filter(e => e['allowed'] === false || e['reached'] === false);
  }

  get filtered(): boolean { return !!(this.kind || this.agent || this.refusedOnly); }

  clearFilters(): void {
    this.kind = '';
    this.agent = '';
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
  chip(event: MonitorEvent): string { return monitorChip(event); }
  tone(event: MonitorEvent): string { return monitorTone(event); }
  bytes(value: any): string { return bytesLabel(value); }

  when(at?: number | null): string {
    if (!at) return '';
    const date = new Date(at * 1000);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
  }

  processors(value: any): string {
    return `${Math.round((Number(value) || 0) * 100)}%`;
  }

  details(event: MonitorEvent): string { return JSON.stringify(event, null, 2); }
}
