import { SafetyComponent } from './safety.component';

describe('SafetyComponent', () => {
  const saved = {
    mcp: 'allowed', webhooks: 'allowed', notification_text: 'shown', blocked_sites: ['tracker.test'], scripts: 'always',
    programs: 'always', packages: 'any', allowed_packages: [],
  };

  const rules = {
    text: 'Answer in French.', updated_by: 'admin@test.org',
    updated_at: '2026-10-01T09:00:00+00:00', limit: 4000,
  };

  function create(
    calls: Record<string, any> = {},
    service: any = {},
    guardrail: any = {},
    can: (action: string) => boolean = () => true,
  ) {
    return new SafetyComponent(
      {
        get: async () => {
          calls['safetyRead'] = true;
          return structuredClone(saved);
        },
        update: async (safety: any) => {
          calls['saved'] = structuredClone(safety);
          return { safety };
        },
        ...service,
      } as any,
      { can } as any,
      {
        get: async () => structuredClone(rules),
        update: async (text: string) => {
          calls['rules'] = text;
          return { ...rules, text: text.trim(), updated_by: 'me@test.org' };
        },
        ...guardrail,
      } as any,
    );
  }

  /** Someone who holds exactly these actions. */
  const holding = (...actions: string[]) => (action: string) => actions.includes(action);

  it('reads a pasted address for its site’s name', () => {
    const component = create();

    expect(component.siteName('https://Shop.Example.com:8443/cart?x=1')).toBe('shop.example.com');
    expect(component.siteName('*.example.com.')).toBe('example.com');
    expect(component.siteName('user@example.com')).toBe('example.com');
  });

  it('keeps a package by its name alone', () => {
    const component = create();

    expect(component.packageName('Pandas==2.2')).toBe('pandas');
    expect(component.packageName('requests[socks]')).toBe('requests');
  });

  it('saves the whole form, and is clean again after', async () => {
    const calls: Record<string, any> = {};
    const component = create(calls);
    await component.load();
    expect(component.unsavedChangesTo()).toBe('');

    component.draft.value.programs = 'quiet';
    expect(component.loosened).toBeTrue();
    expect(component.unsavedChangesTo()).toBe('Safety');
    await component.save();

    expect(calls['saved'].programs).toBe('quiet');
    expect(component.draft.dirty).toBeFalse();
  });

  it('keeps the changes and says why when the save is refused', async () => {
    const component = create({}, { update: async () => ({ error: 'Not allowed.' }) });
    await component.load();
    component.draft.value.mcp = 'blocked';

    await component.save();

    expect(component.saveError).toBe('Not allowed.');
    expect(component.draft.dirty).toBeTrue();
  });

  it('saves who may receive webhooks with the rest of the form', async () => {
    const calls: Record<string, any> = {};
    const component = create(calls);
    await component.load();

    component.draft.value.webhooks = 'blocked';
    expect(component.webhooksMeaning).toContain('No webhook takes an event');
    await component.save();

    expect(calls['saved'].webhooks).toBe('blocked');
  });

  it('says so when the settings could not be read, instead of a blank page', async () => {
    const component = create({}, { get: async () => null });

    await component.load();

    expect(component.loading).toBeFalse();
    expect(component.loadError).not.toBe('');
  });

  it('saves the organization’s rules by their own door, and only them when only they changed', async () => {
    const calls: Record<string, any> = {};
    const component = create(calls);
    await component.load();
    expect(component.rules.value.text).toBe('Answer in French.');
    expect(component.rulesSaved.updated_by).toBe('admin@test.org');

    component.rules.value.text = 'Answer in English. ';
    expect(component.unsavedChangesTo()).toBe('Safety');
    await component.save();

    expect(calls['rules']).toBe('Answer in English. ');
    expect(calls['saved']).toBeUndefined();
    expect(component.rules.value.text).toBe('Answer in English.');
    expect(component.rulesSaved.updated_by).toBe('me@test.org');
    expect(component.dirty).toBeFalse();
  });

  it('saves both parts when both changed, and keeps the refused one’s changes', async () => {
    const calls: Record<string, any> = {};
    const component = create(calls, {}, { update: async () => ({ error: 'The rules are at most 4000 characters.' }) });
    await component.load();
    component.draft.value.mcp = 'blocked';
    component.rules.value.text = 'x';

    await component.save();

    expect(calls['saved'].mcp).toBe('blocked');
    expect(component.draft.dirty).toBeFalse();
    expect(component.rules.dirty).toBeTrue();
    expect(component.saveError).toContain('4000 characters');
    component.discard();
    expect(component.rules.value.text).toBe('Answer in French.');
  });

  it('opens for the rules alone to someone who may read only them, and changes nothing', async () => {
    const calls: Record<string, any> = {};
    const component = create(calls, {}, {}, holding('settings:guardrail:get'));
    await component.load();

    expect(component.canSee).toBeFalse();
    expect(calls['safetyRead']).toBeUndefined();
    expect(component.canSeeRules).toBeTrue();
    expect(component.canEditRules).toBeFalse();
    expect(component.loadError).toBe('');
    component.rules.value.text = 'typed anyway';
    expect(component.dirty).toBeFalse();
  });

  it('does not ask for the rules of someone who may not read them', async () => {
    const component = create({}, {}, {
      get: async () => { throw new Error('should not be asked'); },
    }, holding('settings:safety:get', 'settings:safety:update'));
    await component.load();

    expect(component.loadError).toBe('');
    expect(component.canSeeRules).toBeFalse();
    expect(component.rules.value.text).toBe('');
  });

  it('says so when the rules could not be read', async () => {
    const component = create({}, {}, { get: async () => null });
    await component.load();
    expect(component.loadError).not.toBe('');
  });
});
