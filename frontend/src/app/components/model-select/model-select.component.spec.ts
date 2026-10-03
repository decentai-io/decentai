import { SimpleChange } from '@angular/core';

import { ModelSelectComponent } from './model-select.component';

describe('ModelSelectComponent', () => {
  const connections = [
    { resource_ref: 'llm_openai', name: 'OpenAI',
      keys: { provider: 'openai', model: 'gpt-5', endpoint: 'https://api.openai.com/v1' } },
    { resource_ref: 'llm_home', name: 'Home',
      keys: { provider: 'openai_compatible', model: 'llama3.3', endpoint: 'http://home/v1' } },
  ] as any[];

  const served: Record<string, any[]> = {
    'llm_openai/chat': [
      { id: 'gpt-6', name: 'GPT-6', kind: 'chat' },
      { id: 'gpt-5', name: 'GPT-5', kind: 'chat' },
    ],
    'llm_openai/embedding': [
      { id: 'text-embedding-3-large', name: 'text-embedding-3-large', kind: 'embedding' },
    ],
  };

  function create(kind: any = 'chat') {
    const asked: string[] = [];
    const component = new ModelSelectComponent({
      models: async (connectionId: string, of: string) => {
        asked.push(`${connectionId}/${of}`);
        return served[`${connectionId}/${of}`] ?? [];
      },
    } as any);
    component.connections = connections;
    component.kind = kind;
    const chosen: any[] = [];
    component.changed.subscribe((choice) => chosen.push(choice));
    return { component, chosen, asked };
  }

  const settle = () => new Promise((resolve) => setTimeout(resolve));

  it('starts a chat’s choice with the model the connection starts with', async () => {
    const { component, chosen } = create();
    component.chooseConnection('llm_openai');
    await settle();
    expect(component.models.map((m) => m.id)).toEqual(['gpt-6', 'gpt-5']);
    expect(chosen).toEqual([{ connectionId: 'llm_openai', model: 'gpt-5' }]);
    expect(component.typing).toBeFalse();
  });

  it('offers only the models of the kind asked for, and starts with the first', async () => {
    const { component, chosen, asked } = create('embedding');
    component.chooseConnection('llm_openai');
    await settle();
    expect(asked).toEqual(['llm_openai/embedding']);
    expect(chosen).toEqual([{ connectionId: 'llm_openai', model: 'text-embedding-3-large' }]);
  });

  it('asks for the model by name where the provider lists none', async () => {
    const { component, chosen } = create('embedding');
    component.chooseConnection('llm_home');
    await settle();
    expect(component.typing).toBeTrue();
    expect(chosen).toEqual([{ connectionId: 'llm_home', model: '' }]);
    component.typeModel(' nomic-embed-text ');
    expect(chosen[1]).toEqual({ connectionId: 'llm_home', model: 'nomic-embed-text' });
  });

  it('shows a saved model the list does not have as typed, without choosing another', async () => {
    const { component, chosen } = create();
    component.connectionId = 'llm_openai';
    component.model = 'a-deployment-of-mine';
    component.ngOnChanges({ connectionId: new SimpleChange('', 'llm_openai', true) });
    await settle();
    expect(component.typing).toBeTrue();
    expect(component.model).toBe('a-deployment-of-mine');
    // Reading what was saved is not a choice.
    expect(chosen).toEqual([]);

    component.pickFromList();
    expect(component.typing).toBeFalse();
    expect(chosen).toEqual([{ connectionId: 'llm_openai', model: 'gpt-6' }]);
  });

  it('chooses nothing when no provider is chosen', async () => {
    const { component, chosen } = create();
    component.chooseConnection('llm_openai');
    await settle();
    component.chooseConnection('');
    expect(component.models).toEqual([]);
    expect(chosen[1]).toEqual({ connectionId: '', model: '' });
  });
});
