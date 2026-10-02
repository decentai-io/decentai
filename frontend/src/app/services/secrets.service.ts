import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface SecretOwner {
  groups: string[];
  users: string[];
}

/** An installed agent that may use this credential. For a grant,
 *  `resource_id` names the agent's slot it was lent to. For a saved
 *  login it is empty and `site` says where the person allowed it. */
export interface SecretUser {
  agent_ref: string;
  name: string;
  resource_id: string;
  site?: string;
}

export interface Secret {
  resource_ref: string;
  resource_id: string; // the definition slug
  name: string;
  definition_ref: string;
  definition_version: number;
  owner: SecretOwner;
  keys: Record<string, any>;
  created_by: string;
  created_at?: string | null;
  updated_at?: string | null;
  /** Which agents were lent this, or allowed it on a site. A
   *  credential saved under an agent's own slot is not listed here:
   *  that agent is `home_agent`. */
  used_by_agents?: SecretUser[];
  /** The agent whose own slot this was created under — it reaches the
   *  credential with no grant, so deleting leaves it without one. */
  home_agent?: { agent_ref: string; name: string };
}

/**
 * Client for Secrets:Secret — definition-driven instances. The caller sends
 * a flat `fields` dict; the backend routes each field into plain keys or
 * the encrypted values per the definition, and values are NEVER returned.
 * On update, an empty string for an encrypted field means "keep".
 */
@Injectable({ providedIn: 'root' })
export class SecretsService {
  constructor(private request: RequestService) {}

  async list(definitionId?: string): Promise<Secret[]> {
    const r = await this.request.gateway(
      'Secrets:Secret:list',
      definitionId ? { resource_id: definitionId } : {},
    );
    return Array.isArray(r?.resources) ? r.resources : [];
  }

  async create(payload: {
    definition_ref: string;
    name: string;
    fields: Record<string, any>;
    owner?: Partial<SecretOwner>;
  }): Promise<{ resource?: Secret; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('Secrets:Secret:create', payload);
  }

  async update(
    ref: string,
    changes: {
      name?: string;
      fields?: Record<string, any>;
      owner?: Partial<SecretOwner>;
      /** Move the secret onto its family's CURRENT version: stored
       *  fields carry over where the new shape knows them, and the
       *  whole is re-validated against it. */
      migrate?: boolean;
    },
  ): Promise<{ resource?: Secret; error?: string; ungrantable?: string[] }> {
    return this.request.gateway('Secrets:Secret:update', {
      resource_ref: ref,
      ...changes,
    });
  }

  /** Which of your visible credentials answers when several could —
   *  and the chat has not chosen one itself. Empty ref clears it. */
  async setDefault(
    family: string, ref: string,
  ): Promise<{ default?: string; cleared?: boolean; error?: string }> {
    return this.request.gateway('Secrets:Secret:set_default', {
      resource_id: family,
      resource_ref: ref,
    });
  }

  /** Hand a secret to another member; its sharing stays. */
  async transfer(ref: string, userId: string): Promise<{ resource?: Secret; error?: string }> {
    return this.request.gateway('Secrets:Secret:transfer', { resource_ref: ref, user_id: userId });
  }

  async remove(ref: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('Secrets:Secret:delete', {
      resource_ref: ref,
    });
  }
}
