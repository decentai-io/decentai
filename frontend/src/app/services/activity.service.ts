import { Injectable } from '@angular/core';

import { AiSessionService } from './ai-session.service';

/** One fire of a schedule, as the runtime remembered it on the row. */
export interface ScheduleRun {
  at: number;
  status: 'woke' | 'success' | 'error' | string;
  woke: boolean;
  /** How long the fire took, when the runtime measured it. */
  took_ms?: number;
  error?: string;
  result?: string;
}

/** A schedule row across chats: what, when, and what it did. */
export interface ActivitySchedule {
  schedule_id: string;
  chat_id: string;
  chat_title: string;
  mode: 'wake' | 'invoke' | string;
  function: string;
  inputs: Record<string, unknown>;
  wake_field: string;
  note: string;
  every_seconds: number | null;
  cron: string;
  timezone: string;
  next_run_at: number;
  enabled: boolean;
  last_run_at: number | null;
  runs: ScheduleRun[];
  /** The next fires, the next one first — a cron on its calendar, a
   *  cadence by its period; empty when paused. */
  upcoming?: number[];
}

/** What the Schedules page reads: the rows, and whether the person has
 *  stopped everything — then the rows stay and none of them fires. */
export interface Activity {
  schedules: ActivitySchedule[];
  stopped: boolean;
  /** Why the read failed, when it did: an empty list is then not
   *  "nothing scheduled". */
  error?: string;
}

/** A schedule as a person writes it on the page: what, and one way of
 *  saying when — exactly what the assistant's schedule action takes. */
export interface ScheduleDraft {
  chat_id: string;
  note?: string;
  function?: string;
  inputs?: Record<string, unknown>;
  wake_field?: string;
  at?: string;
  delay_seconds?: number;
  every_seconds?: number;
  cron?: string;
}

/** A function an installed agent's manifest declared fit to run
 *  unattended — the only kind a schedule may run. */
export interface SchedulableFunction {
  value: string;
  label: string;
  agent: string;
  inputs: Record<string, unknown>;
  outputs: string[];
}

/**
 * Client for AI:Activity — what every chat of the person keeps on the
 * clock — and for the person's own hand on it (AI:Schedule).
 */
@Injectable({ providedIn: 'root' })
export class ActivityService {
  constructor(private ai: AiSessionService) {}

  async list(): Promise<Activity> {
    const res = await this.ai.ai('AI:Activity:List', {});
    const data = (res.data || {}) as Partial<Activity>;
    return {
      schedules: data.schedules || [],
      stopped: data.stopped === true,
      error: res.error,
    };
  }

  // ── The person's own hand on the clock ──────────────────────────────

  async create(draft: ScheduleDraft): Promise<{ data?: any; error?: string }> {
    return this.ai.ai('AI:Schedule:Create', draft);
  }

  async setEnabled(chatId: string, scheduleId: string, enabled: boolean) {
    return this.ai.ai('AI:Schedule:Update', {
      chat_id: chatId, schedule_id: scheduleId, enabled,
    });
  }

  async remove(chatId: string, scheduleId: string) {
    return this.ai.ai('AI:Schedule:Delete', { chat_id: chatId, schedule_id: scheduleId });
  }

  /** The functions the person may put on their clock: the ones they
   *  have been given that are declared fit to run unattended. */
  async schedulableFunctions(): Promise<SchedulableFunction[]> {
    const res = await this.ai.ai('AI:Schedule:Functions', {});
    const offered: any[] = res.data?.functions || [];
    return offered.map((fn) => ({
      value: fn.function,
      label: `${fn.agent} · ${fn.name} (${String(fn.function).split('.').slice(1).join('.')})`,
      agent: fn.agent_id,
      inputs: fn.inputs || {},
      outputs: fn.outputs || [],
    }));
  }
}
