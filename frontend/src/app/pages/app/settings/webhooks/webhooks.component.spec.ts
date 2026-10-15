import { of } from 'rxjs';

import { WebhooksComponent } from './webhooks.component';

describe('WebhooksComponent', () => {
  function hook(id: string, more: any = {}) {
    return {
      webhook_id: id, name: id, prompt: 'Do it', fields: [], require_signature: false, enabled: true,
      chat_id: `chat_${id}`, shown: 'whk_ab', url_path: `/hooks/${id}`, chat: { exists: true }, ...more,
    } as any;
  }

  function sub(id: string, more: any = {}) {
    return {
      subscription_id: id, name: id, url: 'https://example.test/in', format: 'json', events: ['reply.ready'],
      include_reply: false, enabled: true, paused: false, failures: 0, ...more,
    } as any;
  }

  function create(service: any = {}, confirm: any = {}, opened: any = null) {
    return new WebhooksComponent(
      {
        list: async () => ({ webhooks: [hook('a')], limit: 25 }),
        subscriptions: async () => ({ subscriptions: [sub('s')], events: [], formats: [], limit: 10 }),
        ...service,
      } as any,
      { can: () => true } as any,
      { open: () => ({ afterClosed: () => of(opened) }) } as any,
      { ask: async () => true, ...confirm } as any,
    );
  }

  it('reads both lists', async () => {
    const component = create();
    await component.ngOnInit();

    expect(component.webhooks.length).toBe(1);
    expect(component.subscriptions.length).toBe(1);
    expect(component.foot).toBe('1 on · 24 available');
  });

  it('says a list could not be read instead of showing it empty', async () => {
    const component = create({ list: async () => ({ webhooks: [], limit: 0, error: 'Not allowed.' }) });
    await component.ngOnInit();

    expect(component.loadError).toBe('Not allowed.');
  });

  it('deletes a webhook only once that was agreed to, and says its chat stays', async () => {
    let removed = '';
    let answer = false;
    const component = create(
      { remove: async (id: string) => { removed = id; return { deleted: true }; } },
      { ask: async () => answer },
    );
    await component.ngOnInit();

    await component.remove(component.webhooks[0]);
    expect(removed).toBe('');
    answer = true;
    await component.remove(component.webhooks[0]);
    expect(removed).toBe('a');
    expect(component.webhooks).toEqual([]);
    expect(component.notice).toContain('chat');
  });

  it('says how a notification stands beyond on and off', () => {
    const component = create();

    expect(component.outState(sub('s'))).toBeNull();
    expect(component.outState(sub('s', { failures: 3 }))?.word).toBe('Failing');
    expect(component.outState(sub('s', { paused: true, failures: 20 }))?.word).toBe('Paused');
  });

  it('says what a test met', async () => {
    const component = create({ test: async () => ({ status: 404, error: 'Not found', delivered: false }) });
    await component.ngOnInit();

    await component.test(component.subscriptions[0]);

    expect(component.error).toContain('404');
  });
});
