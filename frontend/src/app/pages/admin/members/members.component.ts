import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { Group, GroupService } from 'src/app/services/group.service';
import {
  Invitation,
  Member,
  MembersService,
} from 'src/app/services/members.service';
import { DataPageBase } from '../../app/data-page-base';

/**
 * Admin → Users: the deployment's people, and who has been invited.
 *
 * Users are never created here — everyone arrives by invitation. A user's
 * access is exactly their groups, so this page is where group membership is
 * managed; what those groups grant is composed on the Groups/Roles/Policies
 * pages.
 *
 * Guard rails the backend enforces and this page surfaces plainly:
 * - you cannot disable or delete your own account;
 * - no change may leave nobody able to manage access (409);
 * - you cannot place anyone into groups granting more than you hold.
 */
@Component({
  selector: 'app-members',
  standalone: false,
  templateUrl: './members.component.html',
  styleUrls: ['../../app/data-shared.css', '../iam-shared.css'],
})
export class MembersComponent extends DataPageBase implements OnInit {
  loading = true;

  members: Member[] = [];
  invitations: Invitation[] = [];
  groups: Group[] = [];

  /** The user whose groups are being edited, or null. */
  editingId: string | null = null;
  selected = new Set<string>();

  /** Invite form. */
  inviting = false;
  inviteEmail = '';
  inviteGroupIds = new Set<string>();
  /** Handed back when no email service is configured — show it once. */
  inviteLink = '';

  saving = false;
  busy = new Set<string>();

  constructor(
    private members_: MembersService,
    private groupService: GroupService,
    public auth: AuthService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    await this.reload();
    this.loading = false;
  }

  private async reload(): Promise<void> {
    const [members, invitations, groups] = await Promise.all([
      this.members_.list(),
      this.canReadInvitations
        ? this.members_.invitations()
        : Promise.resolve([]),
      this.groupService.list().catch(() => []),
    ]);
    this.members = members;
    this.invitations = invitations;
    this.groups = groups;
  }

  // ── What the current user may do ────────────────────────────────────

  get canInvite(): boolean {
    return this.auth.can('iam:invitation:create');
  }

  get canAssignGroups(): boolean {
    return this.auth.can('iam:user:set_groups');
  }

  get canDisable(): boolean {
    return this.auth.can('iam:user:set_status');
  }

  get canDelete(): boolean {
    return this.auth.can('iam:user:delete');
  }

  get canReadInvitations(): boolean {
    return this.auth.can('iam:invitation:list');
  }

  get canRevokeInvitations(): boolean {
    return this.auth.can('iam:invitation:revoke');
  }

  isSelf(member: Member): boolean {
    return member.user_id === this.auth.user?.user_id;
  }

  isBusy(id: string): boolean {
    return this.busy.has(id);
  }

  // ── Reading a user ──────────────────────────────────────────────────

  groupName(groupId: string): string {
    return this.groups.find((g) => g.group_id === groupId)?.group_name ?? groupId;
  }

  groupNames(member: Member): string {
    if (!member.assigned_groups.length) {
      return 'No groups';
    }
    return member.assigned_groups.map((id) => this.groupName(id)).join(', ');
  }

  // ── Group assignment ────────────────────────────────────────────────

  startEditGroups(member: Member): void {
    this.editingId = member.user_id;
    this.selected = new Set(member.assigned_groups);
    this.error = '';
  }

  cancelEditGroups(): void {
    this.editingId = null;
    this.error = '';
  }

  isGroupOn(groupId: string): boolean {
    return this.selected.has(groupId);
  }

  toggleGroup(groupId: string): void {
    this.selected.has(groupId)
      ? this.selected.delete(groupId)
      : this.selected.add(groupId);
  }

  async saveGroups(): Promise<void> {
    if (this.editingId === null) {
      return;
    }

    this.saving = true;
    try {
      const result = await this.members_.setGroups(this.editingId, [
        ...this.selected,
      ]);
      if (result.error) {
        if (result.ungrantable?.length) {
          const shown = result.ungrantable.slice(0, 5).join(', ');
          return this.fail(
            `You cannot assign groups granting permissions you do not hold: ${shown}.`,
          );
        }
        return this.fail(result.error);
      }

      this.editingId = null;
      await this.reload();
      this.flash('Groups updated.');
      // Their own permissions may have just changed.
      await this.auth.refresh();
    } finally {
      this.saving = false;
    }
  }

  // ── Status & removal ────────────────────────────────────────────────

  async setStatus(member: Member, status: 'active' | 'disabled'): Promise<void> {
    if (
      status === 'disabled' &&
      !confirm(
        `Disable ${member.email}?\n\nTheir sessions end immediately and they ` +
          `cannot sign in until re-enabled.`,
      )
    ) {
      return;
    }

    this.busy.add(member.user_id);
    try {
      const result = await this.members_.setStatus(member.user_id, status);
      if (result.error) {
        return this.fail(result.error);
      }
      await this.reload();
      this.flash(status === 'disabled' ? 'Account disabled.' : 'Account re-enabled.');
    } finally {
      this.busy.delete(member.user_id);
    }
  }

