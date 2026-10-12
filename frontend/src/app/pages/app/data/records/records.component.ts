import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { FilterGroup } from 'src/app/components/ui/filter-column.component';
import { Choice } from 'src/app/components/ui/radio-keys';
import { Sort } from 'src/app/components/ui/sort-header.component';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import {
  AgentRecord, RecordField, RecordShape, RecordsService,
} from 'src/app/services/records.service';
import { DataPageBase } from '../../data-page-base';
import { ShareEditor } from '../../share-editor';

/** One kind an agent keeps, as the filter column shows it. */
interface KindNode {
  resourceId: string;
  label: string;
  count: number;
}

/** One agent in the filter column, with the kinds it keeps beneath it. */
interface AgentNode {
  id: string;
  name: string;
  count: number;
  kinds: KindNode[];
}

/** A column of the table one kind is shown as. */
interface Column {
  name: string;
  label: string;
  type: RecordField['type'];
}

/** Whose records are listed. */
type Show = 'mine' | 'shared';

/** The column a kind with no declared shape is shown by: its name. */
const NAME_COLUMN = '__name';

/** Newest change first: how a kind opens. */
const BY_CHANGE: Sort = { key: 'updated', direction: 'desc' };

/**
 * Saved data: what agents have stored on the user's behalf. Records are
 * grouped by `resource_id` (`agent__kind`), and the page shows one kind
 * at a time: the column at the list's left holds the agents, each
 * opening to the kinds it keeps, and the address names the one open
 * (/data/records/<agent>/<kind>), with the search, the sort and the
 * page in its query.
 *
 * One kind is a table: every record of a kind shares the shape its agent
 * declared, so the columns are that shape's fields, labelled as the
 * manifest labels them, sortable, and a row opens to the whole record. A
 * kind no installed agent declares any more is listed by name. Either
 * way, nothing here is invented: the shape comes from the manifest, and
 * a hand-written record follows it exactly so the agent can read back
 * what was written.
 */
