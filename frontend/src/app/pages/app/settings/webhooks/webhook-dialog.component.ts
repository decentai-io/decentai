import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

import { ModelChoice } from 'src/app/components/model-select/model-select.component';
import { Choice } from 'src/app/components/ui/radio-keys';
import { AuthService } from 'src/app/services/auth.service';
import { LlmConnection, SettingsLlmService } from 'src/app/services/settings-llm.service';
import {
  Webhook, WebhookDraft, WebhookField, WebhooksService,
} from 'src/app/services/webhooks.service';
import { ChatModel } from '../../ai/chat/chat-model-picker/chat-model-picker.component';
import { TRUST_CHOICES } from '../../ai/chat/chat-defaults';
import {
  FIELD_TYPES, HMAC_LINE, copyText, curlExample, fieldNameProblem,
} from './webhooks-shared';

/** What the dialog is opened with: nothing to make one; a webhook to
 *  change it; a webhook and its new secret to show the secret once. */
export interface WebhookDialogData {
  webhook?: Webhook;
  secret?: string;
}

/**
 * Making or changing a webhook in, and — after it is made or its
 * secret replaced — the one moment its secret is in the open, with its
 * address and a call to paste.
 *
 * The chat's trust level and model are the chat's own settings, chosen
 * with the same controls as Chat defaults; left alone, a new webhook's
 * chat starts with the person's defaults. The dialog closes with the
 * webhook as it now is, or nothing when nothing changed.
 *
 * Once the secret is shown the dialog is not dismissed by Escape or a
 * click outside, and Done asks twice if the secret was not copied, as
 * an API key's does.
 */
@Component({
  selector: 'app-webhook-dialog',
  standalone: false,
  templateUrl: './webhook-dialog.component.html',
  styleUrl: './webhooks.component.css',
})
export class WebhookDialogComponent implements OnInit {
  readonly types = FIELD_TYPES;
  readonly trustChoices: Choice<number>[] = TRUST_CHOICES;
  readonly hmacLine = HMAC_LINE;

  /** The webhook being changed, or null while one is being made. */
  readonly editing: Webhook | null;

  name = '';
  prompt = '';
  fields: WebhookField[] = [];
  requireSignature = false;
  /** Null: the person's chat default, left unsaid. */
  trustLevel: number | null = null;
  connections: LlmConnection[] = [];
  connectionId = '';
  model = '';

  /** Save was pressed: what is missing is said under its field. */
  tried = false;
  saving = false;
  error = '';

  /** The webhook and its secret, once made or replaced. */
  made: Webhook | null = null;
  secret = '';
  copied: Record<string, boolean> = {};
  copyFailed = false;
  /** The secret, alone or in the call, was copied. */
  secretCopied = false;
  /** Done was pressed without the secret having been copied. */
  warned = false;

  constructor(
    public dialog: MatDialogRef<WebhookDialogComponent, Webhook | null>,
    @Inject(MAT_DIALOG_DATA) data: WebhookDialogData | null,
    private service: WebhooksService,
    private llm: SettingsLlmService,
    private auth: AuthService,
  ) {
    this.editing = data?.webhook && !data.secret ? data.webhook : null;
    if (data?.webhook && data.secret) this.show(data.webhook, data.secret);
    const from = this.editing;
    if (from) {
      this.name = from.name;
      this.prompt = from.prompt;
      this.fields = (from.fields || []).map((field) => ({ ...field }));
      this.requireSignature = from.require_signature;
      this.trustLevel = Number.isInteger(from.chat?.trust_level) ? from.chat.trust_level as number : null;
      this.connectionId = from.chat?.llm?.secret_ref || '';
      this.model = from.chat?.llm?.model || '';
    }
  }

  async ngOnInit(): Promise<void> {
    if (this.made || !this.canChooseModel) return;
    try {
      this.connections = await this.llm.list();
    } catch {
      this.connections = [];
    }
  }

  /** The model is chosen among providers, which not everyone may list. */
  get canChooseModel(): boolean {
    return this.auth.can('settings:llm:list');
  }

  /** A deleted chat has no settings left to change. */
  get chatGone(): boolean {
    return !!this.editing && this.editing.chat?.exists === false;
  }

  // ── Fields ──────────────────────────────────────────────────────────

  addField(): void {
    this.fields = [...this.fields, { name: '', type: 'text', required: false, description: '' }];
  }

  removeField(index: number): void {
    this.fields = this.fields.filter((_, at) => at !== index);
  }

