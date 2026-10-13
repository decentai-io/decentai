import { headingsOf, searchGuide, slugOf } from './guide-text';

const chapter = { id: 'chats', title: 'Chats', summary: 'Messages and results', file: '02-chats.md' };
const text = [
  '# Chats',
  'Most of the work happens in a chat.',
  '## Sending a message',
  'Type it and press Enter. A file can ride along with it.',
  '```',
  '## not a heading',
  '```',
  '### Files',
  'A **file** you attach is kept under Files.',
  '## Sending a message',
  'Said twice, to see its address.',
].join('\n');

describe('the guide as text', () => {
  it('gives a heading an address made of its words', () => {
    expect(slugOf('Who can see it?')).toBe('who-can-see-it');
  });

  it('lists the headings of a chapter, not the lines of its code', () => {
    expect(headingsOf(text)).toEqual([
      { slug: 'sending-a-message', text: 'Sending a message', level: 2 },
      { slug: 'files', text: 'Files', level: 3 },
      // The second of the same name is told apart.
      { slug: 'sending-a-message-2', text: 'Sending a message', level: 2 },
    ]);
  });

  it('finds a sentence, under the heading it sits below', () => {
    const found = searchGuide('attach', [chapter], { chats: text });
    expect(found.length).toBe(1);
    expect(found[0].heading?.slug).toBe('files');
    // As it is shown: without the markdown's marks.
    expect(found[0].sentence).toBe('A file you attach is kept under Files.');
  });

  it('finds a chapter by what it is about, and asks for two letters at least', () => {
    expect(searchGuide('results', [chapter], { chats: text })[0]).toEqual(
      { chapter, heading: null, sentence: 'Messages and results' });
    expect(searchGuide('a', [chapter], { chats: text })).toEqual([]);
  });
});
