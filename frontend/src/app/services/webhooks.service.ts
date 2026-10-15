import { Injectable } from '@angular/core';

import { ChatModel } from '../pages/app/ai/chat/chat-model-picker/chat-model-picker.component';
import { ResourceOwner } from '../pages/app/share-editor';
import { AiSessionService } from './ai-session.service';

/** What a field of an incoming event may be. */
export type WebhookFieldType = 'text' | 'number' | 'boolean' | 'date' | 'list' | 'object';

/** One field a caller posts: checked on every event. */
export interface WebhookField {
  name: string;
  type: WebhookFieldType;
  required: boolean;
  description: string;
}

/** How the webhook's chat thinks, read from the chat's own config. */
export interface WebhookChat {
  trust_level?: number | null;
  llm?: ChatModel | null;
  enabled_agents?: string[] | null;
  /** False once the chat was deleted. */
  exists: boolean;
}

/** A webhook in, never with its secret: the owner's, with its chat's
 *  settings and the secret's first characters; or one shared with the
 *  reader, who sees what it is and what it did, and nothing more. */
export interface Webhook {
  webhook_id: string;
  name: string;
  prompt: string;
  fields: WebhookField[];
  require_signature: boolean;
  enabled: boolean;
  chat_id: string;
  owner_id: string;
  /** Who else sees it and what it did: groups and people. */
  viewers: ResourceOwner;
  role: 'owner' | 'viewer';
  /** The first characters of the secret, enough to tell them apart.
   *  The owner's only. */
  shown?: string;
  created_at?: string | null;
  updated_at?: string | null;
  last_event_at?: string | null;
  /** '/hooks/<id>', on this deployment's own address. */
  url_path: string;
  /** The owner's only. */
  chat?: WebhookChat;
}

/** What creating and changing a webhook sends. */
export interface WebhookDraft {
  name?: string;
  prompt?: string;
  fields?: WebhookField[];
  require_signature?: boolean;
  enabled?: boolean;
  trust_level?: number;
  llm?: ChatModel;
}

export type WebhookOutcome = 'accepted' | 'delivered' | 'not delivered' | 'refused';

/** What the chat came to with an event; a refused one never reached it. */
export type WebhookResult = 'answered' | 'waiting' | 'working' | 'not delivered' | 'refused';

/** A card the chat opened after an event, and how it stands. */
export interface WebhookCard {
  function: string;
  kind: string;
  status: string;
}

/** One event that arrived, or was refused at the door, beside what
 *  its chat did between it and the next. */
export interface WebhookExecution {
  delivery_id: string;
  received_at?: string | null;
  outcome: WebhookOutcome;
  /** The HTTP status the caller was answered. */
  status?: number | null;
  reason?: string;
  /** What was posted; accepted events only. */
  data?: Record<string, unknown> | null;
  source: 'caller' | 'test';
  result: WebhookResult;
  reply?: string;
  answered_at?: string | null;
  seconds?: number | null;
  cards?: WebhookCard[];
}

/** What a notification out is sent as. */
export type OutboundFormat = 'json' | 'slack' | 'teams';

/** One of the person's notifications out. */
export interface Subscription {
  subscription_id: string;
  name: string;
  url: string;
  format: OutboundFormat;
  events: string[];
  include_reply: boolean;
  enabled: boolean;
  /** Stopped by the platform after too many failures in a row. */
  paused: boolean;
  /** Failures in a row. */
  failures: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SubscriptionDraft {
  name?: string;
  url?: string;
  format?: OutboundFormat;
  events?: string[];
  include_reply?: boolean;
  enabled?: boolean;
}

export interface SubscriptionPage {
  subscriptions: Subscription[];
  events: string[];
  formats: OutboundFormat[];
  limit: number;
  error?: string;
}

export type SendState = 'pending' | 'sending' | 'delivered' | 'failed' | 'dropped';

/** One notification sent, or on its way. */
export interface OutboundDelivery {
  send_id: string;
  event: string;
  created_at?: string | null;
  state: SendState;
  attempts: number;
  last_status?: number | null;
  last_error?: string;
}

/** What a test notification met. */
export interface OutboundTest {
  status: number | null;
  error: string;
  delivered: boolean;
}

/**
 * Client for Settings:Webhook and Settings:Outbound — a person's own
 * webhooks in, each heard in a standing chat of theirs (and those
 * shared with them, to watch), and their notifications out to
 * addresses they choose. A secret is in the open only in the answer
 * that made it.
 */
@Injectable({ providedIn: 'root' })
export class WebhooksService {
  constructor(private ai: AiSessionService) {}

  // ── In ──────────────────────────────────────────────────────────────

