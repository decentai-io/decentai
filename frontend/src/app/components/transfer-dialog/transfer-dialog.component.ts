import { Component, EventEmitter, Input, OnInit, Output } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { MembersService } from 'src/app/services/members.service';
import { ProfileService } from 'src/app/services/profile.service';

/** One person who could take something over. */
export interface Candidate {
  user_id: string;
  user_name: string;
  email: string;
}

/**
 * Hand something to another member. The same dialog wherever a thing
 * has a steward — a credential, a record, a file, a skill, a model
 * connection, an agent source — so the act reads the same everywhere:
 * pick a person, and the thing is theirs, with its sharing intact.
 *
 * The people offered are those the caller can name: colleagues who
 * share a group with them, and, for someone who may list the
 * organization's members, everyone active. The current steward is
 * never offered; a transfer to oneself is not a transfer.
 */
@Component({
  selector: 'app-transfer-dialog',
  standalone: false,
  templateUrl: './transfer-dialog.component.html',
  styleUrls: ['../../pages/app/data-shared.css', './transfer-dialog.component.css'],
})
export class TransferDialogComponent implements OnInit {
  /** What is being handed over, in words: "the credential “Gmail”". */
  @Input() subject = 'this';
  /** The current steward, never offered. */
  @Input() ownerId = '';
  @Input() busy = false;
  @Output() chosen = new EventEmitter<Candidate>();
  @Output() closed = new EventEmitter<void>();

  loading = true;
  candidates: Candidate[] = [];
  query = '';
  selectedId = '';

  constructor(
    private profiles: ProfileService,
    private members: MembersService,
    private auth: AuthService,
  ) {}

  async ngOnInit(): Promise<void> {
    const found = new Map<string, Candidate>();
    for (const peer of await this.profiles.peers().catch(() => [])) {
      found.set(peer.user_id, peer);
    }
    if (this.auth.can('iam:user:list')) {
      for (const member of await this.members.list().catch(() => [])) {
        if (member.status === 'active') {
          found.set(member.user_id, {
            user_id: member.user_id, user_name: member.user_name, email: member.email,
          });
        }
      }
    }
    found.delete(this.ownerId);
    this.candidates = [...found.values()]
      .sort((a, b) => (a.user_name || a.email).localeCompare(b.user_name || b.email));
    this.loading = false;
  }

  get visible(): Candidate[] {
    const q = this.query.trim().toLowerCase();
    if (!q) return this.candidates;
    return this.candidates.filter((c) =>
      `${c.user_name} ${c.email}`.toLowerCase().includes(q));
  }

  get selected(): Candidate | null {
    return this.candidates.find((c) => c.user_id === this.selectedId) || null;
  }

  confirm(): void {
    const pick = this.selected;
    if (pick && !this.busy) this.chosen.emit(pick);
  }

  close(): void {
    if (!this.busy) this.closed.emit();
  }
}
