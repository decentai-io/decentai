import { NgFor, NgIf, NgTemplateOutlet } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';
import { AgentMarkComponent } from './agent-mark.component';

/** One thing a list can be narrowed to. */
export interface FilterItem {
  value: string;
  name: string;
  /** How many rows it holds, shown at its right. */
  count?: number;
  /** A Lucide icon before the name… */
  icon?: string;
  /** …or the agent whose mark goes there. */
  agent?: { name: string; icon?: string | null };
  /** What it opens into, shown while it or one of them is chosen (an
   *  agent's kinds). */
  children?: FilterItem[];
}

/** A set of filters of one sort (sources, types), one chosen at most. */
export interface FilterGroup {
  /** The group's name in `chosen`: 'source', 'type'. */
  key: string;
  /** Its heading; a group without one is the list's first choice. */
  label?: string;
  items: FilterItem[];
  /** One is always chosen. Without this, choosing the chosen item
   *  again clears the group. */
  required?: boolean;
}

/**
 * The filters of a list, in a column at its left: groups of things the
 * list can be narrowed to, each with its count. Under 900px the column
 * is a row of chips above the list.
 *
 *   <div class="side-layout">
 *     <app-filter-column label="Filter files" [groups]="filters"
 *                        [chosen]="chosen" (choose)="filter($event)"></app-filter-column>
 *     <div> …the list… </div>
 *   </div>
 *
 * `chosen` maps each group's key to its chosen value ('' for none);
 * `choose` says which group changed and to what.
 */
@Component({
  selector: 'app-filter-column',
  standalone: true,
  imports: [NgFor, NgIf, NgTemplateOutlet, LucideAngularModule, AgentMarkComponent],
  templateUrl: './filter-column.component.html',
  styleUrl: './filter-column.component.css',
})
export class FilterColumnComponent {
  /** What the column is, for a screen reader: "Filter files". */
  @Input({ required: true }) label = '';
  @Input({ required: true }) groups: FilterGroup[] = [];
  @Input({ required: true }) chosen: Record<string, string> = {};

  @Output() choose = new EventEmitter<{ key: string; value: string }>();

  isChosen(group: FilterGroup, item: FilterItem): boolean {
    return this.chosen[group.key] === item.value;
  }

  /** An item is open while it, or one of what it opens into, is chosen. */
  isOpen(group: FilterGroup, item: FilterItem): boolean {
    const chosen = this.chosen[group.key];
    return !!item.children?.length
      && (chosen === item.value || item.children.some((child) => child.value === chosen));
  }

  pick(group: FilterGroup, item: FilterItem): void {
    const again = this.isChosen(group, item);
    if (again && group.required) return;
    this.choose.emit({ key: group.key, value: again ? '' : item.value });
  }
}
