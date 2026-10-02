import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface GroupMember {
  user_id: string;
  email: string;
  user_name: string;
}

export interface Group {
  group_id: string;
  org_id?: string;
  group_name: string;
  assigned_roles: string[];
  created_at?: string | null;
  /** Present on `get()` only — resolved member rows for display. */
  members?: GroupMember[];
}

/**
 * Client for IAM:Group — named bundles of roles.
 *
 * Membership lives on the USER (`assigned_groups`), so placing people into
 * groups happens on the Users screen; this service manages the group itself
 * and the roles it carries. Attaching roles is bounded by the backend's
 * grant boundary, and a refusal carries `ungrantable`.
 */
@Injectable({ providedIn: 'root' })
export class GroupService {
  constructor(private request: RequestService) {}

  async list(): Promise<Group[]> {
    const r = await this.request.gateway('IAM:Group:list');
    return Array.isArray(r?.groups) ? r.groups : [];
  }

  /** One group, with its members resolved for display. */
  async get(groupId: string): Promise<Group | null> {
    const r = await this.request.gateway('IAM:Group:get', { group_id: groupId });
    return r?.group ?? null;
  }

  async create(
    groupName: string,
    roleIds: string[],
  ): Promise<{ group?: Group; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('IAM:Group:create', {
      group_name: groupName,
      assigned_roles: roleIds,
    });
  }

  async rename(
    groupId: string,
    groupName: string,
  ): Promise<{ group?: Group; error?: string }> {
    return this.request.gateway('IAM:Group:rename', {
      group_id: groupId,
      group_name: groupName,
    });
  }

  async setRoles(
    groupId: string,
    roleIds: string[],
  ): Promise<{ group?: Group; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('IAM:Group:set_roles', {
      group_id: groupId,
      assigned_roles: roleIds,
    });
  }

  async remove(groupId: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('IAM:Group:delete', { group_id: groupId });
  }
}
