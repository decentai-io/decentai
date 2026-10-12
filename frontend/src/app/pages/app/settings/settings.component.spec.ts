import { SettingsComponent } from './settings.component';

describe('SettingsComponent (Model providers)', () => {
  function connection(name: string) {
    return { resource_ref: `llm_${name}`, name, owner: { groups: [], users: ['me'] }, keys: {} } as any;
  }

  it('reads the connections and reads them again when one was changed', async () => {
    const connections = [connection('Anthropic')];
    const component = new SettingsComponent(
      { list: async () => connections.slice(), providers: async () => [] } as any,
      { get: async () => ({ user_id: 'me', groups: [] }), peers: async () => [] } as any,
      { can: () => false } as any,
    );
    await component.ngOnInit();
    expect(component.managed.map((c) => c.name)).toEqual(['Anthropic']);

    connections.push(connection('OpenAI'));
    await component.reload();
    expect(component.managed.map((c) => c.name)).toEqual(['Anthropic', 'OpenAI']);
  });

  it('shows every connection of the organization to whoever may manage any', async () => {
    const asked: boolean[] = [];
    const component = new SettingsComponent(
      { list: async (every = false) => { asked.push(every); return []; }, providers: async () => [] } as any,
      { get: async () => null, peers: async () => [] } as any,
      { can: (action: string) => action === 'settings:llm:manage_any' } as any,
    );

    await component.ngOnInit();

    expect(asked).toEqual([false, true]);
  });
});
