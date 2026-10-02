import { Component, OnInit } from '@angular/core';

import { Profile, ProfileService } from 'src/app/services/profile.service';
import { Skill, SkillsService } from 'src/app/services/skills.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataPageBase } from '../../data-page-base';

type ShareMode = 'private' | 'groups' | 'people' | 'org';

/**
 * Skills: knowledge people write for the assistant. The chat's main loop
 * sees every visible skill as one catalog line (title + summary) and
 * reads the full body only when it judges the skill relevant — so the
 * title and summary are what the MODEL chooses by, and the page says so.
 */
@Component({
  selector: 'app-skills',
  standalone: false,
  templateUrl: './skills.component.html',
  styleUrls: ['../../data-shared.css', '../../../admin/iam-shared.css', './skills.component.css'],
})
export class SkillsComponent extends DataPageBase implements OnInit {
  loading = true;
  skills: Skill[] = [];
  profile: Profile | null = null;

  // Editor state: null = closed, '' = creating, ref = editing.
  editingRef: string | null = null;
  formTitle = '';
  formSummary = '';
  formBody = '';
  shareMode: ShareMode = 'private';
  selectedGroups = new Set<string>();
  selectedUsers = new Set<string>();
  peers: { user_id: string; user_name: string; email: string }[] = [];
  saving = false;
  busyRef = '';
  query = '';

  /** Which skills a NEW chat starts with. An empty preference is a
   *  choice; no preference at all means every visible skill. */
  defaultSkills = new Set<string>();
  savingDefaults = false;
  deleteTarget: Skill | null = null;
  previewSkill: Skill | null = null;
  previewBody = '';
  previewLoading = false;
  previewUnreadable = false;

  /** How many skills a chat lists, where the person never chose. */
  private static readonly STANDARD_LIMIT = 40;

  /** How many skills a new chat of this person lists: their own
   *  "Skills listed" default (Settings → Chat configuration), where 0
   *  is every one. Past it the catalog is cut and the assistant is
   *  told it was cut. */
  get catalogLimit(): number {
    const chosen = this.profile?.preferences?.chat?.max_skills;
    return typeof chosen === 'number' && Number.isInteger(chosen) && chosen >= 0
      ? chosen : SkillsComponent.STANDARD_LIMIT;
  }

