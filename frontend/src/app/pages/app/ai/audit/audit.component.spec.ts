import { AuditComponent } from './audit.component';

describe('AuditComponent', () => {
  function create(asked: any[] = []) {
    return new AuditComponent(
      {
        searchAudit: async (query: any) => {
          asked.push(query);
          return { events: [], next_before: null };
        },
        searchAuditAll: async (query: any) => {
          asked.push({ ...query, all: true });
          return { events: [], next_before: null };
        },
      } as any,
      { can: () => true } as any,
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
});
