import { ChatDefaultsComponent } from './chat-defaults.component';

describe('ChatDefaultsComponent', () => {
  function create(chat: any = {}, calls: Record<string, any> = {}) {
    return new ChatDefaultsComponent(
      {
        get: async () => ({ user_id: 'me', preferences: { chat } }),
        saveChatDefaults: async (changes: any) => {
          calls['defaults'] = changes;
          return { profile: { user_id: 'me', preferences: { chat: changes } } };
        },
      } as any,
      { list: async () => [] } as any,
      { can: () => true } as any,
      { available: false, automatic: false, refresh: async () => false } as any,
      { hearing: async () => 'none', dictation: async () => null, voice: async () => null,
        reading: async () => null, choose: () => undefined } as any,
      { offered: async () => ({ configured: false, ready: false, speech: { configured: false, ready: false } }) } as any,
      { available: false } as any,
    );
  }

  it('starts from the platform’s standard where nothing was chosen', async () => {
    const component = create();
    await component.load();

    expect(component.draft.value).toEqual({
      llm_secret_ref: '', llm_model: '', trust_level: 1, max_turns: 20, max_skills: 40 });
    expect(component.draft.dirty).toBeFalse();
    expect(component.unsavedChangesTo()).toBe('');
  });

  it('remembers a provider and one of its models as what a new chat starts with', async () => {
    const calls: Record<string, any> = {};
    const component = create({ llm_secret_ref: 'llm_Anthropic', llm_model: 'claude-sonnet-5' }, calls);
    await component.load();
    expect(component.draft.value.llm_model).toBe('claude-sonnet-5');

    component.chooseModel({ connectionId: 'llm_Anthropic', model: 'claude-opus-5-5' });
    expect(component.unsavedChangesTo()).toBe('Chat defaults');
    await component.save();

    expect(calls['defaults']).toEqual(jasmine.objectContaining({
      llm_secret_ref: 'llm_Anthropic', llm_model: 'claude-opus-5-5' }));
    expect(component.draft.dirty).toBeFalse();
  });

  it('hands the choice back to the organization’s default with no model of its own', async () => {
    const calls: Record<string, any> = {};
    const component = create({ llm_secret_ref: 'llm_Anthropic', llm_model: 'claude-sonnet-5' }, calls);
    await component.load();

    component.chooseModel({ connectionId: '', model: '' });
    await component.save();

    expect(calls['defaults'].llm_secret_ref).toBeNull();
    expect(calls['defaults'].llm_model).toBe('');
  });

  it('keeps the form and says why when the save is refused', async () => {
    const component = create();
    (component as any).profiles.saveChatDefaults = async () => ({ error: 'Not allowed.' });
    await component.load();
    component.draft.value.trust_level = 3;

    await component.save();

    expect(component.saveError).toBe('Not allowed.');
    expect(component.draft.dirty).toBeTrue();
  });

  it('says so when the page could not be read', async () => {
    const component = create();
    (component as any).profiles.get = async () => null;

    await component.load();

    expect(component.loadError).not.toBe('');
  });
});
