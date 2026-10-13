import { NgIf } from '@angular/common';
import { Component, forwardRef, Input } from '@angular/core';
import { ControlValueAccessor, NG_VALUE_ACCESSOR } from '@angular/forms';

let created = 0;

/**
 * A switch for something that is on or off, with its label at its right
 * and one line of help under it.
 *
 *   <app-switch label="Read replies aloud" help="On this device only."
 *               [(ngModel)]="readAloud"></app-switch>
 *
 * `note` is a word shown beside the label for a moment ("Saved"), for a
 * switch that saves itself.
 */
@Component({
  selector: 'app-switch',
  standalone: true,
  imports: [NgIf],
  styleUrl: './switch.component.css',
  providers: [
    { provide: NG_VALUE_ACCESSOR, useExisting: forwardRef(() => SwitchComponent), multi: true },
  ],
  template: `
    <button
      type="button"
      class="switch"
      role="switch"
      [id]="id"
      [attr.aria-checked]="on"
      [attr.aria-describedby]="help ? id + '-help' : null"
      [disabled]="disabled"
      (click)="toggle()"
      (blur)="touched()"
    >
      <span class="switch-track"><span class="switch-thumb"></span></span>
    </button>
    <div class="switch-words" [class.visually-hidden]="labelHidden">
      <label class="switch-label" [attr.for]="id">
        {{ label }}
        <span class="switch-note" *ngIf="note" role="status">{{ note }}</span>
      </label>
      <p class="switch-help" *ngIf="help" [id]="id + '-help'">{{ help }}</p>
    </div>
  `,
})
export class SwitchComponent implements ControlValueAccessor {
  @Input({ required: true }) label = '';
  @Input() help = '';
  @Input() note = '';
  /** The label is read out and not shown: for a switch in a row that
   *  already says what it turns on. */
  @Input() labelHidden = false;
  @Input() disabled = false;

  readonly id = `switch-${++created}`;
  on = false;

  private changed: (on: boolean) => void = () => {};
  touched: () => void = () => {};

  toggle(): void {
    this.on = !this.on;
    this.changed(this.on);
  }

  writeValue(on: boolean): void {
    this.on = !!on;
  }

  registerOnChange(changed: (on: boolean) => void): void {
    this.changed = changed;
  }

  registerOnTouched(touched: () => void): void {
    this.touched = touched;
  }

  setDisabledState(disabled: boolean): void {
    this.disabled = disabled;
  }
}
