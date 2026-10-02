import { Component, OnDestroy, OnInit } from '@angular/core';

import { AgentOffer, AgentsService } from 'src/app/services/agents.service';
import { Group, GroupService } from 'src/app/services/group.service';
import { ProfileService } from 'src/app/services/profile.service';
import { SecretsService } from 'src/app/services/secrets.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataPageBase } from '../data-page-base';
import { AgentCredential, familyOf, installedLabel } from './agent-shared';

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
 */
@Component({
  selector: 'app-agents',
  standalone: false,
  templateUrl: './agents.component.html',
  styleUrls: [
    '../data-shared.css',
    '../../admin/iam-shared.css',
    './agent-shared.css',
    './agents.component.css',
  ],
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

  /** Which agents a NEW chat starts with. No preference at all means
   *  every installed one. */
  defaultAgents = new Set<string>();
  /** The agent whose switch is mid-write, so only that one is frozen. */
  savingDefaultFor = '';
  /** The agents a confirmation is open for: one opened from its own
   *  page, or everything ticked on the list. One dialog, one path. */
  uninstalling: AgentOffer[] = [];
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
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    await this.reload();
    this.loading = false;

    const profile = await this.profile.get();
    this.myUserId = profile?.user_id ?? '';
    this.myGroups = (profile?.groups ?? []).filter(
      (group: any) => group.group_id !== 'everyone');
    this.secretDefaults = profile?.preferences?.secrets?.defaults ?? {};
    const configured = profile?.preferences?.chat?.enabled_agents;
    this.defaultAgents = new Set(Array.isArray(configured)
      ? configured : this.agents.map((agent) => agent.agent_id));
    await this.countCredentials();
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
    this.stopWatching();
  }

  private async fetchAgents(): Promise<void> {
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

  async openDetail(agent: AgentOffer): Promise<void> {
    this.openAgent = agent;
    this.error = '';
    this.notice = '';
    this.openCredentials = this.credentials(agent);
    if (!this.groups.length) {
      this.groups = await this.groupService.list().catch(() => []);
    }
  }

  closeDetail(): void {
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
    return this.agents.filter((agent) => agent.status === 'installed'
      && !this.missingCredentials(agent).length).length;
  }

  needsAttention(agent: AgentOffer): boolean {
    return agent.status !== 'installed' || this.missingCredentials(agent).length > 0;
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

  statusLabel(agent: AgentOffer): string {
    switch (agent.status) {
      case 'installed': return installedLabel(agent);
      case 'update_available': {
        // The source may have moved without bumping this agent's
        // version — the update is a new commit, not a new number.
        const waiting = agent.loaded_version;
        return `v${agent.installed_version} installed · `
          + (waiting && waiting !== agent.installed_version
            ? `v${waiting} available`
            : 'newer code at the source');
      }
      case 'removed':
        return `v${agent.installed_version} installed · no longer in the source's catalog`;
      default: return installedLabel(agent);
    }
  }

  /** Take the version waiting at the agent's source. */
  async update(agent: AgentOffer): Promise<void> {
    this.busyId = agent.agent_id;
    try {
      const result = await this.service.install(agent.agent_id);
      if (result.error) return this.fail(result.error);
      await this.reload();
      await this.countCredentials();
      if (this.openAgent) this.openCredentials = this.credentials(this.openAgent);
      this.flash(`${agent.name || agent.agent_id} updated.`);
    } finally {
      this.busyId = '';
    }
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

  // ── Uninstalling: one dialog, whether it is one agent or twelve ─────

  get uninstallBusy(): boolean {
    return !!this.busyId || this.bulkBusy;
  }

  get uninstallingNames(): string {
    return this.uninstalling
      .map((agent) => agent.name || agent.agent_id).join(', ');
  }

  requestUninstall(agent: AgentOffer): void {
    this.uninstalling = [agent];
    this.error = '';
  }

  requestUninstallSelected(): void {
    this.uninstalling = this.selectedAgents;
    this.error = '';
  }

  closeUninstall(): void {
    if (!this.uninstallBusy) this.uninstalling = [];
  }

  /** Withdraw each in turn, and say what actually happened. One refusal
   *  neither stops the rest nor hides them: what went is gone, what did
   *  not is named and stays ticked. */
  async confirmUninstall(): Promise<void> {
    const going = this.uninstalling;
    if (!going.length) return;

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

    this.uninstalling = [];
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
