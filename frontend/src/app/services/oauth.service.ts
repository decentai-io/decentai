import { Injectable } from '@angular/core';

import { environment } from 'src/environments/environment';
import { RequestService } from './request.service';

/** What a connection attempt comes back with: the credential it made
 *  or refreshed, or the sentence to show. `redirect_uri` rides along
 *  when the organization has no app registered yet, so the page can
 *  tell an administrator exactly what to paste into the provider. */
export interface ConnectResult {
  resource_ref?: string;
  error?: string;
  redirect_uri?: string;
}

/**
 * Connecting an account instead of typing a credential.
 *
 * The backend builds the provider's consent URL (Secrets:Oauth:Start);
 * this opens it in a popup and waits for the callback page to say how
 * it went. The popup is opened on the click itself, before the round
 * trip, because a window opened later is a window a browser blocks.
 */
@Injectable({ providedIn: 'root' })
export class OauthConnectService {
  constructor(private request: RequestService) {}

  /** Start for a definition (a new credential) or for a credential
   *  (a reconnect), and resolve when the popup reports back. */
  async connect(payload: {
    definition_ref?: string;
    definition_id?: string;
    resource_ref?: string;
    name?: string;
    owner?: Record<string, any>;
  }): Promise<ConnectResult> {
    const popup = window.open('', 'decentai-oauth', 'width=540,height=700,popup=yes');
    const started = await this.request.gateway('Secrets:Oauth:start', { ...payload });
    if (started?.error || !started?.url) {
      popup?.close();
      return {
        error: started?.error || 'Could not start the connection.',
        redirect_uri: started?.redirect_uri,
      };
    }
    if (!popup) {
      return { error: 'Your browser blocked the sign-in window. Allow popups for this site and try again.' };
    }
    popup.location.href = started.url;
    return this.awaitPopup(popup);
  }

  private awaitPopup(popup: Window): Promise<ConnectResult> {
    const trusted = new Set([
      window.location.origin,
      new URL(environment.apiEndpoint, window.location.href).origin,
    ]);
    return new Promise((resolve) => {
      let timer = 0;
      const done = (result: ConnectResult) => {
        window.removeEventListener('message', onMessage);
        window.clearInterval(timer);
        resolve(result);
      };
      const onMessage = (event: MessageEvent) => {
        const data = event.data;
        if (!data || data.type !== 'decentai:oauth' || !trusted.has(event.origin)) return;
        // Only the window this attempt opened may answer for it: another
        // sign-in under way elsewhere reports to its own.
        if (event.source !== popup) return;
        done(data.ok
          ? { resource_ref: String(data.resource_ref || '') }
          : { error: String(data.error || 'Not connected.') });
      };
      window.addEventListener('message', onMessage);
      timer = window.setInterval(() => {
        if (popup.closed) done({ error: 'The sign-in window was closed before it finished.' });
      }, 500);
    });
  }
}
