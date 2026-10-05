import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

/** Where a provider is: consent, tokens, and whose account it is
 *  (empty where the provider says so in the token response). */
export interface OauthEndpoints {
  authorize_url: string;
  token_url: string;
  identity_url: string;
}

/** One app the organization registered with a provider. The client
 *  secret is write-only: nothing here ever returns it. */
export interface OauthApp {
  resource_ref: string;
  provider: string;
  client_id: string;
  /** The provider's addresses this registration settles; an agent
   *  naming others cannot connect through it. */
  endpoints?: OauthEndpoints;
  /** Whether a secret is kept for it, never what it is. An app
   *  registered for a person's own computer may have none. */
  has_secret?: boolean;
  created_by: string;
  created_at?: string | null;
  updated_at?: string | null;
}

/** A provider id an installed agent's credential names, and which
 *  agents need it — what the page offers before anything typed. */
export interface DeclaredProvider {
  provider: string;
  needed_by: string[];
  /** The addresses the agents name for it — more than one set when
   *  they disagree. */
  endpoints?: (OauthEndpoints & { named_by: string[] })[];
}

/** Client for Settings:Oauth — the organization's connected apps, one
 *  per provider, so members can connect accounts with a click. */
@Injectable({ providedIn: 'root' })
export class SettingsOauthService {
  constructor(private request: RequestService) {}

  async list(): Promise<{
    apps: OauthApp[]; redirect_uri: string; declared: DeclaredProvider[];
  }> {
    const r = await this.request.gateway('Settings:Oauth:list');
    return {
      apps: Array.isArray(r?.apps) ? r.apps : [],
      redirect_uri: String(r?.redirect_uri || ''),
      declared: Array.isArray(r?.declared) ? r.declared : [],
    };
  }

  async create(draft: {
    provider: string; client_id: string; client_secret: string; endpoints?: OauthEndpoints;
  }): Promise<{
    app?: OauthApp; error?: string;
  }> {
    return this.request.gateway('Settings:Oauth:create', { ...draft });
  }

  /** Blank secret keeps the stored one. */
  async update(appId: string, draft: {
    client_id: string; client_secret: string; endpoints?: OauthEndpoints;
  }): Promise<{
    app?: OauthApp; error?: string;
  }> {
    return this.request.gateway('Settings:Oauth:update', { app_id: appId, ...draft });
  }

  async remove(appId: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('Settings:Oauth:delete', { app_id: appId });
  }
}
