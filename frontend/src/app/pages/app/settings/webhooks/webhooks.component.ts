import { Component, OnInit } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { firstValueFrom } from 'rxjs';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { AuthService } from 'src/app/services/auth.service';
import { ConfirmService } from 'src/app/services/confirm.service';
import {
  OutboundFormat, Subscription, Webhook, WebhooksService,
} from 'src/app/services/webhooks.service';
import { DataPageBase } from '../../data-page-base';
import { DeliveriesDialogComponent, DeliveriesDialogData } from './deliveries-dialog.component';
import { OutboundDialogComponent, OutboundDialogData } from './outbound-dialog.component';
import { WebhookDialogComponent, WebhookDialogData } from './webhook-dialog.component';
import { eventName, formatName } from './webhooks-shared';

/** How wide the dialogs open. */
const WIDE = { width: '640px', maxWidth: '100vw' };

/**
 * Settings › Webhooks: a person's own doors in and notifications out.
 *
 * Incoming: an address another service posts events to. Each webhook
 * has a standing chat of its own, made with it, which hears every
 * event with the webhook's instruction and acts with the person's
 * permissions. Its secret is shown once, when it is made or replaced.
 * Deleting a webhook keeps its chat.
 *
 * Outgoing: an address the platform posts to when something happens
 * that the person would want to know — a line and a link, and the
 * reply's text only where they asked for it.
 */
@Component({
  selector: 'app-webhooks',
  standalone: false,
  templateUrl: './webhooks.component.html',
  styleUrl: './webhooks.component.css',
})
export class WebhooksComponent extends DataPageBase implements OnInit {
  loading = true;
  /** Why the webhooks could not be read. */
  loadError = '';
  webhooks: Webhook[] = [];
  limit = 0;

  outLoading = true;
  outLoadError = '';
  subscriptions: Subscription[] = [];
  events: string[] = [];
  formats: OutboundFormat[] = [];
  outLimit = 0;

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

  ngOnInit(): Promise<unknown> {
    return Promise.all([this.load(), this.loadOut()]);
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      const page = await this.service.list();
      if (page.error) this.loadError = page.error;
      this.webhooks = page.webhooks;
      this.limit = page.limit;
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  async loadOut(): Promise<void> {
    if (!this.mayListOut) {
      this.outLoading = false;
      return;
    }
    this.outLoading = true;
    this.outLoadError = '';
    try {
      const page = await this.service.subscriptions();
      if (page.error) this.outLoadError = page.error;
      this.subscriptions = page.subscriptions;
      this.events = page.events;
      this.formats = page.formats;
      this.outLimit = page.limit;
    } catch {
      this.outLoadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.outLoading = false;
    }
  }

  // ── What the person may do ──────────────────────────────────────────

  get mayCreate(): boolean { return this.auth.can('settings:webhook:create'); }
  get mayUpdate(): boolean { return this.auth.can('settings:webhook:update'); }
  get mayRotate(): boolean { return this.auth.can('settings:webhook:rotate'); }
  get mayDelete(): boolean { return this.auth.can('settings:webhook:delete'); }
  get mayReadDeliveries(): boolean { return this.auth.can('settings:webhook:deliveries'); }
  get mayListOut(): boolean { return this.auth.can('settings:outbound:list'); }
  get mayCreateOut(): boolean { return this.auth.can('settings:outbound:create'); }
  get mayUpdateOut(): boolean { return this.auth.can('settings:outbound:update'); }
  get mayDeleteOut(): boolean { return this.auth.can('settings:outbound:delete'); }
  get mayTestOut(): boolean { return this.auth.can('settings:outbound:test'); }
  get mayReadSends(): boolean { return this.auth.can('settings:outbound:deliveries'); }

  get atLimit(): boolean { return this.limit > 0 && this.webhooks.length >= this.limit; }
  get outAtLimit(): boolean { return this.outLimit > 0 && this.subscriptions.length >= this.outLimit; }

  get foot(): string {
    if (!this.webhooks.length) return '';
    const on = this.webhooks.filter((hook) => hook.enabled).length;
    return this.limit ? `${on} on · ${Math.max(0, this.limit - this.webhooks.length)} available` : `${on} on`;
  }

  get outFoot(): string {
    if (!this.subscriptions.length) return '';
    return this.outLimit
      ? `${this.subscriptions.length} of ${this.outLimit}`
      : `${this.subscriptions.length}`;
  }

  // ── Incoming ────────────────────────────────────────────────────────

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
    if (made) {
      this.take(made);
      this.flash(`${made.name} is ready. Its events are heard in a chat of its own.`);
    }
  }

