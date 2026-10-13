import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

import { Choice } from 'src/app/components/ui/radio-keys';
import { AgentsService, LendableAgent, LendableSkipped } from 'src/app/services/agents.service';
import { OauthConnectService } from 'src/app/services/oauth.service';
import { Profile } from 'src/app/services/profile.service';
import {
  DefinitionField, SecretDefinition, SecretDefinitionsService,
} from 'src/app/services/secret-definitions.service';
import { Secret, SecretsService } from 'src/app/services/secrets.service';
import { Peer, ShareEditor } from '../share-editor';
import { agentLabel, editableFields, isOutdated, providerName } from './secret-shared';

/** What the page hands the dialog. */
export interface SecretFormData {
  definitions: SecretDefinition[];
  profile: Profile | null;
  peers: Peer[];
  canShareAny: boolean;
  /** The secret to change; absent to add one. */
  secret?: Secret;
  /** Open on lending this secret, and nothing else. */
  lend?: Secret;
  /** Add one of this shape, with no choosing: an agent's own page
   *  knows which shape its slot takes, and at which version. */
  definition?: SecretDefinition;
}

/** What it closes with when something was done: what the page says. */
export interface SecretFormResult {
  notice: string;
  /** The secret made or changed, to open it. */
  ref?: string;
}

/** A group of shapes in the first step: the agent they are for. */
interface DefinitionGroup {
  agent: string;
  items: SecretDefinition[];
}

/**
 * Adding a secret, changing one, and lending one to agents: one dialog.
 *
 * Adding is two steps. First, what it is for: the shapes installed
 * agents declare, grouped by agent, each saying whether it is filled in
 * or signed in to (and then with which scopes, before the sign-in).
 * Then the form generated from the shape, or the provider's sign-in in
 * a window of its own, followed by the offer to lend the new account to
 * the other agents that could use it.
 *
 * Encrypted fields are write-only: typed here and never shown back, so
 * on a change a blank one means "keep what is stored".
 */
@Component({
  selector: 'app-secret-form',
  standalone: false,
  templateUrl: './secret-form.component.html',
  styleUrl: './secret-form.component.css',
})
export class SecretFormComponent implements OnInit {
  step: 'pick' | 'form' | 'signin' | 'lend' = 'pick';
  loading = false;

  // ── What is it for? ─────────────────────────────────────────────────
  definitionQuery = '';

  // ── The form ────────────────────────────────────────────────────────
  definition: SecretDefinition | null = null;
  /** The secret being changed; null while one is added. */
  editing: Secret | null = null;
  /** The change moves the secret onto its shape's current version. */
  migrating = false;
  name = '';
  fields: Record<string, any> = {};
  readonly share: ShareEditor;
  private sharedAtOpen = '';
  /** Save was pressed: what is missing is said under its field. */
  tried = false;
  saving = false;
  problem = '';

  // ── Signing in, and lending what it made ────────────────────────────
  /** Why the sign-in did not finish; '' while it is on its way. */
  signinProblem = '';
  lendRef = '';
  lendable: (LendableAgent & { picked: boolean })[] = [];
  skipped: LendableSkipped[] = [];
  /** What to say once the dialog closes after a sign-in. */
  private connected = '';

