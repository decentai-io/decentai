import { Component, OnDestroy, OnInit } from '@angular/core';
import { Subscription } from 'rxjs';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from 'src/app/services/auth.service';
import { RoutingSettings, SettingsRoutingService } from 'src/app/services/settings-routing.service';
import { SettingsSpeechService } from 'src/app/services/settings-speech.service';
import { SwPush } from '@angular/service-worker';
import { NotificationSettings, NotificationsService } from 'src/app/services/notifications.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import {
  LlmConnection, LlmConnectionDraft, LlmModel, LlmModelKind, LlmProvider,
  SettingsLlmService,
} from 'src/app/services/settings-llm.service';
import { DataPageBase } from '../data-page-base';

/**
 * Settings: what the platform itself is configured with, and what a
 * person has configured of it. Tabbed: the organization's LLM
 * connections, the person's chat defaults, their memory and API keys,
 * the organization's connected apps and safety, and the audit trail.
 *
 * Built for an organization that keeps a hundred of them: a searchable
 * list of rows rather than a form per credential, with one connection
 * marked DEFAULT — the answer for every chat whose person never chose.
 * A person's preference and a chat's own config may each pick another.
 */
type ShareMode = 'private' | 'groups' | 'users' | 'org';
type SettingsTab = 'llm' | 'chat' | 'memory' | 'keys' | 'apps' | 'safety' | 'audit';

@Component({
  selector: 'app-settings',
  standalone: false,
  templateUrl: './settings.component.html',
  styleUrls: [
    '../data-shared.css',
    './settings.component.css',
  ],
})
export class SettingsComponent extends DataPageBase implements OnInit, OnDestroy {
  private subscriptions = new Subscription();

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  loading = true;
  tab: SettingsTab = 'llm';

  connections: LlmConnection[] = [];
  profile: Profile | null = null;
  peers: { user_id: string; user_name: string; email: string }[] = [];
  query = '';

  /** null = closed, '' = adding, id = editing. */
  editingId: string | null = null;
  draft: LlmConnectionDraft = this.blankDraft();
  shareMode: ShareMode = 'private';
  selectedGroups = new Set<string>();
  selectedUsers = new Set<string>();
  saving = false;

  deleteTarget: LlmConnection | null = null;
  busyId = '';

  /** The platform's catalog of providers, as the backend serves it:
   *  the page keeps no list of its own to fall out of step with it. */
  providers: LlmProvider[] = [];

  /** The models the chosen provider is known to serve — the model
   *  field's suggestions. Empty for a provider the catalog lists none for. */
  models: LlmModel[] = [];

  /** The chosen provider's address while it still has parts that are
   *  the customer's own (`<aws-region>`), and what was typed for each.
   *  The form asks for those parts by name instead of having the person
   *  edit an address. */
  endpointTemplate = '';
  blankValues: Record<string, string> = {};

  /** What a blank is usually filled with, so the common case is no step. */
  private readonly blankDefaults: Record<string, string> = { 'aws-region': 'us-east-1' };

  readonly awsRegions = [
    'us-east-1', 'us-east-2', 'us-west-2', 'ca-central-1', 'sa-east-1',
    'eu-west-1', 'eu-west-2', 'eu-west-3', 'eu-central-1', 'eu-north-1',
    'ap-south-1', 'ap-northeast-1', 'ap-northeast-2', 'ap-southeast-1',
    'ap-southeast-2', 'me-central-1',
  ];

  /** The name follows the provider and model until the person types one. */
  private nameIsAuto = true;
  showMore = false;

  selectProvider(provider: string): void {
    const endpoint = this.draft.endpoint.trim();
    const preset = this.providers.find(p => p.id === provider)?.endpoint ?? '';
    // Replace a preset, but preserve a gateway URL the user entered.
    const wasPreset = !!this.endpointTemplate || this.providers.some(p =>
      p.endpoint && p.endpoint.replace(/\/$/, '') === endpoint.replace(/\/$/, ''));
    this.draft.provider = provider;
    if (!endpoint || wasPreset) {
      this.endpointTemplate = /<[^>]+>/.test(preset) ? preset : '';
      this.blankValues = {};
      for (const blank of this.blanks) this.blankValues[blank] = this.blankDefaults[blank] ?? '';
      this.draft.endpoint = this.filled(preset);
    }
    this.models = [];
    this.nameAfterChoice();
    void this.loadModels(provider);
  }

