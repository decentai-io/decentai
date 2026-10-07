import { AfterViewChecked, Component, ElementRef, Input, OnChanges, OnDestroy, Renderer2 } from '@angular/core';
import { Clipboard } from '@angular/cdk/clipboard';
import { MarkdownService } from 'ngx-markdown';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { ReadAloudService } from 'src/app/services/read-aloud.service';
import { ChatMarkdownService } from './chat-markdown.service';

@Component({
  selector: 'app-chat-message',
  standalone: false,
  templateUrl: './chat-message.component.html',
  styleUrls: ['./chat-message.component.css'],
  // A message's markdown loads nothing from elsewhere; every other
  // page keeps the app's own renderer.
  providers: [{ provide: MarkdownService, useClass: ChatMarkdownService }],
})
export class ChatMessageComponent implements OnChanges, AfterViewChecked, OnDestroy {
  @Input() message!: any;

  /** A further assistant message in the same turn: the answer went on,
   *  so the name and the time are not repeated — one speaker, one
   *  header, however many messages it took. */
  @Input() continuation = false;
  /** What the copy control copies when the answer spans several
   *  messages: the whole of it, handed in by the thread. */
  @Input() copyText: string | null = null;
  copiedPart: string | null = null;
  contentExpanded = false;
  fileError = '';
  private enhancedBlocks = new WeakSet<HTMLElement>();
  private cleanups: Array<() => void> = [];

