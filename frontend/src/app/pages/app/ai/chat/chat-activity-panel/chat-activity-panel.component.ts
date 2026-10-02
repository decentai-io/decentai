import { Component, Input } from '@angular/core';

import { ActivityEntry } from 'src/app/services/chat-state.service';
import { PlanStep } from 'src/app/models/chat-protocol';
import { durationLabel } from '../chat-activity-dialog/audit-words';

/**
 * What the assistant is doing in this turn: the plan as it stands, and
 * the steps as they land, each task with its own steps under it.
 *
 * It is held in the browser and lasts the turn. The durable record —
 * every function since the first message, written by the platform and
 * never by the assistant — is the audit trail, read in the Activity &
 * audit dialog.
 */
@Component({
  selector: 'app-chat-activity-panel',
  standalone: false,
  templateUrl: './chat-activity-panel.component.html',
  styleUrl: './chat-activity-panel.component.css',
})
export class ChatActivityPanelComponent {
  @Input() activity: ActivityEntry[] = [];
  @Input() livePlan: PlanStep[] = [];
  @Input() isProcessing = false;
  @Input() agentLabels: Record<string, string> = {};

  /** Tasks the person has opened or closed by hand. Everything else
   *  follows the work: the one running is open, the rest are shut. */
  private taskChoice = new Map<string, boolean>();

  /** The plan with each task's own steps under it, so a finished
   *  task's steps stay with it — the thing a person scrolls back to
   *  see — instead of every step of the turn running flat beneath the
   *  list. */
  get taskGroups(): { item: PlanStep; steps: ActivityEntry[] }[] {
    return this.livePlan.map((item) => ({
      item,
      steps: this.activity.filter((entry) => entry.item && entry.item === item.id),
    }));
  }

  /** Steps belonging to no task: a turn with no plan at all, or the
   *  opening moves before the first item went active. */
  get looseSteps(): ActivityEntry[] {
    const ids = new Set(this.livePlan.map((i) => i.id).filter(Boolean));
    return this.activity.filter((entry) => !entry.item || !ids.has(entry.item));
  }

  isTaskOpen(item: PlanStep): boolean {
    const id = String(item.id || '');
    const chosen = this.taskChoice.get(id);
    return chosen !== undefined ? chosen : item.status === 'active';
  }

  toggleTask(item: PlanStep): void {
    const id = String(item.id || '');
    this.taskChoice.set(id, !this.isTaskOpen(item));
  }

  get hasLive(): boolean {
    return this.activity.length > 0 || this.livePlan.length > 0;
  }

  isFailed(entry: ActivityEntry): boolean {
    return entry.status === 'error' || entry.status === 'failed';
  }

  planLabel(item: PlanStep): string {
    switch (item.status) {
      case 'done': return item.verified ? 'done' : 'done · unverified';
      case 'active': return 'in progress';
      case 'blocked': return 'blocked';
      default: return 'to do';
    }
  }

  /** A minted agent ref read back as the agent a person knows. */
  humanize(text: string): string {
    if (!text) return '';
    return text.replace(/agt_[0-9a-f]{6,}(?:__[a-z0-9_]+)?(?:\.([a-z0-9_]+)\.([a-z0-9_]+))?/g,
      (whole, tool, fn) => {
        const ref = whole.split('.')[0].split('__')[0];
        const name = this.agentLabels[ref];
        if (!name) return whole;
        return tool && fn ? `${name} · ${tool}.${fn}` : name;
      });
  }

  stepDuration(ms?: number): string {
    return ms == null ? '' : durationLabel(ms);
  }
}
