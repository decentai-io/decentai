import { NgZone } from '@angular/core';
import { ChatConnectionService } from './chat-connection.service';

class FakeSocket {
  static readonly OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = FakeSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  sent: string[] = [];
  constructor(public url: string) { FakeSocket.instances.push(this); }
  send(value: string) { this.sent.push(value); }
  close() {}
}

describe('ChatConnectionService', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    originalWebSocket = window.WebSocket;
    FakeSocket.instances = [];
    (window as any).WebSocket = FakeSocket;
  });

  afterEach(() => { (window as any).WebSocket = originalWebSocket; });

  it('owns sending and ignores callbacks from a replaced socket', async () => {
    const ai = {
      buildChatWsUrl: () => 'ws://chat/1',
      listEvents: jasmine.createSpy('listEvents').and.resolveTo({
        events: [], latest_seq: 0,
      }),
    };
    const service = new ChatConnectionService(
      ai as any, new NgZone({ enableLongStackTrace: false }),
    );
    service.connect('chat_1', () => 0);
    const first = FakeSocket.instances[0];
    first.onopen?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(service.state$.value).toBe('connected');
    expect(service.send('AI:Chat:Input', { text: 'hello' })).toBeTrue();
    expect(first.sent[0]).toContain('AI:Chat:Input');

    const staleClose = first.onclose!;
    service.reconnectNow();
    expect(FakeSocket.instances.length).toBe(2);
    staleClose({ code: 1006 });
    expect(service.state$.value).toBe('connecting');

    FakeSocket.instances[1].onopen?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(service.state$.value).toBe('connected');
    service.disconnect();
  });

  it('stands down when a newer connection replaced it', async () => {
    // Two tabs on one chat: the relay closes the older socket with
    // 4001. Reconnecting would take the relay back and the tabs would
    // replace each other for ever, both hanging.
    const ai = {
      buildChatWsUrl: () => 'ws://chat/1',
      listEvents: jasmine.createSpy('listEvents').and.resolveTo({
        events: [], latest_seq: 0,
      }),
    };
    const service = new ChatConnectionService(
      ai as any, new NgZone({ enableLongStackTrace: false }),
    );
    service.connect('chat_1', () => 0);
    const socket = FakeSocket.instances[0];
    socket.onopen?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(service.state$.value).toBe('connected');

    socket.onclose?.({ code: 4001 });
    expect(service.state$.value).toBe('replaced');
    await new Promise((resolve) => setTimeout(resolve, 1100));
    expect(FakeSocket.instances.length).toBe(1);

    // Asked to, this tab takes the relay back.
    service.reconnectNow();
    expect(FakeSocket.instances.length).toBe(2);
    service.disconnect();
  });

  it('holds live frames until replay has been applied', async () => {
    // Replay is slow; the socket speaks first. What replay knew about
    // must reach the page before what only the socket saw.
    let finishReplay!: (value: any) => void;
    const ai = {
      buildChatWsUrl: () => 'ws://chat/1',
      listEvents: () => new Promise((resolve) => { finishReplay = resolve; }),
    };
    const service = new ChatConnectionService(
      ai as any, new NgZone({ enableLongStackTrace: false }),
    );
    const heard: number[] = [];
    service.events$.subscribe((raw) => heard.push(JSON.parse(raw).data.seq));

    service.connect('chat_1', () => 10);
    const socket = FakeSocket.instances[0];
    socket.onopen?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    socket.onmessage?.({ data: JSON.stringify({
      endpoint: 'AI:Chat:Event', data: { event: 'idle', seq: 12 } }) });
    expect(heard).toEqual([]);
    expect(service.state$.value).toBe('connecting');

    finishReplay({ events: [{ seq: 11, event: { event: 'working' } }], latest_seq: 11 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(heard).toEqual([11, 12]);
    expect(service.state$.value).toBe('connected');

    // Once connected, live frames pass straight through.
    socket.onmessage?.({ data: JSON.stringify({
      endpoint: 'AI:Chat:Event', data: { event: 'idle', seq: 13 } }) });
    expect(heard).toEqual([11, 12, 13]);
    service.disconnect();
  });

  it('replays page after page until it reaches the newest event', async () => {
    // One answer is capped. A long run puts more events than that
    // between two connections, and stopping at the first page would let
    // the live frames that follow carry the sequence past the rest.
    const pages: Record<number, any> = {
      10: { events: [{ seq: 11, event: { event: 'working' } },
                     { seq: 12, event: { event: 'idle' } }], latest_seq: 14 },
      12: { events: [{ seq: 13, event: { event: 'working' } },
                     { seq: 14, event: { event: 'idle' } }], latest_seq: 14 },
    };
    const listEvents = jasmine.createSpy('listEvents').and.callFake(
      async (_chat: string, after: number) => pages[after]);
    const service = new ChatConnectionService(
      { buildChatWsUrl: () => 'ws://chat/1', listEvents } as any,
      new NgZone({ enableLongStackTrace: false }),
    );
    const heard: number[] = [];
    service.events$.subscribe((raw) => heard.push(JSON.parse(raw).data.seq));

    service.connect('chat_1', () => 10);
    FakeSocket.instances[0].onopen?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(listEvents).toHaveBeenCalledTimes(2);
    expect(heard).toEqual([11, 12, 13, 14]);
    expect(service.state$.value).toBe('connected');
    service.disconnect();
  });

  it('says so, and replays nothing, when the log has moved past the page', async () => {
    // The backend keeps a bounded tail. Its oldest event past the
    // page's mark is not the next one: what lay between was pruned, and
    // the remainder applied over that hole would be a wrong chat.
    const service = new ChatConnectionService(
      { buildChatWsUrl: () => 'ws://chat/1',
        listEvents: async () => ({
          events: [{ seq: 40, event: { event: 'idle' } }], latest_seq: 40 }) } as any,
      new NgZone({ enableLongStackTrace: false }),
    );
    const heard: number[] = [];
    let gaps = 0;
    service.events$.subscribe((raw) => heard.push(JSON.parse(raw).data.seq));
    service.gap$.subscribe(() => gaps++);

    service.connect('chat_1', () => 10);
    FakeSocket.instances[0].onopen?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(gaps).toBe(1);
    expect(heard).toEqual([]);
    service.disconnect();
  });

  it('does not dial again for a chat that is gone', async () => {
    // 4404: deleted, or no longer this person's. Reconnecting would be
    // refused the same way every few seconds for as long as the page
    // stayed open.
    const service = new ChatConnectionService(
      { buildChatWsUrl: () => 'ws://chat/1',
        listEvents: async () => ({ events: [], latest_seq: 0 }) } as any,
      new NgZone({ enableLongStackTrace: false }),
    );
    service.connect('chat_1', () => 0);
    const socket = FakeSocket.instances[0];
    socket.onopen?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    socket.onclose?.({ code: 4404 });
    expect(service.state$.value).toBe('missing');
    await new Promise((resolve) => setTimeout(resolve, 1100));
    expect(FakeSocket.instances.length).toBe(1);
    service.disconnect();
  });
});
