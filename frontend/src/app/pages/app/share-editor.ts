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

/** The names a listing gives for an owner map's ids, in their order. */
export interface OwnerNames {
  groups?: string[];
  users?: string[];
}

/** Who sees a thing, as a row's chip: a word, and what is behind it. */
export interface SharingChip {
  word: string;
  names: string;
}

/** Up to two names as they are, then the first and how many more. */
function listed(names: string[]): string {
  return names.length <= 2 ? names.join(', ') : `${names[0]} and ${names.length - 1} others`;
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

  /** Who sees a thing, for its row: the groups or the people by name,
   *  "Everyone", "Private", or, on somebody else's, whose it is, which
   *  is what the reader does not know. A private resource still names
   *  its creator in owner.users, so only a group, or a user who is
   *  neither me nor the creator, counts as shared. */
  chip(owner: ResourceOwner | undefined, createdBy: string,
       names?: OwnerNames, createdByName?: string): SharingChip {
    const groups = owner?.groups ?? [];
    const everyone = groups.includes('everyone');
    // An id with no name behind it is a group or a person that is gone.
    const named = (ids: string[], given: string[] | undefined, gone: string, skip: (id: string) => boolean) =>
      ids.map((id, index) => ({ id, name: given?.[index] || '' }))
        .filter((entry) => !skip(entry.id)).map((entry) => entry.name || gone);
    const groupNames = named(groups, names?.groups, 'a deleted group', (id) => id === 'everyone');
    const people = named(owner?.users ?? [], names?.users, 'someone who left',
      (id) => id === this.myUserId || id === createdBy);

    if (createdBy !== this.myUserId) {
      return {
        word: `Shared by ${createdByName || 'a colleague'}`,
        names: everyone ? 'With everyone in the organization'
          : groupNames.length ? `With ${groupNames.join(', ')}` : '',
      };
    }
    if (everyone) return { word: 'Everyone', names: 'Everyone in the organization' };
    if (groupNames.length) return { word: listed(groupNames), names: groupNames.join(', ') };
    if (people.length) return { word: listed(people), names: people.join(', ') };
    return { word: 'Private', names: '' };
  }
}
