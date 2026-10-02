import { Injectable, NgZone } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { AiSessionService } from './ai-session.service';

export type ChatConnectionState =
  | 'disconnected' | 'connecting' | 'connected' | 'reconnecting'
  | 'offline' | 'unauthorized' | 'archived' | 'replaced'
  /** The chat is gone or no longer this person's; the relay closed the
   *  socket over a message it could not read. Neither is retried. */
  | 'missing' | 'refused';

@Injectable({ providedIn: 'root' })
export class ChatConnectionService {
  readonly state$ = new BehaviorSubject<ChatConnectionState>('disconnected');
  readonly events$ = new Subject<string>();
  /** The event log no longer holds everything this page missed: the
   *  tail the backend keeps had moved past where the page stopped. What
   *  is left of it would be applied over a hole, so nothing is replayed
   *  and the page is told to read the chat again from its records. */
  readonly gap$ = new Subject<void>();

  private socket: WebSocket | null = null;
  private chatId: string | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private stopped = true;
  private replayAfter: () => number | null = () => null;
  /** Live frames that arrived while history was being replayed, held
   *  until replay has been applied. Both paths carry the event's
   *  sequence, so a frame both deliver is applied once, and one only
   *  the socket saw is applied after everything replay knew about —
   *  never before it, which is how an event used to be applied out of
   *  order or twice. */
  private held: string[] = [];
  private replaying = false;

  /** How many pages one replay may ask for — far more than the tail
   *  the backend keeps could fill, so a loop that is not advancing ends. */
  private static readonly REPLAY_PAGES = 10;

  constructor(
    private aiSession: AiSessionService,
    private zone: NgZone,
  ) {}

  connect(chatId: string, replayAfter: () => number | null): void {
    this.disconnect(false);
    this.chatId = chatId;
    this.replayAfter = replayAfter;
    this.stopped = false;
    this.open(false);
  }

  reconnectNow(): void {
    if (!this.chatId) return;
    // The person asked: a tab that stood down for a newer connection
    // takes the relay back, and a refused one is refused again aloud.
    this.stopped = false;
    this.reconnectAttempts = 0;
    this.clearTimer();
    this.open(false);
  }

  send(endpoint: string, data: unknown): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify({ endpoint, data }));
    return true;
  }

  disconnect(permanent = true): void {
    if (permanent) this.stopped = true;
    this.clearTimer();
    const socket = this.socket;
    this.socket = null;
    this.held = [];
    this.replaying = false;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try { socket.close(); } catch {}
    }
    if (permanent) {
      this.chatId = null;
      this.state$.next('disconnected');
    }
  }

  private open(reconnecting: boolean): void {
    if (this.stopped || !this.chatId) return;
    this.disconnect(false);
    this.state$.next(reconnecting ? 'reconnecting' : 'connecting');
    const socket = new WebSocket(this.aiSession.buildChatWsUrl(this.chatId));
    this.socket = socket;

    this.zone.runOutsideAngular(() => {
      socket.onopen = () => this.zone.run(async () => {
        if (this.socket !== socket) return;
        this.reconnectAttempts = 0;
        this.replaying = true;
        try {
          await this.replay(socket);
        } finally {
          this.replaying = false;
          if (this.socket === socket) this.release();
          else this.held = [];
        }
        if (this.socket === socket) this.state$.next('connected');
      });
      socket.onmessage = (event) => this.zone.run(() => {
        if (this.socket !== socket) return;
        if (this.replaying) this.held.push(String(event.data));
        else this.events$.next(String(event.data));
      });
      socket.onerror = () => this.zone.run(() => {
        if (this.socket === socket) this.state$.next('offline');
      });
      socket.onclose = (event) => this.zone.run(() => {
        if (this.socket !== socket) return;
        this.socket = null;
        if (event.code === 4401 || event.code === 4403) {
          this.state$.next('unauthorized');
          this.stopped = true;
          return;
        }
        if (event.code === 4409) {
          this.state$.next('archived');
          this.stopped = true;
          return;
        }
        if (event.code === 4404 || event.code === 4400) {
          // The chat was deleted or is no longer this person's (4404),
          // or the relay closed over a message it could not read
          // (4400). Dialing again would be refused the same way, every
          // few seconds, for as long as the page stayed open.
          this.state$.next(event.code === 4404 ? 'missing' : 'refused');
          this.stopped = true;
          return;
        }
        if (event.code === 4001) {
          // A newer connection to this chat — another tab, another
          // window — took the relay. Reconnecting here would take it
          // back, and the two tabs would replace each other for ever,
          // each one hanging. This tab stands down until asked.
          this.state$.next('replaced');
          this.stopped = true;
          return;
        }
        this.scheduleReconnect();
      });
    });
  }

  /** Everything the log holds past the page's last sequence, a page at
   *  a time until the newest is reached. One answer is capped, and a
   *  long run can put more events than that between two connections:
   *  stopping at the first page would let the live frames that follow
   *  carry the sequence past everything in between, for good.
   *
   *  The log is a bounded tail. When its oldest event past the page's
   *  mark is not the very next one, events were pruned unread, and the
   *  page is told instead of being handed the remainder.
   *
   *  It replays for the socket it was started for: once that socket is
   *  replaced or closed, what it reads belongs to nobody. */
  private async replay(socket: WebSocket): Promise<void> {
    const chatId = this.chatId;
    let after = this.replayAfter();
    if (!chatId || after === null) return;
    for (let page = 0; page < ChatConnectionService.REPLAY_PAGES; page++) {
      const log = await this.aiSession.listEvents(chatId, after);
      if (this.socket !== socket) return;
      const events = log.events || [];
      if (!events.length) return;
      if (page === 0 && Number(events[0].seq) !== after + 1) {
        this.gap$.next();
        return;
      }
      for (const entry of events) {
        this.events$.next(JSON.stringify({
          endpoint: 'AI:Chat:Event',
          data: { ...(entry.event || {}), seq: entry.seq },
        }));
      }
      const last = Number(events[events.length - 1].seq);
      if (!(last > after) || last >= Number(log.latest_seq || 0)) return;
      after = last;
    }
  }

  /** What the socket said during replay, in the order it said it. */
  private release(): void {
    const held = this.held;
    this.held = [];
    for (const raw of held) this.events$.next(raw);
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer || !this.chatId) return;
    this.state$.next('reconnecting');
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts++, 8000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open(true);
    }, delay);
  }

  private clearTimer(): void {
    if (!this.reconnectTimer) return;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }
}
