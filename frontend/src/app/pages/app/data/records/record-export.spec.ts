import { recordsAsCsv, recordsAsJson } from './record-export';

describe('exporting records', () => {
  const shape = {
    fields: [
      { name: 'title', label: 'Title', type: 'string', storage: 'keys' },
      { name: 'priority', label: 'Priority', type: 'number', storage: 'keys' },
      { name: 'token', label: 'Token', type: 'secret', storage: 'values' },
      { name: 'content', label: 'Content', type: 'object', storage: 'values' },
    ],
  } as any;
  const records = [{
    resource_ref: 'rec_1', keys: { title: 'Desk, "oak"', priority: 2 },
    values: { token: 's3cret', content: { text: 'two\nlines' } },
    created_at: '2026-10-12T10:00:00Z', updated_at: '2026-10-12T11:00:00Z',
  }] as any;

  it('writes a CSV with a column for each field, quoting what needs it', () => {
    const lines = recordsAsCsv(records, shape).split('\r\n');
    expect(lines[0]).toBe('Title,Priority,Content,Created,Changed,Reference');
    expect(lines[1]).toBe(
      '"Desk, ""oak""",2,"{""text"":""two\\nlines""}",2026-10-12T10:00:00Z,2026-10-12T11:00:00Z,rec_1');
  });

  it('leaves a secret field out of both forms', () => {
    expect(recordsAsCsv(records, shape)).not.toContain('s3cret');
    const json = JSON.parse(recordsAsJson(records, shape));
    expect(json[0]).toEqual({
      reference: 'rec_1', title: 'Desk, "oak"', priority: 2, content: { text: 'two\nlines' },
      created_at: '2026-10-12T10:00:00Z', updated_at: '2026-10-12T11:00:00Z',
    });
  });

  it('exports a kind no agent declares by the keys its records carry', () => {
    expect(recordsAsCsv(records, null).split('\r\n')[0]).toBe('title,priority,Created,Changed,Reference');
  });
});
