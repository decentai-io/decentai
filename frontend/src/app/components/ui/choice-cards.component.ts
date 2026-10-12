import { NgFor, NgIf } from '@angular/common';
import { Component, ElementRef, forwardRef, Input } from '@angular/core';
import { ControlValueAccessor, NG_VALUE_ACCESSOR } from '@angular/forms';
import { Choice, radioKeyTarget } from './radio-keys';

/**
 * A choice whose options each need a sentence: one card per option,
 * with its name and its one-line meaning.
 *
 *   <app-choice-cards label="A program" [choices]="programChoices"
 *                     [(ngModel)]="policy.programs"></app-choice-cards>
 *
 * Inside an <app-field> the field's label names it and `label` is not
 * needed. It is a group of radios: the arrow keys move the choice.
 */
@Component({
  selector: 'app-choice-cards',
  standalone: true,
  imports: [NgFor, NgIf],
  styleUrl: './choice-cards.component.css',
  providers: [
    { provide: NG_VALUE_ACCESSOR, useExisting: forwardRef(() => ChoiceCardsComponent), multi: true },
  ],
  template: `
    <div class="choice-cards" role="radiogroup" [attr.aria-label]="label || null">
      <button
        *ngFor="let choice of choices; let i = index"
        type="button"
        class="choice-card"
        role="radio"
        [attr.aria-checked]="choice.value === value"
        [tabindex]="choice.value === value || (!chosen && i === 0) ? 0 : -1"
        [disabled]="disabled || choice.disabled"
        (click)="choose(choice.value)"
        (keydown)="onKey($event, i)"
        (blur)="touched()"
      >
        <span class="choice-dot"></span>
        <span class="choice-words">
          <span class="choice-name">{{ choice.name }}</span>
          <span class="choice-hint" *ngIf="choice.hint">{{ choice.hint }}</span>
        </span>
      </button>
    </div>
  `,
})
export class ChoiceCardsComponent<T = string> implements ControlValueAccessor {
  @Input({ required: true }) choices: Choice<T>[] = [];
  @Input() label = '';
  @Input() disabled = false;

  value: T | null = null;

  private changed: (value: T) => void = () => {};
  touched: () => void = () => {};

  constructor(private host: ElementRef<HTMLElement>) {}

  /** Whether the value is one of the choices. */
  get chosen(): boolean {
    return this.choices.some((choice) => choice.value === this.value);
  }

  choose(value: T): void {
    if (value === this.value) return;
    this.value = value;
    this.changed(value);
  }

  onKey(event: KeyboardEvent, index: number): void {
    const target = radioKeyTarget(event.key, index, this.choices.length);
    if (target === null) return;
    event.preventDefault();
    this.choose(this.choices[target].value);
    this.host.nativeElement.querySelectorAll<HTMLElement>('.choice-card')[target]?.focus();
  }

  writeValue(value: T | null): void {
    this.value = value;
  }

  registerOnChange(changed: (value: T) => void): void {
    this.changed = changed;
  }

  registerOnTouched(touched: () => void): void {
    this.touched = touched;
  }

  setDisabledState(disabled: boolean): void {
    this.disabled = disabled;
  }
}
