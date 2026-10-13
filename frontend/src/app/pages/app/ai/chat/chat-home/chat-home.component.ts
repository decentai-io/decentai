import { Component, ElementRef, HostListener, OnInit, ViewChild } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { MatDialog } from '@angular/material/dialog';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { AttentionService } from 'src/app/services/attention.service';
import { ProfileService } from 'src/app/services/profile.service';
import { AlertComponent } from 'src/app/components/alert/alert.component';
import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { ConfirmService } from 'src/app/services/confirm.service';
import { ChatModel } from '../chat-model-picker/chat-model-picker.component';

/** The chats page: a new conversation starts at the composer, and every
 *  earlier one is listed under it — searched, archived, restored, or
 *  deleted without leaving the page. */
@Component({
  selector: 'app-chat-home',
  templateUrl: './chat-home.component.html',
  styleUrls: ['./chat-home.component.css'],
  standalone: false,
})
export class ChatHomeComponent implements OnInit {
  @ViewChild('search') private search?: ElementRef<HTMLInputElement>;

  chats: any[] = [];
  filteredChats: any[] = [];
  chatGroups: Array<{ label: string; chats: any[] }> = [];
  searchQuery = '';
  /** Archived chats are still yours — they are simply out of the way.
   *  Deleting one is the irreversible act, and it stays a separate
   *  button so nobody reaches for it when they meant "put this away". */
  showArchived = false;
  isLoading = false;
  /** Whether the list has arrived once — cached or fetched — so the page
   *  knows a skeleton from "nothing here". */
  chatsLoaded = false;
  loadError = false;
  /** A prompt another page handed over, put in the composer for the
   *  person to read and send. */
  draft = '';

  constructor(
    private router: Router,
    private route: ActivatedRoute,
    private aiSession: AiSessionService,
    private profileService: ProfileService,
    public dialog: MatDialog,
    private attention: AttentionService,
    private confirm: ConfirmService,
  ) {}

  async ngOnInit() {
    const cached = this.aiSession.peekChats();
    if (cached) this.setChats(cached);
    // Refreshed in the background: a new conversation never waits for
    // the list.
    void this.loadChats();
    void this.resolveModel();
    // An agent's page hands over a prompt to try. It is put in the
    // composer and nothing more: an address is something anybody can
    // write, and a link must not be able to make a chat and speak in
    // it as the person who followed it. They read it and press send.
    const prompt = String(this.route.snapshot.queryParamMap.get('prompt') || '').trim();
    if (prompt) {
      this.draft = prompt;
      await this.router.navigate([], { queryParams: {}, replaceUrl: true });
    }
  }

  async loadChats() {
    this.loadError = false;
    try {
      // The list endpoint returns chats sorted by updated_at already;
      // the shape is flat: {chat_id, title, status, updated_at, …}.
      const chats = await this.aiSession.listChats();
      this.setChats(chats);
      this.attention.absorb(chats);
    } catch {
      this.loadError = true;
    }
  }

  private setChats(chats: any[]) {
    this.chats = chats.map((chat: any) => ({
      ...chat,
      title: chat.title || 'Untitled chat',
      displayDate: relativeDate(chat.updated_at),
      fullDate: fullDate(chat.updated_at),
      dateGroup: this.dateGroup(chat.updated_at),
      messageLabel: `${chat.message_sequence || 0} ${chat.message_sequence === 1 ? 'message' : 'messages'}`,
    }));
    this.chatsLoaded = true;
    this.applyFilters();
  }

  /** Stand at the shelf of chats in use, or at the archive. */
  showShelf(archived: boolean) {
    if (archived === this.showArchived) return;
    this.showArchived = archived;
    this.searchQuery = '';
    this.applyFilters();
  }

  /** One place decides what the list shows: the archive shelf you are
   *  standing at, then the search within it, grouped by when. */
  applyFilters() {
    const wanted = this.showArchived ? 'archived' : 'active';
    const query = this.searchQuery.trim().toLowerCase();
    this.filteredChats = this.chats.filter((chat) =>
      (chat.status || 'active') === wanted
      && (!query || chat.title.toLowerCase().includes(query)));

    const groups = new Map<string, any[]>();
    for (const chat of this.filteredChats) {
      groups.set(chat.dateGroup, [...(groups.get(chat.dateGroup) || []), chat]);
    }
    this.chatGroups = [...groups.entries()].map(([label, chats]) => ({ label, chats }));
  }

  /** Enter in the search opens the best match — type, Enter, you are
   *  there. */
  openFirstMatch(): void {
    const [first] = this.filteredChats;
    if (first && this.searchQuery.trim()) this.openChat(first.chat_id);
  }

  get archivedCount(): number {
    return this.chats.filter((chat) => chat.status === 'archived').length;
  }

  get activeCount(): number {
    return this.chats.filter((chat) => (chat.status || 'active') === 'active').length;
  }

  /** Which of the four groups a chat is listed under. */
  private dateGroup(dateStr: string): string {
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return 'Earlier';
    const today = new Date();
    const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    const days = Math.floor((startToday - startDate) / 86400000);
    if (days <= 0) return 'Today';
    if (days === 1) return 'Yesterday';
    return days < 7 ? 'Previous 7 days' : 'Earlier';
  }

  openChat(chat_id: string, initialMessage: any = null) {
    this.router.navigate([`ai/chats/${chat_id}`], {
      state: initialMessage ? { initialMessage } : {},
    });
  }

