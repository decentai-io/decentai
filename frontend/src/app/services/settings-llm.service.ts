import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

/** One LLM connection — a model the organization's chats may think with.
 *
 *  Its own record in the settings module, not a secret on a definition.
 *  `resource_ref` and `keys` are deliberately the secret-shaped field
 *  names: the chat picker, the preference and the chat's llm block were
 *  all built against that shape and keep working unchanged. The api key
 *  is write-only — nothing here ever returns it. */
export interface LlmConnection {
  resource_ref: string;
  name: string;
  /** Who may see (and so use) it — the secret layer's owner map. The
   *  default connection is always organization-wide. */
  owner: { groups: string[]; users: string[] };
  created_by: string;
  keys: {
    provider: string;
    model: string;
    endpoint: string;
    reasoning_effort?: string;
    /** What the model is for: a chat thinks with a `chat` connection;
     *  agent routing embeds with an `embedding` one. */
    purpose?: 'chat' | 'embedding' | 'transcription';
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
  /** One of the few most people look for: shown first. */
  popular?: boolean;
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
  model: string;
  endpoint: string;
  /** How hard a reasoning model thinks before each step: blank for the
   *  provider's default, else minimal, low, medium or high. */
  reasoning_effort?: string;
  purpose?: 'chat' | 'embedding' | 'transcription';
  /** Blank on update means keep the stored key. */
  api_key: string;
  /** Absent leaves sharing as it is. */
  owner?: { groups: string[]; users: string[] };
}

/** Client for Settings:Llm — the organization's LLM connections, exactly
 *  one of them the default. */
@Injectable({ providedIn: 'root' })
export class SettingsLlmService {
  constructor(private request: RequestService) {}

  async list(): Promise<LlmConnection[]> {
    const r = await this.request.gateway('Settings:Llm:list');
    return Array.isArray(r?.connections) ? r.connections : [];
  }

  async providers(): Promise<LlmProvider[]> {
    const r = await this.request.gateway('Settings:Llm:providers');
    return Array.isArray(r?.providers) ? r.providers : [];
  }

  async models(provider: string, kind: LlmModelKind | '' = ''): Promise<LlmModel[]> {
    const r = await this.request.gateway('Settings:Llm:providers', { provider, kind });
    return Array.isArray(r?.models) ? r.models : [];
  }

  async create(draft: LlmConnectionDraft): Promise<{
    connection?: LlmConnection; error?: string;
  }> {
    return this.request.gateway('Settings:Llm:create', { ...draft });
  }

  async update(connectionId: string, draft: LlmConnectionDraft): Promise<{
    connection?: LlmConnection; error?: string;
  }> {
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
