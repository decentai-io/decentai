import { Component, Input, OnInit } from '@angular/core';
import { Router } from '@angular/router';

import { byDay, clockTime } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';
import { RecordTone } from 'src/app/components/ui/record-row.component';
import { AiSessionService, AuditPage, AuditQuery } from 'src/app/services/ai-session.service';
import { AuthService } from 'src/app/services/auth.service';
import { auditKind, auditLabel, auditSummary, auditTone } from '../chat/chat-activity-dialog/audit-words';
import { AuditGroup, groupAudit } from './audit-groups';

type AuditTab = 'mine' | 'org';

/**
 * Audit: the record of what agents did. Written by the platform at its
 * chokepoints and by the runtime for every function it ran; never by
 * the assistant, and never carrying a secret value.
 *
 * Two views. "Mine" is the person's own trail across every chat they
 * own — theirs to read, no grant needed. "Organization" is everyone's,
 * plus the installs, grants and credential reads no chat owns — a grant
 * of its own, so the choice appears only for people who hold it.
 *
 * It is read by day. The same thing done several times in a row is one
 * row with a count (audit-groups.ts), and a platform action is said in
 * the words the organization's catalog of actions has for it.
 */
@Component({
  selector: 'app-audit',
  standalone: false,
  templateUrl: './audit.component.html',
  styleUrl: './audit.component.css',
})
export class AuditComponent implements OnInit {
  /** Which view the address names (/audit or /audit/org): the route's
   *  data, bound to this input by the router. */
  @Input() initialView: AuditTab = 'mine';
  loading = true;
  loadingMore = false;
  error = '';
  tab: AuditTab = 'mine';
  readonly tabs: Choice<AuditTab>[] = [
    { value: 'mine', name: 'Mine' },
    { value: 'org', name: 'Organization' },
  ];
  events: any[] = [];
  /** The events in groups, under the day each happened on. */
  days: { day: string; items: AuditGroup[] }[] = [];
  nextBefore: any = null;
  /** What is open: an event by its id, a group by `groupKey`. */
  expanded = new Set<string>();
  /** What each platform action is called: action -> the catalog's words. */
  private actions = new Map<string, string>();

  /** The filters, as typed. Applied on Enter or the button, not on
   *  every keystroke — a trail is read, not searched live. */
  text = '';
  type = '';
  since = '';
  until = '';

  /** The kinds the trail can hold, in the words a person reads. */
  readonly types: Choice<string>[] = [
    { value: '', name: 'Every kind' },
    { value: 'platform.action', name: 'Platform action' },
    { value: 'execution', name: 'Function ran' },
    { value: 'approval.requested', name: 'Approval requested' },
    { value: 'approval.resolved', name: 'Approval answered' },
    { value: 'secret.use', name: 'Credential read' },
    { value: 'llm.use', name: 'Model key read' },
    { value: 'agent.installed', name: 'Agent installed' },
    { value: 'agent.deleted', name: 'Agent uninstalled' },
  ];

  constructor(
    private session: AiSessionService,
    public auth: AuthService,
    private router: Router,
  ) {}

  get canSeeOrg(): boolean { return this.auth.can('ai:audit:list_all'); }

  async ngOnInit(): Promise<void> {
    if (this.initialView === 'org' && this.canSeeOrg) this.tab = 'org';
    for (const service of this.auth.catalog) {
      for (const action of service.actions) this.actions.set(action.action, action.description);
    }
    await this.reload();
  }

  /** Each view has its own address, and going there draws the page
   *  again with that view: nothing is read from here. */
  selectTab(tab: AuditTab): void {
    if (this.tab === tab) return;
    this.router.navigate([tab === 'org' ? '/audit/org' : '/audit']);
  }

  private query(before?: any): AuditQuery {
    const query: AuditQuery = { limit: 50 };
    if (this.text.trim()) query.text = this.text.trim();
    if (this.type) query.event_types = [this.type];
    const since = AuditComponent.localDay(this.since, false);
    const until = AuditComponent.localDay(this.until, true);
    if (since) query.since = since.toISOString();
    if (until) query.until = until.toISOString();
    if (before) query.before = before;
    return query;
  }

