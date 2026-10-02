import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { Group, GroupMember, GroupService } from 'src/app/services/group.service';
import { Role, RoleService } from 'src/app/services/role.service';
import { DataPageBase } from '../../app/data-page-base';

/**
 * Admin → Groups: named bundles of roles.
 *
 * Membership lives on the USER (their `assigned_groups`), so people are
 * placed into groups on the Users page — here a group's members are shown,
 * not edited. What this page manages is the group itself and the roles it
 * carries; attaching a role is bounded by the backend's grant boundary.
 */
@Component({
  selector: 'app-groups',
  standalone: false,
  templateUrl: './groups.component.html',
  styleUrls: ['../../app/data-shared.css', '../iam-shared.css'],
})
export class GroupsComponent extends DataPageBase implements OnInit {
  loading = true;

  groups: Group[] = [];
  roles: Role[] = [];

  /** null = the editor is closed; '' = creating; otherwise the group id. */
  editingId: string | null = null;
  formName = '';
  selected = new Set<string>();
  editingMembers: GroupMember[] = [];

  saving = false;
  busy = new Set<string>();

  constructor(
    private groupService: GroupService,
    private roleService: RoleService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    const [groups, roles] = await Promise.all([
      this.groupService.list(),
      this.roleService.list(),
    ]);
    this.groups = groups;
    this.roles = roles;
    this.loading = false;
  }

  private async reload(): Promise<void> {
    this.groups = await this.groupService.list();
  }

  // ── What the current user may do ────────────────────────────────────

  get canCreate(): boolean {
    return this.auth.can('iam:group:create');
  }

  get canUpdate(): boolean {
    return this.auth.can('iam:group:rename');
  }

  get canAssign(): boolean {
    return this.auth.can('iam:group:set_roles');
  }

  get canDelete(): boolean {
    return this.auth.can('iam:group:delete');
  }

  isBusy(groupId: string): boolean {
    return this.busy.has(groupId);
  }

  // ── Reading a group ─────────────────────────────────────────────────

  roleName(roleId: string): string {
    return this.roles.find((r) => r.role_id === roleId)?.role_name ?? roleId;
  }

  summarise(group: Group): string {
    if (!group.assigned_roles.length) {
      return 'No roles attached';
    }
    const names = group.assigned_roles.map((id) => this.roleName(id));
    const shown = names.slice(0, 3).join(', ');
    return names.length > 3 ? `${shown} +${names.length - 3} more` : shown;
  }

  // ── Editor ──────────────────────────────────────────────────────────

  startCreate(): void {
    this.editingId = '';
    this.formName = '';
    this.selected = new Set<string>();
    this.editingMembers = [];
    this.error = '';
  }

  async startEdit(group: Group): Promise<void> {
    this.editingId = group.group_id;
    this.formName = group.group_name;
    this.selected = new Set(group.assigned_roles);
    this.error = '';

    // Members ride on get(); shown so an admin can see who a change affects.
    const detailed = await this.groupService.get(group.group_id);
    this.editingMembers = detailed?.members ?? [];
  }

  cancelEdit(): void {
    this.editingId = null;
    this.editingMembers = [];
    this.error = '';
  }

  get isCreating(): boolean {
    return this.editingId === '';
  }

  get editorTitle(): string {
    return this.isCreating ? 'New group' : `Edit ${this.formName}`;
  }

  isRoleOn(roleId: string): boolean {
    return this.selected.has(roleId);
  }

  toggleRole(roleId: string): void {
    this.selected.has(roleId)
      ? this.selected.delete(roleId)
      : this.selected.add(roleId);
  }

  // ── Saving ──────────────────────────────────────────────────────────

  private failGrant(result: { error?: string; ungrantable?: string[] }): void {
    if (result.ungrantable?.length) {
      const shown = result.ungrantable.slice(0, 5).join(', ');
      const more = result.ungrantable.length - 5;
      return this.fail(
        `You cannot attach permissions you do not hold: ${shown}` +
          (more > 0 ? ` and ${more} more.` : '.'),
      );
    }
    this.fail(result.error ?? 'Could not save the group.');
  }

  async save(): Promise<void> {
    const name = this.formName.trim();
    if (!name) {
      return this.fail('Give the group a name.');
    }

    const roleIds = [...this.selected];

    this.saving = true;
    try {
      if (this.isCreating) {
        const result = await this.groupService.create(name, roleIds);
        if (result.error) {
          return this.failGrant(result);
        }
      } else {
        // Renaming and attaching roles are two actions, held separately:
        // each is asked only where it changed something and the person
        // may do it, so one of them refused never follows the other
        // half-saved.
        const groupId = this.editingId as string;
        const before = this.groups.find((group) => group.group_id === groupId);
        if (this.canUpdate && name !== before?.group_name) {
          const renamed = await this.groupService.rename(groupId, name);
          if (renamed.error) {
            return this.fail(renamed.error);
          }
        }
        if (this.canAssign && !this.sameIds(roleIds, before?.assigned_roles ?? [])) {
          const attached = await this.groupService.setRoles(groupId, roleIds);
          if (attached.error) {
            return this.failGrant(attached);
          }
        }
      }

      await this.reload();
      this.flash(this.isCreating ? `Group “${name}” created.` : 'Group saved.');
      this.editingId = null;

      // Their own permissions may have just changed.
      await this.auth.refresh();
    } finally {
      this.saving = false;
    }
  }

  private sameIds(a: string[], b: string[]): boolean {
    return a.length === b.length && a.every((id) => b.includes(id));
  }

  async remove(group: Group): Promise<void> {
    if (
      !confirm(
        `Delete the group “${group.group_name}”?\n\nEveryone in it loses ` +
          `whatever access it granted.`,
      )
    ) {
      return;
    }

    this.busy.add(group.group_id);
    try {
      const result = await this.groupService.remove(group.group_id);
      if (result.error) {
        return this.fail(result.error);
      }
      if (this.editingId === group.group_id) {
        this.editingId = null;
      }
      await this.reload();
      this.flash(`Group “${group.group_name}” deleted.`);
      await this.auth.refresh();
    } finally {
      this.busy.delete(group.group_id);
    }
  }
}
