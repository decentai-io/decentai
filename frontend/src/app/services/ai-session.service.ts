import { Injectable } from '@angular/core';
import { environment } from 'src/environments/environment';
import { RequestService } from './request.service';
import {
  ChatSnapshot, MessagePage,
} from 'src/app/models/chat-protocol';

/**
 * The chat pages' client for the platform's AI protocol
 * (docs/system/chat-session.md).
 *
 * One origin, one session cookie, one gateway. AI-domain endpoints answer
 * in the contracts envelope {version, request_id, status, data, error};
 * `ai()` unwraps it to {data?, error?}. The data-layer domains (secrets,
 * files) answer plain objects and go through the same gateway.
 *
 * The chat WebSocket carries the conversation: AI:Chat:Input outbound,
 * AI:Chat:Event inbound (the runtime door's frames, relayed). Everything
 * durable — chats, messages, the cards, storage — is HTTP
 * (docs/system/chat-session.md).
 */
/** How the trail is narrowed: words, kinds, a window, a page cursor. */
export interface AuditQuery {
  text?: string;
  event_types?: string[];
  since?: string;
  until?: string;
  before?: any;
  limit?: number;
}

export interface AuditPage {
  events: any[];
  next_before: any;
  error?: string;
}

/** One thing the model was shown, or one thing it answered. */
export interface TranscriptEntry {
  index: number;
  /** system: its instructions. user: what arrived — the person's
   *  words, a result, a notice. assistant: the action it chose. */
  role: string;
  content: string;
  /** Whether only the first part of a long entry is here. */
  cut: boolean;
  /** Pictures shown to the model beside it: counted, never copied. */
  images: number;
}

export interface AssistantTranscript {
  entries: TranscriptEntry[];
  /** What was folded away to keep the mind small. */
  summary: string;
  /** The agents the assistant has open. */
  opened: string[];
  error?: string;
}

@Injectable({ providedIn: 'root' })
export class AiSessionService {
  private chatsCache: any[] | null = null;
  private readonly chatsCacheKey = 'decentai.chat-list-cache';

  constructor(private request: RequestService) {}

  /** One AI-domain gateway call with the envelope unwrapped. A refusal
   *  carries the backend's own code beside its sentence, for a caller
   *  that treats one refusal differently from another. */
  async ai(
    endpoint: string,
    data: any = {},
  ): Promise<{ data?: any; error?: string; code?: string }> {
    const res = await this.request.sendRequest(
      { endpoint, data },
      null,
      'app',
      'POST',
    );
    const body = res?.data;
    if (res?.status >= 200 && res?.status < 300 && body?.status !== 'error') {
      return { data: body?.data ?? body };
    }
    const error =
      body?.error?.message ||
      body?.error ||
      body?.messages?.[0]?.description ||
      `Request failed (${res?.status}).`;
    const code = body?.error?.code;
    return { error: String(error), ...(typeof code === 'string' ? { code } : {}) };
  }

  // ── Chats ─────────────────────────────────────────────────────

  async listChats(): Promise<any[]> {
    const res = await this.ai('AI:Chat:List');
    if (res.error) throw new Error(res.error);
    const chats = res.data?.chats || [];
    this.chatsCache = chats;
    try {
      sessionStorage.setItem(this.chatsCacheKey, JSON.stringify(chats));
    } catch {}
    return chats;
  }

  /** Lets navigation render the last conversation list immediately while a
   * fresh copy is fetched in the background. The cache only lives for this
   * browser session and every successful list request replaces it. */
  /** The person is leaving: the list kept for them, in memory and in
   *  the browser's session store, is nobody else's to be shown. */
  forgetChats(): void {
    this.chatsCache = null;
    try {
      sessionStorage.removeItem(this.chatsCacheKey);
    } catch {}
  }

  peekChats(): any[] | null {
    if (this.chatsCache) return [...this.chatsCache];
    try {
      const saved = sessionStorage.getItem(this.chatsCacheKey);
      if (saved) {
        const chats = JSON.parse(saved);
        if (Array.isArray(chats)) {
          this.chatsCache = chats;
          return [...chats];
        }
      }
    } catch {}
    return null;
  }

  /** The browser's IANA zone — what "tomorrow at nine" means to the
   *  person. A chat keeps time in it (reminders, cron cadences). */
  static browserTimezone(): string {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch {
      return '';
    }
  }

