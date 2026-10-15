import { SkillEditorComponent } from './skill-editor.component';

describe('SkillEditorComponent', () => {
  const ME = 'u1';

  function stored(ref: string, title: string) {
    return {
      resource_ref: ref, keys: { title, summary: `When ${title} applies.` },
      values: { body: 'The body.' }, owner: { groups: [], users: [ME] }, created_by: ME,
    } as any;
  }

  async function create(options: { ref?: string; example?: boolean; skills?: any[]; service?: any; confirm?: any } = {}) {
    const skills = options.skills ?? [];
    const went: any[] = [];
    const component = new SkillEditorComponent(
      {
        list: async () => skills,
        get: async (ref: string) => skills.find((skill) => skill.resource_ref === ref) ?? null,
        create: async (fields: any) => ({ resource: { resource_ref: 'sk_new', ...fields } }),
        update: async () => ({ resource: {} }),
        ...options.service,
      } as any,
      { get: async () => ({ user_id: ME, groups: [] }), peers: async () => [] } as any,
      { can: () => true } as any,
      { navigate: async (...args: any[]) => { went.push(args); return true; } } as any,
      { snapshot: {
        paramMap: { get: () => options.ref ?? null },
        queryParamMap: { get: () => (options.example ? '1' : null) },
      } } as any,
      { ask: async () => true, ...options.confirm } as any,
    );
    await component.ngOnInit();
    return { component, went };
  }

  it('starts empty and clean, and is unsaved once something is typed', async () => {
    const { component } = await create();
    expect(component.dirty).toBeFalse();
    expect(component.unsavedChangesTo()).toBe('');

    component.draft.value.title = 'Reports';
    expect(component.dirty).toBeTrue();
    expect(component.unsavedChangesTo()).toBe('this skill');

    component.discard();
    expect(component.dirty).toBeFalse();
  });

  it('opens a skill with what is stored, and counts a change of sharing as a change', async () => {
    const { component } = await create({ ref: 'sk_1', skills: [stored('sk_1', 'Reports')] });
    expect(component.draft.value).toEqual({ title: 'Reports', summary: 'When Reports applies.', body: 'The body.' });
    expect(component.dirty).toBeFalse();

    component.share.mode = 'org';
    expect(component.dirty).toBeTrue();
  });

  it('says what is missing under its field and sends nothing', async () => {
    let sent = false;
    const { component } = await create({ service: { create: async () => { sent = true; return {}; } } });
    component.draft.value.title = 'Reports';

    await component.save();

    expect(sent).toBeFalse();
    expect(component.titleError).toBe('');
    expect(component.summaryError).toBe('Say when this skill applies.');
    expect(component.bodyError).toBe('Write the skill itself.');
  });

  it('says at once when another skill has the title', async () => {
    const { component } = await create({ skills: [stored('sk_1', 'Quarterly  Report')] });
    component.draft.value.title = ' quarterly report ';
    expect(component.titleError).toContain('Another skill');

    // Its own title is not somebody else's.
    const editing = await create({ ref: 'sk_1', skills: [stored('sk_1', 'Reports')] });
    expect(editing.component.titleError).toBe('');
  });

  it('saves, leaves nothing unsaved, and goes to the skill as it now reads', async () => {
    const { component, went } = await create({ example: true });
    expect(component.draft.value.title).toBe('Clear, useful writing');
    // The example is written into the form, not saved: the save bar is
    // there from the start, and leaving would lose it.
    expect(component.dirty).toBeTrue();
    expect(component.unsavedChangesTo()).toBe('this skill');

    component.draft.value.title = 'Our writing';
    await component.save();

    expect(component.unsavedChangesTo()).toBe('');
    expect(went[0][0]).toEqual(['/data/skills', 'sk_new']);
    expect(went[0][1].state.skillSaved).toBe('created');
  });

  it('keeps the page and says why when the backend refuses', async () => {
    const { component, went } = await create({
      example: true, service: { create: async () => ({ error: 'A skill with this title exists.' }) },
    });
    component.draft.value.title = 'Our writing';
    await component.save();

    expect(went.length).toBe(0);
    expect(component.saveError).toBe('A skill with this title exists.');
    expect(component.dirty).toBeTrue();
  });

  it('asks before the example replaces what was written', async () => {
    let answer = false;
    const { component } = await create({ confirm: { ask: async () => answer } });
    component.draft.value.body = 'My own rules.';

    await component.insertExample();
    expect(component.draft.value.body).toBe('My own rules.');

    answer = true;
    await component.insertExample();
    expect(component.draft.value.body).toContain('plain, direct language');
  });
});
