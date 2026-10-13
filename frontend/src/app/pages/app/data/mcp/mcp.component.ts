import { Component, OnDestroy, OnInit, TemplateRef, ViewChild } from '@angular/core';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { McpCredential, McpServer, McpService, McpTool } from 'src/app/services/mcp.service';
import { DataPageBase } from '../../data-page-base';

type CredentialKind = 'none' | 'token' | 'header';

interface Level { value: 0 | 1 | 2 | 3; label: string; help: string; }

/**
 * MCP servers: remote tool servers a person adds for their own chats.
 * The platform reads a server's tools when it is added; this page is
 * where the person looks them over — which are on, and what each costs
 * to call — before any chat may use one.
 *
 * One list. A row says how a server stands (on, off, not reachable,
 * off for the deployment), what it offers, what it is reached with and
 * when it was last read; its menu holds everything that can be done to
 * it. A server's name opens it at /data/mcp/<ref>.
 */
@Component({
  selector: 'app-mcp',
  standalone: false,
  templateUrl: './mcp.component.html',
  // The server's own view is still on the old stylesheets until it is
  // a page of its own.
  styleUrls: ['../../data-shared.css', '../../../admin/iam-shared.css', './mcp.component.css', './mcp-list.css'],
})
export class McpComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  /** Why the list could not be read; '' when it was. */
  loadProblem = '';
  servers: McpServer[] = [];
  query = '';
  /** MCP is switched off for the deployment: what is listed stays
   *  listed, and no chat can call any of it. */
  blocked = false;

  /** The scale every function is priced on; a chat asks first for
   *  anything above its own level. */
  readonly levels: Level[] = [
    { value: 0, label: 'Reads', help: 'Changes nothing.' },
    { value: 1, label: 'Changes', help: 'An ordinary change.' },
    { value: 2, label: 'Wider changes', help: 'A change with wider reach, still inside the platform.' },
    { value: 3, label: 'Acts outside', help: 'Sends, posts, buys: leaves the platform.' },
  ];

  readonly blockedWhy = 'MCP servers are off for this deployment';
  readonly credentialKinds: Choice<CredentialKind>[] = [
    { value: 'none', name: 'None' },
    { value: 'token', name: 'A token' },
    { value: 'header', name: 'A header' },
  ];

  // Adding one, and changing a name or an address: each in a dialog.
  @ViewChild('addDialog') private addDialog?: TemplateRef<unknown>;
  @ViewChild('changeDialog') private changeDialog?: TemplateRef<unknown>;
  private dialogRef: MatDialogRef<unknown> | null = null;
  /** Save was pressed: what is missing is said under its field. */
  tried = false;
  /** What the server or the platform said about the address. */
  addProblem = '';
  changeTarget: McpServer | null = null;
  changeKind: 'name' | 'url' = 'name';
  changeValue = '';
  changeProblem = '';

  private address = new Subscription();

  formName = '';
  formUrl = '';
  credentialKind: CredentialKind = 'none';
  formToken = '';
  formHeader = '';
  formValue = '';
  saving = false;

  // Looking one over
  open: McpServer | null = null;
  opening = '';
  busy = '';
  /** Replacing the credential of the open server. */
  replacing = false;

  constructor(
    private service: McpService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    this.address.add(this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) void this.readOpened();
    }));
    await this.load();
    await this.readOpened();
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
    this.dialogRef?.close();
  }

  async load(): Promise<void> {
    this.loading = true;
    const page = await this.service.list();
    this.servers = page.servers;
    this.blocked = page.blocked;
    this.loadProblem = page.error || '';
    this.loading = false;
  }

  /** Opens the server the address names, or the list when it names none. */
  private async readOpened(): Promise<void> {
    const ref = this.route.snapshot.firstChild?.paramMap.get('ref') || '';
    if (!ref) {
      this.open = null;
      return;
    }
    if (this.open?.resource_ref === ref || this.opening === ref) return;
    const server = this.servers.find((one) => one.resource_ref === ref);
    if (server) return this.look(server);
    if (!this.loading) {
      this.fail('That server is not one of yours any more.');
      this.close();
    }
  }

  /** May add at all; whether the deployment lets anyone is `blocked`. */
  get mayAdd(): boolean {
    return this.auth.can('mcp:server:create');
  }

  // ── The list ──────────────────────────────────────────────────────

  get shownServers(): McpServer[] {
    const query = this.query.trim().toLowerCase();
    if (!query) return this.servers;
    return this.servers.filter((server) =>
      `${server.keys.name} ${server.keys.host} ${server.keys.server}`.toLowerCase().includes(query));
  }

  /** How a server stands, as its row's chip. One function, so the list
   *  and the server's own page cannot say two things. */
  status(server: McpServer): { word: string; tone: string; why: string } {
    if (server.unreadable) {
      return { word: 'Unreadable', tone: 'warn', why: 'Its details can no longer be read. Remove it and add it again.' };
    }
    if (this.blocked) return { word: 'Off for this deployment', tone: '', why: this.blockedWhy };
    if (server.keys.last_read_error) {
      return { word: 'Cannot be reached', tone: 'danger', why: server.keys.last_read_error };
    }
    return server.keys.enabled
      ? { word: 'On', tone: 'accent', why: 'Your chats can call the tools you kept on' }
      : { word: 'Off', tone: '', why: 'No chat can call it' };
  }

  /** What it is reached with, as the backend words it; '' for a server
   *  added before rows carried it. */
  credentialWord(server: McpServer): string {
    const kind = server.keys.credential || '';
    return kind === 'none' ? 'no credential' : kind;
  }

  private readAt(server: McpServer): string {
    return server.keys.last_read_at || server.updated_at || '';
  }

  lastRead(server: McpServer): string {
    // Mid-sentence: "read just now".
    return relativeDate(this.readAt(server)).replace(/^J/, 'j');
  }

  lastReadInFull(server: McpServer): string {
    return fullDate(this.readAt(server));
  }

  get foot(): string {
    return this.servers.length
      ? 'A chat asks before calling a tool whose level is above its trust level.' : '';
  }

  // ── Adding ────────────────────────────────────────────────────────

  startAdd(): void {
    this.formName = this.formUrl = this.formToken = this.formHeader = this.formValue = '';
    this.credentialKind = 'none';
    this.tried = false;
    this.addProblem = '';
    this.openDialog(this.addDialog);
  }

  private openDialog(template?: TemplateRef<unknown>): void {
    if (!template || this.dialogRef) return;
    this.dialogRef = this.dialog.open(template, { width: '480px', maxWidth: '100vw' });
    this.dialogRef.afterClosed().subscribe(() => (this.dialogRef = null));
  }

  closeDialog(): void {
    if (!this.saving) this.dialogRef?.close();
  }

  /** A dialog stays while what it asked for is on its way. */
  private hold(saving: boolean): void {
    this.saving = saving;
    if (this.dialogRef) this.dialogRef.disableClose = saving;
  }

  get addReady(): boolean {
    return !!this.formName.trim() && !!this.formUrl.trim() && this.credentialReady;
  }

  get credentialReady(): boolean {
    if (this.credentialKind === 'token') return !!this.formToken.trim();
    if (this.credentialKind === 'header') return !!this.formHeader.trim() && !!this.formValue.trim();
    return true;
  }

  private credential(): McpCredential {
    if (this.credentialKind === 'token') return { token: this.formToken.trim() };
    if (this.credentialKind === 'header') {
      return { header: this.formHeader.trim(), value: this.formValue.trim() };
    }
    return {};
  }

  async add(): Promise<void> {
    if (this.saving) return;
    this.tried = true;
    this.addProblem = '';
    if (!this.addReady) return;
    this.hold(true);
    const result = await this.service.create({
      name: this.formName.trim(), url: this.formUrl.trim(), credential: this.credential(),
    });
    this.hold(false);
    if (result.error || !result.resource) {
      // The backend's sentence is about the address or what it is
      // reached with: said under the address, in the dialog.
      this.addProblem = result.error || 'The server could not be added.';
      return;
    }
    this.closeDialog();
    this.servers = [...this.servers, result.resource];
    this.open = result.resource;
    this.replacing = false;
    void this.router.navigate(['/data/mcp', result.resource.resource_ref]);
    this.flash('Connected. Its tools are on at the highest level, so every chat asks before using '
      + 'one; lower the level of the tools you trust.');
  }

  // ── A new name, or a new address ──────────────────────────────────

  startChange(server: McpServer, kind: 'name' | 'url'): void {
    this.changeTarget = server;
    this.changeKind = kind;
    this.changeValue = kind === 'name' ? server.keys.name : server.keys.url;
    this.changeProblem = '';
    this.tried = false;
    this.openDialog(this.changeDialog);
  }

  async saveChange(): Promise<void> {
    const server = this.changeTarget;
    if (!server || this.saving) return;
    this.tried = true;
    this.changeProblem = '';
    const value = this.changeValue.trim();
    if (!value) return;
    this.hold(true);
    const result = await this.service.update(server.resource_ref,
      this.changeKind === 'name' ? { name: value } : { url: value });
    this.hold(false);
    if (result.error || !result.resource) {
      this.changeProblem = result.error || 'That could not be saved.';
      return;
    }
    this.closeDialog();
    this.kept(result, this.changeKind === 'name' ? 'Renamed.' : 'Address changed, and the server read again there.');
  }

  // ── Looking one over ──────────────────────────────────────────────

  async look(server: McpServer): Promise<void> {
    this.opening = server.resource_ref;
    const full = await this.service.get(server.resource_ref);
    this.opening = '';
    if (!full) {
      this.fail('That server could not be read.');
      return;
    }
    this.open = full;
    this.replacing = false;
    this.error = '';
  }

  close(): void {
    this.open = null;
    void this.router.navigate(['/data/mcp']);
  }

  /** Read again from a row. A read that fails leaves the server as it
   *  was, with its row saying it cannot be reached. */
  async readAgain(server: McpServer): Promise<void> {
    this.busy = server.resource_ref;
    const result = await this.service.refresh(server.resource_ref);
    this.busy = '';
    if (result.resource) {
      const resource = result.resource;
      this.servers = this.servers.map((one) => one.resource_ref === resource.resource_ref ? resource : one);
    }
    if (result.error) return this.fail(`${server.keys.name}: ${result.error}`);
    this.flash(`${server.keys.name} read again. Anything new or changed is switched off until you look.`);
  }

  /** Replacing the credential is done on the server's own view. */
  async replaceOn(server: McpServer): Promise<void> {
    await this.router.navigate(['/data/mcp', server.resource_ref]);
    await this.look(server);
    this.startReplace();
  }

  get tools(): McpTool[] {
    return this.open?.values?.tools || [];
  }

  toolNote(tool: McpTool): string {
    if (tool.changed === 'new') return 'New since you last looked. Switched off until you switch it on.';
    if (tool.changed === 'changed') return 'Its description or inputs changed. Switched off until you switch it on.';
    return '';
  }

  /** Take the server an answer carries. False when it was refused. */
  private kept(result: { resource?: McpServer; error?: string }, said: string): boolean {
    this.busy = '';
    if (result.error || !result.resource) {
      this.fail(result.error || 'That could not be saved.');
      return false;
    }
    const resource = result.resource;
    this.servers = this.servers.map((one) =>
      one.resource_ref === resource.resource_ref ? resource : one);
    if (this.open?.resource_ref === resource.resource_ref) this.open = resource;
    if (said) this.flash(said);
    return true;
  }

  async setEnabled(server: McpServer, enabled: boolean): Promise<void> {
    this.busy = server.resource_ref;
    this.kept(await this.service.update(server.resource_ref, { enabled }),
      enabled ? `${server.keys.name} is on.` : `${server.keys.name} is off. No chat can call it.`);
  }

  async setTool(tool: McpTool, change: { enabled?: boolean; level?: number }): Promise<boolean> {
    if (!this.open) return false;
    this.busy = this.open.resource_ref;
    return this.kept(await this.service.update(this.open.resource_ref, {
      tools: [{ id: tool.id, ...change }],
    }), '');
  }

  /** The box was ticked by the click before anything was saved. When
   *  the save is refused it goes back to what the tool is. */
  async setSwitch(tool: McpTool, box: HTMLInputElement): Promise<void> {
    if (!(await this.setTool(tool, { enabled: !tool.enabled }))) {
      box.checked = tool.enabled;
    }
  }

  async setLevel(tool: McpTool, select: HTMLSelectElement): Promise<void> {
    if (!(await this.setTool(tool, { level: Number(select.value) }))) {
      select.value = String(tool.level);
    }
  }

  async refresh(): Promise<void> {
    if (!this.open) return;
    this.busy = this.open.resource_ref;
    this.kept(await this.service.refresh(this.open.resource_ref),
      'Read again. Anything new or changed is switched off until you look.');
  }

  startReplace(): void {
    this.replacing = true;
    this.credentialKind = 'token';
    this.formToken = this.formHeader = this.formValue = '';
  }

  async replace(): Promise<void> {
    if (!this.open || !this.credentialReady) return;
    this.busy = this.open.resource_ref;
    const result = await this.service.update(this.open.resource_ref, {
      credential: this.credential(),
    });
    if (!result.error) this.replacing = false;
    this.kept(result, 'Credential replaced, and the server read again with it.');
  }

  // ── Removing ──────────────────────────────────────────────────────

  /** Asked first: its tools are gone from every chat at once. */
  async askRemove(server: McpServer): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Remove ${server.keys.name}?`,
      body: 'Your chats stop being able to call its tools at once, and its credential is deleted.',
      confirm: 'Remove',
      danger: true,
    });
    if (sure) await this.remove(server);
  }

  async remove(server: McpServer): Promise<void> {
    this.busy = server.resource_ref;
    const result = await this.service.remove(server.resource_ref);
    this.busy = '';
    if (result.error) {
      this.fail(result.error);
      return;
    }
    this.servers = this.servers.filter((one) => one.resource_ref !== server.resource_ref);
    if (this.open?.resource_ref === server.resource_ref) this.close();
    this.flash(`${server.keys.name} removed.`);
  }
}
