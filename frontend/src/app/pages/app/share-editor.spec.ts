import { ShareEditor } from './share-editor';

describe('ShareEditor', () => {
  const ME = 'usr_me';
  const editor = new ShareEditor(() => ({ user_id: ME, groups: [] } as any));

  it('says who sees a thing of mine, by name', () => {
    expect(editor.chip({ groups: [], users: [ME] }, ME).word).toBe('Private');
    expect(editor.chip({ groups: ['everyone'], users: [] }, ME, { groups: ['Everyone'] }).word).toBe('Everyone');
    expect(editor.chip({ groups: ['g1', 'g2'], users: [ME] }, ME, { groups: ['Design', 'Ops'], users: ['Me'] }))
      .toEqual({ word: 'Design, Ops', names: 'Design, Ops' });
    expect(editor.chip({ groups: [], users: [ME, 'u1', 'u2', 'u3'] }, ME, { users: ['Me', 'Dana', 'Sam', 'Lee'] }))
      .toEqual({ word: 'Dana and 2 others', names: 'Dana, Sam, Lee' });
  });

  it('says whose somebody else\'s thing is, which is what the reader does not know', () => {
    expect(editor.chip({ groups: ['g1'], users: ['u1'] }, 'u1', { groups: ['Design'], users: ['Dana'] }, 'Dana'))
      .toEqual({ word: 'Shared by Dana', names: 'With Design' });
    // A listing from before names were carried still reads.
    expect(editor.chip({ groups: ['everyone'], users: [] }, 'u1').word).toBe('Shared by a colleague');
  });

  it('names a group or a person that is gone as gone, not as nothing', () => {
    expect(editor.chip({ groups: ['g9'], users: [ME] }, ME, { groups: [''], users: ['Me'] }).word)
      .toBe('a deleted group');
    expect(editor.chip({ groups: [], users: [ME, 'u9'] }, ME, { users: ['Me', ''] }).word)
      .toBe('someone who left');
  });
});
