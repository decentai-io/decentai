import { Component, OnInit } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { MatDialog } from '@angular/material/dialog';
import { firstValueFrom } from 'rxjs';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { AttentionService } from 'src/app/services/attention.service';
import { ProfileService } from 'src/app/services/profile.service';
import { AlertComponent } from 'src/app/components/alert/alert.component';
import { ChatLlmDialogComponent } from '../chat-llm-dialog/chat-llm-dialog.component';

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
  ) {}

  async ngOnInit() {
    const cached = this.aiSession.peekChats();
    if (cached) this.setChats(cached);
    // Refreshed in the background: a new conversation never waits for
    // the list.
    void this.loadChats();
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
      displayDate: this.formatDate(chat.updated_at),
      dateGroup: this.dateGroup(chat.updated_at),
      messageLabel: `${chat.message_sequence || 0} ${chat.message_sequence === 1 ? 'message' : 'messages'}`,
    }));
    this.chatsLoaded = true;
    this.applyFilters();
  }

  clearSearch() {
    this.searchQuery = '';
    this.applyFilters();
  }

  toggleArchived() {
    this.showArchived = !this.showArchived;
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

  private dateGroup(dateStr: string): string {
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return 'Earlier';
    const today = new Date();
    const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    const days = Math.floor((startToday - startDate) / 86400000);
    if (days <= 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 7) return 'Previous 7 days';
    if (date.getFullYear() === today.getFullYear()) {
      return date.toLocaleString(undefined, { month: 'long' });
    }
    return String(date.getFullYear());
  }

  openChat(chat_id: string, initialMessage: any = null) {
    this.router.navigate([`ai/chats/${chat_id}`], {
      state: initialMessage ? { initialMessage } : {},
    });
  }

  formatDate(dateStr: string): string {
    if (!dateStr) return 'Just now';
    const date = new Date(dateStr);
    const now = new Date();
    const diff = (now.getTime() - date.getTime()) / 1000;

    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
    if (date.getFullYear() === now.getFullYear()) {
      return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
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

  /** Resolve once before creating the chat, so the first message can never
   *  fall into a chat with no model. A sole key needs no question; with
   *  several, the remembered choice wins and otherwise the picker opens. */
  private async llmForNewChat(): Promise<any | null> {
    const [secrets, profile] = await Promise.all([
      this.aiSession.listLlmSecrets(),
      this.profileService.get(),
    ]);
    const usable = secrets.filter((secret: any) => {
      const keys = secret?.keys || {};
      return !!(keys.provider && keys.model);
    });

    if (usable.length === 0) {
      this.alert({
        title: 'Set up a language model',
        type: 'error',
        messages: [{
          description:
            'No LLM connection is configured. Add one under Settings first.',
        }],
      });
      return null;
    }

    const preferredRef = profile?.preferences?.chat?.llm_secret_ref || '';
    let selected = usable.find(
      (secret: any) => secret.resource_ref === preferredRef,
    );
    if (!selected && usable.length === 1) selected = usable[0];

    let llm = selected ? this.llmFromSecret(selected) : null;
    if (!llm) {
      const ref = this.dialog.open(ChatLlmDialogComponent, {
        width: '480px',
        maxWidth: '95vw',
        data: { llm: {} },
      });
      llm = await firstValueFrom(ref.afterClosed());
    }
    if (!llm) return null;

    if (llm.secret_ref !== preferredRef) {
      const saved = await this.profileService.saveDefaultLlm(llm.secret_ref);
      if (saved.error) {
        this.alert({
          title: 'Default model not saved',
          type: 'error',
          messages: [{ description: saved.error }],
        });
        return null;
      }
    }
    return llm;
  }

  private llmFromSecret(secret: any): any {
    const keys = secret.keys || {};
    return {
      provider: keys.provider,
      model: keys.model,
      secret_ref: secret.resource_ref,
      ...(keys.endpoint ? { endpoint: keys.endpoint } : {}),
    };
  }

  alert(message: any) {
    this.dialog.open(AlertComponent, { width: 'auto', data: message });
  }

  /** Archiving needs no confirmation — it is reversible, and asking
   *  about a reversible thing teaches people to click through the
   *  dialogs that matter. */
  async archive(chat: any, event: MouseEvent) {
    event.stopPropagation();
    const res = await this.aiSession.archiveChat(chat.chat_id);
    if (res.error) {
      this.alert({
        title: 'Archive failed',
        type: 'error',
        messages: [{ description: res.error }],
      });
      return;
    }
    await this.loadChats();
  }

  async restore(chat: any, event: MouseEvent) {
    event.stopPropagation();
    const res = await this.aiSession.restoreChat(chat.chat_id);
    if (res.error) {
      this.alert({
        title: 'Restore failed',
        type: 'error',
        messages: [{ description: res.error }],
      });
      return;
    }
    await this.loadChats();
  }

  confirmDelete(chat: any, event: MouseEvent) {
    event.stopPropagation();

    const dialogRef = this.dialog.open(AlertComponent, {
      width: 'auto',
      data: {
        type: 'confirm',
        title: `Delete Chat "${chat.title || 'Untitled'}"`,
        messages: [
          {
            description: `Are you sure you want to delete this chat?`,
            table: {
              rows: [
                {
                  'Chat ID': chat.chat_id,
                  'Chat Title': chat.title || 'N/A',
                  'Updated At': this.formatDate(chat.updated_at),
                },
              ],
              cols: ['Chat ID', 'Chat Title', 'Updated At'],
            },
          },
        ],
      },
    });

    dialogRef.afterClosed().subscribe(async (result) => {
      if (result?.confirmed) await this.deleteChat(chat);
    });
  }

  async deleteChat(chat: any) {
    const res = await this.aiSession.deleteChat(chat.chat_id);

    if (!res.error) {
      await this.loadChats();
      this.alert({
        title: 'Chat Deleted',
        type: 'success',
        messages: [
          {
            description: `"${chat.title || 'Untitled Chat'}" has been permanently deleted.`,
            table: {
              cols: ['Chat ID', 'Chat Title', 'Updated At'],
              rows: [
                {
                  'Chat ID': chat.chat_id,
                  'Chat Title': chat.title || 'N/A',
                  'Updated At': this.formatDate(chat.updated_at),
                },
              ],
            },
          },
        ],
      });
    } else {
      this.alert({
        title: 'Delete Failed',
        type: 'error',
        messages: [
          {
            description: `Could not delete "${chat.title || 'Untitled Chat'}". The AI service may be unavailable — please try again.`,
          },
        ],
      });
    }
  }
}
