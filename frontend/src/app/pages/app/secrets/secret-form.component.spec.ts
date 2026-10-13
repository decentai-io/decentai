import { SecretFormComponent } from './secret-form.component';

describe('SecretFormComponent', () => {
  const ME = 'usr_me';
  const definition = {
    definition_ref: 'org:agt_1__connection/v1', definition_id: 'agt_1__connection', version: 1,
    label: 'Connection', description: '', agent_uses: ['Jira'],
    fields: [
      { name: 'service_url', label: 'Service URL', type: 'string', storage: 'keys', required: true, options: [] },
      { name: 'api_token', label: 'API Token', type: 'secret', storage: 'values', required: true, options: [] },
    ],
  } as any;

  function secret(overrides: any = {}) {
    return {
      resource_ref: 'sec_1', resource_id: 'agt_1__connection', name: 'Warehouse',
      definition_ref: definition.definition_ref, definition_version: 1,
      owner: { groups: [], users: [ME] }, keys: { service_url: 'https://old.example.com' }, created_by: ME,
      ...overrides,
    } as any;
  }

  async function create(data: any = {}, service: any = {}) {
    const closed: any[] = [];
    const calls: any = {};
    const component = new SecretFormComponent(
      { definitions: [definition], profile: { user_id: ME, groups: [] }, peers: [], canShareAny: true, ...data } as any,
      { close: (result: any) => closed.push(result), disableClose: false } as any,
      {
        create: async (payload: any) => { calls.created = payload; return { resource: { resource_ref: 'sec_new' } }; },
        update: async (ref: string, changes: any) => { calls.updated = { ref, changes }; return { resource: { resource_ref: ref } }; },
        ...service,
      } as any,
      { get: async () => ({ definition }) } as any,
      {} as any,
      { lendable: async () => ({ eligible: [], skipped: [] }) } as any,
    );
    await component.ngOnInit();
    return { component, closed, calls };
  }

  it('asks for what the shape requires before a new secret is sent', async () => {
    const { component, calls, closed } = await create();
    component.choose(definition);
    expect(component.name).toBe('Connection');

    await component.save();
    expect(calls.created).toBeUndefined();
    expect(component.missing(definition.fields[0])).toBe('Enter Service URL.');
    expect(component.missing(definition.fields[1])).toBe('Enter API Token.');

    component.fields['service_url'] = 'https://sync.example.com';
    component.fields['api_token'] = 's3cret';
    await component.save();
    expect(calls.created.fields).toEqual({ service_url: 'https://sync.example.com', api_token: 's3cret' });
    expect(calls.created.owner).toEqual({ groups: [], users: [ME] });
    expect(closed[0]).toEqual({ notice: 'Secret created.', ref: 'sec_new' });
  });

  it('keeps a stored encrypted value when its box is left empty in a change', async () => {
    const { component, calls } = await create({ secret: secret() });
    expect(component.missing(definition.fields[1])).toBe('');

    component.name = 'Renamed';
    await component.save();
    expect(calls.updated.changes.fields).toEqual({ service_url: 'https://old.example.com' });
  });

  it('leaves the owner alone when a change did not touch sharing', async () => {
    // Somebody maintaining a colleague's secret corrects its name; who
    // owns it is not theirs to rewrite by doing so.
    const { component, calls } = await create({
      secret: secret({ created_by: 'usr_other', owner: { groups: [], users: ['usr_other'] } }),
    });

    component.name = 'Renamed';
    await component.save();

    expect(calls.updated.changes.name).toBe('Renamed');
    expect('owner' in calls.updated.changes).toBeFalse();
  });

  it('sends the owner when sharing was changed', async () => {
    const { component, calls } = await create({ secret: secret() });

    component.share.mode = 'org';
    await component.save();

    expect(calls.updated.changes.owner).toEqual({ groups: ['everyone'], users: [] });
  });

  it('keeps the dialog and says why when the backend refuses', async () => {
    const { component, closed } = await create({ secret: secret() }, {
      update: async () => ({ error: 'You cannot share into that group.', ungrantable: ['Finance'] }),
    });

    await component.save();

    expect(closed).toEqual([]);
    expect(component.problem).toBe('You cannot share into that group. (Finance)');
  });

  it('groups the shapes by the agent each is for', async () => {
    const { component } = await create({
      definitions: [definition, { ...definition, definition_id: 'x', label: 'Orphan', agent_uses: [] }],
    });
    expect(component.groups.map((group) => group.agent)).toEqual(['Jira', '']);
    expect(component.aboutOf(definition)).toBe('2 fields');
  });
});
