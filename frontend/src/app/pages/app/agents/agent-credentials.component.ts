import {
  Component, EventEmitter, Input, OnChanges, Output, SimpleChanges,
} from '@angular/core';

import {
  AgentOffer, AgentSecretSlot, AgentsService, LendableAgent,
} from 'src/app/services/agents.service';
import { Group } from 'src/app/services/group.service';
import { OauthConnectService } from 'src/app/services/oauth.service';
import { Secret, SecretsService } from 'src/app/services/secrets.service';
import {
  DefinitionField, SecretDefinition, SecretDefinitionsService,
} from 'src/app/services/secret-definitions.service';
import { AgentCredential } from './agent-shared';

/**
 * The credentials an approved agent needs, filled on the agent itself.
 *
 * An approved agent with no credential is installed and unable to do the
 * thing it was installed for, so this is where that gets fixed — not in
 * a secrets page somebody has to know to visit.
 *
 * Values are write-only: encrypted on arrival and never shown back. What
 * is listed is what exists, who can reach it, and which one answers when
 * a chat has not chosen.
 *
 * Two ways to give this agent a credential, because there are two real
 * situations. Fill one in here, which is private to whoever created it
 * and belongs to this agent alone; or hand it one you already have,
 * which is a GRANT — offered only where the shapes match exactly, and
 * revocable. The manifest declares what shape the agent expects; it
 * never decides which credential answers.
 */
@Component({
  selector: 'app-agent-credentials',
  standalone: false,
  templateUrl: './agent-credentials.component.html',
  styleUrls: [
    '../data-shared.css',
    './agent-shared.css',
    './agents.component.css',
  ],
})
export class AgentCredentialsComponent implements OnChanges {
  @Input() agent!: AgentOffer;
  @Input() credentials: AgentCredential[] = [];
  @Input() groups: Group[] = [];
  /** The groups the person belongs to — the reach sharing may have. */
  @Input() myGroups: { group_id: string; group_name: string }[] = [];
  @Input() canShareAny = false;
  @Input() myUserId = '';
  /** family -> the resource_ref this person marked as their default. */
  @Input() defaults: Record<string, string> = {};

  /** A credential was created or deleted: the list page's counts, and
   *  this agent's readiness, both change. */
  @Output() changed = new EventEmitter<void>();
  @Output() failed = new EventEmitter<string>();
  @Output() flashed = new EventEmitter<string>();

  /** The exact definition version each family pins, and what exists. */
  private definitionsByFamily = new Map<string, SecretDefinition>();
  private secretsByFamily = new Map<string, Secret[]>();

  formFamily = '';
  name = '';
  values: Record<string, string> = {};
  shareMode: 'private' | 'groups' | 'org' = 'private';
  selectedGroups = new Set<string>();
  saving = false;
  busyRef = '';

  /** What this agent may use, and what it could be given, by slot. */
  private slotsById = new Map<string, AgentSecretSlot>();
  /** Which slot's "use one I already have" picker is open. */
  pickerFor = '';
  picked = '';

  /** Offered once, right after a Connect: the other installed agents of
   *  the same provider this account could reach. Ticked by default —
   *  the person just proved they hold the account, and the alternative
   *  is doing this once per agent. Nothing happens until they click. */
  lendOffer: {
    secretRef: string; provider: string;
    agents: (LendableAgent & { picked: boolean })[];
  } | null = null;
  lending = false;

  readonly EVERYONE = 'everyone';

  /** One empty array, shared.
   *
   *  `?? []` would mint a new one on every call, and an *ngFor reading a
   *  new array each change-detection pass re-renders, which triggers
   *  another pass, which freezes the tab. */
  private static readonly NONE: any[] = [];

  constructor(
    private secrets: SecretsService,
    private definitionsService: SecretDefinitionsService,
    private agents: AgentsService,
    private oauth: OauthConnectService,
  ) {}

  // ── Connected accounts ──────────────────────────────────────────────

