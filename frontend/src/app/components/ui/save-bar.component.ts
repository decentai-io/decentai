import { NgIf } from '@angular/common';
import {
  Component,
  EventEmitter,
  HostBinding,
  HostListener,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
} from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

/** How long "Saved" stays before the bar leaves. */
const SAVED_FOR_MS = 2000;

/**
 * The one way a page of fields is saved. Nothing shows while nothing
 * changed; the first change brings the bar up at the foot of the page
 * with Discard and Save; saving says so, then "Saved" for two seconds,
 * and the bar leaves. An error keeps it, with the message.
 *
 *   <app-save-bar [dirty]="dirty" [saving]="saving" [error]="saveError"
 *                 (save)="save()" (discard)="discard()"></app-save-bar>
 *
 * Ctrl+S (⌘S) saves while there is something to save.
 */
@Component({
  selector: 'app-save-bar',
  standalone: true,
  imports: [NgIf, LucideAngularModule],
  styleUrl: './save-bar.component.css',
  host: { 'aria-live': 'polite' },
  template: `
    <ng-container *ngIf="shown">
      <p class="save-state" [class.is-error]="error">
        <lucide-icon *ngIf="saved" name="check" [size]="16" aria-hidden="true"></lucide-icon>
        {{ error || (saving ? 'Saving…' : saved ? 'Saved' : 'Unsaved changes') }}
      </p>
      <ng-container *ngIf="!saved">
        <button type="button" class="btn" [disabled]="saving" (click)="discard.emit()">Discard</button>
        <button type="button" class="btn primary" [disabled]="saving" (click)="save.emit()">Save</button>
      </ng-container>
    </ng-container>
  `,
})
export class SaveBarComponent implements OnChanges, OnDestroy {
  /** The form differs from what is saved. */
  @Input() dirty = false;
  /** The save is on its way. */
  @Input() saving = false;
  /** Why the last save failed. */
  @Input() error = '';

  @Output() save = new EventEmitter<void>();
  @Output() discard = new EventEmitter<void>();

  /** True for two seconds after a save that worked. */
  saved = false;
  private savedTimer?: ReturnType<typeof setTimeout>;

  @HostBinding('class.is-shown')
  get shown(): boolean {
    return this.dirty || this.saving || this.saved;
  }

  ngOnChanges(changes: SimpleChanges): void {
    const finished = changes['saving']?.previousValue === true && !this.saving;
    if (finished && !this.error && !this.dirty) {
      this.saved = true;
      clearTimeout(this.savedTimer);
      this.savedTimer = setTimeout(() => (this.saved = false), SAVED_FOR_MS);
    }
    if (this.dirty) this.saved = false;
  }

  ngOnDestroy(): void {
    clearTimeout(this.savedTimer);
  }

  @HostListener('document:keydown', ['$event'])
  onKey(event: KeyboardEvent): void {
    if (event.key.toLowerCase() !== 's' || !(event.ctrlKey || event.metaKey)) return;
    if (!this.dirty || this.saving) return;
    event.preventDefault();
    this.save.emit();
  }
}
