import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

/** How this person is reached when a chat needs them. */
export interface NotificationSettings {
  push: { configured: boolean; public_key: string; devices: number };
  email: { available: boolean; enabled: boolean };
}

/** Client for Settings:Notifications. */
@Injectable({ providedIn: 'root' })
export class NotificationsService {
  constructor(private request: RequestService) {}

  async get(): Promise<NotificationSettings> {
    return this.request.gateway('Settings:Notifications:get');
  }

  async subscribe(subscription: PushSubscriptionJSON): Promise<NotificationSettings & { error?: string }> {
    return this.request.gateway('Settings:Notifications:subscribe', {
      subscription, user_agent: navigator.userAgent.slice(0, 200),
    });
  }

  async unsubscribe(endpoint: string): Promise<NotificationSettings & { error?: string }> {
    return this.request.gateway('Settings:Notifications:unsubscribe', { endpoint });
  }

  async update(email: boolean): Promise<NotificationSettings & { error?: string }> {
    return this.request.gateway('Settings:Notifications:update', { email });
  }

  async test(): Promise<{ outcome?: any; error?: string }> {
    return this.request.gateway('Settings:Notifications:test');
  }
}
