import { of } from 'rxjs';

import { WebhooksComponent } from './webhooks.component';

describe('WebhooksComponent', () => {
  function hook(id: string, more: any = {}) {
    return {
      webhook_id: id, name: id, prompt: 'Do it', fields: [], require_signature: false, enabled: true,
      chat_id: `chat_${id}`, shown: 'whk_ab', url_path: `/hooks/${id}`, chat: { exists: true },
      owner_id: 'u_me', viewers: { groups: [], users: [] }, role: 'owner', ...more,
    } as any;
  }

  function create(service: any = {}, confirm: any = {}) {
    const router = { events: of(), navigate: jasmine.createSpy('navigate').and.resolveTo(true) };
    const component = new WebhooksComponent(
      {
        list: async () => ({
          webhooks: [hook('a')],
          shared: [hook('b', { owner_id: 'u_ann', role: 'viewer', shown: undefined, chat: undefined })],
          limit: 25,
        }),
        ...service,
      } as any,
      {
        get: async () => ({ user_id: 'u_me', groups: [] }),
        peers: async () => [{ user_id: 'u_ann', user_name: 'Ann', email: 'ann@x.test' }],
      } as any,
      { can: () => true } as any,
      router as any,
      { snapshot: { firstChild: null } } as any,
      {} as any,
      { ask: async () => true, ...confirm } as any,
    );
    return { component, router };
  }

  it('reads its own webhooks and those shared, with whose they are', async () => {
    const { component } = create();
    await component.ngOnInit();

    expect(component.webhooks.length).toBe(1);
    expect(component.shared.length).toBe(1);
    expect(component.ownerName(component.shared[0])).toBe('Ann');
    expect(component.foot).toBe('1 on · 1 of 25');
  });

  it('says the list could not be read instead of showing it empty', async () => {
    const { component } = create({ list: async () => ({ webhooks: [], shared: [], limit: 0, error: 'Not allowed.' }) });
    await component.ngOnInit();

    expect(component.loadError).toBe('Not allowed.');
  });

  it('deletes a webhook only once that was agreed to, and says its chat stays', async () => {
    let removed = '';
    let answer = false;
    const { component } = create(
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

  it('shares with viewers, never naming the owner among them', async () => {
    let sent: any = null;
    const { component } = create({
      share: async (_id: string, viewers: any) => { sent = viewers; return { webhook: hook('a', { viewers }) }; },
    });
    await component.ngOnInit();

    component.openShare(component.webhooks[0]);
    component.share.mode = 'people';
    component.share.toggleUser('u_ann');
    await component.saveShare();

    expect(sent).toEqual({ groups: [], users: ['u_ann'] });
    expect(component.webhooks[0].viewers.users).toEqual(['u_ann']);
  });

  it('hands over only once that was agreed to, then leaves the webhook', async () => {
    let to = '';
    let answer = false;
    const { component, router } = create(
      { transfer: async (_id: string, userId: string) => { to = userId; return {}; } },
      { ask: async () => answer },
    );
    await component.ngOnInit();
    const ann = { user_id: 'u_ann', user_name: 'Ann', email: 'ann@x.test' };

    component.requestTransfer(component.webhooks[0]);
    await component.transfer(ann);
    expect(to).toBe('');
    answer = true;
    await component.transfer(ann);
    expect(to).toBe('u_ann');
    expect(router.navigate).toHaveBeenCalledWith(['/ai/webhooks']);
  });
});
