import { Component, OnDestroy, OnInit } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { FilterGroup, FilterItem } from 'src/app/components/ui/filter-column.component';
import { AgentsService } from 'src/app/services/agents.service';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { OauthConnectService } from 'src/app/services/oauth.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import { SecretDefinition, SecretDefinitionsService } from 'src/app/services/secret-definitions.service';
import { Secret, SecretUser, SecretsService } from 'src/app/services/secrets.service';
import { DataPageBase } from '../data-page-base';
import { Peer, ShareEditor, SharingChip } from '../share-editor';
import { SecretFormComponent, SecretFormData, SecretFormResult } from './secret-form.component';
import {
  Consent, agentLabel, agentsUsing, consentsOf, definitionOf, deleteQuestion, identifier, isOauth, isOutdated, isSiteLogin,
  lentTo, loginHost, needsReconnect, secretTitle,
} from './secret-shared';

type Show = 'mine' | 'shared';

/**
 * Secrets: what a person stores for agents to act with. Two kinds live
 * here: the credentials installed agents declare a shape for, and the
 * logins agents asked for as they worked on a site.
 *
 * One list, with a column at its left that narrows it: whose, which
 * agent uses it, which site a login is for. A secret's name opens it
 * at /secrets/<ref>, beside the list on a wide screen: its fields (the
 * encrypted ones never shown), the agents that use it and how each
 * came to, and who can see it. Adding, changing and lending are one
 * dialog (secret-form.component); what is chosen and the search are in
 * the address.
 */
