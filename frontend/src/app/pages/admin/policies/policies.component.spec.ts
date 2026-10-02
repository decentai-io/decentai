import { PoliciesComponent } from './policies.component';

describe('PoliciesComponent', () => {
  const CATALOG = [
    { service: 'iam:user', label: 'Users', actions: [
      { action: 'iam:user:list', description: 'View users' },
      { action: 'iam:user:delete', description: 'Remove a user' },
    ] },
    { service: 'iam:group', label: 'Groups', actions: [
      { action: 'iam:group:list', description: 'View groups' },
    ] },
  ];

  function createComponent() {
    const component = new PoliciesComponent(
      { list: async () => [] } as any,
      { can: () => true, catalog: CATALOG, ensureLoaded: async () => undefined } as any,
    );
    component.catalog = CATALOG;
    return component;
  }

  function build(component: PoliciesComponent): any {
    return (component as any).buildPermissions();
  }

  it('writes the ticked actions as one statement', () => {
    const component = createComponent();
    component.startCreate();
    component.selected.add('iam:user:list');
    component.selected.add('iam:group:list');

    const document = build(component);
    expect(document.statements.length).toBe(1);
    expect(document.statements[0].actions)
      .toEqual(['iam:group:list', 'iam:user:list']);
    expect('constraints' in document.statements[0]).toBeFalse();
  });

  it('reads a plain document back into the grid', () => {
    const component = createComponent();
    component.startEdit({
      policy_id: 'p1', name: 'Readers', description: '',
      permissions: { statements: [
        { effect: 'Allow', actions: ['iam:user:list'], resources: ['*'] },
      ] },
    } as any);

    expect(component.advanced).toBeFalse();
    expect(component.selected.has('iam:user:list')).toBeTrue();
  });

  it('falls back to the document for a Deny it cannot draw', () => {
    const component = createComponent();
    component.startEdit({
      policy_id: 'p2', name: 'Blocked', description: '',
      permissions: { statements: [
        { effect: 'Deny', actions: ['iam:user:delete'], resources: ['*'] },
      ] },
    } as any);

    expect(component.advanced).toBeTrue();
    expect(component.selected.size).toBe(0);
  });

  it('falls back to the document for a pattern wider than one service', () => {
    // `iam:*` grants every box under two services and is none of them:
    // drawn as a grid it would show nothing ticked.
    const component = createComponent();
    component.startEdit({
      policy_id: 'p3', name: 'All of IAM', description: '',
      permissions: { statements: [
        { effect: 'Allow', actions: ['iam:*'], resources: ['*'] },
      ] },
    } as any);

    expect(component.advanced).toBeTrue();
  });

  it('draws a whole service, and everything, in the grid', () => {
    const component = createComponent();
    component.startEdit({
      policy_id: 'p4', name: 'Users', description: '',
      permissions: { statements: [
        { effect: 'Allow', actions: ['iam:user:*'], resources: ['*'] },
      ] },
    } as any);

    expect(component.advanced).toBeFalse();
    expect(component.isActionOn('iam:user', 'iam:user:delete')).toBeTrue();
    expect(component.isActionOn('iam:group', 'iam:group:list')).toBeFalse();
  });

  it('names the services a policy grants by their labels', () => {
    const component = createComponent();

    expect(component.summarise({
      policy_id: 'p5', name: 'Readers', description: '',
      permissions: { statements: [
        { effect: 'Allow', actions: ['iam:user:list', 'iam:group:list'], resources: ['*'] },
      ] },
    } as any)).toBe('Users, Groups');
  });
});
