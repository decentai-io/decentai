import { Component, OnInit } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { firstValueFrom } from 'rxjs';

import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import { OutboundFormat, Subscription, WebhooksService } from 'src/app/services/webhooks.service';
import { DataPageBase } from '../../data-page-base';
import { eventName, formatName } from '../../ai/webhooks/webhooks-shared';
import { DeliveriesDialogComponent, DeliveriesDialogData } from './deliveries-dialog.component';
import { OutboundDialogComponent, OutboundDialogData } from './outbound-dialog.component';

/** How wide the dialogs open. */
const WIDE = { width: '640px', maxWidth: '100vw' };

/**
 * Settings › Notifications out: addresses the platform posts to when
 * something happens that the person would want to know — a line and a
 * link, and the reply's text only where they asked for it.
 *
 * Webhooks in, which other services post to, have their own page under
 * AI › Webhooks.
 */
@Component({
  selector: 'app-outbound',
  standalone: false,
  templateUrl: './outbound.component.html',
  styleUrl: './outbound.component.css',
})
export class OutboundComponent extends DataPageBase implements OnInit {
  loading = true;
  /** Why the notifications could not be read. */
  loadError = '';
  subscriptions: Subscription[] = [];
  events: string[] = [];
  formats: OutboundFormat[] = [];
  limit = 0;

  /** The row an action is on its way for. */
  busy = '';

  constructor(
    private service: WebhooksService,
    public auth: AuthService,
    private dialog: MatDialog,
    private confirm: ConfirmService,
  ) {
    super();
  }

  ngOnInit(): Promise<void> {
    return this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      const page = await this.service.subscriptions();
      if (page.error) this.loadError = page.error;
      this.subscriptions = page.subscriptions;
      this.events = page.events;
      this.formats = page.formats;
      this.limit = page.limit;
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  // ── What the person may do ──────────────────────────────────────────

  get mayCreate(): boolean { return this.auth.can('settings:outbound:create'); }
  get mayUpdate(): boolean { return this.auth.can('settings:outbound:update'); }
  get mayDelete(): boolean { return this.auth.can('settings:outbound:delete'); }
  get mayTest(): boolean { return this.auth.can('settings:outbound:test'); }
  get mayReadSends(): boolean { return this.auth.can('settings:outbound:deliveries'); }
  get mayListWebhooks(): boolean { return this.auth.can('settings:webhook:list'); }

  get atLimit(): boolean { return this.limit > 0 && this.subscriptions.length >= this.limit; }

  get foot(): string {
    if (!this.subscriptions.length) return '';
    return this.limit ? `${this.subscriptions.length} of ${this.limit}` : `${this.subscriptions.length}`;
  }

  // ── Changing them ───────────────────────────────────────────────────

  private take(subscription: Subscription): void {
    const known = this.subscriptions.some((one) => one.subscription_id === subscription.subscription_id);
    this.subscriptions = known
      ? this.subscriptions.map((one) => one.subscription_id === subscription.subscription_id ? subscription : one)
      : [...this.subscriptions, subscription];
  }

  private async open(subscription?: Subscription): Promise<Subscription | null> {
    const ref = this.dialog.open<OutboundDialogComponent, OutboundDialogData, Subscription | null>(
      OutboundDialogComponent,
      { ...WIDE, data: { subscription, events: this.events, formats: this.formats } });
    return (await firstValueFrom(ref.afterClosed())) ?? null;
  }

  async create(): Promise<void> {
    const made = await this.open();
    if (made) {
      this.take(made);
      this.flash(`${made.name} is ready. Send a test to see it arrive.`);
    }
  }

  async edit(subscription: Subscription): Promise<void> {
    const changed = await this.open(subscription);
    if (changed) {
      this.take(changed);
      this.flash(`${changed.name} saved.`);
    }
  }

  /** Turning one on also lifts a pause the platform put on it. */
  async setEnabled(subscription: Subscription, enabled: boolean): Promise<void> {
    this.busy = subscription.subscription_id;
    const result = await this.service.change(subscription.subscription_id, { enabled });
    this.busy = '';
    if (result.error || !result.subscription) return this.fail(result.error || 'That could not be saved.');
    this.take(result.subscription);
    this.flash(enabled ? `${subscription.name} is on.` : `${subscription.name} is off. Nothing is sent to it.`);
  }

  async test(subscription: Subscription): Promise<void> {
    this.busy = subscription.subscription_id;
    const result = await this.service.test(subscription.subscription_id);
    this.busy = '';
    if (result.failed) return this.fail(result.failed);
    if (result.delivered) {
      this.flash(`Test sent to ${subscription.name}: the address answered ${result.status}.`);
      return;
    }
    const answered = result.status ? `The address answered ${result.status}.` : 'The address could not be reached.';
    this.fail(`The test to ${subscription.name} did not arrive. ${answered} ${result.error || ''}`.trim());
  }

  async remove(subscription: Subscription): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Delete ${subscription.name}?`,
      body: 'Nothing more is sent to its address.',
      confirm: 'Delete',
      danger: true,
    });
    if (!sure) return;
    this.busy = subscription.subscription_id;
    const result = await this.service.unsubscribe(subscription.subscription_id);
    this.busy = '';
    if (result.error) return this.fail(result.error);
    this.subscriptions = this.subscriptions.filter((one) => one.subscription_id !== subscription.subscription_id);
    this.flash(`${subscription.name} deleted.`);
  }

  deliveries(subscription: Subscription): void {
    this.dialog.open<DeliveriesDialogComponent, DeliveriesDialogData>(
      DeliveriesDialogComponent,
      { ...WIDE, data: { id: subscription.subscription_id, name: subscription.name } });
  }

  // ── Words ───────────────────────────────────────────────────────────

  formatName(format: string): string {
    return formatName(format);
  }

  eventsWord(subscription: Subscription): string {
    const events = subscription.events || [];
    if (events.length === 1) return eventName(events[0]);
    return `${events.length} events`;
  }

  eventsInFull(subscription: Subscription): string {
    return (subscription.events || []).map(eventName).join(', ');
  }

  /** How a notification stands, besides on and off, which is its switch's. */
  state(subscription: Subscription): { word: string; tone: string; why: string } | null {
    if (subscription.paused) {
      return { word: 'Paused', tone: 'danger',
               why: 'Stopped after too many failures in a row. Turn it on again to resume.' };
    }
    if (subscription.failures > 0) {
      return { word: 'Failing', tone: 'warn',
               why: `${subscription.failures} ${subscription.failures === 1 ? 'failure' : 'failures'} in a row. Open its deliveries to see why.` };
    }
    return null;
  }
}
