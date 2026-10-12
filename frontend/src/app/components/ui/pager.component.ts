import { NgIf } from '@angular/common';
import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

/**
 * The foot of a list: which rows are shown of how many, and the way to
 * the next and the previous page.
 *
 *   <app-pager [page]="page" [pageSize]="20" [total]="shown.length"
 *              (pageChange)="page = $event"></app-pager>
 *
 * When rows go (a delete, a narrower search) and the page is past the
 * last one, it asks for the last one.
 */
@Component({
  selector: 'app-pager',
  standalone: true,
  imports: [NgIf, LucideAngularModule],
  styleUrl: './pager.component.css',
  host: { '[hidden]': 'total === 0' },
  template: `
    <ng-container *ngIf="total > 0">
      <span class="pager-range">Showing {{ first }}–{{ last }} of {{ total }}</span>
      <nav class="pager-buttons" aria-label="Pages" *ngIf="pageCount > 1">
        <button
          type="button"
          class="icon-btn sm"
          aria-label="Previous page"
          [disabled]="page <= 1"
          (click)="pageChange.emit(page - 1)"
        >
          <lucide-icon name="chevron-left" [size]="16" aria-hidden="true"></lucide-icon>
        </button>
        <button
          type="button"
          class="icon-btn sm"
          aria-label="Next page"
          [disabled]="page >= pageCount"
          (click)="pageChange.emit(page + 1)"
        >
          <lucide-icon name="chevron-right" [size]="16" aria-hidden="true"></lucide-icon>
        </button>
      </nav>
    </ng-container>
  `,
})
export class PagerComponent implements OnChanges {
  /** The page shown, from 1. */
  @Input({ required: true }) page = 1;
  @Input({ required: true }) pageSize = 20;
  /** How many rows there are in all. */
  @Input({ required: true }) total = 0;

  @Output() pageChange = new EventEmitter<number>();

  get pageCount(): number {
    return Math.max(1, Math.ceil(this.total / this.pageSize));
  }

  get first(): number {
    return (this.page - 1) * this.pageSize + 1;
  }

  get last(): number {
    return Math.min(this.page * this.pageSize, this.total);
  }

  ngOnChanges(): void {
    if (this.page > this.pageCount) {
      // After this pass: the page that owns `page` is being checked now.
      queueMicrotask(() => this.pageChange.emit(this.pageCount));
    }
  }
}
