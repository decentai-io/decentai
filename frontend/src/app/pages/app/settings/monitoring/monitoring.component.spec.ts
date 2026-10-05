import { MonitoringComponent } from './monitoring.component';
import {
  bytesLabel, monitorChip, monitorLabel, monitorSummary, monitorTone,
} from './monitor-words';

describe('MonitoringComponent', () => {
  function create(answers: any = {}, asked: any[] = [], can = (_: string) => true) {
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
    component.kind = 'log';
    await component.select('connections');

    expect(asked[0].kinds).toEqual(['connection']);
    component.ngOnDestroy();
  });

  it('asks for the kind and the agent that were chosen', async () => {
    const asked: any[] = [];
    const component = create({}, asked);
    component.kind = 'log';
    component.agent = 'agt_a';
    await component.select('events');

    expect(asked[0]).toEqual({ kinds: ['log'], agent: 'agt_a', before: undefined, limit: 100 });
  });

  it('reads the page after the one it has, from where that one ended', async () => {
    const asked: any[] = [];
    const component = create({
      events: { events: [{ at: 9, kind: 'log', agent: 'agt_a', name: 'Notes' }], next_before: 9 },
    }, asked);
    await component.select('events');
    await component.loadMore();

    expect(asked[1].before).toBe(9);
    expect(component.events.length).toBe(2);
    expect(component.agentChoices).toEqual([{ value: 'agt_a', label: 'Notes' }]);
  });

  it('shows of the connections only those refused or not reached, when asked', async () => {
    const component = create({
      events: {
        next_before: null,
        events: [
          { at: 3, kind: 'connection', allowed: true, reached: true },
          { at: 2, kind: 'connection', allowed: false, why: 'not declared' },
          { at: 1, kind: 'connection', allowed: true, reached: false },
        ],
      },
    });
    await component.select('connections');
    component.refusedOnly = true;

    expect(component.shown.map(e => e.at)).toEqual([2, 1]);
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
    const component = create({}, [], action => action === 'agents:monitor:usage');
    component.initialView = 'events';
    await component.ngOnInit();

    expect(component.view).toBe('agents');
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

  it('reads bytes as a person does', () => {
    expect(bytesLabel(512)).toBe('512 bytes');
    expect(bytesLabel(1536)).toBe('1.5 KB');
    expect(bytesLabel(1.5 * 1024 ** 3)).toBe('1.5 GB');
  });
});
