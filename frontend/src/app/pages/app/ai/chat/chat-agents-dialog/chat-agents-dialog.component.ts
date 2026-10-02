import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { AiSessionService } from 'src/app/services/ai-session.service';

/**
 * Which installed agents this chat may use. Narrowing only: unchecking
 * an agent removes it from this chat, it never grants anything
 * the platform hasn't approved. With every agent checked the chat keeps
 * no list at all, so agents installed later join automatically; any
 * narrowing is an explicit list — an empty one means a pure
 * conversation chat.
 */
@Component({
  selector: 'app-chat-agents-dialog',
  standalone: false,
  templateUrl: './chat-agents-dialog.component.html',
  styleUrls: ['./chat-agents-dialog.component.css'],
})
export class ChatAgentsDialogComponent implements OnInit {
  agents: any[] = [];
  selection = new Set<string>();
  isLoading = true;

  private readonly configured: string[] | null;
  private readonly defaults: string[] | null;

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: any,
    private dialogRef: MatDialogRef<ChatAgentsDialogComponent>,
    private aiSession: AiSessionService,
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
    this.isLoading = false;
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
    });
  }

  reset(): void {
    this.selection = new Set(this.defaults || this.agents.map((agent) => agent.agent_id));
  }

  close(): void {
    this.dialogRef.close(null);
  }
}
