import { SkillsComponent } from './skills.component';

describe('SkillsComponent', () => {
  function skill(ref: string, title = ref) {
    return {
      resource_ref: ref,
      resource_id: 'skill',
      owner: { groups: [], users: ['u1'] },
      keys: { title, summary: `When ${title} applies.` },
      created_by: 'u1',
    } as any;
  }

  function createComponent(
    skills: any[] = [],
    profile: any = {},
    service: any = {},
    profiles: any = {},
    confirm: any = {},
  ) {
    return new SkillsComponent(
      {
        list: async () => skills,
        get: async () => ({ ...skills[0], values: { body: 'The body.' } }),
        remove: async () => ({}),
        ...service,
      } as any,
      {
        get: async () => profile,
        peers: async () => [],
        saveDefaultSkills: async () => ({ profile }),
        ...profiles,
      } as any,
      { can: () => true } as any,
      { events: { subscribe: () => ({ unsubscribe() {} }) }, navigate: async () => true } as any,
      { queryParamMap: { subscribe: () => ({ unsubscribe() {} }) }, snapshot: { firstChild: null } } as any,
      { ask: async () => true, ...confirm } as any,
    );
  }

  it('treats no saved preference as every skill being a default', async () => {
    const component = createComponent([skill('sk_1'), skill('sk_2')]);
    await component.ngOnInit();

    expect(component.defaultCount).toBe(2);
  });

  it('reads a saved preference literally, including an empty one', async () => {
    const chosen = createComponent(
      [skill('sk_1'), skill('sk_2')],
      { preferences: { chat: { enabled_skills: ['sk_2'] } } },
    );
    await chosen.ngOnInit();
    expect(chosen.defaultCount).toBe(1);
    expect(chosen.isDefault(skill('sk_2'))).toBeTrue();

    // An empty list is a choice — no skills — not "unset".
    const none = createComponent(
      [skill('sk_1')],
      { preferences: { chat: { enabled_skills: [] } } },
    );
    await none.ngOnInit();
    expect(none.defaultCount).toBe(0);
  });

  it('saves the default set in the order the skills are listed', async () => {
    let saved: string[] = [];
    const component = createComponent(
      [skill('sk_1'), skill('sk_2'), skill('sk_3')],
      { preferences: { chat: { enabled_skills: [] } } },
      {},
      {
        saveDefaultSkills: async (refs: string[]) => {
          saved = refs;
          return { profile: { preferences: { chat: { enabled_skills: refs } } } };
        },
      },
    );
    await component.ngOnInit();

    await component.toggleDefault(skill('sk_3'));
    await component.toggleDefault(skill('sk_1'));

    expect(saved).toEqual(['sk_1', 'sk_3']);
  });

  it('puts the mark back when the save fails', async () => {
    const component = createComponent(
      [skill('sk_1')],
      { preferences: { chat: { enabled_skills: ['sk_1'] } } },
      {},
      { saveDefaultSkills: async () => ({ error: 'nope' }) },
    );
    await component.ngOnInit();

    await component.toggleDefault(skill('sk_1'));

    expect(component.isDefault(skill('sk_1'))).toBeTrue();
    expect(component.error).toBe('nope');
  });

  it('keeps the groups it shares into after saving a default', async () => {
    // The profile an update answers with carries no groups.
    const component = createComponent(
      [skill('sk_1')],
      { user_id: 'u1', groups: [{ group_id: 'grp_1', group_name: 'Finance' }],
        preferences: { chat: { enabled_skills: [] } } },
      {},
      { saveDefaultSkills: async (refs: string[]) =>
          ({ profile: { user_id: 'u1', preferences: { chat: { enabled_skills: refs } } } }) },
    );
    await component.ngOnInit();

    await component.toggleDefault(skill('sk_1'));

    expect(component.myGroups).toEqual([{ group_id: 'grp_1', group_name: 'Finance' }]);
  });

  it('says who can use a skill by name, and whose somebody else\'s is', async () => {
    const component = createComponent([], { user_id: 'u1' });
    await component.ngOnInit();
    const shared = {
      ...skill('sk_1'), owner: { groups: [], users: ['u1', 'u2'] },
      owner_names: { groups: [], users: ['Me', 'Dana'] },
    };
    const theirs = { ...skill('sk_2'), created_by: 'u2', created_by_name: 'Dana' };

    expect(component.sharing(skill('sk_1')).word).toBe('Private');
    expect(component.sharing(shared).word).toBe('Dana');
    expect(component.sharing(theirs).word).toBe('Shared by Dana');
  });

  it('narrows the list by whose a skill is and by what is on, and orders it', async () => {
    const mine = { ...skill('sk_1', 'Zebra'), updated_at: '2026-10-01T00:00:00Z' };
    const theirs = { ...skill('sk_2', 'Apple'), created_by: 'u2', created_by_name: 'Dana', updated_at: '2026-10-05T00:00:00Z' };
    const component = createComponent([mine, theirs],
      { user_id: 'u1', preferences: { chat: { enabled_skills: ['sk_2'] } } });
    await component.ngOnInit();

    expect(component.shownSkills.map((s) => s.resource_ref)).toEqual(['sk_2', 'sk_1']);
    component.sort = 'title';
    expect(component.shownSkills.map((s) => s.keys.title)).toEqual(['Apple', 'Zebra']);
    component.show = 'mine';
    expect(component.shownSkills.map((s) => s.resource_ref)).toEqual(['sk_1']);
    component.show = 'on';
    expect(component.shownSkills.map((s) => s.resource_ref)).toEqual(['sk_2']);
    expect(component.filters[0].items.map((item) => item.count)).toEqual([2, 1, 1, 1]);
  });

  it('warns when more skills are selected than a chat can show', async () => {
    const many = Array.from({ length: 41 }, (_, i) => skill(`sk_${i}`));
    const component = createComponent(many);
    await component.ngOnInit();

    expect(component.catalogLimit).toBe(40);
    expect(component.defaultCount).toBe(41);
    expect(component.overCatalogLimit).toBeTrue();
  });

  it('counts against the number of skills the person chose to list', async () => {
    const many = Array.from({ length: 41 }, (_, i) => skill(`sk_${i}`));

    const more = createComponent(many, { preferences: { chat: { max_skills: 80 } } });
    await more.ngOnInit();
    expect(more.catalogLimit).toBe(80);
    expect(more.overCatalogLimit).toBeFalse();

    // Zero is every skill: nothing is cut, so nothing to warn about.
    const all = createComponent(many, { preferences: { chat: { max_skills: 0 } } });
    await all.ngOnInit();
    expect(all.overCatalogLimit).toBeFalse();
  });

  it('asks before deleting, and drops the skill from the defaults', async () => {
    let removed = '';
    let answer = false;
    let listed = [skill('sk_1')];
    const component = createComponent(listed, {}, {
      list: async () => listed,
      remove: async (ref: string) => {
        removed = ref;
        listed = [];
        return {};
      },
    }, {}, { ask: async () => answer });
    await component.ngOnInit();

    await component.askDelete(skill('sk_1'));
    expect(removed).toBe('');

    answer = true;
    await component.askDelete(skill('sk_1'));
    expect(removed).toBe('sk_1');
    expect(component.defaultSkills.has('sk_1')).toBeFalse();
  });
});
