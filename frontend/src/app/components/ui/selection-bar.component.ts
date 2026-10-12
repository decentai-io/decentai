import { NgIf } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';

/**
 * What a list's toolbar becomes while rows are ticked: how many, a way
 * to tick every row shown, a way to clear, and what can be done to
 * them, placed inside the tag.
 *
 *   <app-selection-bar *ngIf="selected.size" [count]="selected.size"
 *                      [shown]="selectable.length" (selectAll)="…" (clear)="…">
 *     <button class="btn danger sm" (click)="deleteSelected()">Delete</button>
 *   </app-selection-bar>
 */
@Component({
  selector: 'app-selection-bar',
  standalone: true,
  imports: [NgIf],
  styleUrl: './selection-bar.component.css',
  host: { role: 'status' },
  template: `
    <span class="selection-count">{{ count }} selected</span>
    <button type="button" class="btn ghost sm" *ngIf="count < shown" (click)="selectAll.emit()">
      Select all {{ shown }}
    </button>
    <button type="button" class="btn ghost sm" (click)="clear.emit()">Clear</button>
    <span class="selection-actions"><ng-content></ng-content></span>
  `,
})
export class SelectionBarComponent {
  /** How many rows are ticked. */
  @Input({ required: true }) count = 0;
  /** How many rows shown could be ticked. */
  @Input() shown = 0;

  @Output() selectAll = new EventEmitter<void>();
  @Output() clear = new EventEmitter<void>();
}
