import { Component, OnInit } from '@angular/core';

import { AuthService, CatalogService } from 'src/app/services/auth.service';
import {
  Permissions,
  Policy,
  PolicyService,
} from 'src/app/services/policy.service';
import { DataPageBase } from '../../app/data-page-base';

/**
 * Admin → Policies: where permissions are authored.
 *
 * A policy is the leaf of the access chain (user → groups → roles →
 * policies). Written by hand it is a JSON object of statements; written here
 * it is a grid of the actions the backend actually recognises, because the
 * catalog comes FROM the backend — the same list the evaluator validates
 * against, so the editor cannot offer an action that does not exist.
 *
 * The grid covers the shape almost every policy has: "allow these actions".
 * Documents that go beyond it — a Deny override, or a pattern wider than
 * one service — cannot be drawn as checkboxes without lying about them,
 * so the editor detects that case and switches to the raw document instead.
 *
 * Ticking a whole service stores the wildcard (`secrets:secret:*`) rather than
 * every action inside it, so a policy written today keeps meaning "all of
 * this service" after new actions are added to the product.
 *
 * The backend refuses any grant the author does not hold themselves; the
 * refusal names the out-of-reach actions and they are surfaced verbatim.
 */
@Component({
  selector: 'app-policies',
  standalone: false,
  templateUrl: './policies.component.html',
  styleUrls: ['../../app/data-shared.css', '../iam-shared.css'],
})
export class PoliciesComponent extends DataPageBase implements OnInit {
  loading = true;

  policies: Policy[] = [];
  catalog: CatalogService[] = [];

  /** null = the editor is closed; '' = creating; otherwise the policy id. */
  editingId: string | null = null;
  formName = '';
  formDescription = '';
  selected = new Set<string>();

  /** Set when a document is too rich for the grid; the raw JSON is edited. */
  advanced = false;
  advancedText = '';

  saving = false;
  busy = new Set<string>();


  constructor(
    private policyService: PolicyService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    // The catalog rides on /auth/me; make sure the session has been read.
    await this.auth.ensureLoaded();
    this.catalog = this.auth.catalog;
    this.policies = await this.policyService.list();
    this.loading = false;
  }

  private async reload(): Promise<void> {
    this.policies = await this.policyService.list();
  }

  // ── What the current user may do ────────────────────────────────────

  get canCreate(): boolean {
    return this.auth.can('iam:policy:create');
  }

  get canUpdate(): boolean {
    return this.auth.can('iam:policy:update');
  }

  get canDelete(): boolean {
    return this.auth.can('iam:policy:delete');
  }

  isBusy(policyId: string): boolean {
    return this.busy.has(policyId);
  }

  // ── Reading a policy ────────────────────────────────────────────────

  /** A short, human summary of what a policy grants, for the list. */
  summarise(policy: Policy): string {
    const statements = policy.permissions?.statements ?? [];
    const actions = statements.flatMap((s) => s.actions ?? []);

    if (actions.includes('*')) {
      return 'Everything';
    }
    if (!actions.length) {
      return 'Nothing';
    }

    // A service is the first two parts of an action (`iam:user`), which
    // is how the catalog is keyed.
    const services = new Set(actions.map((a) => a.split(':').slice(0, 2).join(':')));
    const labels = [...services].map(
      (key) => this.catalog.find((c) => c.service === key)?.label ?? key,
    );
    const shown = labels.slice(0, 3).join(', ');
    return labels.length > 3 ? `${shown} +${labels.length - 3} more` : shown;
  }

  /** The action patterns a document selects, or null when it is richer
   *  than the grid can express. The grid can draw plain Allow statements
   *  naming actions, a whole service, or everything. Anything else — a
   *  Deny, or a pattern of another width such as `iam:*` — is shown as
   *  the document it is, rather than a picture that is not quite it. */
  private gridSelection(permissions: Permissions | undefined): Set<string> | null {
    const statements = permissions?.statements ?? [];
    if (!statements.length) return null;

    const selected = new Set<string>();
    for (const statement of statements) {
      if (statement.effect !== 'Allow') return null;
      for (const action of statement.actions ?? []) {
        if (!this.drawable(action)) return null;
        selected.add(action);
      }
    }
    return selected;
  }

  /** Whether one pattern has a box in the grid to tick. */
  private drawable(pattern: string): boolean {
    if (!pattern.includes('*')) return true;
    return pattern === '*'
      || this.catalog.some((group) => pattern === this.serviceWildcard(group.service));
  }

  // ── Opening the editor ──────────────────────────────────────────────

  startCreate(): void {
    this.editingId = '';
    this.formName = '';
    this.formDescription = '';
    this.selected = new Set<string>();
    this.advanced = false;
    this.advancedText = '';
    this.error = '';
  }

  startEdit(policy: Policy): void {
    this.editingId = policy.policy_id;
    this.formName = policy.name;
    this.formDescription = policy.description;
    this.error = '';

    const selection = this.gridSelection(policy.permissions);
    if (selection) {
      this.selected = selection;
      this.advanced = false;
      this.advancedText = '';
    } else {
      // A Deny or a wider pattern: show the real document rather than
      // a checkbox picture that is not quite it.
      this.selected = new Set<string>();
      this.advanced = true;
      this.advancedText = JSON.stringify(policy.permissions, null, 2);
    }
  }

  /** Start a new policy from an existing one. */
  duplicate(policy: Policy): void {
    this.startEdit(policy);
    this.editingId = '';
    this.formName = `${policy.name} (copy)`;
  }

  cancelEdit(): void {
    this.editingId = null;
    this.error = '';
  }

