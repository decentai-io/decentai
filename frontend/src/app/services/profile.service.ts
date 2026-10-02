import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface Profile {
  user_id: string;
  org_id?: string;
  email: string;
  user_name: string;
  status: string;
  assigned_groups: string[];
  groups: { group_id: string; group_name: string }[];
  created_at?: string | null;
  last_login_at?: string | null;
  preferences?: {
    chat?: {
      llm_secret_ref?: string;
      enabled_agents?: string[];
      /** Skill refs a new chat starts with; absent means all of them. */
      enabled_skills?: string[];
      /** How far a new chat may act unattended, 0 to 3. */
      trust_level?: number;
      /** Steps a new chat's turn may take; 0 is unlimited. */
      max_turns?: number;
      /** Skills a new chat's frame lists; 0 is every one. */
      max_skills?: number;
    };
    /** Which credential answers per family, when a chat has not chosen
     *  one itself: {family_slug: resource_ref}. */
    secrets?: {
      defaults?: Record<string, string>;
    };
  };
}

/**
 * Client for Account:Profile — the caller's OWN record.
 *
 * Self-service is not a special case: it goes through the same gateway and
 * the same policy check as everything else, so whether this page works at
 * all is granted from the admin page (normally to everyone, via the
 * built-in Everyone group).
 */
@Injectable({ providedIn: 'root' })
export class ProfileService {
  constructor(private request: RequestService) {}

  async get(): Promise<Profile | null> {
    const r = await this.request.gateway('Account:Profile:get');
    return r?.profile ?? null;
  }

  async rename(
    userName: string,
  ): Promise<{ profile?: Profile; error?: string }> {
    return this.request.gateway('Account:Profile:update', {
      user_name: userName,
    });
  }

  /** The people who share an explicit group with you — the reach of
   *  "share with a person". Never the whole organization. */
  async peers(): Promise<
    { user_id: string; user_name: string; email: string }[]
  > {
    const r = await this.request.gateway('Account:Profile:peers');
    return Array.isArray((r as any)?.peers) ? (r as any).peers : [];
  }

  async saveDefaultLlm(
    secretRef: string | null,
  ): Promise<{ profile?: Profile; error?: string }> {
    return this.request.gateway('Account:Profile:update', {
      preferences: { chat: { llm_secret_ref: secretRef } },
    });
  }

  async saveDefaultAgents(agentRefs: string[]): Promise<{ profile?: Profile; error?: string }> {
    return this.request.gateway('Account:Profile:update', {
      preferences: { chat: { enabled_agents: agentRefs } },
    });
  }

  /** The defaults a new chat starts with — model, trust, budget, skills
   *  listed — checked by the same settings the chat page's are. */
  async saveChatDefaults(changes: {
    llm_secret_ref?: string | null; trust_level?: number; max_turns?: number; max_skills?: number;
  }): Promise<{ profile?: Profile; error?: string }> {
    return this.request.gateway('Account:Profile:update', {
      preferences: { chat: { ...changes } },
    });
  }

  async saveDefaultSkills(skillRefs: string[]): Promise<{ profile?: Profile; error?: string }> {
    return this.request.gateway('Account:Profile:update', {
      preferences: { chat: { enabled_skills: skillRefs } },
    });
  }
}
