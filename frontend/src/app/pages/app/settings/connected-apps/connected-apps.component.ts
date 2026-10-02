import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import {
  DeclaredProvider, OauthApp, OauthEndpoints, SettingsOauthService,
} from 'src/app/services/settings-oauth.service';
import { DataPageBase } from '../../data-page-base';

/**
 * Connected apps: what the organization registered with each provider
 * so its members can connect accounts with a click. A tab of Settings.
 *
 * OAuth involves two secrets with two owners. The app registration —
 * a client id and secret issued to this software — is the
 * organization's, pasted once by an administrator here. The grant a
 * person gives when they click Allow is theirs, and lives with their
 * credentials. Keeping the two apart is the whole point of this page:
 * nobody pastes the organization's client secret into a credential.
 *
 * The provider id is the join key: an agent whose credential says
 * `provider: google` connects through the `google` row here.
 *
 * ON A DESKTOP the app is registered for a person's own computer, and
 * a provider gives such an app no secret to keep, or one it does not
 * treat as a secret. So the form asks for the id alone wherever that
 * is all the provider hands out, and says what to choose in the
 * provider's console, which is not what a server would choose.
 */
@Component({
  selector: 'app-connected-apps',
  standalone: false,
  templateUrl: './connected-apps.component.html',
  styleUrls: ['../../data-shared.css', '../../../admin/iam-shared.css', './connected-apps.component.css'],
})
export class ConnectedAppsComponent extends DataPageBase implements OnInit {
  loading = true;
  apps: OauthApp[] = [];
  redirectUri = '';
  copied = false;
  /** What installed agents' credentials name — offered first, because
   *  an id picked from a manifest cannot be misspelt. */
  declared: DeclaredProvider[] = [];
  /** Whether this deployment keeps a secret for every app: a web one
   *  does, a desktop one takes an app that has none. */
  secretRequired = true;

  /** null = closed, '' = adding, id = editing. */
  editingId: string | null = null;
  draft = { provider: '', client_id: '', client_secret: '' };
  /** The select's value: a provider id, or 'other' for the text box. */
  providerChoice = '';
  /** Which of addressOptions the registration settles on. */
  addressChoice = 0;
  /** The provider's addresses, typed — for one no installed agent names. */
  typedAddresses: OauthEndpoints = { authorize_url: '', token_url: '', identity_url: '' };
  saving = false;

  /** What the backend accepts as an id — the same rule, so the form
   *  refuses before the round trip does. */
  static readonly ID_PATTERN = /^[a-z][a-z0-9_]{1,31}$/;

  deleteTarget: OauthApp | null = null;
  busyId = '';

  /** The ids by convention, and where each provider's console is.
   *  A convention, not a fence: any id that fits the pattern works,
   *  and an agent may name one that is not here.
   *
   *  `desktop` is what the provider hands an app registered for a
   *  person's own computer: `id` when the id is all of it, and the
   *  words that say what to choose in its console. A provider without
   *  it hands out an id and a secret there as it does anywhere. */
  readonly known: {
    id: string; label: string; console: string;
    desktop?: { gives: 'id' | 'id-and-secret'; steps: string };
  }[] = [
    { id: 'google', label: 'Google', console: 'https://console.cloud.google.com/apis/credentials',
      desktop: { gives: 'id-and-secret',
        steps: 'Create an OAuth client ID of the type Web application, and add the redirect URL shown '
          + 'on this page to its authorized redirect URIs. Google gives every app an id and a secret.' } },
    { id: 'microsoft', label: 'Microsoft', console: 'https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade',
      desktop: { gives: 'id',
        steps: 'Register an application, then under Authentication add the platform Mobile and desktop '
          + 'applications with the redirect URL shown on this page. It has an Application (client) ID '
          + 'and no secret.' } },
    { id: 'dropbox', label: 'Dropbox', console: 'https://www.dropbox.com/developers/apps',
      desktop: { gives: 'id',
        steps: 'Create an app and add the redirect URL shown on this page. Its App key is the '
          + 'client id, and its secret is not needed.' } },
    { id: 'slack', label: 'Slack', console: 'https://api.slack.com/apps' },
    { id: 'github', label: 'GitHub', console: 'https://github.com/settings/developers' },
    { id: 'gitlab', label: 'GitLab', console: 'https://gitlab.com/-/user_settings/applications' },
    { id: 'atlassian', label: 'Atlassian (Jira, Confluence)', console: 'https://developer.atlassian.com/console/myapps/' },
    { id: 'salesforce', label: 'Salesforce', console: 'https://help.salesforce.com/s/articleView?id=sf.connected_app_create.htm' },
    { id: 'hubspot', label: 'HubSpot', console: 'https://developers.hubspot.com/' },
    { id: 'zoom', label: 'Zoom', console: 'https://marketplace.zoom.us/develop/create' },
    { id: 'notion', label: 'Notion', console: 'https://www.notion.so/my-integrations' },
    { id: 'box', label: 'Box', console: 'https://app.box.com/developers/console' },
  ];

