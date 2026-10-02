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

  it('opens on Overview when there is nothing to fix', () => {
    const component = create();
    component.ngOnChanges(changed(['agent']));

    expect(component.tab).toBe('overview');
  });

  it('opens on the tab that fixes an unusable agent', () => {
    // An approved agent with no credential is installed and unable to do
    // the thing it was installed for, so the page lands where the work is.
    const component = create([
      { id: 'connection', family: 'agt_1__connection', label: 'Jira', saved: 0 },
    ]);
    component.ngOnChanges(changed(['agent']));

    expect(component.tab).toBe('credentials');
  });

  it('counts a slot a credential was lent to as bound, not missing', () => {
    const component = create([
      { id: 'connection', family: 'agt_1__connection', label: 'Jira', saved: 0, granted: true },
    ]);
    component.ngOnChanges(changed(['agent']));

    expect(component.missing).toEqual([]);
    expect(component.tab).toBe('overview');
  });

  it('stays on the tab when the same agent is read again', () => {
    // After an update, or as it becomes ready, the row is a new object
    // for the same agent: not a reason to move somebody.
    const component = create();
    component.ngOnChanges(changed(['agent']));
    component.tab = 'functions';

    component.ngOnChanges({
      agent: new SimpleChange(agent(), agent({ status: 'update_available' }), false),
    });

    expect(component.tab).toBe('functions');
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

  it('does not throw you off a tab when an unrelated input changes', () => {
    // Every @Input arrives in ngOnChanges, `busyId` included. Resetting on
    // that would move somebody off Access the moment they saved a grant.
    const component = create();
    component.ngOnChanges(changed(['agent']));
    component.tab = 'access';

    component.ngOnChanges(changed(['busyId']));

    expect(component.tab).toBe('access');
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
      { name: 'read', count: 2 },
      { name: 'external', count: 1 },
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
