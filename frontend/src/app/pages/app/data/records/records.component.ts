import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import {
  AgentRecord, RecordField, RecordShape, RecordsService,
} from 'src/app/services/records.service';
import { DataPageBase } from '../../data-page-base';
import { ShareEditor } from '../../share-editor';

/** One kind an agent keeps, as the rail shows it. */
interface KindNode {
  resourceId: string;
  label: string;
  count: number;
}

/** One agent in the rail, with the kinds it keeps beneath it. */
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

type SortKey = 'updated' | 'created' | 'name' | string;

/**
 * Agent records: what agents have stored on the user's behalf. Records are
 * grouped by `resource_id` (`agent__resource`): the page opens on a card
 * per agent with the kinds it keeps and their counts, and an agent opens
 * to one kind at a time, chosen from a selector.
 *
 * One kind is a table: every record of a kind shares the shape its agent
 * declared, so the columns are that shape's fields, labelled as the
 * manifest labels them, sortable, and a row opens to the whole record. A
 * kind no installed agent declares any more is a plain list. Either way,
 * nothing here is invented: the shape comes from the manifest, and a
 * hand-written record follows it exactly so the agent can read back what
 * was written.
 */
@Component({
  selector: 'app-records',
  standalone: false,
  templateUrl: './records.component.html',
  styleUrls: [
    '../../data-shared.css',
    '../../../admin/iam-shared.css',
    './records.component.css',
  ],
})
export class RecordsComponent extends DataPageBase implements OnInit {
  loading = true;
  records: AgentRecord[] = [];
  profile: Profile | null = null;

  /** '' = every agent; else an `agt_…` prefix. */
  activeAgent = '';
  /** '' = every kind the selected agent keeps; else one resource id. */
  activeGroup = '';
  query = '';
  agentQuery = '';
  agentPage = 1;
  recordPage = 1;
  readonly agentPageSize = 12;
  readonly recordPageSize = 20;
  sortKey: SortKey = 'updated';
  sortDesc = true;
  busyRef = '';

  /** What the confirmation is about: one record, or every one ticked.
   *  A single dialog serves both, so there is one place that says what
   *  is about to go. */
  deleting: AgentRecord[] = [];
  /** Records ticked to go together, by resource_ref. */
  selected = new Set<string>();
  bulkBusy = false;

  /** The record types installed agents declare — the rail's labels,
   *  the table's columns, and New record's menu. */
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

