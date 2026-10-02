import {
  Component, EventEmitter, Input, OnChanges, Output, SimpleChanges,
} from '@angular/core';
import { Router } from '@angular/router';

import { AgentOffer, AgentsService, SampleSummary } from 'src/app/services/agents.service';
import { AuthService } from 'src/app/services/auth.service';
import { Group } from 'src/app/services/group.service';
import {
  AgentCredential, AgentNetwork, credentialHost, levelLabel, installedLabel, networkNote,
  notEnforced,
} from './agent-shared';

export type DetailTab = 'overview' | 'credentials' | 'functions' | 'access';

/**
 * One agent, managed in place.
 *
 * Everything you do TO an agent happens here: fill its credentials, read
 * its functions, decide who may use it. Nothing sends you to another
 * page — this component is the header and the tab bar, and each tab is
 * its own component below it.
 */
@Component({
  selector: 'app-agent-detail',
  standalone: false,
  templateUrl: './agent-detail.component.html',
  styleUrls: [
    '../data-shared.css',
    '../../admin/iam-shared.css',
    './agent-shared.css',
    './agents.component.css',
  ],
})
export class AgentDetailComponent implements OnChanges {
  @Input() agent!: AgentOffer;
  @Input() credentials: AgentCredential[] = [];
  @Input() groups: Group[] = [];
  @Input() myGroups: { group_id: string; group_name: string }[] = [];
  @Input() canShareAny = false;
  @Input() myUserId = '';
  @Input() secretDefaults: Record<string, string> = {};
  @Input() canInstall = false;
  @Input() canUninstall = false;
  @Input() busyId = '';

  @Output() back = new EventEmitter<void>();
  @Output() update = new EventEmitter<AgentOffer>();
  @Output() uninstall = new EventEmitter<AgentOffer>();
  @Output() credentialsChanged = new EventEmitter<void>();
  @Output() failed = new EventEmitter<string>();
  @Output() flashed = new EventEmitter<string>();

  tab: DetailTab = 'overview';

  levelLabel = levelLabel;

  ngOnChanges(changes: SimpleChanges): void {
    // Only when the AGENT changes. Every input arrives here, `busyId`
    // included, and resetting on that would throw somebody off the
    // Access tab the moment they saved a grant. The same agent read
    // again — after an update, or as it becomes ready — is not another
    // agent either.
    const change = changes['agent'];
    if (!change) return;
    const before = change.previousValue?.agent_id;
    if (before && before === change.currentValue?.agent_id) return;
    // Land where the work is: an agent still waiting for a credential
    // opens on the tab that fixes it.
    this.tab = this.missing.length ? 'credentials' : 'overview';
    void this.readSamples();
  }

  // ── Sample data ─────────────────────────────────────────────────────

  /** What the agent ships to try it on, and whether this person loaded
   *  it. Read from the approved package, so it is what would load. */
  samples: SampleSummary | null = null;
  samplesLoaded: { records: number; files: number; loaded_at: string } | null = null;
  samplesBusy = false;

  private async readSamples(): Promise<void> {
    this.samples = null;
    this.samplesLoaded = null;
    if (!this.agent || !this.agent.samples) return;
    try {
      const state = await this.agents.samples(this.agent.agent_id);
      this.samples = state.samples;
      this.samplesLoaded = state.loaded;
    } catch {
      this.samples = this.agent.samples ?? null;
    }
  }

  async loadSamples(): Promise<void> {
    if (this.samplesBusy || !this.samples) return;
    this.samplesBusy = true;
    try {
      const result = await this.agents.loadSamples(this.agent.agent_id);
      if (result.error) return this.failed.emit(result.error);
      await this.readSamples();
      const loaded = result.data?.loaded || {};
      this.flashed.emit(`Sample data loaded: ${loaded.records || 0} records`
        + (loaded.files ? ` and ${loaded.files} file${loaded.files === 1 ? '' : 's'}` : '')
        + `. They are yours now — find them under Saved data and Files.`);
    } finally {
      this.samplesBusy = false;
    }
  }

