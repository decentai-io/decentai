import { McpComponent } from './mcp.component';

describe('McpComponent', () => {
  function server(ref: string, keys: any = {}, more: any = {}) {
    return {
      resource_ref: ref,
      keys: { name: ref, url: `https://${ref}.test/mcp`, host: `${ref}.test`, enabled: true, server: '',
              tool_count: 2, tools_on: 1, resources: false, ...keys },
      ...more,
    } as any;
  }

  function create(listed: any = { servers: [], blocked: false }, service: any = {}, confirm: any = {}) {
    return new McpComponent(
      { list: async () => listed, refresh: async () => ({}), remove: async () => ({}), ...service } as any,
      { can: () => true } as any,
      { events: { subscribe: () => ({ unsubscribe() {} }) }, navigate: async () => true } as any,
      { snapshot: { firstChild: null } } as any,
      { open: () => ({ afterClosed: () => ({ subscribe() {} }), close() {} }) } as any,
      { ask: async () => true, ...confirm } as any,
    );
  }

  it('says how a server stands from one place: on, off, unreachable, unreadable', async () => {
    const component = create();
    await component.ngOnInit();

    expect(component.status(server('a')).word).toBe('On');
    expect(component.status(server('a', { enabled: false })).word).toBe('Off');
    const down = component.status(server('a', { last_read_error: 'The server refused the credential.' }));
    expect([down.word, down.tone, down.why]).toEqual(['Cannot be reached', 'danger', 'The server refused the credential.']);
    expect(component.status(server('a', {}, { unreadable: true })).tone).toBe('warn');
  });

  it('says the deployment switched the feature off before anything else about a server', async () => {
    const component = create({ servers: [server('a')], blocked: true });
    await component.ngOnInit();

    expect(component.status(server('a', { last_read_error: 'down' })).word).toBe('Off for this deployment');
  });

  it('says what a server is reached with, and nothing for one added before rows carried it', async () => {
    const component = create();
    expect(component.credentialWord(server('a', { credential: 'none' }))).toBe('no credential');
    expect(component.credentialWord(server('a', { credential: 'header X-Api-Key' }))).toBe('header X-Api-Key');
    expect(component.credentialWord(server('a'))).toBe('');
  });

  it('does not show a list that could not be read as an empty one', async () => {
    const component = create({ servers: [], blocked: false, error: 'The servers could not be read.' });
    await component.ngOnInit();
    expect(component.loadProblem).toBe('The servers could not be read.');
  });

  it('keeps a server whose read failed, with what its row now says', async () => {
    const failing = server('a', { last_read_error: 'The server answered 503.' });
    const component = create({ servers: [server('a')], blocked: false },
      { refresh: async () => ({ error: 'The server answered 503.', resource: failing }) });
    await component.ngOnInit();

    await component.readAgain(component.servers[0]);

    expect(component.servers[0].keys.last_read_error).toBe('The server answered 503.');
    expect(component.error).toContain('503');
  });

  it('removes a server only once that was agreed to', async () => {
    let removed = '';
    let answer = false;
    const component = create({ servers: [server('a')], blocked: false },
      { remove: async (ref: string) => { removed = ref; return {}; } }, { ask: async () => answer });
    await component.ngOnInit();

    await component.askRemove(component.servers[0]);
    expect(removed).toBe('');
    answer = true;
    await component.askRemove(component.servers[0]);
    expect(removed).toBe('a');
    expect(component.servers).toEqual([]);
  });
});
