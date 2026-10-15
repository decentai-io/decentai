import { Component, Inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

import { Webhook, WebhooksService } from 'src/app/services/webhooks.service';
import { FIELD_TYPES, exampleBody } from './webhooks-shared';

/**
 * A test event, sent from the page down the same road as a caller's:
 * the body as JSON, started with every field of the webhook, and
 * checked against them by the server, which says what does not fit.
 *
 * Closes with true once the event was accepted.
 */
@Component({
  selector: 'app-webhook-test-dialog',
  standalone: false,
  templateUrl: './webhook-test-dialog.component.html',
  styleUrl: './webhook-dialog.component.css',
})
export class WebhookTestDialogComponent {
  body: string;
  sending = false;
  error = '';

  constructor(
    public dialog: MatDialogRef<WebhookTestDialogComponent, boolean>,
    @Inject(MAT_DIALOG_DATA) public webhook: Webhook,
    private service: WebhooksService,
  ) {
    this.body = exampleBody(webhook.fields || [], 2);
  }

  typeWord(type: string): string {
    return FIELD_TYPES.find((choice) => choice.value === type)?.name || type;
  }

  /** The body as an object, or why it is not one. */
  private parsed(): Record<string, unknown> | string {
    try {
      const value = JSON.parse(this.body || '{}');
      return value && typeof value === 'object' && !Array.isArray(value)
        ? value : 'The body is one JSON object: { "field": value, … }.';
    } catch {
      return 'That is not JSON. Check the quotes and commas.';
    }
  }

  async send(): Promise<void> {
    if (this.sending) return;
    const data = this.parsed();
    if (typeof data === 'string') {
      this.error = data;
      return;
    }
    this.sending = true;
    this.error = '';
    this.dialog.disableClose = true;
    try {
      const result = await this.service.sendTest(this.webhook.webhook_id, data);
      if (result.error) {
        this.error = result.error;
        return;
      }
      this.dialog.close(true);
    } finally {
      this.sending = false;
      this.dialog.disableClose = false;
    }
  }
}
