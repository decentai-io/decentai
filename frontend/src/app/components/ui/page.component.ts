import { NgIf } from '@angular/common';
import { Component, Input } from '@angular/core';

/**
 * The frame of a page: its one title, one sentence saying what the page
 * is, and under them the page itself.
 *
 *   <app-page title="Monitoring" sentence="What agents use now, and what was seen of them.">
 *     <app-segmented pageActions …></app-segmented>
 *     …
 *   </app-page>
 *
 * What is marked `pageActions` sits at the right of the title. A page
 * of fields is read at 720px; a page that is a table or a list of wide
 * rows sets `wide`.
 */
@Component({
  selector: 'app-page',
  standalone: true,
  imports: [NgIf],
  styleUrl: './page.component.css',
  template: `
    <header class="page-head">
      <div class="page-words">
        <h1 class="page-title">{{ title }}</h1>
        <p class="page-sentence" *ngIf="sentence">{{ sentence }}</p>
      </div>
      <div class="page-actions"><ng-content select="[pageActions]"></ng-content></div>
    </header>
    <ng-content></ng-content>
  `,
  host: { '[class.is-wide]': 'wide' },
})
export class PageComponent {
  @Input({ required: true }) title = '';
  @Input() sentence = '';
  @Input() wide = false;
}
