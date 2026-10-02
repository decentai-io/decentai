import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import { DataPageBase } from '../../app/data-page-base';

/**
 * Admin → Account → Profile: your own record.
 *
 * Nothing here is special-cased in the frontend — the page is visible
 * because `account:profile:get` is granted (by default to everyone, through
 * the built-in Everyone group), and an administrator can widen or withdraw
 * that from the Policies screen like any other page.
 *
 * Changing a password is deliberately elsewhere: credential rotation lives
 * on the public /auth/* surface so policy can never lock a user out of it.
 */
@Component({
  selector: 'app-profile',
  standalone: false,
  templateUrl: './profile.component.html',
  styleUrls: ['../../app/data-shared.css', '../iam-shared.css'],
})
export class ProfileComponent extends DataPageBase implements OnInit {
  loading = true;
  profile: Profile | null = null;

  renaming = false;
  renameValue = '';
  saving = false;

  constructor(
    private profiles: ProfileService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    this.profile = await this.profiles.get();
    this.loading = false;
  }

  get canRename(): boolean {
    return this.auth.can('account:profile:update');
  }

  startRename(): void {
    this.renaming = true;
    this.renameValue = this.profile?.user_name ?? '';
    this.error = '';
  }

  cancelRename(): void {
    this.renaming = false;
    this.renameValue = '';
  }

  async saveRename(): Promise<void> {
    const name = this.renameValue.trim();
    if (!name || name === this.profile?.user_name) {
      this.cancelRename();
      return;
    }

    this.saving = true;
    try {
      const result = await this.profiles.rename(name);
      if (result.error) {
        return this.fail(result.error);
      }
      this.cancelRename();
      this.profile = await this.profiles.get();
      // The shell shows the name too, so re-read the session.
      await this.auth.refresh();
      this.flash('Name updated.');
    } finally {
      this.saving = false;
    }
  }
}
