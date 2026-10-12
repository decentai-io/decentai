import { NgFor } from '@angular/common';
import { Component, ElementRef, forwardRef, Input } from '@angular/core';
import { ControlValueAccessor, NG_VALUE_ACCESSOR } from '@angular/forms';
import { Choice, radioKeyTarget } from './radio-keys';

/**
 * A choice of two to four short words, side by side.
 *
 *   <app-segmented label="View" [choices]="views" [(ngModel)]="view"></app-segmented>
 *
 * Inside an <app-field> the field's label names it and `label` is not
 * needed. It is a group of radios: the arrow keys move the choice.
 */
@Component({
  selector: 'app-segmented',
  standalone: true,
  imports: [NgFor],
  styleUrl: './segmented.component.css',
  providers: [
    { provide: NG_VALUE_ACCESSOR, useExisting: forwardRef(() => SegmentedComponent), multi: true },
  ],
  template: `
    <div class="segmented" role="radiogroup" [attr.aria-label]="label || null">
      <button
        *ngFor="let choice of choices; let i = index"
        type="button"
        class="segment"
        role="radio"
        [attr.aria-checked]="choice.value === value"
        [tabindex]="choice.value === value || (!chosen && i === 0) ? 0 : -1"
        [disabled]="disabled || choice.disabled"
        (click)="choose(choice.value)"
        (keydown)="onKey($event, i)"
        (blur)="touched()"
      >
        {{ choice.name }}
      </button>
    </div>
  `,
})
export class SegmentedComponent<T = string> implements ControlValueAccessor {
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
    this.host.nativeElement.querySelectorAll<HTMLElement>('.segment')[target]?.focus();
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
