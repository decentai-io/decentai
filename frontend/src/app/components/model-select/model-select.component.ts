import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from '@angular/core';

import {
  LlmConnection, LlmModel, LlmModelKind, SettingsLlmService,
} from 'src/app/services/settings-llm.service';

/** A provider the person has a key for, and one of its models. */
export interface ModelChoice {
  connectionId: string;
  model: string;
}

/**
 * Choosing a model: which provider, then which of its models.
 *
 * A connection is a provider and its key, so a model is always chosen
 * as the two together — for the model a new chat starts with, for the
 * one that embeds, for the one that writes speech down. `kind` says
 * which of the provider's models are offered.
 *
 * The list is an offer, never a gate: a model that is not on it — one
 * newer than the catalog, an Azure deployment by its own name — is
 * typed, by the provider's own id for it.
 */
@Component({
  selector: 'app-model-select',
  standalone: false,
  templateUrl: './model-select.component.html',
  styleUrls: ['../../pages/app/data-shared.css', './model-select.component.css'],
})
export class ModelSelectComponent implements OnChanges {
  /** The connections to choose among — already narrowed by the page
   *  to the ones that may be used here. */
  @Input() connections: LlmConnection[] = [];
  @Input() kind: LlmModelKind = 'chat';
  @Input() connectionId = '';
  @Input() model = '';
  @Input() disabled = false;
  /** What choosing no provider means here, in words. */
  @Input() noneLabel = 'None';

  @Output() changed = new EventEmitter<ModelChoice>();

  models: LlmModel[] = [];
  loading = false;
  /** The model is being typed rather than picked from the list. */
  typing = false;

  /** The select's value for "not on the list". */
  readonly other = '__other__';

  constructor(private service: SettingsLlmService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['connectionId'] || changes['kind']) void this.load(false);
  }

  /** The chosen connection's models. `choose` picks one when the
   *  connection has just been chosen by the person: the model it starts
   *  with for a chat, else the first offered. */
  private async load(choose: boolean): Promise<void> {
    const connectionId = this.connectionId;
    this.models = [];
    this.typing = false;
    if (!connectionId) return;
    this.loading = true;
    let models: LlmModel[] = [];
    try {
      models = await this.service.models(connectionId, this.kind);
    } catch {
      models = [];
    }
    // A slow answer for a connection since changed is nobody's list.
    if (this.connectionId !== connectionId) return;
    this.models = models;
    this.loading = false;
    if (choose) {
      const starting = this.connections.find(
        (c) => c.resource_ref === connectionId)?.keys.model ?? '';
      const first = this.kind === 'chat' && models.some((m) => m.id === starting)
        ? starting : models[0]?.id ?? '';
      this.model = first;
      this.changed.emit({ connectionId, model: first });
    }
    this.typing = !!this.connectionId && (!models.length
      || (!!this.model && !models.some((m) => m.id === this.model)));
  }

  chooseConnection(connectionId: string): void {
    this.connectionId = connectionId;
    this.model = '';
    if (!connectionId) {
      this.models = [];
      this.typing = false;
      this.changed.emit({ connectionId: '', model: '' });
      return;
    }
    void this.load(true);
  }

  chooseModel(model: string): void {
    if (model === this.other) {
      this.typing = true;
      this.model = '';
    } else {
      this.model = model;
    }
    this.changed.emit({ connectionId: this.connectionId, model: this.model });
  }

  typeModel(model: string): void {
    this.model = model.trim();
    this.changed.emit({ connectionId: this.connectionId, model: this.model });
  }

  /** Back from typing to the list, where there is one. */
  pickFromList(): void {
    this.typing = false;
    this.chooseModel(this.models[0]?.id ?? '');
  }
}
