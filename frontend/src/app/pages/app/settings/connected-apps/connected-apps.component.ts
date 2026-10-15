import { Component, OnInit, TemplateRef, ViewChild } from '@angular/core';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';

import { Choice } from 'src/app/components/ui/radio-keys';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import {
  DeclaredProvider, OauthApp, OauthEndpoints, SettingsOauthService,
} from 'src/app/services/settings-oauth.service';
import { DataPageBase } from '../../data-page-base';

/** The provider select's value for an id that is typed. */
const OTHER = 'other';

/**
 * Settings › Connected apps: what the organization registered with each
 * provider so its members can connect accounts with a click.
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
 * A SECRET IS THE PROVIDER'S TO ISSUE. An app registered for a server
 * is given one to keep. An app registered for a person's own computer
 * is given none by some providers, or one they do not treat as a
 * secret. The form cannot know which kind was registered, so where a
 * provider issues both kinds the secret is optional, and the form says
 * what to choose in the provider's console.
 */
@Component({
  selector: 'app-connected-apps',
  standalone: false,
  templateUrl: './connected-apps.component.html',
  styleUrls: ['./connected-apps.component.css'],
})
export class ConnectedAppsComponent extends DataPageBase implements OnInit {
  loading = true;
  /** Why the apps could not be read. */
  loadError = '';
  apps: OauthApp[] = [];
  redirectUri = '';
  copied = false;
  /** What installed agents' credentials name — offered first, because
   *  an id picked from a manifest cannot be misspelt. */
  declared: DeclaredProvider[] = [];
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
  /** The secret is shown as it is typed. */
  secretShown = false;
  /** Save was pressed with something missing: from then on the form
   *  says what, under the field it belongs to. */
  private tried = false;

  /** The form, which is shown on the dialog shell. */
  @ViewChild('editor')
  private editor?: TemplateRef<unknown>;
  private editorRef?: MatDialogRef<unknown>;

  /** What the backend accepts as an id — the same rule, so the form
   *  refuses before the round trip does. */
  static readonly ID_PATTERN = /^[a-z][a-z0-9_]{1,31}$/;

  busyId = '';

  /** The ids by convention, and where each provider's console is.
   *  A convention, not a fence: any id that fits the pattern works,
   *  and an agent may name one that is not here.
   *
   *  `secretOptional` marks a provider that also issues apps with no
   *  secret, and `steps` says what to choose in its console. A
   *  provider with neither hands every app an id and a secret. */
  readonly known: {
    id: string; label: string; console: string;
    secretOptional?: boolean; steps?: string;
  }[] = [
    { id: 'google', label: 'Google', console: 'https://console.cloud.google.com/apis/credentials',
      steps: 'Turn on the APIs your agents use, set up the consent screen (Internal for a company on '
        + 'Google Workspace: an External app in Testing makes people reconnect every seven days), then '
        + 'create an OAuth client of the type Web application with the redirect URL shown on this page. '
        + 'Google gives every app an id and a secret.' },
    { id: 'microsoft', label: 'Microsoft', console: 'https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade',
      secretOptional: true,
      steps: 'Register an application for accounts in any organizational directory (multitenant), then '
        + 'under Authentication add a platform with the redirect URL shown on this page. The platform Web '
        + 'has an id and a secret; copy the secret’s Value, and replace it here before it expires. For '
        + 'DecentAI on a computer of your own, choose Mobile and desktop applications instead: it has an '
        + 'Application (client) ID and no secret, so leave the secret empty.' },
    { id: 'dropbox', label: 'Dropbox', console: 'https://www.dropbox.com/developers/apps',
      secretOptional: true,
      steps: 'Create an app and add the redirect URL shown on this page. Its App key is the '
        + 'client id. Its secret may be left empty.' },
    { id: 'slack', label: 'Slack', console: 'https://api.slack.com/apps',
      steps: 'Create an app from scratch, add the redirect URL shown on this page under OAuth & '
        + 'Permissions (Slack needs https), and the permissions under User Token Scopes. The Client ID '
        + 'and Client Secret are under Basic Information.' },
    { id: 'github', label: 'GitHub', console: 'https://github.com/settings/developers' },
    { id: 'gitlab', label: 'GitLab', console: 'https://gitlab.com/-/user_settings/applications' },
    { id: 'atlassian', label: 'Atlassian (Jira, Confluence)', console: 'https://developer.atlassian.com/console/myapps/' },
    { id: 'salesforce', label: 'Salesforce', console: 'https://help.salesforce.com/s/articleView?id=sf.connected_app_create.htm' },
    { id: 'hubspot', label: 'HubSpot', console: 'https://developers.hubspot.com/' },
    { id: 'zoom', label: 'Zoom', console: 'https://marketplace.zoom.us/develop/create' },
    { id: 'notion', label: 'Notion', console: 'https://app.notion.com/developers/connections',
      steps: 'Create a public connection with the redirect URL shown on this page. Its OAuth client ID '
        + 'and OAuth client secret are on its Configuration tab.' },
    { id: 'box', label: 'Box', console: 'https://app.box.com/developers/console',
      steps: 'Create a Custom App with User Authentication (OAuth 2.0), add the redirect URL shown on '
        + 'this page, and tick Read and Write all files and folders under Application Scopes.' },
    { id: 'todoist', label: 'Todoist', console: 'https://app.todoist.com/app/settings/integrations/app-management',
      steps: 'Create a new app and set its OAuth redirect URL to the one shown on this page.' },
  ];

