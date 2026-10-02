import { InstallReviewComponent } from './install-review.component';

describe('InstallReviewComponent', () => {
  function manifest(overrides: any = {}) {
    return {
      agent: { id: 'jira', name: 'Jira', version: '1.0.0' },
      tools: [{
        id: 'main',
        functions: [{ id: 'run', description: 'Runs.', permission_level: 1 }],
      }],
      implementation: { dependencies: ['requests'] },
      resources: { secrets: [{ id: 'connection', label: 'Connection' }] },
      ...overrides,
    };
  }

  function create(entry: any, approved: any = null) {
    const component = new InstallReviewComponent();
    component.source = { source_id: 'one', name: 'Source one' } as any;
    component.entry = entry;
    component.approved = approved;
    return component;
  }

  it('reads a manifest as the facts an approval covers', () => {
    const component = create({ id: 'jira', manifest: manifest() });

    expect(component.functions.map((fn) => fn.name)).toEqual(['main.run']);
    expect(component.functions[0].level).toBe(1);
    expect(component.dependencies).toEqual(['requests']);
    expect(component.resources.map((group) => group.kind)).toEqual(['secrets']);
  });

  it('counts by level, because that is the decision being made', () => {
    const component = create({
      id: 'jira',
      manifest: manifest({
        tools: [{
          id: 'main',
          functions: [
            { id: 'a', permission_level: 0 },
            { id: 'b', permission_level: 0 },
            { id: 'c', permission_level: 3 },
          ],
        }],
      }),
    });

    expect(component.tally).toEqual([
      { name: 'read', count: 2 },
      { name: 'external', count: 1 },
    ]);
  });

  it('has nothing to say about changes on a first install', () => {
    const component = create({ id: 'jira', manifest: manifest() });

    expect(component.isUpdate).toBeFalse();
    expect(component.changes).toEqual([]);
  });

  it('describes an update as what changes, not as a version number', () => {
    const offered = manifest({
      agent: { id: 'jira', name: 'Jira', version: '1.0.0' },
      tools: [{
        id: 'main',
        functions: [
          { id: 'run', description: 'Runs.', permission_level: 1 },
          { id: 'delete', description: 'Deletes.', permission_level: 3 },
        ],
      }],
      implementation: { dependencies: ['requests', 'boto3'] },
    });
    const approved = {
      manifest: {
        agent: { id: 'jira', version: '0.9.0' },
        tools: [{
          id: 'main',
          functions: [{ id: 'run', description: 'Runs.', permission_level: 0 }],
        }],
        implementation: { dependencies: ['requests'] },
        resources: {},
      },
    };

    const component = create(
      { id: 'jira', installed_version: '0.9.0', manifest: offered }, approved);

    expect(component.isUpdate).toBeTrue();
    const changes = component.changes;
    expect(changes.some((line) => line.includes('New function: main.delete'))).toBeTrue();
    expect(changes.some((line) => line.includes('main.run: permission read → change'))).toBeTrue();
    expect(changes.some((line) => line.includes('New package: boto3'))).toBeTrue();
    expect(changes.some((line) => line.includes('New secret: connection'))).toBeTrue();
  });

  it('says when a function was taken away', () => {
    const approved = {
      manifest: {
        agent: { id: 'jira', version: '0.9.0' },
        tools: [{
          id: 'main',
          functions: [
            { id: 'run', permission_level: 1 },
            { id: 'gone', permission_level: 1 },
          ],
        }],
        implementation: { dependencies: ['requests'] },
        resources: { secrets: [{ id: 'connection' }] },
      },
    };
    const component = create(
      { id: 'jira', installed_version: '0.9.0', manifest: manifest() }, approved);

    expect(component.changes).toEqual(['Function removed: main.gone']);
  });
});
