import { NgClass, NgFor, NgIf } from '@angular/common';
import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';

import { EmptyComponent } from 'src/app/components/ui/empty.component';
import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { WebhooksService } from 'src/app/services/webhooks.service';
import { eventName, outcomeWord, stateWord } from './webhooks-shared';

/** Whose deliveries: a webhook's events in, or a notification's sends out. */
export interface DeliveriesDialogData {
  kind: 'in' | 'out';
  id: string;
  name: string;
}

/** One line of the list, in words, whichever way it went. */
interface DeliveryLine {
  id: string;
  word: string;
  tone: string;
  what: string;
  at: string;
  atInFull: string;
  why: string;
}

/**
 * The latest deliveries of a webhook in (what arrived, and whether its
 * chat heard it or the door refused it, and why) or of a notification
 * out (what was sent, how many tries, what the receiver answered).
 */
@Component({
  selector: 'app-deliveries-dialog',
  standalone: true,
  imports: [NgIf, NgFor, NgClass, MatDialogModule, MatTooltipModule, EmptyComponent],
  styleUrl: './webhooks.component.css',
  template: `
    <h2 mat-dialog-title>Deliveries of {{ data.name }}</h2>
    <mat-dialog-content>
      <p class="dialog-note">The latest first.</p>
      <div *ngIf="loading" aria-busy="true" aria-label="Loading deliveries">
        <span class="sk sk-field"></span>
      </div>
      <app-empty *ngIf="!loading && problem" icon="triangle-alert" title="The deliveries could not be read" [line]="problem">
        <button class="btn" type="button" (click)="load()">Try again</button>
      </app-empty>
      <app-empty *ngIf="!loading && !problem && !lines.length" [icon]="data.kind === 'in' ? 'inbox' : 'send'"
                 [title]="data.kind === 'in' ? 'Nothing has arrived yet' : 'Nothing has been sent yet'"
                 [line]="data.kind === 'in' ? 'Events show here as they arrive, refused ones too.' : 'Notifications show here as they are sent.'"></app-empty>
      <ul class="hook-deliveries" *ngIf="!loading && lines.length">
        <li class="hook-delivery" *ngFor="let line of lines">
          <span class="hook-delivery-head">
            <span class="chip" [ngClass]="line.tone">{{ line.word }}</span>
            <span>{{ line.what }}</span>
            <span [matTooltip]="line.atInFull">{{ line.at }}</span>
          </span>
          <span class="hook-delivery-why" *ngIf="line.why">{{ line.why }}</span>
        </li>
      </ul>
    </mat-dialog-content>
    <mat-dialog-actions>
      <button class="btn" type="button" [disabled]="loading" (click)="load()">Refresh</button>
      <button class="btn primary" type="button" mat-dialog-close>Close</button>
    </mat-dialog-actions>
  `,
})
export class DeliveriesDialogComponent implements OnInit {
  loading = true;
  problem = '';
  lines: DeliveryLine[] = [];

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: DeliveriesDialogData,
    private service: WebhooksService,
  ) {}

  ngOnInit(): Promise<void> {
    return this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.problem = '';
    try {
      this.lines = this.data.kind === 'in' ? await this.received() : await this.sent();
    } catch {
      this.problem = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  private async received(): Promise<DeliveryLine[]> {
    const page = await this.service.deliveries(this.data.id);
    if (page.error) this.problem = page.error;
    return page.deliveries.map((one) => {
      const said = outcomeWord(one.outcome);
      const status = one.status ? `answered ${one.status}` : '';
      const key = one.idempotency_key ? `key ${one.idempotency_key}` : '';
      return {
        id: one.delivery_id, word: said.word, tone: said.tone,
        what: [status, key].filter(Boolean).join(' · '),
        at: relativeDate(one.received_at), atInFull: fullDate(one.received_at),
        why: one.reason || '',
      };
    });
  }

  private async sent(): Promise<DeliveryLine[]> {
    const page = await this.service.sends(this.data.id);
    if (page.error) this.problem = page.error;
    return page.deliveries.map((one) => {
      const said = stateWord(one.state);
      const tries = one.attempts === 1 ? '1 try' : `${one.attempts || 0} tries`;
      const answered = one.last_status ? `, answered ${one.last_status}` : '';
      return {
        id: one.send_id, word: said.word, tone: said.tone,
        what: `${eventName(one.event)} · ${tries}${answered}`,
        at: relativeDate(one.created_at), atInFull: fullDate(one.created_at),
        why: one.last_error || '',
      };
    });
  }
}