  /** The first or the last moment of a picked day, in the person's own
   *  zone. A date input gives `YYYY-MM-DD`, which `new Date()` reads as
   *  midnight UTC — hours off the day they chose, and on the day before
   *  it west of UTC — so the parts are read as local ones. */
  static localDay(value: string, end: boolean): Date | null {
    const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
    if (!parts) return null;
    const [year, month, day] = [Number(parts[1]), Number(parts[2]), Number(parts[3])];
    return end
      ? new Date(year, month - 1, day, 23, 59, 59, 999)
      : new Date(year, month - 1, day, 0, 0, 0, 0);
  }

  async reload(): Promise<void> {
    this.loading = true;
    this.error = '';
    try {
      const page = await this.fetch(this.query());
      this.expanded.clear();
      this.take(page.events, page.next_before);
    } finally {
      this.loading = false;
    }
  }

  async loadMore(): Promise<void> {
    if (!this.nextBefore || this.loadingMore) return;
    this.loadingMore = true;
    try {
      const page = await this.fetch(this.query(this.nextBefore));
      this.take([...this.events, ...page.events], page.next_before);
    } finally {
      this.loadingMore = false;
    }
  }

  private async fetch(query: AuditQuery): Promise<AuditPage> {
    const page = this.tab === 'org'
      ? await this.session.searchAuditAll(query)
      : await this.session.searchAudit(query);
    if (page.error) this.error = page.error;
    return page;
  }

  private take(events: any[], nextBefore: any): void {
    this.events = events;
    this.nextBefore = nextBefore;
    this.days = byDay(groupAudit(events), (group) => group.first.timestamp);
  }

  clearFilters(): void {
    this.text = '';
    this.type = '';
    this.since = '';
    this.until = '';
    void this.reload();
  }

  get filtered(): boolean {
    return !!(this.text.trim() || this.type || this.since || this.until);
  }

  /** A group of several opens to its events; a group of one is that
   *  event, and opens to its details. */
  groupKey(group: AuditGroup): string {
    const id = group.first?.event_id || '';
    return group.events.length > 1 ? `group:${id}` : id;
  }

  toggle(key: string): void {
    if (!key) return;
    if (this.expanded.has(key)) this.expanded.delete(key);
    else this.expanded.add(key);
  }

  isOpen(key: string): boolean { return this.expanded.has(key); }

  // ── Words ───────────────────────────────────────────────────────────

  kind(event: any): string { return auditKind(event); }
  tone(event: any): RecordTone { return auditTone(event); }
  time(event: any): string { return clockTime(event?.timestamp); }

  /** What happened, as a sentence: the function an agent ran, the
   *  platform action in the catalog's words, or what the event of any
   *  other kind was about. */
  words(event: any): string {
    if (event?.event_type === 'execution') return auditLabel(event);
    if (event?.event_type === 'platform.action') {
      return this.actions.get(event.function) || event.function || auditKind(event);
    }
    return auditSummary(event) || auditKind(event);
  }

  /** The line under the words: how a function went, or why a platform
   *  action did not. */
  more(event: any): string {
    if (event?.event_type === 'execution') return auditSummary(event);
    if (event?.event_type !== 'platform.action') return '';
    const details = event.details || {};
    return [details.outcome === 'success' ? '' : details.outcome, details.reason]
      .filter(Boolean).join(' · ');
  }

  /** The dot in a word, for the two kinds that succeed or fail. */
  outcome(event: any): string {
    if (event?.event_type !== 'execution' && event?.event_type !== 'platform.action') return '';
    return ({ ok: 'Succeeded', bad: 'Failed', live: '', muted: '' })[this.tone(event)];
  }

  details(event: any): string {
    const details = { ...(event?.details || {}) };
    return JSON.stringify(details, null, 2);
  }

  hasDetails(event: any): boolean {
    return Object.keys(event?.details || {}).length > 0 || (event?.resource_refs || []).length > 0;
  }
}
