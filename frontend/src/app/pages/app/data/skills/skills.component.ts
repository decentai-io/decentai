import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { FilterGroup } from 'src/app/components/ui/filter-column.component';
import { Choice } from 'src/app/components/ui/radio-keys';
import { ConfirmService } from 'src/app/services/confirm.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import { Skill, SkillsService } from 'src/app/services/skills.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataPageBase } from '../../data-page-base';
import { STANDARD_SKILLS_LISTED } from '../../ai/chat/chat-defaults';
import { ShareEditor, SharingChip } from '../../share-editor';

/** Which skills are listed, and in what order. */
type Show = 'all' | 'mine' | 'shared' | 'on';
type SkillSort = 'updated' | 'title' | 'author';

/** How long "Saved" stays beside a switch. */
const SAVED_FOR_MS = 2000;

/**
 * Skills: knowledge people write for the assistant. The chat's main loop
 * sees every visible skill as one catalog line (title + summary) and
 * reads the full body only when it judges the skill relevant — so the
 * title and summary are what the MODEL chooses by, and the page says so.
 *
 * One list, with a column at its left that narrows it (whose skills,
 * and which are on for new chats). A skill's title opens it to be read
 * at /data/skills/<ref>, beside the list on a wide screen. What is
 * chosen, the search and the sort are in the address.
 *
 * Writing a skill and changing one are a page of their own
 * (skill-editor.component): this page only leads there.
 */
