import { Profile } from 'src/app/services/profile.service';

export type ShareMode = 'private' | 'groups' | 'people' | 'org';

export interface Peer {
  user_id: string;
  user_name?: string;
  email?: string;
}

export interface ResourceOwner {
  groups?: string[];
  users?: string[];
}

/**
 * Who can see one resource — the private / my groups / whole organization
 * choice, and the owner map it turns into.
 *
 * Skills grew this logic first, inside their own editor.
 * Files and records need exactly the same thing, and a third and fourth
 * copy of "is 'everyone' in owner.groups" is how the four drift apart
 * and start disagreeing about what "shared" means. So it lives here, as
 * plain state a page can hold, open and save.
 *
 * Not a component: the pages differ in where the control sits and what
 * else is being edited beside it. What must not differ is the reading
 * and the writing of the owner map, which is all this owns.
 */
export class ShareEditor {
  mode: ShareMode = 'private';
  groups = new Set<string>();
  users = new Set<string>();

  /** The people a person-share may reach — everyone sharing an explicit
   *  group with the user. The page loads this once via
   *  ProfileService.peers(); empty hides the option. */
  peers: Peer[] = [];

  /** The resource being shared, or '' when the editor is closed. */
  ref = '';
  saving = false;

  constructor(private profile: () => Profile | null) {}

  // ── The user's own position ─────────────────────────────────────────

  get myUserId(): string {
    return this.profile()?.user_id ?? '';
  }

  /** Groups a person may share INTO. "everyone" is the organization, and
   *  reaching it is a separate grant, so it never appears as a group. */
  get myGroups(): { group_id: string; group_name: string }[] {
    return (this.profile()?.groups ?? []).filter(
      (group) => group.group_id !== 'everyone',
    );
  }

  // ── Opening and closing ─────────────────────────────────────────────

  open(ref: string, owner: ResourceOwner | undefined): void {
    this.ref = ref;
    const groups = owner?.groups ?? [];
    const others = (owner?.users ?? []).filter((id) => id !== this.myUserId);
    if (groups.includes('everyone')) {
      this.mode = 'org';
    } else if (groups.length) {
      this.mode = 'groups';
    } else if (others.length) {
      this.mode = 'people';
    } else {
      this.mode = 'private';
    }
    this.groups = new Set(groups.filter((id) => id !== 'everyone'));
    this.users = new Set(others);
  }

  close(): void {
    this.ref = '';
    this.saving = false;
  }

  isOpen(ref: string): boolean {
    return this.ref === ref;
  }

  toggleGroup(groupId: string): void {
    if (!this.groups.delete(groupId)) this.groups.add(groupId);
  }

  toggleUser(userId: string): void {
    if (!this.users.delete(userId)) this.users.add(userId);
  }

  /** Choosing "my groups" or "specific people" with nothing ticked
   *  would silently save as private, which is not what was said. */
  get ready(): boolean {
    if (this.mode === 'groups') return this.groups.size > 0;
    if (this.mode === 'people') return this.users.size > 0;
    return true;
  }

  // ── What gets saved ─────────────────────────────────────────────────

  owner(): ResourceOwner {
    if (this.mode === 'org') return { groups: ['everyone'], users: [] };
    if (this.mode === 'groups') {
      return { groups: Array.from(this.groups), users: [this.myUserId] };
    }
    if (this.mode === 'people') {
      return {
        groups: [],
        users: [this.myUserId,
                ...Array.from(this.users).filter((u) => u !== this.myUserId)],
      };
    }
    return { groups: [], users: [this.myUserId] };
  }

  // ── What gets shown ─────────────────────────────────────────────────

  /** A private resource still names its creator in owner.users, so only a
   *  group — or a user who is not me — counts as actually shared. */
  summary(owner: ResourceOwner | undefined, createdBy: string): string {
    const groups = owner?.groups ?? [];
    if (groups.includes('everyone')) return 'Organization-wide';
    if (groups.length) {
      return groups.length === 1
        ? 'Shared with a group'
        : `Shared with ${groups.length} groups`;
    }
    const others = (owner?.users ?? []).filter((id) => id !== this.myUserId);
    if (others.length) {
      return others.length === 1
        ? 'Shared with a person'
        : `Shared with ${others.length} people`;
    }
    return createdBy === this.myUserId ? 'Private' : 'Shared with you';
  }
}