  constructor(private service: SettingsOauthService, public auth: AuthService) {
    super();
  }

  async ngOnInit(): Promise<void> {
    await this.reload();
    this.loading = false;
  }

  private async reload(): Promise<void> {
    const page = await this.service.list();
    this.apps = page.apps;
    this.redirectUri = page.redirect_uri;
    this.declared = page.declared;
    this.secretRequired = page.secret_required;
  }

  // ── What the form asks for ──────────────────────────────────────────

  /** What the chosen provider hands a desktop app, when that is known. */
  private get desktop(): { gives: 'id' | 'id-and-secret'; steps: string } | undefined {
    if (this.secretRequired) return undefined;
    return this.known.find((k) => k.id === this.draft.provider)?.desktop;
  }

  /** Whether the form shows the secret at all. Hidden only where the
   *  id is known to be the whole of what the provider hands out. */
  get showsSecret(): boolean {
    return this.desktop?.gives !== 'id';
  }

  /** Whether the secret may be left empty: on a desktop, for a
   *  provider this page knows nothing of. One it knows either hands
   *  out the id alone, and the field is not shown, or issues a secret
   *  and needs it: saying so here is kinder than a refused sign-in. */
  get secretOptional(): boolean {
    if (this.secretRequired) return false;
    return !this.known.some((k) => k.id === this.draft.provider);
  }

  /** What to choose in the provider's console, on a desktop. */
  get steps(): string {
    if (this.secretRequired) return '';
    if (this.desktop) return this.desktop.steps;
    return this.secretOptional
      ? 'Register an app with the provider for this computer, with the redirect URL shown on '
        + 'this page, then paste what it gives you. Leave the secret empty if it gave none.'
      : 'Register an app in the provider’s console with the redirect URL shown on this page, '
        + 'then paste the id and the secret it gives you.';
  }

  // ── Which ids to offer ──────────────────────────────────────────────

  /** Registered by this organization. */
  private registered(id: string): boolean {
    return this.apps.some((app) => app.provider === id);
  }

  /** Declared by an installed agent and not yet registered — the ones
   *  a Connect button is waiting on. */
  get missing(): DeclaredProvider[] {
    return this.declared.filter((d) => !this.registered(d.provider));
  }

  /** Known by convention, not declared by an agent, not registered. */
  get suggested(): { id: string; label: string }[] {
    return this.known.filter((k) =>
      !this.registered(k.id) && !this.declared.some((d) => d.provider === k.id));
  }

  // ── Where the provider is ───────────────────────────────────────────

  /** The address sets on offer for the draft: what it is registered
   *  with, then what each installed agent names. */
  get addressOptions(): { label: string; endpoints: OauthEndpoints }[] {
    const options: { label: string; endpoints: OauthEndpoints }[] = [];
    const add = (label: string, e: OauthEndpoints) => {
      if (!options.some((o) => this.sameAddresses(o.endpoints, e))) {
        options.push({ label, endpoints: { authorize_url: e.authorize_url || '',
          token_url: e.token_url || '', identity_url: e.identity_url || '' } });
      }
    };
    // A registration saved without its addresses offers none: the
    // agents' own, or the typed ones, are what it is saved again with.
    const registered = this.apps.find((a) => a.resource_ref === this.editingId)?.endpoints;
    if (registered?.authorize_url && registered?.token_url) add('As registered', registered);
    const named = this.declared.find((d) => d.provider === this.draft.provider.trim().toLowerCase());
    for (const set of named?.endpoints ?? []) add(`As ${set.named_by.join(', ')} name it`, set);
    return options;
  }

  private sameAddresses(a: OauthEndpoints, b: OauthEndpoints): boolean {
    return a.authorize_url === b.authorize_url && a.token_url === b.token_url
      && a.identity_url === b.identity_url;
  }

