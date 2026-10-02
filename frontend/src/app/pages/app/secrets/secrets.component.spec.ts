import { SecretsComponent } from './secrets.component';

describe('SecretsComponent', () => {
  const ME = 'usr_me';

  function secret(ref: string, overrides: any = {}) {
    return {
      resource_ref: ref,
      resource_id: 'agt_1__connection',
      name: ref,
      definition_ref: 'org:agt_1__connection/v1',
      definition_version: 1,
      owner: { groups: [], users: [ME] },
      keys: {},
      created_by: ME,
      used_by_agents: [],
      ...overrides,
    } as any;
  }

  async function create(secrets: any[], service: any = {}, calls: any = {}) {
    const component = new SecretsComponent(
      {
        list: async () => secrets,
        update: async (ref: string, changes: any) => {
          calls.updated = { ref, changes };
          return { resource: secrets[0] };
        },
        remove: async () => ({ deleted: true }),
        ...service,
      } as any,
      {
        list: async () => [],
        get: async () => ({ definition: {
          definition_ref: 'org:agt_1__connection/v1', definition_id: 'agt_1__connection',
          version: 1, label: 'Connection', description: '', fields: [],
        } }),
      } as any,
      { get: async () => ({ user_id: ME, groups: [] }), peers: async () => [] } as any,
      { can: () => true } as any,
      {} as any,
      {} as any,
    );
    await component.ngOnInit();
    return component;
  }

  // ── Which agent a credential is listed under ────────────────────────

  it('lists a credential under the agent it was saved for', async () => {
    // Saved under an agent's own slot it reaches that agent with no
    // grant, so no grant names it: the home agent is what does.
    const component = await create([
      secret('sec_own', { home_agent: { agent_ref: 'agt_1', name: 'Jira' } }),
    ]);

    expect(component.groupedSecrets.map((group) => group.agent)).toEqual(['Jira']);
  });

  it('lists a lent credential under every agent that reads it', async () => {
    const component = await create([
      secret('sec_lent', {
        home_agent: { agent_ref: 'agt_1', name: 'Jira' },
        used_by_agents: [{ agent_ref: 'agt_2', name: 'Confluence', resource_id: 'connection' }],
      }),
    ]);

    expect(component.groupedSecrets.map((group) => group.agent)).toEqual(['Confluence, Jira']);
  });

  it('keeps a credential no agent reads in the last group, unnamed', async () => {
    const component = await create([
      secret('sec_orphan'),
      secret('sec_own', { home_agent: { agent_ref: 'agt_1', name: 'Jira' } }),
    ]);

    expect(component.groupedSecrets.map((group) => group.agent)).toEqual(['Jira', '']);
  });

  // ── What stands in the way of deleting one ──────────────────────────

  it('lets a saved login go while agents are allowed on its site', async () => {
    // A consent rides in the same list as a grant and is not one: the
    // backend refuses a delete for grants only.
    const login = secret('sec_login', {
      resource_id: 'site__example_com',
      keys: { host: 'example.com', consents: 'agt_1@example.com' },
      used_by_agents: [{ agent_ref: 'agt_1', name: 'Browser', resource_id: '', site: 'example.com' }],
    });
    const component = await create([login]);

    expect(component.grantedTo(login)).toEqual([]);
  });

  it('holds a credential that is lent to an agent', async () => {
    const lent = secret('sec_lent', {
      used_by_agents: [{ agent_ref: 'agt_2', name: 'Confluence', resource_id: 'connection' }],
    });
    const component = await create([lent]);

    expect(component.grantedTo(lent).length).toBe(1);
    expect(component.agentNames(lent)).toBe('Confluence');
  });

  // ── Sharing ─────────────────────────────────────────────────────────

  it('says a secret shared with named people is not private', async () => {
    const component = await create([]);

    expect(component.shareSummary(secret('a'))).toBe('Private');
    expect(component.shareSummary(secret('b', { owner: { groups: [], users: [ME, 'usr_2'] } })))
      .toBe('Shared with a person');
  });

  it('leaves the owner alone when an edit did not touch sharing', async () => {
    // Somebody maintaining a colleague's secret corrects its name; who
    // owns it is not theirs to rewrite by doing so.
    const calls: any = {};
    const theirs = secret('sec_theirs', {
      created_by: 'usr_other', owner: { groups: [], users: ['usr_other'] },
    });
    const component = await create([theirs], {}, calls);

    await component.startEdit(theirs);
    component.formName = 'Renamed';
    await component.save();

    expect(calls.updated.changes.name).toBe('Renamed');
    expect('owner' in calls.updated.changes).toBeFalse();
  });

  it('sends the owner when sharing was changed', async () => {
    const calls: any = {};
    const mine = secret('sec_mine');
    const component = await create([mine], {}, calls);

    await component.startEdit(mine);
    component.shareMode = 'org';
    await component.save();

    expect(calls.updated.changes.owner).toEqual({ groups: ['everyone'], users: [] });
  });
});
