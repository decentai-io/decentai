import { NgFor, NgIf } from '@angular/common';
import { Component, Input } from '@angular/core';

/**
 * The frame of every list: a toolbar (search, a filter, the Add
 * button), the rows, and a foot line for the counts.
 *
 *   <app-list label="Model providers" [loading]="loading" foot="3 active · 7 available">
 *     <ng-container listToolbar> … </ng-container>
 *     <app-row *ngFor="let provider of providers" …></app-row>
 *     <app-empty *ngIf="!providers.length" …></app-empty>
 *   </app-list>
 *
 * While `loading`, it shows three rows' worth of skeleton in place of
 * what is inside it.
 */
@Component({
  selector: 'app-list',
  standalone: true,
  imports: [NgFor, NgIf],
  styleUrl: './list.component.css',
  template: `
    <div class="list-toolbar"><ng-content select="[listToolbar]"></ng-content></div>
    <div class="list-rows" *ngIf="loading" aria-busy="true" [attr.aria-label]="'Loading ' + label">
      <div class="list-skeleton" *ngFor="let row of skeletonRows">
        <span class="sk sk-mark"></span>
        <span class="sk-words"><span class="sk sk-name"></span><span class="sk sk-meta"></span></span>
      </div>
    </div>
    <div class="list-rows" *ngIf="!loading" role="list" [attr.aria-label]="label">
      <ng-content></ng-content>
    </div>
    <p class="list-foot" *ngIf="foot && !loading">{{ foot }}</p>
  `,
})
export class ListComponent {
  /** What the rows are, for a screen reader: "Model providers". */
  @Input({ required: true }) label = '';
  @Input() loading = false;
  /** The counts under the rows: "3 active · 7 available". */
  @Input() foot = '';

  readonly skeletonRows = [0, 1, 2];
}
