import { convertToParamMap } from '@angular/router';
import { EMPTY } from 'rxjs';

import { MonitoringComponent } from './monitoring.component';
import {
  bytesLabel, groupMonitor, monitorChip, monitorFields, monitorKind, monitorLabel, monitorSummary,
  monitorTone,
} from './monitor-words';

describe('MonitoringComponent', () => {
  function create(
    answers: any = {}, asked: any[] = [], can = (_: string) => true,
    address: { view?: string; went?: any[][]; query?: Record<string, string> } = {},
  ) {
    return new MonitoringComponent(
      {
        usage: async () => answers.usage || {
          counted: true, agents: [], limits: {}, memory: 0, confined: {}, services: [],
        },
        events: async (query: any) => {
          asked.push(query);
          return answers.events || { events: [], next_before: null };
        },
        files: async (agent: string) => {
          asked.push({ files: agent });
          return answers.files || { files: [], count: 0, bytes: 0 };
        },
      } as any,
      { can } as any,
      {
        events: EMPTY,
        navigate: async (to: any[], extras?: any) => {
          address.went?.push(extras?.queryParams ? [...to, extras.queryParams] : to);
          return true;
        },
      } as any,
      {
        snapshot: {
          firstChild: { url: address.view ? [{ path: address.view }] : [] },
          queryParamMap: convertToParamMap(address.query || {}),
        },
      } as any,
    );
  }

  afterEach(() => jasmine.clock().uninstall());

  it('says how full the agents are of what they are given', async () => {
    const component = create({
      usage: {
        counted: true, memory: 1500, confined: {},
        limits: { memory: 2000, cpus: 2 },
        agents: [{ agent: 'agt_a', name: 'Browser', memory: 1000, cpu: 0.5, processes: 40 }],
      },
    });
    await component.readUsage();

    expect(component.fullness).toBe(75);
    expect(component.share(component.usage!.agents[0])).toBe(50);
    expect(component.processors(0.5)).toBe('50%');
  });

  it('says how full a part of the platform is, where it was given a limit', () => {
    const component = create();

    expect(component.fullnessOf({ id: 'agents', name: 'Agents', memory: 900, memory_limit: 1000 })).toBe(90);
    expect(component.fullnessOf({ id: 'backend', name: 'Backend', memory: 900, memory_limit: null })).toBeNull();
    expect(component.fullnessOf({ id: 'database', name: 'Database', disk: 5 })).toBeNull();
  });

  it('has no fullness to show where no limit is set', async () => {
    const component = create({
      usage: { counted: true, memory: 1500, confined: {}, limits: {}, agents: [] },
    });
    await component.readUsage();

    expect(component.fullness).toBeNull();
  });

  it('says in words what agents are not held to here', async () => {
    const component = create({
      usage: {
        counted: true, memory: 0, limits: {}, agents: [],
        confined: { user: true, files: false, network: false },
      },
    });
    await component.readUsage();

    expect(component.notHeld.length).toBe(2);
    expect(component.notHeld[0]).toContain('files');
  });

  it('asks for connections alone on the connections view', async () => {
    const asked: any[] = [];
    const component = create({}, asked);
    await component.select('connections');

    expect(asked[0].kinds).toEqual(['connection']);
    expect(asked[0].tally).toBeTrue();
    component.ngOnDestroy();
  });

  it('asks for what the filters narrow it to, the first page added up', async () => {
    const asked: any[] = [];
    const component = create({}, asked);
    component.filters = {
      kind: 'log', agent: 'agt_a', outcome: 'trouble', host: '', q: 'timeout', from: '', to: '',
    };
    await component.select('events');

    expect(asked[0]).toEqual({
      kinds: [], kind: 'log', agent: 'agt_a', outcome: 'trouble', host: '', text: 'timeout',
      before: null, limit: 100, tally: true, since: null, until: null,
    });
  });

  it('asks for the whole of the days chosen', async () => {
    const asked: any[] = [];
    const component = create({}, asked);
    component.filters = { ...component.filters, from: '2026-10-01', to: '2026-10-02' };
    await component.select('events');

    expect(asked[0].since).toBe(new Date(2026, 9, 1).getTime() / 1000);
    expect(asked[0].until).toBe(new Date(2026, 9, 3).getTime() / 1000);
  });

  it('reads the filters from its address, and opens on them', async () => {
    const asked: any[] = [];
    const component = create({}, asked, () => true, {
      view: 'connections',
      query: { host: 'api.example.com', outcome: 'refused', kind: 'log', from: 'not a day', q: 'gmail' },
    });
    await component.ngOnInit();

    // A kind is not the connections' to narrow, nor a day that is not one.
    expect(component.filters).toEqual({
      kind: '', agent: '', outcome: 'refused', host: 'api.example.com', q: 'gmail', from: '', to: '',
    });
    expect(component.text).toBe('gmail');
    expect(asked[0].host).toBe('api.example.com');
    component.ngOnDestroy();
  });

  it('puts a filter chosen in the address, keeping the others', async () => {
    const went: any[][] = [];
    const component = create({}, [], () => true, { view: 'events', went, query: { q: 'pip' } });
    await component.ngOnInit();
    component.choose({ key: 'outcome', value: 'trouble' });
    component.text = '  pandas ';
    component.applyTyped();
    component.clearFilters();

    expect(went).toEqual([
      ['/monitoring', 'events', { outcome: 'trouble', q: 'pip' }],
      ['/monitoring', 'events', { q: 'pandas' }],
      ['/monitoring', 'events', {}],
    ]);
    component.ngOnDestroy();
  });

  it('turns to the older page from where this one ended, and back', async () => {
    const asked: any[] = [];
    const component = create({
      events: {
        events: [{ at: 9, kind: 'log', agent: 'agt_a', name: 'Notes' }],
        next_before: 9,
        tally: {
          matching: 250, complete: true, reached: null, kinds: {}, outcomes: {},
          agents: [], hosts: [], sent: 0, received: 0,
        },
      },
    }, asked);
    await component.select('events');
    expect(component.range).toBe('1–1 of 250');

    await component.older();
    expect(asked[1].before).toBe(9);
    expect(asked[1].tally).toBeFalse();
    expect(component.page).toBe(1);
    expect(component.range).toBe('2–2 of 250');

    await component.newer();
    expect(asked[2].before).toBeNull();
    expect(component.page).toBe(0);
  });

  it('offers each choice with what choosing it would show', async () => {
    const component = create({
      events: {
        events: [], next_before: null,
        tally: {
          matching: 10, complete: false, reached: 5, kinds: { connection: 10 },
          outcomes: { ok: 7, refused: 2, unreached: 1 },
          agents: [{ agent: 'agt_a', name: 'Gmail', count: 10 }],
          hosts: [{ host: 'api.example.com', count: 6 }],
          sent: 1, received: 2,
        },
      },
    });
    await component.select('connections');
    const [outcome, agents, hosts] = component.columns;

    expect(outcome.items.map((item) => [item.name, item.count])).toEqual([
      ['Every connection', 10], ['Made', 7], ['Refused or not reached', 3],
    ]);
    expect(outcome.items[2].children!.map((item) => item.value)).toEqual(['refused', 'unreached']);
    expect(agents.items[0]).toEqual(jasmine.objectContaining({ value: 'agt_a', count: 10 }));
    expect(hosts.items[0]).toEqual(jasmine.objectContaining({ value: 'api.example.com', count: 6 }));
    expect(component.troubleLine).toBe('3 refused or not reached (30%)');
    expect(component.range).toBe('');
  });

  it('lists what an agent keeps, and closes it on a second press', async () => {
    const asked: any[] = [];
    const component = create({
      files: { files: [{ path: 'home/kept', bytes: 4 }], count: 1, bytes: 4 },
    }, asked);
    const agent = { agent: 'agt_a', name: 'Notes', memory: 1, cpu: 0, processes: 1 };

    await component.toggleFiles(agent);
    expect(asked).toEqual([{ files: 'agt_a' }]);
    expect(component.files!.count).toBe(1);

    await component.toggleFiles(agent);
    expect(component.filesOf).toBe('');
    expect(component.files).toBeNull();
  });

  it('opens on the agents for somebody who may not read events', async () => {
    const went: any[][] = [];
    const component = create({}, [], action => action === 'agents:monitor:usage', { view: 'events', went });
    await component.ngOnInit();

    expect(component.view).toBe('agents');
    expect(went).toEqual([['/monitoring']]);
    component.ngOnDestroy();
  });

  it('opens on the view its address names', async () => {
    const asked: any[] = [];
    const component = create({}, asked, () => true, { view: 'connections' });
    await component.ngOnInit();

    expect(component.view).toBe('connections');
    expect(asked[0].kinds).toEqual(['connection']);
    component.ngOnDestroy();
  });

  it('opens on the overview for whoever may see what agents did, added up', async () => {
    const component = create({}, [], () => true, {});
    await component.ngOnInit();

    expect(component.view).toBe('overview');
    expect(component.views.map((view) => view.value)).toEqual(['overview', 'tokens', 'agents', 'events', 'connections']);
    component.ngOnDestroy();
  });

  it('opens on the agents, and offers no overview, without the summary', async () => {
    const component = create({}, [], (action) => action !== 'agents:monitor:summary'
      && action !== 'agents:monitor:tokens', {});
    await component.ngOnInit();

    expect(component.view).toBe('agents');
    expect(component.views.map((view) => view.value)).toEqual(['agents', 'events', 'connections']);
    component.ngOnDestroy();
  });

  it('offers the tokens only to whoever may see them, and sends anyone else back', async () => {
    const went: any[][] = [];
    const component = create({}, [], (action) => action !== 'agents:monitor:tokens',
      { view: 'tokens', went });
    await component.ngOnInit();

    expect(component.views.map((view) => view.value)).not.toContain('tokens');
    expect(went).toEqual([['/monitoring']]);
    component.ngOnDestroy();

    const allowed = create({}, [], () => true, { view: 'tokens' });
    await allowed.ngOnInit();
    expect(allowed.view).toBe('tokens');
    allowed.ngOnDestroy();
  });

  it('says how old the numbers are', async () => {
    jasmine.clock().install();
    jasmine.clock().mockDate(new Date(2026, 9, 12, 11, 0, 0));
    const component = create();
    await component.select('agents');
    expect(component.updated).toBe('Updated just now');

    jasmine.clock().tick(3000);
    expect(component.updated).toBe('Updated 3 s ago');
    component.ngOnDestroy();
  });

  it('stops asking what agents use when it is left', async () => {
    jasmine.clock().install();
    let asked = 0;
    const component = create();
    (component as any).monitoring.usage = async () => {
      asked += 1;
      return { counted: true, agents: [], limits: {}, memory: 0, confined: {} };
    };
    await component.select('agents');
    expect(asked).toBe(1);

    jasmine.clock().tick(MonitoringComponent.EVERY_MS);
    expect(asked).toBe(2);

    component.ngOnDestroy();
    jasmine.clock().tick(MonitoringComponent.EVERY_MS * 3);
    expect(asked).toBe(2);
  });
});

