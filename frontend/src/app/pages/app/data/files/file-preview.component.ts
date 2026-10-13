import {
  Component, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges, ViewChild,
} from '@angular/core';
import { MarkdownService } from 'ngx-markdown';
import * as Prism from 'prismjs';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-sql';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-yaml';

import { fullDate, relativeDate } from 'src/app/components/ui/dates';
import { extensionOf, fileKindOf, sizeLabel } from 'src/app/components/ui/file-types';
import { headingsDown } from 'src/app/components/ui/markdown-levels';
import { FileResource, FilesService } from 'src/app/services/files.service';
import { ChatMarkdownService } from '../../ai/chat/chat-message/chat-markdown.service';

/** How a file is shown. `none` is a type with nothing to show here. */
type Preview = 'image' | 'pdf' | 'markdown' | 'table' | 'code' | 'text' | 'audio' | 'video' | 'none';

/** Text is read into the page up to this size; a picture, a recording
 *  or a PDF up to the second. Beyond, the file is downloaded to be read. */
const TEXT_LIMIT = 5 * 1024 * 1024;
const BYTES_LIMIT = 50 * 1024 * 1024;
/** A table shows this many rows; colouring code stops paying past this. */
const TABLE_ROWS = 200;
const HIGHLIGHT_LIMIT = 200 * 1024;

/** The Prism grammar for an extension, among the ones loaded above. */
const GRAMMARS: Record<string, string> = {
  py: 'python', ipynb: 'json', json: 'json', js: 'javascript', ts: 'typescript',
  yaml: 'yaml', yml: 'yaml', sh: 'bash', sql: 'sql', html: 'markup', xml: 'markup', css: 'css',
};

/**
 * One file, shown: its facts, what can be done with it, and the file
 * itself by type. A picture is shown, text is read, markdown is
 * rendered as in a chat, a CSV is a table, code is coloured, a
 * recording plays. Everything else says there is nothing to show and
 * offers the download.
 *
 * The page owns the actions (it knows what the person may do) and
 * hands them in as `previewActions`; this owns reading the bytes, from
 * the same door a download uses.
 */
