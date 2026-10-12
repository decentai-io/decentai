import { initialsOf } from './agent-mark.component';
import { relativeDate } from './dates';
import { FilterColumnComponent, FilterGroup } from './filter-column.component';
import { PagerComponent } from './pager.component';
import { Sort, SortHeaderComponent } from './sort-header.component';

describe('initialsOf', () => {
  it('takes the first letters of up to two words', () => {
    expect(initialsOf('Google Calendar')).toBe('GC');
    expect(initialsOf('Notebook')).toBe('N');
    expect(initialsOf('acme_data-sync tool')).toBe('AD');
    expect(initialsOf('')).toBe('');
  });
});

describe('relativeDate', () => {
  const now = new Date('2026-10-12T12:00:00Z');
  const ago = (ms: number) => new Date(now.getTime() - ms).toISOString();

  it('says how long ago for what is recent', () => {
    expect(relativeDate(ago(20_000), now)).toBe('Just now');
    expect(relativeDate(ago(12 * 60_000), now)).toBe('12 min ago');
    expect(relativeDate(ago(3 * 3_600_000), now)).toBe('3 h ago');
    expect(relativeDate(ago(30 * 3_600_000), now)).toBe('Yesterday');
  });

  it('says the day for what is not, with the year when it is another one', () => {
    expect(relativeDate('2026-10-06T09:00:00Z', now)).toMatch(/6/);
    expect(relativeDate('2025-10-06T09:00:00Z', now)).toContain('2025');
  });

  it('says nothing for no date', () => {
    expect(relativeDate(null, now)).toBe('');
    expect(relativeDate('not a date', now)).toBe('');
  });
});

describe('FilterColumnComponent', () => {
  const optional: FilterGroup = { key: 'type', items: [{ value: 'images', name: 'Images' }] };
  const required: FilterGroup = { key: 'show', required: true, items: [{ value: 'mine', name: 'Mine' }] };

  function column(chosen: Record<string, string>) {
    const component = new FilterColumnComponent();
    component.chosen = chosen;
    const heard: { key: string; value: string }[] = [];
    component.choose.subscribe((choice) => heard.push(choice));
    return { component, heard };
  }

  it('chooses an item, and clears the group when the chosen one is chosen again', () => {
    const fresh = column({ type: '' });
    fresh.component.pick(optional, optional.items[0]);
    expect(fresh.heard).toEqual([{ key: 'type', value: 'images' }]);

    const again = column({ type: 'images' });
    again.component.pick(optional, optional.items[0]);
    expect(again.heard).toEqual([{ key: 'type', value: '' }]);
  });

  it('never leaves a required group with nothing chosen', () => {
    const { component, heard } = column({ show: 'mine' });
    component.pick(required, required.items[0]);
    expect(heard).toEqual([]);
  });

  it('opens an item while one of what it opens into is chosen', () => {
    const agents: FilterGroup = {
      key: 'kind',
      items: [{ value: 'agt_1', name: 'Notebook', children: [{ value: 'agt_1__note', name: 'Note' }] }],
    };
    expect(column({ kind: 'agt_1__note' }).component.isOpen(agents, agents.items[0])).toBe(true);
    expect(column({ kind: 'agt_2__task' }).component.isOpen(agents, agents.items[0])).toBe(false);
  });
});

describe('SortHeaderComponent', () => {
  function header(key: string, sort: Sort) {
    const component = new SortHeaderComponent();
    component.key = key;
    component.sort = sort;
    const heard: Sort[] = [];
    component.sortChange.subscribe((next) => heard.push(next));
    return { component, heard };
  }

  it('sorts by its column, and turns the order round on a second click', () => {
    const other = header('name', { key: 'size', direction: 'desc' });
    other.component.toggle();
    expect(other.heard).toEqual([{ key: 'name', direction: 'asc' }]);
    expect(other.component.ariaSort).toBe('none');

    const same = header('name', { key: 'name', direction: 'asc' });
    same.component.toggle();
    expect(same.heard).toEqual([{ key: 'name', direction: 'desc' }]);
    expect(same.component.ariaSort).toBe('ascending');
  });
});

describe('PagerComponent', () => {
  it('says which rows are shown of how many', () => {
    const pager = new PagerComponent();
    Object.assign(pager, { page: 2, pageSize: 20, total: 64 });

    expect([pager.first, pager.last, pager.pageCount]).toEqual([21, 40, 4]);
  });

  it('asks for the last page when the page is past it', async () => {
    const pager = new PagerComponent();
    Object.assign(pager, { page: 4, pageSize: 20, total: 41 });
    const asked: number[] = [];
    pager.pageChange.subscribe((page) => asked.push(page));

    pager.ngOnChanges();
    await Promise.resolve();

    expect(asked).toEqual([3]);
  });
});
