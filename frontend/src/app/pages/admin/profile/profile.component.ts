import { Component, OnInit } from '@angular/core';

import { dayDate } from 'src/app/components/ui/dates';
import { AuthService } from 'src/app/services/auth.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';

/**
 * Admin → Account → Profile: your own record.
 *
 * Nothing here is special-cased in the frontend: the page is visible
 * because `account:profile:get` is granted (by default to everyone,
 * through the built-in Everyone group), and an administrator can widen
 * or withdraw that from the Policies page like any other.
 *
 * Changing a password is deliberately elsewhere: it lives on the public
 * /auth/* surface so policy can never lock a person out of it.
 */
@Component({
  selector: 'app-profile',
  standalone: false,
  templateUrl: './profile.component.html',
  styleUrl: '../admin-shared.css',
})
export class ProfileComponent implements OnInit {
  loading = true;
  profile: Profile | null = null;

  name = '';
  /** Save was pressed: what the name still needs is said under it. */
  tried = false;
  saving = false;
  saveError = '';

  constructor(
    private profiles: ProfileService,
    public auth: AuthService,
  ) {}

  async ngOnInit(): Promise<void> {
    this.profile = await this.profiles.get();
    this.name = this.profile?.user_name ?? '';
    this.loading = false;
  }

  get canRename(): boolean {
    return this.auth.can('account:profile:update');
  }

  get dirty(): boolean {
    return this.name.trim() !== (this.profile?.user_name ?? '');
  }

  get lastSignIn(): string {
    return dayDate(this.profile?.last_login_at || '');
  }

  async save(): Promise<void> {
    this.tried = true;
    this.saveError = '';
    const name = this.name.trim();
    if (!name || !this.dirty || this.saving) return;

    this.saving = true;
    try {
      const result = await this.profiles.rename(name);
      if (result.error) {
        this.saveError = result.error;
        return;
      }
      this.profile = await this.profiles.get();
      this.name = this.profile?.user_name ?? name;
      this.tried = false;
      // The shell shows the name too.
      await this.auth.refresh();
    } finally {
      this.saving = false;
    }
  }
}