@Component({
  selector: 'app-skills',
  standalone: false,
  templateUrl: './skills.component.html',
  styleUrl: './skills.component.css',
})
export class SkillsComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  skills: Skill[] = [];
  profile: Profile | null = null;

  peers: { user_id: string; user_name: string; email: string }[] = [];
  busyRef = '';

  // ── What the address says ───────────────────────────────────────────
  show: Show = 'all';
  query = '';
  sort: SkillSort = 'updated';
  /** The skill opened to be read; '' for none. */
  openRef = '';
  private address = new Subscription();

  readonly sortChoices: Choice[] = [
    { value: 'updated', name: 'Updated' },
    { value: 'title', name: 'Title' },
    { value: 'author', name: 'Author' },
  ];

  /** Who can use a skill, changed from its row without opening it. */
  readonly share = new ShareEditor(() => this.profile);
  shareProblem = '';

  /** Which skills a NEW chat starts with. An empty preference is a
   *  choice; no preference at all means every visible skill. */
  defaultSkills = new Set<string>();
  savingDefaults = false;
  /** The skill whose switch was just saved: "Saved" shows beside it. */
  savedRef = '';

  /** How many skills a new chat of this person lists: their own
   *  "Skills listed" default (Settings → Chat configuration), where 0
   *  is every one. Past it the catalog is cut and the assistant is
   *  told it was cut. */
  get catalogLimit(): number {
    const chosen = this.profile?.preferences?.chat?.max_skills;
    return typeof chosen === 'number' && Number.isInteger(chosen) && chosen >= 0
      ? chosen : STANDARD_SKILLS_LISTED;
  }

  constructor(
    private service: SkillsService,
    private profiles: ProfileService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
    private confirm: ConfirmService,
  ) {
    super();
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
  }

  async ngOnInit(): Promise<void> {
    this.address.add(this.route.queryParamMap.subscribe((params) => {
      const show = params.get('show');
      this.show = show === 'mine' || show === 'shared' || show === 'on' ? show : 'all';
      this.query = params.get('q') ?? '';
      const sort = params.get('sort');
      this.sort = sort === 'title' || sort === 'author' ? sort : 'updated';
    }));
    this.address.add(this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) this.readOpened();
    }));

    const [skills, profile, peers] = await Promise.all([
      this.service.list(),
      this.profiles.get(),
      this.profiles.peers().catch(() => []),
    ]);
    this.skills = skills;
    this.profile = profile;
    this.peers = peers;
    this.share.peers = peers;
    this.readDefaults(profile);
    this.loading = false;
    this.readOpened();
    this.sayWhatWasSaved();
  }

  /** Arriving from the editor after a save: said once, here. A person
   *  who chose which skills new chats start with has a list the new
   *  one is not on yet. */
  private sayWhatWasSaved(): void {
    const from = history.state as { skillSaved?: string; ref?: string } | null;
    if (!from?.skillSaved) return;
    history.replaceState({ ...from, skillSaved: undefined }, '');
    this.flash(from.skillSaved !== 'created' ? 'Skill updated.'
      : !from.ref || this.defaultSkills.has(from.ref)
        ? 'Skill created. New chats can read it from now on.'
        : 'Skill created. Switch it on in its row for new chats to read it.');
  }

  // ── The address ─────────────────────────────────────────────────────

  /** Changes what the address says, and so what is listed. */
  go(changes: Record<string, string>): void {
    const queryParams: Record<string, string | null> = {};
    for (const [key, value] of Object.entries(changes)) queryParams[key] = value || null;
    void this.router.navigate([], {
      relativeTo: this.route.firstChild ?? this.route,
      queryParams,
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** Opens what the address names. A ref that names no skill the person
   *  can see, once the list is read, goes back to the list and says so. */
  private readOpened(): void {
    this.openRef = this.route.snapshot.firstChild?.paramMap.get('ref') || '';
    if (this.openRef && !this.loading && !this.opened) {
      this.fail('That skill is not one you can see any more.');
      this.closeOpened();
    }
  }

  get opened(): Skill | null {
    return this.skills.find((skill) => skill.resource_ref === this.openRef) || null;
  }

  closeOpened(): void {
    void this.router.navigate(['/data/skills'], { queryParamsHandling: 'preserve' });
  }

  private readDefaults(profile: Profile | null): void {
    const configured = profile?.preferences?.chat?.enabled_skills;
    this.defaultSkills = new Set(
      Array.isArray(configured)
        ? configured
        : this.skills.map((skill) => skill.resource_ref),
    );
  }

  private async reload(): Promise<void> {
    this.skills = await this.service.list();
    // With no preference saved every skill is a default, a new one
    // included: the set is read again over the new list.
    this.readDefaults(this.profile);
  }

  // ── Access ──────────────────────────────────────────────────────────

  get canCreate(): boolean {
    return this.auth.can('skills:skill:create');
  }

  get canShareAny(): boolean {
    return this.auth.can('skills:skill:set_owner_any');
  }

  get myGroups(): { group_id: string; group_name: string }[] {
    return (this.profile?.groups ?? []).filter(
      (group) => group.group_id !== 'everyone',
    );
  }

  get myUserId(): string {
    return this.profile?.user_id ?? '';
  }

  /** Whether the person may change this skill: its writer, or whoever
   *  may maintain any. */
  mayChange(skill: Skill): boolean {
    return skill.created_by === this.myUserId || this.canShareAny;
  }

  // ── The list ────────────────────────────────────────────────────────

  private matchesShow(skill: Skill, show: Show): boolean {
    switch (show) {
      case 'mine': return skill.created_by === this.myUserId;
      case 'shared': return skill.created_by !== this.myUserId;
      case 'on': return this.isDefault(skill);
      default: return true;
    }
  }

  get filters(): FilterGroup[] {
    const count = (show: Show) => this.skills.filter((skill) => this.matchesShow(skill, show)).length;
    return [{
      key: 'show',
      required: true,
      items: [
        { value: 'all', name: 'All', count: count('all') },
        { value: 'mine', name: 'Mine', count: count('mine') },
        { value: 'shared', name: 'Shared with me', count: count('shared') },
        { value: 'on', name: 'On for new chats', count: count('on') },
      ],
    }];
  }

  /** The skills the filter and the search leave, in the chosen order. */
  get shownSkills(): Skill[] {
    const query = this.query.trim().toLowerCase();
    const changed = (skill: Skill) => skill.updated_at || skill.created_at || '';
    const by: Record<SkillSort, (a: Skill, b: Skill) => number> = {
      updated: (a, b) => changed(b).localeCompare(changed(a)),
      title: (a, b) => a.keys.title.localeCompare(b.keys.title),
      author: (a, b) => (a.created_by_name || '').localeCompare(b.created_by_name || '')
        || a.keys.title.localeCompare(b.keys.title),
    };
    return this.skills
      .filter((skill) => this.matchesShow(skill, this.show)
        && (!query || `${skill.keys.title} ${skill.keys.summary} ${skill.created_by_name || ''}`
          .toLowerCase().includes(query)))
      .sort(by[this.sort]);
  }

  updated(skill: Skill): string {
    // Mid-sentence: "updated just now".
    return relativeDate(skill.updated_at || skill.created_at || '').replace(/^J/, 'j');
  }

  updatedInFull(skill: Skill): string {
    return fullDate(skill.updated_at || skill.created_at || '');
  }

  /** Who can use the skill, as its row's chip. */
  sharing(skill: Skill): SharingChip {
    return this.share.chip(skill.owner, skill.created_by, skill.owner_names, skill.created_by_name);
  }

  /** Under the list: how many skills a chat lists, and what decides
   *  which. Past the number the list is cut, which the assistant is
   *  told, so more on than that is worth saying. */
  get foot(): string {
    if (!this.skills.length) return '';
    if (!this.catalogLimit) return 'A chat lists every skill that is on; the summary decides which it reads.';
    const over = this.overCatalogLimit ? ` ${this.defaultCount} are on, so some are left out.` : '';
    return `A chat lists at most ${this.catalogLimit} skills; the summary decides which it reads.${over}`;
  }

  // ── Writing and changing: the editor's page ─────────────────────────

  startCreate(fromExample = false): void {
    void this.router.navigate(['/data/skills/new'], { queryParams: fromExample ? { example: 1 } : {} });
  }

  startEdit(skill: Skill): void {
    void this.router.navigate(['/data/skills', skill.resource_ref, 'edit']);
  }

  // ── What a new chat starts with ─────────────────────────────────────

  isDefault(skill: Skill): boolean {
    return this.defaultSkills.has(skill.resource_ref);
  }

  /** Flip it and save. The mark goes back when the save fails: one
   *  that says something else than what was saved is worse than one
   *  that moves twice. */
  async toggleDefault(skill: Skill): Promise<void> {
    if (this.savingDefaults) return;
    const wasOn = this.defaultSkills.has(skill.resource_ref);
    if (wasOn) this.defaultSkills.delete(skill.resource_ref);
    else this.defaultSkills.add(skill.resource_ref);

    if (!(await this.saveDefaults())) {
      if (wasOn) this.defaultSkills.add(skill.resource_ref);
      else this.defaultSkills.delete(skill.resource_ref);
      return;
    }
    this.savedRef = skill.resource_ref;
    setTimeout(() => {
      if (this.savedRef === skill.resource_ref) this.savedRef = '';
    }, SAVED_FOR_MS);
  }

  get defaultCount(): number {
    return this.skills.filter((skill) => this.isDefault(skill)).length;
  }

  /** The catalog is bounded in the prompt; past it skills stop being
   *  offered at all, which reads as the assistant getting worse rather
   *  than as a limit being hit. */
  get overCatalogLimit(): boolean {
    return this.catalogLimit > 0 && this.defaultCount > this.catalogLimit;
  }

  /** Write the set as it stands. True when it was saved. */
  async saveDefaults(): Promise<boolean> {
    this.savingDefaults = true;
    try {
      const refs = this.skills
        .map((skill) => skill.resource_ref)
        .filter((ref) => this.defaultSkills.has(ref));
      const result = await this.profiles.saveDefaultSkills(refs);
      if (result.error) {
        this.fail(result.error);
        return false;
      }
      // The answer carries the preferences and not the groups, which
      // the sharing choices are drawn from: those stay as they were read.
      if (result.profile) {
        this.profile = { ...result.profile, groups: this.profile?.groups ?? [] };
      }
      this.readDefaults(this.profile);
      this.error = '';
      return true;
    } finally {
      this.savingDefaults = false;
    }
  }

  // ── Sharing, from the row ───────────────────────────────────────────

  openShare(skill: Skill): void {
    this.shareProblem = '';
    this.share.open(skill.resource_ref, skill.owner);
  }

  async saveShare(): Promise<void> {
    this.share.saving = true;
    this.shareProblem = '';
    try {
      const result = await this.service.update(this.share.ref, { owner: this.share.owner() as any });
      if (result.error) {
        this.shareProblem = result.error;
        return;
      }
      this.share.close();
      await this.reload();
      this.flash('Sharing updated.');
    } finally {
      this.share.saving = false;
    }
  }

  // ── Deleting ────────────────────────────────────────────────────────

  /** Asked first: chats stop being able to read it at once. */
  async askDelete(skill: Skill): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Delete ${skill.keys.title}?`,
      body: 'Chats stop being able to read it immediately. Anything already written using it '
        + 'is unaffected: this only changes what comes next.',
      confirm: 'Delete',
      danger: true,
    });
    if (sure) await this.remove(skill);
  }

  async remove(skill: Skill): Promise<void> {
    this.busyRef = skill.resource_ref;
    try {
      const result = await this.service.remove(skill.resource_ref);
      if (result.error) return this.fail(result.error);
      this.defaultSkills.delete(skill.resource_ref);
      if (skill.resource_ref === this.openRef) this.closeOpened();
      await this.reload();
      this.flash('Skill deleted.');
    } finally {
      this.busyRef = '';
    }
  }

  // ── Handing over ────────────────────────────────────────────────────

  transferTarget: Skill | null = null;
  transferring = false;

  requestTransfer(item: Skill): void {
    this.transferTarget = item;
    this.error = '';
  }

  closeTransfer(): void {
    if (!this.transferring) this.transferTarget = null;
  }

  async transfer(person: { user_id: string; user_name: string; email: string }): Promise<void> {
    const item = this.transferTarget;
    if (!item) return;
    this.transferring = true;
    try {
      const result = await this.service.transfer(item.resource_ref, person.user_id);
      // Closed either way: a refusal is said on the page, where it can
      // be read, not under the dialog.
      this.transferTarget = null;
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(`Handed over to ${person.user_name || person.email}.`);
    } finally {
      this.transferring = false;
    }
  }
}
