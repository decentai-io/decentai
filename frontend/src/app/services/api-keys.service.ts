import { Injectable } from '@angular/core';

import { AiSessionService } from './ai-session.service';

/** One of the person's keys, as the page sees it: never the secret. */
export interface ApiKey {
  key_id: string;
  name: string;
  /** The first characters, enough to tell keys apart. */
  shown: string;
  created_at?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

export interface ApiKeyPage {
  keys: ApiKey[];
  limit: number;
}

/** What creation returns — the only time the secret exists in the open. */
export interface MadeApiKey extends ApiKey {
  key: string;
}

/**
 * Client for Settings:ApiKey — a person's own standing credentials for
 * scripts. A key acts as its owner: the same permissions, walked on
 * every request. Shown once at creation; revoking is the only edit.
 */
@Injectable({ providedIn: 'root' })
export class ApiKeysService {
  constructor(private ai: AiSessionService) {}

  async list(): Promise<ApiKeyPage> {
    const res = await this.ai.ai('Settings:ApiKey:List', {});
    const data = res.data as any;
    return { keys: data?.keys || [], limit: Number(data?.limit) || 0 };
  }

  async create(name: string): Promise<{ key?: MadeApiKey; error?: string }> {
    const res = await this.ai.ai('Settings:ApiKey:Create', { name });
    if (res.error) return { error: res.error };
    return { key: (res.data as any)?.key };
  }

  async revoke(keyId: string): Promise<{ revoked?: boolean; error?: string }> {
    const res = await this.ai.ai('Settings:ApiKey:Revoke', { key_id: keyId });
    if (res.error) return { error: res.error };
    return { revoked: true };
  }
}
