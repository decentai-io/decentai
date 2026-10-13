import { convertToParamMap, NavigationEnd } from '@angular/router';
import { BehaviorSubject, Subject } from 'rxjs';
import { RecordsComponent } from './records.component';

/**
 * The address, as the page meets it: a router that can be navigated and
 * tells of each navigation, and a route whose child names the open kind.
 * Navigating to a kind's address makes it the open one, as the real
 * router would.
 */
function address(agent = '', kind = '') {
  const events = new Subject<unknown>();
  const child = { paramMap: convertToParamMap(agent ? { agent, kind } : {}) };
  const route = {
    snapshot: { firstChild: child },
    queryParamMap: new BehaviorSubject(convertToParamMap({})),
  };
  const router = {
    events,
    navigate: jasmine.createSpy('navigate').and.callFake(async (commands: string[]) => {
      if (commands.length === 3) {
        child.paramMap = convertToParamMap({ agent: commands[1], kind: commands[2] });
        events.next(new NavigationEnd(1, '', ''));
      }
      return true;
    }),
  };
  return { router, route };
}

/** The confirmation, answered yes. */
const confirm = { ask: async () => true };

describe('RecordsComponent deleting several records at once', () => {
  const ME = 'usr_me';

  function record(ref: string, createdBy = ME) {
    return {
      resource_ref: ref,
      resource_id: 'agt_abc123__note',
      created_by: createdBy,
      owner: { users: [], groups: [] },
      keys: { name: ref },
      values: {},
      created_at: '2026-09-01T00:00:00Z',
      updated_at: '2026-09-01T00:00:00Z',
    } as any;
  }

  async function componentWith(rows: any[], remove?: jasmine.Spy) {
    let live = rows.slice();
    const service = {
      list: jasmine.createSpy('list').and.callFake(async () => live.slice()),
      remove: remove || jasmine.createSpy('remove').and.callFake(async (ref: string) => {
        live = live.filter((row) => row.resource_ref !== ref);
        return { deleted: true };
      }),
      shapes: jasmine.createSpy('shapes').and.resolveTo([]),
    };
    const profiles = {
      get: jasmine.createSpy('get').and.resolveTo({ user_id: ME }),
      peers: jasmine.createSpy('peers').and.resolveTo([]),
    };
    const auth = { can: () => true };
    const { router, route } = address();
    const component = new RecordsComponent(
      service as any,
      profiles as any,
      auth as any,
      router as any,
      route as any,
      confirm as any,
    );
    await component.ngOnInit();
    /** A record going away behind our back — somebody else deleted it. */
    const drop = (ref: string) => { live = live.filter((row) => row.resource_ref !== ref); };
    return { component, service, drop };
  }

  it('deletes everything ticked, behind one confirmation', async () => {
    const { component, service } = await componentWith([
      record('rec_1'), record('rec_2'), record('rec_3'),
    ]);

    component.toggleSelected(component.records[0]);
    component.toggleSelected(component.records[2]);
    await component.deleteSelected();

    expect(service.remove).toHaveBeenCalledTimes(2);
    expect(component.records.length).toBe(1);
    expect(component.records[0].resource_ref).toBe('rec_2');
    expect(component.selected.size).toBe(0);
  });

  it('deletes nothing when the confirmation is refused', async () => {
    const { component, service } = await componentWith([record('rec_1')]);
    (component as any).confirm = { ask: async () => false };

    await component.deleteOne(component.records[0]);

    expect(service.remove).not.toHaveBeenCalled();
    expect(component.records.length).toBe(1);
  });

  it('names what would not go, and leaves it ticked', async () => {
    const remove = jasmine.createSpy('remove').and.callFake(async (ref: string) =>
      ref === 'rec_2' ? { error: 'Only the user who created a record can change it.' }
                      : { deleted: true });
    const { component } = await componentWith([record('rec_1'), record('rec_2')], remove);

    component.selectAllShown();
    await component.deleteSelected();

    expect(component.error).toContain('Still here');
    // The one that refused is still ticked; the one that went is not.
    expect(component.selected.has('rec_2')).toBe(true);
    expect(component.selected.has('rec_1')).toBe(false);
  });

  it('will not tick a record the person did not create', async () => {
    const { component } = await componentWith([
      record('rec_mine'), record('rec_theirs', 'usr_someone_else'),
    ]);

    expect(component.deletable(component.records[0])).toBe(true);
    expect(component.deletable(component.records[1])).toBe(false);
    expect(component.selectableShown.length).toBe(1);

    component.selectAllShown();

    expect(component.selected.size).toBe(1);
    expect(component.selected.has('rec_mine')).toBe(true);
  });

  it('still deletes one record on its own, through the same door', async () => {
    const { component, service } = await componentWith([record('rec_1'), record('rec_2')]);

    await component.deleteOne(component.records[1]);

    expect(service.remove).toHaveBeenCalledOnceWith('rec_2');
    expect(component.records.length).toBe(1);
    expect(component.notice).toBe('Record deleted.');
  });

  it('opens on the first kind of the first agent', async () => {
    const { component } = await componentWith([record('rec_1')]);

    expect(component.activeAgent).toBe('agt_abc123');
    expect(component.activeGroup).toBe('agt_abc123__note');
  });

  it('moves to the next kind when the last of one is deleted', async () => {
    /** The filter column is derived from the records, so emptying a
     *  kind removes it. Left pointing at it, the page would list nothing
     *  under an address that names nothing. */
    const note = record('rec_note');
    note.resource_id = 'agt_abc123__note';
    const task = record('rec_task');
    task.resource_id = 'agt_abc123__task';
    const { component } = await componentWith([note, task]);

    component.filter('kind', 'agt_abc123__note');
    expect(component.activeGroup).toBe('agt_abc123__note');

    await component.deleteOne(component.records[0]);

    expect(component.activeAgent).toBe('agt_abc123');
    expect(component.activeGroup).toBe('agt_abc123__task');
    expect(component.selectionRecords.length).toBe(1);
  });

  it('lists nothing when the last record of all is deleted', async () => {
    const { component } = await componentWith([record('rec_only')]);

    await component.deleteOne(component.records[0]);

    expect(component.records).toEqual([]);
    expect(component.shownRecords).toEqual([]);
    expect(component.filters.find((group) => group.key === 'kind')).toBeUndefined();
  });

  it('keeps what is shared with the person apart from what is theirs', async () => {
    const { component } = await componentWith([
      record('rec_mine'), record('rec_theirs', 'usr_someone_else'),
    ]);

    expect(component.shownRecords.map((row) => row.resource_ref)).toEqual(['rec_mine']);

    component.show = 'shared';

    expect(component.shownRecords.map((row) => row.resource_ref)).toEqual(['rec_theirs']);
  });

  it('drops a tick when the record behind it is gone', async () => {
    const { component, drop } = await componentWith([record('rec_1'), record('rec_2')]);

    component.selectAllShown();
    expect(component.selected.size).toBe(2);

    // Somebody else deleted one; the next listing simply does not have it,
    // and a tick on a record that is not there means nothing.
    drop('rec_2');
    await (component as any).reload();

    expect(component.selected.size).toBe(1);
    expect(component.selected.has('rec_1')).toBe(true);
  });
});

