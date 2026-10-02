import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

import { AiSessionService } from './ai-session.service';

/**
 * Client for AI:Switch — the person's own stop for everything of
 * theirs. Stopping ends every chat they have running, at once, and
 * holds it stopped; resuming is theirs too, and never happens by
 * itself.
 */
@Injectable({ providedIn: 'root' })
export class WorkSwitchService {
  /** Whether this person has stopped everything. */
  readonly stopped$ = new BehaviorSubject<boolean>(false);

  constructor(private ai: AiSessionService) {}

  async refresh(): Promise<void> {
    const res = await this.ai.ai('AI:Switch:Status', {});
    if (!res.error) {
      this.stopped$.next(!!res.data?.stopped);
    }
  }

  /** Stop everything. Answers how many chats were ended, or the error. */
  async stop(): Promise<{ chats?: number; error?: string }> {
    const res = await this.ai.ai('AI:Switch:Stop', {});
    if (res.error) {
      return { error: res.error };
    }
    this.stopped$.next(true);
    return { chats: Number(res.data?.chats || 0) };
  }

  async resume(): Promise<{ error?: string }> {
    const res = await this.ai.ai('AI:Switch:Resume', {});
    if (res.error) {
      return { error: res.error };
    }
    this.stopped$.next(false);
    return {};
  }
}
