import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { SecretDefinition } from 'src/app/services/secret-definitions.service';
import { Secret, SecretUser } from 'src/app/services/secrets.service';
import {
  Consent, ShownField, agentLabel, consentsOf, definitionOf, isOauth, isOutdated, isSiteLogin, lentTo,
  needsReconnect, providerName, secretTitle, shownFields,
} from './secret-shared';

/**
 * One secret, opened: what it is, its fields (the plain ones as text,
 * the encrypted ones said to be set and never shown), which agents use
 * it and how each came to, who can see it, and when it was made.
 *
 * Nothing here changes anything itself: the page owns the doors, and
 * this says which one was asked for.
 */
@Component({
  selector: 'app-secret-view',
  standalone: false,
  templateUrl: './secret-view.component.html',
  styleUrl: './secret-view.component.css',
})
export class SecretViewComponent implements OnChanges {
  @Input({ required: true }) secret!: Secret;
  @Input() definitions: SecretDefinition[] = [];
  /** Who can see it, in the words the list uses. */
  @Input() sharing = '';
  /** The person may change this secret. */
  @Input() mine = false;
  @Input() canUpdate = false;
  @Input() canDelete = false;
  /** The secret an action is on its way for. */
  @Input() busy = false;

  @Output() closed = new EventEmitter<void>();
  @Output() edit = new EventEmitter<void>();
  @Output() lend = new EventEmitter<void>();
  @Output() takeBack = new EventEmitter<SecretUser>();
  @Output() revokeConsent = new EventEmitter<Consent>();
  @Output() reconnect = new EventEmitter<void>();
  @Output() share = new EventEmitter<void>();
  @Output() handOver = new EventEmitter<void>();
  @Output() remove = new EventEmitter<void>();

  title = '';
  kind = '';
  fields: ShownField[] = [];
  lent: SecretUser[] = [];
  consents: Consent[] = [];
  login = false;
  oauth = false;
  outdated = false;
  reconnecting = false;
  provider = '';
  scopes: string[] = [];

  ngOnChanges(): void {
    const definition = definitionOf(this.secret, this.definitions);
    this.title = secretTitle(this.secret);
    this.login = isSiteLogin(this.secret);
    this.kind = this.login ? 'A saved login' : definition?.label || 'Secret';
    this.fields = shownFields(this.secret, this.definitions);
    this.lent = lentTo(this.secret);
    this.consents = consentsOf(this.secret);
    this.oauth = isOauth(this.secret, this.definitions);
    this.outdated = isOutdated(this.secret, this.definitions);
    this.reconnecting = needsReconnect(this.secret, this.definitions);
    this.provider = providerName(definition);
    this.scopes = definition?.oauth?.scopes || [];
  }

  /** The agent it was saved for, by name. */
  get home(): string {
    return agentLabel(this.secret.home_agent?.name || '');
  }

  get account(): string {
    return String(this.secret.keys?.['account'] || '');
  }

  get created(): string {
    return fullDate(this.secret.created_at || '');
  }

  get updated(): string {
    const changed = this.secret.updated_at || '';
    return changed && changed !== this.secret.created_at ? relativeDate(changed).replace(/^J/, 'j') : '';
  }

  get updatedInFull(): string {
    return fullDate(this.secret.updated_at || '');
  }
}
