import { Component, Input } from '@angular/core';

import { CredentialDraft } from './mcp-shared';

/**
 * What a server is reached with, as it is typed: one of three kinds,
 * and the boxes the chosen kind needs. The same fields when a server
 * is added and when its credential is replaced.
 */
@Component({
  selector: 'app-mcp-credential',
  standalone: false,
  template: `
    <app-field label="Credential" help="Kept encrypted and sent only to this server. It is never shown again.">
      <app-segmented name="mcp-credential-kind" label="Kind of credential" [choices]="draft.kinds" [(ngModel)]="draft.kind"></app-segmented>
    </app-field>
    <app-field label="Token" *ngIf="draft.kind === 'token'" [error]="tried ? draft.missing('token') : ''">
      <input class="input" name="mcp-token" type="password" autocomplete="new-password" maxlength="4000" [(ngModel)]="draft.token" />
    </app-field>
    <ng-container *ngIf="draft.kind === 'header'">
      <app-field label="Header" help="The name the server asks for, like X-API-Key." [error]="tried ? draft.missing('header') : ''">
        <input class="input" name="mcp-header" autocomplete="off" maxlength="80" [(ngModel)]="draft.header" />
      </app-field>
      <app-field label="Value" [error]="tried ? draft.missing('value') : ''">
        <input class="input" name="mcp-value" type="password" autocomplete="new-password" maxlength="4000" [(ngModel)]="draft.value" />
      </app-field>
    </ng-container>
  `,
  // The fields sit in the form's own column, one after another.
  styles: [':host { display: contents; }'],
})
export class McpCredentialComponent {
  @Input({ required: true }) draft!: CredentialDraft;
  /** Save was pressed: what is missing is said under its field. */
  @Input() tried = false;
}
