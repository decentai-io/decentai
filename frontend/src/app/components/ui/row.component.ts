import { Component, Input } from '@angular/core';

/**
 * One row of a list: a mark, a name with its chips, a line of detail,
 * and the actions at the right.
 *
 *   <app-row name="OpenAI">
 *     <lucide-icon rowMark name="cloud" [size]="20"></lucide-icon>
 *     <span rowChips class="chip accent">Default</span>
 *     <ng-container rowMeta>OpenAI-compatible · starts with gpt-5</ng-container>
 *     <button rowActions class="icon-btn sm" aria-label="Edit OpenAI">…</button>
 *   </app-row>
 *
 * A name too long for an attribute (a memory's text) goes inside the
 * tag instead, marked `rowName`. Rows live in an <app-list>.
 */
@Component({
  selector: 'app-row',
  standalone: true,
  styleUrl: './row.component.css',
  host: { role: 'listitem' },
  template: `
    <div class="row-mark"><ng-content select="[rowMark]"></ng-content></div>
    <div class="row-body">
      <div class="row-title">
        <span class="row-name">{{ name }}<ng-content select="[rowName]"></ng-content></span>
        <ng-content select="[rowChips]"></ng-content>
      </div>
      <div class="row-meta"><ng-content select="[rowMeta]"></ng-content></div>
      <ng-content></ng-content>
    </div>
    <div class="row-actions"><ng-content select="[rowActions]"></ng-content></div>
  `,
})
export class RowComponent {
  @Input() name = '';
}
