import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { McpCredential, McpServer, McpService, McpTool } from 'src/app/services/mcp.service';
import { DataPageBase } from '../../data-page-base';

type CredentialKind = 'none' | 'token' | 'header';

interface Level { value: 0 | 1 | 2 | 3; label: string; help: string; }

/**
 * MCP servers: remote tool servers a person adds for their own chats.
 * The platform reads a server's tools when it is added; this page is
 * where the person looks them over — which are on, and what each costs
 * to call — before any chat may use one.
 */
@Component({
  selector: 'app-mcp',
  standalone: false,
  templateUrl: './mcp.component.html',
  styleUrls: ['../../data-shared.css', '../../../admin/iam-shared.css', './mcp.component.css'],
})
export class McpComponent extends DataPageBase implements OnInit {
  loading = true;
  servers: McpServer[] = [];
  /** MCP is switched off for the deployment: what is listed stays
   *  listed, and no chat can call any of it. */
  blocked = false;

  /** The scale every function is priced on; a chat asks first for
   *  anything above its own level. */
  readonly levels: Level[] = [
    { value: 0, label: 'Reads', help: 'Changes nothing.' },
    { value: 1, label: 'Changes', help: 'An ordinary change.' },
    { value: 2, label: 'Changes, sandboxed', help: 'A change with wider reach.' },
    { value: 3, label: 'Acts outside', help: 'Sends, posts, buys: leaves the platform.' },
  ];

  // Adding one
  adding = false;
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

  deleteTarget: McpServer | null = null;

  constructor(private service: McpService, public auth: AuthService) {
    super();
  }

  async ngOnInit(): Promise<void> {
    const page = await this.service.list();
    this.servers = page.servers;
    this.blocked = page.blocked;
    this.loading = false;
  }

  get canAdd(): boolean {
    return !this.blocked && this.auth.can('mcp:server:create');
  }

  // ── Adding ────────────────────────────────────────────────────────

  startAdd(): void {
    this.adding = true;
    this.formName = this.formUrl = this.formToken = this.formHeader = this.formValue = '';
    this.credentialKind = 'none';
    this.error = '';
  }

  cancelAdd(): void {
    if (!this.saving) this.adding = false;
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
    if (!this.addReady || this.saving) return;
    this.saving = true;
    const result = await this.service.create({
      name: this.formName.trim(), url: this.formUrl.trim(), credential: this.credential(),
    });
    this.saving = false;
    if (result.error || !result.resource) {
      this.fail(result.error || 'The server could not be added.');
      return;
    }
    this.adding = false;
    this.servers = [...this.servers, result.resource];
    this.open = result.resource;
    this.replacing = false;
    this.flash(`${result.resource.keys.name} added. Look its tools over before a chat uses them.`);
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

  requestDelete(server: McpServer): void {
    this.deleteTarget = server;
    this.error = '';
  }

  closeDelete(): void {
    if (!this.busy) this.deleteTarget = null;
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
    if (this.open?.resource_ref === server.resource_ref) this.open = null;
    this.deleteTarget = null;
    this.flash(`${server.keys.name} removed.`);
  }
}
