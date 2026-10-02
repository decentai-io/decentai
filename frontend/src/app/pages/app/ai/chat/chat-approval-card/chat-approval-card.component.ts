import { Component, EventEmitter, Input, Output } from '@angular/core';
import * as Prism from 'prismjs';
import 'prismjs/components/prism-python';
import { CodeAsk, CredentialAsk, CredentialField, FileChoice } from 'src/app/models/chat-protocol';

/**
 * Human-in-the-loop approval card.
 *
 * Renders a pending agent action (from the runtime's `approval_requested`
 * event) with structured metadata only — agent id, function, permission
 * level and a truncated inputs preview. The Approve/Deny decision goes back
 * through the backend gateway, a route that is fully independent of the
 * LLM. While that request is in flight the parent changes the local status,
 * which removes both action buttons and makes the submission single-shot.
 *
 * The same card carries a question (call.ask) — answered in words, with a
 * file the person attaches, or, for the assistant's own files question
 * (find_files), with the files they choose in the shared picker: the
 * candidates the assistant found come pre-ticked, and a search over
 * everything they can see finds what it missed.
 *
 * Code an agent wants to run (call.propose) is that card again: what the
 * code is for, where it runs and what it needs, what the assistant made
 * of it, and the code itself, coloured. It is answered allow or deny.
 */
@Component({
  selector: 'app-chat-approval-card',
  standalone: false,
  templateUrl: './chat-approval-card.component.html',
  styleUrls: ['./chat-approval-card.component.css'],
})
export class ChatApprovalCardComponent {
  @Input() approval: any = null;

  @Output() decided = new EventEmitter<{
    approval_id: string;
    decision: 'approve' | 'deny' | 'answer';
    answer?: string;
    /** A file question's answer, before the page has uploaded it. */
    file?: File;
    /** A files question's answer: the refs the person chose, none
     *  included — that is a decline, not an error. */
    files?: string[];
    /** A credential card's answer: typed fields (entry, once), a word
     *  (consent: allow, deny, update) or a chosen row (choose). */
    credential?: { mode: CredentialAsk['mode']; fields?: Record<string, string>; answer?: string };
    /** A code card's answer. */
    code?: 'allow' | 'deny';
  }>();

  showInputs = false;
  /** A code card: whether the code is open. Unset, it is open while
   *  the card waits and closed once it is decided. */
  codeOpen: boolean | null = null;
  /** The code last coloured, so a redraw does not colour it again. */
  private coloured = { source: '', language: '', html: '' };
  /** The person's own words, for a question. */
  reply = '';
  /** A files question: what the person has ticked, as the picker says. */
  chosen: FileChoice[] = [];
  /** A credential card: what the person has typed, by field name. It
   *  leaves this component only through the vault's own door. */
  typed: Record<string, string> = {};

  /** A card waits until the person decides. */
  get isPending(): boolean {
    return !this.approval?.status || this.approval.status === 'pending';
  }

  /** Who is asking, as its author named it. */
  get agentName(): string {
    return String(
      this.approval?.action?.agent_name || this.approval?.agent_name
      || this.agentRef || (this.isFilesQuestion ? 'The assistant' : 'an agent'),
    );
  }

  /** The platform id behind that name. Kept available rather than shown:
   *  it is what the deployment routes by, and it is what somebody asking
   *  "which one exactly?" needs. */
  get agentRef(): string {
    return String(
      this.approval?.action?.agent_id || this.approval?.agent_id || '',
    );
  }

  /** The function as its tool and name, the agent's id left off: the
   *  card already says which agent, and the id is in the tooltip. */
  get functionLabel(): string {
    const raw = String(this.approval?.action?.function || this.approval?.function || '');
    const parts = raw.split('.');
    if (parts.length > 2 && (parts[0] === this.agentRef || parts[0].startsWith('agt_'))) parts.shift();
    return parts.join(' / ') || raw;
  }

  get actionSummary(): string {
    const raw = String(this.approval?.action?.function || this.approval?.function || 'an action');
    const readable = raw.split(/[.:/]/).pop()!.replace(/[_-]+/g, ' ').trim();
    return `The assistant wants to run “${readable || 'an action'}”.`;
  }

  get levelLabel(): string {
    const labels: Record<number, string> = {
      0: 'observe',
      1: 'read / compute',
      2: 'contained change',
      3: 'external / irreversible',
    };
    return labels[this.approval?.level] ?? 'restricted';
  }