@Component({
  selector: 'app-file-preview',
  standalone: false,
  templateUrl: './file-preview.component.html',
  styleUrl: './file-preview.component.css',
  // A file's markdown is somebody else's: what it would load from
  // elsewhere is taken out, as in a chat.
  providers: [{ provide: MarkdownService, useClass: ChatMarkdownService }],
})
export class FilePreviewComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) file!: FileResource;
  /** Where it came from and who sees it, in the words the list uses. */
  @Input() source = '';
  @Input() sharing = '';
  @Input() canDownload = false;
  /** The person may rename it, and the page's answer to the last try. */
  @Input() canRename = false;
  @Input() renaming = false;
  @Input() renameProblem = '';

  @Output() closed = new EventEmitter<void>();
  @Output() download = new EventEmitter<void>();
  /** The whole new name, extension included. */
  @Output() rename = new EventEmitter<string>();

  /** The name being typed, without its extension; null while the name
   *  is only shown. */
  draftName: string | null = null;
  /** The box, as it appears: the name is selected, ready to be typed over. */
  @ViewChild('nameBox') set nameBox(box: ElementRef<HTMLInputElement> | undefined) {
    // After the form has written the name into it.
    setTimeout(() => box?.nativeElement.select());
  }

  kind: Preview = 'none';
  loading = false;
  /** Why the file is not shown, when it could have been. */
  problem = '';
  /** The bytes, as an address the page can show; '' until they are read. */
  address = '';
  text = '';
  /** Coloured code, as markup Prism wrote and escaped itself. */
  coloured = '';
  rows: string[][] = [];
  rowsLeft = 0;
  zoomed = false;

  constructor(private files: FilesService) {}

  ngOnChanges(changes: SimpleChanges): void {
    const before = changes['file']?.previousValue as FileResource | undefined;
    if (before?.resource_ref !== this.file.resource_ref) void this.read();
    // Another file, or this one under its new name: the box closes.
    if (changes['file'] && before?.values?.filename !== this.name) this.draftName = null;
  }

  ngOnDestroy(): void {
    this.forget();
  }

  // ── The facts ───────────────────────────────────────────────────────

  get name(): string {
    return this.file.values?.filename || 'Untitled file';
  }

  /** The extension with its dot, which a rename keeps. */
  get ending(): string {
    const extension = extensionOf(this.name);
    return extension ? this.name.slice(-(extension.length + 1)) : '';
  }

  startRename(): void {
    this.draftName = this.ending ? this.name.slice(0, -this.ending.length) : this.name;
  }

  saveRename(): void {
    const typed = (this.draftName || '').trim();
    if (!typed || this.renaming) return;
    const name = typed + this.ending;
    if (name === this.name) this.draftName = null;
    else this.rename.emit(name);
  }

  get size(): string {
    return sizeLabel(Number(this.file.values?.file_size ?? 0));
  }

  get created(): string {
    return fullDate(this.file.created_at || '');
  }

  /** When it changed, only where that is not when it was stored. */
  get changed(): string {
    const changed = this.file.updated_at || '';
    return changed && changed !== this.file.created_at ? relativeDate(changed) : '';
  }

  /** What the agent or the chat attached to it, beyond what the facts
   *  above already say. */
  get keys(): [string, string][] {
    return Object.entries(this.file.keys || {})
      .filter(([key]) => !['chat_id', 'file_kind', 'category'].includes(key))
      .map(([key, value]) => [key, typeof value === 'string' ? value : JSON.stringify(value)]);
  }

  // ── Reading the file ────────────────────────────────────────────────

  /** How a file of this name and type is shown. */
  private kindOf(): Preview {
    const extension = extensionOf(this.name);
    const mime = this.file.values?.file_type || '';
    const kind = fileKindOf(this.name, mime);
    if (kind === 'image') return 'image';
    if (kind === 'pdf') return 'pdf';
    if (kind === 'media') return mime.startsWith('video/') || ['mp4', 'mov', 'webm', 'mkv', 'avi'].includes(extension)
      ? 'video' : 'audio';
    if (extension === 'md' || extension === 'markdown') return 'markdown';
    if (extension === 'csv' || extension === 'tsv') return 'table';
    if (kind === 'code') return 'code';
    if (extension === 'txt' || mime.startsWith('text/')) return 'text';
    return 'none';
  }

  private forget(): void {
    if (this.address) URL.revokeObjectURL(this.address);
    this.address = '';
    this.text = '';
    this.coloured = '';
    this.rows = [];
    this.rowsLeft = 0;
    this.zoomed = false;
    this.problem = '';
  }

  private async read(): Promise<void> {
    this.forget();
    this.kind = this.file.unreadable ? 'none' : this.kindOf();
    // A PDF is read when it is opened, not to be looked at here.
    if (this.kind === 'none' || this.kind === 'pdf') return;

    const textual = ['markdown', 'table', 'code', 'text'].includes(this.kind);
    const size = Number(this.file.values?.file_size ?? 0);
    if (size > (textual ? TEXT_LIMIT : BYTES_LIMIT)) {
      this.problem = 'Too large to show here. Download it to read the whole file.';
      return;
    }

    const ref = this.file.resource_ref;
    this.loading = true;
    try {
      const bytes = await this.bytes();
      // Another file was opened while this one was on its way.
      if (ref !== this.file.resource_ref) return;
      if (!textual) {
        this.address = URL.createObjectURL(bytes);
        return;
      }
      this.text = await bytes.text();
      // The file's name is the panel's `h2`: its own headings go under it.
      if (this.kind === 'markdown') this.text = headingsDown(this.text, 2);
      if (this.kind === 'table') this.fillTable();
      if (this.kind === 'code') this.colour();
    } catch {
      if (ref === this.file.resource_ref) this.problem = 'The file could not be read just now.';
    } finally {
      if (ref === this.file.resource_ref) this.loading = false;
    }
  }

  /** The file's bytes, from the door a download uses, with the session. */
  private async bytes(): Promise<Blob> {
    const answer = await fetch(this.files.downloadUrl(this.file.resource_ref), { credentials: 'include' });
    if (!answer.ok) throw new Error(String(answer.status));
    return answer.blob();
  }

  private fillTable(): void {
    const all = parseDelimited(this.text, extensionOf(this.name) === 'tsv' ? '\t' : ',');
    this.rows = all.slice(0, TABLE_ROWS + 1);
    this.rowsLeft = Math.max(0, all.length - this.rows.length);
  }

  private colour(): void {
    const language = GRAMMARS[extensionOf(this.name)];
    const grammar = language ? Prism.languages[language] : undefined;
    if (grammar && this.text.length <= HIGHLIGHT_LIMIT) {
      this.coloured = Prism.highlight(this.text, grammar, language);
    }
  }

  /** A PDF opens in a tab of its own: this deployment lets nothing be
   *  shown inside a frame, its own files included. */
  async openPdf(): Promise<void> {
    this.loading = true;
    try {
      if (!this.address) {
        const bytes = await this.bytes();
        this.address = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
      }
      window.open(this.address, '_blank', 'noopener');
    } catch {
      this.problem = 'The file could not be read just now.';
    } finally {
      this.loading = false;
    }
  }
}

/** The rows of a CSV or a TSV: quoted fields may hold the delimiter, a
 *  doubled quote, and line breaks. */
export function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') { field += '"'; index += 1; }
      else if (character === '"') quoted = false;
      else field += character;
    } else if (character === '"' && field === '') {
      quoted = true;
    } else if (character === delimiter) {
      row.push(field); field = '';
    } else if (character === '\n' || character === '\r') {
      if (character === '\r' && text[index + 1] === '\n') index += 1;
      row.push(field); field = '';
      rows.push(row); row = [];
    } else {
      field += character;
    }
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows;
}
