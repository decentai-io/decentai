import { Component, OnDestroy, OnInit, TemplateRef, ViewChild } from '@angular/core';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';
import { Router } from '@angular/router';

import { fullDate } from 'src/app/components/ui/dates';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { McpServer, McpService } from 'src/app/services/mcp.service';
import { DataPageBase } from '../../data-page-base';
import {
  BLOCKED_WHY, CredentialDraft, ServerStatus, credentialWord, lastRead, readAt, serverStatus,
} from './mcp-shared';

/**
 * MCP servers: remote tool servers a person adds for their own chats.
 * The platform reads a server's tools when it is added; the person
 * looks them over — which are on, and what each costs to call — before
 * any chat may use one.
 *
 * This is the list. A row says how a server stands (on, off, not
 * reachable, off for the deployment), what it offers, what it is
 * reached with and when it was last read; its menu holds what can be
 * done from here, and leads to the server's own page
 * (mcp-server.component) for the rest.
 */
@Component({
  selector: 'app-mcp',
  standalone: false,
  templateUrl: './mcp.component.html',
  styleUrl: './mcp-list.css',
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
  readonly blockedWhy = BLOCKED_WHY;

  /** The server a row's action is on its way for. */
  busy = '';

  // Adding one: a dialog.
  @ViewChild('addDialog') private addDialog?: TemplateRef<unknown>;
  private dialogRef: MatDialogRef<unknown> | null = null;
  formName = '';
  formUrl = '';
  readonly credential = new CredentialDraft();
  /** Save was pressed: what is missing is said under its field. */
  tried = false;
  /** What the server or the platform said about the address. */
  addProblem = '';
  saving = false;

  constructor(
    private service: McpService,
    public auth: AuthService,
    private router: Router,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    await this.load();
    // Arriving from a server's page after it was removed there.
    const said = (history.state as { mcpNotice?: string } | null)?.mcpNotice;
    if (said) {
      history.replaceState({ ...history.state, mcpNotice: undefined }, '');
      this.flash(said);
    }
  }

  ngOnDestroy(): void {
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

  status(server: McpServer): ServerStatus {
    return serverStatus(server, this.blocked);
  }

  credentialWord(server: McpServer): string {
    return credentialWord(server);
  }

  lastRead(server: McpServer): string {
    return lastRead(server);
  }

  lastReadInFull(server: McpServer): string {
    return fullDate(readAt(server));
  }

  get foot(): string {
    return this.servers.length
      ? 'A chat asks before calling a tool whose level is above its trust level.' : '';
  }

  /** Takes the server an answer carries into the list. */
  private take(resource: McpServer): void {
    this.servers = this.servers.map((one) => one.resource_ref === resource.resource_ref ? resource : one);
  }

  // ── Adding ────────────────────────────────────────────────────────

  startAdd(): void {
    this.formName = this.formUrl = '';
    this.credential.reset();
    this.tried = false;
    this.addProblem = '';
    if (!this.addDialog || this.dialogRef) return;
    this.dialogRef = this.dialog.open(this.addDialog, { width: '480px', maxWidth: '100vw' });
    this.dialogRef.afterClosed().subscribe(() => (this.dialogRef = null));
  }

  closeDialog(): void {
    if (!this.saving) this.dialogRef?.close();
  }

  /** The dialog stays while the server is being read. */
  private hold(saving: boolean): void {
    this.saving = saving;
    if (this.dialogRef) this.dialogRef.disableClose = saving;
  }

  async add(): Promise<void> {
    if (this.saving) return;
    this.tried = true;
    this.addProblem = '';
    if (!this.formName.trim() || !this.formUrl.trim() || !this.credential.ready) return;
    this.hold(true);
    const result = await this.service.create({
      name: this.formName.trim(), url: this.formUrl.trim(), credential: this.credential.given(),
    });
    this.hold(false);
    if (result.error || !result.resource) {
      // The backend's sentence is about the address or what it is
      // reached with: said under the address, in the dialog.
      this.addProblem = result.error || 'The server could not be added.';
      return;
    }
    this.closeDialog();
    // Its own page is where its tools are looked over, and says what
    // adding it meant.
    void this.router.navigate(['/data/mcp', result.resource.resource_ref], { state: { mcpAdded: true } });
  }

  // ── What a row's menu does here ───────────────────────────────────

  async setEnabled(server: McpServer, enabled: boolean): Promise<void> {
    this.busy = server.resource_ref;
    const result = await this.service.update(server.resource_ref, { enabled });
    this.busy = '';
    if (result.error || !result.resource) return this.fail(result.error || 'That could not be saved.');
    this.take(result.resource);
    this.flash(enabled ? `${server.keys.name} is on.` : `${server.keys.name} is off. No chat can call it.`);
  }

  /** Read again from a row. A read that fails leaves the server as it
   *  was, with its row saying it cannot be reached. */
  async readAgain(server: McpServer): Promise<void> {
    this.busy = server.resource_ref;
    const result = await this.service.refresh(server.resource_ref);
    this.busy = '';
    if (result.resource) this.take(result.resource);
    if (result.error) return this.fail(`${server.keys.name}: ${result.error}`);
    this.flash(`${server.keys.name} read again. Anything new or changed is switched off until you look.`);
  }

  /** Asked first: its tools are gone from every chat at once. */
  async askRemove(server: McpServer): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Remove ${server.keys.name}?`,
      body: 'Your chats stop being able to call its tools at once, and its credential is deleted.',
      confirm: 'Remove',
      danger: true,
    });
    if (!sure) return;
    this.busy = server.resource_ref;
    const result = await this.service.remove(server.resource_ref);
    this.busy = '';
    if (result.error) return this.fail(result.error);
    this.servers = this.servers.filter((one) => one.resource_ref !== server.resource_ref);
    this.flash(`${server.keys.name} removed.`);
  }
}
