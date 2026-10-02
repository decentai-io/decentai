import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

import { AiSessionService } from './ai-session.service';
import { AuthService } from './auth.service';

/** What a chat needs from the person, as its list row says it. */
export interface ChatAttention {
  working: boolean;
  cards: number;
  unseen: boolean;
}

/** How many chats need the person, for the sidebar — read from the
 *  chats list every so often while the app is open, and at once when a
 *  page that knows better asks. */
@Injectable({ providedIn: 'root' })
export class AttentionService {
  readonly count$ = new BehaviorSubject<number>(0);
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private aiSession: AiSessionService, private auth: AuthService) {}

  /** Start the poll once, for as long as the person is signed in. */
  start(): void {
    if (this.timer || !this.auth.can('ai:chat:list')) return;
    void this.refresh();
    this.timer = setInterval(() => { void this.refresh(); }, 30000);
  }

  /** End the poll on sign-out: a list asked for without a session is
   *  answered 401, and a 401 reloads the page. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.count$.next(0);
  }

  async refresh(): Promise<void> {
    try {
      const chats = await this.aiSession.listChats();
      this.absorb(chats);
    } catch {
      // A list that cannot be read changes nothing shown.
    }
  }

  /** A page that already read the list tells the count from it. */
  absorb(chats: any[]): void {
    const needing = (chats || []).filter((chat) =>
      (chat.status || 'active') === 'active'
      && (Number(chat.attention?.cards || 0) > 0 || !!chat.attention?.unseen));
    this.count$.next(needing.length);
  }
}
