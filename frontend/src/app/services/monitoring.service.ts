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

export interface MonitorPage {
  events: MonitorEvent[];
  next_before: number | null;
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

  async events(query: {
    kinds?: string[]; agent?: string; before?: number | null; limit?: number;
  }): Promise<MonitorPage> {
    const data: any = { limit: query.limit || 100 };
    if (query.kinds?.length) data.kinds = query.kinds;
    if (query.agent) data.agent = query.agent;
    if (query.before != null) data.before = query.before;
    const res = await this.session.ai('Agents:Monitor:Events', data);
    return {
      events: res.data?.events || [],
      next_before: res.data?.next_before ?? null,
      error: res.error,
    };
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
