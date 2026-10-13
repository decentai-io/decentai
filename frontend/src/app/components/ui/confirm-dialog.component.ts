import { NgIf } from '@angular/common';
import { Component, Inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

/** What a confirmation asks. */
export interface Confirmation {
  /** The question: "Discard the changes to Speech?" */
  title: string;
  /** One or two sentences on what happens, when the title needs them. */
  body?: string;
  /** The button that goes ahead: "Discard". */
  confirm: string;
  /** The button that does not; "Cancel" unless said. */
  cancel?: string;
  /** Going ahead destroys something: the button is the red one. */
  danger?: boolean;
}

/**
 * The one dialog that asks before something is lost. Opened through
 * ConfirmService (services/confirm.service.ts), which answers with
 * whether the person went ahead. Its look is the dialog shell
 * (styles/controls.scss); Escape and a click outside are a "no".
 */
@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  imports: [NgIf, MatDialogModule],
  template: `
    <h2 mat-dialog-title>{{ asked.title }}</h2>
    <mat-dialog-content *ngIf="asked.body">{{ asked.body }}</mat-dialog-content>
    <mat-dialog-actions>
      <button type="button" class="btn" (click)="answer.close(false)">
        {{ asked.cancel || 'Cancel' }}
      </button>
      <button
        type="button"
        class="btn primary"
        [class.danger]="asked.danger"
        cdkFocusInitial
        (click)="answer.close(true)"
      >
        {{ asked.confirm }}
      </button>
    </mat-dialog-actions>
  `,
})
export class ConfirmDialogComponent {
  constructor(
    @Inject(MAT_DIALOG_DATA) public asked: Confirmation,
    public answer: MatDialogRef<ConfirmDialogComponent, boolean>,
  ) {}
}
