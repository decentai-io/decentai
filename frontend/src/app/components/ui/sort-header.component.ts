import { NgIf } from '@angular/common';
import { Component, EventEmitter, HostBinding, Input, Output } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

/** What a list is sorted by, and which way. */
export interface Sort {
  key: string;
  direction: 'asc' | 'desc';
}

/**
 * A column heading that sorts the table (class="table",
 * styles/controls.scss) by its column: a click sorts by it, a second
 * click turns the order round.
 *
 *   <th appSort="name" [sort]="sort" (sortChange)="sortBy($event)">Name</th>
 */
@Component({
  selector: 'th[appSort]',
  standalone: true,
  imports: [NgIf, LucideAngularModule],
  styleUrl: './sort-header.component.css',
  template: `
    <button type="button" class="sort-button" (click)="toggle()">
      <ng-content></ng-content>
      <lucide-icon
        *ngIf="active"
        [name]="sort.direction === 'asc' ? 'arrow-up' : 'arrow-down'"
        [size]="12"
        aria-hidden="true"
      ></lucide-icon>
    </button>
  `,
})
export class SortHeaderComponent {
  /** The column's key. */
  @Input({ required: true, alias: 'appSort' }) key = '';
  @Input({ required: true }) sort: Sort = { key: '', direction: 'asc' };
  @Output() sortChange = new EventEmitter<Sort>();

  get active(): boolean {
    return this.sort.key === this.key;
  }

  @HostBinding('attr.aria-sort')
  get ariaSort(): 'ascending' | 'descending' | 'none' {
    if (!this.active) return 'none';
    return this.sort.direction === 'asc' ? 'ascending' : 'descending';
  }

  toggle(): void {
    const turned = this.active && this.sort.direction === 'asc';
    this.sortChange.emit({ key: this.key, direction: turned ? 'desc' : 'asc' });
  }
}
