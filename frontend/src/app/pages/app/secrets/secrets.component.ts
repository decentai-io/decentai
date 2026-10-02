import { Component, OnInit } from '@angular/core';

import { AgentsService, LendableAgent } from 'src/app/services/agents.service';
import { AuthService } from 'src/app/services/auth.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import { OauthConnectService } from 'src/app/services/oauth.service';
import {
  DefinitionField,
  OAUTH_FIELD_NAMES,
  SecretDefinition,
  SecretDefinitionsService,
} from 'src/app/services/secret-definitions.service';
import { Secret, SecretUser, SecretsService } from 'src/app/services/secrets.service';
import { DataPageBase } from '../data-page-base';

type ShareMode = 'private' | 'groups' | 'people' | 'org';

/**
 * Secrets: one dynamically-rendered form for every secret type. The form
 * comes from the type's definition; encrypted fields are write-only (blank
 * on edit — leave empty to keep the stored value). Sharing is the owner
 * field, bounded by the caller's own groups unless they hold the escape
 * action.
 */
@Component({
  selector: 'app-secrets',
  standalone: false,
  templateUrl: './secrets.component.html',
  styleUrls: ['../data-shared.css', '../../admin/iam-shared.css', './secrets.component.css'],
})
export class SecretsComponent extends DataPageBase implements OnInit {
  loading = true;
  secrets: Secret[] = [];
  definitions: SecretDefinition[] = [];
  profile: Profile | null = null;

  activeTab: 'mine' | 'shared' = 'mine';
  /** Two kinds of secret, two views: the credentials agents declare a
   *  shape for, grouped by the agent that uses them; and the logins
   *  agents asked for as they worked, grouped by site. */
  view: 'agents' | 'logins' = 'agents';
  query = '';

  // Editor state: null = closed, '' = creating, ref = editing.
  editingRef: string | null = null;
  formDefinition: SecretDefinition | null = null;
  formName = '';
  formFields: Record<string, any> = {};
  shareMode: ShareMode = 'private';
  selectedGroups = new Set<string>();
  selectedUsers = new Set<string>();
  /** People sharing an explicit group with me — the reach of a
   *  person-share, and exactly what the backend will accept. */
  peers: { user_id: string; user_name?: string; email?: string }[] = [];
  saving = false;
  busyRef = '';
  definitionQuery = '';
  /** The "which shape?" step, asked in a dialog rather than as a second
   *  tab: it is one choice on the way to the form, not somewhere to be. */
  pickerOpen = false;
  /** The secret being deleted, or null. `confirm()` used to ask this,
   *  which put a browser chrome dialog in the middle of the app and gave
   *  no room to say what deleting actually costs. */
  deleteTarget: Secret | null = null;

  constructor(
    private service: SecretsService,
    private definitionsService: SecretDefinitionsService,
    private profiles: ProfileService,
    public auth: AuthService,
    private oauth: OauthConnectService,
    private agents: AgentsService,
  ) {
    super();
  }

  // ── Lending a connected account on to the agents that use it ───────
  //
  // A credential made here belongs to one agent's slot and reaches no
  // other. Offered once, right after the sign-in, ticked by default;
  // nothing happens until the person clicks.

  lendOffer: {
    secretRef: string; provider: string;
    agents: (LendableAgent & { picked: boolean })[];
  } | null = null;
  lending = false;

  private async offerLend(secretRef: string, provider: string): Promise<void> {
    try {
      const found = await this.agents.lendable(secretRef);
      if (!found.eligible.length) return;
      this.lendOffer = {
        secretRef, provider,
        agents: found.eligible.map((agent) => ({ ...agent, picked: true })),
      };
    } catch {
      // Without the lend action the flash already said it connected.
    }
  }

  pickedCount(): number {
    return this.lendOffer?.agents.filter((a) => a.picked).length ?? 0;
  }

  dismissLend(): void {
    this.lendOffer = null;
  }