  /** Whether this slot is a sign-in rather than a form. */
  isOauth(family: string): boolean {
    return !!this.definitionsByFamily.get(family)?.oauth;
  }

  providerOf(family: string): string {
    const id = this.definitionsByFamily.get(family)?.oauth?.provider || '';
    return id ? id.charAt(0).toUpperCase() + id.slice(1) : 'the provider';
  }

  needsReconnect(secret: Secret): boolean {
    return secret.keys?.['status'] === 'needs_reconnect';
  }

  /** Sign in with the provider; the credential is made on the way back,
   *  shared the way the form below chose. */
  async connect(credential: AgentCredential): Promise<void> {
    const pinned = this.agent.resource_refs?.secrets?.[credential.id];
    if (!pinned) return;
    this.saving = true;
    try {
      const result = await this.oauth.connect({
        definition_ref: String(pinned), owner: this.buildOwner(),
      });
      if (result.error) return this.failed.emit(result.error);
      this.formFamily = '';
      await this.reload(credential.family);
      this.changed.emit();
      this.flashed.emit(`${credential.label} connected. This agent can use it now.`);
      if (result.resource_ref) {
        await this.offerLend(result.resource_ref, this.providerOf(credential.family));
      }
    } finally {
      this.saving = false;
    }
  }

  // ── Lending the account on to the other agents that use it ─────────