  private choicesByField = new WeakMap<DefinitionField, Choice[]>();

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: SecretFormData,
    private dialogRef: MatDialogRef<SecretFormComponent, SecretFormResult | null>,
    private service: SecretsService,
    private definitionsService: SecretDefinitionsService,
    private oauth: OauthConnectService,
    private agents: AgentsService,
  ) {
    this.share = new ShareEditor(() => this.data.profile);
    this.share.peers = data.peers;
  }

  async ngOnInit(): Promise<void> {
    if (this.data.lend) return this.startLend(this.data.lend.resource_ref);
    if (this.data.secret) return this.startEdit(this.data.secret);
    this.share.open('new', undefined);
    if (this.data.definition) this.choose(this.data.definition);
  }

  get myUserId(): string {
    return this.data.profile?.user_id ?? '';
  }

  // ── Step one: which shape ───────────────────────────────────────────

  /** The shapes that match the search, grouped by the agent whose slot
   *  each is: the question is "a secret for WHICH agent?", and a slug
   *  answers nobody. Shapes no agent uses any more come last. */
  get groups(): DefinitionGroup[] {
    const query = this.definitionQuery.trim().toLowerCase();
    const groups = new Map<string, SecretDefinition[]>();
    for (const definition of this.data.definitions) {
      const agent = (definition.agent_uses || []).map(agentLabel).join(', ');
      if (query && !`${definition.label} ${definition.description} ${agent}`.toLowerCase().includes(query)) continue;
      groups.set(agent, [...(groups.get(agent) || []), definition]);
    }
    return [...groups.entries()]
      .map(([agent, items]) => ({ agent, items: items.sort((a, b) => a.label.localeCompare(b.label)) }))
      .sort((a, b) => (!a.agent ? 1 : !b.agent ? -1 : a.agent.localeCompare(b.agent)));
  }

  /** What choosing a shape leads to, said on its row. */
  aboutOf(definition: SecretDefinition): string {
    if (definition.oauth) {
      const scopes = definition.oauth.scopes || [];
      return `Sign in with ${providerName(definition)}`
        + (scopes.length ? ` · asks for ${scopes.join(', ')}` : '');
    }
    const count = editableFields(definition).length;
    return `${count} ${count === 1 ? 'field' : 'fields'}`;
  }

  choose(definition: SecretDefinition): void {
    this.definition = definition;
    this.problem = '';
    if (definition.oauth) {
      void this.signIn();
      return;
    }
    this.name = definition.label;
    this.fields = {};
    for (const field of definition.fields) this.fields[field.name] = field.type === 'boolean' ? false : '';
    this.tried = false;
    this.step = 'form';
  }

  backToPick(): void {
    if (this.saving) return;
    this.step = 'pick';
    this.definition = null;
  }

  // ── Changing one ────────────────────────────────────────────────────

  private async startEdit(secret: Secret): Promise<void> {
    this.editing = secret;
    this.step = 'form';
    this.loading = true;
    // The form matches the version the secret was made under, or, when
    // the agent has moved on, the CURRENT one, and saving migrates:
    // stored fields carry over, dropped ones go, new required ones are
    // asked for right here.
    this.migrating = isOutdated(secret, this.data.definitions);
    const result = await this.definitionsService.get(this.migrating
      ? { definition_id: secret.resource_id }
      : { definition_ref: secret.definition_ref });
    this.loading = false;
    if (!result.definition) {
      this.problem = result.error || 'The shape of this secret could not be read.';
      return;
    }
    this.definition = result.definition;
    this.name = secret.name;
    this.fields = {};
    for (const field of result.definition.fields) {
      this.fields[field.name] = field.storage === 'values'
        ? '' // write-only: blank means keep
        : secret.keys[field.name] ?? (field.type === 'boolean' ? false : '');
    }
    this.share.open(secret.resource_ref, secret.owner);
    this.sharedAtOpen = this.sharedNow;
  }

  private get sharedNow(): string {
    return JSON.stringify([this.share.mode, [...this.share.groups].sort(), [...this.share.users].sort()]);
  }

  // ── The form ────────────────────────────────────────────────────────

  get formFields(): DefinitionField[] {
    return this.definition ? editableFields(this.definition) : [];
  }

  isEncrypted(field: DefinitionField): boolean {
    return field.storage === 'values';
  }

  optionsOf(field: DefinitionField): Choice[] {
    let choices = this.choicesByField.get(field);
    if (!choices) {
      choices = (field.options || []).map((option) => ({ value: option, name: option }));
      this.choicesByField.set(field, choices);
    }
    return choices;
  }

  helpOf(field: DefinitionField): string {
    if (field.help) return field.help;
    if (!this.isEncrypted(field)) return '';
    return this.editing ? 'Stored and never shown. Left empty, it is kept.' : 'Encrypted when saved, and never shown again.';
  }

  /** Whether a blank box means "I did not touch this" rather than
   *  "make this empty". On a change that is true only of an ENCRYPTED
   *  field: the form cannot show a stored value back, so it opens blank
   *  and blank has to mean keep. A plaintext field is on screen with
   *  its value in it, and clearing it is a deliberate edit. On adding
   *  there is nothing to keep: a blank optional field is not sent. */
  private blankIsNotAnEdit(field: DefinitionField): boolean {
    return this.editing ? this.isEncrypted(field) : !field.required;
  }

  /** What a field still needs; '' when nothing. */
  missing(field: DefinitionField): string {
    if (field.type === 'boolean' || !field.required) return '';
    const value = this.fields[field.name];
    const blank = value === '' || value === null || value === undefined;
    if (!blank) return '';
    // A stored encrypted value satisfies a required field on a change,
    // unless the change moves the secret onto a shape that adds it.
    if (this.editing && this.isEncrypted(field) && !this.migrating) return '';
    return field.type === 'select' ? 'Choose one.' : `Enter ${field.label || field.name}.`;
  }

  get shareMissing(): string {
    if (this.share.ready) return '';
    return this.share.mode === 'groups' ? 'Tick at least one group.' : 'Tick at least one person.';
  }

  /** Who sees it, in a word, for the fold that holds the choice. */
  get shareWord(): string {
    switch (this.share.mode) {
      case 'org': return 'Everyone';
      case 'groups': return this.share.groups.size === 1 ? '1 group' : `${this.share.groups.size} groups`;
      case 'people': return this.share.users.size === 1 ? '1 person' : `${this.share.users.size} people`;
      default: return 'Only me';
    }
  }

  private builtFields(): Record<string, any> {
    const built: Record<string, any> = {};
    for (const field of this.formFields) {
      let value = this.fields[field.name];
      if (field.type === 'number') {
        if (value === '' || value === null || value === undefined) continue;
        value = Number(value);
      }
      if (value === '' && this.blankIsNotAnEdit(field)) continue;
      built[field.name] = value;
    }
    return built;
  }

  async save(): Promise<void> {
    const definition = this.definition;
    if (!definition || this.saving) return;
    this.tried = true;
    this.problem = '';
    if (!this.name.trim() || this.shareMissing || this.formFields.some((field) => this.missing(field))) return;

    this.saving = true;
    this.dialogRef.disableClose = true;
    try {
      const result = this.editing
        ? await this.service.update(this.editing.resource_ref, {
            name: this.name.trim(),
            fields: this.builtFields(),
            // Sent only when it changed: somebody maintaining a
            // colleague's secret must not rewrite who owns it by
            // correcting its name.
            ...(this.sharedNow !== this.sharedAtOpen ? { owner: this.share.owner() as any } : {}),
            ...(this.migrating ? { migrate: true } : {}),
          })
        : await this.service.create({
            definition_ref: definition.definition_ref,
            name: this.name.trim(),
            fields: this.builtFields(),
            owner: this.share.owner() as any,
          });
      if (result.error) {
        this.problem = result.error + (result.ungrantable?.length ? ` (${result.ungrantable.join(', ')})` : '');
        return;
      }
      this.dialogRef.close({
        notice: this.editing ? 'Secret updated.' : 'Secret created.',
        ref: result.resource?.resource_ref || this.editing?.resource_ref,
      });
    } finally {
      this.saving = false;
      this.dialogRef.disableClose = false;
    }
  }

  // ── Signing in with a provider ──────────────────────────────────────

  get provider(): string {
    return providerName(this.definition);
  }

  /** The provider's consent opens in a window of its own; the secret
   *  is made on the way back. A window the browser blocked, or a
   *  refusal, is said here with a way to try again. */
  async signIn(): Promise<void> {
    const definition = this.definition;
    if (!definition) return;
    this.step = 'signin';
    this.signinProblem = '';
    this.saving = true;
    const result = await this.oauth.connect({ definition_ref: definition.definition_ref });
    this.saving = false;
    if (result.error || !result.resource_ref) {
      this.signinProblem = result.error || 'The sign-in did not finish.';
      return;
    }
    this.connected = `${definition.label} connected.`;
    await this.startLend(result.resource_ref, true);
  }

  // ── Lending it to the agents that could use it ──────────────────────

  /** A secret belongs to one agent's slot and reaches no other. The
   *  ones it could be lent to are the backend's to say, and so are the
   *  ones it could not, each with the reason. */
  private async startLend(ref: string, afterSignIn = false): Promise<void> {
    this.lendRef = ref;
    this.loading = true;
    this.step = 'lend';
    try {
      const found = await this.agents.lendable(ref);
      this.lendable = found.eligible.map((agent) => ({ ...agent, picked: afterSignIn }));
      this.skipped = found.skipped;
    } catch {
      this.lendable = [];
      this.skipped = [];
    }
    this.loading = false;
    // Nothing to offer after a sign-in: the dialog has nothing to ask.
    if (afterSignIn && !this.lendable.length) this.dialogRef.close({ notice: this.connected, ref });
  }

  get pickedCount(): number {
    return this.lendable.filter((agent) => agent.picked).length;
  }

  async lend(): Promise<void> {
    const chosen = this.lendable.filter((agent) => agent.picked)
      .map((agent) => ({ agent_id: agent.agent_id, resource_id: agent.resource_id }));
    if (!chosen.length || this.saving) return;
    this.saving = true;
    this.problem = '';
    try {
      const result = await this.agents.lendMany(this.lendRef, chosen);
      if (result.error) {
        this.problem = result.error;
        return;
      }
      const names = (result.data?.granted || []).map((granted: { name: string }) => granted.name).join(', ');
      const failed = result.data?.failed;
      if (failed) {
        const who = this.lendable.find((agent) => agent.agent_id === failed.agent_id)?.name || failed.agent_id;
        this.problem = `${names ? `Lent to ${names}. ` : ''}Could not lend to ${who}: ${failed.error}`;
        this.lendable = this.lendable.filter((agent) => !(result.data?.granted || [])
          .some((granted: { agent_id: string }) => granted.agent_id === agent.agent_id));
        return;
      }
      this.dialogRef.close({ notice: `${this.connected ? this.connected + ' ' : ''}Lent to ${names}.`, ref: this.lendRef });
    } finally {
      this.saving = false;
    }
  }

  /** Not now: what the sign-in made is still made. */
  close(): void {
    if (this.saving && this.step !== 'signin') return;
    this.dialogRef.close(this.connected ? { notice: this.connected, ref: this.lendRef } : null);
  }
}
