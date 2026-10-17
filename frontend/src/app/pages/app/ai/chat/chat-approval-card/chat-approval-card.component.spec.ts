import { ChatApprovalCardComponent } from './chat-approval-card.component';

describe('ChatApprovalCardComponent skill card', () => {
  const skill = {
    title: 'Jira bulk create and resolve',
    summary: 'When many Jira tickets are to be created and resolved at once.',
    body: 'Run:\n\n```javascript\nawait fetch("/rest/api/2/issue");\n```\n',
  };

  function card(status = 'pending') {
    const component = new ChatApprovalCardComponent();
    component.approval = {
      approval_id: 'apr_1', kind: 'question', expects: 'skill', status, skill,
      action: { agent_id: '', function: 'save_skill', inputs_preview: {} },
    };
    return component;
  }

  it('is a skill card that shows the body whole while it waits', () => {
    const component = card();
    expect(component.isSkill).toBeTrue();
    expect(component.isCode).toBeFalse();
    expect(component.skill.body).toBe(skill.body);
    expect(component.skillLines).toBe(5);
    expect(component.skillShown).toBeTrue();
    component.toggleSkill();
    expect(component.skillShown).toBeFalse();
  });

  it('is answered save or decline, once, while it waits', () => {
    const component = card();
    const emitted: any[] = [];
    component.decided.subscribe((event) => emitted.push(event));
    component.decideSkill('save');
    expect(emitted).toEqual([{ approval_id: 'apr_1', decision: 'answer', skill: 'save' }]);

    const decided = card('answered');
    decided.decided.subscribe((event) => emitted.push(event));
    decided.decideSkill('decline');
    expect(emitted.length).toBe(1);
    expect(decided.skillShown).toBeFalse();
  });

  it('says what became of it', () => {
    const component = card();
    component.decideSkill('decline');
    component.approval = { ...component.approval, status: 'answered' };
    expect(component.skillStatusLabel).toBe('Not saved');
  });
});
