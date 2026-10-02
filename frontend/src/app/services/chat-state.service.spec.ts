import { ChatStateService } from './chat-state.service';
import { ChatSnapshot, toApprovalCard } from 'src/app/models/chat-protocol';

describe('ChatStateService', () => {
  function snapshot(): ChatSnapshot {
    return {
      protocol_version: 2,
      chat: { chat_id: 'chat_1' },
      messages: { messages: [], total: 0, next_before: null, has_more: false },
      approvals: [],
      latest_event_seq: 4,
    };
  }

  it('loads one authoritative snapshot and rejects stale replay', () => {
    const state = new ChatStateService();
    state.load(snapshot(), []);

    expect(state.value.loading).toBeFalse();
    expect(state.value.latestEventSeq).toBe(4);
    expect(state.acceptSequence(4)).toBeFalse();
    expect(state.acceptSequence(5)).toBeTrue();
    expect(state.value.latestEventSeq).toBe(5);
  });

  it('is working from a submission until the door says idle', () => {
    const state = new ChatStateService();
    state.startSubmission('client_1');
    expect(state.value.working).toBeTrue();

    state.setWorking(true);
    state.addProgress('Calling notebook.note.save');
    expect(state.value.liveText).toBe('Calling notebook.note.save');

    state.setWorking(false);
    expect(state.value.working).toBeFalse();
    expect(state.value.liveText).toBeNull();
  });

  it('puts the words up at once, under the id the page gave them', () => {
    /** The server is the author of record, but nobody should watch
     *  their sentence vanish into a composer and wait on a round trip —
     *  one that may be installing an agent — to learn it was heard. */
    const state = new ChatStateService();
    state.startSubmission('client_1', 'is friday free?');

    expect(state.value.messages.length).toBe(1);
    expect(state.value.turns.length).toBe(1);
    expect(state.value.turns[0].userMessage?.client_message_id).toBe('client_1');
    expect((state.value.messages[0].parts[0] as any).text).toBe('is friday free?');
    expect(state.value.working).toBeTrue();
  });

  it('takes the words back when the send never left', () => {
    /** Leaving them on screen beside an error saying they were not sent
     *  is worse than never having shown them. */
    const state = new ChatStateService();
    state.startSubmission('client_1', 'is friday free?');
    state.dropSubmission('client_1');

    expect(state.value.messages).toEqual([]);
    expect(state.value.turns).toEqual([]);
  });

  it('names only the submission when there are no words to show', () => {
    /** A caller that gives just the id — a resend, a test — gets the
     *  old behaviour exactly, and no empty bubble. */
    const state = new ChatStateService();
    state.startSubmission('client_1');

    expect(state.value.messages).toEqual([]);
    expect(state.value.working).toBeTrue();
  });

  it('learns from the hello that a turn is already under way', () => {
    /** A chat opened in the middle of a turn never heard its `working`:
     *  the door's present tense is where the page finds out. */
    const state = new ChatStateService();
    state.load(snapshot(), []);
    expect(state.value.working).toBeFalse();

    state.hello([], [], [], true);
    expect(state.value.working).toBeTrue();
    expect(state.value.liveText).toBe('Thinking…');

    state.hello([], [], [], false);
    expect(state.value.working).toBeFalse();
    expect(state.value.liveText).toBeNull();
  });

  it('holds a sleep until its time, or until a stop ends it', () => {
    jasmine.clock().install();
    jasmine.clock().mockDate(new Date(1_000_000));
    const state = new ChatStateService();

    state.setSleeping(1000 + 600, 'look again');
    expect(state.value.sleeping).toEqual({ until: 1_600_000, why: 'look again' });
    jasmine.clock().tick(600_001);
    expect(state.value.sleeping).toBeNull();

    state.setSleeping(1000 + 1200 + 600, 'again');
    state.setSleeping(null);
    expect(state.value.sleeping).toBeNull();

    // A time already past is no sleep.
    state.setSleeping(1);
    expect(state.value.sleeping).toBeNull();
    jasmine.clock().uninstall();
  });

  it('keeps what it believed when the hello does not say', () => {
    const state = new ChatStateService();
    state.startSubmission('client_1');

    state.hello([], [], []);
    expect(state.value.working).toBeTrue();
  });

  it('keeps a failure once and stops the indicator', () => {
    const state = new ChatStateService();
    state.startSubmission('client_1');
    state.fail('runtime_error', 'Could not finish.', true);
    state.fail('runtime_error', 'Could not finish.', true);

    expect(state.value.working).toBeFalse();
    expect(state.value.errors.length).toBe(1);
    expect(state.value.errors[0].retryable).toBeTrue();
  });

  it('gathers a call under its agent: start, own line, finish', () => {
    const state = new ChatStateService();
    const source = { kind: 'agent' as const, agent: 'agt_1', agent_name: 'Notebook',
      function: 'agt_1.note.save', call_id: 'c_1' };
    state.addActivity({ kind: 'call_started', text: 'Notebook · Save Note', source });
    state.addActivity({ kind: 'agent_progress', text: "Saving note in 'work'", source });
    state.addActivity({ kind: 'call_finished', text: 'Notebook · Save Note', source,
      status: 'success', duration_ms: 1800 });

    expect(state.value.activity.length).toBe(1);
    const [entry] = state.value.activity;
    expect(entry.agent).toBe('Notebook');
    expect(entry.detail).toBe("Saving note in 'work'");
    expect(entry.status).toBe('success');
    expect(entry.durationMs).toBe(1800);
  });

  it('groups replies under the message they answer, cards in no turn', () => {
    const state = new ChatStateService();
    const value = snapshot();
    value.messages.messages = [
      { message_id: 'u1', chat_id: 'chat_1', actor: 'user', sequence: 1,
        parts: [{ type: 'markdown', content: 'Hello' }] },
      { message_id: 'a1', chat_id: 'chat_1', actor: 'ai', sequence: 2,
        parts: [{ type: 'markdown', content: 'Hi' }] },
      { message_id: 'u2', chat_id: 'chat_1', actor: 'user', sequence: 3,
        parts: [{ type: 'markdown', content: 'Push it' }] },
    ];
    const card = toApprovalCard({
      approval_id: 'apr_1', status: 'pending',
      request: { function: 'notebook.sync.push', permission_level: 3 },
    });
    state.load(value, [card]);

    expect(state.value.turns.length).toBe(2);
    expect(state.value.turns[0].assistantMessages[0].message_id).toBe('a1');
    // The card waits above the composer, not inside a turn.
    expect(state.value.approvals[0].action.function).toBe('notebook.sync.push');
  });

  it('keeps what the assistant said before anyone spoke, as its own turn', () => {
    const state = new ChatStateService();
    const value = snapshot();
    value.messages.messages = [
      { message_id: 'a0', chat_id: 'chat_1', actor: 'ai', sequence: 1,
        parts: [{ type: 'markdown', content: 'Your 9am check ran.' }] },
      { message_id: 'a1', chat_id: 'chat_1', actor: 'ai', sequence: 2,
        parts: [{ type: 'markdown', content: 'Nothing changed.' }] },
      { message_id: 'u1', chat_id: 'chat_1', actor: 'user', sequence: 3,
        parts: [{ type: 'markdown', content: 'Thanks' }] },
      { message_id: 'a2', chat_id: 'chat_1', actor: 'ai', sequence: 4,
        parts: [{ type: 'markdown', content: 'Any time.' }] },
    ];
    state.load(value, []);

    expect(state.value.turns.length).toBe(2);
    expect(state.value.turns[0].userMessage).toBeNull();
    expect(state.value.turns[0].assistantMessages.map((m) => m.message_id))
      .toEqual(['a0', 'a1']);
    expect(state.value.turns[1].userMessage?.message_id).toBe('u1');
    expect(state.value.turns[1].assistantMessages[0].message_id).toBe('a2');
  });

  it('puts waiting cards on a chat the assistant alone has spoken in', () => {
    const state = new ChatStateService();
    const value = snapshot();
    value.messages.messages = [
      { message_id: 'a0', chat_id: 'chat_1', actor: 'ai', sequence: 1,
        parts: [{ type: 'markdown', content: 'I need to push this.' }] },
    ];
    const card = toApprovalCard({
      approval_id: 'apr_1', status: 'pending',
      request: { function: 'notebook.sync.push', permission_level: 3 },
    });
    state.load(value, [card]);

    expect(state.value.turns.length).toBe(1);
    expect(state.value.approvals[0].approval_id).toBe('apr_1');
  });
});