  get statusLabel(): string {
    if (this.approval?.status === 'approving') return 'Approving…';
    if (this.approval?.status === 'denying') return 'Denying…';
    if (this.approval?.status === 'answering') return 'Sending…';
    const status = String(this.approval?.status || '');
    return status ? status.charAt(0).toUpperCase() + status.slice(1) : '';
  }

  get inputsPreview(): string {
    const inputs = this.approval?.action?.inputs_preview;
    if (inputs == null) return '';
    if (typeof inputs === 'string') return inputs;
    try {
      return JSON.stringify(inputs, null, 2);
    } catch {
      return String(inputs);
    }
  }

  /** An agent asking rather than an action waiting (call.ask). */
  get isQuestion(): boolean {
    return this.approval?.kind === 'question';
  }

  /** The assistant asking which files the person meant (find_files). */
  get isFilesQuestion(): boolean {
    return this.isQuestion && this.approval?.expects === 'files';
  }

  /** An agent asking for a login as it works (call.credential). */
  get isCredential(): boolean {
    return this.isQuestion && this.approval?.expects === 'credential' && !!this.approval?.credential;
  }

  get credential(): CredentialAsk {
    return this.approval?.credential || { mode: 'entry', host: '' };
  }

  /** An agent putting code before the person (call.propose). */
  get isCode(): boolean {
    return this.isQuestion && this.approval?.expects === 'code' && !!this.approval?.code;
  }

  get code(): CodeAsk {
    return this.approval?.code || { language: 'python', code: '', purpose: '' };
  }

  get codeTitle(): string {
    return `${this.agentName} wants to run code${this.code.where ? ` on ${this.code.where}` : ''}`;
  }

  get languageName(): string {
    return this.code.language === 'javascript' ? 'JavaScript' : 'Python';
  }

  /** What the code needs, in the person's words; a kind it needs
   *  nothing of is left out. */
  get codeNeeds(): Array<{ icon: string; label: string; items: string[]; words?: boolean }> {
    const c = this.code;
    return [
      { icon: 'package', label: 'Installs', items: c.packages || [] },
      { icon: 'globe', label: 'Reaches', items: c.hosts || [] },
      // A credential is named in the person's words, not in code's letters.
      { icon: 'key-round', label: 'Uses', items: c.credentials || [], words: true },
      { icon: 'paperclip', label: 'Reads', items: c.files || [] },
    ].filter((need) => need.items.length);
  }

  /** The code touches nothing outside where it runs. */
  get codeNeedsNothing(): boolean {
    return !this.codeNeeds.length;
  }

  get reviewVerdict(): 'agrees' | 'differs' | 'unread' {
    return this.code.review?.verdict || 'unread';
  }

  get reviewTitle(): string {
    switch (this.reviewVerdict) {
      case 'agrees': return 'The assistant read the code: it does what it says';
      case 'differs': return 'The assistant read the code: it does more than it says';
      default: return 'The assistant could not read this code';
    }
  }

  get reviewNote(): string {
    return this.code.review?.note
      || (this.reviewVerdict === 'unread' ? 'Read it yourself before allowing it, or decline.' : '');
  }

  /** Lines of code, a closing line break not counted as one. */
  get codeLines(): number {
    const code = this.code.code.replace(/\s+$/, '');
    return code ? code.split('\n').length : 0;
  }

  get codeShown(): boolean {
    return this.codeOpen ?? this.isPending;
  }

  toggleCode(): void {
    this.codeOpen = !this.codeShown;
  }

  /** The code as coloured markup. Prism escapes what it colours, and
   *  the template's own sanitizer reads the result again. */
  get codeHtml(): string {
    const { code, language } = this.code;
    if (this.coloured.source !== code || this.coloured.language !== language) {
      const grammar = Prism.languages[language];
      this.coloured = {
        source: code, language,
        html: grammar ? Prism.highlight(code, grammar, language) : Prism.util.encode(code) as string,
      };
    }
    return this.coloured.html;
  }

  decideCode(answer: 'allow' | 'deny'): void {
    if (!this.isPending || !this.approval?.approval_id) return;
    this.decided.emit({
      approval_id: this.approval.approval_id, decision: 'answer', code: answer,
    });
  }

  get credentialFields(): CredentialField[] {
    return this.credential.fields || [];
  }

