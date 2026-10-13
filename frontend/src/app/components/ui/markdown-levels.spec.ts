import { headingsDown } from './markdown-levels';

describe('headingsDown', () => {
  it('moves headings down, leaves code alone, and stops at the sixth level', () => {
    expect(headingsDown('# Jira\n\n## Setup\n\n```\n# a comment\n```\n', 2))
      .toBe('### Jira\n\n#### Setup\n\n```\n# a comment\n```\n');
    expect(headingsDown('##### Deep', 2)).toBe('###### Deep');
    expect(headingsDown('#hashtag is not a heading', 2)).toBe('#hashtag is not a heading');
  });
});
