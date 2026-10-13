import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { FilterGroup, FilterItem } from 'src/app/components/ui/filter-column.component';
import { Choice } from 'src/app/components/ui/radio-keys';
import { Sort } from 'src/app/components/ui/sort-header.component';
import { FilePickerComponent } from 'src/app/pages/app/ai/chat/file-picker/file-picker.component';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { FileResource, FilesService } from 'src/app/services/files.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import { DataPageBase } from '../../data-page-base';
import { ShareEditor } from '../../share-editor';
import { extensionOf, FILE_GROUPS, fileKindOf, sizeLabel } from 'src/app/components/ui/file-types';

/** Whose files are listed. */
type Show = 'all' | 'mine' | 'shared';

/** The sorts the address can name, each a column and a direction. The
 *  toolbar offers the first four; a column's heading reaches them all. */
const SORTS: Record<string, Sort> = {
  newest: { key: 'changed', direction: 'desc' },
  oldest: { key: 'changed', direction: 'asc' },
  name: { key: 'name', direction: 'asc' },
  size: { key: 'size', direction: 'desc' },
  'name-desc': { key: 'name', direction: 'desc' },
  'size-asc': { key: 'size', direction: 'asc' },
};

/** The address's name for a sort. */
function nameOfSort(sort: Sort): string {
  return Object.keys(SORTS).find((name) =>
    SORTS[name].key === sort.key && SORTS[name].direction === sort.direction) ?? 'newest';
}

/**
 * Files: everything stored on the user's behalf, whatever created it — a
 * chat attachment, an agent's output, a direct upload. Read, download,
 * share and delete; the platform owns a file's metadata, so there is
 * nothing here to edit.
 *
 * One list. The column at its left narrows it: whose files, where they
 * came from (uploads, chats, each agent), and what type they are. What
 * is chosen there, the search, the sort and the page are in the
 * address, so a view of the list can be linked to and returned to.
 *
 * Chat titles are fetched to name the conversation an attachment came
 * from, and skipped without complaint when the user cannot list chats.
 */
