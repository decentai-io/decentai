import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

/** The organization's own rules for the assistant: added to the
 *  instructions every chat's assistant is given, and the code
 *  reviewer's. Guidance to the model; the Safety rows and the approval
 *  cards are enforced whatever it says. */
export interface GuardrailSettings {
  text: string;
  /** Who last wrote it, and when; empty for rules never written. */
  updated_by: string;
  updated_at: string | null;
  /** The most characters it may hold. */
  limit: number;
}

/** Client for Settings:Guardrail — its own grants, apart from Safety's:
 *  reading the rules and changing them are each given to whom an
 *  administrator chooses. */
@Injectable({ providedIn: 'root' })
export class SettingsGuardrailService {
  constructor(private request: RequestService) {}

  async get(): Promise<GuardrailSettings | null> {
    const r = await this.request.gateway('Settings:Guardrail:get');
    return r && typeof r.text === 'string' ? r : null;
  }

  async update(text: string): Promise<Partial<GuardrailSettings> & { error?: string }> {
    return this.request.gateway('Settings:Guardrail:update', { text });
  }
}
