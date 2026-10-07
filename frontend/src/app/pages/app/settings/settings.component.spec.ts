import { SettingsComponent } from './settings.component';

describe('SettingsComponent', () => {
  function connection(name: string, overrides: any = {}) {
    return {
      resource_ref: `llm_${name}`,
      name,
      owner: { groups: [], users: ['me'] },
      keys: { provider: 'anthropic', model: 'claude-sonnet-5', endpoint: 'https://api.anthropic.com' },
      is_default: false,
      ...overrides,
    } as any;
  }

  const everyone = { groups: ['everyone'], users: [] };

  function create(connections: any[] = [], calls: Record<string, any> = {}, chat: any = {}) {
    const component = new SettingsComponent(
      { list: async () => connections, providers: async () => [] } as any,
      {
        get: async () => ({ user_id: 'me', groups: [], preferences: { chat } }),
        peers: async () => [],
        saveChatDefaults: async (changes: any) => {
          calls['defaults'] = changes;
          return { profile: { user_id: 'me', preferences: { chat: changes } } };
        },
      } as any,
      { can: () => true } as any,
      {
        get: async () => ({ routing: {
          embedding_connection_id: '', embedding_model: '', threshold: 15,
          shortlist: 15, candidates: 50, rerank: true, open_max: 8 } }),
        update: async (routing: any) => {
          calls['routing'] = routing;
          return { routing };
        },
      } as any,
      {
        get: async () => ({
          speech: {
            transcription_source: 'local', transcription_connection_id: '', transcription_model: '',
            speech_source: 'off', speech_connection_id: '', speech_model: '', speech_voice: '',
          },
          local: { reachable: true, transcription: { state: 'ready', bytes: 9, of: 9, error: '' } },
        }),
        update: async (speech: any) => {
          calls['speech'] = speech;
          return { speech };
        },
      } as any,
      // Notifications and push: not asked for here.
      { get: async () => null } as any,
      { isEnabled: false } as any,
      // The route names the tab; the router is only navigated.
      { snapshot: { data: {} } } as any,
      { navigate: async () => true } as any,
      // The voice: whether replies are said is not asked for here.
      { available: false, automatic: false, refresh: async () => false } as any,
    );
    return component;
  }

  it('reads the connections and reads them again when the providers tab changed one', async () => {
    const connections = [connection('Anthropic')];
    const component = create(connections);
    await component.ngOnInit();
    expect(component.connections.map((c) => c.name)).toEqual(['Anthropic']);

    connections.push(connection('OpenAI'));
    await component.reload();
    expect(component.connections.map((c) => c.name)).toEqual(['Anthropic', 'OpenAI']);
  });

  it('offers speech and routing only the providers shared with everyone', async () => {
    const component = create([
      connection('Mine'), connection('Shared', { owner: everyone }),
    ]);
    await component.ngOnInit();
    expect(component.sharedConnections.map((c) => c.name)).toEqual(['Shared']);
  });

  // ── Chat defaults ───────────────────────────────────────────────────

  it('remembers a provider and one of its models as what a new chat starts with', async () => {
    const calls: Record<string, any> = {};
    const component = create([connection('Anthropic')], calls,
                             { llm_secret_ref: 'llm_Anthropic', llm_model: 'claude-sonnet-5' });
    await component.ngOnInit();
    expect(component.chatDefaults.llm_model).toBe('claude-sonnet-5');
    expect(component.chatDefaultsDirty).toBeFalse();

    component.chooseDefaultModel({ connectionId: 'llm_Anthropic', model: 'claude-opus-5-5' });
    expect(component.chatDefaultsDirty).toBeTrue();
    await component.saveChatDefaults();
    expect(calls['defaults']).toEqual(jasmine.objectContaining({
      llm_secret_ref: 'llm_Anthropic', llm_model: 'claude-opus-5-5' }));
  });

  it('hands the choice back to the organization’s default with no model of its own', async () => {
    const calls: Record<string, any> = {};
    const component = create([connection('Anthropic')], calls,
                             { llm_secret_ref: 'llm_Anthropic', llm_model: 'claude-sonnet-5' });
    await component.ngOnInit();
    component.chooseDefaultModel({ connectionId: '', model: '' });
    await component.saveChatDefaults();
    expect(calls['defaults'].llm_secret_ref).toBeNull();
    expect(calls['defaults'].llm_model).toBe('');
  });

  // ── Speech and routing ──────────────────────────────────────────────

  it('saves the transcription model as a provider and one of its models', async () => {
    const calls: Record<string, any> = {};
    const component = create([connection('OpenAI', { owner: everyone })], calls);
    await component.ngOnInit();
    expect(component.speechDirty).toBeFalse();

    // The platform's own model is what an organization starts with.
    expect(component.speech.transcription_source).toBe('local');
    expect(component.localStanding('transcription')).toContain('ready');
    expect(component.speechBlocker).toBe('');

    // A provider alone does not say which of its models writes speech down.
    component.speech.transcription_source = 'connection';
    component.chooseSpeech({ connectionId: 'llm_OpenAI', model: '' });
    expect(component.speechBlocker).toBe('Choose the transcription model.');
    await component.saveSpeech();
    expect(calls['speech']).toBeUndefined();

    component.chooseSpeech({ connectionId: 'llm_OpenAI', model: 'whisper-1' });
    expect(component.speechBlocker).toBe('');
    await component.saveSpeech();
    expect(calls['speech']).toEqual({
      transcription_source: 'connection',
      transcription_connection_id: 'llm_OpenAI', transcription_model: 'whisper-1',
      speech_source: 'off', speech_connection_id: '', speech_model: '', speech_voice: '' });
    expect(component.speechDirty).toBeFalse();

    // A provider that speaks is named with its model and its voice.
    component.speech.speech_source = 'connection';
    expect(component.speechBlocker).toBe('Choose the provider that speaks.');
    component.speech.speech_connection_id = 'llm_OpenAI';
    component.speech.speech_model = 'tts-1';
    expect(component.speechBlocker).toBe('Name the voice.');
    component.speech.speech_voice = 'alloy';
    expect(component.speechBlocker).toBe('');
  });

  it('saves the embedding model with the routing numbers', async () => {
    const calls: Record<string, any> = {};
    const component = create([connection('OpenAI', { owner: everyone })], calls);
    await component.ngOnInit();
    expect(component.routingDirty).toBeFalse();

    component.chooseEmbedding({ connectionId: 'llm_OpenAI', model: '' });
    expect(component.routingBlocker).toBe('Choose the embedding model.');
    component.chooseEmbedding({ connectionId: 'llm_OpenAI', model: 'text-embedding-3-large' });
    expect(component.routingBlocker).toBe('');
    await component.saveRouting();
    expect(calls['routing']).toEqual(jasmine.objectContaining({
      embedding_connection_id: 'llm_OpenAI', embedding_model: 'text-embedding-3-large',
      threshold: 15 }));
  });
});
