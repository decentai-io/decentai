import { FilePreviewComponent, parseDelimited } from './file-preview.component';

describe('FilePreviewComponent', () => {
  function create(filename: string, values: any = {}, extra: any = {}) {
    const component = new FilePreviewComponent({ downloadUrl: () => '/download/x' } as any);
    component.file = { resource_ref: 'x', keys: {}, values: { filename, ...values }, ...extra } as any;
    return component;
  }

  it('reads a CSV whose fields hold commas, quotes and line breaks', () => {
    expect(parseDelimited('item,cost\n"Desk, oak",120\n"He said ""no""","two\nlines"\n', ','))
      .toEqual([['item', 'cost'], ['Desk, oak', '120'], ['He said "no"', 'two\nlines']]);
    expect(parseDelimited('a\tb\r\n1\t2', '\t')).toEqual([['a', 'b'], ['1', '2']]);
  });

  it('does not read a file too large to show, and says so', async () => {
    const component = create('big.csv', { file_size: 6 * 1024 * 1024 });
    component.ngOnChanges({ file: { previousValue: undefined } } as any);
    await Promise.resolve();

    expect(component.kind).toBe('table');
    expect(component.problem).toContain('Too large');
  });

  it('shows nothing of a file whose details cannot be read', async () => {
    const component = create('notes.md', {}, { unreadable: true });
    component.ngOnChanges({ file: { previousValue: undefined } } as any);
    await Promise.resolve();

    expect(component.kind).toBe('none');
  });

  it('lists the keys attached to a file, without the ones the facts already say', () => {
    const component = create('a.txt');
    component.file.keys = { chat_id: 'c1', category: 'x__y', label: 'invoice', pages: 3 };

    expect(component.keys).toEqual([['label', 'invoice'], ['pages', '3']]);
  });
});
