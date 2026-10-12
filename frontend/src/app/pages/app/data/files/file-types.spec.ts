import { extensionOf, FILE_GROUPS, fileKindOf, sizeLabel } from './file-types';

describe('fileKindOf', () => {
  it('tells the kind from the name', () => {
    expect(fileKindOf('report.PDF')).toBe('pdf');
    expect(fileKindOf('budget.csv')).toBe('spreadsheet');
    expect(fileKindOf('deck.pptx')).toBe('presentation');
    expect(fileKindOf('photo.jpeg')).toBe('image');
    expect(fileKindOf('script.py')).toBe('code');
    expect(fileKindOf('call.mp3')).toBe('media');
    expect(fileKindOf('backup.tar')).toBe('archive');
    expect(fileKindOf('notes.md')).toBe('document');
  });

  it('falls back to the MIME type, then to a plain file', () => {
    expect(fileKindOf('scan', 'image/png')).toBe('image');
    expect(fileKindOf('untitled', 'text/plain')).toBe('document');
    expect(fileKindOf('untitled')).toBe('file');
    expect(fileKindOf('.env')).toBe('file');
  });

  it('puts every kind in one of the groups the page filters by', () => {
    for (const kind of ['document', 'spreadsheet', 'presentation', 'pdf', 'image', 'code', 'media', 'archive', 'file'] as const) {
      expect(FILE_GROUPS.filter((group) => group.kinds.includes(kind)).length).toBe(1);
    }
  });
});

describe('extensionOf', () => {
  it('is the part after the last dot, lower-case', () => {
    expect(extensionOf('a.b.TXT')).toBe('txt');
    expect(extensionOf('noext')).toBe('');
    expect(extensionOf('trailing.')).toBe('');
  });
});

describe('sizeLabel', () => {
  it('reads in the unit that fits', () => {
    expect(sizeLabel(0)).toBe('');
    expect(sizeLabel(512)).toBe('512 B');
    expect(sizeLabel(2048)).toBe('2.0 KB');
    expect(sizeLabel(5 * 1024 * 1024)).toBe('5.0 MB');
  });
});
