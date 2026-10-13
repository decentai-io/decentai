import { NgFor, NgIf } from '@angular/common';
import { Component, ElementRef, forwardRef, Input, ViewChild } from '@angular/core';
import { ControlValueAccessor, NG_VALUE_ACCESSOR } from '@angular/forms';
import { MatMenuModule } from '@angular/material/menu';
import { LucideAngularModule } from 'lucide-angular';
import { Choice } from './radio-keys';

/**
 * A choice of one from a list, in place of the browser's select: a
 * button showing what is chosen, opening the menu panel. An option is
 * a name and, under it, what the name means, so neither is cut.
 *
 *   <app-field label="Trust level">
 *     <app-select [choices]="trustLevels" [(ngModel)]="trust"></app-select>
 *   </app-field>
 *
 * The panel is Material's menu (styles/controls.scss), which gives it
 * the arrow keys, typing to find, and Escape.
 */
@Component({
  selector: 'app-select',
  standalone: true,
  imports: [NgFor, NgIf, MatMenuModule, LucideAngularModule],
  styleUrl: './select.component.css',
  providers: [
    { provide: NG_VALUE_ACCESSOR, useExisting: forwardRef(() => SelectComponent), multi: true },
  ],
  template: `
    <button
      #trigger
      type="button"
      class="input select-trigger"
      [class.is-empty]="!chosen"
      [attr.aria-label]="label || null"
      [disabled]="disabled"
      [matMenuTriggerFor]="panel"
      (menuOpened)="matchWidth(panel.panelId)"
      (menuClosed)="touched()"
    >
      <span class="select-value">{{ chosen?.name ?? placeholder }}</span>
      <lucide-icon name="chevron-down" [size]="16" aria-hidden="true"></lucide-icon>
    </button>

    <mat-menu #panel="matMenu" class="select-panel">
      <button
        *ngFor="let choice of choices; trackBy: byValue"
        type="button"
        mat-menu-item
        [disabled]="choice.disabled"
        [attr.aria-current]="choice.value === value ? 'true' : null"
        (click)="choose(choice.value)"
      >
        <span class="select-option">
          <span class="select-option-name">{{ choice.name }}</span>
          <span class="select-option-hint" *ngIf="choice.hint">{{ choice.hint }}</span>
        </span>
        <lucide-icon
          *ngIf="choice.value === value"
          class="menu-check"
          name="check"
          [size]="16"
          aria-hidden="true"
        ></lucide-icon>
      </button>
    </mat-menu>
  `,
})
export class SelectComponent<T = string> implements ControlValueAccessor {
  @Input({ required: true }) choices: Choice<T>[] = [];
  /** Shown while nothing is chosen. */
  @Input() placeholder = 'Choose';
  /** The control's name, when it is not inside an <app-field>. */
  @Input() label = '';
  @Input() disabled = false;

  @ViewChild('trigger', { static: true })
  private trigger!: ElementRef<HTMLElement>;

  value: T | null = null;

  private changed: (value: T) => void = () => {};
  touched: () => void = () => {};

  get chosen(): Choice<T> | undefined {
    return this.choices.find((choice) => choice.value === this.value);
  }

  byValue(_index: number, choice: Choice<T>): T {
    return choice.value;
  }

  choose(value: T): void {
    if (value === this.value) return;
    this.value = value;
    this.changed(value);
  }

  /** The panel opens in an overlay of its own, so it is told how wide
   *  the button is: never narrower than what it opened from. */
  matchWidth(panelId: string): void {
    const panel = document.getElementById(panelId);
    if (panel) panel.style.minWidth = `${this.trigger.nativeElement.offsetWidth}px`;
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
