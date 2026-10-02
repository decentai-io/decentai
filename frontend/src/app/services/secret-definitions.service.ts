import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface DefinitionField {
  name: string;
  label: string;
  type: 'string' | 'number' | 'boolean' | 'select' | 'secret';
  storage: 'keys' | 'values';
  required: boolean;
  options: string[];
  help?: string;
}

/** A credential obtained by consent rather than typed in: which
 *  provider, which scopes. The organization's registration with that
 *  provider is looked up by the id at connect time. */
export interface DefinitionOauth {
  provider: string;
  scopes: string[];
  authorize_url: string;
  token_url: string;
}

/** What the platform writes into a connected account's credential;
 *  shown as a status, never as a form. */
export const OAUTH_FIELD_NAMES = ['account', 'access_token', 'refresh_token', 'expires_at', 'status'];

export interface SecretDefinition {
  definition_ref: string;
  definition_id: string;
  version: number;
  label: string;
  description: string;
  fields: DefinitionField[];
  oauth?: DefinitionOauth | null;
  instance_count?: number;
  created_at?: string | null;
  /** Installed agents whose approved manifests reference this family. */
  agent_uses?: string[];
}

/**
 * Read-only client for Secrets:Definition. Definitions are not authored
 * any more — every one is derived from an installed agent's manifest —
 * so this lists and resolves shapes; it never writes one.
 */
@Injectable({ providedIn: 'root' })
export class SecretDefinitionsService {
  constructor(private request: RequestService) {}

  async list(): Promise<SecretDefinition[]> {
    const r = await this.request.gateway('Secrets:Definition:list');
    return Array.isArray(r?.definitions) ? r.definitions : [];
  }

  async get(ref: {
    definition_id?: string;
    definition_ref?: string;
  }): Promise<{ definition?: SecretDefinition; error?: string }> {
    return this.request.gateway('Secrets:Definition:get', ref);
  }

}
