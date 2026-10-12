import { SimpleChange, SimpleChanges } from '@angular/core';

import { AgentDetailComponent } from './agent-detail.component';

describe('AgentDetailComponent', () => {
  function agent(overrides: any = {}) {
    return {
      agent_id: 'agt_1',
      name: 'Notebook',
      description: 'Saves and finds notes.',
      status: 'installed',
      loaded_version: '1.0.0',
      installed_version: '1.0.0',
      functions: [],
      resources: {},
      dependencies: [],
      ...overrides,
    } as any;
  }

  function changed(keys: string[]): SimpleChanges {
    return keys.reduce((changes, key) => {
      changes[key] = new SimpleChange(null, null, false);
      return changes;
    }, {} as SimpleChanges);
  }

  function create(credentials: any[] = []) {
    const component = new AgentDetailComponent(
      {} as any, {} as any, { can: () => true } as any);
    component.agent = agent();
    component.credentials = credentials as any;
    return component;
  }

  it('counts a slot a credential was lent to as bound, not missing', () => {
    const component = create([
      { id: 'connection', family: 'agt_1__connection', label: 'Jira', saved: 0, granted: true },
    ]);

    expect(component.missing).toEqual([]);
    expect(component.standing.word).toBe('Ready');
  });

  it('stands where the list says it stands, with the version said apart', () => {
    const component = create([
      { id: 'connection', family: 'agt_1__connection', label: 'Jira', saved: 0 },
    ]);

    expect(component.standing.word).toBe('Needs a credential');
    expect(component.version).toBe('v1.0.0');
    // No version to say is nothing said, never "vnull".
    component.agent = agent({ installed_version: null });
    expect(component.version).toBe('');
  });

  it('asks for a tab rather than changing it: the address is whoever shows it’s to write', () => {
    const component = create();
    const asked: string[] = [];
    component.tabChange.subscribe((tab) => asked.push(tab));

    component.tabChange.emit('access');
    component.ngOnChanges(changed(['busyId']));

    expect(asked).toEqual(['access']);
    expect(component.tab).toBe('overview');
  });

  it('says the hosts are not shown, rather than none, on a row without them', () => {
    const component = create();
    expect(component.networkKnown).toBeFalse();

    component.agent = agent({
      network: { declared: true, any: false, hosts: ['api.example.test'], from_secrets: [] },
    });
    expect(component.networkKnown).toBeTrue();
    expect(component.network.hosts).toEqual(['api.example.test']);
  });

  it('counts what approving it agreed to, by the level that matters', () => {
    const component = create();
    component.agent = agent({
      functions: [
        { name: 'a.b', permission_level: 0 },
        { name: 'a.c', permission_level: 0 },
        { name: 'a.d', permission_level: 3 },
        { name: 'a.e', permission_level: null },
      ],
    });

    expect(component.levelSummary).toEqual([
      { name: 'Reads', count: 2 },
      { name: 'Outside action', count: 1 },
    ]);
  });

  it('names where the code came from, and what was actually kept', () => {
    const component = create();
    component.agent = agent({
      source: { type: 'git', url: 'https://example.test/org/agents.git',
                sha: 'abcdef1234567890' },
      package_digest: 'sha256:9f2c1122334455667788',
    });

    expect(component.origin).toBe('org/agents @ abcdef1');
    expect(component.packageLabel).toBe('9f2c11223344');
  });
});
