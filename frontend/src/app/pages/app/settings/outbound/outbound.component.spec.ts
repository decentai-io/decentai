import { OutboundComponent } from './outbound.component';

describe('OutboundComponent', () => {
  function sub(id: string, more: any = {}) {
    return {
      subscription_id: id, name: id, url: 'https://example.test/in', format: 'json', events: ['reply.ready'],
      include_reply: false, enabled: true, paused: false, failures: 0, ...more,
    } as any;
  }

  function create(service: any = {}) {
    return new OutboundComponent(
      {
        subscriptions: async () => ({ subscriptions: [sub('s')], events: [], formats: [], limit: 10 }),
        ...service,
      } as any,
      { can: () => true } as any,
      {} as any,
      { ask: async () => true } as any,
    );
  }

  it('reads the list', async () => {
    const component = create();
    await component.ngOnInit();

    expect(component.subscriptions.length).toBe(1);
    expect(component.foot).toBe('1 of 10');
  });

  it('says the list could not be read instead of showing it empty', async () => {
    const component = create({
      subscriptions: async () => ({ subscriptions: [], events: [], formats: [], limit: 0, error: 'Not allowed.' }),
    });
    await component.ngOnInit();

    expect(component.loadError).toBe('Not allowed.');
  });

  it('says how a notification stands beyond on and off', () => {
    const component = create();

    expect(component.state(sub('s'))).toBeNull();
    expect(component.state(sub('s', { failures: 3 }))?.word).toBe('Failing');
    expect(component.state(sub('s', { paused: true, failures: 20 }))?.word).toBe('Paused');
  });

  it('says what a test met', async () => {
    const component = create({ test: async () => ({ status: 404, error: 'Not found', delivered: false }) });
    await component.ngOnInit();

    await component.test(component.subscriptions[0]);

    expect(component.error).toContain('404');
  });
});
