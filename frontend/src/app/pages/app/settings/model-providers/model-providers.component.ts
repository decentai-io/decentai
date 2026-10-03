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
    const own = this.catalogEndpoint(connection.keys.provider)
      ? '' : connection.keys.endpoint;
    return [provider === connection.name ? '' : provider, own,
            `starts with ${connection.keys.model}`].filter(Boolean).join(' · ');
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
    if (!query) return named.filter((provider) => provider.popular);
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

  /** Whether the address is the person's to type in full: the custom
   *  entry has none of its own. */
  get endpointIsYours(): boolean {
    return !!this.chosen && !this.chosen.endpoint;
  }

  selectProvider(provider: LlmProvider): void {
    this.choosing = false;
    this.draft = { ...this.blankDraft(), provider: provider.id };
    this.resetEditor(true);
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
    if (choose) this.draft.model = models[0]?.id ?? '';
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
    this.draft.model = this.models[0]?.id ?? '';
  }

  /** The customer's own parts of the provider's address, by name. */
  get blanks(): string[] {
    return [...this.endpointTemplate.matchAll(/<([^>]+)>/g)].map((found) => found[1]);
  }

  blankLabel(blank: string): string {
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
    void this.loadModels(this.draft.provider, false);
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
    const missing = this.blanks.find((blank) => !(this.blankValues[blank] ?? '').trim());
    if (missing) return `Enter your ${this.blankLabel(missing)}.`;
    if (!this.draft.endpoint.trim()) return 'Enter the address the server answers at.';
    if (/[<>]/.test(this.draft.endpoint)) {
      return 'Fill in the address: replace each <...> with your own account’s value.';
    }
    if (this.isCreating && !this.draft.api_key.trim()) return 'Paste the API key.';
    if (!this.draft.model.trim()) {
      return this.models.length ? 'Choose the model to start with.'
        : 'Name the model to start with — the provider’s own id for it.';
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
        api_key: this.draft.api_key,
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
