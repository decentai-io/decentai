import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';

import { AiSessionService } from 'src/app/services/ai-session.service';
import { AuthService } from 'src/app/services/auth.service';
import { FileResource, FilesService } from 'src/app/services/files.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import { DataPageBase } from '../../data-page-base';
import { ShareEditor } from '../../share-editor';
import { FilePickerComponent } from 'src/app/pages/app/ai/chat/file-picker/file-picker.component';

/** One source of files: uploads, chats, or one agent — a card on the
 *  overview, and the workspace once chosen. */
export interface FileSource {
  /** 'uploaded', 'chats', or an agent's ref. */
  id: string;
  name: string;
  kind: 'uploaded' | 'chats' | 'agent';
  count: number;
  /** What it holds, by file type, most common first. */
  types: { label: string; count: number }[];
}

/**
 * Files: everything stored on the user's behalf, whatever created it — a
 * chat attachment, an agent's output, a direct upload. Read, download and
 * delete only; the platform owns a file's metadata, so there is nothing
 * here to edit.
 *
 * The same two levels as Saved data: an overview of where files come
 * from — uploads, chats, and one card per agent, named — and then one
 * source's files, flat, with a tick per file so several can go together.
 *
 * Chat titles are fetched to name the conversation an attachment came
 * from, and skipped without complaint when the user cannot list chats.
 */
@Component({
  selector: 'app-files',
  standalone: false,
  templateUrl: './files.component.html',
  styleUrls: [
    '../../data-shared.css',
    '../../../admin/iam-shared.css',
    './files.component.css',
  ],
})
export class FilesComponent extends DataPageBase implements OnInit {
  loading = true;
  files: FileResource[] = [];
  profile: Profile | null = null;

  activeTab: 'mine' | 'shared' = 'mine';
  /** '' = the overview; else one source's id. */
  activeSource = '';
  query = '';
  sourceQuery = '';
  sourcePage = 1;
  page = 1;
  readonly sourcePageSize = 12;
  readonly pageSize = 20;
  busyRef = '';
  uploading = false;

  /** What the confirmation is about: one file, or every one ticked. */
  deleting: FileResource[] = [];
  /** Files ticked to go together, by resource_ref. */
  selected = new Set<string>();
  bulkBusy = false;

  /** chat_id → title, for naming where an attachment came from. */
  private chatTitles = new Map<string, string>();

  /** Who can see a file. The platform owns everything else about it —
   *  this is the one thing about a file that is the person's to decide. */
  readonly share = new ShareEditor(() => this.profile);

  /** File types a card names before saying "and more". */
  private static readonly MAX_TYPES = 4;

  constructor(
    private service: FilesService,
    private aiSession: AiSessionService,
    private profiles: ProfileService,
    public auth: AuthService,
    private router: Router,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
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

  private async reload(): Promise<void> {
    this.files = await this.service.list();
    // A tick on a file that is no longer there means nothing.
    this.selected = new Set([...this.selected].filter((ref) =>
      this.files.some((file) => file.resource_ref === ref)));
    // The overview is derived from the files, so deleting the last of
    // a source leaves the workspace describing nothing: back to the
    // sources that remain.
    if (this.activeSource && !this.activeSourceNode) this.selectAll();
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

  agentName(file: FileResource): string {
    return file.agent?.name || this.agentRefOf(file);
  }

  sourceOf(file: FileResource): string {
    return this.agentRefOf(file) || (this.chatIdOf(file) ? 'chats' : 'uploaded');
  }

  /** The overview: uploads, chats, and one card per agent that stored
   *  something — in that order, agents by name. */
  get sourceNodes(): FileSource[] {
    const nodes = new Map<string, FileSource>();
    const typeCounts = new Map<string, Map<string, number>>();
    for (const file of this.activeFiles) {
      const id = this.sourceOf(file);
      const node = nodes.get(id) ?? {
        id,
        name: id === 'uploaded' ? 'Uploaded' : id === 'chats' ? 'From chats' : this.agentName(file),
        kind: id === 'uploaded' ? 'uploaded' as const : id === 'chats' ? 'chats' as const : 'agent' as const,
        count: 0,
        types: [],
      };
      node.count += 1;
      nodes.set(id, node);
      const types = typeCounts.get(id) ?? new Map<string, number>();
      const label = this.extensionLabel(file);
      types.set(label, (types.get(label) ?? 0) + 1);
      typeCounts.set(id, types);
    }
    const order = (node: FileSource) => node.kind === 'uploaded' ? 0 : node.kind === 'chats' ? 1 : 2;
    return [...nodes.values()]
      .map((node) => ({
        ...node,
        types: [...(typeCounts.get(node.id) ?? new Map<string, number>()).entries()]
          .map(([label, count]) => ({ label, count }))
          .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
          .slice(0, FilesComponent.MAX_TYPES),
      }))
      .sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name));
  }

  get activeSourceNode(): FileSource | null {
    return this.sourceNodes.find((node) => node.id === this.activeSource) ?? null;
  }

  get filteredSourceNodes(): FileSource[] {
    const query = this.sourceQuery.trim().toLowerCase();
    if (!query) return this.sourceNodes;
    return this.sourceNodes.filter((node) =>
      `${node.name} ${node.types.map((type) => type.label).join(' ')}`.toLowerCase().includes(query));
  }

  get pagedSourceNodes(): FileSource[] {
    const start = (this.sourcePage - 1) * this.sourcePageSize;
    return this.filteredSourceNodes.slice(start, start + this.sourcePageSize);
  }

  get sourcePageCount(): number {
    return Math.max(1, Math.ceil(this.filteredSourceNodes.length / this.sourcePageSize));
  }

