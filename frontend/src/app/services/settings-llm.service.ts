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
