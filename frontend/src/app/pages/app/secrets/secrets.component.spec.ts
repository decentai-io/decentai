import { of } from 'rxjs';

import { SecretsComponent } from './secrets.component';

describe('SecretsComponent', () => {
  const ME = 'usr_me';

  function secret(ref: string, overrides: any = {}) {
    return {
      resource_ref: ref, resource_id: 'agt_1__connection', name: ref,
      definition_ref: 'org:agt_1__connection/v1', definition_version: 1,
      owner: { groups: [], users: [ME] }, keys: {}, created_by: ME, used_by_agents: [],
      ...overrides,
    } as any;
  }

  async function create(secrets: any[], service: any = {}, more: any = {}) {
    const component = new SecretsComponent(
      { list: async () => secrets, update: async () => ({}), remove: async () => ({ deleted: true }), ...service } as any,
      { list: async () => [] } as any,
      { get: async () => ({ user_id: ME, groups: [] }), peers: async () => [] } as any,
      { can: () => true } as any,
      {} as any,
      { revokeSecret: async () => ({}), ...more.agents } as any,
      { events: of(), navigate: async () => true } as any,
      { queryParamMap: of({ get: () => null }), snapshot: { firstChild: null } } as any,
      {} as any,
      { ask: async () => true, ...more.confirm } as any,
    );
    await component.ngOnInit();
    return component;
  }

  it('counts the list by whose a secret is, which agent uses it and which site a login is for', async () => {
    const component = await create([
      secret('own', { home_agent: { agent_ref: 'agt_1', name: 'Jira' } }),
      secret('orphan'),
      secret('login', { resource_id: 'site__example_com', keys: { host: 'example.com' } }),
      secret('theirs', { created_by: 'usr_2' }),
    ]);

    const [whose, agents, sites] = component.filters;
    expect(whose.items.map((item) => item.count)).toEqual([3, 1]);
    expect(agents.items.map((item) => `${item.name} ${item.count}`)).toEqual(['Jira 1', 'No agent 1']);
    expect(sites.items.map((item) => item.name)).toEqual(['example.com']);
  });

  it('narrows the list to an agent, to no agent, and to a site', async () => {
    const component = await create([
      secret('own', { home_agent: { agent_ref: 'agt_1', name: 'Jira' } }),
      secret('orphan'),
      secret('login', { resource_id: 'site__example_com', keys: { host: 'example.com' } }),
    ]);

    component.agent = 'Jira';
    expect(component.shownSecrets.map((s) => s.resource_ref)).toEqual(['own']);
    component.agent = 'none';
    expect(component.shownSecrets.map((s) => s.resource_ref)).toEqual(['orphan']);
    component.agent = '';
    component.host = 'example.com';
    expect(component.shownSecrets.map((s) => s.resource_ref)).toEqual(['login']);
  });

  it('says who uses a secret in one line', async () => {
    const component = await create([]);
    expect(component.usedBy(secret('a', {
      home_agent: { agent_ref: 'agt_1', name: 'Notebook' },
      used_by_agents: [{ agent_ref: 'agt_2', name: 'Gmail', resource_id: 'account', grant_id: 'g1' }],
    }))).toBe('Used by Notebook · lent to Gmail');
    expect(component.usedBy(secret('b'))).toBe('');
  });

  it('deletes a lent secret by taking it back first, once that was agreed to', async () => {
    const asked: any[] = [];
    const removed: any[] = [];
    let answer = false;
    const lent = secret('lent', {
      home_agent: { agent_ref: 'agt_1', name: 'Notebook' },
      used_by_agents: [{ agent_ref: 'agt_2', name: 'Gmail', resource_id: 'account', grant_id: 'g1' }],
    });
    const component = await create([lent], {
      remove: async (ref: string, takeBack: boolean) => { removed.push([ref, takeBack]); return {}; },
    }, { confirm: { ask: async (question: any) => { asked.push(question); return answer; } } });

    await component.askDelete(lent);
    expect(removed).toEqual([]);
    expect(asked[0].body).toContain('lent to Gmail');
    expect(asked[0].body).toContain('Notebook is left without it');
    // An agent named by its id is said as a name.
    expect(component.usedBy(secret('x', { home_agent: { agent_ref: 'agt_9', name: 'google_drive' } })))
      .toBe('Used by Google drive');
    expect(asked[0].confirm).toBe('Take back and delete');

    answer = true;
    await component.askDelete(lent);
    expect(removed).toEqual([['lent', true]]);
  });

  it('deletes a secret nobody was lent without asking for anything to be taken back', async () => {
    const removed: any[] = [];
    const plain = secret('plain');
    const component = await create([plain], {
      remove: async (ref: string, takeBack: boolean) => { removed.push([ref, takeBack]); return {}; },
    });

    await component.askDelete(plain);
    expect(removed).toEqual([['plain', false]]);
  });

  it('takes one agent\'s consent back from a login, leaving the others', async () => {
    let sent: any;
    const login = secret('login', {
      resource_id: 'site__example_com',
      keys: { host: 'example.com', consents: 'agt_1@example.com,agt_2@example.com' },
    });
    const component = await create([login], {
      update: async (_ref: string, changes: any) => { sent = changes; return {}; },
    });

    await component.revokeConsent(login, { agent_ref: 'agt_1', name: 'Browser', site: 'example.com', pair: 'agt_1@example.com' });
    expect(sent).toEqual({ fields: { consents: 'agt_2@example.com' } });
  });
});
