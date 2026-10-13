import { Component, OnInit } from '@angular/core';

import { dayDate } from 'src/app/components/ui/dates';
import { AuthService } from 'src/app/services/auth.service';
import { Organization, OrganizationService } from 'src/app/services/organization.service';

/**
 * Admin → Organization.
 *
 * The person's own organization. Its name is the only thing to manage
 * here: there is no create, no delete, no list.
 */
@Component({
  selector: 'app-admin-organizations',
  standalone: false,
  templateUrl: './organizations.component.html',
  styleUrl: '../admin-shared.css',
})
export class OrganizationsComponent implements OnInit {
  loading = true;
  organization: Organization | null = null;

  name = '';
  /** Save was pressed: what the name still needs is said under it. */
  tried = false;
  saving = false;
  saveError = '';

  constructor(
    private organizations: OrganizationService,
    public auth: AuthService,
  ) {}

  async ngOnInit(): Promise<void> {
    this.organization = await this.organizations.get();
    this.name = this.organization?.org_name ?? '';
    this.loading = false;
  }

  get canRename(): boolean {
    return this.auth.can('iam:organization:rename');
  }

  get dirty(): boolean {
    return this.name.trim() !== (this.organization?.org_name ?? '');
  }

  get created(): string {
    return dayDate(this.organization?.created_at || '');
  }

  async save(): Promise<void> {
    this.tried = true;
    this.saveError = '';
    const name = this.name.trim();
    if (!name || !this.dirty || this.saving) return;

    this.saving = true;
    try {
      const result = await this.organizations.rename(name);
      if (result.error) {
        this.saveError = result.error;
        return;
      }
      this.organization = await this.organizations.get();
      this.name = this.organization?.org_name ?? name;
      this.tried = false;
      // The session carries the organization's name too.
      await this.auth.refresh();
    } finally {
      this.saving = false;
    }
  }
}
