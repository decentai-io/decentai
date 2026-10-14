import { ActivityOverviewComponent, compact, niceTop } from './activity-overview.component';

describe('ActivityOverviewComponent', () => {
  const summary = (series: any[]) => ({
    window: '7d', unit: 'day', since: '', until: '',
    calls: { ok: 8, failed: 1, refused: 1, total: 10, avg_ms: 1500 },
    series, agents: [], hosts: [],
    approvals: { asked: 2, allowed: 1, denied: 0, waiting: 1 },
    model_calls: 4, secret_uses: 0, people: 1, problems: [],
  });

  function create(answer: any) {
    return new ActivityOverviewComponent({
      summary: async () => answer,
      usage: async () => ({ counted: true, agents: [{}, {}], limits: { memory: 100 }, memory: 25 }),
    } as any);
  }

  it('draws each step against a top an axis can say', async () => {
    const component = create(summary([
      { at: '2026-10-13T00:00:00+00:00', ok: 6, failed: 1, refused: 0 },
      { at: '2026-10-14T00:00:00+00:00', ok: 2, failed: 0, refused: 1 },
    ]));
    await component.load();

    expect(component.top).toBe(10);
    expect(component.columns[0].okHeight).toBe(60);
    expect(component.columns[0].badHeight).toBe(10);
    expect(component.columns[1].badHeight).toBe(10);
    expect(component.ticks).toEqual([10, 5, 0]);
  });

  it('says how the calls ended, and what runs now', async () => {
    const component = create(summary([]));
    await component.load();
    // What runs now is read beside the summary, not before it.
    await new Promise((resolve) => setTimeout(resolve));

    expect(component.notOk).toBe(2);
    expect(component.okShare).toBe('80% succeeded');
    expect(component.running).toBe(2);
    expect(component.fullness).toBe(25);
    expect(component.duration(1500)).toBe('1.5 s');
  });

  it('says so when nothing could be added up', async () => {
    const component = create(null);
    await component.load();
    expect(component.error).toContain('could not be added up');
  });

  it('rounds an axis top to 1, 2 or 5 of a power of ten', () => {
    expect(niceTop(0)).toBe(0);
    expect(niceTop(7)).toBe(10);
    expect(niceTop(13)).toBe(20);
    expect(niceTop(48)).toBe(50);
    expect(niceTop(101)).toBe(200);
  });

  it('keeps small numbers whole and shortens large ones', () => {
    expect(compact(1284)).toBe((1284).toLocaleString());
    expect(compact(12_900)).toMatch(/12\.9/);
  });
});
