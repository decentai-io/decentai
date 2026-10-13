import { NgIf } from '@angular/common';
import { Component, Input } from '@angular/core';

/**
 * The one card of everything before the app: loading, signing in (in
 * each of its modes), signed out, and nothing to open. The brand, a
 * title, a sentence, and whatever the screen puts under them, in the
 * app's own type and greys.
 *
 *   <app-entry-card title="Sign in" sentence="Enter your email and password.">
 *     <form class="form" …>…</form>
 *   </app-entry-card>
 */
@Component({
  selector: 'app-entry-card',
  standalone: true,
  imports: [NgIf],
  styleUrl: './entry-card.component.css',
  template: `
    <main class="entry-card">
      <p class="entry-brand">DecentAI</p>
      <h1 class="entry-title">
        <span class="entry-spinner" *ngIf="busy" aria-hidden="true"></span>
        {{ title }}
      </h1>
      <p class="entry-sentence" *ngIf="sentence">{{ sentence }}</p>
      <div class="entry-body"><ng-content></ng-content></div>
    </main>
  `,
})
export class EntryCardComponent {
  @Input({ required: true }) title = '';
  @Input() sentence = '';
  /** Something is on its way: a spinner sits beside the title. */
  @Input() busy = false;
}
