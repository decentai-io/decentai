import { Component, EventEmitter, Input, Output } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { Profile } from 'src/app/services/profile.service';
import {
  LlmCheck, LlmConnection, LlmConnectionDraft, LlmModel, LlmProvider,
  SettingsLlmService,
} from 'src/app/services/settings-llm.service';
import { DataPageBase } from '../../data-page-base';

type ShareMode = 'private' | 'groups' | 'users' | 'org';

/**
 * Model providers: the providers this organization has a key for. A tab
 * of the Settings page.
 *
 * A connection is a provider and its key, pasted once; which of the
 * provider's models a chat thinks with is chosen in the chat. So adding
 * one is two steps and little typing: pick the provider — the few most
 * people look for first, the rest behind a search — then paste the key.
 * The address is filled in, the model it starts with is chosen for you,
 * and the provider is asked whether the key works before anything is
 * saved, so a wrong key is heard about here and not in a chat.
 *
 * One connection is the DEFAULT — the answer for every chat whose
 * person never chose.
 */
@Component({
  selector: 'app-model-providers',
  standalone: false,
  templateUrl: './model-providers.component.html',
  styleUrls: [
    '../../data-shared.css',
    '../settings.component.css',
    './model-providers.component.css',
  ],
})
export class ModelProvidersComponent extends DataPageBase {
  /** The connections this person can see, and the catalog of
   *  providers — both read by the page, which other tabs share them with. */
  @Input() connections: LlmConnection[] = [];
  @Input() providers: LlmProvider[] = [];
  @Input() profile: Profile | null = null;
  @Input() peers: { user_id: string; user_name: string; email: string }[] = [];
  /** A connection was added, changed or removed: the page reads them again. */
  @Output() changed = new EventEmitter<void>();

  query = '';
  busyId = '';
  deleteTarget: LlmConnection | null = null;

  constructor(private service: SettingsLlmService, public auth: AuthService) {
    super();
  }

  get canCreate(): boolean { return this.auth.can('settings:llm:create'); }
  get canUpdate(): boolean { return this.auth.can('settings:llm:update'); }
  get canDelete(): boolean { return this.auth.can('settings:llm:delete'); }
  get canSetDefault(): boolean { return this.auth.can('settings:llm:setdefault'); }
  get canTransfer(): boolean { return this.auth.can('settings:llm:transfer'); }

  // ── The list ────────────────────────────────────────────────────────

  get visible(): LlmConnection[] {
    const query = this.query.trim().toLowerCase();
    if (!query) return this.connections;
    return this.connections.filter((connection) =>
      [connection.name, this.providerName(connection.keys.provider),
       connection.keys.endpoint].join(' ').toLowerCase().includes(query));
  }

  /** Creator-only, like the secret layer: being shared a connection is
   *  permission to use it, never authority over it — unless this person
   *  holds the manage-any grant, the administrator's escape. */
  isMine(connection: LlmConnection): boolean {
    return connection.created_by === (this.profile?.user_id ?? '')
      || this.auth.can('settings:llm:manage_any');
  }

  providerName(id: string): string {
    return this.providers.find((provider) => provider.id === id)?.name ?? id;
  }

  /** The line under a row's name: the provider, where that is not the
   *  name already, and the address where it is the person's own. */
  summary(connection: LlmConnection): string {
    const provider = this.providerName(connection.keys.provider);
    if (connection.keys.provider === ModelProvidersComponent.AZURE) {
      // Azure is somebody's own endpoint and a deployment they named.
      return [provider === connection.name ? '' : provider,
              this.hostOf(connection.keys.endpoint),
              `deployment ${connection.keys.model}`].filter(Boolean).join(' · ');
    }
    const own = this.catalogEndpoint(connection.keys.provider)
      ? '' : connection.keys.endpoint;
    return [provider === connection.name ? '' : provider, own,
            `starts with ${connection.keys.model}`].filter(Boolean).join(' · ');
  }

  private hostOf(address: string): string {
    try {
      return new URL(address).host;
    } catch {
      return address;
    }
  }

