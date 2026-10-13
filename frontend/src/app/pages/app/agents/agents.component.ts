import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';

import { AgentOffer, AgentsService } from 'src/app/services/agents.service';
import { Group, GroupService } from 'src/app/services/group.service';
import { ProfileService } from 'src/app/services/profile.service';
import { SecretsService } from 'src/app/services/secrets.service';
import { AuthService } from 'src/app/services/auth.service';
import { Choice } from 'src/app/components/ui/radio-keys';
import { ConfirmService } from 'src/app/services/confirm.service';
import { DataPageBase } from '../data-page-base';
import { DetailTab } from './agent-detail.component';
import {
  AgentCredential, Standing, agentFacts, familyOf, standingOf, versionLabel,
} from './agent-shared';

/** The tabs an address may name. */
const TABS: DetailTab[] = ['overview', 'credentials', 'functions', 'access'];

/**
 * Installed agents: what this organization has approved, and how it is
 * set up.
 *
 * Everything here is already running or ready to. This page is the list
 * and the one decision that belongs to the SET rather than to any single
 * agent — which agents new chats start with. Everything you do to one
 * agent happens on its own view (agent-detail.component).
 *
 * Finding new agents is the marketplace's job, not this page's.
 *
 * The list is /agents; an agent's page is /agents/<ref>, and its tabs
 * /agents/<ref>/credentials, /functions and /access. They are views of
 * this one page (routes/app/agents.routes.ts), which reads which one
 * from the address after each navigation, so Back and a pasted link
 * both work.
 */