describe('what was written down, in words', () => {
  it('says a connection that was made, and how much passed', () => {
    const made = {
      at: 1, kind: 'connection', name: 'Gmail', host: 'api.example.com', port: 443,
      allowed: true, reached: true, sent: 2048, received: 3 * 1024 * 1024, seconds: 0.4,
    };
    expect(monitorLabel(made)).toBe('Gmail → api.example.com:443');
    expect(monitorSummary(made)).toBe('2.0 KB sent · 3.0 MB received · 400 ms');
    expect(monitorChip(made)).toBe('made');
    expect(monitorTone(made)).toBe('ok');
  });

  it('says a connection that was refused, and why', () => {
    const refused = {
      at: 1, kind: 'connection', name: 'Gmail', host: 'elsewhere.example.org', port: 443,
      allowed: false, why: 'Gmail did not declare elsewhere.example.org',
    };
    expect(monitorSummary(refused)).toBe('Refused: Gmail did not declare elsewhere.example.org');
    expect(monitorChip(refused)).toBe('refused');
    expect(monitorTone(refused)).toBe('bad');
  });

  it('says what the helper was asked to do in words', () => {
    const job = { at: 1, kind: 'helper', job: 'stop', name: 'Browser', code: 0, seconds: 0.02 };
    expect(monitorLabel(job)).toBe("Helper ended every process of the agent's user");
    expect(monitorSummary(job)).toBe('Browser · 20 ms');
    expect(monitorKind(job)).toBe('Helper job');
    expect(monitorTone(job)).toBe('muted');
  });

  it('marks an agent ended for memory', () => {
    const ended = { at: 1, kind: 'memory', name: 'Browser', why: 'Browser was ended because…' };
    expect(monitorLabel(ended)).toBe('Browser was ended for memory');
    expect(monitorTone(ended)).toBe('bad');
  });

  it('tells an agent asked to leave from one that ended otherwise', () => {
    expect(monitorTone({ at: 1, kind: 'worker.ended', why: 'it was asked to leave' })).toBe('muted');
    expect(monitorTone({ at: 1, kind: 'worker.ended', why: 'the worker died' })).toBe('live');
  });

  it('makes one row of alike events next to each other, within a minute', () => {
    const groups = groupMonitor([
      { at: 100, kind: 'log', agent: 'agt_a', line: 'three' },
      { at: 99, kind: 'log', agent: 'agt_a', line: 'two' },
      { at: 98, kind: 'connection', agent: 'agt_a', host: 'a.example', allowed: true },
      { at: 97, kind: 'log', agent: 'agt_a', line: 'one' },
      { at: 10, kind: 'log', agent: 'agt_a', line: 'long ago' },
    ]);
    expect(groups.map((group) => group.events.length)).toEqual([2, 1, 1, 1]);
  });

  it('lists what a line holds, field by field', () => {
    expect(monitorFields({ at: 1, kind: 'program', asked: ['a', 'b'], code: 0 })).toEqual([
      { name: 'kind', value: 'program' },
      { name: 'asked', value: '["a","b"]' },
      { name: 'code', value: '0' },
    ]);
  });

  it('reads bytes as a person does', () => {
    expect(bytesLabel(512)).toBe('512 bytes');
    expect(bytesLabel(1536)).toBe('1.5 KB');
    expect(bytesLabel(1.5 * 1024 ** 3)).toBe('1.5 GB');
  });
});