  async lendToPicked(): Promise<void> {
    const offer = this.lendOffer;
    if (!offer) return;
    const chosen = offer.agents.filter((a) => a.picked)
      .map((a) => ({ agent_id: a.agent_id, resource_id: a.resource_id }));
    if (!chosen.length) return;
    this.lending = true;
    this.error = '';
    try {
      const result = await this.agents.lendMany(offer.secretRef, chosen);
      if (result.error) return this.fail(result.error);
      const granted: { name: string }[] = result.data?.granted || [];
      const failed = result.data?.failed;
      this.lendOffer = null;
      await this.reloadSecrets();
      const names = granted.map((g) => g.name).join(', ');
      if (failed) {
        const who = offer.agents.find((a) => a.agent_id === failed.agent_id)?.name
          || failed.agent_id;
        this.fail(`${names ? `Lent to ${names}. ` : ''}Could not lend to ${who}: ${failed.error}`);
      } else {
        this.flash(`Lent to ${names}.`);
      }
    } finally {
      this.lending = false;
    }
  }

  // ── Connected accounts ──────────────────────────────────────────────
  //
  // A definition with an oauth block is not filled in: the person signs
  // in with the provider in a popup and comes back with the credential
  // made. The platform's own fields on it — tokens, expiry, status —
  // are shown as a state, never as inputs.

  connecting = '';

  isOauthDefinition(definition: SecretDefinition | null | undefined): boolean {
    return !!definition?.oauth;
  }

  isOauth(secret: Secret): boolean {
    return this.isOauthDefinition(
      this.definitions.find((d) => d.definition_id === secret.resource_id));
  }

  providerOf(definition: SecretDefinition | null | undefined): string {
    const id = definition?.oauth?.provider || '';
    return id ? id.charAt(0).toUpperCase() + id.slice(1) : 'the provider';
  }

  needsReconnect(secret: Secret): boolean {
    return this.isOauth(secret) && secret.keys?.['status'] === 'needs_reconnect';
  }

  /** The fields a person edits: for a connected account, only what the
   *  agent declared beyond the platform's own. */
  editableFields(definition: SecretDefinition): DefinitionField[] {
    if (!definition.oauth) return definition.fields;
    return definition.fields.filter((f) => !OAUTH_FIELD_NAMES.includes(f.name));
  }

  async connect(definition: SecretDefinition): Promise<void> {
    this.pickerOpen = false;
    this.error = '';
    this.connecting = definition.definition_ref;
    try {
      const result = await this.oauth.connect({ definition_ref: definition.definition_ref });
      if (result.error) return this.fail(result.error);
      await this.reloadSecrets();
      const made = this.secrets.find((s) => s.resource_ref === result.resource_ref);
      this.flash(made?.keys?.['account']
        ? `Connected as ${made.keys['account']}.`
        : `${definition.label} connected.`);
      if (result.resource_ref) {
        await this.offerLend(result.resource_ref, this.providerOf(definition));
      }
    } finally {
      this.connecting = '';
    }
  }

  async reconnect(secret: Secret): Promise<void> {
    this.error = '';
    this.busyRef = secret.resource_ref;
    try {
      const result = await this.oauth.connect({ resource_ref: secret.resource_ref });
      if (result.error) return this.fail(result.error);
      await this.reloadSecrets();
      this.flash(`${secret.name} reconnected.`);
    } finally {
      this.busyRef = '';
    }
  }

  async ngOnInit(): Promise<void> {
    const [secrets, definitions, profile] = await Promise.all([
      this.service.list(),
      this.definitionsService.list(),
      this.profiles.get(),
    ]);
    this.secrets = secrets;
    this.definitions = definitions;
    this.profile = profile;
    this.loading = false;
    this.peers = await this.profiles.peers().catch(() => []);
  }

  private async reloadSecrets(): Promise<void> {
    this.secrets = await this.service.list();
  }

  get canCreate(): boolean { return this.auth.can('secrets:secret:create'); }
  get canUpdate(): boolean { return this.auth.can('secrets:secret:update'); }
  get canDelete(): boolean { return this.auth.can('secrets:secret:delete'); }
  get canShareAny(): boolean {
    return this.auth.can('secrets:secret:set_owner_any');
  }

  get myGroups(): { group_id: string; group_name: string }[] {
    return (this.profile?.groups ?? []).filter(
      (group) => group.group_id !== 'everyone',
    );
  }

  get myUserId(): string { return this.profile?.user_id ?? ''; }

  get isCreating(): boolean { return this.editingRef === ''; }

