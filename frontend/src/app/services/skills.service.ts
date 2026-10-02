import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface Skill {
  resource_ref: string;
  resource_id: string;
  owner: { groups: string[]; users: string[] };
  keys: { title: string; summary: string };
  values?: { body: string };
  /** Encrypted under a key version this deployment no longer holds: the
   *  catalog line is still true, the body cannot be read. */
  unreadable?: boolean;
  created_by: string;
  created_at?: string | null;
  updated_at?: string | null;
}

/**
 * Client for Skills:Skill — user-authored knowledge the assistant reads
 * on demand. Listing returns the catalog (title + summary); the body
 * travels only on get, the same progressive disclosure the runtime's
 * prompt applies.
 */
@Injectable({ providedIn: 'root' })
export class SkillsService {
  constructor(private request: RequestService) {}

  async list(): Promise<Skill[]> {
    const r = await this.request.gateway('Skills:Skill:List');
    return Array.isArray(r?.resources) ? r.resources : [];
  }

  async get(ref: string): Promise<Skill | null> {
    const r = await this.request.gateway('Skills:Skill:Get', {
      resource_ref: ref,
    });
    return r?.resource ?? null;
  }

  async create(fields: {
    title: string;
    summary: string;
    body: string;
    owner: Record<string, any>;
  }): Promise<{ resource?: Skill; error?: string }> {
    return this.request.gateway('Skills:Skill:Create', fields);
  }

  async update(
    ref: string,
    fields: Partial<{
      title: string;
      summary: string;
      body: string;
      owner: Record<string, any>;
    }>,
  ): Promise<{ resource?: Skill; error?: string }> {
    return this.request.gateway('Skills:Skill:Update', {
      resource_ref: ref,
      ...fields,
    });
  }

  /** Hand a skill to another member; its sharing stays. */
  async transfer(ref: string, userId: string): Promise<{ resource?: any; error?: string }> {
    return this.request.gateway('Skills:Skill:transfer', { resource_ref: ref, user_id: userId });
  }

  async remove(ref: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('Skills:Skill:Delete', { resource_ref: ref });
  }
}
