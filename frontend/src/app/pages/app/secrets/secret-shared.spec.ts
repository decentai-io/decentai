import {
  agentsUsing, consentsOf, identifier, isOutdated, lentTo, secretTitle, shownFields,
} from './secret-shared';

describe('what is read off a secret', () => {
  function secret(overrides: any = {}) {
    return {
      resource_ref: 'sec_1', resource_id: 'agt_1__connection', name: 'Warehouse',
      definition_ref: 'org:agt_1__connection/v1', definition_version: 1,
      owner: { groups: [], users: ['me'] }, keys: {}, created_by: 'me', used_by_agents: [],
      ...overrides,
    } as any;
  }

  const definition = {
    definition_id: 'agt_1__connection', version: 2, label: 'Connection',
    fields: [
      { name: 'service_url', label: 'Service URL', type: 'string', storage: 'keys' },
      { name: 'api_token', label: 'API Token', type: 'secret', storage: 'values' },
    ],
  } as any;

  it('names every agent that reads it: the one it was saved for, then those it was lent to', () => {
    const lent = secret({
      home_agent: { agent_ref: 'agt_1', name: 'Jira' },
      used_by_agents: [{ agent_ref: 'agt_2', name: 'Confluence', resource_id: 'connection', grant_id: 'g1' }],
    });
    expect(agentsUsing(lent)).toEqual(['Jira', 'Confluence']);
    expect(lentTo(lent).map((agent) => agent.name)).toEqual(['Confluence']);
    expect(agentsUsing(secret())).toEqual([]);
  });

  it('does not take a consent on a saved login for a lending', () => {
    const login = secret({
      resource_id: 'site__example_com',
      keys: { host: 'example.com', account: 'dana', consents: 'agt_1@example.com' },
      used_by_agents: [{ agent_ref: 'agt_1', name: 'Browser', resource_id: '', site: 'example.com' }],
    });
    expect(lentTo(login)).toEqual([]);
    expect(consentsOf(login)).toEqual([
      { agent_ref: 'agt_1', name: 'Browser', site: 'example.com', pair: 'agt_1@example.com' },
    ]);
    expect(secretTitle(login)).toBe('example.com · dana');
  });

  it('shows plain fields as text and encrypted ones as set, in the shape\'s order', () => {
    const stored = secret({ keys: { service_url: 'https://sync.example.com' } });
    expect(shownFields(stored, [definition])).toEqual([
      { label: 'Service URL', value: 'https://sync.example.com', hidden: false },
      { label: 'API Token', value: '', hidden: true },
    ]);
    expect(identifier(stored, [definition])).toBe('https://sync.example.com');
  });

  it('says a secret is outdated when its shape has a newer version', () => {
    expect(isOutdated(secret(), [definition])).toBeTrue();
    expect(isOutdated(secret({ definition_version: 2 }), [definition])).toBeFalse();
  });
});
