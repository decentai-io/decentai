import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

export interface RecordOwner {
  groups: string[];
  users: string[];
}

/** One field of a record shape, as an installed agent declared it. */
export interface RecordField {
  name: string;
  label: string;
  type: 'string' | 'number' | 'boolean' | 'select' | 'object' | 'secret';
  storage: 'keys' | 'values';
  required: boolean;
  options: string[];
}

/** One record type an installed agent declares — what New record offers. */
export interface RecordShape {
  agent_ref: string;
  agent_name: string;
  resource_id: string;
  label: string;
  description: string;
  fields: RecordField[];
  /** What a person may do to this kind besides read and delete, as the
   *  agent's manifest says — `create`, `update`. Absent is neither. */
  user_access?: string[];
}

export interface AgentRecord {
  resource_ref: string;
  /** `agent__resource` — which agent keeps this kind of record. */
  resource_id: string;
  owner: RecordOwner;
  /** Find-by metadata the agent set: titles, tags, status. */
  keys: Record<string, any>;
  /** The payload. Stored encrypted, returned decrypted to its owner. */
  values: Record<string, any>;
  created_by: string;
  created_at?: string | null;
  updated_at?: string | null;
}

/**
 * Client for Data:Record — the records agents keep on a user's behalf.
 * Unlike secrets, whose values never leave the backend, a record's values
 * come back decrypted to whoever owns it, so a user can read what was
 * stored about them.
 */
@Injectable({ providedIn: 'root' })
export class RecordsService {
  constructor(private request: RequestService) {}

  async list(resourceId?: string): Promise<AgentRecord[]> {
    const result = await this.request.gateway(
      'Data:Record:list',
      resourceId ? { resource_id: resourceId } : {},
    );
    return Array.isArray(result?.resources) ? result.resources : [];
  }

  /** Hand a record to another member; its sharing stays. */
  async transfer(ref: string, userId: string): Promise<{ resource?: AgentRecord; error?: string }> {
    return this.request.gateway('Data:Record:transfer', { resource_ref: ref, user_id: userId });
  }

  async remove(ref: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('Data:Record:delete', { resource_ref: ref });
  }

  /** The record types installed agents declare, fields and all. */
  async shapes(): Promise<RecordShape[]> {
    const result = await this.request.gateway('Data:Record:shapes', {});
    return Array.isArray(result?.shapes) ? result.shapes : [];
  }

  /** Create a record in a declared shape. The backend validates the
   *  fields against the manifest; the form here is a convenience. */
  async create(
    resourceId: string,
    fields: Record<string, any>,
  ): Promise<{ resource?: AgentRecord; error?: string }> {
    return this.request.gateway('Data:Record:create', {
      resource_id: resourceId, fields,
    });
  }

  /** Edit a record's fields — merged over what is stored, revalidated. */
  async update(
    ref: string,
    fields: Record<string, any>,
  ): Promise<{ resource?: AgentRecord; error?: string }> {
    return this.request.gateway('Data:Record:update', {
      resource_ref: ref, fields,
    });
  }

  /** Change who can see a record. Only the owner map travels — keys and
   *  values are the agent's to write, and an update that omitted them
   *  would be the page quietly deciding what a record contains. */
  async setOwner(
    ref: string,
    owner: RecordOwner,
  ): Promise<{ resource?: AgentRecord; error?: string }> {
    return this.request.gateway('Data:Record:update', {
      resource_ref: ref,
      owner,
    });
  }
}