  fieldProblem(field: WebhookField): string {
    return this.tried ? fieldNameProblem(field.name, this.fields) : '';
  }

  // ── The model ───────────────────────────────────────────────────────

  chooseModel(choice: ModelChoice): void {
    this.connectionId = choice.connectionId;
    this.model = choice.model;
  }

  /** The chosen model as the chat's `llm` block, or null for none. */
  private modelBlock(): ChatModel | null {
    const connection = this.connections.find((one) => one.resource_ref === this.connectionId);
    if (!connection || !this.model) return null;
    const block: ChatModel = { provider: connection.keys.provider, model: this.model, secret_ref: connection.resource_ref };
    if (connection.keys.endpoint) block.endpoint = connection.keys.endpoint;
    return block;
  }

  get modelProblem(): string {
    return this.tried && this.connectionId && !this.model ? 'Choose one of its models.' : '';
  }

  // ── Saving ──────────────────────────────────────────────────────────

  get valid(): boolean {
    return !!this.name.trim() && !!this.prompt.trim() && !this.modelProblem
      && this.fields.every((field) => !fieldNameProblem(field.name, this.fields));
  }

  /** What is sent: everything for a new one; what changed for one being edited. */
  draft(): WebhookDraft {
    const fields = this.fields.map((field) => ({
      ...field, name: field.name.trim(), description: field.description.trim(),
    }));
    const all: WebhookDraft = {
      name: this.name.trim(), prompt: this.prompt.trim(), fields, require_signature: this.requireSignature,
    };
    const llm = this.modelBlock();
    const from = this.editing;
    if (!from) {
      if (this.trustLevel !== null) all.trust_level = this.trustLevel;
      if (llm) all.llm = llm;
      return all;
    }
    const changes: WebhookDraft = {};
    if (all.name !== from.name) changes.name = all.name;
    if (all.prompt !== from.prompt) changes.prompt = all.prompt;
    if (JSON.stringify(fields) !== JSON.stringify(from.fields || [])) changes.fields = fields;
    if (all.require_signature !== from.require_signature) changes.require_signature = all.require_signature;
    if (!this.chatGone) {
      if (this.trustLevel !== null && this.trustLevel !== from.chat?.trust_level) changes.trust_level = this.trustLevel;
      const was = from.chat?.llm;
      if (llm && (llm.secret_ref !== was?.secret_ref || llm.model !== was?.model)) changes.llm = llm;
    }
    return changes;
  }

  async save(): Promise<void> {
    if (this.saving) return;
    this.tried = true;
    this.error = '';
    if (!this.valid) return;
    const draft = this.draft();
    if (this.editing && !Object.keys(draft).length) {
      this.dialog.close(null);
      return;
    }
    this.saving = true;
    this.dialog.disableClose = true;
    try {
      if (this.editing) {
        const result = await this.service.update(this.editing.webhook_id, draft);
        if (result.error || !result.webhook) {
          this.error = result.error || 'The webhook could not be saved.';
          return;
        }
        this.dialog.close(result.webhook);
      } else {
        const result = await this.service.create(draft);
        if (result.error || !result.webhook || !result.secret) {
          this.error = result.error || 'The webhook could not be made.';
          return;
        }
        this.show(result.webhook, result.secret);
      }
    } finally {
      this.saving = false;
      if (!this.made) this.dialog.disableClose = false;
    }
  }

  // ── Shown once ──────────────────────────────────────────────────────

  private show(webhook: Webhook, secret: string): void {
    this.made = webhook;
    this.secret = secret;
    this.dialog.disableClose = true;
  }

  /** The whole address a caller posts to. */
  get url(): string {
    return this.made ? `${window.location.origin}${this.made.url_path}` : '';
  }

  get curl(): string {
    return this.made ? curlExample(this.url, this.secret, this.made.fields || [], this.made.require_signature) : '';
  }

  async copy(what: 'url' | 'secret' | 'curl'): Promise<void> {
    const text = what === 'url' ? this.url : what === 'secret' ? this.secret : this.curl;
    if (!(await copyText(text))) {
      this.copyFailed = true;
      return;
    }
    this.copyFailed = false;
    this.copied = { [what]: true };
    // The call carries the secret, so copying it is copying the secret.
    if (what !== 'url') {
      this.secretCopied = true;
      this.warned = false;
    }
  }

  done(): void {
    if (!this.secretCopied && !this.warned) {
      this.warned = true;
      return;
    }
    this.dialog.close(this.made);
  }
}