  async list(): Promise<{ webhooks: Webhook[]; shared: Webhook[]; limit: number; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:List', {});
    const data = res.data as any;
    return {
      webhooks: data?.webhooks || [], shared: data?.shared || [],
      limit: Number(data?.limit) || 0, error: res.error,
    };
  }

  async get(webhookId: string): Promise<{ webhook?: Webhook; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Get', { webhook_id: webhookId });
    if (res.error) return { error: res.error };
    return { webhook: res.data?.webhook };
  }

  /** Newest first. */
  async executions(webhookId: string, limit = 50): Promise<{ executions: WebhookExecution[]; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Executions', { webhook_id: webhookId, limit });
    return { executions: res.data?.executions || [], error: res.error };
  }

  /** Who else may see it and what it did. The owner's to set. */
  async share(webhookId: string, viewers: ResourceOwner): Promise<{ webhook?: Webhook; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Share', { webhook_id: webhookId, viewers });
    if (res.error) return { error: res.error };
    return { webhook: res.data?.webhook };
  }

  /** The webhook and its chat become the colleague's; it runs as them. */
  async transfer(webhookId: string, userId: string): Promise<{ error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Transfer', { webhook_id: webhookId, user_id: userId });
    return res.error ? { error: res.error } : {};
  }

  /** An event from the page, down the same road as a caller's. */
  async sendTest(webhookId: string, data: Record<string, unknown>): Promise<{ delivery_id?: string; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Test', { webhook_id: webhookId, data });
    if (res.error) return { error: res.error };
    return { delivery_id: res.data?.delivery_id };
  }

  async create(draft: WebhookDraft): Promise<{ webhook?: Webhook; secret?: string; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Create', draft);
    if (res.error) return { error: res.error };
    return { webhook: res.data?.webhook, secret: res.data?.secret };
  }

  async update(webhookId: string, changes: WebhookDraft): Promise<{ webhook?: Webhook; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Update', { webhook_id: webhookId, ...changes });
    if (res.error) return { error: res.error };
    return { webhook: res.data?.webhook };
  }

  async rotate(webhookId: string): Promise<{ webhook?: Webhook; secret?: string; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Rotate', { webhook_id: webhookId });
    if (res.error) return { error: res.error };
    return { webhook: res.data?.webhook, secret: res.data?.secret };
  }

  async remove(webhookId: string): Promise<{ deleted?: boolean; chat_id?: string; error?: string }> {
    const res = await this.ai.ai('Settings:Webhook:Delete', { webhook_id: webhookId });
    if (res.error) return { error: res.error };
    return { deleted: true, chat_id: res.data?.chat_id };
  }

  // ── Out ─────────────────────────────────────────────────────────────

  async subscriptions(): Promise<SubscriptionPage> {
    const res = await this.ai.ai('Settings:Outbound:List', {});
    const data = res.data as any;
    return {
      subscriptions: data?.subscriptions || [],
      events: data?.events || [],
      formats: data?.formats || [],
      limit: Number(data?.limit) || 0,
      error: res.error,
    };
  }

  async subscribe(draft: SubscriptionDraft): Promise<{ subscription?: Subscription; secret?: string; error?: string }> {
    const res = await this.ai.ai('Settings:Outbound:Create', draft);
    if (res.error) return { error: res.error };
    return { subscription: res.data?.subscription, secret: res.data?.secret };
  }

  async change(subscriptionId: string, changes: SubscriptionDraft): Promise<{ subscription?: Subscription; error?: string }> {
    const res = await this.ai.ai('Settings:Outbound:Update', { subscription_id: subscriptionId, ...changes });
    if (res.error) return { error: res.error };
    return { subscription: res.data?.subscription };
  }

  async unsubscribe(subscriptionId: string): Promise<{ error?: string }> {
    const res = await this.ai.ai('Settings:Outbound:Delete', { subscription_id: subscriptionId });
    return res.error ? { error: res.error } : {};
  }

  async test(subscriptionId: string): Promise<OutboundTest & { failed?: string }> {
    const res = await this.ai.ai('Settings:Outbound:Test', { subscription_id: subscriptionId });
    if (res.error) return { status: null, error: res.error, delivered: false, failed: res.error };
    const data = res.data as any;
    return { status: data?.status ?? null, error: data?.error || '', delivered: !!data?.delivered };
  }

  async sends(subscriptionId: string): Promise<{ deliveries: OutboundDelivery[]; error?: string }> {
    const res = await this.ai.ai('Settings:Outbound:Deliveries', { subscription_id: subscriptionId });
    return { deliveries: res.data?.deliveries || [], error: res.error };
  }
}
