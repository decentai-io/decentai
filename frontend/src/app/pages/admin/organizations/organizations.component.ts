import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import {
  Organization,
  OrganizationService,
} from 'src/app/services/organization.service';
import { DataPageBase } from '../../app/data-page-base';

/**
 * Admin → Organization.
 *
 * The person's own organization. Its name is the only thing to manage
 * here — there is no create, no delete, no list.
 */
@Component({
  selector: 'app-admin-organizations',
  standalone: false,
  templateUrl: './organizations.component.html',
  styleUrls: ['../../app/data-shared.css', './organizations.component.css'],
})
export class OrganizationsComponent extends DataPageBase implements OnInit {
  loading = true;
  organization: Organization | null = null;

  renaming = false;
  renameValue = '';
  saving = false;

  constructor(
    private organizations: OrganizationService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    this.organization = await this.organizations.get();
    this.loading = false;
  }

  get canRename(): boolean {
    return this.auth.can('iam:organization:rename');
  }

  startRename(): void {
    this.renaming = true;
    this.renameValue = this.organization?.org_name ?? '';
    this.error = '';
  }

  cancelRename(): void {
    this.renaming = false;
    this.renameValue = '';
  }

  async saveRename(): Promise<void> {
    const name = this.renameValue.trim();
    if (!name || name === this.organization?.org_name) {
      this.cancelRename();
      return;
    }

    this.saving = true;
    try {
      const result = await this.organizations.rename(name);
      if (result.error) {
        return this.fail(result.error);
      }
      this.cancelRename();
      this.organization = await this.organizations.get();
      // The session carries the organization name too, so refresh it.
      await this.auth.refresh();
      this.flash('Organization renamed.');
    } finally {
      this.saving = false;
    }
  }
}