  private async offerLend(secretRef: string, provider: string): Promise<void> {
    try {
      const found = await this.agents.lendable(secretRef);
      if (!found.eligible.length) return;
      this.lendOffer = {
        secretRef, provider,
        agents: found.eligible.map((agent) => ({ ...agent, picked: true })),
      };
    } catch {
      // A reader without the lend action just saw the flash; nothing
      // is lost, the picker on each agent still exists.
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
    try {
      const result = await this.agents.lendMany(offer.secretRef, chosen);
      if (result.error) return this.failed.emit(result.error);
      const granted: { name: string }[] = result.data?.granted || [];
      const failed = result.data?.failed;
      this.lendOffer = null;
      this.changed.emit();
      const names = granted.map((g) => g.name).join(', ');
      if (failed) {
        this.failed.emit(
          `${names ? `Lent to ${names}. ` : ''}Could not lend to `
          + `${offer.agents.find((a) => a.agent_id === failed.agent_id)?.name || failed.agent_id}: `
          + `${failed.error}`);
      } else {
        this.flashed.emit(`Lent to ${names}.`);
      }
    } finally {
      this.lending = false;
    }
  }

  async reconnect(credential: AgentCredential, secret: Secret): Promise<void> {
    this.busyRef = secret.resource_ref;
    try {
      const result = await this.oauth.connect({ resource_ref: secret.resource_ref });
      if (result.error) return this.failed.emit(result.error);
      await this.reload(credential.family);
      this.flashed.emit(`${secret.name} reconnected.`);
    } finally {
      this.busyRef = '';
    }
  }

  async ngOnChanges(changes: SimpleChanges): Promise<void> {
    // The groups list arrives a moment after the agent does; re-reading
    // every secret because a label source turned up is a wasted round
    // trip per credential.
    if (!changes['agent'] && !changes['credentials']) return;
    // An update may pin other versions of the shapes: the forms are
    // read again for the versions the agent approves now.
    if (changes['agent']) this.definitionsByFamily.clear();
    await this.loadAll();
  }

  private async loadAll(): Promise<void> {
    for (const credential of this.credentials) {
      const pinned = this.agent.resource_refs?.secrets?.[credential.id];
      if (pinned && !this.definitionsByFamily.has(credential.family)) {
        // The VERSION the agent approved, not the latest: the form must
        // ask for the fields the backend will validate against.
        const result = await this.definitionsService.get(
          { definition_ref: String(pinned) });
        if (result.definition) {
          this.definitionsByFamily.set(credential.family, result.definition);
        }
      }
      await this.reload(credential.family);
    }
    await this.loadSlots();
  }

  private async reload(family: string): Promise<void> {
    try {
      this.secretsByFamily.set(family, await this.secrets.list(family));
    } catch {
      // A reader without the secrets grant sees the states, not the list.
    }
  }

  /** What this agent has been handed, and what it could be. One call —
   *  the backend decides which of the caller's credentials fit, because
   *  matching a shape is its rule to apply, not the page's to guess. */
  private async loadSlots(): Promise<void> {
    if (!this.agent?.agent_id) return;
    try {
      const slots = await this.agents.secretSlots(this.agent.agent_id);
      this.slotsById = new Map(slots.map((slot) => [slot.resource_id, slot]));
    } catch {
      // A reader without the grant action simply sees no picker.
    }
  }

  // ── Handing over one you already have ───────────────────────────────

  slot(credential: AgentCredential): AgentSecretSlot | null {
    return this.slotsById.get(credential.id) ?? null;
  }

  /** Credentials of exactly this shape that the caller can see. */
  candidates(credential: AgentCredential) {
    return this.slot(credential)?.candidates ?? AgentCredentialsComponent.NONE;
  }

  /** The lent credential, by the name its grant carries. One that no
   *  longer fits is not among the candidates, so the list cannot be
   *  what names it. */
  grantedName(credential: AgentCredential): string {
    const slot = this.slot(credential);
    if (!slot?.grant) return '';
    const match = slot.candidates.find(
      (candidate) => candidate.resource_ref === slot.grant!.secret_ref);
    return slot.grant.secret_name || match?.name || 'a credential';
  }

  openPicker(credential: AgentCredential): void {
    this.pickerFor = credential.id;
    this.picked = this.slot(credential)?.grant?.secret_ref || '';
  }

  closePicker(): void {
    this.pickerFor = '';
    this.picked = '';
  }

  async grant(credential: AgentCredential): Promise<void> {
    if (!this.picked) return;
    this.saving = true;
    try {
      const result = await this.agents.grantSecret(
        this.agent.agent_id, credential.id, this.picked);
      if (result.error) return this.failed.emit(result.error);
      this.closePicker();
      await this.loadSlots();
      this.changed.emit();
      this.flashed.emit(
        `${this.agent.name || 'This agent'} may now use that credential.`);
    } finally {
      this.saving = false;
    }
  }

  async revokeGrant(credential: AgentCredential): Promise<void> {
    const grant = this.slot(credential)?.grant;
    if (!grant) return;
    this.busyRef = grant.grant_id;
    try {
      const result = await this.agents.revokeSecret(
        this.agent.agent_id, grant.grant_id);
      if (result.error) return this.failed.emit(result.error);
      await this.loadSlots();
      this.changed.emit();
      this.flashed.emit('Credential taken back.');
    } finally {
      this.busyRef = '';
    }
  }

  // Identity for the loops, so a re-render never means re-creation.
  byFamily(_: number, credential: AgentCredential): string { return credential.family; }
  byRef(_: number, secret: Secret): string { return secret.resource_ref; }
  byFieldName(_: number, field: DefinitionField): string { return field.name; }

  savedSecrets(family: string): Secret[] {
    return this.secretsByFamily.get(family) ?? AgentCredentialsComponent.NONE;
  }

  fieldsFor(family: string): DefinitionField[] {
    return this.definitionsByFamily.get(family)?.fields
      ?? AgentCredentialsComponent.NONE;
  }

  descriptionFor(id: string): string {
    const declared = this.agent.resources?.secrets || [];
    return (declared.find((r: any) => r.id === id) as any)?.description || '';
  }

  // ── Who can reach a saved credential ────────────────────────────────

  ownerLabel(secret: Secret): string {
    const groups = secret.owner?.groups ?? [];
    if (groups.includes(this.EVERYONE)) return 'organization';
    if (groups.length) {
      const names = groups.map((id) =>
        this.groups.find((g) => g.group_id === id)?.group_name || 'group');
      return names.slice(0, 2).join(', ')
        + (names.length > 2 ? ` +${names.length - 2}` : '');
    }
    return 'personal';
  }

  ownerKind(secret: Secret): 'org' | 'group' | 'personal' {
    const groups = secret.owner?.groups ?? [];
    if (groups.includes(this.EVERYONE)) return 'org';
    return groups.length ? 'group' : 'personal';
  }

  // ── Which one answers ───────────────────────────────────────────────
  //
  // When a chat has not chosen a credential itself, the person's default
  // does — and without one, two credentials mean an "ambiguous" refusal.
  // The pin IS the fix for that refusal, which is why it lives here on
  // the agent rather than in a settings page nobody would find.

  isDefault(family: string, secret: Secret): boolean {
    return this.defaults[family] === secret.resource_ref;
  }

  /** Several credentials, none marked: the state that fails chats. */
  needsDefault(family: string): boolean {
    return this.savedSecrets(family).length > 1 && !this.defaults[family];
  }

  async toggleDefault(credential: AgentCredential, secret: Secret): Promise<void> {
    const clearing = this.isDefault(credential.family, secret);
    const result = await this.secrets.setDefault(
      credential.family, clearing ? '' : secret.resource_ref);
    if (result.error) return this.failed.emit(result.error);

    if (clearing) delete this.defaults[credential.family];
    else this.defaults[credential.family] = secret.resource_ref;
    this.flashed.emit(clearing
      ? `No default for ${credential.label} — chats will need it to be the `
        + `only one, or chosen explicitly.`
      : `${secret.name} is now your default ${credential.label}.`);
  }

  // ── Adding and removing ─────────────────────────────────────────────

  openForm(credential: AgentCredential): void {
    this.formFamily = credential.family;
    this.name = '';
    this.values = {};
    this.shareMode = 'private';
    this.selectedGroups.clear();
  }

  toggleGroup(groupId: string): void {
    if (!this.selectedGroups.delete(groupId)) this.selectedGroups.add(groupId);
  }

  /** Same rule as the secrets page: private is the default; sharing is
   *  a choice, and the backend re-checks the reach either way. */
  private buildOwner(): Record<string, any> {
    if (this.shareMode === 'org') return { groups: ['everyone'], users: [] };
    if (this.shareMode === 'groups') {
      return { groups: Array.from(this.selectedGroups) };
    }
    return { users: [this.myUserId] };
  }

  closeForm(): void {
    this.formFamily = '';
  }

  async save(credential: AgentCredential): Promise<void> {
    const pinned = this.agent.resource_refs?.secrets?.[credential.id];
    if (!pinned) return;

    const fields: Record<string, string> = {};
    for (const [name, value] of Object.entries(this.values)) {
      if (String(value ?? '').trim() !== '') fields[name] = value;
    }

    this.saving = true;
    try {
      const result = await this.secrets.create({
        definition_ref: String(pinned),
        name: this.name.trim() || credential.label,
        fields,
        owner: this.buildOwner(),
      });
      if (result.error) return this.failed.emit(result.error);
      this.formFamily = '';
      await this.reload(credential.family);
      this.changed.emit();
      this.flashed.emit(`${credential.label} saved. This agent can use it now.`);
    } finally {
      this.saving = false;
    }
  }

  /** The credential a delete is asked about, by ref: the row turns
   *  into the question, and nothing goes until it is answered. */
  removingRef = '';

  requestRemove(secret: Secret): void {
    this.removingRef = secret.resource_ref;
  }

  cancelRemove(): void {
    this.removingRef = '';
  }

  async remove(credential: AgentCredential, secret: Secret): Promise<void> {
    this.busyRef = secret.resource_ref;
    try {
      const result = await this.secrets.remove(secret.resource_ref);
      this.removingRef = '';
      if (result.error) return this.failed.emit(result.error);
      await this.reload(credential.family);
      this.changed.emit();
      this.flashed.emit('Credential deleted.');
    } finally {
      this.busyRef = '';
    }
  }
}
