import { Component, OnInit } from '@angular/core';

import { ApiKey, ApiKeysService, MadeApiKey } from 'src/app/services/api-keys.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataPageBase } from '../../data-page-base';

/**
 * API keys: a person's own standing credentials for scripts. A tab of
 * the Settings page, beside the memory the assistant keeps about them.
 *
 * A key is the person — the same permissions, checked on every
 * request — so the page says so plainly and treats the secret the way
 * a secret deserves: shown once, at creation, with a copy button, and
 * never again. Afterwards a row is a name, a prefix and three dates.
 * Revoking is the only edit, and a revoked key stays listed as a
 * record of what existed.
 */
@Component({
  selector: 'app-api-keys',
  standalone: false,
  templateUrl: './api-keys.component.html',
  styleUrls: ['../../data-shared.css', '../../../admin/iam-shared.css', './api-keys.component.css'],
})
export class ApiKeysComponent extends DataPageBase implements OnInit {
  loading = true;
  keys: ApiKey[] = [];
  limit = 0;
  busyId = '';

  composerOpen = false;
  newName = '';
  creating = false;
  /** The one moment the secret is in the open. Closing the card is the
   *  end of it: nothing on this page can show it again. */
  made: MadeApiKey | null = null;
  copied = false;

  revokeTarget: ApiKey | null = null;
  showRevoked = false;
  readonly nameLimit = 80;

  constructor(private service: ApiKeysService, public auth: AuthService) {
    super();
  }

  async ngOnInit(): Promise<void> {
    await this.reload();
    this.loading = false;
  }

  private async reload(): Promise<void> {
    const page = await this.service.list();
    this.keys = page.keys;
    this.limit = page.limit;
  }

  get canCreate(): boolean { return this.auth.can('settings:apikey:create'); }
  get canRevoke(): boolean { return this.auth.can('settings:apikey:revoke'); }

  get live(): ApiKey[] { return this.keys.filter((k) => !k.revoked_at); }
  get revoked(): ApiKey[] { return this.keys.filter((k) => !!k.revoked_at); }
  get visible(): ApiKey[] { return this.showRevoked ? this.keys : this.live; }
  get atLimit(): boolean { return this.limit > 0 && this.live.length >= this.limit; }

  // ── Making one ──────────────────────────────────────────────────────

  openComposer(): void {
    this.composerOpen = true;
    this.newName = '';
    this.error = '';
  }

  closeComposer(): void {
    if (!this.creating) this.composerOpen = false;
  }

  get canSubmit(): boolean {
    return !this.creating && this.newName.trim().length > 0 && !this.atLimit;
  }

  async create(): Promise<void> {
    if (!this.canSubmit) return;
    this.creating = true;
    try {
      const result = await this.service.create(this.newName.trim());
      if (result.error || !result.key) return this.fail(result.error || 'Could not make the key.');
      this.made = result.key;
      this.copied = false;
      this.composerOpen = false;
      await this.reload();
    } finally {
      this.creating = false;
    }
  }

  async copyMade(): Promise<void> {
    if (!this.made) return;
    try {
      await navigator.clipboard.writeText(this.made.key);
      this.copied = true;
    } catch {
      this.copied = false;
      this.fail('Could not copy — select the key and copy it yourself.');
    }
  }

  dismissMade(): void {
    this.made = null;
    this.copied = false;
  }

  // ── Revoking ────────────────────────────────────────────────────────

  requestRevoke(key: ApiKey): void {
    this.revokeTarget = key;
    this.error = '';
  }

  closeRevoke(): void {
    if (!this.busyId) this.revokeTarget = null;
  }

  async revoke(key: ApiKey): Promise<void> {
    this.busyId = key.key_id;
    try {
      const result = await this.service.revoke(key.key_id);
      if (result.error) return this.fail(result.error);
      this.revokeTarget = null;
      await this.reload();
      this.flash(`"${key.name}" revoked. Scripts using it stop on their next call.`);
    } finally {
      this.busyId = '';
    }
  }

  // ── Words ───────────────────────────────────────────────────────────

  when(value?: string | null): string {
    if (!value) return '';
    const date = new Date(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
  }

  usedLabel(key: ApiKey): string {
    if (key.revoked_at) return `Revoked ${this.when(key.revoked_at)}`;
    return key.last_used_at ? `Last used ${this.when(key.last_used_at)}` : 'Never used';
  }
}
