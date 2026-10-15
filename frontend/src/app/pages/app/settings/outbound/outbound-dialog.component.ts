import { Component, Inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

import { Choice } from 'src/app/components/ui/radio-keys';
import {
  OutboundFormat, Subscription, SubscriptionDraft, WebhooksService,
} from 'src/app/services/webhooks.service';
import { EVENT_WORDS, FORMAT_WORDS, HMAC_LINE, copyText } from '../../ai/webhooks/webhooks-shared';

/** The subscription being changed, if any, and what the server offers. */
export interface OutboundDialogData {
  subscription?: Subscription;
  events: string[];
  formats: OutboundFormat[];
}

/**
 * Making or changing a notification out: where it goes, in which
 * shape, and for which events. A JSON one is signed, and its secret is
 * shown once, after it is made; Slack and Teams take their own shape
 * and no signature, so for those the dialog simply closes.
 *
 * The dialog closes with the subscription as it now is, or nothing.
 */
@Component({
  selector: 'app-outbound-dialog',
  standalone: false,
  templateUrl: './outbound-dialog.component.html',
  styleUrl: '../../ai/webhooks/webhook-dialog.component.css',
})
export class OutboundDialogComponent {
  readonly editing: Subscription | null;
  readonly formats: Choice<OutboundFormat>[];
  readonly events: { value: string; name: string; hint: string }[];
  readonly hmacLine = HMAC_LINE;

  name = '';
  url = '';
  format: OutboundFormat = 'json';
  chosen = new Set<string>();
  includeReply = false;

  tried = false;
  saving = false;
  error = '';

  /** The subscription and its signing secret, once made. */
  made: Subscription | null = null;
  secret = '';
  copied = false;
  copyFailed = false;
  warned = false;

  constructor(
    public dialog: MatDialogRef<OutboundDialogComponent, Subscription | null>,
    @Inject(MAT_DIALOG_DATA) data: OutboundDialogData,
    private service: WebhooksService,
  ) {
    const formats = data?.formats?.length ? data.formats : (['json', 'slack', 'teams'] as OutboundFormat[]);
    this.formats = formats.map((format) => ({
      value: format, name: FORMAT_WORDS[format]?.name ?? format, hint: FORMAT_WORDS[format]?.hint ?? '',
    }));
    this.events = (data?.events?.length ? data.events : Object.keys(EVENT_WORDS)).map((event) => ({
      value: event, name: EVENT_WORDS[event]?.name ?? event, hint: EVENT_WORDS[event]?.hint ?? '',
    }));
    this.editing = data?.subscription ?? null;
    const from = this.editing;
    if (from) {
      this.name = from.name;
      this.url = from.url;
      this.format = from.format;
      this.chosen = new Set(from.events);
      this.includeReply = from.include_reply;
    }
  }

  toggle(event: string): void {
    if (this.chosen.has(event)) this.chosen.delete(event);
    else this.chosen.add(event);
  }

  get urlProblem(): string {
    if (!this.tried) return '';
    const url = this.url.trim();
    if (!url) return 'Enter the address to send to.';
    return url.toLowerCase().startsWith('https://') ? '' : 'The address must start with https://.';
  }

  get valid(): boolean {
    return !!this.name.trim() && !this.urlProblem && !!this.url.trim() && this.chosen.size > 0;
  }

  /** Everything, for a new one; what changed, for one being edited. */
  draft(): SubscriptionDraft {
    const events = this.events.map((event) => event.value).filter((event) => this.chosen.has(event));
    const all: SubscriptionDraft = {
      name: this.name.trim(), url: this.url.trim(), format: this.format, events, include_reply: this.includeReply,
    };
    const from = this.editing;
    if (!from) return all;
    const changes: SubscriptionDraft = {};
    if (all.name !== from.name) changes.name = all.name;
    if (all.url !== from.url) changes.url = all.url;
    if (all.format !== from.format) changes.format = all.format;
    if ([...events].sort().join() !== [...from.events].sort().join()) changes.events = events;
    if (all.include_reply !== from.include_reply) changes.include_reply = all.include_reply;
    return changes;
  }

  async save(): Promise<void> {
    if (this.saving) return;
    this.tried = true;
    this.error = '';
    if (!this.valid) return;
    const draft = this.draft();
    if (this.editing && !Object.keys(draft).length) {
      this.dialog.close(null);
      return;
    }
    this.saving = true;
    this.dialog.disableClose = true;
    try {
      if (this.editing) {
        const result = await this.service.change(this.editing.subscription_id, draft);
        if (result.error || !result.subscription) {
          this.error = result.error || 'The notification could not be saved.';
          return;
        }
        this.dialog.close(result.subscription);
        return;
      }
      const result = await this.service.subscribe(draft);
      if (result.error || !result.subscription) {
        this.error = result.error || 'The notification could not be made.';
        return;
      }
      // Only a JSON body is signed: the secret matters for it alone.
      if (result.subscription.format !== 'json' || !result.secret) {
        this.dialog.close(result.subscription);
        return;
      }
      this.made = result.subscription;
      this.secret = result.secret;
    } finally {
      this.saving = false;
      this.dialog.disableClose = !!this.made;
    }
  }

  async copy(): Promise<void> {
    if (await copyText(this.secret)) {
      this.copied = true;
      this.copyFailed = false;
      this.warned = false;
    } else {
      this.copyFailed = true;
    }
  }

  done(): void {
    if (!this.copied && !this.warned) {
      this.warned = true;
      return;
    }
    this.dialog.close(this.made);
  }
}
