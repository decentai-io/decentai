import { SafetyComponent } from './safety.component';

describe('SafetyComponent', () => {
  const saved = {
    mcp: 'allowed', blocked_sites: ['tracker.test'], scripts: 'always',
    programs: 'always', packages: 'any', allowed_packages: [],
  };

  function create(calls: Record<string, any> = {}, service: any = {}) {
    return new SafetyComponent(
      {
        get: async () => structuredClone(saved),
        update: async (safety: any) => {
          calls['saved'] = structuredClone(safety);
          return { safety };
        },
        ...service,
      } as any,
      { can: () => true } as any,
    );
  }

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

  it('says so when the settings could not be read, instead of a blank page', async () => {
    const component = create({}, { get: async () => null });

    await component.load();

    expect(component.loading).toBeFalse();
    expect(component.loadError).not.toBe('');
  });
});
