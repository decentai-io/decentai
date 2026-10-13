import {
  Component, EventEmitter, Input, OnChanges, Output, SimpleChanges,
} from '@angular/core';

import {
  AgentFunction, AgentGrant, AgentOffer, AgentsService,
} from 'src/app/services/agents.service';
import { Choice } from 'src/app/components/ui/radio-keys';
import { ConfirmService } from 'src/app/services/confirm.service';
import { Group, GroupService } from 'src/app/services/group.service';
import { Member, MembersService } from 'src/app/services/members.service';
import { localFunction } from './agent-shared';

/** Who one grant is for. */
type Audience = 'everyone' | 'groups' | 'people';

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
 *
 * A grant is made for one kind of audience at a time: everyone, some
 * groups, or some people. Groups and people together are two grants,
 * which comes to the same, since grants only add.
 */
@Component({
  selector: 'app-agent-access',
  standalone: false,
  templateUrl: './agent-access.component.html',
  styleUrl: './agent-access.component.css',
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

  /** The grant being composed. Nobody is chosen to begin with: who
   *  gets access is the one thing that must be said. The whole agent by
   *  default: the common case is "this team may use this", and a list
   *  is the narrower decision. */
  draftAudience: Audience | null = null;
  draftGroups = new Set<string>();
  draftUsers = new Set<string>();
  draftWhole = true;
  draftFunctions = new Set<string>();
  /** Per scope, the values allowed; none is any value. */
  draftScopes: Record<string, string[]> = {};
  /** Give access was pressed: what is missing is said under its field. */
  tried = false;

  readonly audiences: Choice<Audience>[] = [
    { value: 'everyone', name: 'Everyone', hint: 'Every person in this organization, now and later.' },
    { value: 'groups', name: 'Groups', hint: 'The members of the groups you tick.' },
    { value: 'people', name: 'People', hint: 'Only the people you tick.' },
  ];
  readonly reaches: Choice<boolean>[] = [
    { value: true, name: 'All of them' },
    { value: false, name: 'Only some' },
  ];

  readonly EVERYONE = 'everyone';

  localFunction = localFunction;

  constructor(
    private service: AgentsService,
    private groupService: GroupService,
    private memberService: MembersService,
    private confirm: ConfirmService,
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
    this.draftAudience = null;
    this.draftGroups = new Set<string>();
    this.draftUsers = new Set<string>();
    this.draftWhole = true;
    this.draftFunctions = new Set<string>();
    this.draftScopes = Object.fromEntries(
      (this.agent?.scopes || []).map((scope) => [scope, [] as string[]]));
    this.tried = false;
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

  get hasScopes(): boolean {
    return !!this.agent?.scopes?.length;
  }

  /** Who the grant being composed reaches. */
  private get draftOwner(): { groups: string[]; users: string[] } {
    switch (this.draftAudience) {
      case 'everyone': return { groups: [this.EVERYONE], users: [] };
      case 'groups': return { groups: [...this.draftGroups], users: [] };
      case 'people': return { groups: [], users: [...this.draftUsers] };
      default: return { groups: [], users: [] };
    }
  }

  /** What still has to be said before the grant can be made, by the
   *  field it belongs under. */
  private get missing(): { field: 'who' | 'functions'; message: string }[] {
    const missing: { field: 'who' | 'functions'; message: string }[] = [];
    const owner = this.draftOwner;
    if (!this.draftAudience) {
      missing.push({ field: 'who', message: 'Choose who gets access.' });
    } else if (!owner.groups.length && !owner.users.length) {
      missing.push({
        field: 'who',
        message: this.draftAudience === 'groups' ? 'Tick at least one group.' : 'Tick at least one person.',
      });
    }
    if (!this.draftWhole && !this.draftFunctions.size) {
      missing.push({ field: 'functions', message: 'Tick at least one function, or give all of them.' });
    }
    return missing;
  }

  errorFor(field: 'who' | 'functions'): string {
    return this.missing.find((entry) => entry.field === field)?.message || '';
  }

  /** The grant as a sentence, before it is written. Three controls that
   *  each narrow something are hard to hold in the head at once; the
   *  result of combining them is not. */
  get draftSummary(): string {
    const owner = this.draftOwner;
    const count = owner.groups.length + owner.users.length;
    if (!this.draftAudience || !count) return 'Nobody is chosen yet.';
    const who = this.draftAudience === 'everyone' ? 'Everyone in this organization'
      : this.draftAudience === 'groups' ? `${count} ${count === 1 ? 'group' : 'groups'}`
        : `${count} ${count === 1 ? 'person' : 'people'}`;
    const functions = this.draftFunctions.size;
    const what = this.draftWhole ? 'every function, now and later'
      : `${functions} ${functions === 1 ? 'function' : 'functions'}`;
    const limited = Object.values(this.draftScopes).filter((values) => values.length).length;
    return `${who} may use ${what}`
      + (limited ? `, limited on ${limited} ${limited === 1 ? 'scope' : 'scopes'}.` : '.');
  }

  async save(): Promise<void> {
    this.tried = true;
    if (this.missing.length || this.busyId) return;

    const constraints: Record<string, string[]> = {};
    for (const [scope, values] of Object.entries(this.draftScopes)) {
      if (values.length) constraints[scope] = values;
    }

    this.busyId = this.agent.agent_id;
    try {
      const result = await this.service.grant(
        this.agent.agent_id,
        this.draftOwner,
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

  /** Taking access away stops people mid-work, so it is asked. */
  async askRevoke(grant: AgentGrant): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Revoke the access of ${this.audience(grant)}?`,
      body: `They can no longer ask ${this.agent.name || 'this agent'} for `
        + `${this.scope(grant)}, unless another grant still covers them.`,
      confirm: 'Revoke',
      danger: true,
    });
    if (sure) await this.revoke(grant);
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
