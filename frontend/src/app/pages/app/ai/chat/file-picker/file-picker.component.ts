import {
  Component, EventEmitter, Input, OnChanges, OnInit, Output, SimpleChanges,
} from '@angular/core';
import { FileChoice } from 'src/app/models/chat-protocol';
import { FileResource, FilesService } from 'src/app/services/files.service';

/**
 * Choosing files from everything the person can see.
 *
 * One picker, three places: the assistant's files card (find_files),
 * where the candidates it found come pre-ticked; the composer's
 * "choose from your files", where the newest few lead; and the Files
 * page, which stages one file straight into a new chat. The list is
 * the same list the Files page shows — the backend's visibility rules
 * decide what is in it — searched by name here, in the page, because a
 * file's name is stored encrypted and cannot be queried.
 *
 * The picker owns the ticks and says what changed; whoever embeds it
 * owns the buttons and what the choice means.
 */
@Component({
  selector: 'app-file-picker',
  standalone: false,
  templateUrl: './file-picker.component.html',
  styleUrls: ['./file-picker.component.css'],
})
export class FilePickerComponent implements OnInit, OnChanges {
  /** Files to list first, as the assistant ranked them. */
  @Input() candidates: FileChoice[] = [];
  /** Refs ticked before the person touches anything. */
  @Input() preselect: string[] = [];
  /** With no candidates and no search, the newest this many lead —
   *  zero shows the search alone. */
  @Input() recentCount = 0;

  @Output() selectionChange = new EventEmitter<FileChoice[]>();

  /** How many search results are listed at once. */
  static readonly SEARCH_LIMIT = 30;

  selected = new Set<string>();
  search = '';
  loading = false;
  error = '';
  private everything: FileResource[] | null = null;

  constructor(private files: FilesService) {}

  ngOnInit(): void {
    for (const ref of this.preselect || []) this.selected.add(ref);
    void this.loadEverything();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['preselect'] && !changes['preselect'].firstChange) {
      this.selected = new Set(this.preselect || []);
      this.announce();
    }
  }

  /** The candidates, or the newest few when there are none to propose. */
  get leading(): FileChoice[] {
    if (this.candidates?.length) return this.candidates;
    if (!this.recentCount || !this.everything) return [];
    return [...this.everything]
      .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
      .slice(0, this.recentCount)
      .map((file) => this.asChoice(file));
  }

  get leadingLabel(): string {
    return this.candidates?.length ? 'Files found' : 'Recent files';
  }

  /** Files the search finds, the leading ones left out — they are
   *  already listed above. */
  get searchResults(): FileChoice[] {
    const words = this.search.trim().toLowerCase();
    if (!words || !this.everything) return [];
    const listed = new Set(this.leading.map((c) => c.resource_ref));
    return this.everything
      .filter((file) => !listed.has(file.resource_ref))
      .filter((file) => (file.values?.filename || '').toLowerCase().includes(words))
      .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
      .slice(0, FilePickerComponent.SEARCH_LIMIT)
      .map((file) => this.asChoice(file));
  }

  get searching(): boolean {
    return !!this.search.trim() && !this.loading;
  }

  /** What is ticked, as choices, in the order ticked. */
  get chosen(): FileChoice[] {
    const known = new Map<string, FileChoice>();
    for (const candidate of this.candidates || []) known.set(candidate.resource_ref, candidate);
    for (const file of this.everything || []) {
      if (!known.has(file.resource_ref)) known.set(file.resource_ref, this.asChoice(file));
    }
    return [...this.selected].map((ref) => known.get(ref) || { resource_ref: ref, filename: 'a file' });
  }

  isSelected(ref: string): boolean {
    return this.selected.has(ref);
  }

  toggle(ref: string): void {
    if (this.selected.has(ref)) this.selected.delete(ref);
    else this.selected.add(ref);
    this.announce();
  }

  private announce(): void {
    this.selectionChange.emit(this.chosen);
  }

  /** The kind a person recognises: the extension, else the type. */
  fileKind(file: FileChoice): string {
    const name = String(file.filename || '');
    const dot = name.lastIndexOf('.');
    if (dot > 0 && dot < name.length - 1) return name.slice(dot + 1).toUpperCase();
    return String(file.file_type || '').split('/').pop()?.toUpperCase() || 'FILE';
  }

  fileSize(file: FileChoice): string {
    const bytes = Number(file.file_size || 0);
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  fileDate(file: FileChoice): string {
    const raw = String(file.created_at || '');
    if (!raw) return '';
    const when = new Date(raw);
    return isNaN(when.getTime()) ? '' : when.toLocaleDateString();
  }

  /** One line under the name: kind, size, where it came from, when. */
  fileMeta(file: FileChoice): string {
    return [this.fileKind(file), this.fileSize(file), file.source, this.fileDate(file)]
      .filter((part) => !!part).join(' · ');
  }

  /** A file as the Files page lists it, in the picker's shape. */
  static toChoice(file: FileResource): FileChoice {
    return {
      resource_ref: file.resource_ref,
      filename: file.values?.filename || '',
      file_type: file.values?.file_type || '',
      file_size: file.values?.file_size || 0,
      source: FilePickerComponent.sourceOf(file.values?.folder || ''),
      created_at: file.created_at || '',
    };
  }

  private asChoice(file: FileResource): FileChoice {
    return FilePickerComponent.toChoice(file);
  }

  /** The same words the runtime uses for a candidate's origin. */
  static sourceOf(folder: string): string {
    const leaf = folder.replace(/\/+$/, '').split('/').pop() || '';
    return ({ uploads: 'Files page', chat_artifacts: 'a chat', agents: 'an agent' } as Record<string, string>)[leaf] || '';
  }

  private async loadEverything(): Promise<void> {
    if (this.everything || this.loading) return;
    this.loading = true;
    this.error = '';
    try {
      this.everything = await this.files.list();
    } catch {
      this.everything = [];
      this.error = 'Your files could not be listed.';
    } finally {
      this.loading = false;
    }
  }
}
