import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { McpServer, McpService } from 'src/app/services/mcp.service';

/**
 * Which installed agents this chat may use. Narrowing only: unchecking
 * an agent removes it from this chat, it never grants anything
 * the platform hasn't approved. With every agent checked the chat keeps
 * no list at all, so agents installed later join automatically; any
 * narrowing is an explicit list — an empty one means a pure
 * conversation chat.
 *
 * The person's MCP servers are listed under the agents, the same way:
 * every server that is on is in a chat until the chat leaves it out.
 */
@Component({
  selector: 'app-chat-agents-dialog',
  standalone: false,
  templateUrl: './chat-agents-dialog.component.html',
})
export class ChatAgentsDialogComponent implements OnInit {
  agents: any[] = [];
  selection = new Set<string>();
  /** The person's MCP servers that are on, and which this chat may call. */
  servers: McpServer[] = [];
  serverSelection = new Set<string>();
  isLoading = true;

  private readonly configured: string[] | null;
  private readonly defaults: string[] | null;

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: any,
    private dialogRef: MatDialogRef<ChatAgentsDialogComponent>,
    private aiSession: AiSessionService,
    private mcp: McpService,
  ) {
    const enabled = data?.enabled_agents;
    this.configured = Array.isArray(enabled) ? enabled : null;
    this.defaults = Array.isArray(data?.default_agents) ? data.default_agents : null;
  }

  async ngOnInit(): Promise<void> {
    this.agents = await this.aiSession.listAgents();
    if (this.defaults) this.agents = this.agents.filter((agent) =>
      this.defaults!.includes(agent.agent_id));
    // No stored narrowing means everything is enabled.
    const enabled =
      this.configured ?? this.defaults ?? this.agents.map((a) => a.agent_id);
    this.selection = new Set(enabled);

    // Only servers that are on can be in a chat at all; when the
    // deployment has the feature off there is nothing to choose.
    const page = await this.mcp.list().catch(() => ({ servers: [] as McpServer[], blocked: true }));
    this.servers = page.blocked ? [] : page.servers.filter((server) => server.keys.enabled);
    const chosen = this.data?.enabled_mcp;
    this.serverSelection = new Set(Array.isArray(chosen)
      ? chosen : this.servers.map((server) => server.resource_ref));
    this.isLoading = false;
  }

  toggleServer(ref: string): void {
    if (!this.serverSelection.delete(ref)) this.serverSelection.add(ref);
  }

  toggle(agentId: string): void {
    if (this.selection.has(agentId)) this.selection.delete(agentId);
    else this.selection.add(agentId);
  }

  get allSelected(): boolean {
    return this.agents.every((a) => this.selection.has(a.agent_id));
  }

  save(): void {
    // Everything checked is not a list of everything: a chat that keeps
    // no list is the one that joins agents installed later, which is
    // what a person means by leaving them all on.
    this.dialogRef.close({
      enabled_agents: this.allSelected ? null : this.agents
            .map((a) => a.agent_id)
            .filter((id) => this.selection.has(id)),
      // The same for servers: all of them on is no list.
      enabled_mcp: this.servers.every((server) => this.serverSelection.has(server.resource_ref))
        ? null
        : this.servers.map((server) => server.resource_ref).filter((ref) => this.serverSelection.has(ref)),
    });
  }

  reset(): void {
    this.selection = new Set(this.defaults || this.agents.map((agent) => agent.agent_id));
    this.serverSelection = new Set(this.servers.map((server) => server.resource_ref));
  }

  close(): void {
    this.dialogRef.close(null);
  }
}
