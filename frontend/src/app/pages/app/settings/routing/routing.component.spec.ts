import { RoutingComponent } from './routing.component';

describe('RoutingComponent', () => {
  function create(calls: Record<string, any> = {}) {
    return new RoutingComponent(
      {
        get: async () => ({ routing: {
          embedding_connection_id: '', embedding_model: '', threshold: 15,
          shortlist: 15, candidates: 50, rerank: true, open_max: 8 } }),
        update: async (routing: any) => {
          calls['routing'] = { ...routing };
          return { routing };
        },
      } as any,
      { list: async () => [] } as any,
      { can: () => true } as any,
    );
  }

  it('saves the embedding model with the routing numbers', async () => {
    const calls: Record<string, any> = {};
    const component = create(calls);
    await component.load();
    expect(component.draft.dirty).toBeFalse();

    // A provider alone does not say which of its models embeds.
    component.chooseEmbedding({ connectionId: 'llm_OpenAI', model: '' });
    expect(component.embeddingError).toBe('Choose the embedding model.');
    await component.save();
    expect(calls['routing']).toBeUndefined();

    component.chooseEmbedding({ connectionId: 'llm_OpenAI', model: 'text-embedding-3-large' });
    await component.save();
    expect(calls['routing']).toEqual(jasmine.objectContaining({
      embedding_connection_id: 'llm_OpenAI', embedding_model: 'text-embedding-3-large',
      threshold: 15 }));
    expect(component.unsavedChangesTo()).toBe('');
  });

  it('will not save a number outside its range, and says which', async () => {
    const calls: Record<string, any> = {};
    const component = create(calls);
    await component.load();

    component.draft.value.shortlist = 0;
    expect(component.numberError(component.numbers[1])).toBe('A whole number from 1 to 100.');
    await component.save();

    expect(calls['routing']).toBeUndefined();
    expect(component.saveError).toContain('Agents listed');
  });

  it('goes back to what is saved on Discard', async () => {
    const component = create();
    await component.load();
    component.draft.value.threshold = 99;

    component.discard();

    expect(component.draft.value.threshold).toBe(15);
    expect(component.draft.dirty).toBeFalse();
  });
});
