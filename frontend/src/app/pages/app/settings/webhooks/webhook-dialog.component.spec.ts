import { WebhookDialogComponent } from './webhook-dialog.component';
import { curlExample, exampleBody, fieldNameProblem } from './webhooks-shared';

describe('WebhookDialogComponent', () => {
  const ref = () => ({ close: jasmine.createSpy('close'), disableClose: false }) as any;

  function create(data: any = null, service: any = {}) {
    return new WebhookDialogComponent(
      ref(),
      data,
      { create: async () => ({}), update: async () => ({}), ...service } as any,
      { list: async () => [] } as any,
      { can: () => true } as any,
    );
  }

  it('refuses a field name the backend would', () => {
    const fields: any[] = [{ name: 'ticket_id' }, { name: 'ticket_id' }];
    expect(fieldNameProblem('Ticket', [])).not.toBe('');
    expect(fieldNameProblem('1st', [])).not.toBe('');
    expect(fieldNameProblem('ticket_id', fields)).toBe('Named twice.');
    expect(fieldNameProblem('ticket_id', [fields[0]])).toBe('');
  });

  it('makes a call with every field, and the signature headers when one is required', () => {
    const fields: any[] = [{ name: 'title', type: 'text' }, { name: 'count', type: 'number' }];
    expect(JSON.parse(exampleBody(fields))).toEqual({ title: '…', count: 1 });

    const plain = curlExample('https://x.test/hooks/w1', 's3cret', fields, false);
    expect(plain).toContain('Authorization: Bearer s3cret');
    expect(plain).not.toContain('X-DecentAI-Signature');
    expect(curlExample('https://x.test/hooks/w1', 's3cret', fields, true)).toContain('X-DecentAI-Signature');
  });

  it('sends only what changed when editing', () => {
    const webhook: any = {
      webhook_id: 'w1', name: 'Tickets', prompt: 'File it', require_signature: false,
      fields: [{ name: 'title', type: 'text', required: true, description: '' }],
      chat: { exists: true, trust_level: 1 },
    };
    const dialog = create({ webhook });

    expect(dialog.draft()).toEqual({});
    dialog.name = 'Support tickets';
    dialog.trustLevel = 2;
    expect(dialog.draft()).toEqual({ name: 'Support tickets', trust_level: 2 });
  });

  it('leaves the chat settings to the defaults when none was chosen', () => {
    const dialog = create();
    dialog.name = 'Builds';
    dialog.prompt = 'Tell me what broke.';

    const draft = dialog.draft();
    expect(draft.trust_level).toBeUndefined();
    expect(draft.llm).toBeUndefined();
  });

  it('shows the secret once it is made, and asks before closing on one not copied', async () => {
    const made: any = { webhook_id: 'w1', name: 'Builds', url_path: '/hooks/w1', fields: [], require_signature: false };
    const dialog = create(null, { create: async () => ({ webhook: made, secret: 's3cret' }) });
    dialog.name = 'Builds';
    dialog.prompt = 'Tell me what broke.';

    await dialog.save();
    expect(dialog.made).toBe(made);
    expect(dialog.url).toContain('/hooks/w1');

    dialog.done();
    expect(dialog.warned).toBeTrue();
    expect(dialog.dialog.close).not.toHaveBeenCalled();
    dialog.done();
    expect(dialog.dialog.close).toHaveBeenCalledWith(made);
  });
});
