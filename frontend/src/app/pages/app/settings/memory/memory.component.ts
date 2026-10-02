import { Component, OnInit } from '@angular/core';

import { Memory, MemoryService } from 'src/app/services/memory.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataPageBase } from '../../data-page-base';

/**
 * Memory: what the assistant has been told to remember about you. A tab
 * of the Settings page — the person's own configuration of the
 * assistant, beside the models it thinks with.
 *
 * A memory arrives one of two ways — a chat asking to remember
 * something, which the conversation announces as it happens, or written
 * here by you. Every row says which, and says so again once you have
 * edited it, because a record that cannot account for its own wording is
 * not one anybody can trust. The rest of the bargain is here too: it is
 * yours to correct and to delete.
 */
@Component({
  selector: 'app-memory',
  standalone: false,
  templateUrl: './memory.component.html',
  styleUrls: ['../../data-shared.css', '../../../admin/iam-shared.css', './memory.component.css'],
})
export class MemoryComponent extends DataPageBase implements OnInit {
  loading = true;
  memories: Memory[] = [];
  /** How many memories may be kept at once, from the server rather than
   *  a number copied into the page. */
  limit = 0;
  busyId = '';
  query = '';
  editingId: string | null = null;
  editText = '';
  /** The person's own write, folded away until asked for so the page
   *  still leads with the record rather than a blank box. */
  composerOpen = false;
  newText = '';
  adding = false;
  forgetTarget: Memory | null = null;
  readonly textLimit = 500;

  constructor(
    private service: MemoryService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    await this.reload();
    this.loading = false;
  }

  private async reload(): Promise<void> {
    const page = await this.service.list();
    this.memories = page.memories;
    this.limit = page.limit;
  }

  /** Room left before the oldest memory starts being dropped. */
  get remaining(): number {
    return Math.max(0, this.limit - this.memories.length);
  }

  /** Near enough the cap that the eviction rule is about to matter. */
  get nearLimit(): boolean {
    return !!this.limit && this.remaining <= 5;
  }

  get atLimit(): boolean {
    return !!this.limit && this.memories.length >= this.limit;
  }

  get canForget(): boolean {
    return this.auth.can('settings:memory:delete');
  }

  get canAdd(): boolean {
    return this.auth.can('settings:memory:create');
  }

  get canEdit(): boolean {
    return this.auth.can('settings:memory:update');
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

  openComposer(): void {
    this.composerOpen = true;
    this.error = '';
  }

  closeComposer(): void {
    this.composerOpen = false;
    this.newText = '';
  }

  get canSubmitNew(): boolean {
    const text = this.newText.trim();
    return !!text && text.length <= this.textLimit && !this.adding;
  }

  async addMemory(): Promise<void> {
    if (!this.canSubmitNew) return;
    this.adding = true;
    try {
      const result = await this.service.create(this.newText.trim());
      if (result.error) return this.fail(result.error);
      this.closeComposer();
      await this.reload();
      this.flash('Added. New conversations will use it.');
    } finally {
      this.adding = false;
    }
  }

  dateLabel(value?: string | null): string {
    if (!value) return '';
    const date = new Date(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleDateString();
  }

  /** Where this sentence came from, in the order that matters: who
   *  wrote the words, then when. A chat that only STARTED it is a link
   *  beside this, not the answer to it. */
  originLabel(memory: Memory): string {
    if (memory.authored) {
      const edited = this.dateLabel(memory.updated_at);
      return memory.corrected && edited
        ? `Added by you, edited ${edited}`
        : `Added by you ${this.dateLabel(memory.created_at)}`.trim();
    }
    if (memory.corrected) {
      return `Corrected by you ${this.dateLabel(memory.updated_at)}`.trim();
    }
    return `Saved ${this.dateLabel(memory.created_at)}`.trim();
  }

  startEdit(memory: Memory): void {
    this.editingId = memory.memory_id;
    this.editText = memory.text;
    this.error = '';
  }

  cancelEdit(): void {
    this.editingId = null;
    this.editText = '';
  }

  async saveEdit(memory: Memory): Promise<void> {
    const text = this.editText.trim();
    if (!text || text === memory.text) return;
    this.busyId = memory.memory_id;
    try {
      const result = await this.service.update(memory.memory_id, text);
      if (result.error) return this.fail(result.error);
      const saved = result.data?.memory;
      memory.text = saved?.text ?? text;
      memory.corrected = saved?.corrected ?? true;
      memory.updated_at = saved?.updated_at ?? memory.updated_at;
      this.cancelEdit();
      this.flash('Memory updated. New conversations will use the correction.');
    } finally {
      this.busyId = '';
    }
  }

  requestForget(memory: Memory): void {
    this.forgetTarget = memory;
    this.error = '';
  }

  closeForgetDialog(): void {
    if (this.busyId) return;
    this.forgetTarget = null;
  }

  async forget(memory: Memory): Promise<void> {
    this.busyId = memory.memory_id;
    try {
      const result = await this.service.remove(memory.memory_id);
      if (result.error) return this.fail(result.error);
      this.forgetTarget = null;
      await this.reload();
      this.flash('Forgotten.');
    } finally {
      this.busyId = '';
    }
  }
}
