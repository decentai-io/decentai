import { NgClass, NgFor, NgIf } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { RouterLink } from '@angular/router';

import { AgentMarkComponent } from 'src/app/components/ui/agent-mark.component';

/** The chip at a card's top right: a word, and the chip's variant
 *  (styles/controls.scss; '' is the plain one). */
export interface CardChip {
  word: string;
  tone: '' | 'danger' | 'live' | 'warn' | 'accent';
}

/**
 * One agent, as a card: on the Installed page and in the Marketplace
 * alike, so an agent looks the same before it is approved and after.
 *
 *   <app-agent-card name="Notebook" icon="notebook-pen" version="v2.0.9"
 *                   [chip]="standing(agent)" [link]="['/agents', agent.agent_id]"
 *                   description="Your working memory across conversations."
 *                   [facts]="['8 functions', '1 credential']">
 *     <app-switch cardFoot label="In new chats" …></app-switch>
 *   </app-agent-card>
 *
 * The mark, the name and the words are one link to `link` (the agent's
 * page, or its review). What is marked `cardFoot` is the card's own
 * controls, under a hairline. With `selectable`, a tick box sits at the
 * top left: seen when the card is pointed at or holds the focus, and
 * always once something is ticked (`selecting`) or under a finger.
 */
@Component({
  selector: 'app-agent-card',
  standalone: true,
  imports: [NgClass, NgFor, NgIf, RouterLink, MatCheckboxModule, AgentMarkComponent],
  styleUrl: './agent-card.component.css',
  host: {
    '[class.is-selectable]': 'selectable',
    '[class.is-selected]': 'selected',
    '[class.is-selecting]': 'selecting',
  },
  template: `
    <mat-checkbox
      *ngIf="selectable"
      class="card-check"
      [checked]="selected"
      [attr.aria-label]="'Select ' + name"
      (change)="selectedChange.emit($event.checked)"
    ></mat-checkbox>

    <a class="card-main" [routerLink]="link">
      <app-agent-mark [name]="name" [icon]="icon"></app-agent-mark>
      <span class="card-words">
        <span class="card-title">
          <span class="card-name">{{ name }}</span>
          <span class="card-version" *ngIf="version">{{ version }}</span>
          <span class="chip card-chip" *ngIf="chip" [ngClass]="chip.tone">{{ chip.word }}</span>
        </span>
        <span class="card-description" *ngIf="description">{{ description }}</span>
        <span class="card-facts" *ngIf="facts.length">{{ facts.join(' · ') }}</span>
      </span>
    </a>

    <!-- What went wrong with it, in the words it was reported in. -->
    <p class="card-error" *ngFor="let problem of problems">{{ problem }}</p>

    <div class="card-foot"><ng-content select="[cardFoot]"></ng-content></div>
  `,
})
export class AgentCardComponent {
  @Input({ required: true }) name = '';
  /** The manifest's icon for the mark, when it names one. */
  @Input() icon: string | null | undefined = '';
  /** "v2.0.9", said once beside the name. */
  @Input() version = '';
  @Input() chip: CardChip | null = null;
  @Input() description = '';
  /** What a person compares: how many functions, what it needs, where
   *  it connects. */
  @Input() facts: string[] = [];
  @Input() problems: string[] = [];
  /** Where the card goes: the agent's page, or its review. */
  @Input({ required: true }) link: any[] | string = [];

  @Input() selectable = false;
  @Input() selected = false;
  /** Something on the page is ticked, so every box shows. */
  @Input() selecting = false;
  @Output() selectedChange = new EventEmitter<boolean>();
}
