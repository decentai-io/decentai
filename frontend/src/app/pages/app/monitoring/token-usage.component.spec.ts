import { TokenUsageComponent } from './token-usage.component';

describe('TokenUsageComponent', () => {
  const counts = (said: any = {}) => ({
    calls: 0, input: 0, output: 0, cache_read: 0, cache_write: 0, reasoning: 0,
    prompt: 0, tokens: 0, cache_share: 0, cost: 0, unpriced_tokens: 0, unreported: 0, ...said,
  });
  const usage = (series: any[], said: any = {}) => ({
    window: '7d', unit: 'day', since: '', until: '',
    totals: counts({ calls: 3, input: 200, cache_read: 600, output: 200, prompt: 800, tokens: 1000, cache_share: 0.75 }),
    cost: { estimated: 0.0123, currency: 'USD', priced_tokens: 1000, unpriced_tokens: 0,
            unpriced_share: 0, unpriced_models: [] },
    series, models: [], purposes: [], agents: [], people: [],
    ...said,
  });

  function create(answer: any, asked: string[] = []) {
    return new TokenUsageComponent({
      tokens: async (window: string) => { asked.push(window); return answer; },
    } as any);
  }

  it('draws each step, the cached part apart, against a top an axis can say', async () => {
    const component = create(usage([
      counts({ at: '2026-10-13T00:00:00+00:00', tokens: 800, cache_read: 600, calls: 2 }),
      counts({ at: '2026-10-14T00:00:00+00:00', tokens: 200, cache_read: 0, calls: 1 }),
    ]));
    await component.load();

    expect(component.top).toBe(1000);
    expect(component.columns[0].cachedHeight).toBe(60);
    expect(component.columns[0].restHeight).toBe(20);
    expect(component.columns[1].restHeight).toBe(20);
    expect(component.columns[0].tip).toContain('800 tokens in 2 calls');
    expect(component.ticks).toEqual([1000, 500, 0]);
  });

  it('asks again for the window chosen', async () => {
    const asked: string[] = [];
    const component = create(usage([]), asked);
    await component.load();
    await component.choose('30d');
    expect(asked).toEqual(['7d', '30d']);
  });

  it('says an estimate as an estimate, and what it could not price', async () => {
    const component = create(usage([], {
      cost: { estimated: 12.5, currency: 'USD', priced_tokens: 900, unpriced_tokens: 100,
              unpriced_share: 0.1, unpriced_models: [{ provider: 'custom', model: 'mine', tokens: 100 }] },
    }));
    await component.load();

    expect(component.partlyPriced).toBeTrue();
    expect(component.money(12.5)).toContain('12.50');
    expect(component.money(0.004)).toBe('<$0.01');
    expect(component.money(0)).toBe('$0.00');
    expect(component.percent(0.1)).toBe('10%');
    expect(component.percent(0.004)).toBe('<1%');
    // A row none of whose tokens has a price has no cost to say.
    expect(component.rowCost(counts({ tokens: 5, unpriced_tokens: 5 }))).toBe('—');
    expect(component.rowCost(counts({ tokens: 5, unpriced_tokens: 1, cost: 0.02 }))).toContain('0.02');
  });

  it('names what each call was for', () => {
    const component = create(null);
    expect(component.purpose('title')).toBe('Naming chats');
    expect(component.purpose('agent')).toBe("Agents' own model calls");
  });

  it('says so when nothing could be added up', async () => {
    const component = create(null);
    await component.load();
    expect(component.error).toContain('could not be added up');
  });
});
