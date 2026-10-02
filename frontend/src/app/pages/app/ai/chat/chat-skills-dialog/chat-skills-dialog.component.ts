import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { Skill, SkillsService } from 'src/app/services/skills.service';

/**
 * Which skills this chat puts in front of the assistant.
 *
 * Unlike agents, this is not narrowing a set the platform approved: a
 * skill is text the person can already read, so a chat may enable one
 * their defaults left out. What the choice actually buys is attention —
 * the catalog the model chooses from is bounded, so a conversation that
 * carries only what it is about gets better answers than one carrying
 * everything ever written.
 *
 * With every skill checked the chat keeps no list at all, so skills
 * written later join it automatically; any narrowing is an explicit
 * list, and an empty one means the assistant works from no skills.
 */
@Component({
  selector: 'app-chat-skills-dialog',
  standalone: false,
  templateUrl: './chat-skills-dialog.component.html',
  styleUrls: ['./chat-skills-dialog.component.css'],
})
export class ChatSkillsDialogComponent implements OnInit {
  skills: Skill[] = [];
  selection = new Set<string>();
  isLoading = true;
  query = '';

  /** How many the runtime will show the model before the catalog is
   *  cut: the chat's own cap, where 0 lists every one. */
  readonly catalogLimit: number;

  private readonly configured: string[] | null;
  private readonly defaults: string[] | null;

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: any,
    private dialogRef: MatDialogRef<ChatSkillsDialogComponent>,
    private service: SkillsService,
  ) {
    const enabled = data?.enabled_skills;
    this.configured = Array.isArray(enabled) ? enabled : null;
    this.defaults = Array.isArray(data?.default_skills) ? data.default_skills : null;
    this.catalogLimit = Number.isInteger(data?.max_skills) ? data.max_skills : 40;
  }

  async ngOnInit(): Promise<void> {
    this.skills = await this.service.list();
    // No stored narrowing means every skill this person can see.
    const enabled =
      this.configured ?? this.defaults ?? this.skills.map((s) => s.resource_ref);
    this.selection = new Set(enabled);
    this.isLoading = false;
  }

  get visibleSkills(): Skill[] {
    const query = this.query.trim().toLowerCase();
    if (!query) return this.skills;
    return this.skills.filter((skill) =>
      `${skill.keys.title} ${skill.keys.summary}`.toLowerCase().includes(query));
  }

  toggle(ref: string): void {
    if (this.selection.has(ref)) this.selection.delete(ref);
    else this.selection.add(ref);
  }

  get selectedCount(): number {
    return this.skills.filter((skill) => this.selection.has(skill.resource_ref)).length;
  }

  get overLimit(): boolean {
    return this.catalogLimit > 0 && this.selectedCount > this.catalogLimit;
  }

  get allSelected(): boolean {
    return this.skills.every((skill) => this.selection.has(skill.resource_ref));
  }

  selectAll(): void {
    this.selection = new Set(this.skills.map((skill) => skill.resource_ref));
  }

  clearAll(): void {
    this.selection = new Set();
  }

  save(): void {
    // Everything checked is not a list of everything: a chat that keeps
    // no list is the one that takes up skills written later, which is
    // what a person means by leaving them all on. Where their own
    // defaults narrow the skills, no list would read back as those
    // defaults, so the whole choice is written out.
    this.dialogRef.close({
      enabled_skills: this.allSelected && !this.defaults ? null : this.skills
        .map((skill) => skill.resource_ref)
        .filter((ref) => this.selection.has(ref)),
    });
  }

  reset(): void {
    this.selection = new Set(
      this.defaults || this.skills.map((skill) => skill.resource_ref),
    );
  }

  close(): void {
    this.dialogRef.close(null);
  }
}
