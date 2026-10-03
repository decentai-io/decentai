import {
  Component, ElementRef, EventEmitter, HostListener, Input, OnChanges, Output,
  SimpleChanges, ViewChild,
} from '@angular/core';

import {
  LlmConnection, LlmModel, SettingsLlmService,
} from 'src/app/services/settings-llm.service';

/** The `llm` block a chat carries: a connection, one of its provider's
 *  models, and how hard a reasoning model is asked to think. */
export interface ChatModel {
  provider: string;
  model: string;
  secret_ref: string;
  endpoint?: string;
  reasoning_effort?: string;
}

/** One provider the person has a key for, with the models it offers. */
interface ModelGroup {
  connection: LlmConnection;
  models: LlmModel[];
}

/**
 * Which model this chat thinks with, chosen where the message is
 * written.
 *
 * A chip names the model; pressing it lists every model of every
 * provider the person has a key for — the ones picked lately first,
 * then by provider, narrowed as they type. For a model that thinks
 * before answering, how hard is chosen beside it.
 *
 * The component chooses and says so (`chosen`); saving the choice — to
 * the chat, and as what the person's next chat starts with — is the
 * page's, which knows whether there is a chat yet.
 */
@Component({
  selector: 'app-chat-model-picker',
  standalone: false,
  templateUrl: './chat-model-picker.component.html',
  styleUrls: ['./chat-model-picker.component.css'],
})
export class ChatModelPickerComponent implements OnChanges {
  /** The chat's block, or null where no model is chosen yet. */
  @Input() llm: ChatModel | null = null;
  @Input() disabled = false;

  @Output() chosen = new EventEmitter<ChatModel>();

  @ViewChild('search') search?: ElementRef<HTMLInputElement>;

  open = false;
  /** The list opens above the chip where there is room — the composer
   *  is usually at the foot of the page — and below it where there is
   *  not, as on the page a chat is started from. Never taller than the
   *  room it has. */
  below = false;
  panelHeight = 420;
  loading = false;
  /** Read once, when the list is first opened or a name is needed. */
  private loaded = false;
  groups: ModelGroup[] = [];
  query = '';

  /** Where the models picked lately are kept: this browser. */
  static readonly RECENT_KEY = 'decentai.models.recent';
  static readonly RECENT_MAX = 4;

  constructor(private service: SettingsLlmService, private host: ElementRef<HTMLElement>) {}

  ngOnChanges(changes: SimpleChanges): void {
    // The chip names the model as the provider's list does, which
    // takes the list: read once a chat has a model to name.
    if (changes['llm'] && this.llm?.secret_ref && !this.loaded) void this.load();
  }

  // ── What the chip says ──────────────────────────────────────────────

  get current(): LlmModel | null {
    return this.groups
      .find((group) => group.connection.resource_ref === this.llm?.secret_ref)
      ?.models.find((model) => model.id === this.llm?.model) ?? null;
  }

  get label(): string {
    if (!this.llm?.model) return 'Choose a model';
    return this.current?.name ?? this.llm.model;
  }

  /** How hard the chosen model may be asked to think; empty for one
   *  that does not say. */
  get efforts(): string[] {
    return this.current?.efforts ?? [];
  }

  // ── The list ────────────────────────────────────────────────────────

  private async load(): Promise<void> {
    this.loaded = true;
    this.loading = true;
    try {
      const connections = await this.service.list();
      this.groups = await Promise.all(connections.map(async (connection) => {
        let models: LlmModel[] = [];
        try {
          models = await this.service.models(connection.resource_ref, 'chat');
        } catch {
          models = [];
        }
        return { connection, models };
      }));
    } catch {
      this.groups = [];
      this.loaded = false;
    } finally {
      this.loading = false;
    }
  }

  toggle(): void {
    if (this.disabled) return;
    this.open = !this.open;
    this.query = '';
    // Read again each time it opens: a provider added in Settings a
    // moment ago is offered without reloading the page.
    if (this.open) {
      this.place();
      void this.load();
      // Typing narrows the list at once, without a click to get there.
      setTimeout(() => this.search?.nativeElement.focus());
    }
  }

