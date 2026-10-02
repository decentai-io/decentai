import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface PolicyStatement {
  effect: 'Allow' | 'Deny';
  actions: string[];
  resources: string[];
}

export interface Permissions {
  statements: PolicyStatement[];
}

export interface Policy {
  policy_id: string;
  name: string;
  description: string;
  permissions: Permissions;
  created_at?: string | null;
}

/**
 * Client for IAM:Policy — where permissions are authored.
 *
 * A policy is the leaf of the access chain (user → groups → roles →
 * policies). The backend validates every action against its catalog and
 * refuses any grant the author does not hold themselves — a refusal carries
 * `ungrantable`, the exact actions that were out of reach, so the editor can
 * show them.
 */
@Injectable({ providedIn: 'root' })
export class PolicyService {
  constructor(private request: RequestService) {}

  async list(): Promise<Policy[]> {
    const r = await this.request.gateway('IAM:Policy:list');
    return Array.isArray(r?.policies) ? r.policies : [];
  }

  async create(
    name: string,
    description: string,
    permissions: Permissions,
  ): Promise<{ policy?: Policy; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('IAM:Policy:create', {
      name,
      description,
      permissions,
    });
  }

  async update(
    policyId: string,
    changes: { name?: string; description?: string; permissions?: Permissions },
  ): Promise<{ policy?: Policy; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('IAM:Policy:update', {
      policy_id: policyId,
      ...changes,
    });
  }

  async remove(policyId: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('IAM:Policy:delete', { policy_id: policyId });
  }
}
