import { Component, OnInit } from '@angular/core';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { Memory, MemoryService } from 'src/app/services/memory.service';

/** With fewer spaces than this left, the page says the cap is near. */
const NEAR_LIMIT = 10;

/**
 * Settings › Memory: what the assistant has been told to remember about
 * you.
 *
 * A memory arrives one of two ways — a chat asking to remember
 * something, which the conversation announces as it happens, or written
 * here by you. Every row says which, and says so again once you have
 * edited it, because a record that cannot account for its own wording is
 * not one anybody can trust. The rest of the bargain is here too: it is
 * yours to correct and to forget.
 */
@Component({
  selector: 'app-memory',
  standalone: false,
  templateUrl: './memory.component.html',
  styleUrls: ['./memory.component.css'],
})
export class MemoryComponent implements OnInit {
  loading = true;
  /** Why the memories could not be read. */
  loadError = '';
  memories: Memory[] = [];
  /** How many memories may be kept at once, from the server rather than
   *  a number copied into the page. */
  limit = 0;
  query = '';
  readonly textLimit = 500;

  /** The memory being written. */
  newText = '';
  adding = false;
  addError = '';

  /** The memory being edited in its row. */
  editingId: string | null = null;
  editText = '';
  editError = '';
  busyId = '';
  /** Why a memory could not be forgotten. */
  forgetError = '';
  forgettingAll = false;

  constructor(
    private service: MemoryService,
    public auth: AuthService,
    private confirm: ConfirmService,
  ) {}

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
    this.memories = page.memories;
    this.limit = page.limit;
  }

  // ── Access, and the cap ─────────────────────────────────────────────

  get canAdd(): boolean {
    return this.auth.can('settings:memory:create');
  }

  get canEdit(): boolean {
    return this.auth.can('settings:memory:update');
  }

  get canForget(): boolean {
    return this.auth.can('settings:memory:delete');
  }

  /** Room left before the oldest memory starts being dropped. */
  get remaining(): number {
    return Math.max(0, this.limit - this.memories.length);
  }

  /** Near enough the cap that the eviction rule is about to matter. */
  get nearLimit(): boolean {
    return !!this.limit && this.remaining < NEAR_LIMIT;
  }

  get atLimit(): boolean {
    return !!this.limit && this.memories.length >= this.limit;
  }

  /** The counts under the list. */
  get foot(): string {
    const saved = `${this.memories.length} saved`;
    return this.limit ? `${saved} · ${this.remaining} space${this.remaining === 1 ? '' : 's'} left` : saved;
  }

  /** What the cap means, said before something quietly goes. */
  get capWarning(): string {
    if (this.atLimit) return `Full at ${this.limit}: the next memory saved drops the oldest.`;
    return `At ${this.limit}, a new memory drops the oldest.`;
  }

  /** Newest first. The server hands them over oldest first so the prompt
   *  reads them in the order they were learned; on a page, that buries
   *  the one you just watched get saved at the bottom. */
  get filteredMemories(): Memory[] {
    const query = this.query.trim().toLowerCase();
    const matching = query
      ? this.memories.filter((memory) => memory.text.toLowerCase().includes(query))
      : [...this.memories];
    return matching.sort(
      (a, b) => (b.created_at || '').localeCompare(a.created_at || ''),
    );
  }

  // ── Writing one yourself ────────────────────────────────────────────

  get canSubmitNew(): boolean {
    const text = this.newText.trim();
    return !!text && text.length <= this.textLimit && !this.adding;
  }

  async addMemory(): Promise<void> {
    if (!this.canSubmitNew) return;
    this.adding = true;
    this.addError = '';
    try {
      const result = await this.service.create(this.newText.trim());
      if (result.error) {
        this.addError = result.error;
        return;
      }
      this.newText = '';
      await this.reload();
    } finally {
      this.adding = false;
    }
  }

  // ── A row ───────────────────────────────────────────────────────────

  /** Where this sentence came from, in the order that matters: who
   *  wrote the words, then when. A chat that only STARTED it is a link
   *  beside this, not the answer to it. */
  originLabel(memory: Memory): string {
    if (memory.authored) {
      const edited = relativeDate(memory.updated_at);
      return memory.corrected && edited
        ? `Added by you, edited ${edited}`
        : `Added by you ${relativeDate(memory.created_at)}`.trim();
    }
    if (memory.corrected) {
      return `Corrected by you ${relativeDate(memory.updated_at)}`.trim();
    }
    return `Saved ${relativeDate(memory.created_at)}`.trim();
  }

  /** The whole date, for the origin's tooltip. */
  originInFull(memory: Memory): string {
    return fullDate(memory.updated_at || memory.created_at);
  }

  startEdit(memory: Memory): void {
    this.editingId = memory.memory_id;
    this.editText = memory.text;
    this.editError = '';
  }

  cancelEdit(): void {
    this.editingId = null;
    this.editText = '';
    this.editError = '';
  }

  async saveEdit(memory: Memory): Promise<void> {
    const text = this.editText.trim();
    if (!text || text === memory.text) return;
    this.busyId = memory.memory_id;
    this.editError = '';
    try {
      const result = await this.service.update(memory.memory_id, text);
      if (result.error) {
        // The editor stays open, holding the text that was refused.
        this.editError = result.error;
        return;
      }
      const saved = result.data?.memory;
      memory.text = saved?.text ?? text;
      memory.corrected = saved?.corrected ?? true;
      memory.updated_at = saved?.updated_at ?? memory.updated_at;
      this.cancelEdit();
    } finally {
      this.busyId = '';
    }
  }

  // ── Forgetting ──────────────────────────────────────────────────────

  async forget(memory: Memory): Promise<void> {
    const sure = await this.confirm.ask({
      title: 'Forget this memory?',
      body: `“${memory.text}” The assistant stops using it in new conversations; those that already used it are unchanged.`,
      confirm: 'Forget',
      cancel: 'Keep it',
      danger: true,
    });
    if (!sure) return;
    this.busyId = memory.memory_id;
    this.forgetError = '';
    try {
      const result = await this.service.remove(memory.memory_id);
      if (result.error) {
        this.forgetError = result.error;
        return;
      }
      await this.reload();
    } finally {
      this.busyId = '';
    }
  }

  /** Every memory, one after the other, after one question. */
  async forgetAll(): Promise<void> {
    const count = this.memories.length;
    const sure = await this.confirm.ask({
      title: `Forget all ${count} memories?`,
      body: 'The assistant starts new conversations knowing none of them. This cannot be undone.',
      confirm: 'Forget all',
      cancel: 'Keep them',
      danger: true,
    });
    if (!sure) return;
    this.forgettingAll = true;
    this.forgetError = '';
    try {
      for (const memory of [...this.memories]) {
        const result = await this.service.remove(memory.memory_id);
        if (result.error) {
          this.forgetError = result.error;
          break;
        }
      }
      await this.reload();
    } finally {
      this.forgettingAll = false;
    }
  }
}