  private catalogEndpoint(provider: string): string {
    return this.providers.find((p) => p.id === provider)?.endpoint ?? '';
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

  // ── Adding: first which provider ────────────────────────────────────

  /** null = closed, '' = adding, id = editing. */
  editingId: string | null = null;
  /** Adding, and no provider chosen yet. */
  choosing = false;
  providerQuery = '';

  get isCreating(): boolean { return this.editingId === ''; }

  /** The providers offered: the popular few until something is typed,
   *  then whatever matches among all of them. The custom entry — a
   *  server of the person's own — is offered apart, last. */
  get offered(): LlmProvider[] {
    const query = this.providerQuery.trim().toLowerCase();
    const named = this.providers.filter((provider) => !!provider.endpoint);
    if (!query) {
      // The few most people look for, the most looked-for first.
      return named.filter((provider) => provider.popular)
        .sort((a, b) => Number(a.popular) - Number(b.popular));
    }
    return named.filter((provider) =>
      `${provider.name} ${provider.id}`.toLowerCase().includes(query));
  }

  get custom(): LlmProvider | null {
    return this.providers.find((provider) => !provider.endpoint) ?? null;
  }

  /** How many more a search would find. */
  get others(): number {
    return this.providers.filter((p) => !!p.endpoint && !p.popular).length;
  }

  startCreate(): void {
    this.editingId = '';
    this.choosing = true;
    this.providerQuery = '';
    this.draft = this.blankDraft();
    this.resetEditor(true);
    // It starts as its creator's alone, like everything a person
    // makes. Speech and agent routing take only a provider shared
    // with everyone, which is a choice made on this form.
    this.shareMode = 'private';
    this.selectedGroups.clear();
    this.selectedUsers.clear();
    this.error = '';
  }

  /** Back to the list of providers, from the form of one. */
  chooseAnother(): void {
    if (!this.saving && this.isCreating) this.choosing = true;
  }

  // ── The form ────────────────────────────────────────────────────────

  draft: LlmConnectionDraft = this.blankDraft();
  shareMode: ShareMode = 'private';
  selectedGroups = new Set<string>();
  selectedUsers = new Set<string>();
  saving = false;
  showMore = false;

  /** The chat models the chosen provider is known to serve — what the
   *  connection may start with. Empty for a provider the catalog lists
   *  none for, whose model is typed. */
  models: LlmModel[] = [];
  /** The starting model is being typed rather than picked. */
  typingModel = false;
  readonly other = '__other__';

  /** What the provider said when the key was last tried and refused. */
  refused: LlmCheck | null = null;

  /** The chosen provider's address while it still has parts that are
   *  the customer's own (`<aws-region>`), and what was typed for each.
   *  The form asks for those parts by name instead of having the person
   *  edit an address. */
  endpointTemplate = '';
  blankValues: Record<string, string> = {};

  /** What a blank is usually filled with, so the common case is no step. */
  private readonly blankDefaults: Record<string, string> = { 'aws-region': 'us-east-1' };

  /** A blank, as its provider's own console calls it, and where there
   *  it is found. One not named here is asked for by its own words. */
  private static readonly BLANKS: Record<string, { label: string; help: string; example: string }> = {
    'aws-region': {
      label: 'AWS region',
      help: 'The region your Bedrock models are enabled in.',
      example: 'us-east-1',
    },
    'cloudflare-account-id': {
      label: 'Cloudflare account ID',
      help: 'In the Cloudflare dashboard, on the Workers AI overview.',
      example: '',
    },
    'databricks-host': {
      label: 'Databricks workspace',
      help: 'Your workspace’s address, without https://.',
      example: 'dbc-a1b2c3d4-e5f6.cloud.databricks.com',
    },
    'infomaniak-product-id': {
      label: 'Infomaniak product ID',
      help: 'The number of your AI Tools product, in the Infomaniak manager.',
      example: '',
    },
    'snowflake-account': {
      label: 'Snowflake account',
      help: 'Your account identifier: the part of your Snowflake address before .snowflakecomputing.com.',
      example: 'myorg-myaccount',
    },
  };

  /** Where a provider's key is made, for the few most people use: the
   *  one thing the form asks for, and the one a person has to go and
   *  fetch. */
  private static readonly KEYS: Record<string, { where: string; url: string }> = {
    anthropic: { where: 'the Anthropic Console, under API keys',
                 url: 'https://console.anthropic.com/settings/keys' },
    openai: { where: 'the OpenAI platform, under API keys',
              url: 'https://platform.openai.com/api-keys' },
    gemini: { where: 'Google AI Studio, under Get API key',
              url: 'https://aistudio.google.com/apikey' },
    openrouter: { where: 'OpenRouter, under Keys',
                  url: 'https://openrouter.ai/settings/keys' },
    'amazon-bedrock': { where: 'the AWS console: Amazon Bedrock, then API keys',
                        url: 'https://console.aws.amazon.com/bedrock/home#/api-keys' },
    azure: { where: 'the Azure portal: your resource, then Keys and Endpoint',
             url: 'https://portal.azure.com' },
    groq: { where: 'the Groq console, under API Keys',
            url: 'https://console.groq.com/keys' },
    mistral: { where: 'the Mistral console, under API keys',
               url: 'https://console.mistral.ai/api-keys' },
    xai: { where: 'the xAI console, under API keys', url: 'https://console.x.ai' },
    deepseek: { where: 'the DeepSeek platform, under API keys',
                url: 'https://platform.deepseek.com/api_keys' },
  };

  /** The models a new connection starts with, by the beginning of
   *  their id, where the newest the catalog lists is not the one to
   *  start with: a provider that serves other makers' models lists
   *  those among its own. An empty list is a provider whose model is
   *  the person's to choose — what they may call there depends on
   *  their account. */
  private static readonly STARTS: Record<string, string[]> = {
    anthropic: ['claude-sonnet'],
    openai: ['gpt-'],
    gemini: ['gemini-'],
    openrouter: ['anthropic/claude-sonnet', 'openai/gpt-'],
    'amazon-bedrock': [],
    groq: ['llama-'],
    mistral: ['mistral-medium', 'mistral-large', 'mistral-'],
    xai: ['grok-'],
    deepseek: ['deepseek-'],
  };

  /** How many models a plain list holds before it is searched instead. */
  static readonly LONG_LIST = 40;

  /** Addresses a model on this computer usually answers at, as the
   *  platform sees them from inside its container. */
  readonly ownServers = [
    { name: 'Ollama', address: 'http://host.docker.internal:11434/v1' },
    { name: 'LM Studio', address: 'http://host.docker.internal:1234/v1' },
  ];

  get keyPlace(): { where: string; url: string } | null {
    return ModelProvidersComponent.KEYS[this.draft.provider] ?? null;
  }

  blankHelp(blank: string): string {
    return ModelProvidersComponent.BLANKS[blank]?.help ?? '';
  }

  blankExample(blank: string): string {
    const example = ModelProvidersComponent.BLANKS[blank]?.example ?? '';
    return example ? `e.g. ${example}` : '';
  }

  /** The list is long enough to be searched rather than scrolled. */
  get searchingModels(): boolean {
    return this.models.length > ModelProvidersComponent.LONG_LIST;
  }

  /** The model a new connection starts with: the first of the
   *  provider's own where that is known, the newest listed where it is
   *  not, and none where the person must choose. */
  private startingModel(provider: string, models: LlmModel[]): string {
    const starts = ModelProvidersComponent.STARTS[provider];
    if (!starts) return models[0]?.id ?? '';
    for (const start of starts) {
      const found = models.find((model) => model.id.startsWith(start));
      if (found) return found.id;
    }
    return starts.length ? models[0]?.id ?? '' : '';
  }

  /** What an address has in each blank of the catalog's, or null when
   *  it is not that address at all — a gateway of the person's own. */
  private blanksIn(template: string, address: string): Record<string, string> | null {
    const names = [...template.matchAll(/<([^>]+)>/g)].map((found) => found[1]);
    const pattern = template.replace(/\/+$/, '')
      .split(/<[^>]+>/)
      .map((piece) => piece.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
      .join('([^/]+?)');
    const found = new RegExp(`^${pattern}$`).exec(address.replace(/\/+$/, ''));
    if (!found) return null;
    return Object.fromEntries(names.map((name, index) => [name, found[index + 1]]));
  }

  /** A model on this computer, by one of the addresses such a server
   *  usually answers at. */
  useOwnServer(address: string): void {
    this.setEndpoint(address);
  }

  readonly awsRegions = [
    'us-east-1', 'us-east-2', 'us-west-2', 'ca-central-1', 'sa-east-1',
    'eu-west-1', 'eu-west-2', 'eu-west-3', 'eu-central-1', 'eu-north-1',
    'ap-south-1', 'ap-northeast-1', 'ap-northeast-2', 'ap-southeast-1',
    'ap-southeast-2', 'me-central-1',
  ];

  /** The name follows the provider until the person types one. */
  private nameIsAuto = true;

  private blankDraft(): LlmConnectionDraft {
    return { name: '', provider: '', model: '', endpoint: '', api_key: '' };
  }

  private resetEditor(creating: boolean): void {
    this.models = [];
    this.typingModel = false;
    this.endpointTemplate = '';
    this.blankValues = {};
    this.nameIsAuto = creating;
    this.showMore = false;
    this.refused = null;
  }

  get chosen(): LlmProvider | null {
    return this.providers.find((p) => p.id === this.draft.provider) ?? null;
  }

  // ── Azure ───────────────────────────────────────────────────────────
  //
  // Azure is not shaped like the others: a customer has an endpoint of
  // their own, shown in the portal in several forms, and calls a
  // deployment by a name they chose, not a model from a list. So the
  // form asks for what the portal shows — the endpoint, pasted as it
  // is; the key; the deployment's name — and the platform works out the
  // address requests go to (contracts/llm_providers.py, `azure`).

  static readonly AZURE = 'azure';
  private static readonly AZURE_HOSTS = [
    '.openai.azure.com', '.cognitiveservices.azure.com', '.services.ai.azure.com',
  ];

  /** The chosen provider is called by deployment, at the person's own
   *  endpoint. */
  get byDeployment(): boolean {
    return this.draft.provider === ModelProvidersComponent.AZURE;
  }

  /** What a pasted Azure endpoint comes to — the address requests go
   *  to, and the deployment it named where it named one — or null for
   *  one that is not Azure's own. The backend settles it the same way;
   *  this is so the form can say what it understood. */
  private azure(pasted: string): { endpoint: string; deployment: string } | null {
    const text = pasted.trim();
    if (!text) return null;
    let url: URL;
    try {
      url = new URL(text.includes('://') ? text : `https://${text}`);
    } catch {
      return null;
    }
    const host = url.host.toLowerCase();
    if (!ModelProvidersComponent.AZURE_HOSTS.some(
        (suffix) => host.endsWith(suffix) && host.length > suffix.length)) {
      return null;
    }
    const named = /\/openai\/deployments\/([^/?#]+)/.exec(url.pathname);
    return { endpoint: `https://${host}/openai/v1`,
             deployment: named ? decodeURIComponent(named[1]) : '' };
  }

  /** The endpoint as the person pasted it. A target URI names the
   *  deployment too, and that is filled in where it is still empty. */
  setAzureEndpoint(pasted: string): void {
    this.draft.endpoint = pasted;
    this.refused = null;
    const found = this.azure(pasted);
    if (found?.deployment && !this.draft.model.trim()) this.draft.model = found.deployment;
    if (this.nameIsAuto && found) {
      this.draft.name = this.freeName(`Azure ${new URL(found.endpoint).host.split('.')[0]}`);
    }
  }

  /** What the form understood of the endpoint, said under the field. */
  get azureUnderstood(): string {
    const pasted = this.draft.endpoint.trim();
    if (!pasted) return '';
    const found = this.azure(pasted);
    if (!found) return 'That is not an Azure endpoint. It is used as written.';
    return found.endpoint === pasted ? '' : `Requests go to ${found.endpoint}`;
  }

  /** Whether the address is the person's to type in full: the custom
   *  entry has none of its own. */
  get endpointIsYours(): boolean {
    return !!this.chosen && !this.chosen.endpoint;
  }

  selectProvider(provider: LlmProvider): void {
    this.choosing = false;
    this.draft = { ...this.blankDraft(), provider: provider.id };
    this.resetEditor(true);
    if (this.byDeployment) {
      // Nothing is filled in: the endpoint and the deployment are the
      // person's own, and there is no list to choose from.
      this.draft.name = this.freeName(provider.name);
      this.typingModel = true;
      return;
    }
    this.endpointTemplate = /<[^>]+>/.test(provider.endpoint) ? provider.endpoint : '';
    for (const blank of this.blanks) this.blankValues[blank] = this.blankDefaults[blank] ?? '';
    this.draft.endpoint = this.filled(provider.endpoint);
    this.draft.name = this.freeName(provider.endpoint ? provider.name : '');
    // A server of the person's own: its address is the first thing asked.
    this.showMore = false;
    void this.loadModels(provider.id, true);
  }

  /** The provider's name, or that with a number where this person
   *  already has a connection by it. */
  private freeName(name: string): string {
    if (!name) return '';
    const mine = new Set(this.connections
      .filter((c) => c.created_by === (this.profile?.user_id ?? ''))
      .map((c) => c.name.toLowerCase()));
    if (!mine.has(name.toLowerCase())) return name;
    for (let number = 2; ; number++) {
      if (!mine.has(`${name} ${number}`.toLowerCase())) return `${name} ${number}`;
    }
  }

  /** The provider's chat models; `choose` starts the connection with
   *  the first — the newest the catalog knows. */
  private async loadModels(provider: string, choose: boolean): Promise<void> {
    let models: LlmModel[] = [];
    try {
      models = await this.service.catalogModels(provider, 'chat');
    } catch {
      models = [];
    }
    // A slow answer for a provider since changed is nobody's list.
    if (this.draft.provider !== provider) return;
    this.models = models;
    if (choose) this.draft.model = this.startingModel(provider, models);
    this.typingModel = !models.length
      || (!!this.draft.model && !models.some((m) => m.id === this.draft.model));
  }

  chooseModel(model: string): void {
    if (model === this.other) {
      this.typingModel = true;
      this.draft.model = '';
    } else {
      this.draft.model = model;
    }
    this.refused = null;
  }

  pickFromList(): void {
    this.typingModel = false;
    this.draft.model = this.startingModel(this.draft.provider, this.models);
  }

  /** The customer's own parts of the provider's address, by name. */
  get blanks(): string[] {
    return [...this.endpointTemplate.matchAll(/<([^>]+)>/g)].map((found) => found[1]);
  }

  blankLabel(blank: string): string {
    const known = ModelProvidersComponent.BLANKS[blank]?.label;
    if (known) return known;
    const words = blank.replace(/-/g, ' ');
    return words.startsWith('aws ') ? 'AWS ' + words.slice(4)
      : words.charAt(0).toUpperCase() + words.slice(1);
  }

  setBlank(blank: string, value: string): void {
    this.blankValues[blank] = value;
    this.draft.endpoint = this.filled(this.endpointTemplate);
    this.refused = null;
  }

  /** The address with every part that was typed put in; one still
   *  missing stays marked, and the blocker says so. */
  private filled(template: string): string {
    return template.replace(/<([^>]+)>/g,
      (mark, blank) => (this.blankValues[blank] ?? '').trim() || mark);
  }

  setName(name: string): void {
    this.draft.name = name;
    this.nameIsAuto = false;
  }

  /** A server of the person's own is named after where it is, until
   *  they name it themselves. */
  setEndpoint(endpoint: string): void {
    this.draft.endpoint = endpoint;
    this.refused = null;
    if (!this.nameIsAuto || !this.endpointIsYours) return;
    try {
      this.draft.name = this.freeName(new URL(endpoint.trim()).host);
    } catch {
      this.draft.name = '';
    }
  }

  setKey(key: string): void {
    this.draft.api_key = key;
    this.refused = null;
  }

  startEdit(connection: LlmConnection): void {
    this.editingId = connection.resource_ref;
    this.choosing = false;
    this.draft = {
      name: connection.name,
      provider: connection.keys.provider,
      model: connection.keys.model,
      endpoint: connection.keys.endpoint,
      api_key: '', // write-only: blank means keep
    };
    // A saved connection keeps its name and its address as they are.
    this.resetEditor(false);
    if (this.byDeployment) {
      this.typingModel = true;
    } else {
      // The same fields it was added with: an address that is the
      // provider's own with the person's parts in it is shown as those
      // parts. One that is not — a gateway — stays an address.
      const template = this.catalogEndpoint(this.draft.provider);
      const parts = /<[^>]+>/.test(template)
        ? this.blanksIn(template, this.draft.endpoint) : null;
      if (parts) {
        this.endpointTemplate = template;
        this.blankValues = parts;
      }
      void this.loadModels(this.draft.provider, false);
    }
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

  get myGroups(): { group_id: string; group_name: string }[] {
    return (this.profile?.groups ?? []).filter(
      (group) => group.group_id !== 'everyone',
    );
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

  closeEditor(): void {
    if (!this.saving) this.editingId = null;
  }

  /** What is still missing, said before the request rather than after.
   *  The key is only demanded for a NEW connection — on an existing one,
   *  blank keeps the stored key. */
  get blocker(): string {
    if (!this.draft.provider) return 'Choose a provider.';
    if (this.byDeployment) {
      if (!this.draft.endpoint.trim()) return 'Paste your Azure endpoint.';
      if (this.isCreating && !this.draft.api_key.trim()) return 'Paste the API key.';
      if (!this.draft.model.trim()) return 'Name your deployment.';
    }
    const missing = this.blanks.find((blank) => !(this.blankValues[blank] ?? '').trim());
    if (missing) return `Enter your ${this.blankLabel(missing)}.`;
    if (!this.draft.endpoint.trim()) return 'Enter the address the server answers at.';
    if (/[<>]/.test(this.draft.endpoint)) {
      return 'Fill in the address: replace each <...> with your own account’s value.';
    }
    // A server of the person's own may ask for no key at all.
    if (this.isCreating && !this.endpointIsYours && !this.draft.api_key.trim()) {
      return 'Paste the API key.';
    }
    if (!this.draft.model.trim()) {
      return this.models.length ? 'Choose a model.'
        : 'Name the model, as the server calls it.';
    }
    if (!this.draft.name.trim()) return 'Give it a name.';
    if (this.shareMode === 'groups' && !this.selectedGroups.size) {
      return 'Pick at least one group, or share it another way.';
    }
    if (this.shareMode === 'users' && !this.selectedUsers.size) {
      return 'Pick at least one person, or share it another way.';
    }
    return '';
  }

  /** Save, asking the provider first whether the key works. A key it
   *  refuses, or an address nobody answers at, is not saved: the reason
   *  is shown, with the way to save all the same (`check` false). */
  async save(check = true): Promise<void> {
    if (this.blocker || this.saving) return;
    this.saving = true;
    this.refused = null;
    try {
      const draft: LlmConnectionDraft = {
        name: this.draft.name.trim(),
        provider: this.draft.provider,
        model: this.draft.model.trim(),
        endpoint: this.draft.endpoint.trim(),
        // A server that asks for no key is still spoken to with one:
        // the protocol has a place for it, and the store keeps none
        // empty.
        api_key: this.draft.api_key.trim() || !this.isCreating || !this.endpointIsYours
          ? this.draft.api_key : 'none',
        owner: this.buildOwner(),
        check,
      };
      const result = this.isCreating
        ? await this.service.create(draft)
        : await this.service.update(this.editingId!, draft);
      if (result.error) {
        if (result.check) this.refused = result.check;
        else this.fail(result.error);
        return;
      }

      this.editingId = null;
      this.changed.emit();
      const name = result.connection?.name ?? draft.name;
      if (result.check?.outcome === 'works') {
        this.flash(`"${name}" saved — the key works.`);
      } else if (result.check?.outcome === 'unknown') {
        this.flash(`"${name}" saved. The provider could not be asked whether the key works.`);
      } else {
        this.flash(`"${name}" saved.`);
      }
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
      this.changed.emit();
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
      this.changed.emit();
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
      this.changed.emit();
      this.flash(`Handed over to ${person.user_name || person.email}.`);
    } finally {
      this.transferring = false;
    }
  }
}
