import { Component, OnDestroy, OnInit } from '@angular/core';
import { Subscription } from 'rxjs';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from 'src/app/services/auth.service';
import { ModelChoice } from 'src/app/components/model-select/model-select.component';
import { RoutingSettings, SettingsRoutingService } from 'src/app/services/settings-routing.service';
import { SettingsSpeechService, SpeechSettings } from 'src/app/services/settings-speech.service';
import { SwPush } from '@angular/service-worker';
import { NotificationSettings, NotificationsService } from 'src/app/services/notifications.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import {
  LlmConnection, LlmProvider, SettingsLlmService,
} from 'src/app/services/settings-llm.service';
import { DataPageBase } from '../data-page-base';

type SettingsTab = 'llm' | 'chat' | 'memory' | 'keys' | 'apps' | 'safety' | 'audit';

/**
 * Settings: what the platform itself is configured with, and what a
 * person has configured of it. Tabbed: the organization's model
 * providers, the person's chat defaults, their memory and API keys,
 * the organization's connected apps and safety, and the audit trail.
 *
 * Each tab but the chat defaults is a component of its own; this one
 * reads what several of them share — the connections and the catalog
 * of providers — and holds the chat tab: what a new chat starts with,
 * the models that write speech down and that find the right agent.
 */
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

  /** The platform's catalog of providers, as the backend serves it:
   *  the page keeps no list of its own to fall out of step with it. */
  providers: LlmProvider[] = [];

  /** The connections shared with the whole organization: what speech
   *  and routing may choose, since every member's chat will use them. */
  get sharedConnections(): LlmConnection[] {
    return this.connections.filter(
      (connection) => (connection.owner?.groups || []).includes('everyone'));
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

  /** No connection = no transcription model: the composer offers no
   *  microphone. */
  speech: SpeechSettings = { transcription_connection_id: '', transcription_model: '' };
  private speechSaved = '';
  speechSaving = false;

  get speechDirty(): boolean { return JSON.stringify(this.speech) !== this.speechSaved; }

  /** A provider alone does not say which of its models writes speech down. */
  get speechBlocker(): string {
    return this.speech.transcription_connection_id && !this.speech.transcription_model
      ? 'Choose the transcription model.' : '';
  }

  chooseSpeech(choice: ModelChoice): void {
    this.speech = {
      transcription_connection_id: choice.connectionId,
      transcription_model: choice.model,
    };
  }

  private async loadSpeech(): Promise<void> {
    if (!this.canSeeSpeech) return;
    try {
      const answer = await this.speechService.get();
      this.speech = {
        transcription_connection_id: answer.speech?.transcription_connection_id || '',
        transcription_model: answer.speech?.transcription_model || '',
      };
    } catch {
      // Left as it is: nothing chosen.
    }
    this.speechSaved = JSON.stringify(this.speech);
  }

  async saveSpeech(): Promise<void> {
    if (this.speechSaving || this.speechBlocker) return;
    this.speechSaving = true;
    try {
      const result = await this.speechService.update(this.speech);
      if (result.error) return this.fail(result.error);
      this.speechSaved = JSON.stringify(this.speech);
      this.flash('Speech to text saved.');
    } finally {
      this.speechSaving = false;
    }
  }

  // ── Chat defaults: what a new chat of this person starts with ──────

  get canSeeChat(): boolean { return this.auth.can('ai:chat:create'); }

  chatDefaults = { llm_secret_ref: '', llm_model: '', trust_level: 1, max_turns: 20, max_skills: 40 };
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

  /** The model a new chat starts with: a provider and one of its
   *  models, or neither for the organization's default. */
  chooseDefaultModel(choice: ModelChoice): void {
    this.chatDefaults.llm_secret_ref = choice.connectionId;
    this.chatDefaults.llm_model = choice.model;
  }

  get chatDefaultsDirty(): boolean {
    return JSON.stringify(this.chatDefaults) !== this.chatDefaultsSaved;
  }

  private readChatDefaults(): void {
    const chat = this.profile?.preferences?.chat ?? {};
    this.chatDefaults = {
      llm_secret_ref: chat.llm_secret_ref || '',
      llm_model: chat.llm_secret_ref ? chat.llm_model || '' : '',
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
        llm_model: this.chatDefaults.llm_secret_ref ? this.chatDefaults.llm_model : '',
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

  chooseEmbedding(choice: ModelChoice): void {
    if (!this.routingDraft) return;
    this.routingDraft.embedding_connection_id = choice.connectionId;
    this.routingDraft.embedding_model = choice.model;
  }

  /** A provider alone does not say which of its models embeds. */
  get routingBlocker(): string {
    return this.routingDraft?.embedding_connection_id && !this.routingDraft.embedding_model
      ? 'Choose the embedding model.' : '';
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
    if (!this.routingDraft || this.routingSaving || this.routingBlocker) return;
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

  /** Read the connections again: the providers tab added, changed or
   *  removed one, and the chat tab chooses among them. */
  async reload(): Promise<void> {
    this.connections = await this.service.list();
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
}