  constructor(
    private service: SettingsOauthService,
    public auth: AuthService,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {
    super();
  }

  ngOnInit(): Promise<void> {
    return this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      await this.reload();
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  private async reload(): Promise<void> {
    const page = await this.service.list();
    this.apps = page.apps;
    this.redirectUri = page.redirect_uri;
    this.declared = page.declared;
  }

  // ── What the form asks for ──────────────────────────────────────────

  /** What this page knows of the chosen provider, if anything. */
  private get chosen(): { secretOptional?: boolean; steps?: string } | undefined {
    return this.known.find((k) => k.id === this.draft.provider);
  }

  /** Whether the secret may be left empty: for a provider that issues
   *  apps without one, and for one this page knows nothing of. One it
   *  knows to hand every app a secret needs it: saying so here is
   *  kinder than a refused sign-in. */
  get secretOptional(): boolean {
    return this.chosen ? !!this.chosen.secretOptional : true;
  }

  /** What to choose in the provider's console. */
  get steps(): string {
    if (this.chosen?.steps) return this.chosen.steps;
    return this.secretOptional
      ? 'Register an app in the provider’s console with the redirect URL shown on this page, '
        + 'then paste what it gives you. Leave the secret empty if it gave none.'
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

  /** The counts under the list: "2 connected · 7 waiting". */
  get foot(): string {
    const parts: string[] = [];
    if (this.apps.length) parts.push(`${this.apps.length} connected`);
    if (this.missing.length) parts.push(`${this.missing.length} waiting`);
    return parts.join(' · ');
  }

  /** Known by convention, not declared by an agent, not registered. */
  get suggested(): { id: string; label: string }[] {
    return this.known.filter((k) =>
      !this.registered(k.id) && !this.declared.some((d) => d.provider === k.id));
  }

  /** The providers the form offers: those an installed agent is
   *  waiting on first, then the known ones, then the way to any other.
   *  Rebuilt only when what is registered or declared changes. */
  get providerChoices(): Choice[] {
    const key = `${this.apps.length}|${this.declared.length}`;
    if (key !== this.choicesFor) {
      this.choicesFor = key;
      this.choices = [
        ...this.missing.map((need) => ({
          value: need.provider,
          name: this.labelFor(need.provider),
          hint: `Needed by ${need.needed_by.join(', ')}`,
        })),
        ...this.suggested.map((known) => ({ value: known.id, name: known.label })),
        { value: OTHER, name: 'Another provider…', hint: 'By the id an agent\'s manifest names.' },
      ];
    }
    return this.choices;
  }
  private choicesFor = '';
  private choices: Choice[] = [];

  /** Whether the provider's id is being typed. */
  get typingProvider(): boolean {
    return this.providerChoice === OTHER;
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

  /** The address sets as the select's choices. */
  get addressChoices(): Choice<number>[] {
    return this.addressOptions.map((option, index) => ({ value: index, name: option.label }));
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
    this.draft.provider = choice === OTHER ? '' : choice;
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
      this.fail('Could not copy. Select the address and copy it yourself.');
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
    this.openEditor();
  }

  startEdit(app: OauthApp): void {
    this.editingId = app.resource_ref;
    this.draft = { provider: app.provider, client_id: app.client_id, client_secret: '' };
    this.addressChoice = 0;
    this.typedAddresses = { authorize_url: '', token_url: '', identity_url: '' };
    this.error = '';
    this.openEditor();
  }

  /** Shows the form on the dialog shell, which gives it Escape, the
   *  focus and the way back to what opened it. */
  private openEditor(): void {
    this.secretShown = false;
    this.tried = false;
    if (!this.editor || this.editorRef) return;
    this.editorRef = this.dialog.open(this.editor, { width: '560px' });
    this.editorRef.afterClosed().subscribe(() => {
      this.editorRef = undefined;
      this.editingId = null;
    });
  }

  closeEditor(): void {
    if (this.saving) return;
    this.editingId = null;
    this.editorRef?.close();
  }

  /** What is still missing, with the field it belongs to. */
  private get missingField(): { field: string; message: string } | null {
    const at = (field: string, message: string) => ({ field, message });
    if (!this.draft.provider.trim()) return at('provider', 'Choose a provider.');
    if (!this.providerValid) {
      return at('provider', 'Lowercase letters, digits and underscores, starting with a letter, as the manifest spells it.');
    }
    const where = this.chosenAddresses;
    if (!(where.authorize_url || '').startsWith('https://')) {
      return at('authorize', 'The consent address must begin with https://');
    }
    if (!(where.token_url || '').startsWith('https://')) {
      return at('token', 'The token address must begin with https://');
    }
    if (!this.draft.client_id.trim()) return at('client_id', 'Paste the client id.');
    if (this.isCreating && !this.secretOptional && !this.draft.client_secret.trim()) {
      return at('client_secret', 'Paste the client secret.');
    }
    return null;
  }

  /** What is missing from this field, once Save has been pressed. */
  errorFor(field: string): string {
    const missing = this.tried ? this.missingField : null;
    return missing?.field === field ? missing.message : '';
  }

  get canSubmit(): boolean {
    return !this.saving && !this.missingField;
  }

  private get secret(): string {
    return this.draft.client_secret;
  }

  async save(): Promise<void> {
    if (this.saving) return;
    if (this.missingField) {
      this.tried = true;
      return;
    }
    this.saving = true;
    if (this.editorRef) this.editorRef.disableClose = true;
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
      this.editorRef?.close();
      await this.reload();
      this.flash(made
        ? `${this.labelFor(result.app!.provider)} connected. People can now connect their accounts.`
        : `${this.labelFor(result.app!.provider)} updated.`);
    } finally {
      this.saving = false;
      if (this.editorRef) this.editorRef.disableClose = false;
    }
  }

  // ── Removing ────────────────────────────────────────────────────────

  async askRemove(app: OauthApp): Promise<void> {
    const name = this.labelFor(app.provider);
    const sure = await this.confirm.ask({
      title: `Remove the ${name} app?`,
      body: `Nobody can connect a new ${name} account, and connected ones stop refreshing once their tokens expire: each will ask to be reconnected.`,
      confirm: 'Remove',
      cancel: 'Keep it',
      danger: true,
    });
    if (sure) await this.remove(app);
  }

  async remove(app: OauthApp): Promise<void> {
    this.busyId = app.resource_ref;
    try {
      const result = await this.service.remove(app.resource_ref);
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(`${this.labelFor(app.provider)} removed. Accounts already connected keep working until their tokens expire.`);
    } finally {
      this.busyId = '';
    }
  }
}