  async removeSamples(): Promise<void> {
    if (this.samplesBusy || !this.samplesLoaded) return;
    this.samplesBusy = true;
    try {
      const result = await this.agents.removeSamples(this.agent.agent_id);
      if (result.error) return this.failed.emit(result.error);
      await this.readSamples();
      this.flashed.emit('The sample data you loaded is removed.');
    } finally {
      this.samplesBusy = false;
    }
  }

  /** Slots with neither a credential of their own nor one lent. */
  get missing(): AgentCredential[] {
    return this.credentials.filter(
      (credential) => !credential.saved && !credential.granted);
  }

  constructor(
    private router: Router,
    private agents: AgentsService,
    private auth: AuthService,
  ) {}

  /** Open the chats page with this prompt written in the composer, for
   *  the person to send: the shortest path from
   *  reading what an agent does to seeing it do it. */
  tryPrompt(prompt: string): void {
    this.router.navigate(['/ai/chats'], { queryParams: { prompt } });
  }

  /** Who may use an agent is its own grant to read, whatever else is
   *  true of the agent: an update waiting does not hide it. */
  get showAccess(): boolean {
    return this.auth.can('agents:agent:grants');
  }

  // ── Overview ────────────────────────────────────────────────────────

  /** What was approved, counted by the level that matters: how many of
   *  these run without asking, and how many reach outside. */
  get levelSummary(): Array<{ name: string; count: number }> {
    const counts = new Map<string, number>();
    for (const fn of this.agent.functions || []) {
      if (fn.permission_level === null || fn.permission_level === undefined) continue;
      const name = levelLabel(fn.permission_level);
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    return [...counts.entries()].map(([name, count]) => ({ name, count }));
  }

  /** What the agent KEEPS. Credentials have a tab of their own. */
  get keeps(): Array<{ kind: string; items: any[] }> {
    const resources: any = this.agent.resources || {};
    return ['data', 'files']
      .map((kind) => ({ kind, items: resources[kind] || [] }))
      .filter((group) => group.items.length);
  }

  get resourceTotal(): number {
    const resources: any = this.agent.resources || {};
    return ['secrets', 'data', 'files']
      .reduce((total, kind) => total + (resources[kind] || []).length, 0);
  }

  /** Whether the row says where the agent connects. The plain list a
   *  reader gets does not. */
  get networkKnown(): boolean {
    return !!this.agent.network;
  }

  /** Where it connects, as the platform read its manifest. */
  get network(): AgentNetwork {
    return this.agent.network
      || { declared: false, any: false, hosts: [], from_secrets: [] };
  }

  get networkNote(): string {
    return networkNote(this.network);
  }

  readonly credentialHost = credentialHost;

  /** What this install does not hold the agent to, as the runtime
   *  that prepared it said. */
  get notEnforced(): string[] {
    return notEnforced(this.agent);
  }

  /** A runtime has said what it holds the agent to, and it is all of it. */
  get fullyConfined(): boolean {
    return !!this.agent.prepared?.confined && !this.notEnforced.length;
  }

  statusLabel(agent: AgentOffer): string {
    switch (agent.status) {
      case 'installed': return installedLabel(agent);
      case 'update_available':
        return `v${agent.installed_version} installed · `
          + `v${agent.loaded_version} available`;
      case 'removed':
        return `v${agent.installed_version} installed · no longer in the source's catalog`;
      default: return installedLabel(agent);
    }
  }

  /** Where the code came from, short enough to sit in a header. */
  get origin(): string {
    const source = this.agent.source;
    if (!source?.url) return 'a repository';
    const sha = String(source.sha || '').slice(0, 7);
    const repository = String(source.url || '')
      .replace(/\.git$/, '').split('/').slice(-2).join('/');
    return sha ? `${repository} @ ${sha}` : repository || 'a repository';
  }

  /** The code itself, short enough to read. The repository and commit say
   *  where it came from and can both change; this cannot. */
  get packageLabel(): string {
    const digest = String(this.agent.package_digest || '');
    return digest ? digest.replace('sha256:', '').slice(0, 12) : '';
  }
}
