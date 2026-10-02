import { Injectable } from '@angular/core';

import { LlmConnection } from './settings-llm.service';
import { RequestService } from './request.service';

/** How a chat finds the right agent among many — the organization's
 *  numbers, and the embedding model they route by. */
export interface RoutingSettings {
  /** The embedding connection's ref, or '' for none: every agent listed. */
  embedding_connection_id: string;
  /** Above this many enabled agents, a chat routes instead of listing all. */
  threshold: number;
  /** How many agents the assistant is shown per message. */
  shortlist: number;
  /** How many closest agents the rerank chooses among. */
  candidates: number;
  /** Whether the chat's model reranks the candidates. */
  rerank: boolean;
  /** How many agents a chat keeps open at once. */
  open_max: number;
}

/** Client for Settings:Routing. */
@Injectable({ providedIn: 'root' })
export class SettingsRoutingService {
  constructor(private request: RequestService) {}

  async get(): Promise<{ routing: RoutingSettings; embedding_connection: LlmConnection | null }> {
    const r = await this.request.gateway('Settings:Routing:get');
    return {
      routing: r?.routing,
      embedding_connection: r?.embedding_connection ?? null,
    };
  }

  async update(changes: Partial<RoutingSettings>): Promise<{
    routing?: RoutingSettings; embedding_connection?: LlmConnection | null; error?: string;
  }> {
    return this.request.gateway('Settings:Routing:update', { ...changes });
  }
}
