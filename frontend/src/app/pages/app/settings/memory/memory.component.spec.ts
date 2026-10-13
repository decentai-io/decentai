import { MemoryComponent } from './memory.component';

describe('MemoryComponent', () => {
  /** `sure` is what the person answers when asked before forgetting. */
  function createComponent(page: any = { memories: [], limit: 50 }, service: any = {}, sure = true) {
    return new MemoryComponent(
      {
        list: async () => page,
        update: async () => ({ data: { memory: {} } }),
        remove: async () => ({}),
        ...service,
      } as any,
      { can: () => true } as any,
      { ask: async () => sure } as any,
    );
  }

  function filled(count: number) {
    return Array.from({ length: count }, (_, index) => ({
      memory_id: `mem_${index}`,
      text: `Fact ${index}`,
      source_chat_id: 'chat_1',
    }));
  }

  it('reports how much room is left before the oldest is dropped', async () => {
    const component = createComponent({ memories: filled(12), limit: 50 });
    await component.ngOnInit();

    expect(component.limit).toBe(50);
    expect(component.remaining).toBe(38);
    expect(component.nearLimit).toBeFalse();
    expect(component.atLimit).toBeFalse();
  });

  it('warns as the cap comes into range, and once it is reached', async () => {
    const near = createComponent({ memories: filled(46), limit: 50 });
    await near.ngOnInit();
    expect(near.remaining).toBe(4);
    expect(near.nearLimit).toBeTrue();
    expect(near.atLimit).toBeFalse();

    const full = createComponent({ memories: filled(50), limit: 50 });
    await full.ngOnInit();
    expect(full.remaining).toBe(0);
    expect(full.atLimit).toBeTrue();
  });

  it('says nothing about a cap the server did not report', async () => {
    const component = createComponent({ memories: filled(3), limit: 0 });
    await component.ngOnInit();

    expect(component.nearLimit).toBeFalse();
    expect(component.atLimit).toBeFalse();
  });

  it('shows the newest first, whatever order the server sent', async () => {
    const component = createComponent({
      memories: [
        { memory_id: 'old', text: 'Learned first.', created_at: '2026-08-01T09:00:00Z' },
        { memory_id: 'new', text: 'Learned last.', created_at: '2026-08-20T09:00:00Z' },
      ],
      limit: 50,
    });
    await component.ngOnInit();

    expect(component.filteredMemories.map((m) => m.memory_id))
      .toEqual(['new', 'old']);
  });

  it('names the origin in the order that matters: whose words, then when', async () => {
    const component = createComponent();

    expect(component.originLabel({
      text: '', created_at: '2026-08-18T09:00:00Z', source_chat_id: 'chat_1',
    } as any)).toMatch(/^Saved .*18/);

    expect(component.originLabel({
      text: '', corrected: true, updated_at: '2026-08-20T09:00:00Z',
      source_chat_id: 'chat_1',
    } as any)).toContain('Corrected by you');

    expect(component.originLabel({
      text: '', authored: true, created_at: '2026-08-20T09:00:00Z',
    } as any)).toContain('Added by you');

    expect(component.originLabel({
      text: '', authored: true, corrected: true,
      created_at: '2026-08-19T09:00:00Z', updated_at: '2026-08-20T09:00:00Z',
    } as any)).toContain('Added by you, edited');
  });

  it('writes a memory of your own and reloads the record', async () => {
    let created = '';
    const component = createComponent({ memories: [], limit: 50 }, {
      create: async (text: string) => {
        created = text;
        return { data: { memory: { memory_id: 'mem_1', text } } };
      },
    });
    await component.ngOnInit();

    expect(component.canSubmitNew).toBeFalse();

    component.newText = '  I prefer concise status updates.  ';
    expect(component.canSubmitNew).toBeTrue();

    await component.addMemory();
    expect(created).toBe('I prefer concise status updates.');
    expect(component.newText).toBe('');
  });

  it('keeps what was written, and says why, when it is refused', async () => {
    const component = createComponent({ memories: [], limit: 50 }, {
      create: async () => ({ error: 'Another memory already says exactly that.' }),
    });
    await component.ngOnInit();
    component.newText = 'Works in Abu Dhabi.';

    await component.addMemory();

    expect(component.addError).toBe('Another memory already says exactly that.');
    expect(component.newText).toBe('Works in Abu Dhabi.');
  });

  it('asks before forgetting instead of going straight through', async () => {
    const memory = { memory_id: 'mem_1', text: 'Delete me.' } as any;
    const removed: string[] = [];
    const remove = async (id: string) => {
      removed.push(id);
      return {};
    };

    const kept = createComponent({ memories: [memory], limit: 50 }, { remove }, false);
    await kept.ngOnInit();
    await kept.forget(memory);
    expect(removed).toEqual([]);

    const gone = createComponent({ memories: [memory], limit: 50 }, { remove }, true);
    await gone.ngOnInit();
    await gone.forget(memory);
    expect(removed).toEqual(['mem_1']);
  });

  it('forgets every memory after one question', async () => {
    const removed: string[] = [];
    const component = createComponent({ memories: filled(3), limit: 50 }, {
      remove: async (id: string) => {
        removed.push(id);
        return {};
      },
    });
    await component.ngOnInit();

    await component.forgetAll();

    expect(removed).toEqual(['mem_0', 'mem_1', 'mem_2']);
  });

  it('says so when the memories could not be read, and stops loading', async () => {
    const component = createComponent(undefined, {
      list: async () => { throw new Error('offline'); },
    });

    await component.ngOnInit();

    expect(component.loading).toBeFalse();
    expect(component.loadError).not.toBe('');
  });

  it('marks a corrected memory so the row stops crediting the chat', async () => {
    const memory = {
      memory_id: 'mem_1', text: 'Prefers long reports.', source_chat_id: 'chat_1',
    } as any;
    const component = createComponent({ memories: [memory], limit: 50 }, {
      update: async () => ({
        data: {
          memory: {
            memory_id: 'mem_1',
            text: 'Prefers one-page reports.',
            corrected: true,
            updated_at: '2026-08-20T10:00:00Z',
          },
        },
      }),
    });
    await component.ngOnInit();

    component.startEdit(memory);
    component.editText = 'Prefers one-page reports.';
    await component.saveEdit(memory);

    expect(memory.text).toBe('Prefers one-page reports.');
    expect(memory.corrected).toBeTrue();
    expect(memory.updated_at).toBe('2026-08-20T10:00:00Z');
    expect(component.editingId).toBeNull();
  });

  it('surfaces a refused correction instead of showing it as saved', async () => {
    const memory = {
      memory_id: 'mem_1', text: 'Prefers long reports.', source_chat_id: 'chat_1',
    } as any;
    const component = createComponent({ memories: [memory], limit: 50 }, {
      update: async () => ({ error: 'Another memory already says exactly that.' }),
    });
    await component.ngOnInit();

    component.startEdit(memory);
    component.editText = 'Works in Abu Dhabi.';
    await component.saveEdit(memory);

    expect(component.editError).toBe('Another memory already says exactly that.');
    expect(memory.text).toBe('Prefers long reports.');
    expect(memory.corrected).toBeUndefined();
    // The editor stays open, holding the text that was refused.
    expect(component.editingId).toBe('mem_1');
    expect(component.editText).toBe('Works in Abu Dhabi.');
  });
});