  async createChat(
    title?: string,
    config?: any,
  ): Promise<{ data?: any; error?: string }> {
    const timezone = AiSessionService.browserTimezone();
    return this.ai('AI:Chat:Create', {
      ...(title ? { title } : {}),
      config: { ...(timezone ? { timezone } : {}), ...(config || {}) },
    });
  }

  /** Replace the chat's config (send the FULL merged config object). */
  async updateChatConfig(
    chatId: string,
    config: any,
  ): Promise<{ data?: any; error?: string }> {
    return this.ai('AI:Chat:Update', { chat_id: chatId, config });
  }

  async renameChat(chatId: string, title: string) {
    return this.ai('AI:Chat:Update', { chat_id: chatId, title });
  }

  async archiveChat(chatId: string) {
    return this.ai('AI:Chat:Archive', { chat_id: chatId });
  }

  async restoreChat(chatId: string) {
    return this.ai('AI:Chat:Restore', { chat_id: chatId });
  }

  async deleteChat(chatId: string) {
    return this.ai('AI:Chat:Delete', { chat_id: chatId });
  }

  // ── Conversation data ─────────────────────────────────────────

  /** The one door a session goes through: the chat, its contract (the
   *  model, the agents and functions this person may call, their
   *  powers here) and the snapshot to rehydrate from. */
  async openChat(
    chatId: string,
    limit = 50,
  ): Promise<{ data?: ChatSnapshot; error?: string }> {
    const timezone = AiSessionService.browserTimezone();
    return this.ai('AI:Chat:Open', {
      chat_id: chatId, limit, ...(timezone ? { timezone } : {}),
    }) as Promise<{ data?: ChatSnapshot; error?: string }>;
  }

  async listMessagePage(
    chatId: string,
    limit = 50,
    before?: number,
  ): Promise<{ data?: MessagePage; error?: string }> {
    return this.ai('AI:Message:List', {
      chat_id: chatId, limit,
      ...(typeof before === 'number' ? { before } : {}),
    }) as Promise<{ data?: MessagePage; error?: string }>;
  }

  async resolveApproval(
    approvalId: string,
    decision: 'approve' | 'deny',
  ): Promise<{ data?: any; error?: string; code?: string }> {
    return this.ai('AI:Approval:Decide', {
      approval_id: approvalId,
      decision,
    });
  }

  /** An answer to a question — an agent's (call.ask) in words or a
   *  file's ref, the assistant's files question as the chosen refs —
   *  the same door as a decision, recorded before the runtime hears it. */
  async answerQuestion(
    approvalId: string,
    answer: string | string[] | Record<string, string>,
  ): Promise<{ data?: any; error?: string; code?: string }> {
    return this.ai('AI:Approval:Decide', { approval_id: approvalId, answer });
  }

  /** A credential card's typed values go to the vault, never onto the
   *  card: its own door, which closes the card with the row's ref. */
  async saveCredential(
    approvalId: string,
    fields: Record<string, string>,
  ): Promise<{ resource_ref?: string; error?: string }> {
    return this.request.gateway('Secrets:Credential:save', { approval_id: approvalId, fields });
  }

  /** The consent card's yes: this agent may use this login on this site. */
  async allowCredential(approvalId: string): Promise<{ resource_ref?: string; error?: string }> {
    return this.request.gateway('Secrets:Credential:allow', { approval_id: approvalId });
  }

  /** The stop button: recorded through the gateway, carried to the
   *  runtime as the door's own frame, honored by the assistant between
   *  beats. With force, the kill switch — the run, its jobs and its
   *  browser end where they stand and every waiting card in the chat
   *  is expired. */
  async stop(chatId: string, force = false): Promise<{ data?: any; error?: string }> {
    return this.ai('AI:Chat:Stop', force ? { chat_id: chatId, force: true } : { chat_id: chatId });
  }

  /** The platform's installed agents — what config.enabled_agents can
   *  narrow a chat down to. */
  async listAgents(): Promise<any[]> {
    const res = await this.ai('Agents:Agent:List', {});
    return res.data?.agents || [];
  }

  /** The audit trail: what the PLATFORM recorded while the assistant
   *  worked — approvals, credential reads, execution boundaries. It has
   *  always been written; the Activity dialog is where it is finally
   *  read. Never carries a secret value, only that one was used. */
  async listAudit(chatId: string): Promise<any[]> {
    const res = await this.ai('AI:Audit:List', { chat_id: chatId, limit: 500 });
    return res.data?.events || [];
  }

