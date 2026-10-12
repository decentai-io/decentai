import { Component, OnInit } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { firstValueFrom } from 'rxjs';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { ApiKey, ApiKeysService } from 'src/app/services/api-keys.service';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { CreateKeyDialogComponent } from './create-key-dialog.component';

/**
 * Settings › API keys: a person's own standing credentials for scripts.
 *
 * A key is the person — the same permissions, checked on every
 * request — so the page says so plainly and treats the secret the way
 * a secret deserves: shown once, at creation (the create dialog), and
 * never again. Afterwards a row is a name, a prefix and two dates.
 * Revoking is the only edit, and a revoked key stays listed as a
 * record of what existed.
 */
@Component({
  selector: 'app-api-keys',
  standalone: false,
  templateUrl: './api-keys.component.html',
  styleUrls: ['./api-keys.component.css'],
})
export class ApiKeysComponent implements OnInit {
  loading = true;
  /** Why the keys could not be read. */
  loadError = '';
  keys: ApiKey[] = [];
  /** How many active keys a person may hold, from the server. */
  limit = 0;
  showRevoked = false;
  busyId = '';
  /** Why a key could not be revoked. */
  revokeError = '';

  constructor(
    private service: ApiKeysService,
    public auth: AuthService,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {}

  ngOnInit(): Promise<void> {
    return this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      await this.reload();
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  private async reload(): Promise<void> {
    const page = await this.service.list();
    this.keys = page.keys;
    this.limit = page.limit;
  }

  get canCreate(): boolean { return this.auth.can('settings:apikey:create'); }
  get canRevoke(): boolean { return this.auth.can('settings:apikey:revoke'); }

  get live(): ApiKey[] { return this.keys.filter((key) => !key.revoked_at); }
  get revoked(): ApiKey[] { return this.keys.filter((key) => !!key.revoked_at); }
  get visible(): ApiKey[] { return this.showRevoked ? this.keys : this.live; }
  get atLimit(): boolean { return this.limit > 0 && this.live.length >= this.limit; }

  /** The counts under the list. */
  get foot(): string {
    const active = `${this.live.length} active`;
    return this.limit ? `${active} · ${Math.max(0, this.limit - this.live.length)} available` : active;
  }

  /** Opens the create dialog, which makes the key and shows it once. */
  async create(): Promise<void> {
    const made = await firstValueFrom(this.dialog.open(CreateKeyDialogComponent).afterClosed());
    if (made) await this.reload();
  }

  async revoke(key: ApiKey): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Revoke “${key.name}”?`,
      body: 'Scripts using it stop on their next call. It cannot be turned back on.',
      confirm: 'Revoke',
      danger: true,
    });
    if (!sure) return;
    this.busyId = key.key_id;
    this.revokeError = '';
    try {
      const result = await this.service.revoke(key.key_id);
      if (result.error) {
        this.revokeError = result.error;
        return;
      }
      await this.reload();
    } finally {
      this.busyId = '';
    }
  }

  // ── Words ───────────────────────────────────────────────────────────

  created(key: ApiKey): string {
    return `Created ${relativeDate(key.created_at)}`.trim();
  }

  used(key: ApiKey): string {
    if (key.revoked_at) return `Revoked ${relativeDate(key.revoked_at)}`;
    return key.last_used_at ? `Last used ${relativeDate(key.last_used_at)}` : 'Never used';
  }

  /** The whole dates, for the row's tooltip. */
  datesInFull(key: ApiKey): string {
    const last = key.revoked_at || key.last_used_at;
    return [fullDate(key.created_at), last ? fullDate(last) : ''].filter(Boolean).join(' · ');
  }
}
