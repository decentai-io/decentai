import { Injectable } from '@angular/core';

import { AiSessionService } from './ai-session.service';

/** One running agent, as the platform counts it now. */
export interface AgentUsage {
  agent: string;
  name: string;
  /** Bytes its processes hold that are theirs alone. */
  memory: number;
  /** Its share of the processor, in processors: 0.5 is half of one. */
  cpu: number;
  processes: number;
  /** Bytes it keeps in its own folders, measured every half minute;
   *  null until the first measuring, and where nothing confines it. */
  disk?: number | null;
}

/** One part of the platform, as it read of its own container — or,
 *  for the database, as its server says. A missing number is one that
 *  part could not read. */
export interface ServiceUsage {
  id: string;
  name: string;
  memory?: number | null;
  memory_limit?: number | null;
  /** Processors used since the last asking: 0.5 is half of one. */
  cpu?: number | null;
  cpus?: number | null;
  /** What the database keeps on disk for this platform, in bytes. */
  disk?: number | null;
  connections?: number | null;
}

export interface UsageNow {
  /** False where nobody adds it up: agents with no container of their
   *  own. */
  counted: boolean;
  agents: AgentUsage[];
  /** What the agents are given together; a missing one is no limit. */
  limits: { memory?: number | null; cpus?: number | null };
  /** What the agents' container holds now, in bytes. */
  memory: number | null;
  /** What agents are held to here: a user of its own, its files, its
   *  connections. */
  confined: { user?: boolean; files?: boolean; network?: boolean };
  /** The platform's own parts; empty where the deployment is more
   *  than this organization's. */
  services: ServiceUsage[];
  error?: string;
}

/** One thing that was seen of an agent, as the runtime wrote it down. */
export interface MonitorEvent {
  at: number;
  kind: string;
  agent?: string;
  name?: string;
  [said: string]: any;
}

/** How a thing went, as a person narrows to it: "trouble" is any of
 *  refused, unreached and failed. */
export type MonitorOutcome = 'ok' | 'trouble' | 'refused' | 'unreached' | 'failed';

/** What the filters leave, added up over the latest events read. Each
 *  count of a kind, an outcome, an agent or a host is what choosing it
 *  would show, the other filters kept. */
export interface MonitorTally {
  matching: number;
  /** False where only the latest were read: back to `reached`. */
  complete: boolean;
  reached: number | null;
  kinds: Record<string, number>;
  outcomes: Partial<Record<'ok' | 'refused' | 'unreached' | 'failed', number>>;
  agents: { agent: string; name: string; count: number }[];
  hosts: { host: string; count: number }[];
  /** Bytes the connections among them carried. */
  sent: number;
  received: number;
}

export interface MonitorQuery {
  /** What the runtime reads: the connections view reads only those. */
  kinds?: string[];
  kind?: string;
  agent?: string;
  outcome?: MonitorOutcome | '';
  host?: string;
  /** Words looked for in an event's words, whatever their case. */
  text?: string;
  before?: number | null;
  limit?: number;
  /** The days asked for, in seconds: nothing from before `since`,
   *  nothing from `until` on. */
  since?: number | null;
  until?: number | null;
  /** Add up what the filters leave, with this page. */
  tally?: boolean;
}

export interface MonitorPage {
  events: MonitorEvent[];
  next_before: number | null;
  tally?: MonitorTally;
  error?: string;
}

export interface AgentFiles {
  files: { path: string; bytes: number; modified?: number }[];
  count: number;
  bytes: number;
  /** Why there is nothing to list, when there is not. */
  note?: string;
  error?: string;
}

/** One step of the calls over time: how many ended each way. */
export interface ActivityStep {
  at: string;
  ok: number;
  failed: number;
  refused: number;
}

/** What an organization's agents did in a window, added up from the
 *  audit trail (Agents:Monitor:Summary). */
export interface ActivitySummary {
  window: SummaryWindow;
  unit: 'hour' | 'day';
  since: string;
  until: string;
  calls: { ok: number; failed: number; refused: number; total: number; avg_ms: number };
  series: ActivityStep[];
  agents: { agent: string; name: string; calls: number; failed: number; avg_ms: number }[];
  hosts: { host: string; connections: number; calls: number }[];
  approvals: { asked: number; allowed: number; denied: number; waiting: number };
  model_calls: number;
  secret_uses: number;
  people: number;
  problems: { at: string; chat_id?: string | null; function?: string; agent: string; status: string; error: string }[];
  error?: string;
}

export type SummaryWindow = '24h' | '7d' | '30d';

/** Client for Agents:Monitor — what agents use and did. Read-only. */
@Injectable({ providedIn: 'root' })
export class MonitoringService {
  constructor(private session: AiSessionService) {}

  async usage(): Promise<UsageNow> {
    const res = await this.session.ai('Agents:Monitor:Usage');
    return {
      counted: !!res.data?.counted,
      agents: res.data?.agents || [],
      limits: res.data?.limits || {},
      memory: res.data?.memory ?? null,
      confined: res.data?.confined || {},
      services: res.data?.services || [],
      error: res.error,
    };
  }

  async events(query: MonitorQuery): Promise<MonitorPage> {
    const data: any = { limit: query.limit || 100 };
    if (query.kinds?.length) data.kinds = query.kinds;
    for (const key of ['kind', 'agent', 'outcome', 'host', 'text'] as const) {
      if (query[key]) data[key] = query[key];
    }
    for (const key of ['before', 'since', 'until'] as const) {
      if (query[key] != null) data[key] = query[key];
    }
    if (query.tally) data.tally = true;
    const res = await this.session.ai('Agents:Monitor:Events', data);
    return {
      events: res.data?.events || [],
      next_before: res.data?.next_before ?? null,
      tally: res.data?.tally,
      error: res.error,
    };
  }

  async summary(window: SummaryWindow): Promise<ActivitySummary | null> {
    const res = await this.session.ai('Agents:Monitor:Summary', { window });
    if (res.error || !res.data) return null;
    return res.data as ActivitySummary;
  }

  async files(agent: string): Promise<AgentFiles> {
    const res = await this.session.ai('Agents:Monitor:Files', { agent });
    return {
      files: res.data?.files || [],
      count: res.data?.count || 0,
      bytes: res.data?.bytes || 0,
      note: res.data?.note,
      error: res.error,
    };
  }
}
