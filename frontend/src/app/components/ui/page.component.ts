import { NgIf } from '@angular/common';
import { Component, Input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';

import { GuidePanelService } from 'src/app/pages/app/guide/guide-panel.service';

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
 * rows sets `wide`. `guide` names the chapter of the Guide that
 * explains the page ("chats", or "chats#approvals" for one heading):
 * the sentence then ends in a link to it, and a "?" beside the title
 * opens the chapter in a panel beside the page.
 */
@Component({
  selector: 'app-page',
  standalone: true,
  imports: [NgIf, RouterLink, LucideAngularModule],
  styleUrl: './page.component.css',
  template: `
    <header class="page-head">
      <div class="page-words">
        <h1 class="page-title">
          {{ title }}
          <button class="icon-btn sm page-help" type="button" *ngIf="guide"
                  [attr.aria-label]="'About ' + title + ': open the Guide beside this page'" (click)="guidePanel.open(guide)">
            <lucide-icon name="circle-help" [size]="16" aria-hidden="true"></lucide-icon>
          </button>
        </h1>
        <p class="page-sentence" *ngIf="sentence">
          {{ sentence }}
          <a class="page-guide" *ngIf="guide" [routerLink]="['/guide', chapter]" [fragment]="heading">Learn more</a>
        </p>
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
  /** The Guide's chapter for this page, with a heading after a #. */
  @Input() guide = '';

  constructor(readonly guidePanel: GuidePanelService) {}

  get chapter(): string { return this.guide.split('#')[0]; }
  get heading(): string | undefined { return this.guide.split('#')[1] || undefined; }
}
