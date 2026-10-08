// The chat protocol as the page speaks it (docs/system/chat-session.md):
// durable records over HTTP, and the runtime door's frames — relayed by
// the backend as AI:Chat:Event — over the socket.
// The vocabulary is contracts/chat.py's, written here by hand;
// tests/test_chat_contract.py fails when the event names or the part
// types of the two differ. The fields of each are not compared.
export const CHAT_PROTOCOL_VERSION = 2 as const;

/** Who produced an event or a part: the assistant, an installed agent
 *  (with the call and job it spoke on), a helper, the scheduler, or the
 *  platform. */
export interface Source {
  kind: 'assistant' | 'agent' | 'helper';
  agent?: string; agent_name?: string; function?: string;
  call_id?: string; job_id?: string; child?: string;
}

export interface MarkdownPart { type: 'markdown'; content: string; text?: string; source?: Source; }
export interface FilePart {
  type: 'file'; resource_ref: string; filename?: string;
  file_size?: number; file_type?: string; text?: string; source?: Source;
}
/** A step's status comes from what the runtime actually did, never
 *  from what the model said about it. */
export type PlanStatus = 'pending' | 'active' | 'done' | 'blocked';

/** One item of the work in hand. `evidence` is what the runtime's trace
 *  proved for it — storage refs of successful calls, ids of finished
 *  jobs; `verified` is whether there is any. An item done with none is
 *  the model's word alone, and the page says so. */
export interface PlanStep {
  id?: string;
  text: string;
  status: PlanStatus;
  evidence?: string[];
  blocker?: string;
  depends_on?: string[];
  verified?: boolean;
}

export interface StoredPart {
  type: 'table' | 'graph'; storage_ref: string; text?: string; path?: string;
  columns?: string[]; source?: Source; data?: unknown;
}
/** A verified write: kept on the message, never rendered. */
export interface SuccessPart { type: 'success'; text: string; storage_ref?: string; source?: Source; }
export type MessagePart = MarkdownPart | FilePart | StoredPart | SuccessPart;

export interface ChatMessage {
  message_id: string; chat_id: string; actor: 'user' | 'ai' | 'system' | 'parent';
  parts: MessagePart[]; sequence: number; thread?: string | null;
  client_message_id?: string | null; created_at?: string;
}

/** One file a files question proposes (the assistant's find_files):
 *  the ref the answer names it by, and what the card shows. */
export interface FileChoice {
  resource_ref: string; filename?: string; file_type?: string;
  file_size?: number; source?: string; created_at?: string;
}

/** One field a credential card asks for; `remember: false` is asked
 *  every time and stored nowhere. */
export interface CredentialField {
  name: string; label?: string; type?: 'text' | 'secret';
  required?: boolean; remember?: boolean;
}
export interface CredentialInstance { resource_ref: string; name?: string; account?: string; }
/** An agent asking for a login as it works (call.credential): which
 *  card, for which host and site, in whose name. */
export interface CredentialAsk {
  mode: 'entry' | 'consent' | 'choose' | 'once';
  host: string; site?: string; account?: string; agent_ref?: string;
  resource_id?: string; definition_ref?: string; resource_ref?: string;
  existing?: boolean; fields?: CredentialField[]; instances?: CredentialInstance[];
}

/** What the assistant made of the code before the person saw it. */
export interface CodeReview { verdict: 'agrees' | 'differs' | 'unread'; note?: string; }
/** Code an agent wants to run (call.propose): the code whole, what it
 *  is for, where it runs and what it needs. Answered allow or deny. */
export interface CodeAsk {
  language: 'python' | 'javascript'; code: string; purpose: string; where?: string;
  packages?: string[]; hosts?: string[]; credentials?: string[]; files?: string[];
  review?: CodeReview;
}

/** A card, as the backend records it: what the assistant asked, and
 *  what the person decided. A child's card names its thread. */
