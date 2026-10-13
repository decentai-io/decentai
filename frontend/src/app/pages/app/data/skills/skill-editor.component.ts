import { Component, ElementRef, HostListener, OnInit, ViewChild } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { MarkdownService } from 'ngx-markdown';

import { Draft } from 'src/app/components/ui/draft';
import { headingsDown } from 'src/app/components/ui/markdown-levels';
import { Choice } from 'src/app/components/ui/radio-keys';
import { HasUnsavedChanges } from 'src/app/guards/unsaved-changes.guard';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import { Skill, SkillsService } from 'src/app/services/skills.service';
import { ChatMarkdownService } from '../../ai/chat/chat-message/chat-markdown.service';
import { ShareEditor } from '../../share-editor';

/** The three things a skill is made of. */
interface SkillText {
  title: string;
  summary: string;
  body: string;
}

/** What the writing area's toolbar can do to the selection. */
type Mark = 'bold' | 'italic' | 'heading' | 'list' | 'numbered' | 'code' | 'link';

/** The same limits the backend holds a skill to. */
export const SKILL_LIMITS = { title: 120, summary: 300, body: 20000 };

const EXAMPLE: SkillText = {
  title: 'Clear, useful writing',
  summary: 'Use when drafting or revising professional messages, explanations, and updates',
  body: `Write in plain, direct language.

- Lead with the main point or requested action.
- Keep each paragraph focused on one idea.
- Prefer concrete verbs and familiar words.
- Include only context the reader needs to decide or act.
- Put steps in the order they should happen.
- Match the reader's level of technical knowledge.
- End with the next action, owner, or decision when needed.

Before sending, remove repetition and vague filler. Preserve important facts, constraints, dates, and names.`,
};

/**
 * Writing a skill, or changing one: a page of its own, at
 * /data/skills/new and /data/skills/<ref>/edit, so that what is being
 * written is not lost to a click outside a dialog.
 *
 * A skill is a title, one line saying when it applies, and the skill
 * itself. The assistant sees the first two for every skill and reads
 * the third only once it decides the skill applies, which is why the
 * line matters and the page says so. The skill itself is written as
 * markdown, with a preview of how it reads.
 *
 * Nothing is saved until Save. Leaving with changes asks first (the
 * route names the guard), and so does reloading the tab.
 */
@Component({
  selector: 'app-skill-editor',
  standalone: false,
  templateUrl: './skill-editor.component.html',
  styleUrl: './skill-editor.component.css',
  // The preview is the chat's markdown, as the reading view's is.
  providers: [{ provide: MarkdownService, useClass: ChatMarkdownService }],
})
export class SkillEditorComponent implements OnInit, HasUnsavedChanges {
  /** The skill being changed; '' while one is being written. */
  ref = '';
  loading = true;
  /** Why the skill to change could not be opened. */
  problem = '';
  /** Its body cannot be read: what is written here replaces it. */
  unreadable = false;

  readonly limits = SKILL_LIMITS;
  readonly draft = new Draft<SkillText>({ title: '', summary: '', body: '' });
  /** Who can use it, and what that was when the page opened. */
  readonly share = new ShareEditor(() => this.profile);
  private sharedAtFirst = '';

  tab: 'write' | 'preview' = 'write';
  readonly tabs: Choice<'write' | 'preview'>[] = [
    { value: 'write', name: 'Write' },
    { value: 'preview', name: 'Preview' },
  ];
  readonly marks: { mark: Mark; icon: string; name: string }[] = [
    { mark: 'bold', icon: 'bold', name: 'Bold' },
    { mark: 'italic', icon: 'italic', name: 'Italic' },
    { mark: 'heading', icon: 'heading', name: 'Heading' },
    { mark: 'list', icon: 'list', name: 'List' },
    { mark: 'numbered', icon: 'list-ordered', name: 'Numbered list' },
    { mark: 'code', icon: 'code', name: 'Code' },
    { mark: 'link', icon: 'link', name: 'Link' },
  ];

  /** Save was pressed: what is missing is said under its field. */
  tried = false;
  saving = false;
  saveError = '';

  private profile: Profile | null = null;
  /** The other skills' titles: two skills may not share one. */
  private titles: string[] = [];
  /** Escape was the last key in the writing area: the next Tab leaves
   *  it, where a Tab otherwise indents. */
  private leaving = false;