  /** What the card is for, in the person's words. */
  get credentialTitle(): string {
    const c = this.credential;
    const where = c.site && c.site !== c.host ? ` on ${c.site}` : '';
    switch (c.mode) {
      case 'consent': return `${this.agentName} wants to use your ${c.host} login${c.account ? ` (${c.account})` : ''}${where}`;
      case 'choose': return `Which ${c.host} login should ${this.agentName} use${where}?`;
      case 'once': return `${c.host} asks for a code`;
      default: return c.existing
        ? `Update your ${c.host} login${c.account ? ` (${c.account})` : ''}`
        : `Sign in to ${c.host}${where}`;
    }
  }

  get credentialNote(): string {
    switch (this.credential.mode) {
      case 'consent': return 'Allowing lets this agent sign in with the saved login on this site, now and later. Nothing is shown to it beyond what it needs to sign in.';
      case 'choose': return 'The one you pick is used for this site until you say otherwise.';
      case 'once': return 'Asked each time and never stored.';
      default: return this.credential.existing
        ? 'Kept with your saved login. Values are encrypted and never shown to the assistant.'
        : 'Saved as one of your secrets, encrypted, and never shown to the assistant. You can share it or delete it on the Secrets page.';
    }
  }

  get credentialReady(): boolean {
    return this.credentialFields.every((f) => f.required === false || !!(this.typed[f.name] || '').trim());
  }

  private emitCredential(credential: { mode: CredentialAsk['mode']; fields?: Record<string, string>; answer?: string }): void {
    if (!this.isPending || !this.approval?.approval_id) return;
    this.decided.emit({
      approval_id: this.approval.approval_id, decision: 'answer', credential,
    });
  }

  /** Entry and once: the typed fields, in the vault's direction. */
  submitCredential(): void {
    if (!this.credentialReady) return;
    const fields: Record<string, string> = {};
    for (const field of this.credentialFields) fields[field.name] = this.typed[field.name] || '';
    const mode = this.credential.mode === 'once' ? 'once' : 'entry';
    this.emitCredential({ mode, fields });
    this.typed = {};
  }

  consent(answer: 'allow' | 'deny' | 'update'): void {
    this.emitCredential({ mode: 'consent', answer });
  }

  chooseLogin(instance: { resource_ref: string; name?: string; account?: string }): void {
    this.emitCredential({ mode: 'choose', answer: instance.resource_ref });
  }

  /** What the assistant found, in the order it ranked them. */
  get candidates(): FileChoice[] {
    return Array.isArray(this.approval?.candidates) ? this.approval.candidates : [];
  }

  /** Ticked before the person touches anything: the assistant's guess. */
  get candidateRefs(): string[] {
    return this.candidates.map((candidate) => candidate.resource_ref);
  }

  get selectedNames(): string[] {
    return this.chosen.map((file) => file.filename || 'a file');
  }

  /** The chosen files are the answer; none of them is an answer too. */
  useSelected(): void {
    if (!this.isPending || !this.approval?.approval_id) return;
    this.decided.emit({
      approval_id: this.approval.approval_id, decision: 'answer',
      files: this.chosen.map((file) => file.resource_ref),
    });
  }

  useNone(): void {
    if (!this.isPending || !this.approval?.approval_id) return;
    this.decided.emit({
      approval_id: this.approval.approval_id, decision: 'answer', files: [],
    });
  }

  /** A file question: the chosen file goes up with the decision, and the
   *  page uploads it to the chat before answering with its ref. */
  fileChosen(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input?.files?.[0];
    input.value = '';
    if (!this.isPending || !file || !this.approval?.approval_id) return;
    this.decided.emit({
      approval_id: this.approval.approval_id, decision: 'answer', file,
    });
  }

  /** A choice, or the person's own words — once, while it waits. */
  answerWith(text: string): void {
    const answer = String(text || '').trim();
    if (!this.isPending || !answer || !this.approval?.approval_id) return;
    this.decided.emit({
      approval_id: this.approval.approval_id, decision: 'answer', answer,
    });
  }

  toggleInputs(): void {
    this.showInputs = !this.showInputs;
  }

  approve(): void {
    this.emitDecision('approve');
  }

  deny(): void {
    this.emitDecision('deny');
  }

  private emitDecision(decision: 'approve' | 'deny'): void {
    if (!this.isPending || !this.approval?.approval_id) return;
    this.decided.emit({ approval_id: this.approval.approval_id, decision });
  }
}
