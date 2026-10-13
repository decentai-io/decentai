import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from '@angular/core';
import { MarkdownService } from 'ngx-markdown';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { headingsDown } from 'src/app/components/ui/markdown-levels';
import { Skill, SkillsService } from 'src/app/services/skills.service';
import { ChatMarkdownService } from '../../ai/chat/chat-message/chat-markdown.service';

/**
 * One skill, read: its title, when it applies, who wrote it and who
 * can use it, and the skill itself as the assistant reads it once it
 * decides the skill applies. Reading is its own view so that seeing a
 * skill never means opening it for change.
 *
 * The list carries the title and the summary; the body is read here,
 * as the chat's own markdown with what it would load from elsewhere
 * taken out. The page hands the actions in as `skillActions`.
 */
@Component({
  selector: 'app-skill-view',
  standalone: false,
  templateUrl: './skill-view.component.html',
  styleUrl: './skill-view.component.css',
  providers: [{ provide: MarkdownService, useClass: ChatMarkdownService }],
})
export class SkillViewComponent implements OnChanges {
  @Input({ required: true }) skill!: Skill;
  /** Who can use it, in the words the list uses. */
  @Input() sharing = '';

  @Output() closed = new EventEmitter<void>();

  loading = false;
  /** The skill itself, with its headings under the panel's own. */
  body = '';
  /** Its body is encrypted under a key this deployment no longer has. */
  unreadable = false;
  problem = '';

  constructor(private service: SkillsService) {}

  ngOnChanges(changes: SimpleChanges): void {
    const before = changes['skill']?.previousValue as Skill | undefined;
    // The same skill handed in again after a save is read again: its
    // body may be what changed.
    if (before?.resource_ref !== this.skill.resource_ref || before?.updated_at !== this.skill.updated_at) {
      void this.read();
    }
  }

  private async read(): Promise<void> {
    const ref = this.skill.resource_ref;
    this.loading = true;
    this.body = '';
    this.unreadable = false;
    this.problem = '';
    try {
      const full = await this.service.get(ref);
      // Another skill was opened while this one was on its way.
      if (ref !== this.skill.resource_ref) return;
      if (!full) {
        this.problem = 'That skill could not be read just now.';
        return;
      }
      this.unreadable = !!full.unreadable;
      this.body = headingsDown(full.values?.body ?? '', 2);
    } finally {
      if (ref === this.skill.resource_ref) this.loading = false;
    }
  }

  get author(): string {
    return this.skill.created_by_name || '';
  }

  get updated(): string {
    // Mid-sentence: "updated just now".
    return relativeDate(this.skill.updated_at || this.skill.created_at || '').replace(/^J/, 'j');
  }

  get updatedInFull(): string {
    return fullDate(this.skill.updated_at || this.skill.created_at || '');
  }

  get created(): string {
    return fullDate(this.skill.created_at || '');
  }
}
