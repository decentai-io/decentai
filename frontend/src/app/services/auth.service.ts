import { Injectable } from '@angular/core';

import { environment } from 'src/environments/environment';
import { DataStoreService } from './datastore.service';

export interface AuthUser {
  user_id: string;
  org_id: string;
  email: string;
  user_name: string;
  status: string;
  assigned_groups: string[];
}

export interface AuthOrganization {
  org_id: string;
  org_name: string;
}

export interface CatalogAction {
  action: string;
  description: string;
}

export interface CatalogService {
  service: string;
  label: string;
  actions: CatalogAction[];
}

/** What the backend answers with once a session exists. */
export interface Identity {
  user: AuthUser;
  organization: AuthOrganization;
  allowed_actions: string[];
  /** What the deployment is: served to an organization at an address
   *  of its own, or installed on one person's computer. */
  deployment?: { kind: 'web' | 'desktop' };
  /** Rides along on /auth/me: the action vocabulary, for the policy editor. */
  catalog?: CatalogService[];
}

/**
 * The client half of the authentication module.
 *
 * Sessions live in an httpOnly cookie, so there is no token to hold here —
 * `credentials: 'include'` is what carries them. What this service does hold
 * is the resolved identity, and in particular `allowed_actions`: the backend
 * has already walked user → groups → roles → policies, so `can()` is a set
 * membership test rather than a second policy engine that could drift from
 * the real one.
 *
 * There is no signup — the deployment's organization and first administrator
 * are seeded at launch; everyone else arrives by invitation.
 *
 * Hiding a control the user cannot use is a courtesy, not a security
 * boundary — every action is checked again on the server.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly endpoint = environment.apiEndpoint;

  identity: Identity | null = null;

  /** Whether this is the platform on one person's own computer. A
   *  deployment that does not say is a web one. */
  get isDesktop(): boolean {
    return this.identity?.deployment?.kind === 'desktop';
  }
  private allowed = new Set<string>();

  /** Whether the session has been read at least once, and the in-flight read
   *  if one is happening — so a route guard that runs before boot finishes
   *  can await the same request instead of firing a second one. */
  private loaded = false;
  private inflight: Promise<boolean> | null = null;

  constructor(private datastore: DataStoreService) {}

  get user(): AuthUser | null {
    return this.identity?.user ?? null;
  }

  get organization(): AuthOrganization | null {
    return this.identity?.organization ?? null;
  }

  get catalog(): CatalogService[] {
    return this.identity?.catalog ?? [];
  }

  /** May the current user perform this action? */
  can(action: string): boolean {
    return this.allowed.has(action);
  }

  // ------------------------------------------------------------------
  // Flow
  // ------------------------------------------------------------------

  /** Email + password. One organization per deployment, so one step. */
  async login(payload: {
    email: string;
    password: string;
    /** Keep the sign-in on this device past the browser being closed. */
    remember?: boolean;
  }): Promise<{ ok: boolean; error?: string; throttled?: boolean }> {
    const { status, data } = await this.post('auth/login', payload);

    if (status === 429) {
      return { ok: false, error: data?.error ?? '', throttled: true };
    }

    return this.adopt(status, data);
  }

  /**
   * What an invitation link is an invitation to.
   *
   * The token goes in the body, not the query string — a token in a URL ends
   * up in browser history and referrer headers.
   */
  async invitation(
    token: string,
  ): Promise<{ ok: boolean; email?: string; orgName?: string; error?: string }> {
    const { status, data } = await this.post('auth/invitation', { token });

    return status === 200
      ? {
          ok: true,
          email: data?.email ?? '',
          orgName: data?.organization?.org_name ?? '',
        }
      : {
          ok: false,
          error: data?.error ?? 'This invitation link is invalid or has expired.',
        };
  }

  /** Accept an invitation: become a user and sign in, in one step. */
  async acceptInvitation(payload: {
    token: string;
    name: string;
    password: string;
  }): Promise<{ ok: boolean; error?: string }> {
    const { status, data } = await this.post('auth/invitation/accept', payload);
    return this.adopt(status, data);
  }

  /**
   * Ask for a password reset link.
   *
   * Always succeeds from the caller's point of view — the backend answers the
   * same way whether or not the account exists, so this cannot be used to
   * discover who has one. Where no email is set up, `message` says how this
   * install resets a password instead.
   */
  async forgotPassword(email: string): Promise<{
    ok: boolean;
    message?: string;
    error?: string;
  }> {
    const { status, data } = await this.post('auth/password/forgot', { email });

    if (status !== 200) {
      return { ok: false, error: data?.error ?? 'Could not send a reset link.' };
    }

    return {
      ok: true,
      message: data?.message ?? '',
    };
  }

  /** What a reset link is for, so the screen can name the account. */
  async resetInfo(
    token: string,
  ): Promise<{ ok: boolean; email?: string; orgName?: string; error?: string }> {
    const { status, data } = await this.post('auth/password/reset/check', {
      token,
    });

    return status === 200
      ? {
          ok: true,
          email: data?.email ?? '',
          orgName: data?.organization?.org_name ?? '',
        }
      : {
          ok: false,
          error: data?.error ?? 'This reset link is invalid or has expired.',
        };
  }

  /** Set a new password from a link, and sign in. */
  async resetPassword(payload: {
    token: string;
    password: string;
  }): Promise<{ ok: boolean; error?: string }> {
    const { status, data } = await this.post('auth/password/reset', payload);
    return this.adopt(status, data);
  }

  /**
   * Re-read the session. Called on boot to choose between app and login, and
   * again as the app runs so a permission change takes effect without a manual
   * reload. Concurrent callers share one in-flight request.
   */
  refresh(): Promise<boolean> {
    if (this.inflight) {
      return this.inflight;
    }
    this.inflight = this.request('auth/me', 'GET')
      .then(({ status, data }) => {
        // Only an answer counts as having read the session: after a
        // failure to reach the backend, the next guard asks again.
        this.loaded = this.answered(status);
        return this.adopt(status, data).ok;
      })
      .finally(() => {
        this.inflight = null;
      });
    return this.inflight;
  }

  /**
   * Resolve once the session's permissions are known. A route guard calls this
   * so it never decides against an empty permission set while boot is still
   * fetching it.
   */
  async ensureLoaded(): Promise<void> {
    if (!this.loaded) {
      await this.refresh();
    }
  }

  async logout(): Promise<void> {
    await this.post('auth/logout', {});
    this.identity = null;
    this.allowed = new Set<string>();
    this.loaded = false;
    this.datastore.setUserInfo(null);
  }

  async changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<{ ok: boolean; error?: string }> {
    const { status, data } = await this.post('auth/password', {
      current_password: currentPassword,
      new_password: newPassword,
    });
    return status === 200
      ? { ok: true }
      : { ok: false, error: data?.error ?? 'Could not change your password.' };
  }

  // ------------------------------------------------------------------
  // Internals
  // ------------------------------------------------------------------

  /** Whether the backend answered for itself, as opposed to not being
   *  reached (0) or failing on its own account (5xx). */
  private answered(status: number): boolean {
    return status !== 0 && status < 500;
  }

  /** Take on an identity response, or report why it failed. */
  private adopt(status: number, data: any): { ok: boolean; error?: string } {
    if (status !== 200 || !data?.user) {
      // A backend that did not answer says nothing about the session, so
      // what is held stays: a dropped connection or a restart must not
      // empty a signed-in person's permissions.
      if (this.answered(status)) {
        this.identity = null;
        this.allowed = new Set<string>();
      }
      return { ok: false, error: data?.error ?? '' };
    }

    this.identity = data as Identity;
    this.allowed = new Set<string>(data.allowed_actions ?? []);

    // The rest of the shell reads the user from the datastore, so keep it in
    // step. `name` mirrors `user_name` for the older screens that show it.
    this.datastore.setUserInfo({
      ...data.user,
      name: data.user.user_name ?? '',
      organization_id: data.user.org_id,
      organization_name: data.organization?.org_name ?? '',
    });

    return { ok: true };
  }

  private post(path: string, body: any) {
    return this.request(path, 'POST', body);
  }

  private async request(
    path: string,
    method: 'GET' | 'POST',
    body?: any,
  ): Promise<{ status: number; data: any }> {
    try {
      const response = await fetch(`${this.endpoint}${path}`, {
        method,
        credentials: 'include', // the session cookie rides on this
        headers: { 'Content-Type': 'application/json' },
        body: method === 'POST' ? JSON.stringify(body ?? {}) : undefined,
      });

      const text = await response.text();
      let data: any;
      try {
        data = JSON.parse(text);
      } catch {
        data = {};
      }

      return { status: response.status, data };
    } catch {
      return {
        status: 0,
        data: { error: 'Could not reach the server. Check your connection.' },
      };
    }
  }
}