  get mySecrets(): Secret[] {
    return this.secrets.filter((s) => s.created_by === this.myUserId && !this.isSiteLogin(s));
  }

  get sharedSecrets(): Secret[] {
    return this.secrets.filter((s) => s.created_by !== this.myUserId && !this.isSiteLogin(s));
  }

  /** A login an agent asked for as it worked (call.credential): an
   *  ordinary secret under a host's family, listed apart because its
   *  use is by consent — this agent on this site — not by grant. */
  isSiteLogin(secret: Secret): boolean {
    return String(secret.resource_id || '').startsWith('site__');
  }

  get myLogins(): Secret[] {
    return this.secrets.filter((s) => this.isSiteLogin(s) && s.created_by === this.myUserId);
  }

  get sharedLogins(): Secret[] {
    return this.secrets.filter((s) => this.isSiteLogin(s) && s.created_by !== this.myUserId);
  }

  get agentCredentialCount(): number {
    return this.mySecrets.length + this.sharedSecrets.length;
  }

  get loginCount(): number {
    return this.myLogins.length + this.sharedLogins.length;
  }

  /** The logins shown, one section per site. */
  get groupedLogins(): { host: string; items: Secret[] }[] {
    const groups = new Map<string, Secret[]>();
    for (const secret of this.shownLogins) {
      const host = this.loginHost(secret);
      if (!groups.has(host)) groups.set(host, []);
      groups.get(host)!.push(secret);
    }
    return [...groups.entries()]
      .map(([host, items]) => ({ host, items }))
      .sort((a, b) => a.host.localeCompare(b.host));
  }

  /** The agents one credential was lent to by a grant. A consent on a
   *  saved login rides in the same list and is not one: it has no slot. */
  grantedTo(secret: Secret): SecretUser[] {
    return (secret.used_by_agents || []).filter((agent) => !!agent.resource_id);
  }

  /** Every agent that reads a credential: the one whose own slot it
   *  was saved under, which needs no grant, and those it was lent to. */
  agentsUsing(secret: Secret): string[] {
    const names = (secret.used_by_agents || []).map((a) => a.name);
    if (secret.home_agent?.name) names.unshift(secret.home_agent.name);
    return [...new Set(names.filter(Boolean))];
  }

  /** The credentials shown, one section per agent that uses them — the
   *  question a person brings here is "what does this agent have?".
   *  A credential no agent uses sits last, on its own. */
  get groupedSecrets(): { agent: string; items: Secret[] }[] {
    const groups = new Map<string, Secret[]>();
    for (const secret of this.shownSecrets) {
      const agents = this.agentsUsing(secret);
      const key = agents.length ? [...new Set(agents)].sort().join(', ') : '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(secret);
    }
    return [...groups.entries()]
      .map(([agent, items]) => ({ agent, items }))
      .sort((a, b) => {
        if (!a.agent) return 1;
        if (!b.agent) return -1;
        return a.agent.localeCompare(b.agent);
      });
  }

  get shownLogins(): Secret[] {
    const mine = this.activeTab === 'mine';
    const query = this.query.trim().toLowerCase();
    return this.secrets
      .filter((s) => this.isSiteLogin(s) && (s.created_by === this.myUserId) === mine)
      .filter((s) => !query || [s.name, this.loginHost(s), this.loginAccount(s),
        this.consentSummary(s)].join(' ').toLowerCase().includes(query));
  }

  loginHost(secret: Secret): string {
    return String(secret.keys?.['host'] || secret.resource_id.replace(/^site__/, '').replace(/_/g, '.'));
  }

  loginAccount(secret: Secret): string {
    return String(secret.keys?.['account'] || '');
  }

  /** The consent pairs on a login: which agent, on which site. */
  consents(secret: Secret): { agent_ref: string; name: string; site: string; pair: string }[] {
    const names = new Map((secret.used_by_agents || []).map((a) => [a.agent_ref, a.name]));
    return String(secret.keys?.['consents'] || '').split(',')
      .map((pair) => pair.trim()).filter(Boolean)
      .map((pair) => {
        const at = pair.indexOf('@');
        const agent_ref = at > 0 ? pair.slice(0, at) : pair;
        return { agent_ref, name: names.get(agent_ref) || agent_ref, site: at > 0 ? pair.slice(at + 1) : '', pair };
      });
  }