  /** Removal is a hand-over: the dialog shows what the person owns,
   *  what will be deleted and what will pass to a successor. */
  removing: Member | null = null;
  leaving: any = null;
  successorId = '';

  async requestRemove(member: Member): Promise<void> {
    this.removing = member;
    this.leaving = null;
    this.successorId = '';
    this.error = '';
    const preview = await this.members_.leaving(member.user_id);
    if (preview.error) return this.fail(preview.error);
    this.leaving = preview;
  }

  closeRemove(): void {
    if (!this.busy.size) {
      this.removing = null;
      this.leaving = null;
    }
  }

  get successors(): Member[] {
    return this.members.filter((m) => m.status === 'active' && m.user_id !== this.removing?.user_id);
  }

  /** The kinds a removal touches, in words a person reads. */
  private static readonly KIND_LABELS: Record<string, string> = {
    mcp_servers: 'MCP servers',
    api_keys: 'API keys',
    agent_grants: 'agent grants naming them',
    connections: 'model connections',
    sources: 'agent sources',
  };

  ownsLine(kind: string): string {
    const n = Number(this.leaving?.owns?.[kind] || 0);
    return `${n} ${MembersComponent.KIND_LABELS[kind] || kind.replace(/_/g, ' ')}`;
  }

  get handedOver(): string[] {
    return (this.leaving?.transferred || []).filter((k: string) => Number(this.leaving?.owns?.[k] || 0) > 0);
  }

  get deletedKinds(): string[] {
    return (this.leaving?.deleted || []).filter((k: string) => Number(this.leaving?.owns?.[k] || 0) > 0);
  }

  async remove(member: Member): Promise<void> {
    this.busy.add(member.user_id);
    try {
      const result = await this.members_.remove(member.user_id, this.successorId);
      if (result.error) {
        return this.fail(result.error);
      }
      this.removing = null;
      this.leaving = null;
      await this.reload();
      const to = result.successor?.email ? ` What they owned is now ${result.successor.email}'s.` : '';
      this.flash(`${member.email} removed.${to}`);
    } finally {
      this.busy.delete(member.user_id);
    }
  }

  // ── Invitations ─────────────────────────────────────────────────────

  startInvite(): void {
    this.inviting = true;
    this.inviteEmail = '';
    this.inviteGroupIds = new Set<string>();
    this.inviteLink = '';
    this.error = '';
  }

  cancelInvite(): void {
    this.inviting = false;
    this.inviteLink = '';
  }

  toggleInviteGroup(groupId: string): void {
    this.inviteGroupIds.has(groupId)
      ? this.inviteGroupIds.delete(groupId)
      : this.inviteGroupIds.add(groupId);
  }

  async sendInvite(): Promise<void> {
    const email = this.inviteEmail.trim();
    if (!email) {
      return this.fail('An email address is required.');
    }

    this.saving = true;
    try {
      const result = await this.members_.invite(email, [...this.inviteGroupIds]);
      if (result.error) {
        if (result.ungrantable?.length) {
          const shown = result.ungrantable.slice(0, 5).join(', ');
          return this.fail(
            `You cannot invite into groups granting permissions you do not hold: ${shown}.`,
          );
        }
        return this.fail(result.error);
      }

      await this.reload();

      if (result.email_sent) {
        this.inviting = false;
        this.flash(`Invitation emailed to ${email}.`);
      } else {
        // No email service configured: the link exists only here, right now.
        this.inviteLink = result.accept_url ?? '';
        this.flash(`Invitation created for ${email} — copy the link below.`);
      }
    } finally {
      this.saving = false;
    }
  }

  /** Re-inviting the same address refreshes its link — that IS "resend". */
  async resend(invitation: Invitation): Promise<void> {
    this.busy.add(invitation.invitation_id);
    try {
      const result = await this.members_.invite(
        invitation.email,
        invitation.assigned_groups,
      );
      if (result.error) {
        return this.fail(result.error);
      }
      await this.reload();
      if (result.email_sent) {
        this.flash(`Invitation re-sent to ${invitation.email}.`);
      } else {
        this.inviting = true;
        this.inviteEmail = invitation.email;
        this.inviteLink = result.accept_url ?? '';
        this.flash('New link created — the old one no longer works.');
      }
    } finally {
      this.busy.delete(invitation.invitation_id);
    }
  }

  async revoke(invitation: Invitation): Promise<void> {
    this.busy.add(invitation.invitation_id);
    try {
      const result = await this.members_.revoke(invitation.invitation_id);
      if (result.error) {
        return this.fail(result.error);
      }
      await this.reload();
      this.flash(`Invitation for ${invitation.email} revoked.`);
    } finally {
      this.busy.delete(invitation.invitation_id);
    }
  }

  async copyLink(): Promise<void> {
    if (!this.inviteLink) return;
    try {
      await navigator.clipboard.writeText(this.inviteLink);
      this.flash('Link copied.');
    } catch {
      this.fail('Could not copy — select the link and copy it yourself.');
    }
  }
}