export interface ChatApproval {
  approval_id: string; chat_id: string; thread?: string | null;
  status: 'pending' | 'approved' | 'denied' | 'answered' | 'expired';
  /** An approval waits on a yes; a question (call.ask) on words. */
  kind?: 'approval' | 'question';
  answer?: string | string[];
  /** A file question's answer is a ref, a files question's the refs;
   *  this is the name(s) to show. */
  answer_label?: string;
  request: {
    agent_id?: string; agent_name?: string; function: string; inputs?: Record<string, unknown>;
    question?: string; choices?: string[];
    /** What the agent wants back: words, an attached file, files
     *  chosen from what the person can see, or a yes to its code. */
    expects?: '' | 'text' | 'file' | 'files' | 'credential' | 'code';
    /** A files question: what the assistant found, and what it looked for. */
    candidates?: FileChoice[]; query?: string;
    /** A credential question: which card, for which host, in whose name. */
    credential?: CredentialAsk;
    /** A code question: the code, what it is for and what it needs. */
    code?: CodeAsk;
    permission_level?: number | null; chat_level?: number | null;
    job_id?: string | null;
  };
  requested_at?: string; resolved_at?: string | null;
}

/** The card as the page shows it. */
export interface ApprovalCard {
  approval_id: string;
  status: 'pending' | 'approving' | 'denying' | 'approved' | 'denied'
    | 'answering' | 'answered' | 'expired';
  level?: number | null;
  kind?: 'approval' | 'question';
  question?: string;
  choices?: string[];
  /** 'file': the card offers an attach button and answers with the ref.
   *  'files': the card lists the candidates, searches the rest, and
   *  answers with the chosen refs. 'code': the card shows the code and
   *  what it needs, and answers allow or deny. */
  expects?: '' | 'text' | 'file' | 'files' | 'credential' | 'code';
  candidates?: FileChoice[];
  query?: string;
  credential?: CredentialAsk;
  code?: CodeAsk;
  thread?: string | null;
  action: { agent_id: string; agent_name?: string; function: string; inputs_preview: Record<string, unknown> };
}

export interface MessagePage {
  messages: ChatMessage[]; total: number; next_before: number | null;
  has_more: boolean;
}

/** What the page reads of AI:Chat:Open's answer: the chat, the
 *  contract, the snapshot. */
export interface ChatSnapshot {
  protocol_version: typeof CHAT_PROTOCOL_VERSION;
  chat: Record<string, any>; messages: MessagePage;
  approvals: ChatApproval[];
  latest_event_seq: number;
  contract?: SessionContract;
}

/** What the page reads of the session contract. `powers` is what this
 *  person may do in the chat: `send` false means the composer is not
 *  theirs to use. */
export interface SessionContract {
  agents: Record<string, { name: string; qualified_id?: string; version?: string; functions: string[]; watch?: string }>;
  powers?: Record<string, boolean>;
  llm?: Record<string, unknown> | null;
  llm_missing?: string;
}

/** A user message and what the assistant said after it, until the next.
 *  No user message when the assistant spoke first — a scheduled run, a
 *  background job reporting in. */
export interface ChatTurn {
  id: string;
  userMessage: ChatMessage | null;
  assistantMessages: ChatMessage[];
}

export interface ChatInputCommand {
  protocol_version: typeof CHAT_PROTOCOL_VERSION;
  client_message_id: string; text: string; attachments: FilePart[];
}

/** One job the assistant runs in the background, from the door's hello. */
export interface ChatJob {
  job_id: string; kind?: string; function?: string; status: string;
  approval_id?: string; child?: string;
}

/** What an `activity` line is about (contracts/chat.py, ActivityKind). */
export type ActivityKind =
  | 'call_started' | 'call_finished' | 'agent_progress'
  | 'job_started' | 'job_finished' | 'helper_spawned' | 'helper_said';

