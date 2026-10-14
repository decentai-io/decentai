import { Component, OnDestroy, OnInit, TemplateRef, ViewChild } from '@angular/core';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';
import { ActivatedRoute, Router } from '@angular/router';

import { fullDate } from 'src/app/components/ui/dates';
import { ConfirmService } from 'src/app/services/confirm.service';
import { McpServer, McpService, McpTool } from 'src/app/services/mcp.service';
import {
  BLOCKED_WHY, CredentialDraft, ServerStatus, TOOL_LEVELS, credentialWord, lastRead, readAt, serverStatus,
  needsChip, switchTip,
} from './mcp-shared';

/** One input a tool takes, read from its JSON schema. */
interface ToolInput {
  name: string;
  type: string;
  required: boolean;
  about: string;
}

/** How long "Saved" stays beside a tool. */
const SAVED_FOR_MS = 2000;

/**
 * One MCP server, looked over: how it stands, what it is reached with,
 * and every tool it offers with its switch and what it may cost to
 * call. This is where a person decides what their chats may use, so
 * each tool saves itself and says so on its own row, and nothing else
 * on the page waits for it.
 *
 * A server does not say what its tools do to the world, so each starts
 * at the highest level and every chat asks before using one; lowering
 * a level is the person saying they know what the tool does.
 */
@Component({
  selector: 'app-mcp-server',
  standalone: false,
  templateUrl: './mcp-server.component.html',
  styleUrl: './mcp-server.component.css',
})
export class McpServerComponent implements OnInit, OnDestroy {
  loading = true;
  /** Why the server could not be opened. */
  problem = '';
  server: McpServer | null = null;
  blocked = false;
  readonly blockedWhy = BLOCKED_WHY;
  readonly levels = TOOL_LEVELS;

  /** Said once at the top: what adding it meant, or what was done. */
  notice = '';
  /** Why the last read, or the last change of the server itself, failed. */
  error = '';
  /** 'read' while it is read again; 'switch' while it is switched. */
  busy: '' | 'read' | 'switch' | 'all' = '';

  query = '';
  /** How each tool's last save went, by id: on its way, done, or why not. */
  private saves = new Map<string, string>();

  // Changing the name, the address or the credential: one dialog.
  @ViewChild('changeDialog') private changeDialog?: TemplateRef<unknown>;
  private dialogRef: MatDialogRef<unknown> | null = null;
  changing: 'name' | 'url' | 'credential' = 'name';
  changeValue = '';
  changeProblem = '';
  readonly credential = new CredentialDraft();
  tried = false;
  saving = false;