  private place(): void {
    const chip = this.host.nativeElement.getBoundingClientRect();
    // Above, the page's own header takes the top of the window.
    const above = chip.top - 72;
    const under = window.innerHeight - chip.bottom - 16;
    this.below = under > above;
    this.panelHeight = Math.max(180, Math.min(420, this.below ? under : above));
  }

  close(): void {
    this.open = false;
  }

  @HostListener('document:mousedown', ['$event'])
  onOutside(event: MouseEvent): void {
    if (this.open && !this.host.nativeElement.contains(event.target as Node)) this.close();
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.close();
  }

  /** The providers and their models, narrowed by what was typed: a
   *  word matches a model's name or id, or its provider's name. */
  get visible(): ModelGroup[] {
    const words = this.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return this.groups.filter((group) => group.models.length);
    return this.groups
      .map((group) => ({
        connection: group.connection,
        models: group.models.filter((model) => {
          const text = `${model.name} ${model.id} ${group.connection.name}`.toLowerCase();
          return words.every((word) => text.includes(word));
        }),
      }))
      .filter((group) => group.models.length);
  }

  /** The models picked lately that are still offered — shown first,
   *  and only while nothing is typed. */
  get recent(): { connection: LlmConnection; model: LlmModel }[] {
    if (this.query.trim()) return [];
    const found: { connection: LlmConnection; model: LlmModel }[] = [];
    for (const entry of this.remembered()) {
      const group = this.groups.find((g) => g.connection.resource_ref === entry.ref);
      const model = group?.models.find((m) => m.id === entry.model);
      if (group && model) found.push({ connection: group.connection, model });
    }
    return found;
  }

  isCurrent(connection: LlmConnection, model: LlmModel): boolean {
    return this.llm?.secret_ref === connection.resource_ref && this.llm?.model === model.id;
  }

  // ── Choosing ────────────────────────────────────────────────────────

  choose(connection: LlmConnection, model: LlmModel): void {
    // The effort is kept only where the model picked takes that word.
    const effort = this.llm?.reasoning_effort ?? '';
    this.emit(connection, model.id, (model.efforts ?? []).includes(effort) ? effort : '');
    this.remember(connection.resource_ref, model.id);
    this.close();
  }

  chooseEffort(effort: string): void {
    const group = this.groups.find(
      (g) => g.connection.resource_ref === this.llm?.secret_ref);
    if (!group || !this.llm) return;
    this.emit(group.connection, this.llm.model, effort);
  }

  private emit(connection: LlmConnection, model: string, effort: string): void {
    const block: ChatModel = {
      provider: connection.keys.provider,
      model,
      secret_ref: connection.resource_ref,
    };
    if (connection.keys.endpoint) block.endpoint = connection.keys.endpoint;
    if (effort) block.reasoning_effort = effort;
    this.chosen.emit(block);
  }

  effortLabel(effort: string): string {
    return effort === 'xhigh' ? 'Extra high'
      : effort.charAt(0).toUpperCase() + effort.slice(1);
  }

  // ── Picked lately ───────────────────────────────────────────────────

  private remembered(): { ref: string; model: string }[] {
    try {
      const stored = JSON.parse(
        localStorage.getItem(ChatModelPickerComponent.RECENT_KEY) || '[]');
      return Array.isArray(stored)
        ? stored.filter((entry) => entry && typeof entry.ref === 'string'
            && typeof entry.model === 'string')
        : [];
    } catch {
      return [];
    }
  }

  private remember(ref: string, model: string): void {
    const kept = [{ ref, model }, ...this.remembered().filter(
      (entry) => entry.ref !== ref || entry.model !== model)]
      .slice(0, ChatModelPickerComponent.RECENT_MAX);
    try {
      localStorage.setItem(ChatModelPickerComponent.RECENT_KEY, JSON.stringify(kept));
    } catch {
      // A browser that keeps nothing: the list simply has no "recent".
    }
  }
}
