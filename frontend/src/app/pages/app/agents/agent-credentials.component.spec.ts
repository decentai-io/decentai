import { SimpleChange, SimpleChanges } from '@angular/core';

import { AgentCredentialsComponent } from './agent-credentials.component';

describe('AgentCredentialsComponent', () => {
  const credential = {
    id: 'connection', family: 'agt_1__connection',
    label: 'Jira Connection', saved: 0,
  };

  function changed(keys: string[]): SimpleChanges {
    return keys.reduce((changes, key) => {
      changes[key] = new SimpleChange(null, null, false);
      return changes;
    }, {} as SimpleChanges);
  }

  function create(secrets: any = {}, definitions: any = {}, agents: any = {}) {
    const component = new AgentCredentialsComponent(
      {
        list: async () => [],
        create: async () => ({}),
        remove: async () => ({}),
        setDefault: async () => ({}),
        ...secrets,
      } as any,
      { get: async () => ({}), ...definitions } as any,
      {
        secretSlots: async () => [],
        grantSecret: async () => ({ data: {} }),
        revokeSecret: async () => ({ data: {} }),
        ...agents,
      } as any,
      // Connecting an account opens a popup; nothing here does.
      {} as any,
    );
    component.agent = {
      agent_id: 'agt_1',
      resource_refs: { secrets: { connection: 'agt_1__connection/v1' } },
      resources: { secrets: [{ id: 'connection', label: 'Jira Connection' }] },
    } as any;
    component.credentials = [{ ...credential }];
    return component;
  }

  // ── Why the credential panel froze ──────────────────────────────────
  //
  // A template that loops over a fresh array on every change-detection
  // pass re-renders, and re-rendering triggers another pass. With form
  // controls inside the loop it never settles — the tab locks up. These
  // pin the identity that breaks the cycle.

  it('hands back the same empty array every time, not a new one', () => {
    const component = create();

    expect(component.fieldsFor('nothing')).toBe(component.fieldsFor('nothing'));
    expect(component.savedSecrets('nothing')).toBe(component.savedSecrets('nothing'));
  });

  it('does not re-read every secret because an unrelated input arrived', async () => {
    // The groups list turns up a moment after the agent does. Reloading
    // on it is a wasted round trip per credential.
    let reads = 0;
    const component = create({
      list: async () => {
        reads += 1;
        return [];
      },
    });

    await component.ngOnChanges(changed(['agent']));
    expect(reads).toBe(1);

    await component.ngOnChanges(changed(['groups']));
    expect(reads).toBe(1);
  });

  it('asks for the definition VERSION the agent approved, not the latest', async () => {
    // The form has to ask for the fields the backend will validate
    // against, which is the version pinned at install.
    let asked: any = null;
    const component = create({}, {
      get: async (query: any) => {
        asked = query;
        return { definition: { fields: [{ name: 'token' }] } };
      },
    });

    await component.ngOnChanges(changed(['agent']));

    expect(asked).toEqual({ definition_ref: 'agt_1__connection/v1' });
    expect(component.fieldsFor('agt_1__connection').length).toBe(1);
  });

  it('knows when several credentials exist and none was pinned', async () => {
    // The state that fails chats: with no default and more than one
    // visible, a chat that has not chosen is refused as ambiguous.
    const component = create({
      list: async () => [
        { resource_ref: 'sec_1', name: 'A' },
        { resource_ref: 'sec_2', name: 'B' },
      ],
    });
    await component.ngOnChanges(changed(['agent']));

    expect(component.needsDefault('agt_1__connection')).toBe(true);

    component.defaults = { agt_1__connection: 'sec_1' };
    expect(component.needsDefault('agt_1__connection')).toBe(false);
  });

  it('says who can reach a saved credential', () => {
    const component = create();
    component.groups = [{ group_id: 'g1', group_name: 'Research' }] as any;

    expect(component.ownerKind({ owner: { groups: ['everyone'] } } as any)).toBe('org');
    expect(component.ownerLabel({ owner: { groups: ['everyone'] } } as any))
      .toBe('organization');
    expect(component.ownerKind({ owner: { groups: ['g1'] } } as any)).toBe('group');
    expect(component.ownerLabel({ owner: { groups: ['g1'] } } as any)).toBe('Research');
    expect(component.ownerKind({ owner: { groups: [] } } as any)).toBe('personal');
  });

  // ── Being handed one that already exists ────────────────────────────
  //
  // Declaring the shape of a credential is the agent's to do; handing
  // over a filled-in one is a person's decision, and revocable.

  function slot(overrides: any = {}) {
    return {
      resource_id: 'connection',
      label: 'Jira Connection',
      description: '',
      definition_ref: 'agt_1__connection/v1',
      grant: null,
      candidates: [
        { resource_ref: 'sec_1', name: 'Shared Jira',
          definition_ref: 'org:shared/v1' },
      ],
      ...overrides,
    };
  }

  it('offers only what the backend said fits this slot', async () => {
    const component = create({}, {}, { secretSlots: async () => [slot()] });
    await component.ngOnChanges(changed(['agent']));

    expect(component.candidates(credential).map((c: any) => c.name))
      .toEqual(['Shared Jira']);
    // A slot the agent never declared has nothing to offer.
    expect(component.candidates({ ...credential, id: 'other' } as any))
      .toEqual([]);
  });

  it('names the credential it was handed, not its ref', async () => {
    const component = create({}, {}, {
      secretSlots: async () => [slot({
        grant: { grant_id: 'asg_1', agent_ref: 'agt_1',
                 resource_id: 'connection', secret_ref: 'sec_1',
                 created_by: 'a@test.org' },
      })],
    });
    await component.ngOnChanges(changed(['agent']));

    expect(component.grantedName(credential)).toBe('Shared Jira');
  });

  it('grants the picked credential and reloads', async () => {
    const calls: any[] = [];
    const component = create({}, {}, {
      secretSlots: async () => [slot()],
      grantSecret: async (agent: string, resource: string, secret: string) => {
        calls.push({ agent, resource, secret });
        return { data: {} };
      },
    });
    await component.ngOnChanges(changed(['agent']));

    component.openPicker(credential);
    component.picked = 'sec_1';
    await component.grant(credential);

    expect(calls).toEqual([
      { agent: 'agt_1', resource: 'connection', secret: 'sec_1' },
    ]);
    expect(component.pickerFor).toBe('');
  });

  it('will not grant with nothing chosen', async () => {
    let called = false;
    const component = create({}, {}, {
      secretSlots: async () => [slot()],
      grantSecret: async () => {
        called = true;
        return { data: {} };
      },
    });
    await component.ngOnChanges(changed(['agent']));

    component.openPicker(credential);
    component.picked = '';
    await component.grant(credential);

    expect(called).toBeFalse();
  });

  it('surfaces a refusal rather than pretending it worked', async () => {
    const failures: string[] = [];
    const component = create({}, {}, {
      secretSlots: async () => [slot()],
      grantSecret: async () => ({ error: 'not the shape this agent asked for' }),
    });
    await component.ngOnChanges(changed(['agent']));
    component.failed.subscribe((message: string) => failures.push(message));

    component.openPicker(credential);
    component.picked = 'sec_1';
    await component.grant(credential);

    expect(failures).toEqual(['not the shape this agent asked for']);
    // Still open, so the choice is not silently lost.
    expect(component.pickerFor).toBe('connection');
  });

  it('takes a granted credential back', async () => {
    const revoked: string[] = [];
    const component = create({}, {}, {
      secretSlots: async () => [slot({
        grant: { grant_id: 'asg_1', agent_ref: 'agt_1',
                 resource_id: 'connection', secret_ref: 'sec_1',
                 created_by: 'a@test.org' },
      })],
      revokeSecret: async (_agent: string, grantId: string) => {
        revoked.push(grantId);
        return { data: {} };
      },
    });
    await component.ngOnChanges(changed(['agent']));

    await component.revokeGrant(credential);
    expect(revoked).toEqual(['asg_1']);
  });
});