  consentSummary(secret: Secret): string {
    return this.consents(secret).map((c) => `${c.name} on ${c.site}`).join(', ');
  }

  /** Taking one agent's consent back: the pair leaves the row, and the
   *  next time that agent reaches the site it asks again. */
  async revokeConsent(secret: Secret, pair: string): Promise<void> {
    this.busyRef = secret.resource_ref;
    this.error = '';
    try {
      const consents = this.consents(secret).map((c) => c.pair).filter((p) => p !== pair).join(',');
      const result = await this.service.update(secret.resource_ref, { fields: { consents } });
      if (result.error) { this.error = result.error; return; }
      await this.reloadSecrets();
    } finally {
      this.busyRef = '';
    }
  }

  /** The version a secret was created under is the shape it still has —
   *  the definition may have been published forward without it. */
  latestVersion(secret: Secret): number {
    return this.definitions.find(
      (definition) => definition.definition_id === secret.resource_id,
    )?.version ?? secret.definition_version;
  }

  isBehindDefinition(secret: Secret): boolean {
    return secret.definition_version < this.latestVersion(secret);
  }

  get shownSecrets(): Secret[] {
    const pool = this.activeTab === 'mine' ? this.mySecrets : this.sharedSecrets;
    const query = this.query.trim().toLowerCase();
    if (!query) return pool;
    return pool.filter((secret) =>
      [secret.name, this.definitionLabel(secret.resource_id),
       this.keysSummary(secret),
       ...this.agentsUsing(secret)].join(' ').toLowerCase().includes(query));
  }

  /** The picker, grouped by the agent whose slot the shape is — the
   *  question being answered is "a credential for WHICH agent?", and a
   *  slug like agt_9f3c__connection answers nobody. */
  get groupedDefinitions(): { agent: string; items: SecretDefinition[] }[] {
    const groups = new Map<string, SecretDefinition[]>();
    for (const definition of this.matchingDefinitions) {
      const agents = definition.agent_uses || [];
      const key = agents.length ? agents.join(', ') : '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(definition);
    }
    return [...groups.entries()]
      .map(([agent, items]) => ({ agent, items }))
      .sort((a, b) => {
        if (!a.agent) return 1; // orphaned shapes last
        if (!b.agent) return -1;
        return a.agent.localeCompare(b.agent);
      });
  }

  definitionLabel(slug: string): string {
    return (
      this.definitions.find((d) => d.definition_id === slug)?.label ?? slug
    );
  }

  get matchingDefinitions(): SecretDefinition[] {
    const query = this.definitionQuery.trim().toLowerCase();
    return [...this.definitions]
      .filter((definition) => !query ||
        `${definition.label} ${definition.description} ${definition.definition_id}`
          .toLowerCase().includes(query))
      .sort((a, b) => a.label.localeCompare(b.label));
  }

  // ── Editor ──────────────────────────────────────────────────────────

  startCreate(definition: SecretDefinition): void {
    if (definition.oauth) {
      void this.connect(definition);
      return;
    }
    this.pickerOpen = false;
    this.migrating = false;
    this.editingRef = '';
    this.formDefinition = definition;
    this.formName = '';
    this.formFields = {};
    for (const field of definition.fields) {
      this.formFields[field.name] = field.type === 'boolean' ? false : '';
    }
    this.shareMode = 'private';
    this.selectedGroups.clear();
    this.selectedUsers.clear();
    this.sharingAtOpen = '';
    this.error = '';
    this.definitionQuery = '';
  }

  /** The sharing an edit opened with. Saving sends `owner` only when
   *  this changed: somebody maintaining a colleague's secret must not
   *  rewrite who owns it by correcting its name. */
  private sharingAtOpen = '';

  private sharingNow(): string {
    return JSON.stringify([this.shareMode, [...this.selectedGroups].sort(),
                           [...this.selectedUsers].sort()]);
  }

  /** Whether the open editor is moving the secret onto its family's
   *  current version rather than editing in place. */
  migrating = false;

