import { Injectable } from '@angular/core';

import { AiSessionService } from './ai-session.service';

export interface Memory {
  memory_id: string;
  text: string;
  source_chat_id: string;
  created_at?: string | null;
  /** True once a person edited it: the words are theirs, not the chat's. */
  corrected?: boolean;
  updated_at?: string | null;
  /** Written by the person rather than learned in a conversation. */
  authored?: boolean;
}

/** The record and the size of the shelf — at the cap the oldest is
 *  dropped to make room for the newest. */
export interface MemoryPage {
  memories: Memory[];
  limit: number;
}

/**
 * Client for Settings:Memory — what the assistant was told to remember about
 * you. A memory is either learned (a `remember` action in a chat, which
 * you watch happen) or written here by you; the record says which. This
 * service is how you add to, review, correct, and remove from it.
 */
@Injectable({ providedIn: 'root' })
export class MemoryService {
  constructor(private ai: AiSessionService) {}

  async list(): Promise<MemoryPage> {
    const res = await this.ai.ai('Settings:Memory:List', {});
    const data = res.data as any;
    return {
      memories: data?.memories || [],
      limit: Number(data?.limit) || 0,
    };
  }

  /** The person's own write. The server marks it authored by them —
   *  a chat's `remember` arrives at this same endpoint with a chat id. */
  async create(text: string): Promise<{ data?: any; error?: string }> {
    return this.ai.ai('Settings:Memory:Create', { text });
  }

  async remove(memoryId: string): Promise<{ data?: any; error?: string }> {
    return this.ai.ai('Settings:Memory:Delete', { memory_id: memoryId });
  }

  async update(memoryId: string, text: string): Promise<{ data?: any; error?: string }> {
    return this.ai.ai('Settings:Memory:Update', { memory_id: memoryId, text });
  }
}
