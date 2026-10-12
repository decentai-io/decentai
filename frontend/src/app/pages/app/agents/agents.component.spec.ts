import { convertToParamMap } from '@angular/router';
import { EMPTY } from 'rxjs';

import { AgentsComponent } from './agents.component';

describe('AgentsComponent', () => {
  function offer(id: string, overrides: any = {}) {
    return {
      agent_id: id,
      name: id,
      description: `The ${id} agent.`,
      status: 'installed',
      loaded_version: '1.0.0',
      installed_version: '1.0.0',
      functions: [],
      resources: {},
      dependencies: [],
      ...overrides,
    } as any;
  }

  function createComponent(
    agents: any[] = [],
    service: any = {},
    profile: any = {},
    secretsByFamily: Record<string, any[]> = {},
    profileService: any = {},
  ) {
    return new AgentsComponent(
      {
        available: async () => agents,
        installed: async () => [],
        install: async () => ({ data: {} }),
        uninstall: async () => ({ data: {} }),
        ...service,
      } as any,
      { can: () => true } as any,
      {
        get: async () => profile,
        saveDefaultAgents: async () => ({}),
        ...profileService,
      } as any,
      { list: async (family: string) => secretsByFamily[family] || [] } as any,
      { list: async () => [] } as any,
    );
  }

  it('says where an approved agent stands in one word, the first that holds', () => {
    const component = createComponent();
    const word = (agent: any) => component.standing(agent).word;

    expect(word(offer('a'))).toBe('Ready');
    expect(word(offer('a', { prepared: { state: 'preparing' } }))).toBe('Preparing');
    expect(word(offer('a', { prepared: { state: 'failed', error: 'pip' } }))).toBe('Failed');
    expect(word(offer('b', { status: 'update_available', loaded_version: null })))
      .toBe('Update available');
    expect(word(offer('c', { status: 'removed' }))).toBe('Not in its source any more');
    // Failing comes before anything else that is also true of it.
    expect(word(offer('d', { status: 'update_available', prepared: { state: 'failed' } })))
      .toBe('Failed');
  });

  it('counts what needs attention from the same standing the cards show', async () => {
    const component = createComponent([
      offer('a'), offer('b', { status: 'update_available' }),
      offer('c', { prepared: { state: 'failed' } }),
    ]);
    await component.ngOnInit();

    expect(component.readyCount).toBe(1);
    expect(component.attentionCount).toBe(2);
  });

  // ── Credentials an approved agent is still waiting for ──────────────

  it('says which credential an approved agent is still waiting for', async () => {
    const agent = offer('jira', {
      resource_refs: { secrets: { connection: 'agt_jira__connection/v1' } },
      resources: {
        secrets: [{ id: 'connection', label: 'Jira Connection' }],
      },
    });
    const component = createComponent([agent]);
    await component.ngOnInit();

    // Installing defined the credential TYPE; nobody has created one yet,
    // so the agent is approved and unable to do its job. It is named by
    // the manifest's label — the derived slug is bookkeeping, and nobody
    // should have to recognise their agent in it.
    expect(component.credentials(agent)).toEqual([
      { id: 'connection', family: 'agt_jira__connection',
        label: 'Jira Connection', saved: 0, granted: false },
    ]);
    expect(component.missingCredentials(agent).length).toBe(1);
  });

  it('counts a granted slot as satisfied, not missing', async () => {
    // No secret of this agent's own exists, but somebody granted it a
    // saved credential — the page must stop saying "needs credential".
    const agent = offer('jira', {
      resource_refs: { secrets: { connection: 'agt_jira__connection/v1' } },
      granted_secrets: ['connection'],
    });
    const component = createComponent([agent]);
    await component.ngOnInit();

    expect(component.credentials(agent)[0].granted).toBe(true);
    expect(component.missingCredentials(agent).length).toBe(0);
  });

  it('keys the family bare, even though the pinned ref carries the org', async () => {
    // A real pinned ref is `<org>:agt_x__connection/v1`, but secrets
    // record their family WITHOUT the org — keeping the prefix made the
    // saved list under every agent come back empty.
    const agent = offer('jira', {
      resource_refs: {
        secrets: { connection: 'org_abc:agt_jira__connection/v1' },
      },
    });
    const component = createComponent([agent], {}, {}, {
      agt_jira__connection: [{ resource_ref: 'sec_1' }],
    });
    await component.ngOnInit();

    expect(component.credentials(agent)[0].family).toBe('agt_jira__connection');
    expect(component.credentials(agent)[0].saved).toBe(1);
  });

  it('falls back to the resource id when the manifest gave no label', async () => {
    const agent = offer('jira', {
      resource_refs: { secrets: { connection: 'agt_jira__connection/v1' } },
    });
    const component = createComponent([agent]);
    await component.ngOnInit();

    expect(component.credentials(agent)[0].label).toBe('connection');
  });

  it('stops asking once a credential exists', async () => {
    const agent = offer('jira', {
      resource_refs: { secrets: { connection: 'agt_jira__connection/v1' } },
    });
    const component = createComponent([agent], {}, {}, {
      agt_jira__connection: [{ resource_ref: 'sec_1' }],
    });
    await component.ngOnInit();

    expect(component.credentials(agent)[0].saved).toBe(1);
    expect(component.missingCredentials(agent)).toEqual([]);
  });

  it('never asks for a credential an agent does not declare', async () => {
    const agent = offer('web_reader', { resource_refs: { secrets: {} } });
    const component = createComponent([agent]);
    await component.ngOnInit();

    expect(component.credentials(agent)).toEqual([]);
    expect(component.missingCredentials(agent)).toEqual([]);
  });

  it('hands the open agent one credential list, not a fresh one each read', async () => {
    // A template that loops over a fresh array on every change-detection
    // pass re-renders, and re-rendering triggers another pass. With form
    // controls inside the loop it never settles. This pins the identity
    // that breaks the cycle; it is cheap, and the bug it prevents is
    // invisible until it is fatal.
    const agent = offer('jira', {
      resource_refs: { secrets: { connection: 'agt_jira__connection/v1' } },
      resources: { secrets: [{ id: 'connection', label: 'Jira Connection' }] },
    });
    const component = createComponent([agent]);
    await component.ngOnInit();
    await component.openDetail(agent);

    const first = component.openCredentials;
    expect(first.length).toBe(1);
    expect(component.openCredentials).toBe(first);
    expect(component.credentials(agent)).not.toBe(first);
  });

  // ── What a new chat starts with ─────────────────────────────────────

  it('treats no saved preference as every agent being a default', async () => {
    const component = createComponent([offer('one'), offer('two')]);
    await component.ngOnInit();

    expect(component.defaultAgents.size).toBe(2);
  });

  it('reads a saved preference literally', async () => {
    const component = createComponent(
      [offer('one'), offer('two')], {},
      { preferences: { chat: { enabled_agents: ['two'] } } },
    );
    await component.ngOnInit();

    expect([...component.defaultAgents]).toEqual(['two']);
  });

  it('counts an agent with an update waiting as being in new chats', async () => {
    const component = createComponent([
      offer('current'),
      offer('behind', { status: 'update_available', loaded_version: '1.1.0' }),
      offer('withdrawn', { status: 'removed' }),
    ]);
    await component.ngOnInit();

    // All three are installed and can start a chat; where each stands
    // against its source is another matter.
    expect(component.defaultCount).toBe(3);
  });

  it('keeps an agent with an update waiting when another switch moves', async () => {
    // What is written is the whole set. Leaving out an installed agent
    // because an update waits for it would take it out of new chats.
    const written: string[][] = [];
    const behind = offer('behind', { status: 'update_available', loaded_version: '1.1.0' });
    const component = createComponent(
      [offer('one'), behind, offer('two')], {},
      { preferences: { chat: { enabled_agents: ['one', 'behind', 'two'] } } }, {},
      {
        saveDefaultAgents: async (ids: string[]) => {
          written.push(ids);
          return {};
        },
      },
    );
    await component.ngOnInit();

    await component.toggleDefault(offer('two'));
    expect(written).toEqual([['one', 'behind']]);

    await component.toggleDefault(behind);
    expect(written[1]).toEqual(['one']);
  });

  it('saves the moment the switch moves, because there is no Save button', async () => {
    const written: string[][] = [];
    const component = createComponent(
      [offer('one'), offer('two')], {},
      { preferences: { chat: { enabled_agents: ['one', 'two'] } } }, {},
      {
        saveDefaultAgents: async (ids: string[]) => {
          written.push(ids);
          return {};
        },
      },
    );
    await component.ngOnInit();

    await component.toggleDefault(offer('two'));

    expect(component.isDefault(offer('two'))).toBe(false);
    expect(written).toEqual([['one']]);
  });

  it('puts the switch back when the save fails', async () => {
    // A switch that lies about what was saved is worse than one that
    // moves twice.
    const component = createComponent(
      [offer('one')], {},
      { preferences: { chat: { enabled_agents: ['one'] } } }, {},
      { saveDefaultAgents: async () => ({ error: 'nope' }) },
    );
    await component.ngOnInit();

    await component.toggleDefault(offer('one'));

    expect(component.isDefault(offer('one'))).toBe(true);
    expect(component.error).toBe('nope');
  });

  // ── The open agent ──────────────────────────────────────────────────

  it('shows the open agent as it reads after an update', async () => {
    // The update itself is taken on the review page; what this page
    // owes is to show the open agent from the next read of the rows.
    let rows = [offer('jira', { status: 'update_available', loaded_version: '1.1.0' })];
    const component = createComponent([], { available: async () => rows });
    await component.ngOnInit();
    await component.openDetail(component.agents[0]);

    rows = [offer('jira', { installed_version: '1.1.0', loaded_version: '1.1.0' })];
    await component.onCredentialsChanged();

    expect(component.openAgent?.status).toBe('installed');
    expect(component.openAgent?.installed_version).toBe('1.1.0');
  });

  it('leaves the open agent alone when it reads the same', async () => {
    const component = createComponent([], {
      available: async () => [offer('jira')],
    });
    await component.ngOnInit();
    await component.openDetail(component.agents[0]);
    const open = component.openAgent;

    await component.onCredentialsChanged();

    expect(component.openAgent).toBe(open);
  });

  it('gives a reader the scopes and the origin the list carries', async () => {
    const component = new AgentsComponent(
      {
        installed: async () => [{
          agent_id: 'agt_1', name: 'Notebook', description: '', version: '1.0.0',
          functions: ['agt_1.notes.save'], resources: {}, scopes: ['notebook'],
          source: { url: 'https://example.test/org/agents.git', sha: 'abcdef1' },
          package_digest: 'sha256:9f2c',
        }],
      } as any,
      { can: (action: string) => action !== 'agents:agent:available' } as any,
      { get: async () => ({}) } as any,
      { list: async () => [] } as any,
      { list: async () => [] } as any,
    );
    await component.ngOnInit();

    const row = component.agents[0];
    expect(row.scopes).toEqual(['notebook']);
    expect(row.source?.sha).toBe('abcdef1');
    expect(row.package_digest).toBe('sha256:9f2c');
    // Where it connects is not on the list: absent, not "nothing".
    expect(row.network).toBeUndefined();
  });

  // ── Lifecycle ───────────────────────────────────────────────────────

  it('asks before uninstalling instead of going straight through', async () => {
    let removed = '';
    const agent = offer('jira');
    const component = createComponent([agent], {
      uninstall: async (id: string) => {
        removed = id;
        return { data: {} };
      },
    });
    await component.ngOnInit();

    component.requestUninstall(agent);
    expect(component.uninstalling).toEqual([agent]);
    expect(removed).toBe('');

    component.closeUninstall();
    expect(component.uninstalling).toEqual([]);

    component.requestUninstall(agent);
    await component.confirmUninstall();
    expect(removed).toBe('jira');
    expect(component.uninstalling).toEqual([]);
  });

  it('uninstalls everything ticked, behind one confirmation', async () => {
    const removed: string[] = [];
    const one = offer('one');
    const two = offer('two');
    const component = createComponent([one, two], {
      uninstall: async (id: string) => {
        removed.push(id);
        return { data: {} };
      },
    });
    await component.ngOnInit();

    component.toggleSelected(one);
    component.toggleSelected(two);
    component.requestUninstallSelected();
    expect(component.uninstalling.length).toBe(2);

    await component.confirmUninstall();

    expect(removed).toEqual(['one', 'two']);
    expect(component.selected.size).toBe(0);
    expect(component.notice).toContain('2 agents uninstalled');
  });

  it('names what would not go, and leaves it ticked', async () => {
    // One refusal neither stops the rest nor hides them: what went is
    // gone, what stayed is named and still selected to try again.
    const one = offer('one');
    const two = offer('two');
    const component = createComponent([one, two], {
      uninstall: async (id: string) =>
        (id === 'two' ? { error: 'It is in use.' } : { data: {} }),
    });
    await component.ngOnInit();

    component.toggleSelected(one);
    component.toggleSelected(two);
    component.requestUninstallSelected();
    await component.confirmUninstall();

    expect(component.error).toContain('two');
    expect(component.error).toContain('It is in use.');
    expect(component.selected.has('one')).toBe(false);
    expect(component.selected.has('two')).toBe(true);
  });

  // ── Every view has an address ───────────────────────────────────────

  function addressed(agents: any[], params: Record<string, string>, secretsByFamily: any = {}) {
    const went: any[][] = [];
    const component = new AgentsComponent(
      { available: async () => agents, installed: async () => [] } as any,
      { can: () => true } as any,
      { get: async () => ({}) } as any,
      { list: async (family: string) => secretsByFamily[family] || [] } as any,
      { list: async () => [] } as any,
      { events: EMPTY, navigate: async (to: any[]) => { went.push(to); return true; } } as any,
      { snapshot: { firstChild: { paramMap: convertToParamMap(params) } } } as any,
    );
    return { component, went };
  }

  it('opens the agent and the tab its address names', async () => {
    const { component } = addressed([offer('agt_1')], { ref: 'agt_1', tab: 'functions' });
    await component.ngOnInit();

    expect(component.openAgent?.agent_id).toBe('agt_1');
    expect(component.openTab).toBe('functions');
  });

  it('lands on Credentials when the address names no tab and one is missing', async () => {
    const waiting = offer('agt_1', {
      resources: { secrets: [{ id: 'connection', label: 'Jira' }] },
      resource_refs: { secrets: { connection: 'org:agt_1__connection/v1' } },
    });
    const { component } = addressed([waiting], { ref: 'agt_1' });
    await component.ngOnInit();

    expect(component.openTab).toBe('credentials');
  });

  it('says so, and goes back to the list, for an agent that is not installed', async () => {
    const { component, went } = addressed([offer('agt_1')], { ref: 'agt_gone' });
    await component.ngOnInit();

    expect(component.openAgent).toBeNull();
    expect(component.error).toContain('not installed');
    expect(went).toEqual([['/agents']]);
  });

  it('puts the agent and its tab in the address when they are opened', async () => {
    const { component, went } = addressed([offer('agt_1')], {});
    await component.ngOnInit();

    await component.openDetail(component.agents[0]);
    component.openTabOf('access');
    component.openTabOf('overview');

    expect(went).toEqual([['/agents', 'agt_1'], ['/agents', 'agt_1', 'access'], ['/agents', 'agt_1']]);
  });
});
