import {
  Component, EventEmitter, Input, OnChanges, Output, SimpleChanges,
} from '@angular/core';
import { Clipboard } from '@angular/cdk/clipboard';
import { Router } from '@angular/router';

import { relativeDate } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';

import { AgentOffer, AgentsService, SampleSummary } from 'src/app/services/agents.service';
import { AuthService } from 'src/app/services/auth.service';
import { Group } from 'src/app/services/group.service';
import {
  AgentCredential, AgentNetwork, Confinement, PERMISSION_LEVELS, Standing, confinementOf,
  credentialHost, levelLabel, networkNote, standingOf, versionLabel,
} from './agent-shared';

export type DetailTab = 'overview' | 'credentials' | 'functions' | 'access';

/**
 * One agent's page.
 *
 * Everything you do TO an agent happens here: fill its credentials, read
 * its functions, decide who may use it. This component is the title row,
 * the row of sections and the Overview; each other section is its own
 * component below. The Overview answers, in order: what it is, what to
 * say to it, what approving it agreed to, and then one fact a card.
 */
@Component({
  selector: 'app-agent-detail',
  standalone: false,
  templateUrl: './agent-detail.component.html',
  styleUrls: ['./agent-shared.css', './agent-detail.component.css'],
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

  @Output() update = new EventEmitter<AgentOffer>();
  @Output() uninstall = new EventEmitter<AgentOffer>();
  @Output() credentialsChanged = new EventEmitter<void>();
  @Output() failed = new EventEmitter<string>();
  @Output() flashed = new EventEmitter<string>();

  /** Which tab is shown: the address's to say (the Installed page
   *  reads it), and asked for here with `tabChange`. */
  @Input() tab: DetailTab = 'overview';
  @Output() tabChange = new EventEmitter<DetailTab>();

  /** The sections, each saying how many it holds. Built when what they
   *  count changes, since the control draws its choices again for a
   *  new array. */
  tabs: Choice<DetailTab>[] = [];

  private nameTabs(): void {
    const tabs: Choice<DetailTab>[] = [{ value: 'overview', name: 'Overview' }];
    if (this.credentials.length) {
      tabs.push({
        value: 'credentials',
        name: this.missing.length ? `Credentials · ${this.missing.length} missing` : 'Credentials',
      });
    }
    tabs.push({ value: 'functions', name: `Functions ${this.agent.functions.length}` });
    if (this.showAccess) tabs.push({ value: 'access', name: 'Access' });
    if (tabs.map((tab) => tab.name).join() !== this.tabs.map((tab) => tab.name).join()) {
      this.tabs = tabs;
    }
  }

  ngOnChanges(changes: SimpleChanges): void {
    this.nameTabs();
    // Only when the AGENT changes. Every input arrives here, `busyId`
    // included, and resetting on that would throw somebody off the
    // Access tab the moment they saved a grant. The same agent read
    // again — after an update, or as it becomes ready — is not another
    // agent either.
    const change = changes['agent'];
    if (!change) return;
    const before = change.previousValue?.agent_id;
    if (before && before === change.currentValue?.agent_id) return;
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
    private clipboard: Clipboard,
  ) {}

  /** What the sample data is about, as one sentence. */
  get samplesStory(): string {
    const story = String(this.samples?.story || '').trim().replace(/\.+$/, '');
    return story ? `${story}.` : '';
  }

  /** When this person loaded the sample data, as a list says a date. */
  get samplesLoadedWhen(): string {
    return relativeDate(this.samplesLoaded?.loaded_at).toLowerCase();
  }

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
    return PERMISSION_LEVELS
      .filter((name) => counts.has(name))
      .map((name) => ({ name, count: counts.get(name)! }));
  }

  /** What the agent keeps, records and files together. Credentials
   *  have a section of their own. */
  get kept(): Array<{ id: string; kind: string; label?: string; description?: string }> {
    const resources: any = this.agent.resources || {};
    return [
      ...(resources.data || []).map((resource: any) => ({ ...resource, kind: 'records' })),
      ...(resources.files || []).map((resource: any) => ({ ...resource, kind: 'files' })),
    ];
  }

  get keptCount(): number {
    return this.kept.length;
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

  /** What this install holds the agent to, as the runtime that
   *  prepared it said: three lines, each held or not. */
  get confinement(): Confinement[] {
    return confinementOf(this.agent);
  }

  /** Where the agent stands, in the one word the list uses too. */
  get standing(): Standing {
    return standingOf(this.agent, this.missing.length);
  }

  /** The installed version, said once beside the name. */
  get version(): string {
    return versionLabel(this.agent);
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

  /** The repository the code came from, where it is one a browser can
   *  open. */
  get repository(): string {
    const url = String(this.agent.source?.url || '').replace(/\.git$/, '');
    return /^https:\/\//.test(url) ? url : '';
  }

  /** The whole digest, copied: the short one on the page names it, and
   *  a comparison needs every character. */
  copied = false;
  copyDigest(): void {
    this.clipboard.copy(String(this.agent.package_digest || ''));
    this.copied = true;
    setTimeout(() => (this.copied = false), 1500);
  }

  /** The code itself, short enough to read. The repository and commit say
   *  where it came from and can both change; this cannot. */
  get packageLabel(): string {
    const digest = String(this.agent.package_digest || '');
    return digest ? digest.replace('sha256:', '').slice(0, 12) : '';
  }
}
