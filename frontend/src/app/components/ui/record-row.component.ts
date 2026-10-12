import { NgIf } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

/** How what a row records went: well, badly, still waiting, or
 *  neither. */
export type RecordTone = 'ok' | 'bad' | 'live' | 'muted';

/**
 * One row of a record that is read and never changed (Monitoring's
 * events, the Audit trail): the time, a dot for how it went, what
 * happened in words, and its kind as a chip. Pressing it opens what is
 * inside the tag under it: the details as they were written down.
 *
 *   <app-record-row time="11:34:08" tone="ok" outcome="Succeeded"
 *                   words="Upload files" [count]="5" chip="Platform action"
 *                   [open]="isOpen(row)" (toggle)="toggle(row)">
 *     <a recordEnd class="icon-btn sm" aria-label="Open the chat" …>…</a>
 *     <pre class="record-json">…</pre>
 *   </app-record-row>
 *
 * What is marked `recordEnd` sits at the right of the row, outside its
 * button, so it may be a link. `count` above one says the row stands
 * for that many alike. Rows live in an element with role="list".
 */
@Component({
  selector: 'app-record-row',
  standalone: true,
  imports: [NgIf, LucideAngularModule],
  styleUrl: './record-row.component.css',
  host: { role: 'listitem' },
  template: `
    <div class="record-line">
      <button class="record-head" type="button" [disabled]="!canOpen" [attr.aria-expanded]="canOpen ? open : null" (click)="toggle.emit()">
        <span class="record-time">{{ time }}</span>
        <span class="record-words">
          <span class="record-what">
            <span class="record-dot" [class]="'record-dot is-' + tone"></span>
            <span class="visually-hidden" *ngIf="outcome">{{ outcome }}: </span>
            {{ words }}
            <span class="record-count" *ngIf="count > 1">×{{ count }}</span>
          </span>
          <span class="record-more" *ngIf="more">{{ more }}</span>
        </span>
        <span class="chip record-chip" *ngIf="chip">{{ chip }}</span>
        <lucide-icon class="record-caret" *ngIf="canOpen" [name]="open ? 'chevron-up' : 'chevron-down'" [size]="16" aria-hidden="true"></lucide-icon>
      </button>
      <ng-content select="[recordEnd]"></ng-content>
    </div>
    <div class="record-body" *ngIf="open"><ng-content></ng-content></div>
  `,
})
export class RecordRowComponent {
  @Input({ required: true }) time = '';
  @Input({ required: true }) words = '';
  /** The line under the words: the outcome, the size, the reason. */
  @Input() more = '';
  @Input() chip = '';
  @Input() tone: RecordTone = 'muted';
  /** The tone in a word, for someone who cannot see the dot. */
  @Input() outcome = '';
  @Input() count = 1;
  @Input() open = false;
  /** False for a row with nothing more to show. */
  @Input() canOpen = true;
  @Output() toggle = new EventEmitter<void>();
}
