import {
  Component, EventEmitter, Input, OnChanges, Output, SimpleChanges,
} from '@angular/core';

import {
  AgentSource, SourceCredential,
} from 'src/app/services/agents.service';

export interface SourceDraft {
  name: string;
  url: string;
  ref: string;
  /** undefined = leave the stored credential alone, null = clear it,
   *  a value = set it (blank token keeps the stored one). */
  credential?: SourceCredential | null;
  /** Who inside the org may see it. */
  owner?: { groups: string[]; users: string[] };
}

type ShareMode = 'private' | 'groups' | 'users' | 'org';

/**
 * Saving a repository: everything the platform needs to read one, in
 * one place — including the credential, when the repository is private.
 * The token is typed here once, stored encrypted on the source, and
 * never shown back; a repository nobody needs a token for is the normal
 * case and the default.
 */
@Component({
  selector: 'app-source-form',
  standalone: false,
  templateUrl: './source-form.component.html',
  styleUrls: [
    '../../data-shared.css',
    './marketplace.component.css',
  ],
})
export class SourceFormComponent implements OnChanges {
  /** The source being edited, or null when adding a new one. */
  @Input() source: AgentSource | null = null;
  @Input() myGroups: { group_id: string; group_name: string }[] = [];
  @Input() peers: { user_id: string; user_name: string; email: string }[] = [];
  @Input() busy = false;
  /** A repository this deployment suggests. A hint, never a default:
   *  a platform that shipped pointing at its vendor would be making that
   *  choice for every deployment. */
  @Input() suggestedUrl = '';

  @Output() save = new EventEmitter<SourceDraft>();
  @Output() cancel = new EventEmitter<void>();

  draft: SourceDraft = { name: '', url: '', ref: '' };
  isPrivate = false;
  username = '';
  token = '';
  shareMode: ShareMode = 'private';
  selectedGroups = new Set<string>();
  selectedUsers = new Set<string>();
  guideOpen = false;

  ngOnChanges(changes: SimpleChanges): void {
    // Reset ONLY when the source being edited changes. The group/peer
    // lists arrive async as inputs too, and resetting on those wiped
    // whatever the person had just ticked.
    if (!changes['source']) return;
    this.draft = {
      name: this.source?.name || '',
      url: this.source?.url || '',
      ref: this.source?.ref || '',
    };
    this.isPrivate = !!this.source?.has_credential;
    this.username = this.source?.credential_user || '';
    this.token = ''; // write-only: blank means keep

    // A NEW source (no owner yet) starts private, like everything else.
    const groups = this.source?.owner?.groups || [];
    const others = (this.source?.owner?.users || [])
      .filter((u) => u !== this.source?.created_by_id);
    if (groups.includes('everyone')) this.shareMode = 'org';
    else if (groups.length) this.shareMode = 'groups';
    else if (others.length) this.shareMode = 'users';
    else this.shareMode = 'private';
    this.selectedGroups = new Set(groups.filter((g) => g !== 'everyone'));
    this.selectedUsers = new Set(others);
  }

  toggleGroup(groupId: string): void {
    if (this.selectedGroups.has(groupId)) this.selectedGroups.delete(groupId);
    else this.selectedGroups.add(groupId);
  }

  toggleUser(userId: string): void {
    if (this.selectedUsers.has(userId)) this.selectedUsers.delete(userId);
    else this.selectedUsers.add(userId);
  }

  private buildOwner(): { groups: string[]; users: string[] } {
    if (this.shareMode === 'org') return { groups: ['everyone'], users: [] };
    if (this.shareMode === 'groups') {
      return { groups: Array.from(this.selectedGroups), users: [] };
    }
    if (this.shareMode === 'users') {
      return { groups: [], users: Array.from(this.selectedUsers) };
    }
    return { groups: [], users: [] }; // creator is kept server-side
  }

  get shareBlocker(): string {
    if (this.shareMode === 'groups' && !this.selectedGroups.size) {
      return 'Pick at least one group, or share it another way.';
    }
    if (this.shareMode === 'users' && !this.selectedUsers.size) {
      return 'Pick at least one person, or share it another way.';
    }
    return '';
  }

  /** Whether a token is already stored — the placeholder that says
   *  "leave blank to keep it" is only honest when one is. */
  get hasStoredToken(): boolean {
    return !!this.source?.has_credential;
  }

  get editing(): boolean {
    return !!this.source;
  }

  /** A NEW private source needs its token now; an edited one may leave
   *  the box blank to keep the stored token. */
  get missingToken(): boolean {
    return this.isPrivate && !this.token.trim() && !this.hasStoredToken;
  }

  useSuggested(): void {
    if (!this.suggestedUrl) return;
    this.draft = {
      name: this.draft.name || 'Reference agents',
      url: this.suggestedUrl,
      ref: 'main',
    };
    this.isPrivate = false;
    this.username = '';
    this.token = '';
  }

  submit(): void {
    if (!this.draft.url.trim() || this.busy || this.missingToken
        || this.shareBlocker) return;
    this.save.emit({
      name: this.draft.name.trim(),
      url: this.draft.url.trim(),
      ref: this.draft.ref.trim(),
      // Unticked on a source that had one = an instruction to clear it;
      // unticked on a fresh form = simply nothing to send.
      credential: this.isPrivate
        ? { username: this.username.trim(), token: this.token }
        : (this.hasStoredToken ? null : undefined),
      owner: this.buildOwner(),
    });
  }
}
