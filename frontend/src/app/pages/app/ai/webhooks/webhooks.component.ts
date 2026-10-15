import { Component, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';

import { Candidate } from 'src/app/components/transfer-dialog/transfer-dialog.component';
import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';
import { Webhook, WebhooksService } from 'src/app/services/webhooks.service';
import { DataPageBase } from '../../data-page-base';
import { Peer, ShareEditor } from '../../share-editor';
import { WebhookDialogComponent, WebhookDialogData } from './webhook-dialog.component';
import { WebhookTestDialogComponent } from './webhook-test-dialog.component';
import { WebhookViewComponent } from './webhook-view.component';

/** How wide the dialogs open. */
const WIDE = { width: '640px', maxWidth: '100vw' };

/**
 * Webhooks: addresses other services post events to, each heard in a
 * standing chat of the person's, which acts on it with their
 * permissions. Beside them, the webhooks colleagues shared, to watch.
 *
 * A row's name opens the webhook at /ai/webhooks/<id>, beside the list
 * on a wide screen, as a schedule opens: every event it heard and what
 * its chat did with it, and, for its owner, its settings. Making one
 * and changing one are the same dialog (webhook-dialog.component).
 *
 * Notifications out are a setting of the person's, under Settings.
 */
@Component({
  selector: 'app-webhooks',
  standalone: false,
  templateUrl: './webhooks.component.html',
  styleUrl: './webhooks.component.css',
})
export class WebhooksComponent extends DataPageBase implements OnInit, OnDestroy {
  loading = true;
  /** Why the webhooks could not be read. */
  loadError = '';
  webhooks: Webhook[] = [];
  shared: Webhook[] = [];
  limit = 0;

  /** The webhook opened beside the list; '' for none. */
  openId = '';
  private address = new Subscription();
  @ViewChild(WebhookViewComponent) private view?: WebhookViewComponent;

  /** The webhook an action is on its way for. */
  busyId = '';

  private profile: Profile | null = null;
  private peers: Peer[] = [];
  /** Who sees a webhook's events, beside its owner. */
  readonly share = new ShareEditor(() => this.profile);
  shareProblem = '';

  transferTarget: Webhook | null = null;
  transferring = false;

  constructor(
    private service: WebhooksService,
    private profiles: ProfileService,
    public auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {
    super();
  }

  async ngOnInit(): Promise<void> {
    this.address.add(this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) this.readOpened();
    }));
    const [, profile, peers] = await Promise.all([
      this.reload(),
      this.profiles.get().catch(() => null),
      this.profiles.peers().catch(() => []),
    ]);
    this.profile = profile;
    this.peers = peers;
    this.share.peers = peers;
    this.loading = false;
    this.readOpened();
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
  }

  async reload(): Promise<void> {
    this.loadError = '';
    try {
      const page = await this.service.list();
      // A read that failed says so and leaves what was on screen.
      if (page.error) {
        this.loadError = page.error;
        return;
      }
      this.webhooks = page.webhooks;
      this.shared = page.shared;
      this.limit = page.limit;
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    }
  }

  // ── What the person may do ──────────────────────────────────────────

  get mayCreate(): boolean { return this.auth.can('settings:webhook:create'); }
  get mayUpdate(): boolean { return this.auth.can('settings:webhook:update'); }

  get atLimit(): boolean { return this.limit > 0 && this.webhooks.length >= this.limit; }

  get foot(): string {
    if (!this.webhooks.length) return '';
    const on = this.webhooks.filter((hook) => hook.enabled).length;
    return this.limit ? `${on} on · ${this.webhooks.length} of ${this.limit}` : `${on} on`;
  }

  // ── The address ─────────────────────────────────────────────────────

  private readOpened(): void {
    this.openId = this.route.snapshot.firstChild?.paramMap.get('id') || '';
    if (this.openId && !this.loading && !this.loadError && !this.opened) {
      this.fail('That webhook is not yours, or shared with you, any more.');
      this.closeOpened();
    }
  }

  get opened(): Webhook | null {
    return [...this.webhooks, ...this.shared].find((hook) => hook.webhook_id === this.openId) || null;
  }

  closeOpened(): void {
    void this.router.navigate(['/ai/webhooks']);
  }

  // ── Words ───────────────────────────────────────────────────────────

  lastEvent(webhook: Webhook): string {
    return webhook.last_event_at ? `Last event ${relativeDate(webhook.last_event_at)}` : 'No events yet';
  }

  lastEventInFull(webhook: Webhook): string {
    return fullDate(webhook.last_event_at);
  }

  /** Whose a shared webhook is, by the name of a colleague the reader
   *  shares a group with. */
  ownerName(webhook: Webhook): string {
    const owner = this.peers.find((peer) => peer.user_id === webhook.owner_id);
    return owner?.user_name || owner?.email || 'a colleague';
  }

  sharedWith(webhook: Webhook): number {
    return (webhook.viewers?.groups?.length || 0) + (webhook.viewers?.users?.length || 0);
  }

  // ── Making and changing: the one dialog ─────────────────────────────

  private take(webhook: Webhook): void {
    const known = this.webhooks.some((one) => one.webhook_id === webhook.webhook_id);
    this.webhooks = known
      ? this.webhooks.map((one) => one.webhook_id === webhook.webhook_id ? webhook : one)
      : [...this.webhooks, webhook];
  }

  private async openWebhook(data: WebhookDialogData | null): Promise<Webhook | null> {
    const ref = this.dialog.open<WebhookDialogComponent, WebhookDialogData | null, Webhook | null>(
      WebhookDialogComponent, { ...WIDE, data });
    return (await firstValueFrom(ref.afterClosed())) ?? null;
  }

  async create(): Promise<void> {
    const made = await this.openWebhook(null);
    if (!made) return;
    this.take({ ...made, role: 'owner' });
    this.flash(`${made.name} is ready. Its events are heard in a chat of its own.`);
    void this.router.navigate(['/ai/webhooks', made.webhook_id]);
  }

  async edit(webhook: Webhook): Promise<void> {
    const changed = await this.openWebhook({ webhook });
    if (changed) {
      this.take({ ...changed, role: 'owner' });
      this.flash(`${changed.name} saved.`);
    }
  }

  async setEnabled(webhook: Webhook, enabled: boolean): Promise<void> {
    this.busyId = webhook.webhook_id;
    const result = await this.service.update(webhook.webhook_id, { enabled });
    this.busyId = '';
    if (result.error || !result.webhook) return this.fail(result.error || 'That could not be saved.');
    this.take({ ...result.webhook, role: 'owner' });
    this.flash(enabled ? `${webhook.name} is on.` : `${webhook.name} is off. Events sent to it are refused.`);
  }

  async sendTest(webhook: Webhook): Promise<void> {
    const ref = this.dialog.open<WebhookTestDialogComponent, Webhook, boolean>(
      WebhookTestDialogComponent, { ...WIDE, data: webhook });
    if (!(await firstValueFrom(ref.afterClosed()))) return;
    this.flash('Test event sent. What its chat does with it shows under Executions.');
    await this.view?.load();
  }

  async rotate(webhook: Webhook): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Replace the secret of ${webhook.name}?`,
      body: 'The old secret stops working at once. Whatever sends to this webhook needs the new one.',
      confirm: 'Replace',
      danger: true,
    });
    if (!sure) return;
    this.busyId = webhook.webhook_id;
    const result = await this.service.rotate(webhook.webhook_id);
    this.busyId = '';
    if (result.error || !result.webhook || !result.secret) {
      return this.fail(result.error || 'The secret could not be replaced.');
    }
    this.take({ ...result.webhook, role: 'owner' });
    await this.openWebhook({ webhook: result.webhook, secret: result.secret });
  }

  async remove(webhook: Webhook): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Delete ${webhook.name}?`,
      body: 'Events sent to it are refused from now on. Its chat stays, with everything it heard.',
      confirm: 'Delete',
      danger: true,
    });
    if (!sure) return;
    this.busyId = webhook.webhook_id;
    const result = await this.service.remove(webhook.webhook_id);
    this.busyId = '';
    if (result.error) return this.fail(result.error);
    if (this.openId === webhook.webhook_id) this.closeOpened();
    this.webhooks = this.webhooks.filter((one) => one.webhook_id !== webhook.webhook_id);
    this.flash(`${webhook.name} deleted. Its chat is still in your chats.`);
  }

  // ── Sharing: to watch, never to change ──────────────────────────────

  openShare(webhook: Webhook): void {
    this.shareProblem = '';
    this.share.open(webhook.webhook_id, webhook.viewers);
  }

  async saveShare(): Promise<void> {
    this.share.saving = true;
    this.shareProblem = '';
    try {
      // The owner map names its owner; the viewers are everyone else.
      const owner = this.share.owner();
      const viewers = {
        groups: owner.groups ?? [],
        users: (owner.users ?? []).filter((id) => id !== this.share.myUserId),
      };
      const result = await this.service.share(this.share.ref, viewers);
      if (result.error || !result.webhook) {
        this.shareProblem = result.error || 'Sharing could not be saved.';
        return;
      }
      this.take({ ...result.webhook, role: 'owner' });
      this.share.close();
      this.flash('Sharing updated.');
    } finally {
      this.share.saving = false;
    }
  }

  // ── Handing over ────────────────────────────────────────────────────

  requestTransfer(webhook: Webhook): void {
    this.transferTarget = webhook;
  }

  closeTransfer(): void {
    if (!this.transferring) this.transferTarget = null;
  }

  async transfer(person: Candidate): Promise<void> {
    const webhook = this.transferTarget;
    if (!webhook) return;
    const who = person.user_name || person.email;
    const sure = await this.confirm.ask({
      title: `Hand ${webhook.name} over to ${who}?`,
      body: `It and its chat become ${who}’s. Its events run as them, with their permissions, from now on; `
        + 'its address and secret stay the same. It is no longer yours: only they can change it or give it back.',
      confirm: 'Hand over',
      danger: true,
    });
    if (!sure) return;
    this.transferring = true;
    try {
      const result = await this.service.transfer(webhook.webhook_id, person.user_id);
      // Closed either way: a refusal is said on the page, where it can
      // be read, not under the dialog.
      this.transferTarget = null;
      if (result.error) return this.fail(result.error);
      this.closeOpened();
      await this.reload();
      this.flash(`${webhook.name} handed over to ${who}.`);
    } finally {
      this.transferring = false;
    }
  }
}
