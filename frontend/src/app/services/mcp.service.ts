import { Injectable } from '@angular/core';

import { RequestService } from './request.service';

/** One tool a server offers, as the person keeps it. */
export interface McpTool {
  /** What the platform calls it: `<server>.tools.<id>`. */
  id: string;
  /** What the server calls it. */
  name: string;
  description: string;
  inputs: Record<string, unknown>;
  /** Whether the person's chats may call it. */
  enabled: boolean;
  /** What it costs to call, on the scale every function is priced on. */
  level: 0 | 1 | 2 | 3;
  /** Switched off because the server offered it after the person
   *  looked ('new'), or describes it differently now ('changed'). */
  changed?: 'new' | 'changed';
}

export interface McpServer {
  resource_ref: string;
  keys: {
    name: string;
    url: string;
    host: string;
    enabled: boolean;
    /** What the server calls itself. */
    server: string;
    tool_count: number;
    tools_on: number;
    resources: boolean;
  };
  /** On get, create, update and refresh; a list carries keys only. */
  values?: {
    tools: McpTool[];
    resources: boolean;
    /** The credential itself never leaves the platform. */
    has_credential: boolean;
  };
  unreadable?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

/** A credential as a person gives it: a bearer token, or a header a
 *  server names itself. Empty for a server that wants none. */
export type McpCredential =
  | { token: string }
  | { header: string; value: string }
  | Record<string, never>;

type Answer = { resource?: McpServer; error?: string };

/**
 * Client for Mcp:Server — remote MCP servers a person adds for their
 * own chats. The platform reads each server's tools when it is added;
 * the person switches tools on and off and says what each costs.
 */
@Injectable({ providedIn: 'root' })
export class McpService {
  constructor(private request: RequestService) {}

  /** The person's servers, and whether the deployment has switched
   *  MCP off (Settings → Safety): then none of them can be called. */
  async list(): Promise<{ servers: McpServer[]; blocked: boolean }> {
    const r = await this.request.gateway('Mcp:Server:List');
    return {
      servers: Array.isArray(r?.resources) ? r.resources : [],
      blocked: r?.blocked === true,
    };
  }

  async get(ref: string): Promise<McpServer | null> {
    const r = await this.request.gateway('Mcp:Server:Get', { resource_ref: ref });
    return r?.resource ?? null;
  }

  async create(fields: { name: string; url: string; credential: McpCredential }): Promise<Answer> {
    return this.request.gateway('Mcp:Server:Create', fields);
  }

  async update(
    ref: string,
    fields: Partial<{
      name: string;
      url: string;
      enabled: boolean;
      credential: McpCredential;
      tools: Array<{ id: string; enabled?: boolean; level?: number }>;
    }>,
  ): Promise<Answer> {
    return this.request.gateway('Mcp:Server:Update', { resource_ref: ref, ...fields });
  }

  /** Read again what the server offers. */
  async refresh(ref: string): Promise<Answer> {
    return this.request.gateway('Mcp:Server:Refresh', { resource_ref: ref });
  }

  async remove(ref: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('Mcp:Server:Delete', { resource_ref: ref });
  }
}