  async startEdit(secret: Secret): Promise<void> {
    // The form matches the version the secret was created under — or,
    // when the agent has moved on, the CURRENT one, and saving migrates:
    // stored fields carry over, dropped ones go, new required ones are
    // asked for right here.
    this.migrating = this.isBehindDefinition(secret);
    const result = await this.definitionsService.get(
      this.migrating
        ? { definition_id: secret.resource_id }
        : { definition_ref: secret.definition_ref },
    );
    if (!result.definition) {
      return this.fail(result.error || 'Could not load this secret type.');
    }

    this.editingRef = secret.resource_ref;
    this.formDefinition = result.definition;
    this.formName = secret.name;
    this.formFields = {};
    for (const field of result.definition.fields) {
      this.formFields[field.name] =
        field.storage === 'values'
          ? '' // write-only: blank means keep
          : secret.keys[field.name] ?? (field.type === 'boolean' ? false : '');
    }

    const owner = secret.owner;
    const others = (owner.users ?? []).filter((id) => id !== this.myUserId);
    if (owner.groups.includes('everyone')) {
      this.shareMode = 'org';
    } else if (owner.groups.length) {
      this.shareMode = 'groups';
    } else if (others.length) {
      this.shareMode = 'people';
    } else {
      this.shareMode = 'private';
    }
    this.selectedGroups = new Set(owner.groups);
    this.selectedUsers = new Set(others);
    this.sharingAtOpen = this.sharingNow();
    this.error = '';
  }

  cancelEdit(): void {
    this.editingRef = null;
    this.formDefinition = null;
    this.migrating = false;
  }

  openValues(): void {
    if (this.saving) return;
    this.cancelEdit();
    this.error = '';
  }

  openPicker(): void {
    if (this.saving) return;
    this.cancelEdit();
    this.pickerOpen = true;
    this.definitionQuery = '';
    this.error = '';
  }

  closePicker(): void {
    this.pickerOpen = false;
  }

  toggleGroup(groupId: string): void {
    if (this.selectedGroups.has(groupId)) this.selectedGroups.delete(groupId);
    else this.selectedGroups.add(groupId);
  }

  toggleUser(userId: string): void {
    if (!this.selectedUsers.delete(userId)) this.selectedUsers.add(userId);
  }

  isEncrypted(field: DefinitionField): boolean {
    return field.storage === 'values';
  }

  private buildOwner(): Record<string, any> {
    if (this.shareMode === 'org') {
      return { groups: ['everyone'], users: [] };
    }
    if (this.shareMode === 'groups') {
      return { groups: Array.from(this.selectedGroups) };
    }
    if (this.shareMode === 'people') {
      // Myself always: sharing to a peer must never lock me out.
      return { groups: [], users: [this.myUserId, ...Array.from(
        this.selectedUsers).filter((u) => u !== this.myUserId)] };
    }
    return { users: [this.myUserId] };
  }

  private buildFields(): Record<string, any> {
    const definition = this.formDefinition!;
    const fields: Record<string, any> = {};
    for (const field of this.editableFields(definition)) {
      let value = this.formFields[field.name];
      if (field.type === 'number') {
        if (value === '' || value === null || value === undefined) continue;
        value = Number(value);
      }
      if (value === '' && this.blankIsNotAnEdit(field)) continue;
      fields[field.name] = value;
    }
    return fields;
  }

  /** Whether a blank box means "I did not touch this" rather than "make
   *  this empty".
   *
   *  On edit that is true only of an ENCRYPTED field: the form cannot show
   *  a stored secret back, so it opens blank and blank has to mean keep. A
   *  plaintext field is on screen with its value in it — clearing it is a
   *  deliberate edit, and dropping it here left the old value in place.
   *
   *  On create there is nothing to keep: a blank optional field is simply
   *  not sent, and a blank required one is sent so the store says so. */
  private blankIsNotAnEdit(field: DefinitionField): boolean {
    return this.isCreating ? !field.required : field.storage === 'values';
  }