@Component({
  selector: 'app-files',
  standalone: false,
  templateUrl: './files.component.html',
  styleUrls: ['../../data-shared.css', './files.component.css'],
})
export class FilesComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  files: FileResource[] = [];
  profile: Profile | null = null;

  // ── What the address says ───────────────────────────────────────────
  show: Show = 'all';
  /** '', 'uploaded', 'chats', or an agent's ref. */
  source = '';
  /** '' or one of FILE_GROUPS. */
  type = '';
  query = '';
  sort: Sort = SORTS['newest'];
  page = 1;

  readonly pageSize = 20;
  readonly sortChoices: Choice[] = [
    { value: 'newest', name: 'Newest' },
    { value: 'oldest', name: 'Oldest' },
    { value: 'name', name: 'Name' },
    { value: 'size', name: 'Size' },
  ];

  busyRef = '';
  uploading = false;
  /** Files ticked to go together, by resource_ref. */
  selected = new Set<string>();
  bulkBusy = false;

  /** chat_id → title, for naming where an attachment came from. */
  private chatTitles = new Map<string, string>();
  private address?: Subscription;

  /** Who can see a file. The platform owns everything else about it —
   *  this is the one thing about a file that is the person's to decide. */
  readonly share = new ShareEditor(() => this.profile);

  constructor(
    private service: FilesService,
    private aiSession: AiSessionService,
    private profiles: ProfileService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
    private confirm: ConfirmService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    this.address = this.route.queryParamMap.subscribe((params) => {
      const show = params.get('show');
      this.show = show === 'mine' || show === 'shared' ? show : 'all';
      this.source = params.get('source') ?? '';
      this.type = params.get('type') ?? '';
      this.query = params.get('q') ?? '';
      this.sort = SORTS[params.get('sort') ?? ''] ?? SORTS['newest'];
      this.page = Math.max(1, Number(params.get('page')) || 1);
    });

    const [files, profile] = await Promise.all([
      this.service.list(),
      this.profiles.get(),
    ]);
    this.files = files;
    this.profile = profile;
    this.loading = false;

    // The reach of a person-share: peers only, and the backend agrees.
    this.share.peers = await this.profiles.peers().catch(() => []);

    if (this.auth.can('ai:chat:list')) {
      // Titles only name where an attachment came from: a list that
      // cannot be read leaves "From a chat", not a broken page.
      const chats = await this.aiSession.listChats().catch(() => []);
      for (const chat of chats) {
        this.chatTitles.set(chat.chat_id, chat.title || 'Untitled chat');
      }
    }
  }

  ngOnDestroy(): void {
    this.address?.unsubscribe();
  }

  private async reload(): Promise<void> {
    this.files = await this.service.list();
    // A tick on a file that is no longer there means nothing.
    this.selected = new Set([...this.selected].filter((ref) =>
      this.files.some((file) => file.resource_ref === ref)));
  }

  /** Changes what the address says, and so what is listed. A value of
   *  '' (or the default) leaves the address without it. */
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

  // ── Access ──────────────────────────────────────────────────────────

  get canDownload(): boolean {
    return this.auth.can('files:file:download');
  }

  get canUpload(): boolean {
    return this.auth.can('files:file:upload');
  }

  get canDelete(): boolean {
    return this.auth.can('files:file:delete');
  }

  get canShare(): boolean {
    return this.auth.can('files:file:update');
  }

  get canTransfer(): boolean {
    return this.auth.can('files:file:transfer');
  }

  get canShareOrgWide(): boolean {
    return this.auth.can('files:file:set_owner_any');
  }

  /** "Use in a chat" opens a new chat with the file staged. */
  get canChat(): boolean {
    return this.auth.can('ai:chat:create');
  }

  get myUserId(): string {
    return this.profile?.user_id ?? '';
  }

  canTouch(file: FileResource): boolean {
    return file.created_by === this.myUserId;
  }

  // ── Where a file came from ──────────────────────────────────────────

  /** The agent's ref when an agent stored it; empty otherwise. The
   *  backend names it from the category key; an older row without the
   *  block is read the same way here. */
  agentRefOf(file: FileResource): string {
    if (file.agent?.agent_id) return file.agent.agent_id;
    const category = String(file.keys?.['category'] || '');
    const cut = category.indexOf('__');
    const ref = cut > 0 ? category.slice(0, cut) : '';
    return ref.startsWith('agt_') ? ref : '';
  }

  chatIdOf(file: FileResource): string {
    return String(file.keys?.['chat_id'] || '');
  }

  /** 'uploaded', 'chats', or the agent's ref. */
  sourceOf(file: FileResource): string {
    return this.agentRefOf(file) || (this.chatIdOf(file) ? 'chats' : 'uploaded');
  }

  /** Where the file came from, named as specifically as the metadata
   *  allows: the agent, the chat by its title, or that it was uploaded. */
  sourceLabel(file: FileResource): string {
    if (this.agentRefOf(file)) return file.agent?.name || 'A former agent';
    const chatId = this.chatIdOf(file);
    if (chatId) return this.chatTitles.get(chatId) || 'A chat';
    const kind = String(file.keys?.['file_kind'] || '');
    return kind ? kind.replace(/_/g, ' ') : 'Uploaded';
  }

  // ── The filters ─────────────────────────────────────────────────────

  private isMine(file: FileResource): boolean {
    return file.created_by === this.myUserId;
  }

  /** The files of the chosen "whose": what the sources and the types
   *  are counted in. */
  private get pool(): FileResource[] {
    if (this.show === 'mine') return this.files.filter((file) => this.isMine(file));
    if (this.show === 'shared') return this.files.filter((file) => !this.isMine(file));
    return this.files;
  }

  private typeOf(file: FileResource): string {
    const kind = fileKindOf(this.filename(file), file.values?.file_type);
    return FILE_GROUPS.find((group) => group.kinds.includes(kind))!.value;
  }

  get chosen(): Record<string, string> {
    return { show: this.show, source: this.source, type: this.type };
  }

  /** The column at the list's left: whose, then the sources and the
   *  types that hold something, each with its count. */
  get filters(): FilterGroup[] {
    const mine = this.files.filter((file) => this.isMine(file)).length;
    const pool = this.pool;

    const sources = new Map<string, FilterItem>();
    for (const file of pool) {
      const id = this.sourceOf(file);
      const item = sources.get(id) ?? this.sourceItem(id, file);
      item.count = (item.count ?? 0) + 1;
      sources.set(id, item);
    }
    // Uploads, then chats, then the agents by name.
    const rank = (item: FilterItem) => item.value === 'uploaded' ? 0 : item.value === 'chats' ? 1 : 2;

    return [
      {
        key: 'show',
        required: true,
        items: [
          { value: 'all', name: 'All', count: this.files.length },
          { value: 'mine', name: 'Mine', count: mine },
          { value: 'shared', name: 'Shared with me', count: this.files.length - mine },
        ],
      },
      {
        key: 'source',
        label: 'Sources',
        items: [...sources.values()].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name)),
      },
      {
        key: 'type',
        label: 'Types',
        items: FILE_GROUPS
          .map((group) => ({
            value: group.value,
            name: group.name,
            icon: group.icon,
            count: pool.filter((file) => this.typeOf(file) === group.value).length,
          }))
          .filter((item) => item.count > 0),
      },
    ].filter((group) => group.items.length > 0);
  }

  private sourceItem(id: string, file: FileResource): FilterItem {
    if (id === 'uploaded') return { value: id, name: 'Uploaded', icon: 'upload' };
    if (id === 'chats') return { value: id, name: 'From chats', icon: 'message-square' };
    const name = file.agent?.name || 'A former agent';
    return { value: id, name, agent: { name } };
  }

  filter(key: string, value: string): void {
    this.selected.clear();
    this.share.close();
    this.go({ [key]: key === 'show' && value === 'all' ? '' : value, page: null });
  }

  get filtered(): boolean {
    return !!(this.source || this.type || this.query || this.show !== 'all');
  }

  clearFilters(): void {
    this.go({ show: null, source: null, type: null, q: null, page: null });
  }

  search(value: string): void {
    this.go({ q: value, page: null });
  }

  // ── Sorting ─────────────────────────────────────────────────────────

  /** The toolbar's choice: the sort's name, which is none of its four
   *  when a heading set another (name, Z to A). */
  get sortChoice(): string {
    return nameOfSort(this.sort);
  }

  sortByChoice(name: string): void {
    this.go({ sort: name === 'newest' ? null : name, page: null });
  }

  /** A column heading was clicked. */
  sortBy(sort: Sort): void {
    this.sortByChoice(nameOfSort(sort));
  }

  private sorted(files: FileResource[]): FileResource[] {
    const way = this.sort.direction === 'asc' ? 1 : -1;
    const value = (file: FileResource): string | number =>
      this.sort.key === 'name' ? this.filename(file).toLowerCase()
        : this.sort.key === 'size' ? Number(file.values?.file_size ?? 0)
        : this.changedAt(file);
    return [...files].sort((a, b) => {
      const [left, right] = [value(a), value(b)];
      return (left < right ? -1 : left > right ? 1 : 0) * way;
    });
  }

  // ── What is listed ──────────────────────────────────────────────────

  get shownFiles(): FileResource[] {
    const query = this.query.trim().toLowerCase();
    return this.sorted(this.pool.filter((file) =>
      (!this.source || this.sourceOf(file) === this.source)
      && (!this.type || this.typeOf(file) === this.type)
      && (!query || [this.filename(file), this.sourceLabel(file), this.keysSummary(file)]
        .join(' ').toLowerCase().includes(query))));
  }

  get pagedFiles(): FileResource[] {
    const start = (this.page - 1) * this.pageSize;
    return this.shownFiles.slice(start, start + this.pageSize);
  }

  toPage(page: number): void {
    this.go({ page: page === 1 ? null : page });
  }

  // ── Choosing several at once ────────────────────────────────────────

  /** Whether this file is one the person may delete at all: the
   *  permission, and the rule that only whoever created it may touch it. */
  deletable(file: FileResource): boolean {
    return this.canDelete && this.canTouch(file);
  }

  isSelected(file: FileResource): boolean {
    return this.selected.has(file.resource_ref);
  }

  toggleSelected(file: FileResource): void {
    if (this.selected.has(file.resource_ref)) {
      this.selected.delete(file.resource_ref);
    } else {
      this.selected.add(file.resource_ref);
    }
  }

  /** "All" means all of what the filters and the search show — never
   *  files the person cannot see from here. */
  get selectableShown(): FileResource[] {
    return this.shownFiles.filter((file) => this.deletable(file));
  }

  get allShownSelected(): boolean {
    const shown = this.selectableShown;
    return shown.length > 0 && shown.every((file) => this.isSelected(file));
  }

  selectAllShown(): void {
    for (const file of this.selectableShown) this.selected.add(file.resource_ref);
  }

  clearSelection(): void {
    this.selected.clear();
  }

  // ── Upload ──────────────────────────────────────────────────────────

  /** Upload straight from the page — the same door a chat attachment
   *  uses, minus the chat. The list stays where it is; what was
   *  uploaded is at its top when it is sorted by newest. */
  async onUpload(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const chosen = Array.from(input.files ?? []);
    input.value = ''; // the same file can be picked again
    if (!chosen.length || this.uploading) return;

    this.uploading = true;
    try {
      const failed: string[] = [];
      for (const file of chosen) {
        const result = await this.service.upload(file);
        if (result.error) failed.push(`${file.name}: ${result.error}`);
      }
      await this.reload();
      if (failed.length) return this.fail(failed.join(' — '));
      this.flash(chosen.length === 1
        ? `"${chosen[0].name}" uploaded.`
        : `${chosen.length} files uploaded.`);
    } finally {
      this.uploading = false;
    }
  }

  // ── Display ─────────────────────────────────────────────────────────

  filename(file: FileResource): string {
    return file.values?.filename || 'Untitled file';
  }

  /** The name without its extension, and the extension: the row says
   *  the second more quietly. */
  stem(file: FileResource): string {
    const name = this.filename(file);
    const extension = extensionOf(name);
    return extension ? name.slice(0, -(extension.length + 1)) : name;
  }

  extension(file: FileResource): string {
    return extensionOf(this.filename(file));
  }

  sizeOf(file: FileResource): string {
    return sizeLabel(Number(file.values?.file_size ?? 0));
  }

  /** When the file last changed: files are written once, so for most
   *  this is when they were stored. */
  private changedAt(file: FileResource): string {
    return file.updated_at || file.created_at || '';
  }

  changed(file: FileResource): string {
    return relativeDate(this.changedAt(file));
  }

  changedInFull(file: FileResource): string {
    return fullDate(this.changedAt(file));
  }

  /** Any caller metadata beyond what the row already spells out. */
  keysSummary(file: FileResource): string {
    return Object.entries(file.keys || {})
      .filter(([key]) => !['chat_id', 'file_kind', 'category'].includes(key))
      .map(([key, value]) => `${key}: ${value}`)
      .join(' · ');
  }

  /** Who sees the file, as the row's chip: a word, and the names
   *  behind it where the word is a count. Somebody else's file says
   *  whose it is, which is what the reader does not know. */
  sharing(file: FileResource): { word: string; names: string } {
    const groups = file.owner?.groups ?? [];
    const everyone = groups.includes('everyone');
    const named = (ids: string[], names: string[] | undefined, skip: (id: string) => boolean) =>
      ids.map((id, index) => ({ id, name: names?.[index] || '' }))
        .filter((entry) => !skip(entry.id)).map((entry) => entry.name || 'someone who left');
    const groupNames = named(groups, file.owner_names?.groups, (id) => id === 'everyone');
    const people = named(file.owner?.users ?? [], file.owner_names?.users,
      (id) => id === this.myUserId || id === file.created_by);

    if (!this.isMine(file)) {
      return {
        word: `Shared by ${file.created_by_name || 'a colleague'}`,
        names: everyone ? 'With everyone in the organization'
          : groupNames.length ? `With ${groupNames.join(', ')}` : '',
      };
    }
    if (everyone) return { word: 'Everyone', names: 'Everyone in the organization' };
    if (groupNames.length) {
      return groupNames.length === 1 ? { word: groupNames[0], names: 'A group' }
        : { word: `${groupNames.length} groups`, names: groupNames.join(', ') };
    }
    if (people.length) {
      return people.length === 1 ? { word: people[0], names: 'One person' }
        : { word: `${people.length} people`, names: people.join(', ') };
    }
    return { word: 'Private', names: '' };
  }

  // ── Actions ─────────────────────────────────────────────────────────

  /** A new chat with this file staged in the composer, by reference —
   *  the person says what to do with it. */
  async useInChat(file: FileResource): Promise<void> {
    if (this.busyRef) return;
    this.busyRef = file.resource_ref;
    this.error = '';
    try {
      const created = await this.aiSession.createChat(this.filename(file).slice(0, 80), {});
      const chatId = created.data?.chat?.chat_id;
      if (!chatId) {
        this.error = created.error || 'Could not open a chat for it.';
        return;
      }
      this.router.navigate(['ai/chats', chatId], {
        state: { stagedFiles: [FilePickerComponent.toChoice(file)] },
      });
    } finally {
      this.busyRef = '';
    }
  }

  download(file: FileResource): void {
    const link = document.createElement('a');
    link.href = this.service.downloadUrl(file.resource_ref);
    link.download = this.filename(file);
    link.click();
  }

  openShare(file: FileResource): void {
    if (this.share.isOpen(file.resource_ref)) return this.share.close();
    this.share.open(file.resource_ref, file.owner);
  }

  async saveShare(file: FileResource): Promise<void> {
    this.share.saving = true;
    try {
      const result = await this.service.setOwner(
        file.resource_ref,
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

  // ── Deleting ────────────────────────────────────────────────────────

  get deleteBusy(): boolean {
    return !!this.busyRef || this.bulkBusy;
  }

  deleteOne(file: FileResource): Promise<void> {
    return this.delete([file]);
  }

  deleteSelected(): Promise<void> {
    return this.delete(this.files.filter((file) => this.isSelected(file)));
  }

  /** One file or many, the same way: asked first, then through the door
   *  that already checks who may delete what. A file that refuses does
   *  not stop the rest, and stays ticked so it is clear what did not go. */
  private async delete(going: FileResource[]): Promise<void> {
    if (!going.length) return;
    const names = going.map((file) => this.filename(file));
    const listed = names.length <= 3
      ? names.join(', ')
      : `${names.slice(0, 3).join(', ')} and ${names.length - 3} more`;
    // A chat's message would be left pointing at a file that is gone.
    const fromChats = going.some((file) => !!this.chatIdOf(file))
      ? (going.length === 1 ? ' It is attached to a chat, whose message will point at nothing.'
        : ' Some are attached to chats, whose messages will point at nothing.')
      : '';
    const sure = await this.confirm.ask({
      title: going.length === 1 ? 'Delete this file?' : `Delete ${going.length} files?`,
      body: `${listed}. Deleted files cannot be recovered.${fromChats}`,
      confirm: going.length === 1 ? 'Delete file' : `Delete ${going.length} files`,
      danger: true,
    });
    if (!sure) return;

    const gone: string[] = [];
    const refused: string[] = [];
    this.bulkBusy = true;
    try {
      for (const file of going) {
        this.busyRef = file.resource_ref;
        const result = await this.service.remove(file.resource_ref);
        if (result.error) {
          refused.push(this.filename(file));
          continue;
        }
        gone.push(this.filename(file));
        this.selected.delete(file.resource_ref);
      }
    } finally {
      this.busyRef = '';
      this.bulkBusy = false;
    }
    await this.reload();
    if (refused.length) {
      return this.fail(`Could not delete ${refused.join(', ')}.`);
    }
    this.flash(gone.length === 1 ? 'File deleted.' : `${gone.length} files deleted.`);
  }

  // ── Handing over ────────────────────────────────────────────────────

  transferTarget: FileResource | null = null;
  transferring = false;

  requestTransfer(item: FileResource): void {
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
