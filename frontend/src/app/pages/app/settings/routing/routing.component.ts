import { Component, OnInit } from '@angular/core';

import { ModelChoice } from 'src/app/components/model-select/model-select.component';
import { Draft } from 'src/app/components/ui/draft';
import { HasUnsavedChanges } from 'src/app/guards/unsaved-changes.guard';
import { AuthService } from 'src/app/services/auth.service';
import { LlmConnection, SettingsLlmService } from 'src/app/services/settings-llm.service';
import { RoutingSettings, SettingsRoutingService } from 'src/app/services/settings-routing.service';

/** A whole number a routing field takes, with the range it must be in. */
interface NumberField {
  key: 'threshold' | 'shortlist' | 'candidates' | 'open_max';
  label: string;
  help: string;
  unit: string;
  min: number;
  max: number;
}

/** What the form shows before the settings are read. */
const BLANK: RoutingSettings = {
  embedding_connection_id: '', embedding_model: '',
  threshold: 15, shortlist: 15, candidates: 50, rerank: true, open_max: 8,
};

/**
 * Settings › Agent routing: for the whole organization, how a chat
 * finds the right agent when many are installed. An embedding model,
 * four numbers and whether the chat's model reranks. One form, saved
 * with the save bar.
 */
@Component({
  selector: 'app-routing',
  standalone: false,
  templateUrl: './routing.component.html',
})
export class RoutingComponent implements OnInit, HasUnsavedChanges {
  loading = true;
  loadError = '';
  saving = false;
  saveError = '';

  readonly draft = new Draft<RoutingSettings>(BLANK);
  /** The providers shared with the whole organization: every chat
   *  routes with what is chosen here, so only those may be. */
  shared: LlmConnection[] = [];

  readonly numbers: NumberField[] = [
    { key: 'threshold', label: 'Route above', help: 'Enabled agents in a chat before routing starts.', unit: 'agents', min: 1, max: 1000 },
    { key: 'shortlist', label: 'Agents listed', help: 'Shown to the assistant for each message.', unit: 'agents', min: 1, max: 100 },
    { key: 'candidates', label: 'Candidates reranked', help: 'Closest agents the chat\'s model chooses among.', unit: 'agents', min: 1, max: 500 },
    { key: 'open_max', label: 'Agents kept open', help: 'In a chat; the least recently used closes to make room.', unit: 'agents', min: 1, max: 30 },
  ];

  constructor(
    private service: SettingsRoutingService,
    private llm: SettingsLlmService,
    private auth: AuthService,
  ) {}

  get canEdit(): boolean {
    return this.auth.can('settings:routing:update');
  }

  ngOnInit(): Promise<void> {
    return this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      const [answer, connections] = await Promise.all([
        this.service.get(),
        this.auth.can('settings:llm:list') ? this.llm.list() : Promise.resolve([]),
      ]);
      if (!answer?.routing) throw new Error('no settings');
      this.shared = connections.filter((connection) =>
        (connection.owner?.groups || []).includes('everyone'));
      this.draft.take(answer.routing);
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  chooseEmbedding(choice: ModelChoice): void {
    this.draft.value.embedding_connection_id = choice.connectionId;
    this.draft.value.embedding_model = choice.model;
  }

  // ── What is wrong, under the field it belongs to ────────────────────

  /** A provider alone does not say which of its models embeds. */
  get embeddingError(): string {
    const routing = this.draft.value;
    return routing.embedding_connection_id && !routing.embedding_model
      ? 'Choose the embedding model.' : '';
  }

  /** Why a number cannot be saved, or ''. */
  numberError(field: NumberField): string {
    const value = this.draft.value[field.key];
    return Number.isInteger(value) && value >= field.min && value <= field.max
      ? '' : `A whole number from ${field.min} to ${field.max}.`;
  }

  /** The first thing in the way of saving, or ''. */
  get blocker(): string {
    const number = this.numbers.find((field) => this.numberError(field));
    return this.embeddingError || (number ? `${number.label}: ${this.numberError(number).toLowerCase()}` : '');
  }

  // ── Saving ──────────────────────────────────────────────────────────

  async save(): Promise<void> {
    if (this.saving) return;
    if (this.blocker) {
      this.saveError = this.blocker;
      return;
    }
    this.saving = true;
    this.saveError = '';
    try {
      const result = await this.service.update(this.draft.value);
      if (result.error) {
        this.saveError = result.error;
        return;
      }
      this.draft.take(result.routing ?? this.draft.value);
    } finally {
      this.saving = false;
    }
  }

  discard(): void {
    this.draft.discard();
    this.saveError = '';
  }

  unsavedChangesTo(): string {
    return this.draft.dirty ? 'Agent routing' : '';
  }
}