  private async loadModels(provider: string): Promise<void> {
    // Only the models the connection's purpose has a use for.
    const models = await this.service.models(
      provider, (this.draft.purpose || 'chat') as LlmModelKind);
    // A slow answer for a provider since changed is nobody's list.
    if (this.draft.provider === provider) this.models = models;
  }

  /** The customer's own parts of the provider's address, by name. */
  get blanks(): string[] {
    return [...this.endpointTemplate.matchAll(/<([^>]+)>/g)].map(found => found[1]);
  }

  blankLabel(blank: string): string {
    const words = blank.replace(/-/g, ' ');
    return words.startsWith('aws ') ? 'AWS ' + words.slice(4)
      : words.charAt(0).toUpperCase() + words.slice(1);
  }

  setBlank(blank: string, value: string): void {
    this.blankValues[blank] = value;
    this.draft.endpoint = this.filled(this.endpointTemplate);
  }

  /** The address with every part that was typed put in; one still
   *  missing stays marked, and the blocker says so. */
  private filled(template: string): string {
    return template.replace(/<([^>]+)>/g,
      (mark, blank) => (this.blankValues[blank] ?? '').trim() || mark);
  }

  setModel(model: string): void {
    this.draft.model = model;
    this.nameAfterChoice();
  }

  setName(name: string): void {
    this.draft.name = name;
    this.nameIsAuto = false;
  }

  private nameAfterChoice(): void {
    if (!this.nameIsAuto) return;
    const provider = this.providers.find(p => p.id === this.draft.provider)?.name ?? '';
    const model = this.models.find(m => m.id === this.draft.model)?.name
      ?? this.draft.model.trim();
    this.draft.name = [provider, model].filter(Boolean).join(' · ');
  }

  /** Whether the address is the person's to type in full: the custom
   *  entry has none of its own. Otherwise it is filled in and sits
   *  under "More options", for whoever points it at a gateway. */
  get endpointIsYours(): boolean {
    const chosen = this.providers.find(p => p.id === this.draft.provider);
    return !!chosen && !chosen.endpoint;
  }

  get endpointPlaceholder(): string {
    return this.providers.find(p => p.id === this.draft.provider)?.endpoint
      || 'https://your-server.example/v1';
  }

  get endpointHelp(): string {
    if (this.draft.provider === 'openai_compatible') {
      return 'Enter your OpenAI-compatible API base URL, including its version path.';
    }
    // An address that differs per customer arrives with its blanks marked.
    const preset = this.providers.find(p => p.id === this.draft.provider)?.endpoint ?? '';
    return /[<>]/.test(preset)
      ? 'Replace each <...> in the endpoint with your own value — your region, account or resource name.'
      : 'The provider endpoint is filled in for you. You can change it to use your own gateway.';
  }

  constructor(
    private service: SettingsLlmService,
    private profiles: ProfileService,
    public auth: AuthService,
    private routingService: SettingsRoutingService,
    private speechService: SettingsSpeechService,
    private notifications: NotificationsService,
    private swPush: SwPush,
    private route: ActivatedRoute,
    private router: Router,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    this.tab = this.initialTab();
    this.loading = true;
    const [connections, providers, profile, peers] = await Promise.all([
      this.canSeeLlm ? this.service.list() : Promise.resolve([]),
      this.canSeeLlm ? this.service.providers() : Promise.resolve([]),
      this.canSeeLlm || this.canSeeChat ? this.profiles.get() : Promise.resolve(null),
      this.canSeeLlm ? this.profiles.peers() : Promise.resolve([]),
    ]);
    this.connections = connections;
    this.providers = providers;
    this.profile = profile;
    this.peers = peers;
    this.loading = false;
    this.readChatDefaults();
    await Promise.all([this.loadRouting(), this.loadSpeech(), this.loadNotifications()]);
  }

  // ── Notifications: being reached when a chat needs you ─────────────

  notificationSettings: NotificationSettings | null = null;
  /** This browser's own subscription, when it has one. */
  thisDevice: PushSubscription | null = null;
  notificationsBusy = false;
  notificationsNote = '';

  get canSeeNotifications(): boolean { return this.auth.can('settings:notifications:get'); }

  /** Push needs the service worker, which runs only in a built app,
   *  and a browser that offers the API. */
  get pushSupported(): boolean {
    return this.swPush.isEnabled && typeof Notification !== 'undefined';
  }