interface EventBase { event: string; seq?: number; chat_id?: string; child?: string; source?: Source; }
export type ChatEvent =
  | (EventBase & { event: 'hello'; protocol_version?: number; working?: boolean; sleeping?: { until: number; why?: string } | null; active_jobs?: ChatJob[]; pending_approvals?: Array<{ approval_id: string; job_id?: string; function?: string; child?: string; agent?: string; agent_name?: string; kind?: 'approval' | 'question'; question?: string; choices?: string[] }>; plan?: PlanStep[] })
  | (EventBase & { event: 'working' })
  | (EventBase & { event: 'idle' })
  | (EventBase & { event: 'error'; detail?: string })
  | (EventBase & { event: 'runtime_unavailable' })
  | (EventBase & { event: 'runtime_disconnected' })
  /** The person stopped everything of theirs; nothing runs until they resume. */
  | (EventBase & { event: 'work_stopped' })
  | (EventBase & { event: 'invalid_input'; detail?: string })
  /** Retired — only a replay of older events still carries it. */
  | (EventBase & { event: 'progress'; description?: string })
  /** One line of the work, saying whose; a call's lines share its call_id. */
  | (EventBase & { event: 'activity'; kind: ActivityKind; text: string; status?: string; duration_ms?: number })
  /** An agent asking the person mid-call (call.ask); then how it ended. */
  | (EventBase & { event: 'question_asked'; approval_id: string; job_id?: string; function?: string; agent?: string; agent_name?: string; question: string; choices?: string[]; expects?: '' | 'text' | 'file' | 'files' | 'credential' | 'code'; candidates?: FileChoice[]; query?: string; credential?: CredentialAsk; code?: CodeAsk })
  | (EventBase & { event: 'question_closed'; approval_id: string; status: 'answered' | 'expired' })
  /** The door narrating a first open: an agent the runtime did not
   *  hold is being fetched and built before the hello. Socket-only. */
  | (EventBase & { event: 'agent_status'; agent?: string; name?: string; phase?: 'pulling' | 'installing' | 'ready' | 'failed'; text?: string })
  | (EventBase & { event: 'plan_updated'; steps?: PlanStep[] })
  | (EventBase & { event: 'memory_saved'; text?: string })
  | (EventBase & { event: 'schedule_set'; schedule?: { note?: string; function?: string; every_seconds?: number | null } })
  | (EventBase & { event: 'schedule_removed'; schedule_id?: string })
  /** The assistant paused until a time (epoch seconds); null when a stop ended the pause. */
  | (EventBase & { event: 'sleeping'; until?: number | null; why?: string })
  /** A screen an agent shows (call.screen): pictures, socket-only, never recorded; then that it stopped. */
  | (EventBase & { event: 'screen_frame'; call_id: string; image_base64: string; mime?: 'image/jpeg' | 'image/png'; width: number; height: number; frame?: number; taken?: boolean; tabs?: { index: number; title?: string; address?: string; active?: boolean }[] })
  | (EventBase & { event: 'screen_closed'; call_id: string })
  | (EventBase & { event: 'screen_unavailable'; detail?: string })
  | (EventBase & { event: 'chat_titled'; title: string })
  | (EventBase & { event: 'stopped'; jobs?: number; children?: number; cards?: number; detail?: string })
  | (EventBase & { event: 'approval_requested'; approval_id?: string; job_id?: string; function?: string; permission_level?: number; inputs?: Record<string, unknown>; agent?: string; agent_name?: string })
  | (EventBase & { event: 'message_created'; message?: ChatMessage });

export function parseChatEvent(raw: string): ChatEvent | null {
  try {
    const envelope: unknown = JSON.parse(raw);
    if (!envelope || typeof envelope !== 'object') return null;
    const record = envelope as { endpoint?: unknown; data?: unknown };
    if (record.endpoint !== 'AI:Chat:Event' || !record.data
        || typeof record.data !== 'object') return null;
    const data = record.data as { event?: unknown };
    return typeof data.event === 'string' ? record.data as ChatEvent : null;
  } catch {
    return null;
  }
}

/** A backend card, or a door's hello/approval_requested frame, as the
 *  page's card. `labels` (ref → name, from the contract) names the agent
 *  on an older card that was recorded without its name. */
export function toApprovalCard(source: any, labels: Record<string, string> = {}): ApprovalCard {
  const request = source?.request || source || {};
  const functionName = String(request.function || source?.function || '');
  const agentId = String(request.agent_id || source?.agent || functionName.split('.')[0] || '');
  const status = ['approved', 'denied', 'answered', 'expired'].includes(source?.status)
    ? source.status : 'pending';
  return {
    approval_id: String(source?.approval_id || ''),
    status,
    level: request.permission_level ?? source?.permission_level ?? null,
    kind: (source?.kind || request.kind) === 'question' ? 'question' : 'approval',
    question: request.question || source?.question || undefined,
    choices: request.choices || source?.choices || undefined,
    expects: request.expects || source?.expects || undefined,
    candidates: request.candidates || source?.candidates || undefined,
    query: request.query || source?.query || undefined,
    credential: request.credential || source?.credential || undefined,
    code: request.code || source?.code || undefined,
    thread: source?.thread ?? source?.child ?? null,
    action: {
      agent_id: agentId,
      agent_name: request.agent_name || source?.agent_name || labels[agentId] || '',
      function: functionName,
      inputs_preview: request.inputs || source?.inputs || {},
    },
  };
}
