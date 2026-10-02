import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface Member {
  user_id: string;
  org_id: string;
  email: string;
  user_name: string;
  status: string;
  assigned_groups: string[];
  created_at?: string | null;
  last_login_at?: string | null;
}

export interface Invitation {
  invitation_id: string;
  email: string;
  status: string;
  assigned_groups: string[];
  invited_by_email: string;
  created_at?: string | null;
  expires_at?: string | null;
}

export interface InviteResult {
  invitation?: Invitation;
  /** False when email is not configured — `accept_url` carries the link instead. */
  email_sent?: boolean;
  accept_url?: string | null;
  error?: string;
  /** Actions the caller holds too few of to grant, when the backend refused. */
  ungrantable?: string[];
}

/**
 * Client for the IAM domain's membership surface: who is in the deployment,
 * and who has been invited into it.
 *
 * A user's access is entirely their `assigned_groups` — there is no direct
 * role assignment. Re-inviting an address refreshes its link (the backend
 * keeps one live invitation per email), which is also how "resend" works.
 */
@Injectable({ providedIn: 'root' })
export class MembersService {
  constructor(private request: RequestService) {}

  // ── Users ───────────────────────────────────────────────────────────
  async list(): Promise<Member[]> {
    const r = await this.request.gateway('IAM:User:list');
    return Array.isArray(r?.users) ? r.users : [];
  }

  async setGroups(
    userId: string,
    groupIds: string[],
  ): Promise<{ user?: Member; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('IAM:User:set_groups', {
      user_id: userId,
      assigned_groups: groupIds,
    });
  }

  async setStatus(userId: string, status: 'active' | 'disabled'): Promise<any> {
    return this.request.gateway('IAM:User:set_status', {
      user_id: userId,
      status,
    });
  }

  /** What removing this person would delete and hand over. */
  async leaving(userId: string): Promise<any> {
    return this.request.gateway('IAM:User:leaving', { user_id: userId });
  }

  async remove(userId: string, successorId = ''): Promise<any> {
    return this.request.gateway('IAM:User:delete', {
      user_id: userId, ...(successorId ? { successor_id: successorId } : {}),
    });
  }

  // ── Invitations ─────────────────────────────────────────────────────
  async invitations(): Promise<Invitation[]> {
    const r = await this.request.gateway('IAM:Invitation:list');
    return Array.isArray(r?.invitations) ? r.invitations : [];
  }

  /** Invite an address, or refresh the link an earlier invite created. */
  async invite(email: string, groupIds: string[]): Promise<InviteResult> {
    return this.request.gateway('IAM:Invitation:create', {
      email,
      assigned_groups: groupIds,
    });
  }

  async revoke(invitationId: string): Promise<any> {
    return this.request.gateway('IAM:Invitation:revoke', {
      invitation_id: invitationId,
    });
  }
}
