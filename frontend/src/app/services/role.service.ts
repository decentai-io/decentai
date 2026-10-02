import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface Role {
  role_id: string;
  role_name: string;
  description: string;
  assigned_policies: string[];
  created_at?: string | null;
}

/**
 * Client for IAM:Role — named bundles of policies.
 *
 * A role carries no statements of its own; attaching policies is bounded by
 * the backend's grant boundary (you cannot bundle permissions you do not
 * hold), and a refusal carries `ungrantable` with the actions out of reach.
 */
@Injectable({ providedIn: 'root' })
export class RoleService {
  constructor(private request: RequestService) {}

  async list(): Promise<Role[]> {
    const r = await this.request.gateway('IAM:Role:list');
    return Array.isArray(r?.roles) ? r.roles : [];
  }

  async create(
    roleName: string,
    description: string,
    policyIds: string[],
  ): Promise<{ role?: Role; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('IAM:Role:create', {
      role_name: roleName,
      description,
      assigned_policies: policyIds,
    });
  }

  async update(
    roleId: string,
    roleName: string,
    description: string,
  ): Promise<{ role?: Role; error?: string }> {
    return this.request.gateway('IAM:Role:update', {
      role_id: roleId,
      role_name: roleName,
      description,
    });
  }

  async setPolicies(
    roleId: string,
    policyIds: string[],
  ): Promise<{ role?: Role; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('IAM:Role:set_policies', {
      role_id: roleId,
      assigned_policies: policyIds,
    });
  }

  async remove(roleId: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('IAM:Role:delete', { role_id: roleId });
  }
}
