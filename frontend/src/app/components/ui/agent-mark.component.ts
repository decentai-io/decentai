import { NgIf } from '@angular/common';
import { Component, Inject, Input, OnChanges } from '@angular/core';
import { LUCIDE_ICONS, LucideAngularModule, LucideIconProviderInterface } from 'lucide-angular';

/** The first letters of up to two words: "Google Calendar" is GC,
 *  "Notebook" is N. */
export function initialsOf(name: string): string {
  return name
    .split(/[\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0].toUpperCase())
    .join('');
}

/**
 * An agent's face: a small tile with its initials, or with the icon
 * its manifest names. Wherever an agent is shown (a card, a row, a
 * filter, a chip) this is how the eye tells one from the next, with no
 * colour of its own.
 *
 *   <app-agent-mark name="Google Calendar" icon="calendar"></app-agent-mark>
 *
 * `icon` is a Lucide icon's name; one the app does not carry
 * (app.module.ts registers the set) falls back to the initials.
 * `size="sm"` is the 20px mark for a row or a chip.
 */
@Component({
  selector: 'app-agent-mark',
  standalone: true,
  imports: [NgIf, LucideAngularModule],
  styleUrl: './agent-mark.component.css',
  host: { 'aria-hidden': 'true', '[class.is-small]': "size === 'sm'" },
  template: `
    <lucide-icon *ngIf="shownIcon" [name]="shownIcon" [size]="size === 'sm' ? 12 : 18" [strokeWidth]="1.75"></lucide-icon>
    <ng-container *ngIf="!shownIcon">{{ initials }}</ng-container>
  `,
})
export class AgentMarkComponent implements OnChanges {
  /** The agent's name, which the initials are taken from. */
  @Input({ required: true }) name = '';
  /** The manifest's icon, when it gives one. */
  @Input() icon: string | null | undefined = '';
  @Input() size: 'sm' | 'md' = 'md';

  initials = '';
  /** The icon, when the app carries it; else ''. */
  shownIcon = '';

  constructor(@Inject(LUCIDE_ICONS) private carried: LucideIconProviderInterface[]) {}

  ngOnChanges(): void {
    // A small mark has room for one letter.
    this.initials = initialsOf(this.name).slice(0, this.size === 'sm' ? 1 : 2);
    this.shownIcon = this.icon && this.isCarried(this.icon) ? this.icon : '';
  }

  /** Lucide keeps its icons by their class name: "calendar-clock" is
   *  CalendarClock. */
  private isCarried(icon: string): boolean {
    const className = icon.replace(/(^|-)([a-z0-9])/g, (_match, _dash, letter) => letter.toUpperCase());
    return this.carried.some((icons) => icons.hasIcon(className));
  }
}
