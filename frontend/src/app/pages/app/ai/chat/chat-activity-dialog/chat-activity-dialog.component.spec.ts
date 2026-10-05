import { ChatActivityDialogComponent } from './chat-activity-dialog.component';

describe('ChatActivityDialogComponent, the assistant', () => {
  function create(transcript: any, asked: string[] = []) {
    return new ChatActivityDialogComponent(
      { chat_id: 'chat_1', title: 'Notes' },
      { close: () => undefined } as any,
      {
        listAudit: async () => [],
        transcript: async (chatId: string) => {
          asked.push(chatId);
          return transcript;
        },
      } as any,
    );
  }

  const entry = (role: string, content: string, more: any = {}) => ({
    index: 0, role, content, cut: false, images: 0, ...more,
  });

  it('reads what the assistant was shown the first time it is asked for, and once', async () => {
    const asked: string[] = [];
    const component = create({ entries: [], summary: '', opened: [] }, asked);

    expect(asked).toEqual([]);
    await component.show('assistant');
    await component.show('record');
    await component.show('assistant');

    expect(asked).toEqual(['chat_1']);
    expect(component.view).toBe('assistant');
  });

  it('says an action by its kind and what it was aimed at', () => {
    const component = create(null);
    const invoke = entry('assistant', JSON.stringify({
      action: 'invoke', function: 'agt_a.notes.save', inputs: { title: 'Rent' },
    }));
    const say = entry('assistant', JSON.stringify({ action: 'say', text: 'Saved\nyour note.' }));
    const finish = entry('assistant', JSON.stringify({ action: 'finish' }));

    expect(component.turnSummary(invoke)).toBe('invoke: agt_a.notes.save');
    expect(component.turnSummary(say)).toBe('say: Saved your note.');
    expect(component.turnSummary(finish)).toBe('finish');
    expect(component.turnChip(invoke)).toBe('decided');
    expect(component.turnText(invoke)).toContain('"title": "Rent"');
  });

  it('says what the model was told by its first line', () => {
    const component = create(null);
    const told = entry('user', '\n[10:00] save a note about rent\nmore');

    expect(component.turnChip(told)).toBe('told');
    expect(component.turnSummary(told)).toBe('[10:00] save a note about rent');
    expect(component.turnText(told)).toBe(told.content);
  });

  it('names the instructions without quoting them in the row', () => {
    const component = create(null);
    const instructions = entry('system', 'You are the assistant of…');

    expect(component.turnChip(instructions)).toBe('instructions');
    expect(component.turnSummary(instructions)).toBe('What the assistant is and how it works');
  });

  it('shows a reply that is not an action as the words it was', () => {
    const component = create(null);
    const prose = entry('assistant', 'I think the answer is 4.');

    expect(component.turnSummary(prose)).toBe('I think the answer is 4.');
    expect(component.turnText(prose)).toBe('I think the answer is 4.');
  });

  it('opens and closes an entry by where it is in the order', () => {
    const component = create(null);
    const third = entry('user', 'x', { index: 2 });

    component.toggleTurn(third);
    expect(component.isTurnOpen(third)).toBeTrue();
    expect(component.isTurnOpen(entry('user', 'y', { index: 3 }))).toBeFalse();
    component.toggleTurn(third);
    expect(component.isTurnOpen(third)).toBeFalse();
  });
});