  /** The chosen set, or the typed one where nothing is on offer. */
  get chosenAddresses(): OauthEndpoints {
    return this.addressOptions[this.addressChoice]?.endpoints ?? this.typedAddresses;
  }

  hostOf(url: string): string {
    try { return url ? new URL(url).host : ''; } catch { return url; }
  }

  neededBy(id: string): string {
    return this.declared.find((d) => d.provider === id)?.needed_by.join(', ') || '';
  }

  chooseProvider(choice: string): void {
    this.providerChoice = choice;
    this.draft.provider = choice === 'other' ? '' : choice;
    this.addressChoice = 0;
  }

  get providerValid(): boolean {
    return ConnectedAppsComponent.ID_PATTERN.test(this.draft.provider.trim().toLowerCase());
  }

  get canCreate(): boolean { return this.auth.can('settings:oauth:create'); }
  get canUpdate(): boolean { return this.auth.can('settings:oauth:update'); }
  get canDelete(): boolean { return this.auth.can('settings:oauth:delete'); }

  get isCreating(): boolean { return this.editingId === ''; }

  labelFor(provider: string): string {
    return this.known.find((k) => k.id === provider)?.label || provider;
  }

  consoleFor(provider: string): string {
    return this.known.find((k) => k.id === provider)?.console || '';
  }

  async copyRedirect(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.redirectUri);
      this.copied = true;
      setTimeout(() => (this.copied = false), 1800);
    } catch {
      this.fail('Could not copy — select the address and copy it yourself.');
    }
  }

  // ── Editor ──────────────────────────────────────────────────────────

  startCreate(provider = ''): void {
    this.editingId = '';
    this.draft = { provider, client_id: '', client_secret: '' };
    this.addressChoice = 0;
    this.typedAddresses = { authorize_url: '', token_url: '', identity_url: '' };
    this.providerChoice = provider;
    this.error = '';
  }

  startEdit(app: OauthApp): void {
    this.editingId = app.resource_ref;
    this.draft = { provider: app.provider, client_id: app.client_id, client_secret: '' };
    this.addressChoice = 0;
    this.typedAddresses = { authorize_url: '', token_url: '', identity_url: '' };
    this.error = '';
  }

  closeEditor(): void {
    if (!this.saving) this.editingId = null;
  }

  get canSubmit(): boolean {
    if (this.saving) return false;
    const { client_id, client_secret } = this.draft;
    if (!this.providerValid || !client_id.trim()) return false;
    const where = this.chosenAddresses;
    if (!(where.authorize_url || '').startsWith('https://')
        || !(where.token_url || '').startsWith('https://')) {
      return false;
    }
    if (!this.isCreating || !this.showsSecret || this.secretOptional) return true;
    return !!client_secret.trim();
  }

  /** What is sent: nothing, for a field the form did not show. */
  private get secret(): string {
    return this.showsSecret ? this.draft.client_secret : '';
  }

  async save(): Promise<void> {
    if (!this.canSubmit) return;
    this.saving = true;
    try {
      const result = this.isCreating
        ? await this.service.create({
            provider: this.draft.provider.trim().toLowerCase(),
            client_id: this.draft.client_id.trim(),
            client_secret: this.secret,
            endpoints: this.chosenAddresses,
          })
        : await this.service.update(this.editingId!, {
            client_id: this.draft.client_id.trim(),
            client_secret: this.secret,
            endpoints: this.chosenAddresses,
          });
      if (result.error) return this.fail(result.error);
      const made = this.isCreating;
      this.editingId = null;
      await this.reload();
      this.flash(made
        ? `${this.labelFor(result.app!.provider)} registered. Members can now connect their accounts.`
        : `${this.labelFor(result.app!.provider)} updated.`);
    } finally {
      this.saving = false;
    }
  }

  // ── Removing ────────────────────────────────────────────────────────

  requestDelete(app: OauthApp): void {
    this.deleteTarget = app;
    this.error = '';
  }

  closeDelete(): void {
    if (!this.busyId) this.deleteTarget = null;
  }

  async remove(app: OauthApp): Promise<void> {
    this.busyId = app.resource_ref;
    try {
      const result = await this.service.remove(app.resource_ref);
      if (result.error) return this.fail(result.error);
      this.deleteTarget = null;
      await this.reload();
      this.flash(`${this.labelFor(app.provider)} removed. Accounts already connected keep working until their tokens expire.`);
    } finally {
      this.busyId = '';
    }
  }
}
