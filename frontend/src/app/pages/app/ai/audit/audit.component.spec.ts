import { AuditComponent } from './audit.component';
import { groupAudit } from './audit-groups';

describe('AuditComponent', () => {
  const catalog = [{
    service: 'files', label: 'Files',
    actions: [{ action: 'files:file:upload', description: 'Upload files' }],
  }];

  function create(asked: any[] = [], events: any[] = []) {
    return new AuditComponent(
      {
        searchAudit: async (query: any) => {
          asked.push(query);
          return { events, next_before: null };
        },
        searchAuditAll: async (query: any) => {
          asked.push({ ...query, all: true });
          return { events: [], next_before: null };
        },
      } as any,
      { can: () => true, catalog } as any,
      { navigate: async () => true } as any,
    );
  }

  it('reads a picked day as that day where the person is', () => {
    // A date input gives YYYY-MM-DD. Read as UTC it starts hours into
    // the day east of UTC and ends on the day before it west of UTC.
    const start = AuditComponent.localDay('2026-10-02', false)!;
    const end = AuditComponent.localDay('2026-10-02', true)!;

    expect([start.getFullYear(), start.getMonth(), start.getDate()]).toEqual([2026, 9, 2]);
    expect([start.getHours(), start.getMinutes(), start.getSeconds()]).toEqual([0, 0, 0]);
    expect([end.getFullYear(), end.getMonth(), end.getDate()]).toEqual([2026, 9, 2]);
    expect([end.getHours(), end.getMinutes(), end.getSeconds()]).toEqual([23, 59, 59]);
  });

  it('reads nothing from a box left empty', () => {
    expect(AuditComponent.localDay('', false)).toBeNull();
    expect(AuditComponent.localDay('yesterday', true)).toBeNull();
  });

  it('sends the window as the whole of the days chosen', async () => {
    const asked: any[] = [];
    const component = create(asked);
    component.since = '2026-10-01';
    component.until = '2026-10-02';

    await component.reload();

    expect(asked[0].since).toBe(new Date(2026, 9, 1, 0, 0, 0, 0).toISOString());
    expect(asked[0].until).toBe(new Date(2026, 9, 2, 23, 59, 59, 999).toISOString());
  });

  it('opens on the organization view only for someone who may read it', async () => {
    const asked: any[] = [];
    const component = create(asked);
    component.initialView = 'org';

    await component.ngOnInit();

    expect(component.tab).toBe('org');
    expect(asked[0].all).toBeTrue();
  });

  it('says a platform action in the catalog’s words, and its own name where there are none', async () => {
    const component = create();
    await component.ngOnInit();
    const upload = { event_type: 'platform.action', function: 'files:file:upload', details: { outcome: 'success' } };
    const unknown = { event_type: 'platform.action', function: 'new:thing:do', details: { outcome: 'denied', reason: 'Not allowed.' } };

    expect(component.words(upload)).toBe('Upload files');
    expect(component.more(upload)).toBe('');
    expect(component.outcome(upload)).toBe('Succeeded');
    expect(component.words(unknown)).toBe('new:thing:do');
    expect(component.more(unknown)).toBe('denied · Not allowed.');
    expect(component.outcome(unknown)).toBe('Failed');
  });

  it('lists the record by day, the same thing done again as one row', async () => {
    const at = (hour: number, second: number) => new Date(2026, 9, 12, hour, 0, second).toISOString();
    const upload = (id: string, timestamp: string) => ({
      event_id: id, timestamp, event_type: 'platform.action', function: 'files:file:upload',
      actor: 'me', details: { outcome: 'success' },
    });
    const component = create([], [
      upload('a', at(11, 30)), upload('b', at(11, 20)), upload('c', at(11, 10)),
      upload('d', new Date(2026, 9, 11, 9, 0, 0).toISOString()),
    ]);
    await component.ngOnInit();

    expect(component.days.length).toBe(2);
    expect(component.days[0].items.map(group => group.events.length)).toEqual([3]);
    expect(component.groupKey(component.days[0].items[0])).toBe('group:a');
    expect(component.groupKey(component.days[1].items[0])).toBe('d');
  });
});

describe('the audit trail in groups', () => {
  const event = (second: number, more: any = {}) => ({
    timestamp: new Date(2026, 9, 12, 11, 0, second).toISOString(),
    event_type: 'platform.action', function: 'settings:llm:providers', actor: 'me',
    details: { outcome: 'success' }, ...more,
  });

  it('keeps apart what differs in action, in person or in how it went', () => {
    const groups = groupAudit([
      event(50), event(40, { function: 'settings:llm:models' }), event(30),
      event(20, { actor: 'you' }), event(10, { actor: 'you', details: { outcome: 'denied' } }),
    ]);

    expect(groups.map(group => group.events.length)).toEqual([1, 1, 1, 1, 1]);
  });

  it('starts a new row once a minute has passed since the row’s newest', () => {
    const late = { ...event(0), timestamp: new Date(2026, 9, 12, 10, 58, 0).toISOString() };
    const groups = groupAudit([event(50), event(5), late]);

    expect(groups.map(group => group.events.length)).toEqual([2, 1]);
  });
});