  constructor(
    private service: RecordsService,
    private profiles: ProfileService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    const [records, profile] = await Promise.all([
      this.service.list(),
      this.profiles.get(),
    ]);
    this.records = records;
    this.profile = profile;
    this.loading = false;
    this.shapes = await this.service.shapes().catch(() => []);
    this.share.peers = await this.profiles.peers().catch(() => []);
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
   *  The rail is derived from the records themselves, so deleting the
   *  last of a kind — or of an agent — leaves this page describing a
   *  selection that is no longer there: the workspace renders nothing
   *  at all, while its header still counts the items that were. And
   *  because the sidebar link is the route this page is already on,
   *  Angular never rebuilds the component, so nothing recovers until
   *  the person navigates away and back.
   *
   *  So the page settles itself: to the agent's next remaining kind,
   *  else back to the agents it still has. */
  private settleSelection(): void {
    if (!this.activeAgent) return;
    const agent = this.agentNodes.find((node) => node.id === this.activeAgent);
    if (!agent) {
      this.selectAll();
      return;
    }
    if (this.activeGroup
        && !agent.kinds.some((kind) => kind.resourceId === this.activeGroup)) {
      this.activeGroup = agent.kinds[0]?.resourceId ?? '';
      this.recordPage = 1;
    }
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

  /** "All" means all of what is on screen under the current search and
   *  data type — never records the person cannot see from here. */
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

  get selectedRecords(): AgentRecord[] {
    return this.records.filter((record) => this.isSelected(record));
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

  // ── The rail ────────────────────────────────────────────────────────

  get agentNodes(): AgentNode[] {
    const byAgent = new Map<string, Map<string, number>>();
    for (const record of this.records) {
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

  get activeAgentNode(): AgentNode | null {
    return this.agentNodes.find((agent) => agent.id === this.activeAgent) ?? null;
  }

  get filteredAgentNodes(): AgentNode[] {
    const query = this.agentQuery.trim().toLowerCase();
    if (!query) return this.agentNodes;
    return this.agentNodes.filter((agent) =>
      `${agent.name} ${agent.kinds.map((kind) => kind.label).join(' ')}`.toLowerCase().includes(query),
    );
  }

  get pagedAgentNodes(): AgentNode[] {
    const start = (this.agentPage - 1) * this.agentPageSize;
    return this.filteredAgentNodes.slice(start, start + this.agentPageSize);
  }

  get agentPageCount(): number {
    return Math.max(1, Math.ceil(this.filteredAgentNodes.length / this.agentPageSize));
  }

  /** The kinds a person may create here: those whose agent says so. */
  get pickerShapes(): RecordShape[] {
    return this.shapes.filter((shape) =>
      (shape.user_access ?? []).includes('create')
      && (!this.activeAgent || shape.agent_ref === this.activeAgent));
  }

  /** Whether the record's agent lets a person edit its kind. */
  mayEdit(record: AgentRecord): boolean {
    return (this.shapeOf(record)?.user_access ?? []).includes('update');
  }

  kindDescription(resourceId: string): string {
    const description = this.shapeFor(resourceId)?.description?.trim();
    return description || `It stores ${this.kindLabelOf(resourceId).toLowerCase()} items the agent may need again.`;
  }

  selectAll(): void {
    this.activeAgent = '';
    this.activeGroup = '';
    this.resetSort();
    this.recordPage = 1;
  }

  selectAgent(id: string): void {
    this.activeAgent = id;
    this.activeGroup = this.agentNodes.find((agent) => agent.id === id)?.kinds[0]?.resourceId ?? '';
    this.resetSort();
    this.recordPage = 1;
  }

  selectKind(agentId: string, resourceId: string): void {
    this.activeAgent = agentId;
    this.activeGroup = resourceId;
    this.resetSort();
    this.recordPage = 1;
  }

  private resetSort(): void {
    this.sortKey = 'updated';
    this.sortDesc = true;
  }

  // ── Filtering and sorting ───────────────────────────────────────────

  get selectionRecords(): AgentRecord[] {
    return this.records
      .filter((record) => !this.activeAgent
        || this.agentOf(record.resource_id) === this.activeAgent
        || record.resource_id === this.activeAgent)
      .filter((record) => !this.activeGroup || record.resource_id === this.activeGroup);
  }

  get selectionRecordCount(): number {
    return this.selectionRecords.length;
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
    return rows.sort((a, b) => this.compare(a, b) * (this.sortDesc ? -1 : 1));
  }

  get pagedRecords(): AgentRecord[] {
    const start = (this.recordPage - 1) * this.recordPageSize;
    return this.shownRecords.slice(start, start + this.recordPageSize);
  }

  get recordPageCount(): number {
    return Math.max(1, Math.ceil(this.shownRecords.length / this.recordPageSize));
  }

  searchRecords(value: string): void {
    this.query = value;
    this.recordPage = 1;
  }

  searchAgents(value: string): void {
    this.agentQuery = value;
    this.agentPage = 1;
  }

  private compare(a: AgentRecord, b: AgentRecord): number {
    const key = this.sortKey;
    if (key === 'updated' || key === 'created') {
      const field = key === 'updated' ? 'updated_at' : 'created_at';
      return String((a as any)[field] || a.created_at || '')
        .localeCompare(String((b as any)[field] || b.created_at || ''));
    }
    if (key === 'name') return this.recordName(a).localeCompare(this.recordName(b));
    const av = a.keys?.[key];
    const bv = b.keys?.[key];
    if (typeof av === 'number' && typeof bv === 'number') return av - bv;
    return String(av ?? '').localeCompare(String(bv ?? ''), undefined, { numeric: true });
  }

  sortBy(key: SortKey): void {
    if (this.sortKey === key) this.sortDesc = !this.sortDesc;
    else {
      this.sortKey = key;
      this.sortDesc = key === 'updated' || key === 'created';
    }
  }

  // ── The table one kind is shown as ──────────────────────────────────

  get tableShape(): RecordShape | null {
    return this.activeGroup ? this.shapeFor(this.activeGroup) : null;
  }

  /** The kind's key fields, in manifest order, the name first, objects
   *  and secrets left to the record itself. */
  get columns(): Column[] {
    const shape = this.tableShape;
    if (!shape) return [];
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
    const value = record.keys?.[column.name];
    if (value === null || value === undefined || value === '') return '';
    if (column.type === 'boolean') return value ? 'yes' : 'no';
    if (typeof value === 'number') return this.numberLabel(value);
    return String(value);
  }

  cellKind(record: AgentRecord, column: Column): string {
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

  dateLabel(value?: string | null): string {
    if (!value) return '';
    const date = new Date(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
  }

  /** "2 hours ago" for the list; the dialog says the whole date. */
  ageLabel(value?: string | null): string {
    if (!value) return '';
    const then = new Date(value).getTime();
    if (isNaN(then)) return '';
    const minutes = Math.round((Date.now() - then) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} h ago`;
    const days = Math.round(hours / 24);
    if (days < 30) return `${days} d ago`;
    return new Date(value).toLocaleDateString();
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
      // A first record of a kind made from the overview: show it.
      if (!editing) this.selectKind(shape.agent_ref, shape.resource_id);
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

  /** What is about to go, as a person reads a list. */
  get deletingNames(): string {
    const names = this.deleting.map((record) => this.recordName(record));
    if (names.length <= 3) return names.join(', ');
    return `${names.slice(0, 3).join(', ')} and ${names.length - 3} more`;
  }

  requestDelete(record: AgentRecord): void {
    this.deleting = [record];
    this.error = '';
  }

  requestDeleteSelected(): void {
    this.deleting = this.selectedRecords;
    this.error = '';
  }

  closeDelete(): void {
    if (!this.deleteBusy) this.deleting = [];
  }

  /** One record or many, the same way: through the door that already
   *  checks who may delete what. A record that refuses does not stop the
   *  rest, and stays ticked so it is clear what did not go. */
  async confirmDelete(): Promise<void> {
    const going = this.deleting.slice();
    if (!going.length) return;
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
    this.deleting = [];
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