  @ViewChild('area') private area?: ElementRef<HTMLTextAreaElement>;

  constructor(
    private service: SkillsService,
    private profiles: ProfileService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
    private confirm: ConfirmService,
  ) {}

  async ngOnInit(): Promise<void> {
    this.ref = this.route.snapshot.paramMap.get('ref') || '';
    const [skills, profile, peers] = await Promise.all([
      this.service.list(),
      this.profiles.get(),
      this.profiles.peers().catch(() => []),
    ]);
    this.profile = profile;
    this.share.peers = peers;
    this.titles = skills.filter((skill) => skill.resource_ref !== this.ref).map((skill) => skill.keys.title);

    if (this.ref) await this.open(this.ref);
    else {
      this.share.open('new', undefined);
      if (this.route.snapshot.queryParamMap.get('example')) this.draft.take({ ...EXAMPLE });
    }
    this.sharedAtFirst = this.sharedNow;
    this.loading = false;
  }

  /** The list is the catalog; the body needs its own read. */
  private async open(ref: string): Promise<void> {
    const full = await this.service.get(ref);
    if (!full) {
      this.problem = 'That skill could not be opened. It may have been deleted, or is not yours to change.';
      return;
    }
    this.unreadable = !!full.unreadable;
    this.draft.take({ title: full.keys.title, summary: full.keys.summary, body: full.values?.body ?? '' });
    this.share.open(ref, full.owner);
  }

  // ── What changed ────────────────────────────────────────────────────

  private get sharedNow(): string {
    return JSON.stringify([this.share.mode, [...this.share.groups].sort(), [...this.share.users].sort()]);
  }

  get dirty(): boolean {
    return this.draft.dirty || this.sharedNow !== this.sharedAtFirst;
  }

  /** For the guard on the way out of the page. */
  unsavedChangesTo(): string {
    return this.dirty && !this.saving ? 'this skill' : '';
  }

  /** Reloading or closing the tab asks too, in the browser's own words. */
  @HostListener('window:beforeunload', ['$event'])
  onUnload(event: BeforeUnloadEvent): void {
    if (this.dirty) event.preventDefault();
  }

  // ── What is wrong ───────────────────────────────────────────────────

  /** Told as it is typed: a title another skill has. */
  get duplicateTitle(): boolean {
    const normal = (value: string) => value.trim().replace(/\s+/g, ' ').toLowerCase();
    const mine = normal(this.draft.value.title);
    return !!mine && this.titles.some((title) => normal(title) === mine);
  }

  get titleError(): string {
    if (this.duplicateTitle) return 'Another skill has this title. Choose a different one.';
    return this.tried && !this.draft.value.title.trim() ? 'Give the skill a title.' : '';
  }

  get summaryError(): string {
    return this.tried && !this.draft.value.summary.trim() ? 'Say when this skill applies.' : '';
  }

  get bodyError(): string {
    return this.tried && !this.draft.value.body.trim() ? 'Write the skill itself.' : '';
  }

  get shareError(): string {
    if (!this.tried || this.share.ready) return '';
    return this.share.mode === 'groups' ? 'Tick at least one group.' : 'Tick at least one person.';
  }

  /** The last tenth of a limit: the counter says so. */
  near(length: number, limit: number): boolean {
    return length >= limit * 0.9;
  }

  // ── The writing area ────────────────────────────────────────────────

  get preview(): string {
    return headingsDown(this.draft.value.body, 2);
  }