  async edit(webhook: Webhook): Promise<void> {
    const changed = await this.openWebhook({ webhook });
    if (changed) {
      this.take(changed);
      this.flash(`${changed.name} saved.`);
    }
  }

  async setEnabled(webhook: Webhook, enabled: boolean): Promise<void> {
    this.busy = webhook.webhook_id;
    const result = await this.service.update(webhook.webhook_id, { enabled });
    this.busy = '';
    if (result.error || !result.webhook) return this.fail(result.error || 'That could not be saved.');
    this.take(result.webhook);
    this.flash(enabled ? `${webhook.name} is on.` : `${webhook.name} is off. Events sent to it are refused.`);
  }

  async rotate(webhook: Webhook): Promise<void> {
    const sure = await this.confirm.ask({
      title: `Replace the secret of ${webhook.name}?`,
      body: 'The old secret stops working at once. Whatever sends to this webhook needs the new one.',
      confirm: 'Replace',
      danger: true,
    });
    if (!sure) return;
    this.busy = webhook.webhook_id;
    const result = await this.service.rotate(webhook.webhook_id);
    this.busy = '';
    if (result.error || !result.webhook || !result.secret) {
      return this.fail(result.error || 'The secret could not be replaced.');
    }
    this.take(result.webhook);
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
    this.busy = webhook.webhook_id;
    const result = await this.service.remove(webhook.webhook_id);
    this.busy = '';
    if (result.error) return this.fail(result.error);
    this.webhooks = this.webhooks.filter((one) => one.webhook_id !== webhook.webhook_id);
    this.flash(`${webhook.name} deleted. Its chat is still in your chats.`);
  }

  deliveries(kind: 'in' | 'out', id: string, name: string): void {
    this.dialog.open<DeliveriesDialogComponent, DeliveriesDialogData>(
      DeliveriesDialogComponent, { ...WIDE, data: { kind, id, name } });
  }

  // ── Outgoing ────────────────────────────────────────────────────────

  private takeOut(subscription: Subscription): void {
    const known = this.subscriptions.some((one) => one.subscription_id === subscription.subscription_id);
    this.subscriptions = known
      ? this.subscriptions.map((one) => one.subscription_id === subscription.subscription_id ? subscription : one)
      : [...this.subscriptions, subscription];
  }

  private async openOut(subscription?: Subscription): Promise<Subscription | null> {
    const ref = this.dialog.open<OutboundDialogComponent, OutboundDialogData, Subscription | null>(
      OutboundDialogComponent,
      { ...WIDE, data: { subscription, events: this.events, formats: this.formats } });
    return (await firstValueFrom(ref.afterClosed())) ?? null;
  }

  async createOut(): Promise<void> {
    const made = await this.openOut();
    if (made) {
      this.takeOut(made);
      this.flash(`${made.name} is ready. Send a test to see it arrive.`);
    }
  }

  async editOut(subscription: Subscription): Promise<void> {
    const changed = await this.openOut(subscription);
    if (changed) {
      this.takeOut(changed);
      this.flash(`${changed.name} saved.`);
    }
  }

  /** Turning one on also lifts a pause the platform put on it. */
  async setOutEnabled(subscription: Subscription, enabled: boolean): Promise<void> {
    this.busy = subscription.subscription_id;
    const result = await this.service.change(subscription.subscription_id, { enabled });
    this.busy = '';
    if (result.error || !result.subscription) return this.fail(result.error || 'That could not be saved.');
    this.takeOut(result.subscription);
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

  async removeOut(subscription: Subscription): Promise<void> {
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

  // ── Words ───────────────────────────────────────────────────────────

  lastEvent(webhook: Webhook): string {
    return webhook.last_event_at ? `Last event ${relativeDate(webhook.last_event_at)}` : 'No events yet';
  }

  lastEventInFull(webhook: Webhook): string {
    return fullDate(webhook.last_event_at);
  }

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
  outState(subscription: Subscription): { word: string; tone: string; why: string } | null {
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
