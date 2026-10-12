import { NgIf } from '@angular/common';
import { Component, Input } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

/**
 * What a list shows when it has nothing, and what a page shows when it
 * could not load: the kind's icon, a title, one line, and the action
 * that changes it.
 *
 *   <app-empty icon="key-round" title="No API keys yet"
 *              line="A key lets a script act as you.">
 *     <button class="btn primary" (click)="create()">Create a key</button>
 *   </app-empty>
 */
@Component({
  selector: 'app-empty',
  standalone: true,
  imports: [NgIf, LucideAngularModule],
  styleUrl: './empty.component.css',
  host: { role: 'status' },
  template: `
    <lucide-icon class="empty-icon" [name]="icon" [size]="24" [strokeWidth]="1.75" aria-hidden="true"></lucide-icon>
    <p class="empty-title">{{ title }}</p>
    <p class="empty-line" *ngIf="line">{{ line }}</p>
    <div class="empty-action"><ng-content></ng-content></div>
  `,
})
export class EmptyComponent {
  /** A Lucide icon's name, from the set registered in app.module.ts. */
  @Input({ required: true }) icon = '';
  @Input({ required: true }) title = '';
  @Input() line = '';
}