  get isCreating(): boolean {
    return this.editingId === '';
  }

  get editorTitle(): string {
    return this.isCreating ? 'New policy' : `Edit ${this.formName}`;
  }

  // ── The grid ────────────────────────────────────────────────────────

  private serviceWildcard(service: string): string {
    return `${service}:*`;
  }

  isActionOn(service: string, action: string): boolean {
    return (
      this.selected.has('*') ||
      this.selected.has(this.serviceWildcard(service)) ||
      this.selected.has(action)
    );
  }

  isServiceOn(group: CatalogService): boolean {
    return (
      this.selected.has('*') ||
      this.selected.has(this.serviceWildcard(group.service)) ||
      group.actions.every((a) => this.selected.has(a.action))
    );
  }

  isServicePartial(group: CatalogService): boolean {
    return (
      !this.isServiceOn(group) &&
      group.actions.some((a) => this.selected.has(a.action))
    );
  }

  selectedCount(group: CatalogService): number {
    return group.actions.filter((a) => this.isActionOn(group.service, a.action))
      .length;
  }

  toggleService(group: CatalogService): void {
    const wildcard = this.serviceWildcard(group.service);

    if (this.isServiceOn(group)) {
      this.selected.delete(wildcard);
      group.actions.forEach((a) => this.selected.delete(a.action));
      // A blanket '*' has to be expanded before one service can be removed.
      if (this.selected.has('*')) {
        this.selected.delete('*');
        for (const other of this.catalog) {
          if (other.service !== group.service) {
            this.selected.add(this.serviceWildcard(other.service));
          }
        }
      }
      return;
    }

    // Whole service: store the wildcard so the policy keeps up with new actions.
    group.actions.forEach((a) => this.selected.delete(a.action));
    this.selected.add(wildcard);
  }

  toggleAction(group: CatalogService, action: string): void {
    const wildcard = this.serviceWildcard(group.service);

    // Turning one action off inside a wildcard means the wildcard no longer
    // holds — expand it to the concrete actions, minus this one.
    if (this.selected.has('*') || this.selected.has(wildcard)) {
      this.selected.delete('*');
      this.selected.delete(wildcard);

      if (this.selected.size === 0) {
        for (const other of this.catalog) {
          if (other.service !== group.service) {
            this.selected.add(this.serviceWildcard(other.service));
          }
        }
      }

      group.actions.forEach((a) => {
        if (a.action !== action) {
          this.selected.add(a.action);
        }
      });
      return;
    }

    this.selected.has(action)
      ? this.selected.delete(action)
      : this.selected.add(action);
  }

  get totalSelected(): number {
    if (this.selected.has('*')) {
      return this.catalog.reduce((sum, g) => sum + g.actions.length, 0);
    }
    return this.catalog.reduce((sum, g) => sum + this.selectedCount(g), 0);
  }

  // ── Saving ──────────────────────────────────────────────────────────

  /** Build the permissions document from whichever editor is in use. */
  private buildPermissions(): Permissions | null {
    if (this.advanced) {
      try {
        const parsed = JSON.parse(this.advancedText);
        if (!parsed || !Array.isArray(parsed.statements)) {
          this.fail('The document needs a "statements" list.');
          return null;
        }
        return parsed as Permissions;
      } catch {
        this.fail('That is not valid JSON.');
        return null;
      }
    }

    if (!this.selected.size) {
      this.fail('Choose at least one permission.');
      return null;
    }

    return {
      statements: [
        { effect: 'Allow', actions: [...this.selected].sort(), resources: ['*'] },
      ],
    };
  }

  /** Name the actions a refused grant was missing, or fall back to the error. */
  private failGrant(result: { error?: string; ungrantable?: string[] }): void {
    if (result.ungrantable?.length) {
      const shown = result.ungrantable.slice(0, 5).join(', ');
      const more = result.ungrantable.length - 5;
      return this.fail(
        `You cannot grant permissions you do not hold: ${shown}` +
          (more > 0 ? ` and ${more} more.` : '.'),
      );
    }
    this.fail(result.error ?? 'Could not save the policy.');
  }

  async save(): Promise<void> {
    const name = this.formName.trim();
    if (!name) {
      return this.fail('Give the policy a name.');
    }

    const permissions = this.buildPermissions();
    if (!permissions) {
      return;
    }

    this.saving = true;
    try {
      const result = this.isCreating
        ? await this.policyService.create(
            name,
            this.formDescription.trim(),
            permissions,
          )
        : await this.policyService.update(this.editingId as string, {
            name,
            description: this.formDescription.trim(),
            permissions,
          });

      if (result.error) {
        return this.failGrant(result);
      }

      await this.reload();
      this.flash(this.isCreating ? `Policy “${name}” created.` : 'Policy saved.');
      this.editingId = null;

      // Their own permissions may have just changed.
      await this.auth.refresh();
    } finally {
      this.saving = false;
    }
  }

  async remove(policy: Policy): Promise<void> {
    if (
      !confirm(
        `Delete the policy “${policy.name}”?\n\nIt will be detached from ` +
          `every role that carries it, and whoever relied on it loses that access.`,
      )
    ) {
      return;
    }

    this.busy.add(policy.policy_id);
    try {
      const result = await this.policyService.remove(policy.policy_id);
      if (result.error) {
        return this.fail(result.error);
      }
      if (this.editingId === policy.policy_id) {
        this.editingId = null;
      }
      await this.reload();
      this.flash(`Policy “${policy.name}” deleted.`);
      await this.auth.refresh();
    } finally {
      this.busy.delete(policy.policy_id);
    }
  }
}