  /** Why this window cannot turn push on, said in the person's words:
   *  the reason decides what they do next, so it is never one sentence
   *  for every case. */
  get pushUnsupportedReason(): string {
    if (typeof Notification === 'undefined') {
      if (this.isIos) {
        return this.standalone
          ? 'This iPhone or iPad cannot show web notifications yet: update it to iOS 16.4 or later.'
          : 'On iPhone and iPad, notifications reach only a copy of DecentAI added to the Home Screen. '
            + 'In Safari, tap Share, then "Add to Home Screen", open DecentAI from that icon, and press Turn on here there.';
      }
      return 'This browser does not offer web notifications. Chrome, Edge, Firefox and Brave do.';
    }
    if (!this.swPush.isEnabled) {
      return 'This window cannot run the background worker that receives push (a private window, or a development build). Open DecentAI in a normal window.';
    }
    return '';
  }

  /** Brave ships with its push service switched off, so the person is told
   *  before the click, not by the browser's error after it. */
  get braveHint(): string {
    if (!this.isBrave) return '';
    return 'Brave turns off its push service by default: in Brave settings, under Privacy and security, '
      + 'turn on "Use Google services for push messaging", restart Brave, then press Turn on here.';
  }

  private get isIos(): boolean {
    return /iPhone|iPad|iPod/.test(navigator.userAgent)
      || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }

  private get standalone(): boolean {
    return (window.matchMedia?.('(display-mode: standalone)').matches ?? false)
      || (navigator as any).standalone === true;
  }

  private get isBrave(): boolean {
    return 'brave' in navigator;
  }

  get pushDenied(): boolean {
    return typeof Notification !== 'undefined' && Notification.permission === 'denied';
  }

  private async loadNotifications(): Promise<void> {
    if (!this.canSeeNotifications) return;
    try {
      this.notificationSettings = await this.notifications.get();
    } catch {
      this.notificationSettings = null;
    }
    if (this.pushSupported) {
      this.subscriptions.add(this.swPush.subscription.subscribe((sub) => { this.thisDevice = sub; }));
    }
  }

  /** Ask this browser for permission and keep its subscription here. */
  async enablePushHere(): Promise<void> {
    if (!this.notificationSettings?.push.configured || this.notificationsBusy) return;
    this.notificationsBusy = true;
    this.notificationsNote = '';
    try {
      const subscription = await this.swPush.requestSubscription({
        serverPublicKey: this.notificationSettings.push.public_key,
      });
      const result = await this.notifications.subscribe(subscription.toJSON());
      if (result.error) return this.fail(result.error);
      this.notificationSettings = result;
      this.flash('This device will be notified.');
    } catch (error: any) {
      const message = String(error?.message || error || '');
      if (this.pushDenied) {
        this.notificationsNote = 'Notifications are blocked for this site in the browser; allow them in the site settings and try again.';
      } else if (error?.name === 'AbortError' || /push service/i.test(message)) {
        // The browser has no push service to register with: Brave's is
        // off until the person turns it on; elsewhere it is rare.
        this.notificationsNote = this.braveHint
          || 'This browser has no push service to register with. In Brave, turn on "Use Google services for push messaging" under Privacy and security and restart it; other browsers need nothing.';
      } else {
        this.notificationsNote = `Could not subscribe this device: ${message}`;
      }
    } finally {
      this.notificationsBusy = false;
    }
  }

  async disablePushHere(): Promise<void> {
    if (!this.thisDevice || this.notificationsBusy) return;
    this.notificationsBusy = true;
    try {
      const endpoint = this.thisDevice.endpoint;
      try { await this.swPush.unsubscribe(); } catch { /* the browser side may already be gone */ }
      const result = await this.notifications.unsubscribe(endpoint);
      if (result.error) return this.fail(result.error);
      this.notificationSettings = result;
      this.flash('This device will no longer be notified.');
    } finally {
      this.notificationsBusy = false;
    }
  }

  async setEmailNotifications(enabled: boolean): Promise<void> {
    const result = await this.notifications.update(enabled);
    if (result.error) return this.fail(result.error);
    this.notificationSettings = result;
    this.flash(enabled ? 'Email will follow when no device can be reached.' : 'No emails will be sent.');
  }