  /** When the message happened — time alone for today, date + time
      otherwise. Empty string → nothing shown. */
  get timeLabel(): string {
    const raw = this.message?.created_at;
    if (!raw) return '';
    const date = new Date(raw);
    if (isNaN(date.getTime())) return '';
    const time = date.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
    const today = new Date();
    if (date.toDateString() === today.toDateString()) return time;
    const day = date.toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
    });
    return `${day}, ${time}`;
  }

  constructor(
    private clipboard: Clipboard,
    private aiSession: AiSessionService,
    private host: ElementRef<HTMLElement>,
    private renderer: Renderer2,
    public readAloud: ReadAloudService,
  ) {}

  /** What this reply is known by to the voice: one reply is said at a
   *  time, and the control shows which. */
  get speechKey(): string {
    return String(this.message?.message_id || '');
  }

  get isSpeaking(): boolean {
    return !!this.speechKey && this.readAloud.speaking === this.speechKey;
  }

  ngOnChanges(): void {
    this.contentExpanded = false;
    this.fileError = '';
  }

  ngAfterViewChecked(): void {
    this.host.nativeElement.querySelectorAll<HTMLElement>('pre').forEach((pre) => {
      if (this.enhancedBlocks.has(pre)) return;
      this.enhancedBlocks.add(pre);
      const code = pre.querySelector('code');
      if (!code) return;
      const language = Array.from(code.classList).find((name) => name.startsWith('language-'))
        ?.replace('language-', '') || 'code';
      const toolbar = this.renderer.createElement('div');
      this.renderer.addClass(toolbar, 'code-toolbar');
      const label = this.renderer.createElement('span');
      this.renderer.appendChild(label, this.renderer.createText(language));
      const wrap = this.renderer.createElement('button');
      this.renderer.setAttribute(wrap, 'type', 'button');
      this.renderer.setAttribute(wrap, 'aria-label', 'Toggle code wrapping');
      this.renderer.appendChild(wrap, this.renderer.createText('Wrap'));
      const copy = this.renderer.createElement('button');
      this.renderer.setAttribute(copy, 'type', 'button');
      this.renderer.setAttribute(copy, 'aria-label', 'Copy code block');
      this.renderer.appendChild(copy, this.renderer.createText('Copy'));
      this.renderer.appendChild(toolbar, label);
      this.renderer.appendChild(toolbar, wrap);
      this.renderer.appendChild(toolbar, copy);
      this.renderer.insertBefore(pre, toolbar, code);
      this.cleanups.push(this.renderer.listen(wrap, 'click', () =>
        pre.classList.toggle('code-wrap')));
      this.cleanups.push(this.renderer.listen(copy, 'click', () => {
        this.clipboard.copy(code.textContent || '');
        copy.textContent = 'Copied';
        setTimeout(() => copy.textContent = 'Copy', 1500);
      }));
    });
    this.host.nativeElement.querySelectorAll<HTMLAnchorElement>('.ai-markdown a').forEach((link) => {
      if (/^https?:/i.test(link.href)) {
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
    });
  }

  ngOnDestroy(): void { this.cleanups.forEach((cleanup) => cleanup()); }

  get isLongResponse(): boolean { return this.isAI() && this.getFullText().length > 6000; }
  toggleContent(): void { this.contentExpanded = !this.contentExpanded; }
  isFilePart(part: any): boolean { return part?.type === 'file'; }

  /** From this many files in a row, they become one scrollable strip
   *  rather than that many cards. Six downloads at 60px each pushed the
   *  answer they were produced for off the screen. */
  private static readonly FILES_FIT_ON_SCREEN = 3;

  /** The parts to render, with runs of files gathered up.
   *
   *  A file part is 60px of card plus a "from OneDrive" line, and an
   *  agent that fetches a folder emits one per file. They are grouped
   *  where they already sit, so nothing is reordered and a group ends
   *  wherever something that is not a file interrupts it.
   */
  get renderParts(): any[] {
    const parts: any[] = this.message?.parts || [];
    const out: any[] = [];
    let run: any[] = [];
    const flush = () => {
      if (!run.length) return;
      if (run.length >= ChatMessageComponent.FILES_FIT_ON_SCREEN) {
        out.push({ type: 'files', files: run, source: run[0]?.source });
      } else {
        out.push(...run);
      }
      run = [];
    };
    for (const part of parts) {
      if (this.isFilePart(part)) { run.push(part); continue; }
      flush();
      out.push(part);
    }
    flush();
    return out;
  }

  filesLabel(group: any): string {
    const n = group?.files?.length || 0;
    const from = this.partSource(group?.files?.[0]);
    return `${n} file${n === 1 ? '' : 's'}${from ? ' from ' + from : ''}`;
  }

  copyToClipboard(text: string, id: string) {
    this.clipboard.copy(text || '');
    this.copiedPart = id;
    setTimeout(() => (this.copiedPart = null), 1500);
  }

  isUser(): boolean {
    return this.getActor() === 'user';
  }

  isAI(): boolean {
    return this.getActor() === 'ai';
  }

  getFullText(): string {
    return (this.message?.parts || [])
      .filter((p: any) => p?.text)
      .map((p: any) => p.text)
      .join('\n\n');
  }

  isDownloadableFile(part: any): boolean {
    return part?.type === 'file' && !!this.getFileUrl(part);
  }

  /** Whether this file is something a person should simply see. The
   *  stored type is the provider's word for it; no contract change was
   *  needed to show a picture, only the willingness to look at it. */
  isImageFile(part: any): boolean {
    return String(part?.file_type || '').startsWith('image/');
  }

  /** Whose words or data a part is, when an agent produced it — a
   *  quiet caption under a table, chart or file, or under what an agent
   *  said itself (call.post). The voice stays the assistant's. */
  partSource(part: any): string {
    if (!['table', 'graph', 'file', 'markdown'].includes(part?.type)) return '';
    return part?.source?.kind === 'agent' ? String(part.source.agent_name || '') : '';
  }

  downloadFile(part: any) {
    try {
      const href = this.getFileUrl(part);
      if (!href) {
        return this.showError('This file is unavailable.');
      }

      const link = document.createElement('a');
      link.href = href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';

      if (part?.filename) {
        link.download = part.filename;
      }

      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch {
      this.showError('Error downloading file.');
    }
  }

  openFileUrl(part: any) {
    try {
      const href = this.getFileUrl(part);
      if (!href) {
        return this.showError('This file is unavailable.');
      }

      window.open(href, '_blank', 'noopener,noreferrer');
    } catch {
      this.showError('Error opening file.');
    }
  }

  private showError(message: string) {
    this.fileError = message;
  }

  /** A file part names its file by reference, never by address: the
   *  platform's own download route serves it, under the session. */
  getFileUrl(part: any): string {
    const ref = String(part?.resource_ref || '');
    return ref ? this.aiSession.buildDownloadUrl(ref) || '' : '';
  }

  getFileExt(part: any): string {
    const name = String(part?.filename || '');
    return name.includes('.') ? name.split('.').pop()!.toUpperCase() : 'FILE';
  }

  getFileIcon(part: any): string {
    const ext = this.getFileExt(part).toLowerCase();
    const map: Record<string, string> = {
      pdf: 'file-text',
      doc: 'file-text', docx: 'file-text',
      xls: 'file-spreadsheet', xlsx: 'file-spreadsheet', csv: 'file-spreadsheet',
      ppt: 'presentation', pptx: 'presentation',
      jpg: 'file-image', jpeg: 'file-image', png: 'file-image', gif: 'file-image',
      svg: 'file-image', webp: 'file-image',
      mp4: 'file-video-camera', mov: 'file-video-camera', avi: 'file-video-camera',
      mp3: 'file-music', wav: 'file-music',
      zip: 'file-archive', rar: 'file-archive',
      txt: 'file-text', json: 'file-braces',
      py: 'file-code', js: 'file-code', ts: 'file-code', html: 'file-code', css: 'file-code',
    };
    return map[ext] || 'paperclip';
  }

  private getActor(): string {
    return String(this.message?.actor || '').toLowerCase();
  }
}
