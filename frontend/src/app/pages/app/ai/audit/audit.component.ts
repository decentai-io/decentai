import { Component, Input, OnInit } from '@angular/core';
import { Router } from '@angular/router';

import { AiSessionService, AuditPage, AuditQuery } from 'src/app/services/ai-session.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataPageBase } from '../../data-page-base';
import { auditLabel, auditSummary, auditTone } from '../chat/chat-activity-dialog/audit-words';

type AuditTab = 'mine' | 'org';

/**
 * Audit: the record of what agents did. Written by the platform at its
 * chokepoints and by the runtime for every function it ran; never by
 * the assistant, and never carrying a secret value.
 *
 * Two views. "Mine" is the person's own trail across every chat they
 * own — theirs to read, no grant needed. "Organization" is everyone's,
 * plus the installs, grants and credential reads no chat owns — a grant
 * of its own, so the tab appears only for people who hold it.
 */
@Component({
  selector: 'app-audit',
  standalone: false,
  templateUrl: './audit.component.html',
  styleUrls: ['../../data-shared.css', '../../../admin/iam-shared.css', './audit.component.css'],
})
export class AuditComponent extends DataPageBase implements OnInit {
  /** Which view the address names: the Settings page reads it from
   *  the route and hands it down. */
  @Input() initialView: AuditTab = 'mine';
  loading = true;
  loadingMore = false;
  tab: AuditTab = 'mine';
  events: any[] = [];
  nextBefore: any = null;
  expanded = new Set<string>();

  /** The filters, as typed. Applied on Enter or the button, not on
   *  every keystroke — a trail is read, not searched live. */
  text = '';
  type = '';
  since = '';
  until = '';

  /** The kinds the trail can hold, in the words a person reads. */
  readonly types: { value: string; label: string }[] = [
    { value: '', label: 'Every kind' },
    { value: 'platform.action', label: 'Platform action' },
    { value: 'execution', label: 'Function ran' },
    { value: 'approval.requested', label: 'Approval requested' },
    { value: 'approval.resolved', label: 'Approval answered' },
    { value: 'secret.use', label: 'Credential read' },
    { value: 'llm.use', label: 'Model key read' },
    { value: 'agent.installed', label: 'Agent installed' },
    { value: 'agent.deleted', label: 'Agent uninstalled' },
  ];

  constructor(
    private session: AiSessionService,
    public auth: AuthService,
    private router: Router,
  ) {
    super();
  }

  get canSeeOrg(): boolean { return this.auth.can('ai:audit:list_all'); }

  async ngOnInit(): Promise<void> {
    if (this.initialView === 'org' && this.canSeeOrg) this.tab = 'org';
    await this.reload();
  }

  /** Each view has its own address, and going there draws the page
   *  again with that view: nothing is read from here. */
  selectTab(tab: AuditTab): void {
    if (this.tab === tab) return;
    this.router.navigate([tab === 'org' ? '/settings/audit/org' : '/settings/audit']);
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
      this.events = page.events;
      this.nextBefore = page.next_before;
      this.expanded.clear();
    } finally {
      this.loading = false;
    }
  }

  async loadMore(): Promise<void> {
    if (!this.nextBefore || this.loadingMore) return;
    this.loadingMore = true;
    try {
      const page = await this.fetch(this.query(this.nextBefore));
      this.events = [...this.events, ...page.events];
      this.nextBefore = page.next_before;
    } finally {
      this.loadingMore = false;
    }
  }

  private async fetch(query: AuditQuery): Promise<AuditPage> {
    const page = this.tab === 'org'
      ? await this.session.searchAuditAll(query)
      : await this.session.searchAudit(query);
    if (page.error) this.fail(page.error);
    return page;
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

  toggle(event: any): void {
    const id = event?.event_id;
    if (!id) return;
    if (this.expanded.has(id)) this.expanded.delete(id);
    else this.expanded.add(id);
  }

  isOpen(event: any): boolean {
    return this.expanded.has(event?.event_id);
  }

  // ── Words ───────────────────────────────────────────────────────────

  label(event: any): string { return auditLabel(event); }
  summary(event: any): string { return auditSummary(event); }
  tone(event: any): string { return auditTone(event); }

  when(value?: string | null): string {
    if (!value) return '';
    const date = new Date(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
  }

  details(event: any): string {
    const details = { ...(event?.details || {}) };
    return JSON.stringify(details, null, 2);
  }

  hasDetails(event: any): boolean {
    return Object.keys(event?.details || {}).length > 0 || (event?.resource_refs || []).length > 0;
  }
}