  async testNotification(): Promise<void> {
    if (this.notificationsBusy) return;
    this.notificationsBusy = true;
    try {
      const result = await this.notifications.test();
      if (result.error) return this.fail(result.error);
      const outcome = result.outcome || {};
      this.notificationsNote = outcome.pushed
        ? `Sent to ${outcome.pushed} device${outcome.pushed === 1 ? '' : 's'}.`
        : outcome.emailed ? 'No device could be reached; an email was sent.'
          : 'Nothing could be reached: no device is subscribed and email is off or not configured.';
    } finally {
      this.notificationsBusy = false;
    }
  }

  // ── Speech to text ──────────────────────────────────────────────────

  get canSeeSpeech(): boolean { return this.auth.can('settings:speech:get'); }
  get canEditSpeech(): boolean { return this.auth.can('settings:speech:update'); }

  /** '' = no transcription model: the composer offers no microphone. */
  speechConnectionId = '';
  private speechSaved = '';
  speechSaving = false;

  get transcriptionConnections(): LlmConnection[] {
    return this.connections.filter((c) => c.keys.purpose === 'transcription');
  }

  get speechDirty(): boolean { return this.speechConnectionId !== this.speechSaved; }

  private async loadSpeech(): Promise<void> {
    if (!this.canSeeSpeech) return;
    try {
      const answer = await this.speechService.get();
      this.speechConnectionId = answer.speech?.transcription_connection_id || '';
      this.speechSaved = this.speechConnectionId;
    } catch {
      this.speechSaved = this.speechConnectionId;
    }
  }

  async saveSpeech(): Promise<void> {
    if (this.speechSaving) return;
    this.speechSaving = true;
    try {
      const result = await this.speechService.update(this.speechConnectionId);
      if (result.error) return this.fail(result.error);
      this.speechSaved = this.speechConnectionId;
      this.flash('Speech to text saved.');
    } finally {
      this.speechSaving = false;
    }
  }

  // ── Chat defaults: what a new chat of this person starts with ──────

  get canSeeChat(): boolean { return this.auth.can('ai:chat:create'); }

  chatDefaults = { llm_secret_ref: '', trust_level: 1, max_turns: 20, max_skills: 40 };
  private chatDefaultsSaved = '';
  chatDefaultsSaving = false;

  /** A function runs without asking when its level is at or below the
   *  chat's trust level; each hint says what that leaves to ask about. */
  readonly trustOptions = [
    { level: 0, label: 'Ask for every change', hint: 'Reads run; every change asks first' },
    { level: 1, label: 'Standard', hint: 'Reads and ordinary changes run; wider changes and outside actions ask first' },
    { level: 2, label: 'Trusted', hint: 'Everything inside the platform runs; outside actions ask first' },
    { level: 3, label: 'Autonomous', hint: 'Everything runs without asking' },
  ];
  readonly turnBudgetOptions = [
    { turns: 20, label: 'Standard', hint: '20 steps' },
    { turns: 40, label: 'Long', hint: '40 steps' },
    { turns: 60, label: 'Very long', hint: '60 steps' },
    { turns: 100, label: 'Longest', hint: '100 steps' },
    { turns: 0, label: 'Unlimited', hint: 'Runs until done, or until you stop it' },
  ];
  readonly skillsCapOptions = [
    { rows: 40, label: 'Standard', hint: '40 skills listed' },
    { rows: 80, label: 'More', hint: '80 skills listed' },
    { rows: 160, label: 'Many', hint: '160 skills listed' },
    { rows: 0, label: 'All', hint: 'Every skill you can see' },
  ];

  /** The chat models this person may pick as their default — never an
   *  embedding or a transcription one. */
  get chatConnections(): LlmConnection[] {
    return this.connections.filter((c) => (c.keys.purpose || 'chat') === 'chat');
  }

  get chatDefaultsDirty(): boolean {
    return JSON.stringify(this.chatDefaults) !== this.chatDefaultsSaved;
  }

  private readChatDefaults(): void {
    const chat = this.profile?.preferences?.chat ?? {};
    this.chatDefaults = {
      llm_secret_ref: chat.llm_secret_ref || '',
      trust_level: Number.isInteger(chat.trust_level) ? (chat.trust_level as number) : 1,
      max_turns: Number.isInteger(chat.max_turns) ? (chat.max_turns as number) : 20,
      max_skills: Number.isInteger(chat.max_skills) ? (chat.max_skills as number) : 40,
    };
    this.chatDefaultsSaved = JSON.stringify(this.chatDefaults);
  }

