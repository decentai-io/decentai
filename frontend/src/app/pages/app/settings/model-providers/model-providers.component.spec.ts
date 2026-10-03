import { ModelProvidersComponent } from './model-providers.component';

describe('ModelProvidersComponent', () => {
  function connection(name: string, overrides: any = {}) {
    return {
      resource_ref: `llm_${name}`,
      name,
      created_by: 'me',
      owner: { groups: [], users: ['me'] },
      keys: { provider: 'anthropic', model: 'claude-sonnet-5', endpoint: 'https://api.anthropic.com' },
      is_default: false,
      ...overrides,
    } as any;
  }

  // A few entries in the shape Settings:Llm:providers serves.
  const catalog = [
    { id: 'anthropic', name: 'Anthropic', protocol: 'anthropic', endpoint: 'https://api.anthropic.com', popular: 1 },
    { id: 'openrouter', name: 'OpenRouter', protocol: 'openai', endpoint: 'https://openrouter.ai/api/v1', popular: 3 },
    { id: 'together', name: 'Together AI', protocol: 'openai', endpoint: 'https://api.together.xyz/v1' },
    { id: 'amazon-bedrock', name: 'Amazon Bedrock', protocol: 'bedrock', endpoint: 'https://bedrock-runtime.<aws-region>.amazonaws.com', popular: 2 },
    { id: 'openai_compatible', name: 'Custom / OpenAI-compatible', protocol: 'openai', endpoint: '' },
  ] as any[];
  const provider = (id: string) => catalog.find((entry) => entry.id === id);

  const served: Record<string, any[]> = {
    anthropic: [
      { id: 'claude-opus-5-5', name: 'Claude Opus 5.5', kind: 'chat' },
      { id: 'claude-sonnet-5', name: 'Claude Sonnet 5', kind: 'chat' },
    ],
    'amazon-bedrock': [
      { id: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0', name: 'Claude Sonnet 4.5 (US)', kind: 'chat' },
    ],
  };

  function create(connections: any[] = [], calls: Record<string, any> = {},
                  answer: (draft: any) => any = () => ({}), isDesktop = false) {
    const component = new ModelProvidersComponent(
      {
        catalogModels: async (id: string) => served[id] ?? [],
        create: async (draft: any) => {
          (calls['created'] ??= []).push(draft);
          return { connection: connection(draft.name), ...answer(draft) };
        },
        update: async (id: string, draft: any) => {
          calls['updated'] = { id, draft };
          return { connection: connection(draft.name), ...answer(draft) };
        },
        setDefault: async (id: string) => {
          calls['defaulted'] = id;
          return { connection: connection('x', { is_default: true }) };
        },
        remove: async () => ({ deleted: true }),
      } as any,
      { can: () => true, isDesktop } as any,
    );
    component.connections = connections;
    component.providers = catalog;
    component.profile = { user_id: 'me', groups: [] } as any;
    component.changed.subscribe(() => { calls['changed'] = (calls['changed'] ?? 0) + 1; });
    return component;
  }

  /** Let the models a provider serves arrive. */
  const settle = () => new Promise((resolve) => setTimeout(resolve));

  // ── The list ────────────────────────────────────────────────────────

  it('filters across the name and the provider', () => {
    const component = create([
      connection('Work'),
      connection('Router', { keys: { provider: 'openrouter', model: 'm',
        endpoint: 'https://openrouter.ai/api/v1' } }),
    ]);
    component.query = 'openrouter';
    expect(component.visible.map((c) => c.name)).toEqual(['Router']);
    component.query = 'anthropic';
    expect(component.visible.map((c) => c.name)).toEqual(['Work']);
  });

  it('says a row’s provider and the model it starts with, and an address only when it is the person’s own', () => {
    const component = create();
    expect(component.summary(connection('Anthropic'))).toBe('starts with claude-sonnet-5');
    expect(component.summary(connection('Work'))).toBe('Anthropic · starts with claude-sonnet-5');
    expect(component.summary(connection('Home', { keys: {
      provider: 'openai_compatible', model: 'llama3.3', endpoint: 'http://host.docker.internal:11434/v1',
    } }))).toBe('Custom / OpenAI-compatible · http://host.docker.internal:11434/v1 · starts with llama3.3');
  });

  // ── Choosing a provider ─────────────────────────────────────────────

  it('offers the popular few first, the rest behind a search, and a server of one’s own apart', () => {
    const component = create();
    component.startCreate();
    expect(component.choosing).toBeTrue();
    expect(component.offered.map((p) => p.id)).toEqual(['anthropic', 'amazon-bedrock', 'openrouter']);
    expect(component.others).toBe(1);
    expect(component.custom?.id).toBe('openai_compatible');

    component.providerQuery = 'together';
    expect(component.offered.map((p) => p.id)).toEqual(['together']);
    component.providerQuery = 'nobody';
    expect(component.offered).toEqual([]);
  });

  it('asks only for the key once a provider is chosen: the address, the model and the name are filled in', async () => {
    const component = create();
    component.startCreate();
    component.selectProvider(provider('anthropic'));
    await settle();

    expect(component.choosing).toBeFalse();
    expect(component.draft.endpoint).toBe('https://api.anthropic.com');
    expect(component.draft.name).toBe('Anthropic');
    // The newest the catalog knows is what it starts with.
    expect(component.draft.model).toBe('claude-opus-5-5');
    expect(component.blocker).toBe('Paste the API key.');
    component.setKey('sk-x');
    expect(component.blocker).toBe('');
  });

  it('names a second connection to the same provider apart from the first', () => {
    const component = create([connection('Anthropic')]);
    component.startCreate();
    component.selectProvider(provider('anthropic'));
    expect(component.draft.name).toBe('Anthropic 2');
  });

  it('asks for a region by name instead of an address to edit', async () => {
    const component = create();
    component.startCreate();
    component.selectProvider(provider('amazon-bedrock'));
    await settle();

    // The usual region is already in; the address is made from it.
    expect(component.blanks).toEqual(['aws-region']);
    expect(component.blankLabel('aws-region')).toBe('AWS region');
    expect(component.draft.endpoint).toBe('https://bedrock-runtime.us-east-1.amazonaws.com');
    expect(component.endpointIsYours).toBeFalse();

    component.setBlank('aws-region', 'eu-west-1');
    expect(component.draft.endpoint).toBe('https://bedrock-runtime.eu-west-1.amazonaws.com');

    component.setBlank('aws-region', ' ');
    component.setKey('key');
    expect(component.blocker).toBe('Enter your AWS region.');
  });

  it('asks a server of one’s own for its address and a model by name', async () => {
    const component = create();
    component.startCreate();
    component.selectProvider(provider('openai_compatible'));
    await settle();

    expect(component.endpointIsYours).toBeTrue();
    expect(component.typingModel).toBeTrue();
    expect(component.blocker).toBe('Enter the address the server answers at.');
    component.setEndpoint('http://host.docker.internal:11434/v1');
    // Named after where it is, until the person names it.
    expect(component.draft.name).toBe('host.docker.internal:11434');
    component.setKey('any');
    expect(component.blocker).toContain('Name the model');
    component.chooseModel('llama3.3');
    expect(component.blocker).toBe('');
  });

  it('lets a model the list does not have be typed, and goes back to the list', async () => {
    const component = create();
    component.startCreate();
    component.selectProvider(provider('anthropic'));
    await settle();

    component.chooseModel(component.other);
    expect(component.typingModel).toBeTrue();
    expect(component.draft.model).toBe('');
    component.chooseModel('claude-released-tomorrow');
    expect(component.draft.model).toBe('claude-released-tomorrow');
    component.pickFromList();
    expect(component.typingModel).toBeFalse();
    expect(component.draft.model).toBe('claude-opus-5-5');
  });

  // ── Saving ──────────────────────────────────────────────────────────

  it('asks the provider whether the key works, and says so when it does', async () => {
    const calls: Record<string, any> = {};
    const component = create([], calls, () => ({ check: { outcome: 'works', reason: '' } }));
    component.startCreate();
    component.selectProvider(provider('anthropic'));
    await settle();
    component.setKey('sk-1');
    await component.save();

    expect(calls['created'][0]).toEqual(jasmine.objectContaining({
      name: 'Anthropic', provider: 'anthropic', model: 'claude-opus-5-5',
      endpoint: 'https://api.anthropic.com', api_key: 'sk-1', check: true,
    }));
    expect(component.editingId).toBeNull();
    expect(component.notice).toContain('the key works');
    expect(calls['changed']).toBe(1);
  });

  it('does not save a key the provider refuses, until the person says to save anyway', async () => {
    const calls: Record<string, any> = {};
    const component = create([], calls, (draft) => draft.check
      ? { connection: undefined, error: 'The provider refused the key: no.',
          check: { outcome: 'refused', reason: 'The provider refused the key: no.' } }
      : {});
    component.startCreate();
    component.selectProvider(provider('anthropic'));
    await settle();
    component.setKey('sk-wrong');
    await component.save();

    expect(component.editingId).toBe('');
    expect(component.refused?.outcome).toBe('refused');
    expect(calls['changed']).toBeUndefined();
    // Typing another key clears what was said about the last one.
    component.setKey('sk-wrong-2');
    expect(component.refused).toBeNull();

    await component.save(false);
    expect(calls['created'][1].check).toBeFalse();
    expect(component.editingId).toBeNull();
    expect(calls['changed']).toBe(1);
  });

  it('saves when the provider could not be asked, and says that it could not', async () => {
    const component = create([], {}, () => ({ check: { outcome: 'unknown', reason: '404' } }));
    component.startCreate();
    component.selectProvider(provider('openrouter'));
    await settle();
    component.setKey('k');
    component.chooseModel('some/model');
    await component.save();
    expect(component.editingId).toBeNull();
    expect(component.notice).toContain('could not be asked');
  });

  it('shares a new provider with everyone on a desktop install, and with nobody on a served one', async () => {
    const calls: Record<string, any> = {};
    const desktop = create([], calls, () => ({}), true);
    desktop.startCreate();
    expect(desktop.shareMode).toBe('org');
    desktop.selectProvider(provider('anthropic'));
    await settle();
    desktop.setKey('sk-1');
    await desktop.save();
    expect(calls['created'][0].owner).toEqual({ groups: ['everyone'], users: [] });

    const served = create();
    served.startCreate();
    expect(served.shareMode).toBe('private');
  });

  // ── Editing ─────────────────────────────────────────────────────────

  it('never prefills the key when editing, and keeps the saved address', async () => {
    const component = create();
    component.startEdit(connection('Azure', { keys: {
      provider: 'anthropic', model: 'deployment', endpoint: 'https://gateway.example/anthropic',
    } }));
    await settle();

    expect(component.choosing).toBeFalse();
    expect(component.draft.api_key).toBe('');
    expect(component.draft.endpoint).toBe('https://gateway.example/anthropic');
    // A model the list does not have is shown as typed, not lost.
    expect(component.typingModel).toBeTrue();
    // Blank means keep — demanding the key back would force retyping a
    // value nobody can read out.
    expect(component.blocker).toBe('');
  });

  it('routes an edit to update, with a blank key', async () => {
    const calls: Record<string, any> = {};
    const component = create([], calls);
    component.startEdit(connection('Old'));
    await settle();
    component.chooseModel('claude-opus-5-5');
    await component.save();
    expect(calls['updated'].id).toBe('llm_Old');
    expect(calls['updated'].draft.api_key).toBe('');
    expect(calls['updated'].draft.model).toBe('claude-opus-5-5');
  });

  // ── The default, and removing ───────────────────────────────────────

  it('never re-sets the default that already is', async () => {
    const calls: Record<string, any> = {};
    const component = create([], calls);

    await component.makeDefault(connection('X', { is_default: true }));
    expect(calls['defaulted']).toBeUndefined();

    await component.makeDefault(connection('Y'));
    expect(calls['defaulted']).toBe('llm_Y');
  });

  it('removes through the dialog, never directly', async () => {
    const calls: Record<string, any> = {};
    const component = create([connection('X')], calls);

    const target = component.connections[0];
    component.requestDelete(target);
    expect(component.deleteTarget).toBe(target);

    await component.remove(target);
    expect(component.deleteTarget).toBeNull();
    expect(calls['changed']).toBe(1);
  });
});