describe('RecordsComponent writing a record by hand', () => {
  const ME = 'usr_me';

  function shape(slot: string, access: string[], fields: any[] = []) {
    return {
      agent_ref: 'agt_abc123', agent_name: 'Notebook',
      resource_id: `agt_abc123__${slot}`, label: slot, description: '',
      fields, user_access: access,
    } as any;
  }

  async function componentWith(rows: any[], shapes: any[], calls: any = {}) {
    const { router, route } = address();
    const component = new RecordsComponent(
      {
        list: async () => rows,
        shapes: async () => shapes,
        create: async (resourceId: string, fields: any) => {
          calls.created = { resourceId, fields };
          return { resource: {} };
        },
        update: async (ref: string, fields: any) => {
          calls.updated = { ref, fields };
          return { resource: {} };
        },
      } as any,
      { get: async () => ({ user_id: ME }), peers: async () => [] } as any,
      { can: () => true } as any,
      router as any,
      route as any,
      confirm as any,
    );
    await component.ngOnInit();
    return component;
  }

  function note(keys: any) {
    return {
      resource_ref: 'rec_1', resource_id: 'agt_abc123__note', created_by: ME,
      owner: { users: [ME], groups: [] }, keys, values: {},
    } as any;
  }

  const TITLE = { name: 'title', label: 'Title', type: 'string', storage: 'keys', required: true, options: [] };
  const TAG = { name: 'tag', label: 'Tag', type: 'string', storage: 'keys', required: false, options: [] };
  const COUNT = { name: 'count', label: 'Count', type: 'number', storage: 'keys', required: false, options: [] };

  it('offers a kind that holds no record yet', async () => {
    // Kinds on screen come from the records there are, so the first
    // record of a kind has nowhere on the page to be made from.
    const component = await componentWith([], [shape('note', ['create'], [TITLE])]);

    component.openPicker();

    expect(component.formShape?.resource_id).toBe('agt_abc123__note');
  });

  it('does not open a form for a kind its agent keeps to itself', async () => {
    const component = await componentWith(
      [note({ title: 'a' })],
      [shape('note', []), shape('task', ['create']), shape('list', ['create'])],
    );

    component.openPicker();

    expect(component.formShape).toBeNull();
    expect(component.pickerOpen).toBeTrue();
    expect(component.pickerShapes.map((s) => s.label)).toEqual(['task', 'list']);
  });

  it('empties a text field that was cleared in an edit', async () => {
    // An edit is merged over what is stored: a field left out keeps
    // its old value, so a cleared one has to be sent.
    const calls: any = {};
    const stored = note({ title: 'a', tag: 'old' });
    const component = await componentWith(
      [stored], [shape('note', ['create', 'update'], [TITLE, TAG, COUNT])], calls);

    component.startEdit(stored);
    component.formFields['tag'] = '';
    await component.saveForm();

    expect(calls.updated.fields).toEqual({ title: 'a', tag: '' });
  });

  it('refuses to empty what the shape gives no empty value', async () => {
    const calls: any = {};
    const stored = note({ title: 'a', count: 3 });
    const component = await componentWith(
      [stored], [shape('note', ['update'], [TITLE, TAG, COUNT])], calls);

    component.startEdit(stored);
    component.formFields['count'] = '';
    await component.saveForm();
    expect(calls.updated).toBeUndefined();
    // Said under the field it belongs to, once Save was pressed.
    expect(component.tried).toBeTrue();
    expect(component.problemOf(COUNT as any)).toContain('Count');

    component.formFields['count'] = 3;
    component.formFields['title'] = '';
    await component.saveForm();
    expect(calls.updated).toBeUndefined();
    expect(component.problemOf(TITLE as any)).toBe('Enter Title.');
  });

  it('asks for what is required before a new record is sent', async () => {
    const calls: any = {};
    const component = await componentWith([], [shape('note', ['create'], [TITLE, COUNT])], calls);

    component.openPicker();
    component.formFields['count'] = 'many';
    await component.saveForm();

    expect(calls.created).toBeUndefined();
    expect(component.problemOf(TITLE as any)).toBe('Enter Title.');
    expect(component.problemOf(COUNT as any)).toContain('number');

    component.formFields['title'] = 'Ops';
    component.formFields['count'] = '4';
    await component.saveForm();
    expect(calls.created.fields).toEqual({ title: 'Ops', count: 4 });
  });

  it('keeps the form open with the reason when the backend refuses', async () => {
    const { router, route } = address();
    const component = new RecordsComponent(
      {
        list: async () => [], shapes: async () => [shape('note', ['create'], [TITLE])],
        create: async () => ({ error: 'A note with this title exists.' }),
      } as any,
      { get: async () => ({ user_id: ME }), peers: async () => [] } as any,
      { can: () => true } as any, router as any, route as any, confirm as any,
    );
    await component.ngOnInit();

    component.openPicker();
    component.formFields['title'] = 'Ops';
    await component.saveForm();

    expect(component.formShape).not.toBeNull();
    expect(component.formError).toBe('A note with this title exists.');
  });
});
