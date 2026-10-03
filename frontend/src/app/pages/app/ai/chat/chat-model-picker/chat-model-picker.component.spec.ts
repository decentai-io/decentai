import { SimpleChange } from '@angular/core';

import { ChatModelPickerComponent } from './chat-model-picker.component';

describe('ChatModelPickerComponent', () => {
  const connections = [
    { resource_ref: 'llm_anthropic', name: 'Anthropic',
      keys: { provider: 'anthropic', model: 'claude-sonnet-5', endpoint: 'https://api.anthropic.com' } },
    { resource_ref: 'llm_home', name: 'Home',
      keys: { provider: 'openai_compatible', model: 'llama3.3', endpoint: 'http://home/v1' } },
  ] as any[];

  const served: Record<string, any[]> = {
    llm_anthropic: [
      { id: 'claude-opus-5-5', name: 'Claude Opus 5.5', kind: 'chat', efforts: ['low', 'high', 'xhigh'] },
      { id: 'claude-sonnet-5', name: 'Claude Sonnet 5', kind: 'chat', efforts: ['low', 'high'] },
      { id: 'claude-haiku-4-5', name: 'Claude Haiku 4.5', kind: 'chat' },
    ],
    llm_home: [{ id: 'llama3.3', name: 'llama3.3', kind: 'chat' }],
  };

  function create(llm: any = null) {
    localStorage.removeItem(ChatModelPickerComponent.RECENT_KEY);
    const component = new ChatModelPickerComponent(
      { list: async () => connections,
        models: async (ref: string) => served[ref] ?? [] } as any,
      { nativeElement: document.createElement('div') } as any,
    );
    component.llm = llm;
    const chosen: any[] = [];
    component.chosen.subscribe((block) => chosen.push(block));
    return { component, chosen };
  }

  const settle = () => new Promise((resolve) => setTimeout(resolve));

  it('names the model as its provider does, once a chat has one', async () => {
    const { component } = create({ provider: 'anthropic', model: 'claude-sonnet-5', secret_ref: 'llm_anthropic' });
    // Until the list is read the id is what there is to say.
    expect(component.label).toBe('claude-sonnet-5');
    component.ngOnChanges({ llm: new SimpleChange(null, component.llm, true) });
    await settle();
    expect(component.label).toBe('Claude Sonnet 5');
    expect(component.efforts).toEqual(['low', 'high']);
  });

  it('asks for a model where there is none', () => {
    const { component } = create();
    expect(component.label).toBe('Choose a model');
    expect(component.efforts).toEqual([]);
  });

  it('lists every model of every provider, narrowed by what is typed', async () => {
    const { component } = create();
    component.toggle();
    await settle();
    expect(component.open).toBeTrue();
    expect(component.visible.map((g) => [g.connection.name, g.models.length]))
      .toEqual([['Anthropic', 3], ['Home', 1]]);

    component.query = 'opus';
    expect(component.visible.map((g) => g.models.map((m) => m.id)))
      .toEqual([['claude-opus-5-5']]);
    // A provider's name finds its models; several words all count.
    component.query = 'home';
    expect(component.visible.map((g) => g.connection.name)).toEqual(['Home']);
    component.query = 'anthropic haiku';
    expect(component.visible.map((g) => g.models.map((m) => m.id)))
      .toEqual([['claude-haiku-4-5']]);
    component.query = 'nothing-like-this';
    expect(component.visible).toEqual([]);
  });

  it('chooses a model as the block a chat carries, and closes', async () => {
    const { component, chosen } = create();
    component.toggle();
    await settle();
    const group = component.visible[0];
    component.choose(group.connection, group.models[0]);
    expect(chosen).toEqual([{
      provider: 'anthropic', model: 'claude-opus-5-5',
      secret_ref: 'llm_anthropic', endpoint: 'https://api.anthropic.com' }]);
    expect(component.open).toBeFalse();
  });

  it('keeps the effort only where the model picked takes that word', async () => {
    const { component, chosen } = create({
      provider: 'anthropic', model: 'claude-opus-5-5', secret_ref: 'llm_anthropic',
      reasoning_effort: 'xhigh' });
    component.toggle();
    await settle();
    const [opus, sonnet, haiku] = component.visible[0].models;
    const anthropic = component.visible[0].connection;

    component.choose(anthropic, sonnet);
    expect(chosen[0].reasoning_effort).toBeUndefined();
    component.llm = { ...chosen[0], reasoning_effort: 'high' };
    component.choose(anthropic, opus);
    expect(chosen[1].reasoning_effort).toBe('high');
    component.choose(anthropic, haiku);
    expect(chosen[2].reasoning_effort).toBeUndefined();
  });

  it('changes how hard the chosen model thinks without changing the model', async () => {
    const { component, chosen } = create({
      provider: 'anthropic', model: 'claude-sonnet-5', secret_ref: 'llm_anthropic' });
    component.ngOnChanges({ llm: new SimpleChange(null, component.llm, true) });
    await settle();
    component.chooseEffort('high');
    expect(chosen).toEqual([{
      provider: 'anthropic', model: 'claude-sonnet-5', secret_ref: 'llm_anthropic',
      endpoint: 'https://api.anthropic.com', reasoning_effort: 'high' }]);
    component.chooseEffort('');
    expect(chosen[1].reasoning_effort).toBeUndefined();
    expect(component.effortLabel('xhigh')).toBe('Extra high');
  });

  it('shows the models picked lately first, while nothing is typed', async () => {
    const { component } = create();
    component.toggle();
    await settle();
    expect(component.recent).toEqual([]);

    const anthropic = component.visible[0];
    const home = component.visible[1];
    component.choose(anthropic.connection, anthropic.models[2]);
    component.choose(home.connection, home.models[0]);
    component.choose(anthropic.connection, anthropic.models[2]);
    expect(component.recent.map((entry) => entry.model.id))
      .toEqual(['claude-haiku-4-5', 'llama3.3']);

    component.query = 'opus';
    expect(component.recent).toEqual([]);
    localStorage.removeItem(ChatModelPickerComponent.RECENT_KEY);
  });

  it('does not open while it is locked, and closes on Escape', async () => {
    const { component } = create();
    component.disabled = true;
    component.toggle();
    expect(component.open).toBeFalse();

    component.disabled = false;
    component.toggle();
    expect(component.open).toBeTrue();
    component.onEscape();
    expect(component.open).toBeFalse();
  });
});