  constructor(
    private service: SkillsService,
    private profiles: ProfileService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    const [skills, profile, peers] = await Promise.all([
      this.service.list(),
      this.profiles.get(),
      this.profiles.peers().catch(() => []),
    ]);
    this.skills = skills;
    this.profile = profile;
    this.peers = peers;
    this.readDefaults(profile);
    this.loading = false;
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

  get isCreating(): boolean {
    return this.editingRef === '';
  }

  get filteredSkills(): Skill[] {
    const query = this.query.trim().toLowerCase();
    if (!query) return this.skills;
    return this.skills.filter((skill) =>
      `${skill.keys.title} ${skill.keys.summary}`.toLowerCase().includes(query),
    );
  }

  // ── Editor ──────────────────────────────────────────────────────────

  startCreate(): void {
    this.editingRef = '';
    this.formTitle = '';
    this.formSummary = '';
    this.formBody = '';
    this.shareMode = 'private';
    this.selectedGroups.clear();
    this.selectedUsers.clear();
    this.error = '';
  }

  useExample(): void {
    this.editingRef = '';
    if (!this.formTitle.trim()) this.formTitle = 'Clear, useful writing';
    if (!this.formSummary.trim()) this.formSummary = 'Use when drafting or revising professional messages, explanations, and updates';
    this.formBody = `Write in plain, direct language.

- Lead with the main point or requested action.
- Keep each paragraph focused on one idea.
- Prefer concrete verbs and familiar words.
- Include only context the reader needs to decide or act.
- Put steps in the order they should happen.
- Match the reader's level of technical knowledge.
- End with the next action, owner, or decision when needed.

Before sending, remove repetition and vague filler. Preserve important facts, constraints, dates, and names.`;
    this.error = '';
  }

  async startEdit(skill: Skill): Promise<void> {
    // The list is the catalog; the body needs its own read.
    const full = await this.service.get(skill.resource_ref);
    if (!full) return this.fail('Could not load this skill.');

    this.editingRef = full.resource_ref;
    this.formTitle = full.keys.title;
    this.formSummary = full.keys.summary;
    this.formBody = full.values?.body ?? '';

    const owner = full.owner;
    if (owner.groups.includes('everyone')) {
      this.shareMode = 'org';
    } else if (owner.groups.length) {
      this.shareMode = 'groups';
    } else if (owner.users.some((userId) => userId !== this.myUserId)) {
      this.shareMode = 'people';
    } else {
      this.shareMode = 'private';
    }
    this.selectedGroups = new Set(owner.groups);
    this.selectedUsers = new Set(owner.users.filter((userId) => userId !== this.myUserId));
    this.error = '';
  }

  cancelEdit(): void {
    this.editingRef = null;
  }

  toggleGroup(groupId: string): void {
    if (!this.selectedGroups.delete(groupId)) {
      this.selectedGroups.add(groupId);
    }
  }

  toggleUser(userId: string): void {
    if (!this.selectedUsers.delete(userId)) this.selectedUsers.add(userId);
  }

  private buildOwner(): Record<string, any> {
    if (this.shareMode === 'org') {
      return { groups: ['everyone'], users: [] };
    }
    if (this.shareMode === 'groups') {
      return { groups: Array.from(this.selectedGroups) };
    }
    if (this.shareMode === 'people') {
      return { groups: [], users: [this.myUserId, ...Array.from(this.selectedUsers)] };
    }
    return { users: [this.myUserId] };
  }

  get formReady(): boolean {
    return (
      !!this.formTitle.trim() &&
      !!this.formSummary.trim() &&
      !!this.formBody.trim() && !this.duplicateTitle
    );
  }

  get duplicateTitle(): boolean {
    const normalize = (value: string) => value.trim().replace(/\s+/g, ' ').toLowerCase();
    return this.skills.some(skill => skill.resource_ref !== this.editingRef
      && normalize(skill.keys.title) === normalize(this.formTitle));
  }

  async save(): Promise<void> {
    if (this.saving || !this.formReady) return;
    this.saving = true;
    const creating = this.isCreating;
    try {
      const fields = {
        title: this.formTitle.trim(),
        summary: this.formSummary.trim(),
        body: this.formBody.trim(),
        owner: this.buildOwner(),
      };
      const result = creating
        ? await this.service.create(fields)
        : await this.service.update(this.editingRef!, fields);
      if (result.error) return this.fail(result.error);

      this.editingRef = null;
      await this.reload();
      // A person who chose which skills new chats start with has a list
      // the new one is not on yet.
      const made = result.resource?.resource_ref;
      this.flash(
        !creating ? 'Skill updated.'
          : !made || this.defaultSkills.has(made)
            ? 'Skill created. New chats can read it from now on.'
            : 'Skill created. Switch it on in its row for new chats to read it.',
      );
    } finally {
      this.saving = false;
    }
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
    }
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
      this.flash(
        refs.length
          ? `${refs.length} skill${refs.length === 1 ? "" : "s"} will be in new chats.`
          : 'New chats will start with no skills.',
      );
      return true;
    } finally {
      this.savingDefaults = false;
    }
  }

  // ── Reading one without opening the editor ──────────────────────────

  async preview(skill: Skill): Promise<void> {
    this.previewSkill = skill;
    this.previewBody = '';
    this.previewUnreadable = false;
    this.previewLoading = true;
    try {
      const full = await this.service.get(skill.resource_ref);
      if (!full) return this.fail('That skill could not be read.');
      this.previewUnreadable = !!full.unreadable;
      this.previewBody = full.values?.body ?? '';
    } finally {
      this.previewLoading = false;
    }
  }

  closePreview(): void {
    this.previewSkill = null;
    this.previewBody = '';
    this.previewUnreadable = false;
  }

  // ── Deleting ────────────────────────────────────────────────────────

  requestDelete(skill: Skill): void {
    this.deleteTarget = skill;
    this.error = '';
  }

  closeDeleteDialog(): void {
    if (this.busyRef) return;
    this.deleteTarget = null;
  }

  async remove(skill: Skill): Promise<void> {
    this.busyRef = skill.resource_ref;
    try {
      const result = await this.service.remove(skill.resource_ref);
      if (result.error) return this.fail(result.error);
      this.deleteTarget = null;
      this.defaultSkills.delete(skill.resource_ref);
      await this.reload();
      this.flash('Skill deleted.');
    } finally {
      this.busyRef = '';
    }
  }

  shareLabel(skill: Skill): string {
    if (skill.owner.groups.includes('everyone')) return 'Organization';
    if (skill.owner.groups.length) return 'Groups';
    // A private skill still names its creator; anyone else in the list
    // is somebody it was shared with.
    const others = (skill.owner.users ?? []).filter((id) => id !== skill.created_by);
    if (others.length) return 'People';
    return skill.created_by === this.myUserId ? 'Private' : 'Shared with you';
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
