import { SourceFormComponent } from './source-form.component';

describe('SourceFormComponent', () => {
  function create(source: any = null, suggestedUrl = ''): SourceFormComponent {
    const component = new SourceFormComponent();
    component.source = source;
    component.suggestedUrl = suggestedUrl;
    component.ngOnChanges({ source: { currentValue: source } } as any);
    return component;
  }

  it('starts empty when adding, and filled when editing', () => {
    expect(create().draft).toEqual({ name: '', url: '', ref: '' });

    const editing = create({
      source_id: 'one', name: 'Platform agents',
      url: 'https://example.test/one.git', ref: 'main',
      has_credential: true, credential_user: 'deploy-bot',
    });
    expect(editing.editing).toBeTrue();
    expect(editing.draft).toEqual({
      name: 'Platform agents',
      url: 'https://example.test/one.git',
      ref: 'main',
    });
    // The stored credential shows its username and an empty token box —
    // the token is write-only, so there is nothing to prefill.
    expect(editing.isPrivate).toBeTrue();
    expect(editing.username).toBe('deploy-bot');
    expect(editing.token).toBe('');
  });

  it('demands the token only when none is stored yet', () => {
    const fresh = create();
    fresh.isPrivate = true;
    expect(fresh.missingToken).toBeTrue();
    fresh.token = 's3cret';
    expect(fresh.missingToken).toBeFalse();

    // Editing a private source: blank keeps the stored token.
    const editing = create({ has_credential: true, credential_user: '' });
    expect(editing.missingToken).toBeFalse();
  });

  it('unticking private on a stored credential clears it explicitly', () => {
    const editing = create({
      name: 'X', url: 'https://example.test/one.git', ref: 'main',
      has_credential: true, credential_user: 'bot',
    });
    const emitted: any[] = [];
    editing.save.subscribe((draft: any) => emitted.push(draft));

    editing.isPrivate = false;
    editing.submit();
    // null is the instruction to clear; undefined would leave it alone.
    expect(emitted[0].credential).toBeNull();
  });

  it('offers the suggested catalog only when the deployment sets one', () => {
    const bare = create();
    bare.useSuggested();
    // Nothing configured: calling it anyway must not fill the form with
    // an empty URL.
    expect(bare.draft.url).toBe('');

    const offered = create(null, 'https://example.test/decentai.git');
    offered.useSuggested();
    expect(offered.draft.url).toBe('https://example.test/decentai.git');
    expect(offered.draft.ref).toBe('main');
    expect(offered.draft.name).toBe('Reference agents');
  });

  it('trims what it emits, and will not emit without a URL', () => {
    const component = create();
    const emitted: any[] = [];
    component.save.subscribe((draft: any) => emitted.push(draft));

    component.submit();
    expect(emitted.length).toBe(0);

    component.draft = {
      name: '  Platform agents  ',
      url: '  https://example.test/one.git  ',
      ref: '  main  ',
    };
    component.submit();

    expect(emitted).toEqual([{
      name: 'Platform agents',
      url: 'https://example.test/one.git',
      ref: 'main',
      credential: undefined,
      // Private by default, like everything a person makes — sharing
      // is a choice, never a side effect of saving.
      owner: { groups: [], users: [] },
    }]);
  });

  it('stays quiet while a save is already in flight', () => {
    const component = create();
    const emitted: any[] = [];
    component.save.subscribe((draft: any) => emitted.push(draft));

    component.draft.url = 'https://example.test/one.git';
    component.busy = true;
    component.submit();

    expect(emitted.length).toBe(0);
  });
});
