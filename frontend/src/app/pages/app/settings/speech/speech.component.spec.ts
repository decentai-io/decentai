import { SpeechComponent } from './speech.component';

describe('SpeechComponent', () => {
  const everyone = { groups: ['everyone'], users: [] };

  function create(calls: Record<string, any> = {}) {
    return new SpeechComponent(
      {
        get: async () => ({
          speech: {
            transcription_source: 'local', transcription_connection_id: '', transcription_model: '',
            speech_source: 'off', speech_connection_id: '', speech_model: '', speech_voice: '',
          },
          local: { reachable: true, transcription: { state: 'ready', bytes: 9, of: 9, error: '' } },
        }),
        update: async (speech: any) => {
          calls['speech'] = { ...speech };
          return { speech };
        },
      } as any,
      {
        list: async () => [
          { resource_ref: 'llm_Mine', name: 'Mine', owner: { groups: [], users: ['me'] } },
          { resource_ref: 'llm_OpenAI', name: 'OpenAI', owner: everyone },
        ],
      } as any,
      { can: () => true } as any,
      { refresh: async () => false } as any,
    );
  }

  it('offers only the providers shared with everyone', async () => {
    const component = create();
    await component.load();

    expect(component.shared.map((connection) => connection.name)).toEqual(['OpenAI']);
  });

  it('starts on the platform’s own model, ready on this machine', async () => {
    const component = create();
    await component.load();

    expect(component.draft.value.transcription_source).toBe('local');
    expect(component.localStanding('transcription')).toContain('Ready');
    expect(component.blocker).toBe('');
    expect(component.draft.dirty).toBeFalse();
  });

  it('saves the transcription model as a provider and one of its models', async () => {
    const calls: Record<string, any> = {};
    const component = create(calls);
    await component.load();

    // A provider alone does not say which of its models writes speech down.
    component.draft.value.transcription_source = 'connection';
    component.chooseTranscription({ connectionId: 'llm_OpenAI', model: '' });
    expect(component.transcriptionError).toBe('Choose the transcription model.');
    await component.save();
    expect(calls['speech']).toBeUndefined();
    expect(component.saveError).toBe('Choose the transcription model.');

    component.chooseTranscription({ connectionId: 'llm_OpenAI', model: 'whisper-1' });
    await component.save();
    expect(calls['speech']).toEqual({
      transcription_source: 'connection',
      transcription_connection_id: 'llm_OpenAI', transcription_model: 'whisper-1',
      speech_source: 'off', speech_connection_id: '', speech_model: '', speech_voice: '' });
    expect(component.draft.dirty).toBeFalse();
    expect(component.saveError).toBe('');
  });

  it('asks for the provider that speaks, then its model, then its voice', async () => {
    const component = create();
    await component.load();
    const speech = component.draft.value;

    speech.speech_source = 'connection';
    expect(component.blocker).toBe('Choose the provider that speaks.');
    speech.speech_connection_id = 'llm_OpenAI';
    expect(component.blocker).toBe('Name the speech model.');
    speech.speech_model = 'tts-1';
    expect(component.blocker).toBe('Name the voice.');
    speech.speech_voice = 'alloy';
    expect(component.blocker).toBe('');
  });
});
