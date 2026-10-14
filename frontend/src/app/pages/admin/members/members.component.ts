import { Component, OnInit, TemplateRef, ViewChild } from '@angular/core';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';

import { dayDate } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';

import { AuthService } from 'src/app/services/auth.service';
import { Group, GroupService } from 'src/app/services/group.service';
import {
  Invitation,
  Member,
  MembersService,
} from 'src/app/services/members.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { DataPageBase } from '../../app/data-page-base';

/**
 * Admin → Users: the deployment's people, and who has been invited.
 *
 * Where the deployment sends email, everyone arrives by invitation.
 * Where no mail server is set it sends none: the administrator adds a
 * person here and hands them a temporary password, and resets a
 * forgotten one the same way. A user's access is exactly their groups, so this page is
 * where group membership is managed; what those groups grant is composed
 * on the Groups/Roles/Policies pages.
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
  styleUrl: '../admin-shared.css',
})
export class MembersComponent extends DataPageBase implements OnInit {
  loading = true;

  members: Member[] = [];
  invitations: Invitation[] = [];
  groups: Group[] = [];

  /** The user whose groups are being edited, or null. */
  editingId: string | null = null;
  selected = new Set<string>();

  /** Invite form — where no email is sent, the form a person is added with. */
  inviting = false;
  inviteName = '';
  /** A temporary password just made, shown once: whose, and what. */
  handed: { email: string; password: string; reset: boolean } | null = null;
  inviteEmail = '';
  inviteGroupIds = new Set<string>();
  /** Handed back when no email service is configured — show it once. */
  inviteLink = '';

  saving = false;
  busy = new Set<string>();

  query = '';
  /** A dialog's button was pressed: what a field still needs is said under it. */
  tried = false;
  /** What is shown once was copied. */
  copied = false;

  /** The page's dialogs, one open at a time. */
  @ViewChild('inviteDialog') private inviteDialog!: TemplateRef<unknown>;
  @ViewChild('handedDialog') private handedDialog!: TemplateRef<unknown>;
  @ViewChild('groupsDialog') private groupsDialog!: TemplateRef<unknown>;
  @ViewChild('leavingDialog') private leavingDialog!: TemplateRef<unknown>;
  private dialogRef: MatDialogRef<unknown> | null = null;

  constructor(
    private members_: MembersService,
    private groupService: GroupService,
    public auth: AuthService,
    private dialog: MatDialog,
    private confirm: ConfirmService,
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
    return this.adds
      ? this.auth.can('iam:user:create')
      : this.auth.can('iam:invitation:create');
  }

  /** People are added here, not invited: this install sends no
   *  email, and an invitation would reach nobody. */
  get adds(): boolean {
    return !this.auth.sendsEmail;
  }

  get canResetPassword(): boolean {
    return this.auth.can('iam:user:reset_password');
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

  // ── The page's dialogs ──────────────────────────────────────────────

  /** Opens one of the page's dialogs in place of whichever is open.
   *  Closing it, however it is closed, ends what it was for. */
  private show(template: TemplateRef<unknown>, width = '520px'): void {
    const before = this.dialogRef;
    this.dialogRef = null;
    before?.close();
    this.tried = false;
    this.copied = false;
    const ref = this.dialog.open(template, { width, maxWidth: '100vw' });
    this.dialogRef = ref;
    ref.afterClosed().subscribe(() => {
      // Replaced by another dialog: that one's state is not this one's to clear.
      if (this.dialogRef !== ref) return;
      this.dialogRef = null;
      this.editingId = null;
      this.inviting = false;
      this.inviteLink = '';
      this.handed = null;
      this.removing = null;
      this.leaving = null;
      this.error = '';
    });
  }

  closeDialog(): void {
    if (!this.saving && !this.busy.size) this.dialogRef?.close();
  }

  /** A dialog is open: what goes wrong is said in it, not behind it. */
  get dialogOpen(): boolean {
    return this.dialogRef !== null;
  }

  // ── Reading a user ──────────────────────────────────────────────────

  /** The people the search leaves. */
  get shown(): Member[] {
    const query = this.query.trim().toLowerCase();
    return this.members.filter((member) => !query
      || `${member.user_name} ${member.email} ${this.groupNames(member)}`.toLowerCase().includes(query));
  }

  /** Whether the row's menu has anything in it for this person. */
  hasActions(member: Member): boolean {
    return this.canAssignGroups
      || (!this.isSelf(member) && (this.canResetPassword || this.canDelete))
      || (this.canDisable && (!this.isSelf(member) || member.status === 'disabled'));
  }

  day(when: string | null | undefined): string {
    return dayDate(when || '');
  }

  /** The person whose groups are open, by name. */
  get editingName(): string {
    const member = this.members.find((each) => each.user_id === this.editingId);
    return member?.user_name || member?.email || '';
  }


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
    this.show(this.groupsDialog);
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

      this.dialogRef?.close();
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
    if (status === 'disabled') {
      const sure = await this.confirm.ask({
        title: `Disable ${member.email}?`,
        body: 'Their sessions end at once, and they cannot sign in until the account is enabled again.',
        confirm: 'Disable',
        danger: true,
      });
      if (!sure) return;
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
  /** Who what they own can pass to: the remover first, then the rest. */
  successorChoices: Choice[] = [];

  async requestRemove(member: Member): Promise<void> {
    this.removing = member;
    this.leaving = null;
    this.successorId = '';
    this.error = '';
    this.successorChoices = [
      { value: '', name: 'Me' },
      ...this.members
        .filter((each) => each.status === 'active' && each.user_id !== member.user_id && !this.isSelf(each))
        .map((each) => ({ value: each.user_id, name: each.user_name || each.email, hint: each.email })),
    ];
    this.show(this.leavingDialog);
    const preview = await this.members_.leaving(member.user_id);
    if (preview.error) return this.fail(preview.error);
    this.leaving = preview;
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
      this.dialogRef?.close();
      await this.reload();
      const to = result.successor?.email ? ` What they owned is now ${result.successor.email}'s.` : '';
      this.flash(`${member.email} removed.${to}`);
    } finally {
      this.busy.delete(member.user_id);
    }
  }

  // ── Invitations ─────────────────────────────────────────────────────

  /** The group the platform seeds for people who use it without
   *  administering it (backend/server/authentication/provisioning.py). */
  private static readonly MEMBERS_GROUP = 'Members';

  startInvite(): void {
    this.inviting = true;
    this.show(this.inviteDialog);
    this.inviteName = '';
    this.handed = null;
    this.inviteEmail = '';
    // A new person is offered Members already ticked: in no group at
    // all they could sign in and do nothing.
    const members = this.groups.find(
      (group) => group.group_name === MembersComponent.MEMBERS_GROUP);
    this.inviteGroupIds = new Set<string>(members ? [members.group_id] : []);
    this.inviteLink = '';
    this.error = '';
  }

toggleInviteGroup(groupId: string): void {
    this.inviteGroupIds.has(groupId)
      ? this.inviteGroupIds.delete(groupId)
      : this.inviteGroupIds.add(groupId);
  }

  /** Add a person and show the password to hand them. */
  async addPerson(): Promise<void> {
    const email = this.inviteEmail.trim();
    const name = this.inviteName.trim();
    this.tried = true;
    this.error = '';
    if (!name || !email) return;

    this.saving = true;
    try {
      const result = await this.members_.create(email, name, [...this.inviteGroupIds]);
      if (result.error) {
        if (result.ungrantable?.length) {
          const shown = result.ungrantable.slice(0, 5).join(', ');
          return this.fail(
            `You cannot add someone to groups granting permissions you do not hold: ${shown}.`,
          );
        }
        return this.fail(result.error);
      }
      await this.reload();
      this.inviting = false;
      this.handed = { email, password: result.password ?? '', reset: false };
      this.error = '';
      this.show(this.handedDialog);
    } finally {
      this.saving = false;
    }
  }

  /** A temporary password for someone who forgot theirs. */
  async resetPassword(member: Member): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Reset the password of ${member.email}?`,
      body: 'They are signed out everywhere, and you are shown a temporary password to hand them.',
      confirm: 'Reset password',
    });
    if (!sure) return;

    this.busy.add(member.user_id);
    try {
      const result = await this.members_.resetPassword(member.user_id);
      if (result.error) {
        return this.fail(result.error);
      }
      await this.reload();
      this.handed = { email: member.email, password: result.password ?? '', reset: true };
      this.error = '';
      this.show(this.handedDialog);
    } finally {
      this.busy.delete(member.user_id);
    }
  }

  async copyPassword(): Promise<void> {
    if (!this.handed) return;
    try {
      await navigator.clipboard.writeText(this.handed.password);
      this.copied = true;
    } catch {
      this.fail('Could not copy — select the password and copy it yourself.');
    }
  }

  async sendInvite(): Promise<void> {
    this.tried = true;
    this.error = '';
    const email = this.inviteEmail.trim();
    if (!email) return;

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
        this.dialogRef?.close();
        this.flash(`Invitation emailed to ${email}.`);
      } else {
        // No email service configured: the link exists only here, right now.
        this.inviteLink = result.accept_url ?? '';
        this.copied = false;
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
        this.show(this.inviteDialog);
        this.inviteEmail = invitation.email;
        this.inviteLink = result.accept_url ?? '';
        this.copied = false;
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
      this.copied = true;
    } catch {
      this.fail('Could not copy — select the link and copy it yourself.');
    }
  }
}