  sourceIcon(node: FileSource): string {
    return node.kind === 'agent' ? 'bot' : node.kind === 'chats' ? 'message-square' : 'upload';
  }

  selectAll(): void {
    this.activeSource = '';
    this.query = '';
    this.page = 1;
    this.selected.clear();
    this.share.close();
  }

  selectSource(id: string): void {
    this.activeSource = id;
    this.query = '';
    this.page = 1;
    this.selected.clear();
    this.share.close();
  }

  searchSources(value: string): void {
    this.sourceQuery = value;
    this.sourcePage = 1;
  }

  // ── Grouping ────────────────────────────────────────────────────────

  get myFiles(): FileResource[] {
    return this.files.filter((file) => file.created_by === this.myUserId);
  }

  get sharedFiles(): FileResource[] {
    return this.files.filter((file) => file.created_by !== this.myUserId);
  }

  get activeFiles(): FileResource[] {
    return this.activeTab === 'mine' ? this.myFiles : this.sharedFiles;
  }

  /** The chosen source's files, before the search. */
  get sourceFiles(): FileResource[] {
    return this.activeFiles.filter((file) => this.sourceOf(file) === this.activeSource);
  }

  get shownFiles(): FileResource[] {
    const pool = this.sourceFiles;
    const query = this.query.trim().toLowerCase();
    if (!query) return pool;
    return pool.filter((file) =>
      [this.filename(file), this.sourceLabel(file), this.keysSummary(file)]
        .join(' ').toLowerCase().includes(query));
  }

  get pagedFiles(): FileResource[] {
    const start = (this.page - 1) * this.pageSize;
    return this.shownFiles.slice(start, start + this.pageSize);
  }

  get pageCount(): number {
    return Math.max(1, Math.ceil(this.shownFiles.length / this.pageSize));
  }

  selectTab(tab: 'mine' | 'shared'): void {
    this.activeTab = tab;
    this.sourceQuery = '';
    this.sourcePage = 1;
    this.selectAll();
  }

  searchFiles(value: string): void {
    this.query = value;
    this.page = 1;
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

  /** "All" means all of what is on screen under the current source and
   *  search — never files the person cannot see from here. */
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

  get selectedFiles(): FileResource[] {
    return this.files.filter((file) => this.isSelected(file));
  }

  // ── Upload ──────────────────────────────────────────────────────────

  /** Upload straight from the page — the same door a chat attachment
   *  uses, minus the chat. What was uploaded is then on screen. */
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
      this.activeTab = 'mine';
      // Open what was uploaded — when something was. With every upload
      // refused there may be no such source, and a workspace for a
      // source that is not there shows nothing at all.
      if (this.sourceNodes.some((node) => node.id === 'uploaded')) {
        this.selectSource('uploaded');
      } else {
        this.selectAll();
      }
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

  /** The extension, which reads better in a list than a MIME type. */
  extensionLabel(file: FileResource): string {
    const name = this.filename(file);
    const dot = name.lastIndexOf('.');
    if (dot > 0 && dot < name.length - 1) {
      return name.slice(dot + 1).toUpperCase();
    }
    return (file.values?.file_type || 'file').split('/').pop()!.toUpperCase();
  }

  sizeLabel(file: FileResource): string {
    const bytes = Number(file.values?.file_size ?? 0);
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  chatIdOf(file: FileResource): string {
    return String(file.keys?.['chat_id'] || '');
  }

  /** Where the file came from, named as specifically as the metadata allows. */
  sourceLabel(file: FileResource): string {
    const ref = this.agentRefOf(file);
    if (ref) {
      const name = file.agent?.name;
      return name ? `From ${name}` : `From a former agent (${ref})`;
    }
    const chatId = this.chatIdOf(file);
    if (chatId) {
      const title = this.chatTitles.get(chatId);
      return title ? `From chat “${title}”` : 'From a chat';
    }
    const kind = String(file.keys?.['file_kind'] || '');
    return kind ? kind.replace(/_/g, ' ') : 'Uploaded';
  }

  /** Any caller metadata beyond what the row already spells out. */
  keysSummary(file: FileResource): string {
    return Object.entries(file.keys || {})
      .filter(([key]) => !['chat_id', 'file_kind', 'category'].includes(key))
      .map(([key, value]) => `${key}: ${value}`)
      .join(' · ');
  }

  shareSummary(file: FileResource): string {
    return this.share.summary(file.owner, file.created_by);
  }

  dateLabel(value?: string | null): string {
    if (!value) return '';
    const date = new Date(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
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

  get deletingNames(): string {
    const names = this.deleting.map((file) => this.filename(file));
    if (names.length <= 3) return names.join(', ');
    return `${names.slice(0, 3).join(', ')} and ${names.length - 3} more`;
  }

  /** Whether any of what is about to go is attached to a chat, whose
   *  message will be left pointing at a file that no longer exists. */
  get deletingFromChats(): boolean {
    return this.deleting.some((file) => !!this.chatIdOf(file));
  }

  requestDelete(file: FileResource): void {
    this.deleting = [file];
    this.error = '';
  }

  requestDeleteSelected(): void {
    this.deleting = this.selectedFiles;
    this.error = '';
  }

  closeDelete(): void {
    if (!this.deleteBusy) this.deleting = [];
  }

  /** One file or many, the same way: through the door that already
   *  checks who may delete what. A file that refuses does not stop the
   *  rest, and stays ticked so it is clear what did not go. */
  async confirmDelete(): Promise<void> {
    const going = this.deleting.slice();
    if (!going.length) return;
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
    this.deleting = [];
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
