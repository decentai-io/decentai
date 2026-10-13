import { showValue } from './record-values';

describe('showValue', () => {
  it('shows a value as what it is', () => {
    expect(showValue('').kind).toBe('empty');
    expect(showValue(null).kind).toBe('empty');
    expect(showValue('Ops meeting')).toEqual({ kind: 'text', text: 'Ops meeting' });
    expect(showValue(2)).toEqual({ kind: 'number', text: '2' });
    expect(showValue(false)).toEqual({ kind: 'flag', on: false });
    expect(showValue('0123456789abcdef0123456789abcdef').kind).toBe('ref');
    expect(showValue('open', 'select')).toEqual({ kind: 'chip', text: 'open' });
  });

  it('reads a long string, or one with line breaks, as paragraphs', () => {
    expect(showValue('First line.\n\nSecond line.'))
      .toEqual({ kind: 'prose', paragraphs: ['First line.', 'Second line.'] });
    expect(showValue('x'.repeat(200)).kind).toBe('prose');
  });

  it('shows a list of plain values as chips and a list of objects as a table', () => {
    expect(showValue(['ops', 'q3', 4])).toEqual({ kind: 'chips', items: ['ops', 'q3', '4'] });
    expect(showValue([{ item: 'Desk', cost: 120 }, { item: 'Chair', done: true }])).toEqual({
      kind: 'table',
      columns: ['item', 'cost', 'done'],
      rows: [['Desk', '120', ''], ['Chair', '', 'yes']],
    });
  });

  it('unfolds an object two levels, and no further', () => {
    // An object that only wraps one text reads as that text.
    expect(showValue({ text: 'Honey never spoils.' })).toEqual({ kind: 'text', text: 'Honey never spoils.' });

    const shown = showValue({ text: 'Dana will send it.', meta: { by: 'me', deep: { a: 1 } } }) as any;
    expect(shown.kind).toBe('object');
    expect(shown.entries[0]).toEqual({ name: 'text', value: { kind: 'text', text: 'Dana will send it.' } });
    const deep = shown.entries[1].value.entries.find((entry: any) => entry.name === 'deep');
    expect(deep.value).toEqual({ kind: 'text', text: '{"a":1}' });
  });

  it('marks what the manifest calls secret, whatever it holds', () => {
    expect(showValue('s3cret', 'secret')).toEqual({ kind: 'secret', text: 's3cret' });
  });
});