  /** What the assistant of one of the person's own chats was told and
   *  what it decided, in the order the model was shown it. */
  async transcript(chatId: string): Promise<AssistantTranscript> {
    const res = await this.ai('AI:State:Transcript', { chat_id: chatId });
    return {
      entries: res.data?.entries || [],
      summary: res.data?.summary || '',
      opened: res.data?.opened || [],
      error: res.error,
    };
  }

  /** The person's own trail across every chat they own, narrowed and
   *  paged: `before` is the cursor the previous page returned. */
  async searchAudit(query: AuditQuery): Promise<AuditPage> {
    const res = await this.ai('AI:Audit:List', { ...query });
    return {
      events: res.data?.events || [],
      next_before: res.data?.next_before || null,
      error: res.error,
    };
  }

  /** The organization's whole trail — a grant of its own. */
  async searchAuditAll(query: AuditQuery): Promise<AuditPage> {
    const res = await this.ai('AI:Audit:List_all', { ...query });
    return {
      events: res.data?.events || [],
      next_before: res.data?.next_before || null,
      error: res.error,
    };
  }

  /** The most events one AI:Event:List answer may carry — the
   *  backend's own ceiling, above the tail it keeps per chat. */
  static readonly EVENTS_PAGE = 500;

  /** The sequenced event log — how a client that was away replays
   *  exactly the narration it missed. One page, oldest first; a caller
   *  that wants everything asks again past the last sequence it got
   *  until it reaches `latest_seq`. */
  async listEvents(
    chatId: string,
    afterSeq?: number,
  ): Promise<{ events: any[]; latest_seq: number }> {
    const res = await this.ai('AI:Event:List', {
      chat_id: chatId,
      limit: AiSessionService.EVENTS_PAGE,
      ...(typeof afterSeq === 'number' ? { after_seq: afterSeq } : {}),
    });
    return {
      events: res.data?.events || [],
      latest_seq: res.data?.latest_seq || 0,
    };
  }

  /** A stored result (or its dot-path slice) — how table/graph parts get
   *  their verified data. */
  async getStorage(storageRef: string, path?: string): Promise<any> {
    const res = await this.ai('AI:Storage:Get', {
      storage_ref: storageRef,
      ...(path ? { path } : {}),
    });
    if (res.error) return null;
    return path ? res.data?.value : res.data?.storage?.data;
  }

  // ── Chat configuration helpers ────────────────────────────────

  /** The organization's LLM connections — the same secret-shaped rows
   *  (resource_ref + keys) the chat pickers were built against, served
   *  by the settings module. Keys metadata only; never the api key. */
  async listLlmSecrets(): Promise<any[]> {
    const res = await this.request.gateway('Settings:Llm:list');
    return (res as any)?.connections || [];
  }

  // ── Attachments ───────────────────────────────────────────────

  /** Upload a file bound to this chat (dies with the chat). */
  async uploadChatFile(
    chatId: string,
    file: File,
  ): Promise<{ data?: any; error?: string }> {
    const content_base64 = await this.fileToBase64(file);
    return this.ai('AI:Chat:UploadFile', {
      chat_id: chatId,
      filename: file.name,
      content_base64,
    });
  }

  private fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () =>
        resolve((reader.result as string).split(',')[1] || '');
      reader.onerror = () => reject(new Error('Failed to read file'));
      reader.readAsDataURL(file);
    });
  }

  // ── URLs ──────────────────────────────────────────────────────

  /** Direct download URL for a file resource; the session cookie rides
   *  along with the browser's GET. */
  buildDownloadUrl(resourceRef: string): string | null {
    if (!resourceRef) return null;
    return this.httpUrl(`download/${encodeURIComponent(resourceRef)}`);
  }

  /** WebSocket URL for a chat. The handshake carries the session cookie,
   *  so the socket authenticates like any other request. */
  buildChatWsUrl(chatId: string): string {
    const url = new URL(this.httpUrl(`chats/${chatId}`));
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
  }

  private httpUrl(path: string): string {
    return new URL(
      path,
      new URL(environment.apiEndpoint, window.location.origin),
    ).toString();
  }
}
