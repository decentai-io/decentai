import { Clipboard } from '@angular/cdk/clipboard';
import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { AgentRecord, RecordShape } from 'src/app/services/records.service';
import { Shown, showValue } from './record-values';

/** One field of a record, as the view lists it. */
interface Entry {
  name: string;
  label: string;
  value: Shown;
}

/**
 * One record, read: its name, whose it is, its fields by the labels
 * the agent's manifest gives them, and its content shown as what it is.
 * A field the manifest marks secret is masked until it is revealed.
 * The raw form is one press away at the foot, and never the default.
 *
 * The page owns the actions (it knows what the person may do) and
 * hands them in as `recordActions`.
 */
@Component({
  selector: 'app-record-view',
  standalone: false,
  templateUrl: './record-view.component.html',
  styleUrl: './record-view.component.css',
})
export class RecordViewComponent implements OnChanges {
  @Input({ required: true }) record!: AgentRecord;
  /** The kind's declared shape; null for a kind no installed agent declares. */
  @Input() shape: RecordShape | null = null;
  @Input() name = '';
  /** "Note", "Notebook", "Private": the line under the name. */
  @Input() kind = '';
  @Input() agent = '';
  @Input() sharing = '';

  @Output() closed = new EventEmitter<void>();

  /** The find-by fields, then the content, each in the shape's order. */
  fields: Entry[] = [];
  content: Entry[] = [];
  /** The secret fields that were asked to be shown, by name. */
  revealed = new Set<string>();
  json = '';
  copied = false;

  constructor(private clipboard: Clipboard) {}

  ngOnChanges(): void {
    this.fields = this.entries(this.record.keys ?? {});
    this.content = this.entries(this.record.values ?? {});
    this.revealed = new Set<string>();
    this.json = JSON.stringify({ keys: this.masked(this.record.keys), values: this.masked(this.record.values) }, null, 2);
  }

  /** A part of the record as entries: the fields the shape declares
   *  first, in its order, then whatever else the agent stored. */
  private entries(held: Record<string, unknown>): Entry[] {
    const declared = this.shape?.fields ?? [];
    const order = (name: string) => {
      const index = declared.findIndex((field) => field.name === name);
      return index < 0 ? declared.length : index;
    };
    return Object.keys(held)
      .sort((a, b) => order(a) - order(b))
      .map((name) => {
        const field = declared.find((candidate) => candidate.name === name);
        return { name, label: field?.label || name, value: showValue(held[name], field?.type) };
      });
  }

  /** The same part with its secret fields masked: the raw form shows
   *  no more than the page does. */
  private masked(held: Record<string, unknown> | undefined): Record<string, unknown> {
    const secret = new Set((this.shape?.fields ?? [])
      .filter((field) => field.type === 'secret').map((field) => field.name));
    return Object.fromEntries(Object.entries(held ?? {})
      .map(([name, value]) => [name, secret.has(name) && value ? '••••••' : value]));
  }

  toggleReveal(name: string): void {
    if (!this.revealed.delete(name)) this.revealed.add(name);
  }

  get created(): string {
    return fullDate(this.record.created_at || '');
  }

  /** When it changed, only where that is not when it was made. */
  get changed(): string {
    const changed = this.record.updated_at || '';
    return changed && changed !== this.record.created_at ? relativeDate(changed) : '';
  }

  get changedInFull(): string {
    return fullDate(this.record.updated_at || '');
  }

  copyRef(): void {
    this.clipboard.copy(this.record.resource_ref);
    this.copied = true;
    setTimeout(() => (this.copied = false), 1500);
  }
}
