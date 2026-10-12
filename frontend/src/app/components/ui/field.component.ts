import { NgIf } from '@angular/common';
import { AfterContentChecked, Component, ElementRef, Input } from '@angular/core';
import { RouterLink } from '@angular/router';

let created = 0;

/** What a label's `for` can point at. */
const LABELABLE = 'input, textarea, select, button';
/** The control a field is about: the first of these inside it. */
const CONTROL = `${LABELABLE}, [role="radiogroup"]`;

/**
 * One field of a form: the label, the control placed inside the tag,
 * and under it one line of help or, when there is one, the error.
 *
 *   <app-field label="Name" help="Shown in the list." [error]="nameError">
 *     <input class="input" [(ngModel)]="name" />
 *   </app-field>
 *
 * The field ties the three together for a screen reader itself: the
 * label names the control, and the help and the error describe it.
 */
@Component({
  selector: 'app-field',
  standalone: true,
  imports: [NgIf, RouterLink],
  styleUrl: './field.component.css',
  template: `
    <label class="field-label" [id]="id + '-label'" [attr.for]="labelFor">{{ label }}</label>
    <ng-content></ng-content>
    <p class="field-error" *ngIf="error" [id]="id + '-error'" role="alert">{{ error }}</p>
    <p class="field-help" *ngIf="!error && help" [id]="id + '-help'">
      {{ help }}
      <a *ngIf="learnMore" [routerLink]="learnMorePath" [fragment]="learnMoreFragment">Learn more</a>
    </p>
  `,
})
export class FieldComponent implements AfterContentChecked {
  @Input({ required: true }) label = '';
  /** One sentence, or nothing. */
  @Input() help = '';
  /** Replaces the help while it is set, and marks the control invalid. */
  @Input() error = '';
  /** The guide's page for the rest of the explanation, as
   *  `/guide/chapter#heading`. */
  @Input() learnMore = '';

  readonly id = `field-${++created}`;
  /** The control's id, when it is something a label can point at. */
  labelFor: string | null = null;

  private control: HTMLElement | null = null;

  constructor(private host: ElementRef<HTMLElement>) {}

  get learnMorePath(): string {
    return this.learnMore.split('#')[0];
  }

  get learnMoreFragment(): string | undefined {
    return this.learnMore.split('#')[1];
  }

  /** The control can arrive late (inside an @if), so it is looked for
   *  until found, and its state kept in step with the error. */
  ngAfterContentChecked(): void {
    if (!this.control?.isConnected) {
      this.control = this.host.nativeElement.querySelector<HTMLElement>(CONTROL);
      if (!this.control) return;
      this.control.id ||= `${this.id}-control`;
      if (this.control.matches(LABELABLE)) {
        // The label is drawn after its content is checked, so it reads
        // the id in this same pass.
        this.labelFor = this.control.id;
      } else {
        this.control.setAttribute('aria-labelledby', `${this.id}-label`);
      }
    }
    const describedBy = this.error ? `${this.id}-error` : this.help ? `${this.id}-help` : null;
    this.setAttribute('aria-describedby', describedBy);
    this.setAttribute('aria-invalid', this.error ? 'true' : null);
  }

  private setAttribute(name: string, value: string | null): void {
    if (!this.control || this.control.getAttribute(name) === value) return;
    if (value === null) this.control.removeAttribute(name);
    else this.control.setAttribute(name, value);
  }
}
