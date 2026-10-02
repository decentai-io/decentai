import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { Policy, PolicyService } from 'src/app/services/policy.service';
import { Role, RoleService } from 'src/app/services/role.service';
import { DataPageBase } from '../../app/data-page-base';

/**
 * Admin → Roles: named bundles of policies.
 *
 * A role carries no permissions of its own — it points at policies, and the
 * union of their statements is what it grants. Attaching a policy is bounded
 * by the backend: nobody can bundle permissions they do not hold themselves.
 */
@Component({
  selector: 'app-roles',
  standalone: false,
  templateUrl: './roles.component.html',
  styleUrls: ['../../app/data-shared.css', '../iam-shared.css'],
})
export class RolesComponent extends DataPageBase implements OnInit {
  loading = true;

  roles: Role[] = [];
  policies: Policy[] = [];

  /** null = the editor is closed; '' = creating; otherwise the role id. */
  editingId: string | null = null;
  formName = '';
  formDescription = '';
  selected = new Set<string>();

  saving = false;
  busy = new Set<string>();

  constructor(
    private roleService: RoleService,
    private policyService: PolicyService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    const [roles, policies] = await Promise.all([
      this.roleService.list(),
      this.policyService.list(),
    ]);
    this.roles = roles;
    this.policies = policies;
    this.loading = false;
  }

  private async reload(): Promise<void> {
    this.roles = await this.roleService.list();
  }

  // ── What the current user may do ────────────────────────────────────

  get canCreate(): boolean {
    return this.auth.can('iam:role:create');
  }

  get canUpdate(): boolean {
    return this.auth.can('iam:role:update');
  }

  get canAssign(): boolean {
    return this.auth.can('iam:role:set_policies');
  }

  get canDelete(): boolean {
    return this.auth.can('iam:role:delete');
  }

  isBusy(roleId: string): boolean {
    return this.busy.has(roleId);
  }

  // ── Reading a role ──────────────────────────────────────────────────

  policyName(policyId: string): string {
    return (
      this.policies.find((p) => p.policy_id === policyId)?.name ?? policyId
    );
  }

  /** The attached policies by name, for the list row. */
  summarise(role: Role): string {
    if (!role.assigned_policies.length) {
      return 'No policies attached';
    }
    const names = role.assigned_policies.map((id) => this.policyName(id));
    const shown = names.slice(0, 3).join(', ');
    return names.length > 3 ? `${shown} +${names.length - 3} more` : shown;
  }

  // ── Editor ──────────────────────────────────────────────────────────

  startCreate(): void {
    this.editingId = '';
    this.formName = '';
    this.formDescription = '';
    this.selected = new Set<string>();
    this.error = '';
  }

  startEdit(role: Role): void {
    this.editingId = role.role_id;
    this.formName = role.role_name;
    this.formDescription = role.description;
    this.selected = new Set(role.assigned_policies);
    this.error = '';
  }

  cancelEdit(): void {
    this.editingId = null;
    this.error = '';
  }

  get isCreating(): boolean {
    return this.editingId === '';
  }

  get editorTitle(): string {
    return this.isCreating ? 'New role' : `Edit ${this.formName}`;
  }

  isPolicyOn(policyId: string): boolean {
    return this.selected.has(policyId);
  }

  togglePolicy(policyId: string): void {
    this.selected.has(policyId)
      ? this.selected.delete(policyId)
      : this.selected.add(policyId);
  }

  // ── Saving ──────────────────────────────────────────────────────────

  private failGrant(result: { error?: string; ungrantable?: string[] }): void {
    if (result.ungrantable?.length) {
      const shown = result.ungrantable.slice(0, 5).join(', ');
      const more = result.ungrantable.length - 5;
      return this.fail(
        `You cannot bundle permissions you do not hold: ${shown}` +
          (more > 0 ? ` and ${more} more.` : '.'),
      );
    }
    this.fail(result.error ?? 'Could not save the role.');
  }

  async save(): Promise<void> {
    const name = this.formName.trim();
    if (!name) {
      return this.fail('Give the role a name.');
    }

    const policyIds = [...this.selected];

    this.saving = true;
    try {
      if (this.isCreating) {
        const result = await this.roleService.create(
          name,
          this.formDescription.trim(),
          policyIds,
        );
        if (result.error) {
          return this.failGrant(result);
        }
      } else {
        // Editing a role and attaching policies are two actions, held
        // separately: each is asked only where it changed something and
        // the person may do it, so one of them refused never follows
        // the other half-saved.
        const roleId = this.editingId as string;
        const before = this.roles.find((role) => role.role_id === roleId);
        const description = this.formDescription.trim();
        if (this.canUpdate
            && (name !== before?.role_name || description !== (before?.description ?? ''))) {
          const renamed = await this.roleService.update(roleId, name, description);
          if (renamed.error) {
            return this.fail(renamed.error);
          }
        }
        if (this.canAssign && !this.sameIds(policyIds, before?.assigned_policies ?? [])) {
          const attached = await this.roleService.setPolicies(roleId, policyIds);
          if (attached.error) {
            return this.failGrant(attached);
          }
        }
      }

      await this.reload();
      this.flash(this.isCreating ? `Role “${name}” created.` : 'Role saved.');
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

  async remove(role: Role): Promise<void> {
    if (
      !confirm(
        `Delete the role “${role.role_name}”?\n\nEvery group carrying it ` +
          `loses it, and whoever relied on it loses the access it granted.`,
      )
    ) {
      return;
    }

    this.busy.add(role.role_id);
    try {
      const result = await this.roleService.remove(role.role_id);
      if (result.error) {
        return this.fail(result.error);
      }
      if (this.editingId === role.role_id) {
        this.editingId = null;
      }
      await this.reload();
      this.flash(`Role “${role.role_name}” deleted.`);
      await this.auth.refresh();
    } finally {
      this.busy.delete(role.role_id);
    }
  }
}
