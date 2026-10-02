import {
  Component, EventEmitter, Input, OnChanges, Output, SimpleChanges,
} from '@angular/core';

import {
  AgentFunction, AgentGrant, AgentOffer, AgentsService,
} from 'src/app/services/agents.service';
import { Group, GroupService } from 'src/app/services/group.service';
import { Member, MembersService } from 'src/app/services/members.service';
import { localFunction } from './agent-shared';

/**
 * Who may ask this agent to do things.
 *
 * Access lives on the agent rather than in an IAM policy, because an
 * agent's functions are DISCOVERED from its manifest: they appear when
 * it is installed, change shape when it updates, and vanish when it is
 * removed. A policy naming them would rot on all three.
 *
 * Grants are allow-only — absence is denial — and a grant can be
 * narrowed twice over: to particular functions, and to particular values
 * of the scopes the manifest declares.
 */
@Component({
  selector: 'app-agent-access',
  standalone: false,
  templateUrl: './agent-access.component.html',
  styleUrls: ['../data-shared.css', './agent-shared.css', './agents.component.css'],
})
export class AgentAccessComponent implements OnChanges {
  @Input() agent!: AgentOffer;

  @Output() failed = new EventEmitter<string>();
  @Output() flashed = new EventEmitter<string>();

  loading = false;
  busyId = '';

  grants: AgentGrant[] = [];
  groups: Group[] = [];
  members: Member[] = [];

  /** The grant being composed. Whole agent by default: the common case
   *  is "this team may use this", and a list is the narrower decision. */
  draftGroups = new Set<string>();
  draftUsers = new Set<string>();
  draftWhole = true;
  draftFunctions = new Set<string>();
  draftScopes: Record<string, string> = {};

  readonly EVERYONE = 'everyone';

  localFunction = localFunction;

  constructor(
    private service: AgentsService,
    private groupService: GroupService,
    private memberService: MembersService,
  ) {}

  /** Why the grants could not be read, when they could not: an empty
   *  list would otherwise read as "nobody has access". */
  unreadable = '';

  async ngOnChanges(changes?: SimpleChanges): Promise<void> {
    // The same agent read again is not another agent: a grant half
    // composed stays as it is.
    const change = changes?.['agent'];
    const before = change?.previousValue?.agent_id;
    if (before && before === change?.currentValue?.agent_id) return;

    this.resetDraft();
    this.loading = true;
    try {
      const [grants, groups, members] = await Promise.all([
        this.service.grantsOrError(this.agent.agent_id),
        this.groupService.list().catch(() => []),
        this.memberService.list().catch(() => []),
      ]);
      this.unreadable = grants.error || '';
      this.grants = grants.grants;
      this.groups = groups;
      this.members = members;
    } finally {
      this.loading = false;
    }
  }

  private resetDraft(): void {
    this.draftGroups = new Set<string>();
    this.draftUsers = new Set<string>();
    this.draftWhole = true;
    this.draftFunctions = new Set<string>();
    this.draftScopes = {};
  }

  // ── What a grant row says ───────────────────────────────────────────

  audience(grant: AgentGrant): string {
    const names: string[] = [];
    for (const id of grant.owner.groups || []) {
      names.push(id === this.EVERYONE ? 'Everyone'
        : this.groups.find((g) => g.group_id === id)?.group_name || id);
    }
    for (const id of grant.owner.users || []) {
      names.push(this.members.find((m) => m.user_id === id)?.email || id);
    }
    return names.join(', ') || 'nobody';
  }

  scope(grant: AgentGrant): string {
    if (grant.functions === '*') return 'every function, now and later';
    const list = grant.functions as string[];
    return list.length === 1 ? list[0] : `${list.length} functions`;
  }

  limits(grant: AgentGrant): string {
    return Object.entries(grant.constraints || {})
      .map(([scope, values]) => `${scope}: ${values.join(', ')}`)
      .join(' · ');
  }

  // ── Composing one ───────────────────────────────────────────────────

  toggleGroup(groupId: string): void {
    if (!this.draftGroups.delete(groupId)) this.draftGroups.add(groupId);
  }

  toggleUser(userId: string): void {
    if (!this.draftUsers.delete(userId)) this.draftUsers.add(userId);
  }

  toggleFunction(fn: AgentFunction): void {
    const local = localFunction(fn);
    if (!this.draftFunctions.delete(local)) this.draftFunctions.add(local);
  }

  isFunctionOn(fn: AgentFunction): boolean {
    return this.draftWhole || this.draftFunctions.has(localFunction(fn));
  }

  get reaches(): boolean {
    return this.draftGroups.size > 0 || this.draftUsers.size > 0;
  }

  get hasScopes(): boolean {
    return !!this.agent?.scopes?.length;
  }

  /** The grant as a sentence, before it is written. Three controls that
   *  each narrow something are hard to hold in the head at once; the
   *  result of combining them is not. */
  get draftSummary(): string {
    const people = this.draftGroups.size + this.draftUsers.size;
    const who = this.draftGroups.has(this.EVERYONE)
      ? 'Everyone'
      : `${people} group${people === 1 ? '' : 's'}/person`;
    const what = this.draftWhole
      ? 'every function'
      : `${this.draftFunctions.size} function${this.draftFunctions.size === 1 ? '' : 's'}`;
    const limited = Object.values(this.draftScopes)
      .filter((value) => String(value || '').trim()).length;
    return `${who}, for ${what}`
      + (limited ? `, limited on ${limited} scope${limited === 1 ? '' : 's'}` : '');
  }

  async save(): Promise<void> {
    if (!this.reaches) return;

    const constraints: Record<string, string[]> = {};
    for (const [scope, raw] of Object.entries(this.draftScopes)) {
      const values = String(raw || '')
        .split(',').map((value) => value.trim()).filter(Boolean);
      if (values.length) constraints[scope] = values;
    }

    this.busyId = this.agent.agent_id;
    try {
      const result = await this.service.grant(
        this.agent.agent_id,
        { groups: [...this.draftGroups], users: [...this.draftUsers] },
        this.draftWhole ? '*' : [...this.draftFunctions],
        constraints,
      );
      if (result.error) return this.failed.emit(result.error);
      this.grants = await this.service.grants(this.agent.agent_id);
      this.resetDraft();
      this.flashed.emit('Access granted.');
    } finally {
      this.busyId = '';
    }
  }

  async revoke(grant: AgentGrant): Promise<void> {
    this.busyId = grant.grant_id;
    try {
      const result = await this.service.revoke(this.agent.agent_id, grant.grant_id);
      if (result.error) return this.failed.emit(result.error);
      this.grants = await this.service.grants(this.agent.agent_id);
      this.flashed.emit('Access removed.');
    } finally {
      this.busyId = '';
    }
  }
}