@Component({
  selector: 'app-secrets',
  standalone: false,
  templateUrl: './secrets.component.html',
  styleUrl: './secrets.component.css',
})
export class SecretsComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  secrets: Secret[] = [];
  definitions: SecretDefinition[] = [];
  profile: Profile | null = null;
  private peers: Peer[] = [];

  // ── What the address says ───────────────────────────────────────────
  show: Show = 'mine';
  /** An agent's name, 'none' for secrets no agent uses, or ''. */
  agent = '';
  /** A login's site, or ''. */
  host = '';
  query = '';
  /** The secret opened beside the list; '' for none. */
  openRef = '';
  private address = new Subscription();

  /** The secret an action is on its way for. */
  busyRef = '';

  /** Who can see a secret, changed from its page without the form. */
  readonly share = new ShareEditor(() => this.profile);
  shareProblem = '';

  transferTarget: Secret | null = null;
  transferring = false;

  constructor(
    private service: SecretsService,
    private definitionsService: SecretDefinitionsService,
    private profiles: ProfileService,
    public auth: AuthService,
    private oauth: OauthConnectService,
    private agents: AgentsService,
    private router: Router,
    private route: ActivatedRoute,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    this.address.add(this.route.queryParamMap.subscribe((params) => {
      this.show = params.get('show') === 'shared' ? 'shared' : 'mine';
      this.agent = params.get('agent') ?? '';
      this.host = params.get('site') ?? '';
      this.query = params.get('q') ?? '';
    }));
    this.address.add(this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) this.readOpened();
    }));

    const [secrets, definitions, profile] = await Promise.all([
      this.service.list(),
      this.definitionsService.list(),
      this.profiles.get(),
    ]);
    this.secrets = secrets;
    this.definitions = definitions;
    this.profile = profile;
    this.loading = false;
    this.readOpened();
    this.peers = await this.profiles.peers().catch(() => []);
    this.share.peers = this.peers;
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
  }

  private async reload(): Promise<void> {
    this.secrets = await this.service.list();
  }

  // ── Access ──────────────────────────────────────────────────────────

  get canCreate(): boolean { return this.auth.can('secrets:secret:create'); }
  get canUpdate(): boolean { return this.auth.can('secrets:secret:update'); }
  get canDelete(): boolean { return this.auth.can('secrets:secret:delete'); }
  get canShareAny(): boolean { return this.auth.can('secrets:secret:set_owner_any'); }
  get myUserId(): string { return this.profile?.user_id ?? ''; }

  /** Whether the person may change this secret: its maker, or whoever
   *  may maintain any. */
  mayChange(secret: Secret): boolean {
    return secret.created_by === this.myUserId || this.canShareAny;
  }

  // ── The address ─────────────────────────────────────────────────────

  go(changes: Record<string, string>): void {
    const queryParams: Record<string, string | null> = {};
    for (const [key, value] of Object.entries(changes)) queryParams[key] = value || null;
    void this.router.navigate([], {
      relativeTo: this.route.firstChild ?? this.route,
      queryParams,
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** The column chose something: whose, an agent, or a site. An agent
   *  and a site are two views of the list, so one clears the other. */
  filter(key: string, value: string): void {
    if (key === 'show') return this.go({ show: value === 'mine' ? '' : value });
    if (key === 'agent') return this.go({ agent: value, site: '' });
    this.go({ site: value, agent: '' });
  }

  private readOpened(): void {
    this.openRef = this.route.snapshot.firstChild?.paramMap.get('ref') || '';
    if (this.openRef && !this.loading && !this.opened) {
      this.fail('That secret is not one you can see any more.');
      this.closeOpened();
    }
  }

  get opened(): Secret | null {
    return this.secrets.find((secret) => secret.resource_ref === this.openRef) || null;
  }

  closeOpened(): void {
    void this.router.navigate(['/secrets'], { queryParamsHandling: 'preserve' });
  }

  // ── The list ────────────────────────────────────────────────────────

  private isMine(secret: Secret): boolean {
    return secret.created_by === this.myUserId;
  }

  /** The secrets of the chosen "whose": what the agents and the sites
   *  are counted in. */
  private get pool(): Secret[] {
    return this.secrets.filter((secret) => this.isMine(secret) === (this.show === 'mine'));
  }

  get chosen(): Record<string, string> {
    return { show: this.show, agent: this.agent, site: this.host };
  }

  get filters(): FilterGroup[] {
    const mine = this.secrets.filter((secret) => this.isMine(secret)).length;
    const pool = this.pool;
    const byAgent = new Map<string, FilterItem>();
    const bySite = new Map<string, FilterItem>();
    let orphans = 0;
    for (const secret of pool) {
      if (isSiteLogin(secret)) {
        const host = loginHost(secret);
        const item = bySite.get(host) ?? { value: host, name: host, icon: 'globe', count: 0 };
        item.count = (item.count ?? 0) + 1;
        bySite.set(host, item);
        continue;
      }
      const names = agentsUsing(secret);
      if (!names.length) orphans += 1;
      for (const name of names) {
        const item = byAgent.get(name) ?? { value: name, name, agent: { name }, count: 0 };
        item.count = (item.count ?? 0) + 1;
        byAgent.set(name, item);
      }
    }
    const agents = [...byAgent.values()].sort((a, b) => a.name.localeCompare(b.name));
    if (orphans) agents.push({ value: 'none', name: 'No agent', icon: 'key-round', count: orphans });
    const groups: FilterGroup[] = [{
      key: 'show',
      required: true,
      items: [
        { value: 'mine', name: 'Mine', count: mine },
        { value: 'shared', name: 'Shared with me', count: this.secrets.length - mine },
      ],
    }];
    if (agents.length) groups.push({ key: 'agent', label: 'By agent', items: agents });
    if (bySite.size) {
      groups.push({
        key: 'site', label: 'Saved logins',
        items: [...bySite.values()].sort((a, b) => a.name.localeCompare(b.name)),
      });
    }
    return groups;
  }

  /** The secrets the column and the search leave, newest change first. */
  get shownSecrets(): Secret[] {
    const query = this.query.trim().toLowerCase();
    const changed = (secret: Secret) => secret.updated_at || secret.created_at || '';
    return this.pool.filter((secret) => {
      if (this.host && !(isSiteLogin(secret) && loginHost(secret) === this.host)) return false;
      if (this.agent) {
        const names = isSiteLogin(secret) ? null : agentsUsing(secret);
        if (!names) return false;
        if (this.agent === 'none' ? names.length > 0 : !names.includes(this.agent)) return false;
      }
      return !query || [secretTitle(secret), this.kindOf(secret), this.identifierOf(secret),
        ...agentsUsing(secret), secret.created_by_name || ''].join(' ').toLowerCase().includes(query);
    }).sort((a, b) => changed(b).localeCompare(changed(a)));
  }

  get filtered(): boolean {
    return !!(this.query || this.agent || this.host);
  }

  titleOf(secret: Secret): string { return secretTitle(secret); }
  isLogin(secret: Secret): boolean { return isSiteLogin(secret); }
  isOauth(secret: Secret): boolean { return isOauth(secret, this.definitions); }
  outdated(secret: Secret): boolean { return isOutdated(secret, this.definitions); }
  needsReconnect(secret: Secret): boolean { return needsReconnect(secret, this.definitions); }
  identifierOf(secret: Secret): string { return identifier(secret, this.definitions); }

  /** What kind of thing it is: the shape's label, or a saved login. */
  kindOf(secret: Secret): string {
    return isSiteLogin(secret) ? 'Saved login' : definitionOf(secret, this.definitions)?.label || secret.resource_id;
  }

  /** The agent whose mark stands for it in the list: the one it was
   *  saved for, else the first that uses it; '' for none. */
  markOf(secret: Secret): string {
    return agentsUsing(secret)[0] || '';
  }

  /** Who uses it, in a line: "Used by Notebook · lent to Gmail". */
  usedBy(secret: Secret): string {
    if (isSiteLogin(secret)) {
      const consents = consentsOf(secret);
      return consents.length ? `Allowed for ${consents.map((consent) => consent.name).join(', ')}` : '';
    }
    const lent = lentTo(secret).map((agent) => agent.name);
    return [
      secret.home_agent ? `Used by ${agentLabel(secret.home_agent.name)}` : '',
      lent.length ? `lent to ${lent.join(', ')}` : '',
    ].filter(Boolean).join(' · ');
  }

  updated(secret: Secret): string {
    return relativeDate(secret.updated_at || secret.created_at || '').replace(/^J/, 'j');
  }

  updatedInFull(secret: Secret): string {
    return fullDate(secret.updated_at || secret.created_at || '');
  }

  sharing(secret: Secret): SharingChip {
    return this.share.chip(secret.owner, secret.created_by, secret.owner_names, secret.created_by_name);
  }

  // ── Adding, changing, lending: the one dialog ───────────────────────

  private async openForm(more: Partial<SecretFormData>): Promise<void> {
    const ref = this.dialog.open<SecretFormComponent, SecretFormData, SecretFormResult | null>(
      SecretFormComponent, {
        width: '560px', maxWidth: '100vw',
        data: {
          definitions: this.definitions, profile: this.profile, peers: this.peers,
          canShareAny: this.canShareAny, ...more,
        },
      });
    const done = await firstValueFrom(ref.afterClosed());
    if (!done) return;
    await this.reload();
    this.flash(done.notice);
    // A secret just made is the one to look at.
    if (done.ref && !more.secret && !more.lend) void this.router.navigate(['/secrets', done.ref]);
  }

  add(): Promise<void> { return this.openForm({}); }
  edit(secret: Secret): Promise<void> { return this.openForm({ secret }); }
  lend(secret: Secret): Promise<void> { return this.openForm({ lend: secret }); }

  // ── What a secret's page asks for ───────────────────────────────────

  /** Takes a lent secret back from one agent. */
  async takeBack(secret: Secret, agent: SecretUser): Promise<void> {
    if (!agent.grant_id) return;
    const sure = await this.confirm.ask({
      title: `Take ${secret.name} back from ${agent.name}?`,
      body: `${agent.name} can no longer use it, and will ask for a credential the next time it needs one.`,
      confirm: 'Take back',
      danger: true,
    });
    if (!sure) return;
    this.busyRef = secret.resource_ref;
    try {
      const result = await this.agents.revokeSecret(agent.agent_ref, agent.grant_id);
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(`Taken back from ${agent.name}.`);
    } finally {
      this.busyRef = '';
    }
  }

  /** Takes one agent's consent back from a login: the pair leaves, and
   *  the next time that agent reaches the site it asks again. */
  async revokeConsent(secret: Secret, consent: Consent): Promise<void> {
    this.busyRef = secret.resource_ref;
    try {
      const consents = consentsOf(secret).map((each) => each.pair)
        .filter((pair) => pair !== consent.pair).join(',');
      const result = await this.service.update(secret.resource_ref, { fields: { consents } });
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(`${consent.name} is no longer allowed on ${consent.site}.`);
    } finally {
      this.busyRef = '';
    }
  }

  async reconnect(secret: Secret): Promise<void> {
    this.busyRef = secret.resource_ref;
    try {
      const result = await this.oauth.connect({ resource_ref: secret.resource_ref });
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(`${secret.name} reconnected.`);
    } finally {
      this.busyRef = '';
    }
  }

  // ── Sharing ─────────────────────────────────────────────────────────

  openShare(secret: Secret): void {
    this.shareProblem = '';
    this.share.open(secret.resource_ref, secret.owner);
  }

  async saveShare(): Promise<void> {
    this.share.saving = true;
    this.shareProblem = '';
    try {
      const result = await this.service.update(this.share.ref, { owner: this.share.owner() as any });
      if (result.error) {
        this.shareProblem = result.error + (result.ungrantable?.length ? ` (${result.ungrantable.join(', ')})` : '');
        return;
      }
      this.share.close();
      await this.reload();
      this.flash('Sharing updated.');
    } finally {
      this.share.saving = false;
    }
  }

  // ── Deleting ────────────────────────────────────────────────────────

  /** Asked first, in the words of what goes with it: the agent it was
   *  saved for is left without one, and it is taken back from the
   *  agents it was lent to. Deleting does both; it is not refused. */
  async askDelete(secret: Secret): Promise<void> {
    const question = deleteQuestion(secret);
    const sure = await this.confirm.ask({ ...question, danger: true });
    if (!sure) return;
    this.busyRef = secret.resource_ref;
    try {
      const result = await this.service.remove(secret.resource_ref, question.takeBack);
      if (result.error) return this.fail(result.error);
      if (secret.resource_ref === this.openRef) this.closeOpened();
      await this.reload();
      this.flash('Secret deleted.');
    } finally {
      this.busyRef = '';
    }
  }

  // ── Handing over ────────────────────────────────────────────────────

  requestTransfer(secret: Secret): void {
    this.transferTarget = secret;
  }

  closeTransfer(): void {
    if (!this.transferring) this.transferTarget = null;
  }

  async transfer(person: { user_id: string; user_name: string; email: string }): Promise<void> {
    const item = this.transferTarget;
    if (!item) return;
    this.transferring = true;
    try {
      const result = await this.service.transfer(item.resource_ref, person.user_id);
      // Closed either way: a refusal is said on the page, where it can
      // be read, not under the dialog.
      this.transferTarget = null;
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(`Handed over to ${person.user_name || person.email}.`);
    } finally {
      this.transferring = false;
    }
  }
}