  /** Tab indents by two spaces. Escape, then Tab, leaves the area: a
   *  keyboard has to be able to get out. */
  onAreaKey(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      this.leaving = true;
      return;
    }
    if (event.key === 'Tab' && !event.shiftKey && !this.leaving) {
      event.preventDefault();
      this.replaceSelection((chosen) => ({ text: '  ' + chosen, select: [2, 2 + chosen.length] }));
    }
    this.leaving = false;
  }

  /** Puts the toolbar's mark around the selection, or at the start of
   *  its line, and leaves the selection on what was marked. */
  apply(mark: Mark): void {
    const line = (prefix: string) => this.prefixLines(prefix);
    switch (mark) {
      case 'bold': return this.wrap('**', '**', 'bold text');
      case 'italic': return this.wrap('*', '*', 'italic text');
      case 'link': return this.wrap('[', '](https://)', 'link text');
      case 'code': {
        const chosen = this.selected();
        return chosen.includes('\n') ? this.wrap('```\n', '\n```', 'code') : this.wrap('`', '`', 'code');
      }
      case 'heading': return line('## ');
      case 'list': return line('- ');
      default: return line('1. ');
    }
  }

  private selected(): string {
    const box = this.area?.nativeElement;
    return box ? box.value.slice(box.selectionStart, box.selectionEnd) : '';
  }

  private wrap(before: string, after: string, placeholder: string): void {
    this.replaceSelection((chosen) => {
      const inner = chosen || placeholder;
      return { text: before + inner + after, select: [before.length, before.length + inner.length] };
    });
  }

  /** Puts `prefix` at the start of every line the selection touches. */
  private prefixLines(prefix: string): void {
    const box = this.area?.nativeElement;
    if (!box) return;
    const from = box.value.lastIndexOf('\n', box.selectionStart - 1) + 1;
    box.setSelectionRange(from, box.selectionEnd);
    this.replaceSelection((chosen) => {
      const text = chosen.split('\n').map((each) => prefix + each).join('\n');
      return { text, select: [0, text.length] };
    });
  }

  /** Replaces the selection with what `change` makes of it, keeps the
   *  draft in step, and selects the part `change` names. */
  private replaceSelection(change: (chosen: string) => { text: string; select: [number, number] }): void {
    const box = this.area?.nativeElement;
    if (!box) return;
    const start = box.selectionStart;
    const made = change(box.value.slice(start, box.selectionEnd));
    const body = box.value.slice(0, start) + made.text + box.value.slice(box.selectionEnd);
    if (body.length > this.limits.body) return;
    this.draft.value.body = body;
    box.value = body;
    box.focus();
    box.setSelectionRange(start + made.select[0], start + made.select[1]);
  }

  /** The example replaces what is written, so it asks when something is. */
  async insertExample(): Promise<void> {
    if (this.draft.value.body.trim()) {
      const sure = await this.confirm.ask({
        title: 'Replace what you wrote with the example?',
        body: 'The skill itself is replaced. The title and when it applies are filled in only where they are empty.',
        confirm: 'Replace',
        danger: true,
      });
      if (!sure) return;
    }
    const value = this.draft.value;
    value.title = value.title.trim() ? value.title : EXAMPLE.title;
    value.summary = value.summary.trim() ? value.summary : EXAMPLE.summary;
    value.body = EXAMPLE.body;
    this.tab = 'write';
  }

  // ── Saving and leaving ──────────────────────────────────────────────

  async save(): Promise<void> {
    if (this.saving) return;
    this.tried = true;
    this.saveError = '';
    if (this.titleError || this.summaryError || this.bodyError || this.shareError) {
      this.saveError = 'Something is missing above.';
      return;
    }

    this.saving = true;
    try {
      const fields = {
        title: this.draft.value.title.trim(),
        summary: this.draft.value.summary.trim(),
        body: this.draft.value.body.trim(),
        owner: this.share.owner() as Record<string, any>,
      };
      const result = this.ref
        ? await this.service.update(this.ref, fields)
        : await this.service.create(fields);
      if (result.error) {
        this.saveError = result.error;
        return;
      }
      // Saved: nothing is unsaved, so the way out asks nothing.
      this.draft.take({ title: fields.title, summary: fields.summary, body: fields.body });
      this.sharedAtFirst = this.sharedNow;
      await this.leave(result.resource, !this.ref);
    } finally {
      this.saving = false;
    }
  }

  /** To the skill as it now reads, with what the list should say. */
  private async leave(saved: Skill | undefined, created: boolean): Promise<void> {
    const ref = saved?.resource_ref || this.ref;
    await this.router.navigate(ref ? ['/data/skills', ref] : ['/data/skills'], {
      state: { skillSaved: created ? 'created' : 'updated', ref },
    });
  }

  /** Back to what was there when the page opened. */
  discard(): void {
    this.draft.discard();
    const [mode, groups, users] = JSON.parse(this.sharedAtFirst);
    this.share.mode = mode;
    this.share.groups = new Set(groups);
    this.share.users = new Set(users);
    this.tried = false;
    this.saveError = '';
  }

  get canShareAny(): boolean {
    return this.auth.can('skills:skill:set_owner_any');
  }

  /** Where Back goes: the skill being changed, or the list. */
  get back(): string[] {
    return this.ref ? ['/data/skills', this.ref] : ['/data/skills'];
  }
}