  async saveChatDefaults(): Promise<void> {
    if (this.chatDefaultsSaving) return;
    this.chatDefaultsSaving = true;
    try {
      const result = await this.profiles.saveChatDefaults({
        llm_secret_ref: this.chatDefaults.llm_secret_ref || null,
        trust_level: Number(this.chatDefaults.trust_level),
        max_turns: Number(this.chatDefaults.max_turns),
        max_skills: Number(this.chatDefaults.max_skills),
      });
      if (result.error) return this.fail(result.error);
      // The answer carries the preferences and not the groups, which
      // the sharing choices are drawn from: those stay as they were read.
      if (result.profile) {
        this.profile = { ...result.profile, groups: this.profile?.groups ?? [] };
      }
      this.readChatDefaults();
      this.flash('Chat defaults saved.');
    } finally {
      this.chatDefaultsSaving = false;
    }
  }

  // ── Agent routing ───────────────────────────────────────────────────

  /** The organization's numbers, as saved; null until read or when
   *  this person may not see them. */
  routing: RoutingSettings | null = null;
  routingDraft: RoutingSettings | null = null;
  routingSaving = false;

  get canSeeRouting(): boolean { return this.auth.can('settings:routing:get'); }
  get canEditRouting(): boolean { return this.auth.can('settings:routing:update'); }

  /** The connections made for embeddings — what routing can choose. */
  get embeddingConnections(): LlmConnection[] {
    return this.connections.filter((c) => (c.keys.purpose || 'chat') === 'embedding');
  }

  get routingDirty(): boolean {
    return JSON.stringify(this.routing) !== JSON.stringify(this.routingDraft);
  }

  private async loadRouting(): Promise<void> {
    if (!this.canSeeRouting) return;
    try {
      const answer = await this.routingService.get();
      this.routing = answer.routing;
      this.routingDraft = { ...answer.routing };
    } catch {
      this.routing = null;
    }
  }

  async saveRouting(): Promise<void> {
    if (!this.routingDraft || this.routingSaving) return;
    this.routingSaving = true;
    try {
      const result = await this.routingService.update(this.routingDraft);
      if (result.error) return this.fail(result.error);
      this.routing = result.routing ?? this.routing;
      this.routingDraft = this.routing ? { ...this.routing } : null;
      this.flash('Agent routing saved.');
    } finally {
      this.routingSaving = false;
    }
  }

  private async reload(): Promise<void> {
    this.connections = await this.service.list();
  }

  get myGroups(): { group_id: string; group_name: string }[] {
    return (this.profile?.groups ?? []).filter(
      (group) => group.group_id !== 'everyone',
    );
  }

  // ── Tabs ────────────────────────────────────────────────────────────

  get canSeeLlm(): boolean { return this.auth.can('settings:llm:list'); }
  get canSeeMemory(): boolean { return this.auth.can('settings:memory:list'); }
  get canSeeKeys(): boolean { return this.auth.can('settings:apikey:list'); }
  get canSeeApps(): boolean { return this.auth.can('settings:oauth:list'); }
  get canSeeSafety(): boolean { return this.auth.can('settings:safety:get'); }
  get canSeeAudit(): boolean { return this.auth.can('ai:audit:list'); }
  get auditView(): 'mine' | 'org' {
    return this.route.snapshot.data['auditView'] === 'org' ? 'org' : 'mine';
  }

  /** The route names the tab (/settings/memory); otherwise the first
   *  tab this person may see. */
  private initialTab(): SettingsTab {
    const named = this.route.snapshot.data['tab'] as SettingsTab | undefined;
    if (named === 'chat' && this.canSeeChat) return 'chat';
    if (named === 'memory' && this.canSeeMemory) return 'memory';
    if (named === 'keys' && this.canSeeKeys) return 'keys';
    if (named === 'apps' && this.canSeeApps) return 'apps';
    if (named === 'safety' && this.canSeeSafety) return 'safety';
    if (named === 'audit' && this.canSeeAudit) return 'audit';
    if (this.canSeeLlm) return 'llm';
    if (this.canSeeMemory) return 'memory';
    if (this.canSeeKeys) return 'keys';
    if (this.canSeeApps) return 'apps';
    return this.canSeeAudit ? 'audit' : 'llm';
  }

