import { SkillViewComponent } from './skill-view.component';

describe('SkillViewComponent', () => {
  const skill = {
    resource_ref: 'sk_1', keys: { title: 'Writing', summary: 'When writing.' },
    owner: { groups: [], users: ['u1'] }, created_by: 'u1', updated_at: '2026-10-01T00:00:00Z',
  } as any;

  async function read(answer: any) {
    const component = new SkillViewComponent({ get: async () => answer } as any);
    component.skill = skill;
    component.ngOnChanges({ skill: { previousValue: undefined } } as any);
    await Promise.resolve();
    await Promise.resolve();
    return component;
  }

  it('reads the skill itself, with its headings under the panel\'s own', async () => {
    const component = await read({ ...skill, values: { body: '# Rules\n\nLead with the point.' } });
    expect(component.body).toBe('### Rules\n\nLead with the point.');
    expect(component.loading).toBeFalse();
  });

  it('says a body cannot be read rather than showing it as empty', async () => {
    const component = await read({ ...skill, values: {}, unreadable: true });
    expect(component.unreadable).toBeTrue();
    expect(component.body).toBe('');
  });

  it('says so when the skill could not be fetched', async () => {
    const component = await read(null);
    expect(component.problem).toContain('could not be read');
  });
});