@Component({
  selector: 'app-records',
  standalone: false,
  templateUrl: './records.component.html',
  // iam-shared.css styles the fields of the form dialog, until that
  // dialog is on the form system.
  styleUrls: [
    '../../data-shared.css',
    '../../../admin/iam-shared.css',
    './records.component.css',
  ],
})
export class RecordsComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  records: AgentRecord[] = [];
  profile: Profile | null = null;

  // ── What the address says ───────────────────────────────────────────
  show: Show = 'mine';
  /** The agent whose kind is open: an `agt_…` ref; '' before one is. */
  activeAgent = '';
  /** The kind half of the address. */
  private kindParam = '';
  query = '';
  sort: Sort = BY_CHANGE;
  page = 1;

  readonly pageSize = 20;
  readonly sortChoices: Choice[] = [
    { value: 'updated', name: 'Changed' },
    { value: 'created', name: 'Created' },
    { value: 'name', name: 'Name' },
  ];
  busyRef = '';

  /** Records ticked to go together, by resource_ref. */
  selected = new Set<string>();
  bulkBusy = false;

  /** The record types installed agents declare — the filter column's
   *  labels, the table's columns, and what Add a record offers. */
  shapes: RecordShape[] = [];
  pickerOpen = false;
  formShape: RecordShape | null = null;
  editingRef = '';
  formFields: Record<string, any> = {};
  saving = false;

  /** The record open in the View dialog, or null. */
  viewTarget: AgentRecord | null = null;

  /** Who can see a record. An agent stored it privately; deciding to
   *  show it to a colleague is the person's call, not the agent's. */
  readonly share = new ShareEditor(() => this.profile);

  /** Columns a kind's table shows at once; the rest are in the record. */
  private static readonly MAX_COLUMNS = 7;

  private readonly address = new Subscription();

  constructor(
    private service: RecordsService,
    private profiles: ProfileService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
    private confirm: ConfirmService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    // The kind is a child route's (routes/app/data.routes.ts), so that
    // moving between kinds keeps this page; it is read after each
    // navigation, and the page then makes sure it names something.
    this.readKind();
    this.address.add(this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => {
        this.readKind();
        if (!this.loading) this.settleSelection();
      }));
    this.address.add(this.route.queryParamMap.subscribe((params) => {
      this.show = params.get('show') === 'shared' ? 'shared' : 'mine';
      this.query = params.get('q') ?? '';
      const key = params.get('sort') ?? BY_CHANGE.key;
      this.sort = { key, direction: params.get('dir') === 'asc' ? 'asc' : params.get('dir') === 'desc' ? 'desc' : this.firstDirection(key) };
      this.page = Math.max(1, Number(params.get('page')) || 1);
    }));

    const [records, profile] = await Promise.all([
      this.service.list(),
      this.profiles.get(),
    ]);
    this.records = records;
    this.profile = profile;
    this.loading = false;
    this.settleSelection();
    this.shapes = await this.service.shapes().catch(() => []);
    this.share.peers = await this.profiles.peers().catch(() => []);
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
  }

  private readKind(): void {
    const params = this.route.snapshot.firstChild?.paramMap;
    const agent = params?.get('agent') ?? '';
    const kind = params?.get('kind') ?? '';
    if (agent === this.activeAgent && kind === this.kindParam) return;
    this.activeAgent = agent;
    this.kindParam = kind;
    this.selected.clear();
    this.share.close();
  }

  /** The kind that is open: its resource id, `agent__kind`. A kind from
   *  before agents had refs has no agent half, and its address says its
   *  whole id twice. */
  get activeGroup(): string {
    if (!this.activeAgent || !this.kindParam) return '';
    return this.activeAgent === this.kindParam
      ? this.activeAgent
      : `${this.activeAgent}__${this.kindParam}`;
  }

  /** Opens a kind: its address. */
  private openKind(resourceId: string, replaceUrl = false): void {
    const agent = this.agentOf(resourceId) || resourceId;
    void this.router.navigate(['/data/records', agent, this.kindOf(resourceId)], {
      queryParams: { show: this.show === 'shared' ? 'shared' : null },
      replaceUrl,
    });
  }

  /** Changes the query of the address, and so what is listed. */
  private go(changes: Record<string, string | number | null>): void {
    const queryParams: Record<string, string | number | null> = {};
    for (const [key, value] of Object.entries(changes)) queryParams[key] = value || null;
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams,
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  private async reload(): Promise<void> {
    this.records = await this.service.list();
    // A tick on a record that is no longer there means nothing.
    this.selected = new Set(
      [...this.selected].filter((ref) =>
        this.records.some((record) => record.resource_ref === ref)),
    );
    this.settleSelection();
  }

  /** Point at something that still exists.
   *
   *  The filter column is derived from the records themselves, so with
   *  no kind in the address, or after the last record of the open kind
   *  is deleted, the address names nothing that is there. The page
   *  settles itself: to the same agent's next kind, else to the first
   *  kind of the first agent; with no record at all it stays where it
   *  is and says so. A kind that holds nothing yet but that its agent
   *  lets a person write is somewhere to be: its first record is made
   *  from there. */
  private settleSelection(): void {
    const open = this.activeGroup;
    if (open && (this.selectionRecords.length || this.creatable(this.shapeFor(open)))) return;
    const agents = this.agentNodes;
    const next = agents.find((node) => node.id === this.activeAgent)?.kinds[0]
      ?? agents[0]?.kinds[0];
    if (next && next.resourceId !== open) this.openKind(next.resourceId, true);
  }

  // ── Choosing several at once ────────────────────────────────────────

  /** Whether this record is one the person may delete at all. The same
   *  pair the row's own Delete obeys: the permission, and the rule that
   *  only whoever created a record may touch it. */
  deletable(record: AgentRecord): boolean {
    return this.canDelete && this.canTouch(record);
  }

  isSelected(record: AgentRecord): boolean {
    return this.selected.has(record.resource_ref);
  }

  toggleSelected(record: AgentRecord): void {
    if (this.selected.has(record.resource_ref)) {
      this.selected.delete(record.resource_ref);
    } else {
      this.selected.add(record.resource_ref);
    }
  }

  /** "All" means all of what the search shows of the open kind —
   *  never records the person cannot see from here. */
  get selectableShown(): AgentRecord[] {
    return this.shownRecords.filter((record) => this.deletable(record));
  }

  get allShownSelected(): boolean {
    const shown = this.selectableShown;
    return shown.length > 0 && shown.every((record) => this.isSelected(record));
  }

  selectAllShown(): void {
    for (const record of this.selectableShown) this.selected.add(record.resource_ref);
  }

  clearSelection(): void {
    this.selected.clear();
  }

  // ── Access ──────────────────────────────────────────────────────────

  get canDelete(): boolean { return this.auth.can('data:record:delete'); }
  get canCreate(): boolean { return this.auth.can('data:record:create'); }
  get canUpdate(): boolean { return this.auth.can('data:record:update'); }
  get canShare(): boolean { return this.auth.can('data:record:update'); }
  get canTransfer(): boolean { return this.auth.can('data:record:transfer'); }
  get canShareOrgWide(): boolean { return this.auth.can('data:record:set_owner_any'); }

  get myUserId(): string { return this.profile?.user_id ?? ''; }

  canTouch(record: AgentRecord): boolean {
    return record.created_by === this.myUserId;
  }

  // ── Naming ──────────────────────────────────────────────────────────

  /** The agent half of the category — who keeps this kind of record. */
  agentOf(resourceId: string): string {
    return resourceId.includes('__') ? resourceId.split('__')[0] : '';
  }

  /** The what half — the kind of thing it keeps. */
  kindOf(resourceId: string): string {
    return resourceId.includes('__')
      ? resourceId.split('__').slice(1).join('__')
      : resourceId;
  }

  /** The agent's readable name, from the shapes it declared — never the
   *  ref, which is the platform's bookkeeping. */
  agentNameOf(resourceId: string): string {
    const prefix = this.agentOf(resourceId);
    const shape = this.shapes.find((s) => s.agent_ref === prefix);
    return shape?.agent_name || prefix || 'unknown agent';
  }

  /** The kind's manifest label — "Note", not "note". */
  kindLabelOf(resourceId: string): string {
    const shape = this.shapes.find((s) => s.resource_id === resourceId);
    return shape?.label || this.kindOf(resourceId);
  }

  shapeOf(record: AgentRecord): RecordShape | null {
    return this.shapes.find((shape) => shape.resource_id === record.resource_id) ?? null;
  }

  private shapeFor(resourceId: string): RecordShape | null {
    return this.shapes.find((shape) => shape.resource_id === resourceId) ?? null;
  }

  /** A field's label as its manifest names it, else the key itself. */
  fieldLabel(record: AgentRecord, name: string): string {
    const field = this.shapeOf(record)?.fields.find((f) => f.name === name);
    return field?.label || name;
  }

  /** The most name-like key an agent set, else the record's own id. */
  recordName(record: AgentRecord): string {
    const keys = record.keys ?? {};
    for (const candidate of ['title', 'name', 'label', 'subject', 'number', 'supplier']) {
      const value = keys[candidate];
      if (typeof value === 'string' && value.trim()) return value;
    }
    return record.resource_ref;
  }

  // ── The filter column ───────────────────────────────────────────────

  private isMine(record: AgentRecord): boolean {
    return record.created_by === this.myUserId;
  }

  /** The records of the chosen "whose". */
  private get pool(): AgentRecord[] {
    return this.records.filter((record) => this.isMine(record) === (this.show === 'mine'));
  }

  get agentNodes(): AgentNode[] {
    const byAgent = new Map<string, Map<string, number>>();
    for (const record of this.pool) {
      const agent = this.agentOf(record.resource_id) || record.resource_id;
      const kinds = byAgent.get(agent) ?? new Map<string, number>();
      kinds.set(record.resource_id, (kinds.get(record.resource_id) ?? 0) + 1);
      byAgent.set(agent, kinds);
    }
    return [...byAgent.entries()]
      .map(([id, kinds]) => ({
        id,
        name: this.agentNameOf(`${id}__`),
        count: [...kinds.values()].reduce((a, b) => a + b, 0),
        kinds: [...kinds.entries()]
          .map(([resourceId, count]) => ({
            resourceId, count, label: this.kindLabelOf(resourceId),
          }))
          .sort((a, b) => a.label.localeCompare(b.label)),
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  get chosen(): Record<string, string> {
    return { show: this.show, kind: this.activeGroup };
  }

  /** Whose records, then the agents, each opening to its kinds. */
  get filters(): FilterGroup[] {
    const mine = this.records.filter((record) => this.isMine(record)).length;
    return [
      {
        key: 'show',
        required: true,
        items: [
          { value: 'mine', name: 'Mine', count: mine },
          { value: 'shared', name: 'Shared with me', count: this.records.length - mine },
        ],
      },
      {
        key: 'kind',
        label: 'Agents',
        required: true,
        items: this.agentNodes.map((agent) => ({
          value: agent.id,
          name: agent.name,
          count: agent.count,
          agent: { name: agent.name },
          children: agent.kinds.map((kind) => ({
            value: kind.resourceId, name: kind.label, count: kind.count,
          })),
        })),
      },
    ].filter((group) => group.items.length > 0);
  }

  /** A filter was chosen: whose records, or an agent (its first kind)
   *  or one of its kinds. */
  filter(key: string, value: string): void {
    if (key === 'show') {
      this.show = value as Show;
      const stillThere = this.agentNodes.some((agent) =>
        agent.kinds.some((kind) => kind.resourceId === this.activeGroup));
      const first = this.agentNodes[0]?.kinds[0];
      if (!stillThere && first) return this.openKind(first.resourceId);
      return this.go({ show: value === 'shared' ? 'shared' : null, page: null });
    }
    const agent = this.agentNodes.find((node) => node.id === value);
    this.openKind(agent ? agent.kinds[0].resourceId : value);
  }

  /** The kinds a person may create: those whose agent says so. */
  get pickerShapes(): RecordShape[] {
    return this.shapes.filter((shape) => (shape.user_access ?? []).includes('create'));
  }

  /** Whether the record's agent lets a person edit its kind. */
  mayEdit(record: AgentRecord): boolean {
    return (this.shapeOf(record)?.user_access ?? []).includes('update');
  }

  /** What the manifest says the open kind is, when it says. */
  get kindDescription(): string {
    return this.shapeFor(this.activeGroup)?.description?.trim() ?? '';
  }

  // ── Filtering and sorting ───────────────────────────────────────────

  /** The open kind's records, before the search. */
  get selectionRecords(): AgentRecord[] {
    return this.pool.filter((record) => record.resource_id === this.activeGroup);
  }

  get shownRecords(): AgentRecord[] {
    const query = this.query.trim().toLowerCase();
    const rows = this.selectionRecords
      .filter((record) => {
        if (!query) return true;
        const haystack = [
          this.recordName(record),
          JSON.stringify(record.keys ?? {}),
          JSON.stringify(record.values ?? {}),
        ].join(' ').toLowerCase();
        return haystack.includes(query);
      });
    return rows.sort((a, b) => this.compare(a, b) * (this.sort.direction === 'desc' ? -1 : 1));
  }

  get pagedRecords(): AgentRecord[] {
    const start = (this.page - 1) * this.pageSize;
    return this.shownRecords.slice(start, start + this.pageSize);
  }

  toPage(page: number): void {
    this.go({ page: page === 1 ? null : page });
  }

  search(value: string): void {
    this.go({ q: value, page: null });
  }

  private compare(a: AgentRecord, b: AgentRecord): number {
    const key = this.sort.key;
    if (key === 'updated' || key === 'created') {
      const field = key === 'updated' ? 'updated_at' : 'created_at';
      return String((a as any)[field] || a.created_at || '')
        .localeCompare(String((b as any)[field] || b.created_at || ''));
    }
    if (key === 'name' || key === NAME_COLUMN) return this.recordName(a).localeCompare(this.recordName(b));
    const av = a.keys?.[key];
    const bv = b.keys?.[key];
    if (typeof av === 'number' && typeof bv === 'number') return av - bv;
    return String(av ?? '').localeCompare(String(bv ?? ''), undefined, { numeric: true });
  }

  /** Dates open newest first; everything else A to Z. */
  private firstDirection(key: string): Sort['direction'] {
    return key === 'updated' || key === 'created' ? 'desc' : 'asc';
  }

  /** A column heading was clicked, or the toolbar's sort chosen. */
  sortBy(sort: Sort): void {
    const first = sort.key !== this.sort.key;
    const direction = first ? this.firstDirection(sort.key) : sort.direction;
    this.go({
      sort: sort.key === BY_CHANGE.key ? null : sort.key,
      dir: direction === this.firstDirection(sort.key) ? null : direction,
      page: null,
    });
  }

  sortByChoice(key: string): void {
    this.sortBy({ key, direction: this.firstDirection(key) });
  }

  // ── The table one kind is shown as ──────────────────────────────────

  get tableShape(): RecordShape | null {
    return this.activeGroup ? this.shapeFor(this.activeGroup) : null;
  }

  /** The kind's key fields, in manifest order, the name first, objects
   *  and secrets left to the record itself. A kind with no declared
   *  shape has one column: the record's name. */
  get columns(): Column[] {
    const shape = this.tableShape;
    if (!shape) return [{ name: NAME_COLUMN, label: 'Name', type: 'string' }];
    const usable = shape.fields.filter((f) =>
      f.storage === 'keys' && f.type !== 'object' && f.type !== 'secret');
    const nameFirst = [...usable].sort((a, b) => {
      const rank = (f: RecordField) => ['title', 'name', 'label', 'subject', 'number'].includes(f.name) ? 0 : 1;
      return rank(a) - rank(b);
    });
    return nameFirst.slice(0, RecordsComponent.MAX_COLUMNS)
      .map((f) => ({ name: f.name, label: f.label || f.name, type: f.type }));
  }

  cell(record: AgentRecord, column: Column): string {
    if (column.name === NAME_COLUMN) return this.recordName(record);
    const value = record.keys?.[column.name];
    if (value === null || value === undefined || value === '') return '';
    if (column.type === 'boolean') return value ? 'yes' : 'no';
    if (typeof value === 'number') return this.numberLabel(value);
    return String(value);
  }

  cellKind(record: AgentRecord, column: Column): string {
    if (column.name === NAME_COLUMN) return 'text';
    const value = record.keys?.[column.name];
    if (value === null || value === undefined || value === '') return 'empty';
    if (column.type === 'select') return 'chip';
    if (column.type === 'number' || typeof value === 'number') return 'number';
    if (typeof value === 'string' && /^[0-9a-f]{32}$/.test(value)) return 'ref';
    return 'text';
  }

  private numberLabel(value: number): string {
    return Number.isInteger(value) ? String(value)
      : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  // ── The record, whole ───────────────────────────────────────────────

  /** Every key, labelled, for the View dialog. */
  keyEntries(record: AgentRecord): { name: string; label: string; value: string; long: boolean }[] {
    return Object.entries(record.keys ?? {}).map(([name, value]) => {
      const text = typeof value === 'string' ? value
        : typeof value === 'number' ? this.numberLabel(value)
        : JSON.stringify(value);
      // A field an agent writes prose into — a site's notes, a summary —
      // keeps its lines and reads whole, not as one collapsed cell.
      return { name, label: this.fieldLabel(record, name), value: text,
               long: text.length > 120 || text.includes('\n') };
    });
  }

  valueEntries(record: AgentRecord): { name: string; label: string; text: string }[] {
    return Object.entries(record.values ?? {}).map(([name, value]) => ({
      name,
      label: this.fieldLabel(record, name),
      text: typeof value === 'string' ? value : JSON.stringify(value, null, 2),
    }));
  }

  shareSummary(record: AgentRecord): string {
    return this.share.summary(record.owner, record.created_by);
  }

  /** When the record last changed. */
  private changedAt(record: AgentRecord): string {
    return record.updated_at || record.created_at || '';
  }

  changed(record: AgentRecord): string {
    return relativeDate(this.changedAt(record));
  }

  changedInFull(record: AgentRecord): string {
    return fullDate(this.changedAt(record));
  }

  /** The whole date and time, for the record's own view. */
  dateLabel(value?: string | null): string {
    return fullDate(value);
  }

  // ── Authoring ───────────────────────────────────────────────────────

  /** Whether a person may write this kind by hand. */
  private creatable(shape: RecordShape | null): boolean {
    return !!shape && (shape.user_access ?? []).includes('create');
  }

  openPicker(): void {
    if (this.saving) return;
    // The kind on screen is the kind to make, where its agent lets a
    // person make one. Otherwise the choice is among the kinds that
    // do — which includes kinds that hold no record yet.
    const shape = this.tableShape;
    if (this.creatable(shape)) return this.startCreate(shape!);
    const offered = this.pickerShapes;
    if (offered.length === 1) return this.startCreate(offered[0]);
    this.pickerOpen = true;
    this.error = '';
  }

  closePicker(): void {
    this.pickerOpen = false;
  }

  startCreate(shape: RecordShape): void {
    this.pickerOpen = false;
    this.formShape = shape;
    this.editingRef = '';
    this.formFields = {};
    for (const field of shape.fields) {
      this.formFields[field.name] = this.blankFor(field);
    }
    this.error = '';
  }

  startEdit(record: AgentRecord): void {
    const shape = this.shapeOf(record);
    if (!shape) return;
    this.formShape = shape;
    this.editingRef = record.resource_ref;
    this.formFields = {};
    for (const field of shape.fields) {
      const stored = field.storage === 'values'
        ? record.values?.[field.name]
        : record.keys?.[field.name];
      this.formFields[field.name] = field.type === 'object'
        ? JSON.stringify(stored ?? {}, null, 2)
        : stored ?? this.blankFor(field);
    }
    this.error = '';
  }

  cancelForm(): void {
    this.formShape = null;
    this.editingRef = '';
  }

  private blankFor(field: RecordField): any {
    if (field.type === 'boolean') return false;
    return '';
  }

  async saveForm(): Promise<void> {
    const shape = this.formShape;
    if (!shape) return;

    const editing = !!this.editingRef;
    const stored = editing
      ? this.records.find((record) => record.resource_ref === this.editingRef)
      : undefined;
    const fields: Record<string, any> = {};
    for (const field of shape.fields) {
      const raw = this.formFields[field.name];
      if (field.type === 'boolean') {
        fields[field.name] = !!raw;
        continue;
      }
      if (raw === '' || raw === null || raw === undefined) {
        // On an edit a blank box over a stored value is a deliberate
        // clearing, and an edit is merged over what is stored: leaving
        // the field out would keep the old value. Text can be emptied;
        // the other types have no empty value in the agent's shape.
        const had = (field.storage === 'values' ? stored?.values : stored?.keys)?.[field.name];
        if (!editing || had === undefined || had === null || had === '') continue;
        if (field.required) return this.fail(`${field.label || field.name} is required.`);
        if (field.type !== 'string' && field.type !== 'secret') {
          return this.fail(`${field.label || field.name} cannot be emptied once it has a value.`);
        }
        fields[field.name] = '';
        continue;
      }
      if (field.type === 'number') {
        const parsed = Number(raw);
        if (Number.isNaN(parsed)) return this.fail(`${field.label} must be a number.`);
        fields[field.name] = parsed;
      } else if (field.type === 'object') {
        try {
          fields[field.name] = JSON.parse(String(raw));
        } catch {
          return this.fail(`${field.label} must be valid JSON.`);
        }
      } else {
        fields[field.name] = raw;
      }
    }

    this.saving = true;
    try {
      const result = editing
        ? await this.service.update(this.editingRef, fields)
        : await this.service.create(shape.resource_id, fields);
      if (result.error) return this.fail(result.error);
      this.cancelForm();
      await this.reload();
      // A first record of a kind that held none: show it.
      if (!editing && shape.resource_id !== this.activeGroup) this.openKind(shape.resource_id);
      this.flash(editing ? 'Record updated.' : 'Record created.');
    } finally {
      this.saving = false;
    }
  }

  // ── Actions ─────────────────────────────────────────────────────────

  openView(record: AgentRecord): void {
    this.viewTarget = record;
  }

  closeView(): void {
    this.viewTarget = null;
  }

  openShare(record: AgentRecord): void {
    if (this.share.isOpen(record.resource_ref)) return this.share.close();
    this.share.open(record.resource_ref, record.owner);
  }

  async saveShare(record: AgentRecord): Promise<void> {
    this.share.saving = true;
    try {
      const result = await this.service.setOwner(
        record.resource_ref,
        this.share.owner() as any,
      );
      if (result.error) return this.fail(result.error);
      this.share.close();
      await this.reload();
      this.flash('Sharing updated.');
    } finally {
      this.share.saving = false;
    }
  }

  get deleteBusy(): boolean {
    return !!this.busyRef || this.bulkBusy;
  }

  /** Whether the row's menu would hold anything. */
  hasActions(record: AgentRecord): boolean {
    return this.canTouch(record)
      && ((this.canUpdate && this.mayEdit(record)) || this.canShare || this.canTransfer || this.canDelete);
  }

  deleteOne(record: AgentRecord): Promise<void> {
    return this.delete([record]);
  }

  deleteSelected(): Promise<void> {
    return this.delete(this.records.filter((record) => this.isSelected(record)));
  }

  /** One record or many, the same way: asked first, then through the
   *  door that already checks who may delete what. A record that refuses
   *  does not stop the rest, and stays ticked so it is clear what did
   *  not go. */
  private async delete(going: AgentRecord[]): Promise<void> {
    if (!going.length) return;
    const names = going.map((record) => this.recordName(record));
    const listed = names.length <= 3
      ? names.join(', ')
      : `${names.slice(0, 3).join(', ')} and ${names.length - 3} more`;
    const sure = await this.confirm.ask({
      title: going.length === 1 ? 'Delete this record?' : `Delete ${going.length} records?`,
      body: `${listed}. Deleted records cannot be recovered, and the agent will no longer be able to read ${going.length === 1 ? 'it' : 'them'}.`,
      confirm: going.length === 1 ? 'Delete record' : `Delete ${going.length} records`,
      danger: true,
    });
    if (!sure) return;

    const gone: string[] = [];
    const refused: string[] = [];
    this.bulkBusy = true;
    try {
      for (const record of going) {
        this.busyRef = record.resource_ref;
        const result = await this.service.remove(record.resource_ref);
        if (result.error) {
          refused.push(this.recordName(record));
          continue;
        }
        gone.push(this.recordName(record));
        this.selected.delete(record.resource_ref);
        if (this.viewTarget?.resource_ref === record.resource_ref) this.viewTarget = null;
      }
    } finally {
      this.busyRef = '';
      this.bulkBusy = false;
    }
    await this.reload();
    if (refused.length) {
      this.fail(`Deleted ${gone.length}. Still here — ${refused.join(', ')}.`);
    } else if (gone.length === 1) {
      this.flash('Record deleted.');
    } else {
      this.flash(`${gone.length} records deleted.`);
    }
  }

  // ── Handing over ────────────────────────────────────────────────────

  transferTarget: AgentRecord | null = null;
  transferring = false;

  requestTransfer(item: AgentRecord): void {
    this.transferTarget = item;
    this.error = '';
  }

  closeTransfer(): void {
    if (!this.transferring) this.transferTarget = null;
  }

  async transfer(person: { user_id: string; user_name: string; email: string }): Promise<void> {
    const item = this.transferTarget;
    if (!item) return;
    this.transferring = true;
    try {
      const result = await this.service.transfer(item.resource_ref, person.user_id);
      // Closed either way: a refusal is said on the page, where it can
      // be read, not under the dialog.
      this.transferTarget = null;
      if (result.error) return this.fail(result.error);
      await this.reload();
      this.flash(`Handed over to ${person.user_name || person.email}.`);
    } finally {
      this.transferring = false;
    }
  }
}
