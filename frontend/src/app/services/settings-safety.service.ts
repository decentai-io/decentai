import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

/** What agents may do without asking — the organization's setting.
 *  Every row starts where the platform stood before the setting
 *  existed, so nothing is loosened by an update. */
export interface SafetySettings {
  /** Whether people may add MCP servers and their chats may call them. */
  mcp: 'allowed' | 'blocked';
  /** Names no agent may open; a name covers every host under it. */
  blocked_sites: string[];
  /** A script in a page: a card every time, or once for a site in a chat. */
  scripts: 'always' | 'once_per_site';
  /** A program: a card every time; not again for a correction that
   *  needs nothing new; or only when it reaches a site or uses a
   *  credential. */
  programs: 'always' | 'corrections' | 'quiet';
  /** Any package the card names, or only the listed ones. */
  packages: 'any' | 'listed';
  allowed_packages: string[];
}

/** Client for Settings:Safety. */
@Injectable({ providedIn: 'root' })
export class SettingsSafetyService {
  constructor(private request: RequestService) {}

  async get(): Promise<SafetySettings | null> {
    const r = await this.request.gateway('Settings:Safety:get');
    return r?.safety ?? null;
  }

  async update(changes: Partial<SafetySettings>): Promise<{ safety?: SafetySettings; error?: string }> {
    return this.request.gateway('Settings:Safety:update', { ...changes });
  }
}
