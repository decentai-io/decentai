import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface Organization {
  org_id: string;
  org_name: string;
  created_at?: string | null;
}

/**
 * Client for IAM:Organization — the caller's own organization.
 *
 * Renaming is the only mutation: through this door there is no create,
 * no delete and no list.
 */
@Injectable({ providedIn: 'root' })
export class OrganizationService {
  constructor(private request: RequestService) {}

  async get(): Promise<Organization | null> {
    const r = await this.request.gateway('IAM:Organization:get');
    return r?.organization ?? null;
  }

  async rename(
    orgName: string,
  ): Promise<{ organization?: Organization; error?: string }> {
    return this.request.gateway('IAM:Organization:rename', {
      org_name: orgName,
    });
  }
}