@Component({
  selector: 'app-agents',
  standalone: false,
  templateUrl: './agents.component.html',
  styleUrl: './agent-shared.css',
})
export class AgentsComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  agents: AgentOffer[] = [];
  busyId = '';
  query = '';
  filter: 'all' | 'attention' | 'updates' = 'all';
  page = 1;
  readonly pageSize = 12;

  openAgent: AgentOffer | null = null;
  /** The open agent's tab, as the address names it. */
  openTab: DetailTab = 'overview';
  private address = new Subscription();

  /** Which agents a NEW chat starts with. No preference at all means
   *  every installed one. */
  defaultAgents = new Set<string>();
  /** The agent whose switch is mid-write, so only that one is frozen. */
  savingDefaultFor = '';
  /** Ticked for a bulk uninstall, by id — pruned on every reload, so a
   *  row that has gone cannot stay selected. */
  selected = new Set<string>();
  /** A run is in flight; busyId names the one being withdrawn now. */
  bulkBusy = false;

  groups: Group[] = [];
  /** The groups the person belongs to — how far their sharing reaches. */
  myGroups: { group_id: string; group_name: string }[] = [];
  myUserId = '';
  /** family -> the resource_ref this person marked as their default. */
  secretDefaults: Record<string, string> = {};

  /** definition_id -> how many secrets exist for it. An agent that
   *  declares a credential and has none saved is installed and unable to
   *  do the thing it was installed for. */
  private secretCounts = new Map<string, number>();

  /** The open agent's credentials, computed once and kept — a template
   *  that loops over freshly built objects while holding form controls
   *  re-registers them on every change-detection pass, and the tab
   *  freezes. A stable array is what breaks that cycle. */
  openCredentials: AgentCredential[] = [];

  constructor(
    private service: AgentsService,
    public auth: AuthService,
    private profile: ProfileService,
    private secrets: SecretsService,
    private groupService: GroupService,
    private confirm: ConfirmService,
    private router?: Router,
    private route?: ActivatedRoute,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    await this.reload();
    this.loading = false;
    await this.readAddress();
    if (this.router) {
      this.address.add(this.router.events
        .pipe(filter((event) => event instanceof NavigationEnd))
        .subscribe(() => void this.readAddress()));
    }

    const profile = await this.profile.get();
    this.myUserId = profile?.user_id ?? '';
    this.myGroups = (profile?.groups ?? []).filter(
      (group: any) => group.group_id !== 'everyone');
    this.secretDefaults = profile?.preferences?.secrets?.defaults ?? {};
    const configured = profile?.preferences?.chat?.enabled_agents;
    this.defaultAgents = new Set(Array.isArray(configured)
      ? configured : this.agents.map((agent) => agent.agent_id));
    await this.countCredentials();
    // The counts say whether a credential is missing: the open agent's
    // slots, and the tab an address without one lands on, follow them.
    if (this.openAgent) {
      this.openCredentials = this.credentials(this.openAgent);
      await this.readAddress();
    }
  }

  /** Show what the address names: the list, or an agent and its tab.
   *  An address with no tab opens where the work is: on Credentials
   *  while one is missing, on the Overview otherwise. */
  private async readAddress(): Promise<void> {
    const params = this.route?.snapshot.firstChild?.paramMap;
    const ref = params?.get('ref') || '';
    if (!ref) {
      if (this.openAgent) this.showList();
      return;
    }
    const agent = this.agents.find((candidate) => candidate.agent_id === ref);
    if (!agent) {
      this.showList();
      this.fail('That agent is not installed here.');
      void this.router?.navigate(['/agents'], { replaceUrl: true });
      return;
    }
    if (this.openAgent?.agent_id !== ref) await this.showAgent(agent);
    const tab = params?.get('tab') as DetailTab | null;
    this.openTab = tab && TABS.includes(tab) ? tab
      : this.missingCredentials(agent).length ? 'credentials' : 'overview';
  }

  private async reload(): Promise<void> {
    await this.fetchAgents();
    // A tick on a row that no longer exists would count toward a bulk
    // action nobody can see.
    this.selected = new Set([...this.selected].filter(
      (id) => this.agents.some((agent) => agent.agent_id === id)));
    this.followOpenAgent();
    this.watchPreparing();
  }

  /** The open agent is one of the rows just read: its status, version
   *  and readiness are shown from the fresh row. A row that reads the
   *  same is left alone, so the tabs beneath it are not redrawn. */
  private followOpenAgent(): void {
    const open = this.openAgent;
    if (!open) return;
    const fresh = this.agents.find((agent) => agent.agent_id === open.agent_id);
    if (!fresh) {
      this.closeDetail();
      return;
    }
    if (JSON.stringify(fresh) === JSON.stringify(open)) return;
    this.openAgent = fresh;
    this.openCredentials = this.credentials(fresh);
  }

  // ── Readiness ───────────────────────────────────────────────────────

  /** While a runtime is still building something, the page asks again
   *  every few seconds, and stops the moment nothing is preparing. */
  private preparingTimer: ReturnType<typeof setInterval> | null = null;
  /** A read still under way when the page closes must not start the
   *  timer again. */
  private destroyed = false;

  private watchPreparing(): void {
    const preparing = this.agents.some((agent) => agent.prepared?.state === 'preparing');
    if (!preparing || this.destroyed) {
      this.stopWatching();
      return;
    }
    if (this.preparingTimer) return;
    this.preparingTimer = setInterval(() => { void this.reload(); }, 4000);
  }

  private stopWatching(): void {
    if (this.preparingTimer) {
      clearInterval(this.preparingTimer);
      this.preparingTimer = null;
    }
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.address.unsubscribe();
    this.stopWatching();
  }

  /** What a screen reader is told when an agent that was preparing
   *  no longer is: the chip changes on its own, and silently. */
  announcement = '';

  private announcePrepared(before: AgentOffer[]): void {
    const waiting = new Set(before
      .filter((agent) => agent.prepared?.state === 'preparing')
      .map((agent) => agent.agent_id));
    const done = this.agents
      .filter((agent) => waiting.has(agent.agent_id) && agent.prepared?.state !== 'preparing')
      .map((agent) => `${agent.name || agent.agent_id} `
        + (agent.prepared?.state === 'failed' ? 'could not be prepared' : 'is ready'));
    if (done.length) this.announcement = `${done.join('. ')}.`;
  }

  private async fetchAgents(): Promise<void> {
    const before = this.agents;
    await this.readAgents();
    this.announcePrepared(before);
  }

  private async readAgents(): Promise<void> {
    if (this.canDiscover) {
      this.agents = await this.service.available();
      return;
    }
    // A reader sees the approved set — the same rows, minus anything that
    // would only matter to whoever may install. The list does not carry
    // where an agent connects, so `network` stays absent and the detail
    // says it is not shown rather than guessing.
    this.agents = (await this.service.installed()).map((agent) => ({
      agent_id: agent.agent_id,
      name: agent.name,
      description: agent.description,
      status: 'installed' as const,
      loaded_version: null,
      installed_version: agent.version,
      functions: (agent.functions || []).map((name: string) => ({
        name, tool: '', description: '',
        permission_level: null, timeout_seconds: null,
      })),
      resources: {},
      resource_refs: agent.resources || {},
      dependencies: [],
      scopes: agent.scopes || [],
      source: agent.source || {},
      package_digest: agent.package_digest || '',
      prepared: agent.prepared ?? null,
    }));
  }

  get canDiscover(): boolean { return this.auth.can('agents:agent:available'); }
  get canInstall(): boolean { return this.auth.can('agents:agent:install'); }
  get canUninstall(): boolean { return this.auth.can('agents:agent:delete'); }
  get canShareAny(): boolean {
    return this.auth.can('secrets:secret:set_owner_any');
  }

  // ── Credentials an approved agent still needs ───────────────────────

  private async countCredentials(): Promise<void> {
    const families = new Set<string>();
    for (const agent of this.agents) {
      for (const ref of Object.values(agent.resource_refs?.secrets || {})) {
        families.add(familyOf(String(ref)));
      }
    }
    for (const family of families) {
      try {
        this.secretCounts.set(family, (await this.secrets.list(family)).length);
      } catch {
        // A reader without the secrets grant simply sees no readiness
        // note — it is a hint, never a gate.
      }
    }
  }

  credentials(agent: AgentOffer): AgentCredential[] {
    const declared = agent.resources?.secrets || [];
    const granted: string[] = (agent as any).granted_secrets || [];
    return Object.entries(agent.resource_refs?.secrets || {}).map(([id, ref]) => {
      const family = familyOf(String(ref));
      return {
        id, family,
        label: declared.find((r) => r.id === id)?.label || id,
        saved: this.secretCounts.get(family) ?? 0,
        granted: granted.includes(id),
      };
    });
  }

  missingCredentials(agent: AgentOffer): AgentCredential[] {
    return this.credentials(agent).filter(
      (credential) => !credential.saved && !credential.granted);
  }

  // ── One agent, opened in place ──────────────────────────────────────

  /** Open an agent: its page is shown, and the address says so. */
  async openDetail(agent: AgentOffer): Promise<void> {
    void this.router?.navigate(['/agents', agent.agent_id]);
    await this.showAgent(agent);
    this.openTab = this.missingCredentials(agent).length ? 'credentials' : 'overview';
  }

  closeDetail(): void {
    void this.router?.navigate(['/agents']);
    this.showList();
  }

  /** One of the open agent's tabs; the Overview is the agent's own
   *  address. */
  openTabOf(tab: DetailTab): void {
    if (!this.openAgent) return;
    this.openTab = tab;
    void this.router?.navigate(tab === 'overview'
      ? ['/agents', this.openAgent.agent_id] : ['/agents', this.openAgent.agent_id, tab]);
  }

  private async showAgent(agent: AgentOffer): Promise<void> {
    this.openAgent = agent;
    this.error = '';
    this.notice = '';
    this.openCredentials = this.credentials(agent);
    if (!this.groups.length) {
      this.groups = await this.groupService.list().catch(() => []);
    }
  }

  private showList(): void {
    this.openAgent = null;
    this.openCredentials = [];
    this.error = '';
  }

  /** A credential was created, deleted, lent or taken back on the open
   *  agent: the counts the list reads, the slots a grant satisfies, and
   *  its readiness badge all change. */
  async onCredentialsChanged(): Promise<void> {
    await this.reload();
    await this.countCredentials();
    if (this.openAgent) this.openCredentials = this.credentials(this.openAgent);
  }

  // ── What new chats start with ───────────────────────────────────────
  //
  // The switch lives on the agent because that is what it is about. It
  // used to be a separate list with its own Save button, which asked
  // somebody to hold "which agents" and "did I save it" as two thoughts
  // about one decision.

  isDefault(agent: AgentOffer): boolean {
    return this.defaultAgents.has(agent.agent_id);
  }

  /** Flip it and persist. With no Save button there is nowhere to put a
   *  pending state, so the write happens now and the switch goes back if
   *  it fails — a switch that lies about what was saved is worse than
   *  one that moves twice. */
  async toggleDefault(agent: AgentOffer): Promise<void> {
    if (this.savingDefaultFor) return;
    const id = agent.agent_id;
    const wasOn = this.defaultAgents.has(id);

    if (wasOn) this.defaultAgents.delete(id);
    else this.defaultAgents.add(id);

    this.savingDefaultFor = id;
    try {
      const result = await this.profile.saveDefaultAgents(this.enabledIds);
      if (result.error) {
        if (wasOn) this.defaultAgents.add(id);
        else this.defaultAgents.delete(id);
        return this.fail(result.error);
      }
      this.flash(wasOn
        ? `${agent.name || id} will not be in new chats.`
        : `${agent.name || id} will be in new chats.`);
    } finally {
      this.savingDefaultFor = '';
    }
  }

  /** What gets written: the agents on this page currently switched on.
   *  Every row here is installed, an update waiting or not, so each can
   *  start a chat; an id that is no longer on the page is left out. */
  private get enabledIds(): string[] {
    return this.agents
      .filter((agent) => this.isDefault(agent))
      .map((agent) => agent.agent_id);
  }

  get defaultCount(): number {
    return this.enabledIds.length;
  }

  get attentionCount(): number {
    return this.agents.filter((agent) => this.needsAttention(agent)).length;
  }

  get updateCount(): number {
    return this.agents.filter((agent) => agent.status === 'update_available').length;
  }

  get readyCount(): number {
    return this.agents.filter((agent) => this.standing(agent).kind === 'ready').length;
  }

  /** The three views of the list, each saying how many it holds. The
   *  same array until a count changes: the control draws its choices
   *  again for a new one. */
  get filters(): Choice<'all' | 'attention' | 'updates'>[] {
    const counts = [this.agents.length, this.attentionCount, this.updateCount];
    if (counts.join() !== this.filterCounts.join()) {
      this.filterCounts = counts;
      this.filterChoices = [
        { value: 'all', name: `All ${counts[0]}` },
        { value: 'attention', name: `Needs attention ${counts[1]}` },
        { value: 'updates', name: `Updates ${counts[2]}` },
      ];
    }
    return this.filterChoices;
  }
  private filterCounts: number[] = [];
  private filterChoices: Choice<'all' | 'attention' | 'updates'>[] = [];

  /** The line under the grid: what the four tiles used to say. */
  get foot(): string {
    if (!this.agents.length) return '';
    return `${this.agents.length} installed · ${this.readyCount} ready · `
      + `${this.defaultCount} in new chats`;
  }

  /** Everything but Ready needs somebody: the counts and the filter are
   *  read from the same standing the cards show. */
  needsAttention(agent: AgentOffer): boolean {
    return this.standing(agent).kind !== 'ready';
  }

  version(agent: AgentOffer): string { return versionLabel(agent); }

  /** A card's line of facts: its functions, the credentials it
   *  declares, where it connects. */
  facts(agent: AgentOffer): string[] {
    return agentFacts(agent.functions.length, this.credentials(agent).length,
      agent.network as any);
  }

  /** Where an agent stands (agent-shared.ts), with the credentials it
   *  is still waiting for counted here. */
  standing(agent: AgentOffer): Standing {
    return standingOf(agent, this.missingCredentials(agent).length);
  }

  get shownAgents(): AgentOffer[] {
    const query = this.query.trim().toLowerCase();
    return this.agents.filter((agent) => {
      if (this.filter === 'attention' && !this.needsAttention(agent)) return false;
      if (this.filter === 'updates' && agent.status !== 'update_available') return false;
      return !query || `${agent.name || ''} ${agent.agent_id} ${agent.description || ''}`
        .toLowerCase().includes(query);
    });
  }

  get pagedAgents(): AgentOffer[] {
    const start = (this.page - 1) * this.pageSize;
    return this.shownAgents.slice(start, start + this.pageSize);
  }

  get pageCount(): number {
    return Math.max(1, Math.ceil(this.shownAgents.length / this.pageSize));
  }

  setFilter(filter: 'all' | 'attention' | 'updates'): void {
    this.filter = filter;
    this.page = 1;
  }

  search(value: string): void {
    this.query = value;
    this.page = 1;
  }

  // ── Lifecycle ───────────────────────────────────────────────────────

  /** The version waiting at the agent's source, opened where it is
   *  read: an update is a new approval, and approving is on the page
   *  that says what the update changes and nowhere else. Nothing is
   *  installed from here. */
  update(agent: AgentOffer): void {
    void this.router?.navigate(['/agents/marketplace'], {
      queryParams: { review: agent.agent_id },
    });
  }

  // ── Choosing several at once ────────────────────────────────────────

  /** Every row is an approved agent, so each can be ticked by whoever
   *  may uninstall. */
  selectable(_agent: AgentOffer): boolean {
    return this.canUninstall;
  }

  isSelected(agent: AgentOffer): boolean {
    return this.selected.has(agent.agent_id);
  }

  toggleSelected(agent: AgentOffer): void {
    if (this.selected.has(agent.agent_id)) this.selected.delete(agent.agent_id);
    else this.selected.add(agent.agent_id);
  }

  /** What "select all" means here: what is on screen under the current
   *  search and filter, never the rows they are hiding. */
  get selectableShown(): AgentOffer[] {
    return this.shownAgents.filter((agent) => this.selectable(agent));
  }

  get allShownSelected(): boolean {
    const shown = this.selectableShown;
    return shown.length > 0 && shown.every((agent) => this.isSelected(agent));
  }

  selectAllShown(): void {
    for (const agent of this.selectableShown) this.selected.add(agent.agent_id);
  }

  clearSelection(): void {
    this.selected.clear();
  }

  get selectedAgents(): AgentOffer[] {
    return this.agents.filter((agent) => this.isSelected(agent));
  }

  // ── Uninstalling: one question, whether it is one agent or twelve ───

  get uninstallBusy(): boolean {
    return !!this.busyId || this.bulkBusy;
  }

  requestUninstall(agent: AgentOffer): Promise<void> {
    return this.askUninstall([agent]);
  }

  requestUninstallSelected(): Promise<void> {
    return this.askUninstall(this.selectedAgents);
  }

  /** Asked once, by name, saying what goes and what stays. */
  private async askUninstall(going: AgentOffer[]): Promise<void> {
    if (!going.length || this.uninstallBusy) return;
    const one = going.length === 1;
    const names = going.map((agent) => agent.name || agent.agent_id).join(', ');
    const sure = await this.confirm.ask({
      title: one ? `Uninstall ${names}?` : `Uninstall ${going.length} agents?`,
      body: (one ? '' : `${names}. `)
        + `Chats stop being served by ${one ? 'it' : 'them'} on their next connection. `
        + `Credentials saved for ${one ? 'it' : 'them'} and the records ${one ? 'it' : 'they'} kept `
        + 'stay yours: uninstalling takes away the code, not your data.',
      confirm: one ? 'Uninstall' : `Uninstall ${going.length}`,
      danger: true,
    });
    if (sure) await this.uninstall(going);
  }

  /** Withdraw each in turn, and say what actually happened. One refusal
   *  neither stops the rest nor hides them: what went is gone, what did
   *  not is named and stays ticked. */
  private async uninstall(going: AgentOffer[]): Promise<void> {
    const gone: string[] = [];
    const refused: string[] = [];
    this.bulkBusy = true;
    try {
      for (const agent of going) {
        const name = agent.name || agent.agent_id;
        this.busyId = agent.agent_id;
        const result = await this.service.uninstall(agent.agent_id);
        if (result.error) {
          refused.push(`${name} (${result.error})`);
          continue;
        }
        gone.push(name);
        this.selected.delete(agent.agent_id);
      }
    } finally {
      this.busyId = '';
      this.bulkBusy = false;
    }

    const open = this.openAgent?.agent_id;
    if (open && gone.length && going.some((agent) => agent.agent_id === open)) {
      this.closeDetail();
    }
    await this.reload();

    if (refused.length) {
      this.fail(`Uninstalled ${gone.length}. Still installed — `
        + refused.join('; '));
    } else if (gone.length === 1) {
      this.flash(`${gone[0]} uninstalled.`);
    } else {
      this.flash(`${gone.length} agents uninstalled.`);
    }
  }
}
