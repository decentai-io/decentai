import { AgentAccessComponent } from './agent-access.component';

describe('AgentAccessComponent', () => {
  const agent: any = {
    agent_id: 'agt_box', name: 'Box', scopes: ['folder'],
    functions: [{ name: 'agt_box.files.list' }, { name: 'agt_box.files.read' }, { name: 'agt_box.files.write' }],
  };
  const someone = {
    grant_id: 'g1', owner: { groups: [], users: ['u1'] },
    functions: ['files.list', 'files.read'], constraints: { folder: ['Reports'] },
  };
  const everyone = { grant_id: 'g2', owner: { groups: ['everyone'], users: [] }, functions: '*' };

  function create(calls: string[] = []) {
    let grants: any[] = [someone, everyone];
    const service = {
      grantsOrError: async () => ({ grants, error: '' }),
      grants: async () => grants,
      grant: async (_agent: string, owner: any, functions: any, constraints: any) => {
        calls.push(`grant ${JSON.stringify({ owner, functions, constraints })}`);
        grants = [...grants, { grant_id: 'g3', owner, functions, constraints }];
        return {};
      },
      revoke: async (_agent: string, id: string) => {
        calls.push(`revoke ${id}`);
        grants = grants.filter((grant) => grant.grant_id !== id);
        return {};
      },
    };
    const component = new AgentAccessComponent(
      service as any,
      { list: async () => [] } as any,
      { list: async () => [{ user_id: 'u1', email: 'sam@test.org' }] } as any,
      { ask: async () => true } as any,
    );
    component.agent = agent;
    return component;
  }

  it('names the functions a grant covers, not only how many', async () => {
    const component = create();
    await component.ngOnChanges();
    expect(component.scope(someone as any)).toBe('2 functions');
    expect(component.namedFunctions(someone as any)).toEqual(['files.list', 'files.read']);
    expect(component.namedFunctions(everyone as any)).toEqual([]);
  });

  it('fills the form with a grant to change it', async () => {
    const component = create();
    await component.ngOnChanges();
    component.edit(someone as any);
    expect(component.editing).toBe(someone as any);
    expect(component.draftAudience).toBe('people');
    expect([...component.draftUsers]).toEqual(['u1']);
    expect(component.draftWhole).toBeFalse();
    expect([...component.draftFunctions]).toEqual(['files.list', 'files.read']);
    expect(component.draftScopes['folder']).toEqual(['Reports']);

    component.edit(everyone as any);
    expect(component.draftAudience).toBe('everyone');
    expect(component.draftWhole).toBeTrue();

    component.cancelEdit();
    expect(component.editing).toBeNull();
    expect(component.draftAudience).toBeNull();
  });

  it('saves a change as the new grant first, then takes the old one away', async () => {
    const calls: string[] = [];
    const component = create(calls);
    await component.ngOnChanges();
    component.edit(someone as any);
    component.toggleFunction({ name: 'agt_box.files.write' } as any);
    await component.save();

    expect(calls.length).toBe(2);
    expect(calls[0]).toContain('"functions":["files.list","files.read","files.write"]');
    expect(calls[1]).toBe('revoke g1');
    expect(component.editing).toBeNull();
    expect(component.grants.map((grant) => grant.grant_id)).toEqual(['g2', 'g3']);
  });

  it('does not offer to change a grant that names groups and people at once', () => {
    const component = create();
    expect(component.editable(someone as any)).toBeTrue();
    expect(component.editable({ owner: { groups: ['grp'], users: ['u1'] } } as any)).toBeFalse();
  });
});