  async onPrompt(message: any) {
    this.isLoading = true;
    try {
      const query = message.query || 'Start a new chat';
      const llm = await this.llmForNewChat();
      if (!llm) return;

      // First words become the title; the detail page sends the query.
      const res = await this.aiSession.createChat(query.substring(0, 80), {
        llm,
      });

      if (res.data?.chat?.chat_id) {
        this.openChat(res.data.chat.chat_id, {
          query,
          uploads: message.uploads || [],
        });
      } else {
        this.alert({
          title: 'Chat',
          type: 'error',
          messages: [{ description: res.error || 'Failed to create chat.' }],
        });
      }
    } finally {
      this.isLoading = false;
    }
  }

  /** The model the next chat will think with, named under the
   *  composer and changed there: what this person last picked, else
   *  the organization's default, else the only provider there is.
   *  Null while there is no provider at all. */
  llm: ChatModel | null = null;
  /** What was last saved as the person's pick, to save again only
   *  when it changed. */
  private preferred = '';

  private async resolveModel(): Promise<void> {
    const [connections, profile] = await Promise.all([
      this.aiSession.listLlmSecrets(),
      this.profileService.get(),
    ]);
    const chat = profile?.preferences?.chat ?? {};
    const mine = connections.find(
      (connection: any) => connection.resource_ref === chat.llm_secret_ref);
    const selected = mine
      ?? connections.find((connection: any) => connection.is_default)
      ?? connections[0];
    // The person's own pick carries its model and effort; a default
    // starts with the model its connection starts with.
    this.llm = selected ? this.llmFromSecret(
      selected, mine ? chat.llm_model || '' : '',
      mine ? chat.llm_reasoning_effort || '' : '') : null;
    this.preferred = mine && this.llm ? JSON.stringify(this.llm) : '';
  }

  onModelChosen(llm: ChatModel): void {
    this.llm = llm;
  }

  /** Resolved before creating the chat, so the first message can never
   *  fall into a chat with no model. */
  private async llmForNewChat(): Promise<ChatModel | null> {
    if (!this.llm) await this.resolveModel();
    const llm = this.llm;
    if (!llm) {
      this.alert({
        title: 'Add a model provider',
        type: 'error',
        messages: [{
          description:
            'No model provider is set up yet. Add one under Settings, Model providers, and paste its key.',
        }],
      });
      return null;
    }

    if (JSON.stringify(llm) !== this.preferred) {
      const saved = await this.profileService.saveDefaultLlm(
        llm.secret_ref, llm.model, llm.reasoning_effort || '');
      if (saved.error) {
        this.alert({
          title: 'Default model not saved',
          type: 'error',
          messages: [{ description: saved.error }],
        });
        return null;
      }
      this.preferred = JSON.stringify(llm);
    }
    return llm;
  }

  private llmFromSecret(secret: any, model = '', effort = ''): ChatModel {
    const keys = secret.keys || {};
    return {
      provider: keys.provider,
      model: model || keys.model,
      secret_ref: secret.resource_ref,
      ...(keys.endpoint ? { endpoint: keys.endpoint } : {}),
      ...(effort ? { reasoning_effort: effort } : {}),
    };
  }

  alert(message: any) {
    this.dialog.open(AlertComponent, { width: 'auto', data: message });
  }

  /** Archiving needs no confirmation — it is reversible, and asking
   *  about a reversible thing teaches people to click through the
   *  dialogs that matter. */
  async archive(chat: any) {
    const res = await this.aiSession.archiveChat(chat.chat_id);
    if (res.error) {
      this.alert({ title: 'Archive failed', type: 'error', messages: [{ description: res.error }] });
      return;
    }
    await this.loadChats();
  }

  async restore(chat: any) {
    const res = await this.aiSession.restoreChat(chat.chat_id);
    if (res.error) {
      this.alert({ title: 'Restore failed', type: 'error', messages: [{ description: res.error }] });
      return;
    }
    await this.loadChats();
  }

  /** Deleting is the one act here that cannot be taken back, so it is
   *  asked; once done, the row is simply gone. */
  async askDelete(chat: any) {
    const sure = await this.confirm.ask({
      title: `Delete “${chat.title}”?`,
      body: 'Its messages and the files uploaded to it are deleted for good. To put it out of the way instead, archive it.',
      confirm: 'Delete',
      danger: true,
    });
    if (!sure) return;
    const res = await this.aiSession.deleteChat(chat.chat_id);
    if (res.error) {
      this.alert({
        title: 'Delete failed',
        type: 'error',
        messages: [{ description: `“${chat.title}” could not be deleted. ${res.error}` }],
      });
      return;
    }
    await this.loadChats();
  }

  // ── Keys ────────────────────────────────────────────────────────────

  /** "/" anywhere on the page, outside a box being typed in, goes to
   *  the search. */
  @HostListener('document:keydown', ['$event'])
  onKey(event: KeyboardEvent): void {
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
    const typing = event.target instanceof Element
      && event.target.closest('input, textarea, [contenteditable="true"]');
    if (typing) return;
    event.preventDefault();
    this.search?.nativeElement.focus();
  }

  /** The arrows move through the rows, from one conversation's link to
   *  the next. */
  moveThroughRows(event: KeyboardEvent): void {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const list = event.currentTarget as HTMLElement;
    const rows = Array.from(list.querySelectorAll<HTMLElement>('.chat-open'));
    const at = rows.indexOf(document.activeElement as HTMLElement);
    if (at < 0) return;
    event.preventDefault();
    rows[at + (event.key === 'ArrowDown' ? 1 : -1)]?.focus();
  }
}
