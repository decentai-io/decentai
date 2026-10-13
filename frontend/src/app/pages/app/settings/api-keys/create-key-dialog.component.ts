import { NgIf } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { LucideAngularModule } from 'lucide-angular';

import { FieldComponent } from 'src/app/components/ui/field.component';
import { ApiKeysService, MadeApiKey } from 'src/app/services/api-keys.service';

/**
 * Creating an API key, in two moments of one dialog: its name, then the
 * key itself, which is the only time it is in the open. The dialog
 * closes with whether a key was made.
 *
 * Once the key is shown the dialog cannot be dismissed by Escape or a
 * click outside: closing it is the end of the key being readable, so it
 * takes Done, and a second Done if the key was not copied.
 */
@Component({
  selector: 'app-create-key-dialog',
  standalone: true,
  imports: [NgIf, FormsModule, MatDialogModule, LucideAngularModule, FieldComponent],
  styleUrl: './create-key-dialog.component.css',
  template: `
    <ng-container *ngIf="!made">
      <h2 mat-dialog-title>Create an API key</h2>
      <form (ngSubmit)="create()">
        <mat-dialog-content>
          <app-field label="Name" help="What will use it, so you know which to revoke later." [error]="error">
            <input
              class="input"
              name="key-name"
              placeholder="Nightly report script"
              cdkFocusInitial
              [maxlength]="nameLimit"
              [(ngModel)]="name"
            />
          </app-field>
        </mat-dialog-content>
        <mat-dialog-actions>
          <button type="button" class="btn" [disabled]="creating" (click)="dialog.close(false)">Cancel</button>
          <button type="submit" class="btn primary" [disabled]="creating || !name.trim()">
            {{ creating ? 'Creating…' : 'Create' }}
          </button>
        </mat-dialog-actions>
      </form>
    </ng-container>

    <ng-container *ngIf="made">
      <h2 mat-dialog-title>{{ made.name }}</h2>
      <mat-dialog-content>
        <code class="made-key">{{ made.key }}</code>
        <p class="made-note" [class.is-warning]="warned" role="status">
          {{ warned ? 'You have not copied it, and it cannot be shown again. Press Done again to close.'
                    : copyFailed ? 'Could not copy. Select the key and copy it yourself.'
                    : 'Shown once. Copy it now.' }}
        </p>
      </mat-dialog-content>
      <mat-dialog-actions>
        <button type="button" class="btn" (click)="copy()">
          <lucide-icon [name]="copied ? 'check' : 'copy'" [size]="16" aria-hidden="true"></lucide-icon>
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
        <button type="button" class="btn primary" (click)="done()">Done</button>
      </mat-dialog-actions>
    </ng-container>
  `,
})
export class CreateKeyDialogComponent {
  readonly nameLimit = 80;
  name = '';
  creating = false;
  error = '';

  /** The key, once made: the one moment its secret is in the open. */
  made: MadeApiKey | null = null;
  copied = false;
  copyFailed = false;
  /** Done was pressed without the key having been copied. */
  warned = false;

  constructor(
    public dialog: MatDialogRef<CreateKeyDialogComponent, boolean>,
    private service: ApiKeysService,
  ) {}

  async create(): Promise<void> {
    const name = this.name.trim();
    if (!name || this.creating) return;
    this.creating = true;
    this.error = '';
    try {
      const result = await this.service.create(name);
      if (result.error || !result.key) {
        this.error = result.error || 'Could not create the key.';
        return;
      }
      this.made = result.key;
      this.dialog.disableClose = true;
    } finally {
      this.creating = false;
    }
  }

  async copy(): Promise<void> {
    if (!this.made) return;
    try {
      await navigator.clipboard.writeText(this.made.key);
      this.copied = true;
      this.copyFailed = false;
      this.warned = false;
    } catch {
      this.copyFailed = true;
    }
  }

  done(): void {
    if (!this.copied && !this.warned) {
      this.warned = true;
      return;
    }
    this.dialog.close(true);
  }
}
