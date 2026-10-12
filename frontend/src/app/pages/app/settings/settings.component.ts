import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import {
  LlmConnection, LlmProvider, SettingsLlmService,
} from 'src/app/services/settings-llm.service';
import { DataPageBase } from '../data-page-base';

/**
 * Settings › Model providers: reads what the providers list is drawn
 * from (the organization's connections, the platform's catalog of
 * providers, the person's profile and peers) and hands it to
 * <app-model-providers>, which is the page.
 */
@Component({
  selector: 'app-settings',
  standalone: false,
  templateUrl: './settings.component.html',
  styleUrls: ['../data-shared.css'],
})
export class SettingsComponent extends DataPageBase implements OnInit {
  loading = true;

  /** The connections this person can use. */
  connections: LlmConnection[] = [];
  /** The connections the page shows: the same, or, for a holder of the
   *  manage-any grant, every one of the organization, a colleague's
   *  unshared one included, to maintain them. */
  managed: LlmConnection[] = [];
  profile: Profile | null = null;
  peers: { user_id: string; user_name: string; email: string }[] = [];

  /** The platform's catalog of providers, as the backend serves it:
   *  the page keeps no list of its own to fall out of step with it. */
  providers: LlmProvider[] = [];

  constructor(
    private service: SettingsLlmService,
    private profiles: ProfileService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    const [connections, providers, profile, peers] = await Promise.all([
      this.service.list(),
      this.service.providers(),
      this.profiles.get(),
      this.profiles.peers(),
    ]);
    this.connections = connections;
    this.managed = await this.everyConnection();
    this.providers = providers;
    this.profile = profile;
    this.peers = peers;
    this.loading = false;
  }

  /** Read the connections again: one was added, changed or removed. */
  async reload(): Promise<void> {
    this.connections = await this.service.list();
    this.managed = await this.everyConnection();
  }

  private async everyConnection(): Promise<LlmConnection[]> {
    return this.auth.can('settings:llm:manage_any')
      ? this.service.list(true) : this.connections;
  }
}