  selectTab(tab: SettingsTab): void {
    if (this.tab === tab) return;
    this.tab = tab;
    this.router.navigate([
      tab === 'chat' ? '/settings/chat'
        : tab === 'memory' ? '/settings/memory'
        : tab === 'keys' ? '/settings/keys'
        : tab === 'apps' ? '/settings/apps'
        : tab === 'safety' ? '/settings/safety'
        : tab === 'audit' ? '/settings/audit'
        : '/settings',
    ]);
  }

  get canCreate(): boolean { return this.auth.can('settings:llm:create'); }
  get canUpdate(): boolean { return this.auth.can('settings:llm:update'); }
  get canDelete(): boolean { return this.auth.can('settings:llm:delete'); }
  get canSetDefault(): boolean {
    return this.auth.can('settings:llm:setdefault');
  }
  get canTransfer(): boolean { return this.auth.can('settings:llm:transfer'); }

  // ── The list ────────────────────────────────────────────────────────

  get visible(): LlmConnection[] {
    const query = this.query.trim().toLowerCase();
    if (!query) return this.connections;
    return this.connections.filter((connection) =>
      [connection.name, connection.keys.provider, connection.keys.model,
       connection.keys.endpoint].join(' ').toLowerCase().includes(query));
  }

  /** Creator-only, like the secret layer: being shared a connection is
   *  permission to use it, never authority over it — unless this person
   *  holds the manage-any grant, the administrator's escape. */
  isMine(connection: LlmConnection): boolean {
    return connection.created_by === (this.profile?.user_id ?? '')
      || this.auth.can('settings:llm:manage_any');
  }

  summary(connection: LlmConnection): string {
    const keys = connection.keys;
    return [keys.provider, keys.model, keys.endpoint]
      .filter(Boolean).join(' · ');
  }

  /** A connection that is not for thinking: embedding or transcription. */
  isEmbedding(connection: LlmConnection): boolean {
    return (connection.keys.purpose || 'chat') !== 'chat';
  }

  purposeLabel(connection: LlmConnection): string {
    switch (connection.keys.purpose) {
      case 'embedding': return 'Embedding model — for finding the right agent, not for chats.';
      case 'transcription': return 'Transcription model — writes spoken messages down, not for chats.';
      default: return '';
    }
  }

  // ── The editor dialog ───────────────────────────────────────────────

  private blankDraft(): LlmConnectionDraft {
    return { name: '', provider: '', model: '', endpoint: '', reasoning_effort: '', purpose: 'chat', api_key: '' };
  }

  get isCreating(): boolean { return this.editingId === ''; }

  private resetEditor(creating: boolean): void {
    this.models = [];
    this.endpointTemplate = '';
    this.blankValues = {};
    this.nameIsAuto = creating;
    this.showMore = false;
  }

  startCreate(): void {
    this.editingId = '';
    this.draft = this.blankDraft();
    this.resetEditor(true);
    this.shareMode = 'private';
    this.selectedGroups.clear();
    this.selectedUsers.clear();
    this.error = '';
  }

  startEdit(connection: LlmConnection): void {
    this.editingId = connection.resource_ref;
    this.draft = {
      name: connection.name,
      provider: connection.keys.provider,
      model: connection.keys.model,
      endpoint: connection.keys.endpoint,
      reasoning_effort: connection.keys.reasoning_effort || '',
      purpose: connection.keys.purpose || 'chat',
      api_key: '', // write-only: blank means keep
    };
    // A saved connection keeps its name and its address as they are.
    this.resetEditor(false);
    void this.loadModels(this.draft.provider);
    const groups = connection.owner?.groups || [];
    // The creator sits in users on every connection; anyone BEYOND them
    // is a deliberate person-share.
    const others = (connection.owner?.users || [])
      .filter((u) => u !== connection.created_by);
    if (groups.includes('everyone')) this.shareMode = 'org';
    else if (groups.length) this.shareMode = 'groups';
    else if (others.length) this.shareMode = 'users';
    else this.shareMode = 'private';
    this.selectedGroups = new Set(groups.filter((g) => g !== 'everyone'));
    this.selectedUsers = new Set(others);
    this.error = '';
  }

  toggleUser(userId: string): void {
    if (this.selectedUsers.has(userId)) this.selectedUsers.delete(userId);
    else this.selectedUsers.add(userId);
  }

  toggleGroup(groupId: string): void {
    if (this.selectedGroups.has(groupId)) this.selectedGroups.delete(groupId);
    else this.selectedGroups.add(groupId);
  }

