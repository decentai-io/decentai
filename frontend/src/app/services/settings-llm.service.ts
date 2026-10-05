import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

/** One LLM connection — a provider and the key to it.
 *
 *  Which of the provider's models a chat thinks with is the chat's own
 *  choice; a connection only names the one it starts with (`keys.model`).
 *
 *  Its own record in the settings module, not a secret on a definition.
 *  `resource_ref` and `keys` are deliberately the secret-shaped field
 *  names: the chat picker, the preference and the chat's llm block were
 *  all built against that shape. The api key is write-only — nothing
 *  here ever returns it. */
export interface LlmConnection {
  resource_ref: string;
  name: string;
  /** Who may see (and so use) it — the secret layer's owner map. */
  owner: { groups: string[]; users: string[] };
  created_by: string;
  keys: {
    provider: string;
    /** The model it starts with: what a chat that chose nothing uses. */
    model: string;
    endpoint: string;
  };
  is_default: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

/** One provider a connection may name — an entry of the platform's
 *  catalog (contracts/llm_providers.json), served by the backend so the
 *  form offers exactly what the store accepts and the runtime can reach. */
export interface LlmProvider {
  id: string;
  name: string;
  protocol: 'openai' | 'openai-responses' | 'anthropic' | 'gemini' | 'bedrock';
  /** Where the provider answers. A `<blank>` in it is the customer's
   *  own part (an account, a region) and is filled in on the form; the
   *  custom entry's is empty, the whole address being the person's. */
  endpoint: string;
  /** Set on the few most people look for, which a page shows first:
   *  its place among them, from 1. */
  popular?: number;
}

export type LlmModelKind = 'chat' | 'embedding' | 'transcription';

/** A model a provider is known to serve: its own id for it, a name to
 *  read, what it is for and — where the catalog knows — what it can do.
 *  An offer for the page: any model may be named. */
export interface LlmModel {
  id: string;
  name: string;
  kind: LlmModelKind;
  /** Reads a picture. */
  images?: boolean;
  /** Thinks before answering, and how hard it may be asked to. */
  reasoning?: boolean;
  efforts?: string[];
  /** The sizes, in tokens, of its window and of one reply. */
  context?: number;
  output?: number;
}

export interface LlmConnectionDraft {
  name: string;
  provider: string;
  /** The model the connection starts with. */
  model: string;
  endpoint: string;
  /** Blank on update means keep the stored key. */
  api_key: string;
  /** Absent leaves sharing as it is. */
  owner?: { groups: string[]; users: string[] };
  /** Ask the provider whether the key works before saving. */
  check?: boolean;
}

/** What the provider said when asked whether a key works. */
export interface LlmCheck {
  outcome: 'works' | 'refused' | 'unreachable' | 'unknown';
  reason: string;
}

export interface LlmSaved {
  connection?: LlmConnection;
  check?: LlmCheck;
  error?: string;
}

/** Client for Settings:Llm — the organization's connections to model
 *  providers, exactly one of them the default. */
@Injectable({ providedIn: 'root' })
export class SettingsLlmService {
  constructor(private request: RequestService) {}

  /** The connections this person can see. With `manage`, asked by the
   *  page connections are kept on: a holder of the manage-any grant is
   *  listed every connection of the organization, to maintain them. */
  async list(manage = false): Promise<LlmConnection[]> {
    const r = await this.request.gateway(
      'Settings:Llm:list', manage ? { manage: true } : {});
    return Array.isArray(r?.connections) ? r.connections : [];
  }

  async providers(): Promise<LlmProvider[]> {
    const r = await this.request.gateway('Settings:Llm:providers');
    return Array.isArray(r?.providers) ? r.providers : [];
  }

  /** The models the CATALOG lists for a provider — what the form that
   *  adds a connection offers before any key exists. */
  async catalogModels(provider: string, kind: LlmModelKind | '' = ''): Promise<LlmModel[]> {
    const r = await this.request.gateway('Settings:Llm:providers', { provider, kind });
    return Array.isArray(r?.models) ? r.models : [];
  }

  /** The models ONE connection offers, of one kind: the catalog's for
   *  its provider, or the server's own list where the catalog has none. */
  async models(connectionId: string, kind: LlmModelKind = 'chat'): Promise<LlmModel[]> {
    const r = await this.request.gateway('Settings:Llm:models', {
      connection_id: connectionId, kind,
    });
    return Array.isArray(r?.models) ? r.models : [];
  }

  async create(draft: LlmConnectionDraft): Promise<LlmSaved> {
    return this.request.gateway('Settings:Llm:create', { ...draft });
  }

  async update(connectionId: string, draft: Partial<LlmConnectionDraft>): Promise<LlmSaved> {
    return this.request.gateway('Settings:Llm:update', {
      connection_id: connectionId,
      ...draft,
    });
  }

  async setDefault(connectionId: string): Promise<{
    connection?: LlmConnection; error?: string;
  }> {
    return this.request.gateway('Settings:Llm:setdefault', {
      connection_id: connectionId,
    });
  }

  /** Hand a connection to another member; its sharing stays. */
  async transfer(connectionId: string, userId: string): Promise<{
    connection?: LlmConnection; error?: string;
  }> {
    return this.request.gateway('Settings:Llm:transfer', {
      connection_id: connectionId, user_id: userId,
    });
  }

  async remove(connectionId: string): Promise<{
    deleted?: boolean; error?: string;
  }> {
    return this.request.gateway('Settings:Llm:delete', {
      connection_id: connectionId,
    });
  }
}
