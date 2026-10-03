import { SettingsComponent } from './settings.component';

describe('SettingsComponent', () => {
  function connection(name: string, overrides: any = {}) {
    return {
      resource_ref: `llm_${name}`,
      name,
      keys: { provider: 'anthropic', model: 'claude-sonnet-5', endpoint: 'https://api.anthropic.com' },
      is_default: false,
      ...overrides,
    } as any;
  }

  // A few entries in the shape Settings:Llm:providers serves.
  const catalog = [
    { id: 'anthropic', name: 'Anthropic', protocol: 'anthropic', endpoint: 'https://api.anthropic.com' },
    { id: 'openrouter', name: 'OpenRouter', protocol: 'openai', endpoint: 'https://openrouter.ai/api/v1' },
    { id: 'together', name: 'Together AI', protocol: 'openai', endpoint: 'https://api.together.xyz/v1' },
    { id: 'amazon-bedrock', name: 'Amazon Bedrock', protocol: 'bedrock', endpoint: 'https://bedrock-runtime.<aws-region>.amazonaws.com' },
    { id: 'openai_compatible', name: 'Custom / OpenAI-compatible', protocol: 'openai', endpoint: '' },
  ];

  function create(connections: any[] = [], calls: Record<string, any> = {}) {
    const component = new SettingsComponent(
      {
        list: async () => connections,
        providers: async () => catalog,
        create: async (draft: any) => {
          calls['created'] = draft;
          return { connection: connection(draft.name) };
        },
        update: async (id: string, draft: any) => {
          calls['updated'] = { id, draft };
          return { connection: connection(draft.name) };
        },
        setDefault: async (id: string) => {
          calls['defaulted'] = id;
          return { connection: connection('x', { is_default: true }) };
        },
        remove: async () => ({ deleted: true }),
      } as any,
      { get: async () => ({ user_id: 'me', groups: [] }),
        peers: async () => [] } as any,
      { can: () => true } as any,
      // Routing, speech, notifications and push: other tabs' services.
      {} as any,
      {} as any,
      {} as any,
      {} as any,
      // The route names the tab; the router is only navigated.
      { snapshot: { data: {} } } as any,
      { navigate: async () => true } as any,
    );
    component.connections = connections;
    component.providers = catalog as any;
    return component;
  }

  // ── The list ────────────────────────────────────────────────────────

  it('filters across name, provider and model', () => {
    const component = create([
      connection('Prod', { keys: { provider: 'anthropic',
        model: 'claude-sonnet-5', endpoint: 'https://api.anthropic.com' } }),
      connection('Cheap', { keys: { provider: 'openai',
        model: 'gpt-4o-mini', endpoint: 'https://api.anthropic.com' } }),
    ]);

    component.query = 'openai';
    expect(component.visible.map((c) => c.name)).toEqual(['Cheap']);
    component.query = 'sonnet';
    expect(component.visible.map((c) => c.name)).toEqual(['Prod']);
  });

  it('fills each provider endpoint and allows an explicit custom URL', () => {
    const component = create();
    component.startCreate();
    for (const provider of component.providers) {
      component.selectProvider(provider.id);
      expect(component.draft.endpoint).toBe(provider.endpoint);
    }
    component.draft.endpoint = 'https://my-gateway.example/v1';
    component.selectProvider('openrouter');
    expect(component.draft.endpoint).toBe('https://my-gateway.example/v1');
  });

  it('holds a connection back until the blanks in its endpoint are filled in', () => {
    const component = create();
    component.startCreate();
    component.draft.name = 'Bedrock';
    component.draft.model = 'us.anthropic.claude-sonnet-4-5-20250929-v1:0';
    component.draft.api_key = 'key';
    component.selectProvider('amazon-bedrock');
    expect(component.draft.endpoint).toContain('<aws-region>');
    expect(component.blocker).toContain('Fill in the endpoint');
    expect(component.endpointHelp).toContain('your region');

    component.draft.endpoint = 'https://bedrock-runtime.eu-west-1.amazonaws.com';
    expect(component.blocker).toBe('');
  });

  it('keeps saved endpoints when opening the editor', () => {
    const component = create();
    component.startEdit(connection('Custom', { keys: {
      provider: 'openai', model: 'deployment', endpoint: 'https://azure.example/openai/v1/',
    } }));
    expect(component.draft.endpoint).toBe('https://azure.example/openai/v1/');
  });

  // ── The editor ──────────────────────────────────────────────────────

  it('demands the key only for a NEW connection', () => {
    const component = create();
    component.startCreate();
    component.draft = { name: 'X', provider: 'anthropic',
                        model: 'claude-sonnet-5', endpoint: 'https://api.anthropic.com', api_key: '' };
    expect(component.blocker).toContain('API key');

    component.startEdit(connection('X'));
    // Blank means keep — demanding the key back would force retyping a
    // value nobody can read out.
    expect(component.blocker).toBe('');
  });

  it('blocks saving an existing connection with a blank endpoint', async () => {
    const calls: Record<string, any> = {};
    const component = create([], calls);
    component.startEdit(connection('X'));
    component.draft.endpoint = '   ';
    expect(component.blocker).toContain('Endpoint');
    await component.save();
    expect(calls['updated']).toBeUndefined();
  });

  it('never prefills the key when editing', () => {
    const component = create();
    component.startEdit(connection('X'));

    expect(component.draft.name).toBe('X');
    expect(component.draft.provider).toBe('anthropic');
    expect(component.draft.api_key).toBe('');
  });

  it('says what is missing before the request', () => {
    const component = create();
    component.startCreate();

    expect(component.blocker).toContain('name');
    component.draft.name = 'X';
    expect(component.blocker).toContain('provider');
    component.draft.provider = 'anthropic';
    expect(component.blocker).toContain('model');
    component.draft.model = 'claude-sonnet-5';
    expect(component.blocker).toContain('Endpoint');
    component.draft.endpoint = 'https://api.anthropic.com';
    expect(component.blocker).toContain('API key');
    component.draft.api_key = 'sk-x';
    expect(component.blocker).toBe('');
  });

  it('routes create and edit to different calls', async () => {
    const calls: Record<string, any> = {};
    const component = create([], calls);

    component.startCreate();
    component.draft = { name: 'New', provider: 'openai', model: 'gpt-4o',
                        endpoint: 'https://api.anthropic.com', api_key: 'sk-1' };
    await component.save();
    expect(calls['created'].name).toBe('New');

    component.startEdit(connection('Old'));
    component.draft.model = 'gpt-4o-mini';
    await component.save();
    expect(calls['updated'].id).toBe('llm_Old');
    expect(calls['updated'].draft.api_key).toBe('');
  });

  // ── The default ─────────────────────────────────────────────────────

  it('never re-sets the default that already is', async () => {
    const calls: Record<string, any> = {};
    const component = create([], calls);

    await component.makeDefault(connection('X', { is_default: true }));
    expect(calls['defaulted']).toBeUndefined();

    await component.makeDefault(connection('Y'));
    expect(calls['defaulted']).toBe('llm_Y');
  });

  // ── Removing ────────────────────────────────────────────────────────

  it('deletes through the dialog, never directly', async () => {
    const component = create([connection('X')]);

    const target = component.connections[0];
    component.requestDelete(target);
    expect(component.deleteTarget).toBe(target);

    await component.remove(target);
    expect(component.deleteTarget).toBeNull();
  });
});
