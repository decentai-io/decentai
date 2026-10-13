import { McpServerComponent } from './mcp-server.component';

describe('McpServerComponent', () => {
  function tool(id: string, more: any = {}) {
    return { id, name: id, description: `Does ${id}.`, inputs: {}, enabled: true, level: 3, ...more };
  }

  function server(tools: any[], keys: any = {}) {
    return {
      resource_ref: 'mcp_1',
      keys: { name: 'Wiki', url: 'https://wiki.test/mcp', host: 'wiki.test', enabled: true, server: '',
              tool_count: tools.length, tools_on: tools.length, resources: false, credential: 'token', ...keys },
      values: { tools, resources: false, has_credential: true },
    } as any;
  }

  async function create(stored: any, service: any = {}, confirm: any = {}, blocked = false) {
    const went: any[] = [];
    const component = new McpServerComponent(
      {
        get: async () => stored,
        list: async () => ({ servers: [], blocked }),
        update: async () => ({ resource: stored }),
        refresh: async () => ({ resource: stored }),
        remove: async () => ({}),
        ...service,
      } as any,
      { snapshot: { paramMap: { get: () => 'mcp_1' }, queryParamMap: { get: () => null } } } as any,
      { navigate: async (...args: any[]) => { went.push(args); return true; } } as any,
      { open: () => ({ afterClosed: () => ({ subscribe() {} }), close() {} }) } as any,
      { ask: async () => true, ...confirm } as any,
    );
    await component.ngOnInit();
    return { component, went };
  }

  it('says a server that could not be opened could not be, and shows no tools', async () => {
    const { component } = await create(null);
    expect(component.problem).toContain('could not be opened');
    expect(component.tools).toEqual([]);
  });

  it('saves one tool on its own row, and says how it went there', async () => {
    const before = server([tool('search'), tool('wipe')]);
    const after = server([tool('search', { level: 0 }), tool('wipe')]);
    let sent: any;
    const { component } = await create(before, {
      update: async (_ref: string, fields: any) => { sent = fields; return { resource: after }; },
    });

    await component.setTool(component.tools[0], { level: 0 });

    expect(sent).toEqual({ tools: [{ id: 'search', level: 0 }] });
    expect(component.tools[0].level).toBe(0);
    expect(component.saveOf(component.tools[0])).toBe('Saved');
    // The other row said nothing, and the page was not busy for it.
    expect(component.saveOf(component.tools[1])).toBe('');
    expect(component.busy).toBe('');
  });

  it('leaves a tool as it was when its save is refused, with the reason on its row', async () => {
    const { component } = await create(server([tool('search')]), {
      update: async () => ({ error: 'A level is 0, 1, 2 or 3.' }),
    });

    await component.setTool(component.tools[0], { level: 7 });

    expect(component.tools[0].level).toBe(3);
    expect(component.saveOf(component.tools[0])).toBe('A level is 0, 1, 2 or 3.');
    expect(component.saveFailed(component.tools[0])).toBeTrue();
  });

  it('switches every tool in one save', async () => {
    let sent: any;
    const { component } = await create(server([tool('a'), tool('b')]), {
      update: async (_ref: string, fields: any) => {
        sent = fields;
        return { resource: server([tool('a', { enabled: false }), tool('b', { enabled: false })]) };
      },
    });

    await component.setAll(false);

    expect(sent.tools).toEqual([{ id: 'a', enabled: false }, { id: 'b', enabled: false }]);
    expect(component.onCount).toBe(0);
  });

  it('keeps the tools when a read fails, and says the server could not be reached', async () => {
    const failing = { ...server([]), values: undefined };
    failing.keys.last_read_error = 'The server answered 503.';
    const { component } = await create(server([tool('search')]), {
      refresh: async () => ({ error: 'The server answered 503.', resource: failing }),
    });

    await component.readAgain();

    expect(component.error).toBe('The server answered 503.');
    expect(component.tools.length).toBe(1);
    expect(component.status.word).toBe('Cannot be reached');
  });

  it('says how many tools came back switched off after a read', async () => {
    const { component } = await create(server([tool('search')]), {
      refresh: async () => ({ resource: server([tool('search'), tool('wipe', { enabled: false, changed: 'new' })]) }),
    });

    await component.readAgain();

    expect(component.notice).toContain('1 tool is new or changed');
    expect(component.changeWord(component.tools[1])).toBe('new');
  });

  it('reads what a tool takes from its schema', async () => {
    const { component } = await create(server([tool('search', { inputs: {
      properties: { query: { type: 'string', description: 'What to look for.' }, limit: { type: 'integer' } },
      required: ['query'],
    } })]));

    expect(component.inputsOf(component.tools[0])).toEqual([
      { name: 'query', type: 'string', required: true, about: 'What to look for.' },
      { name: 'limit', type: 'integer', required: false, about: '' },
    ]);
  });

  it('changes nothing while the deployment has the feature off', async () => {
    let called = false;
    const { component } = await create(server([tool('search')]),
      { update: async () => { called = true; return {}; } }, {}, true);

    await component.setTool(component.tools[0], { enabled: false });
    await component.setAll(false);

    expect(called).toBeFalse();
    expect(component.status.word).toBe('Off for this deployment');
  });

  it('goes back to the list after a removal that was agreed to', async () => {
    let answer = false;
    const { component, went } = await create(server([tool('search')]), {}, { ask: async () => answer });

    await component.askRemove();
    expect(went.length).toBe(0);

    answer = true;
    await component.askRemove();
    expect(went[0][0]).toEqual(['/data/mcp']);
    expect(went[0][1].state.mcpNotice).toBe('Wiki removed.');
  });
});