  private buildOwner(): { groups: string[]; users: string[] } {
    if (this.shareMode === 'org') return { groups: ['everyone'], users: [] };
    if (this.shareMode === 'groups') {
      return { groups: Array.from(this.selectedGroups), users: [] };
    }
    if (this.shareMode === 'users') {
      return { groups: [], users: Array.from(this.selectedUsers) };
    }
    return { groups: [], users: [] }; // creator is kept server-side
  }

  /** How a row says who can use it. */
  shareLabel(connection: LlmConnection): string {
    const groups = connection.owner?.groups || [];
    if (groups.includes('everyone')) return 'Organization-wide';
    if (groups.length) {
      return `${groups.length} group${groups.length === 1 ? '' : 's'}`;
    }
    const others = (connection.owner?.users || [])
      .filter((u) => u !== connection.created_by);
    if (others.length) {
      return `${others.length} ${others.length === 1 ? 'person' : 'people'}`;
    }
    return 'Private';
  }

  closeEditor(): void {
    if (!this.saving) this.editingId = null;
  }

  /** What is still missing, said before the request rather than after.
   *  The key is only demanded for a NEW connection — on an existing one,
   *  blank keeps the stored key. */
  get blocker(): string {
    if (!this.draft.name.trim()) return 'Give it a name.';
    if (!this.draft.provider) return 'Choose a provider.';
    if (!this.draft.model.trim()) {
      return 'Name the model — the provider’s own name, copied exactly.';
    }
    if (!this.draft.endpoint.trim()) return 'Endpoint is required.';
    const missing = this.blanks.find(blank => !(this.blankValues[blank] ?? '').trim());
    if (missing) return `Enter your ${this.blankLabel(missing)}.`;
    if (/[<>]/.test(this.draft.endpoint)) {
      return 'Fill in the endpoint: replace each <...> with your own account’s value.';
    }
    if (this.isCreating && !this.draft.api_key.trim()) {
      return 'The API key is required.';
    }
    if (this.shareMode === 'groups' && !this.selectedGroups.size) {
      return 'Pick at least one group, or share it another way.';
    }
    if (this.shareMode === 'users' && !this.selectedUsers.size) {
      return 'Pick at least one person, or share it another way.';
    }
    return '';
  }

  async save(): Promise<void> {
    if (this.blocker || this.saving) return;
    this.saving = true;
    try {
      const draft: LlmConnectionDraft = {
        name: this.draft.name.trim(),
        provider: this.draft.provider,
        model: this.draft.model.trim(),
        endpoint: this.draft.endpoint.trim(),
        reasoning_effort: this.draft.purpose === 'embedding' ? '' : (this.draft.reasoning_effort || ''),
        purpose: this.draft.purpose || 'chat',
        api_key: this.draft.api_key,
        owner: this.buildOwner(),
      };
      const result = this.isCreating
        ? await this.service.create(draft)
        : await this.service.update(this.editingId!, draft);
      if (result.error) return this.fail(result.error);

      this.editingId = null;
      await this.reload();
      this.flash(`"${result.connection?.name}" saved.`);
    } finally {
      this.saving = false;
    }
  }

  // ── Default ─────────────────────────────────────────────────────────

  async makeDefault(connection: LlmConnection): Promise<void> {
    if (connection.is_default || this.busyId) return;
    this.busyId = connection.resource_ref;
    try {
      const result = await this.service.setDefault(connection.resource_ref);
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(`"${connection.name}" is now the default.`);
    } finally {
      this.busyId = '';
    }
  }

  // ── Removing ────────────────────────────────────────────────────────

  requestDelete(connection: LlmConnection): void {
    this.deleteTarget = connection;
    this.error = '';
  }

  closeDelete(): void {
    if (!this.busyId) this.deleteTarget = null;
  }

  async remove(connection: LlmConnection): Promise<void> {
    this.busyId = connection.resource_ref;
    try {
      const result = await this.service.remove(connection.resource_ref);
      if (result.error) return this.fail(result.error);
      this.deleteTarget = null;
      await this.reload();
      this.flash(`"${connection.name}" removed.`);
    } finally {
      this.busyId = '';
    }
  }


  // ── Handing over ────────────────────────────────────────────────────

  transferTarget: LlmConnection | null = null;
  transferring = false;

  requestTransfer(item: LlmConnection): void {
    this.transferTarget = item;
    this.error = '';
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