  async save(): Promise<void> {
    if (!this.formDefinition) return;
    this.saving = true;
    const creating = this.isCreating;
    try {
      const result = this.isCreating
        ? await this.service.create({
            definition_ref: this.formDefinition.definition_ref,
            name: this.formName.trim(),
            fields: this.buildFields(),
            owner: this.buildOwner(),
          })
        : await this.service.update(this.editingRef!, {
            name: this.formName.trim(),
            fields: this.buildFields(),
            ...(this.sharingNow() !== this.sharingAtOpen ? { owner: this.buildOwner() } : {}),
            ...(this.migrating ? { migrate: true } : {}),
          });

      if (result.error) {
        const detail = result.ungrantable?.length
          ? ` (${result.ungrantable.join(', ')})`
          : '';
        return this.fail(result.error + detail);
      }

      this.editingRef = null;
      this.formDefinition = null;
      await this.reloadSecrets();
      this.flash(creating ? 'Secret created.' : 'Secret updated.');
    } finally {
      this.saving = false;
    }
  }

  requestDelete(secret: Secret): void {
    this.deleteTarget = secret;
    this.error = '';
  }

  /** The agents granted this credential, for the sentence that says what
   *  loses access when it goes. */
  agentNames(secret: Secret): string {
    return this.grantedTo(secret).map((a) => a.name).join(', ');
  }

  closeDelete(): void {
    if (!this.busyRef) this.deleteTarget = null;
  }

  async remove(secret: Secret): Promise<void> {
    this.busyRef = secret.resource_ref;
    try {
      const result = await this.service.remove(secret.resource_ref);
      if (result.error) return this.fail(result.error);
      this.deleteTarget = null;
      await this.reloadSecrets();
      this.flash('Secret deleted.');
    } finally {
      this.busyRef = '';
    }
  }

  canTouch(secret: Secret): boolean {
    return secret.created_by === this.myUserId || this.canShareAny;
  }

  shareSummary(secret: Secret): string {
    const owner = secret.owner;
    if (owner.groups.includes('everyone')) return 'Organization-wide';
    if (owner.groups.length) {
      const names = owner.groups.map(
        (id) => this.myGroups.find((g) => g.group_id === id)?.group_name ?? id,
      );
      return `Shared with: ${names.join(', ')}`;
    }
    if (secret.created_by !== this.myUserId) return 'Shared with you directly';
    // A private secret still names its creator; anyone else in the list
    // is somebody it was shared with.
    const others = (owner.users ?? []).filter((id) => id !== secret.created_by);
    if (!others.length) return 'Private';
    return others.length === 1 ? 'Shared with a person' : `Shared with ${others.length} people`;
  }

  /** The plaintext half, for the row. A field left empty says nothing
   *  worth a line of its own — "username:" with nothing after it reads
   *  like a fault rather than a deliberately empty box. */
  /** A login's own fields — what the person typed on the card, with
   *  an encrypted one named and not shown. The platform's bookkeeping
   *  keys (host, account, status, consents, sites) are the row's
   *  heading and consent line, not fields. */
  loginFieldsSummary(secret: Secret): string {
    const bookkeeping = new Set(['host', 'account', 'status', 'consents', 'sites']);
    const parts = Object.entries(secret.keys || {})
      .filter(([key, value]) => !bookkeeping.has(key) && value !== '' && value !== null && value !== undefined)
      .map(([key, value]) => `${key}: ${value}`);
    const definition = this.definitions.find((d) => d.definition_id === secret.resource_id);
    for (const field of definition?.fields || []) {
      if ((field as any).type === 'secret' && !bookkeeping.has(field.name)) parts.push(`${field.name}: set`);
    }
    return parts.join(' · ') || 'no fields';
  }

  keysSummary(secret: Secret): string {
    const oauth = this.isOauth(secret);
    const parts = Object.entries(secret.keys)
      .filter(([key, value]) => value !== '' && value !== null && value !== undefined
        && !(oauth && OAUTH_FIELD_NAMES.includes(key)))
      .map(([key, value]) => `${key}: ${value}`);
    if (oauth) {
      const account = secret.keys?.['account'];
      parts.unshift(this.needsReconnect(secret)
        ? 'needs reconnecting'
        : account ? `connected as ${account}` : 'connected');
    }
    return parts.join(' · ');
  }


  // ── Handing over ────────────────────────────────────────────────────

  transferTarget: Secret | null = null;
  transferring = false;

  requestTransfer(item: Secret): void {
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
      await this.reloadSecrets();
      this.flash(`Handed over to ${person.user_name || person.email}.`);
    } finally {
      this.transferring = false;
    }
  }
}