  constructor(
    private service: McpService,
    private route: ActivatedRoute,
    private router: Router,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {}

  async ngOnInit(): Promise<void> {
    const ref = this.route.snapshot.paramMap.get('ref') || '';
    const [server, page] = await Promise.all([this.service.get(ref), this.service.list()]);
    this.blocked = page.blocked;
    this.server = server;
    this.loading = false;
    if (!server) {
      this.problem = 'That server could not be opened. It may have been removed, or is not yours.';
      return;
    }
    if ((history.state as { mcpAdded?: boolean } | null)?.mcpAdded) {
      history.replaceState({ ...history.state, mcpAdded: undefined }, '');
      this.notice = 'Connected. Its tools are on at the highest level, so every chat asks before using one; '
        + 'lower the level of the tools you trust.';
    }
    // The list's menu leads here to change one thing.
    const change = this.route.snapshot.queryParamMap.get('change');
    if (change === 'name' || change === 'url' || change === 'credential') {
      void this.router.navigate([], { relativeTo: this.route, queryParams: {}, replaceUrl: true });
      if (!this.blocked) setTimeout(() => this.startChange(change));
    }
  }

  ngOnDestroy(): void {
    this.dialogRef?.close();
  }

  // ── How it stands ───────────────────────────────────────────────────

  get status(): ServerStatus {
    return serverStatus(this.server!, this.blocked);
  }

  get credentialWord(): string {
    const word = credentialWord(this.server!);
    if (word) return word;
    return this.server!.values?.has_credential ? 'a credential' : 'no credential';
  }

  get lastRead(): string {
    return lastRead(this.server!);
  }

  get lastReadInFull(): string {
    return fullDate(readAt(this.server!));
  }

  /** Why nothing but Remove can be done, for a tooltip; '' when it can. */
  get off(): string {
    return this.blocked ? this.blockedWhy : '';
  }

  /** On or off is the switch's to show; the chip stays for the rest. */
  get showChip(): boolean {
    return needsChip(this.status);
  }

  /** What the server's switch means, said when it is pointed at. */
  get switchTip(): string {
    return switchTip(this.server!, this.blocked);
  }

  // ── The tools ───────────────────────────────────────────────────────

  get tools(): McpTool[] {
    return this.server?.values?.tools || [];
  }

  get shownTools(): McpTool[] {
    const query = this.query.trim().toLowerCase();
    if (!query) return this.tools;
    return this.tools.filter((tool) => `${tool.name} ${tool.description}`.toLowerCase().includes(query));
  }

  get onCount(): number {
    return this.tools.filter((tool) => tool.enabled).length;
  }

  byId(_: number, tool: McpTool): string { return tool.id; }

  /** What a tool takes, as rows: the schema's properties. */
  inputsOf(tool: McpTool): ToolInput[] {
    const schema = (tool.inputs || {}) as { properties?: Record<string, any>; required?: string[] };
    const required = schema.required || [];
    return Object.entries(schema.properties || {}).map(([name, property]) => ({
      name,
      type: Array.isArray(property?.type) ? property.type.join(' or ') : String(property?.type || 'any'),
      required: required.includes(name),
      about: String(property?.description || ''),
    }));
  }

  /** Why a tool came back switched off after a read. */
  changeWord(tool: McpTool): string {
    return tool.changed === 'new' ? 'new' : tool.changed === 'changed' ? 'changed' : '';
  }

  changeWhy(tool: McpTool): string {
    return tool.changed === 'new'
      ? 'New since the last read. Off until you switch it on.'
      : 'Its description or inputs changed since the last read. Off until you switch it on.';
  }

  /** How a tool's last save went: 'Saving…', 'Saved', or the refusal. */
  saveOf(tool: McpTool): string {
    return this.saves.get(tool.id) || '';
  }

  saveFailed(tool: McpTool): boolean {
    const said = this.saveOf(tool);
    return !!said && said !== 'Saving…' && said !== 'Saved';
  }

  /** One tool's switch or level. Only its row waits and says how it
   *  went; a refusal leaves the tool as it was, with the reason. */
  async setTool(tool: McpTool, change: { enabled?: boolean; level?: number }): Promise<void> {
    const server = this.server;
    if (!server || this.blocked) return;
    this.saves.set(tool.id, 'Saving…');
    const result = await this.service.update(server.resource_ref, { tools: [{ id: tool.id, ...change }] });
    if (result.error || !result.resource) {
      this.saves.set(tool.id, result.error || 'That could not be saved.');
      // The controls go back to what the tool is.
      this.server = { ...server };
      return;
    }
    this.server = result.resource;
    this.saves.set(tool.id, 'Saved');
    setTimeout(() => {
      if (this.saves.get(tool.id) === 'Saved') this.saves.delete(tool.id);
    }, SAVED_FOR_MS);
  }

  /** Every tool on, or every tool off, in one save. */
  async setAll(enabled: boolean): Promise<void> {
    const server = this.server;
    if (!server || this.busy || this.blocked) return;
    this.busy = 'all';
    this.error = '';
    const result = await this.service.update(server.resource_ref, {
      tools: this.tools.map((tool) => ({ id: tool.id, enabled })),
    });
    this.busy = '';
    if (result.error || !result.resource) {
      this.error = result.error || 'That could not be saved.';
      return;
    }
    this.server = result.resource;
    this.notice = enabled ? 'Every tool is on.' : 'Every tool is off.';
  }

  // ── The server itself ───────────────────────────────────────────────

  async setEnabled(enabled: boolean): Promise<void> {
    const server = this.server;
    if (!server || this.busy) return;
    this.busy = 'switch';
    this.error = '';
    const result = await this.service.update(server.resource_ref, { enabled });
    this.busy = '';
    if (result.error || !result.resource) {
      this.error = result.error || 'That could not be saved.';
      this.server = { ...server };
      return;
    }
    this.server = result.resource;
  }

  /** Reads again what the server offers. A read that fails leaves the
   *  tools as they were, and the page says the server cannot be reached. */
  async readAgain(): Promise<void> {
    const server = this.server;
    if (!server || this.busy) return;
    this.busy = 'read';
    this.error = '';
    this.notice = '';
    const result = await this.service.refresh(server.resource_ref);
    this.busy = '';
    if (result.resource) this.server = { ...result.resource, values: result.resource.values ?? server.values };
    if (result.error) {
      this.error = result.error;
      return;
    }
    const held = this.tools.filter((tool) => tool.changed).length;
    this.notice = held
      ? `Read again. ${held} ${held === 1 ? 'tool is' : 'tools are'} new or changed, and off until you look.`
      : 'Read again. Nothing changed.';
  }

  // ── A new name, address or credential ───────────────────────────────

  startChange(what: 'name' | 'url' | 'credential'): void {
    const server = this.server;
    if (!server || !this.changeDialog || this.dialogRef) return;
    this.changing = what;
    this.changeValue = what === 'name' ? server.keys.name : what === 'url' ? server.keys.url : '';
    this.credential.reset(server.keys.credential === 'none' ? 'token'
      : server.keys.credential?.startsWith('header') ? 'header' : 'token');
    this.changeProblem = '';
    this.tried = false;
    this.dialogRef = this.dialog.open(this.changeDialog, { width: '480px', maxWidth: '100vw' });
    this.dialogRef.afterClosed().subscribe(() => (this.dialogRef = null));
  }

  closeDialog(): void {
    if (!this.saving) this.dialogRef?.close();
  }

  get changeTitle(): string {
    const name = this.server?.keys.name || '';
    return this.changing === 'name' ? `Rename ${name}`
      : this.changing === 'url' ? `Change the address of ${name}` : `Replace the credential of ${name}`;
  }

  /** What is missing from the name or the address; '' when nothing is. */
  get valueMissing(): string {
    if (this.changing === 'credential' || this.changeValue.trim()) return '';
    return this.changing === 'name' ? 'Give the server a name.' : 'Enter the server\'s address.';
  }

  async saveChange(): Promise<void> {
    const server = this.server;
    if (!server || this.saving) return;
    this.tried = true;
    this.changeProblem = '';
    if (this.valueMissing || (this.changing === 'credential' && !this.credential.ready)) return;

    // Taking the credential away is a change of what the server is
    // reached with, and is asked.
    if (this.changing === 'credential' && this.credential.kind === 'none' && server.keys.credential !== 'none') {
      const sure = await this.confirm.ask({
        title: `Remove the credential of ${server.keys.name}?`,
        body: 'The server is read again without one. If it refuses, nothing is changed.',
        confirm: 'Remove the credential',
        danger: true,
      });
      if (!sure) return;
    }

    this.saving = true;
    if (this.dialogRef) this.dialogRef.disableClose = true;
    const result = await this.service.update(server.resource_ref,
      this.changing === 'name' ? { name: this.changeValue.trim() }
        : this.changing === 'url' ? { url: this.changeValue.trim() }
          : { credential: this.credential.given() });
    this.saving = false;
    if (this.dialogRef) this.dialogRef.disableClose = false;
    if (result.error || !result.resource) {
      this.changeProblem = result.error || 'That could not be saved.';
      return;
    }
    this.server = result.resource;
    this.error = '';
    this.notice = this.changing === 'name' ? 'Renamed.'
      : this.changing === 'url' ? 'Address changed, and the server read again there.'
        : 'Credential replaced, and the server read again with it.';
    this.closeDialog();
  }

  // ── Removing ────────────────────────────────────────────────────────

  async askRemove(): Promise<void> {
    const server = this.server;
    if (!server) return;
    const sure = await this.confirm.ask({
      title: `Remove ${server.keys.name}?`,
      body: 'Your chats stop being able to call its tools at once, and its credential is deleted.',
      confirm: 'Remove',
      danger: true,
    });
    if (!sure) return;
    const result = await this.service.remove(server.resource_ref);
    if (result.error) {
      this.error = result.error;
      return;
    }
    await this.router.navigate(['/data/mcp'], { state: { mcpNotice: `${server.keys.name} removed.` } });
  }
}
