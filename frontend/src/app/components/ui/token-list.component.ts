import { NgFor } from '@angular/common';
import { Component, forwardRef, Input } from '@angular/core';
import { ControlValueAccessor, FormsModule, NG_VALUE_ACCESSOR } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';

/**
 * A list of short values a person types one at a time (blocked sites,
 * allowed packages): an input with Add, and the values as chips under
 * it, in order, each with a remove. Enter adds.
 *
 *   <app-field label="Sites no agent may open">
 *     <app-token-list placeholder="example.com" [(ngModel)]="policy.blocked"></app-token-list>
 *   </app-field>
 */
@Component({
  selector: 'app-token-list',
  standalone: true,
  imports: [NgFor, FormsModule, LucideAngularModule],
  styleUrl: './token-list.component.css',
  providers: [
    { provide: NG_VALUE_ACCESSOR, useExisting: forwardRef(() => TokenListComponent), multi: true },
  ],
  template: `
    <div class="token-add">
      <input
        class="input"
        type="text"
        [placeholder]="placeholder"
        [disabled]="disabled"
        [(ngModel)]="draft"
        [ngModelOptions]="{ standalone: true }"
        (keydown.enter)="add($event)"
        (blur)="touched()"
      />
      <button type="button" class="btn" [disabled]="disabled || !draft.trim()" (click)="add()">
        {{ addLabel }}
      </button>
    </div>
    <ul class="tokens" [attr.aria-label]="listLabel || null">
      <li class="chip token" *ngFor="let token of tokens">
        {{ token }}
        <button
          type="button"
          class="token-remove"
          [attr.aria-label]="'Remove ' + token"
          [disabled]="disabled"
          (click)="remove(token)"
        >
          <lucide-icon name="x" [size]="12" aria-hidden="true"></lucide-icon>
        </button>
      </li>
    </ul>
  `,
})
export class TokenListComponent implements ControlValueAccessor {
  @Input() placeholder = '';
  /** What the list of chips is, for a screen reader. */
  @Input() listLabel = '';
  @Input() disabled = false;
  /** Turns what was typed into the value that is kept (a pasted address
   *  into its site's name), or '' to keep nothing. */
  @Input() normalize: (typed: string) => string = (typed) => typed;
  /** The button's word: "Add", or the page's own ("Block", "Allow"). */
  @Input() addLabel = 'Add';

  tokens: string[] = [];
  draft = '';

  private changed: (tokens: string[]) => void = () => {};
  touched: () => void = () => {};

  /** Adds what is typed; a value already in the list is not added twice. */
  add(event?: Event): void {
    event?.preventDefault();
    const token = this.normalize(this.draft.trim());
    this.draft = '';
    if (!token || this.tokens.includes(token)) return;
    this.tokens = [...this.tokens, token].sort();
    this.changed(this.tokens);
  }

  remove(token: string): void {
    this.tokens = this.tokens.filter((kept) => kept !== token);
    this.changed(this.tokens);
  }

  writeValue(tokens: string[] | null): void {
    this.tokens = [...(tokens ?? [])];
  }

  registerOnChange(changed: (tokens: string[]) => void): void {
    this.changed = changed;
  }

  registerOnTouched(touched: () => void): void {
    this.touched = touched;
  }

  setDisabledState(disabled: boolean): void {
    this.disabled = disabled;
  }
}
