// chat-composer.component.ts
import {
  Component,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  OnInit,
  Output,
  SimpleChanges,
  ViewChild,
  ElementRef,
  NgZone,
} from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { FileChoice } from 'src/app/models/chat-protocol';
import { RequestService } from 'src/app/services/request.service';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { SettingsSpeechService } from 'src/app/services/settings-speech.service';
import { MicrophoneService } from 'src/app/services/microphone.service';
import { ChatFilePickerDialogComponent } from '../chat-file-picker-dialog/chat-file-picker-dialog.component';

@Component({
  selector: 'app-chat-composer',
  standalone: false,
  templateUrl: './chat-composer.component.html',
  styleUrls: ['./chat-composer.component.css'],
})
export class ChatComposerComponent implements OnChanges, OnInit, OnDestroy {
  /** Parent controls loading. Composer shows a stop button while true;
   *  sending stays possible — a message mid-turn steers the work.
   *  The composer clears itself the moment a message is sent (see
   *  sendMessage), not when loading ends. */
  @Input() loading = false;
  @Input() disabled = false;
  @Input() placeholder = 'Write a message';
  @Input() hint = 'Enter to send · Shift+Enter for a new line';

  /** Chat ID for the active session. Upload button is shown only when set. */
  @Input() chatId: string | null = null;

  /** Files the platform already holds, staged as attachments by whoever
   *  opened this chat — the Files page's "Use in a chat". Attached by
   *  reference, nothing uploaded. */
  @Input() staged: FileChoice[] | null = null;

  /** Words put in the box for the person to read, change and send —
   *  a prompt handed over by another page. Never sent on their behalf. */
  @Input() draft = '';

  @ViewChild('messageInput') messageInput!: ElementRef<HTMLTextAreaElement>;
  @ViewChild('fileInput') fileInput!: ElementRef<HTMLInputElement>;
  @ViewChild('cameraInput') cameraInput?: ElementRef<HTMLInputElement>;

  @Output() promptSent = new EventEmitter<{ query: string; uploads: any[] }>();
  @Output() stopRequested = new EventEmitter<void>();

  /** While a response is generating, the send button becomes a stop button. */
  stop(): void {
    this.stopRequested.emit();
  }

  userInput = '';
  userUploads: any[] = [];
  isUploading = false;

  constructor(
    private request: RequestService,
    private aiSession: AiSessionService,
    private dialog: MatDialog,
    private speech: SettingsSpeechService,
    private zone: NgZone,
    private microphone: MicrophoneService,
  ) {}

  // ── Speaking a message ──────────────────────────────────────────────

  /** The microphone is offered only when the organization has a
   *  transcription model and this browser can record. */
  canSpeak = false;
  recording = false;
  transcribing = false;
  /** What the composer itself has to say under the box — the
   *  microphone refused, nothing was heard — until the next recording.
   *  It stands in for the page's hint while it lasts. */
  notice = '';
  private recorder: MediaRecorder | null = null;
  private recorded: Blob[] = [];
  private destroyed = false;

  async ngOnInit(): Promise<void> {
    this.canSpeak = this.microphone.available && await this.speech.configured();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    // A recording under way ends with the composer: nothing would read
    // it, and a recorder left running keeps the microphone's stream.
    const recorder = this.recorder;
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = null;
      recorder.ondataavailable = null;
      try { recorder.stop(); } catch {}
    }
    this.recorded = [];
    this.stopTracks();
    this.releasePreviews(this.userUploads);
  }

  /** A photo, taken now: on a phone the camera opens; on a computer the
   *  same control offers the pictures it has. It lands as an attachment
   *  like any file. */
  openCamera(): void {
    if (this.disabled || this.isUploading) return;
    this.cameraInput?.nativeElement.click();
  }

  /** One button: press to start, press again to stop. The words land
   *  in the box for the person to read and send, never sent unseen. */
  async toggleRecording(): Promise<void> {
    if (this.recording) {
      this.recorder?.stop();
      return;
    }
    if (this.disabled || this.transcribing) return;
    this.notice = '';
    let stream: MediaStream;
    try {
      stream = await this.microphone.acquire();
    } catch {
      this.notice = 'The microphone is not available — allow it in the browser and try again.';
      return;
    }
    if (this.destroyed) {
      this.microphone.release();
      return;
    }
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']
      .find((candidate) => MediaRecorder.isTypeSupported(candidate));
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    this.recorded = [];
    recorder.ondataavailable = (event) => { if (event.data.size) this.recorded.push(event.data); };
    // The recorder's events fire outside Angular's zone, so a finish
    // handled there would leave the page showing "writing it down"
    // until the next click. Brought back in, the words show at once.
    recorder.onstop = () => {
      this.zone.run(() => { void this.finishRecording(recorder.mimeType); });
    };
    this.recorder = recorder;
    this.recording = true;
    recorder.start();
  }

  private async finishRecording(mime: string): Promise<void> {
    this.recording = false;
    this.stopTracks();
    const blob = new Blob(this.recorded, { type: mime || 'audio/webm' });
    this.recorded = [];
    if (!blob.size) return;
    this.transcribing = true;
    try {
      const result = await this.speech.transcribe(blob);
      if (result.error || !result.text) {
        this.notice = result.error || 'Nothing was heard.';
        return;
      }
      const spoken = result.text.trim();
      this.userInput = this.userInput.trim()
        ? `${this.userInput.trimEnd()} ${spoken}`
        : spoken;
      queueMicrotask(() => {
        const el = this.messageInput?.nativeElement;
        if (!el) return;
        el.focus();
        // The binding writes the new words into the box only on the next
        // change detection; measured now, the box would size itself to
        // the old text. Put the words in first, then measure.
        el.value = this.userInput;
        this.grow(el);
      });
    } finally {
      this.transcribing = false;
    }
  }

  private stopTracks(): void {
    // Muted, not ended: the grant is kept for the next recording, so a
    // phone does not ask again in the next chat.
    this.microphone.release();
    this.recorder = null;
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['staged'] && this.staged?.length) this.addStaged(this.staged);
    if (changes['draft'] && this.draft) {
      this.userInput = this.draft;
      queueMicrotask(() => {
        const el = this.messageInput?.nativeElement;
        if (!el) return;
        el.focus();
        el.value = this.userInput;
        this.grow(el);
      });
    }
  }

  openFilePicker(): void {
    if (this.disabled || this.isUploading) return;
    this.fileInput?.nativeElement?.click();
  }

  /** "Choose from your files": the shared picker in a dialog, and what
   *  is chosen joins the chips as attachments-to-be. */
  chooseFromFiles(): void {
    if (this.disabled || this.isUploading) return;
    const ref = this.dialog.open(ChatFilePickerDialogComponent, {
      width: '560px', maxWidth: '95vw',
    });
    ref.afterClosed().subscribe((chosen: FileChoice[] | undefined) => {
      if (chosen?.length) this.addStaged(chosen);
      queueMicrotask(() => this.messageInput?.nativeElement?.focus());
    });
  }

  /** A file the platform already holds becomes a chip, ready at once:
   *  the message will name it by reference. One already staged is not
   *  staged twice. */
  private addStaged(files: FileChoice[]): void {
    const present = new Set(this.userUploads.map((item) => item.file_id).filter(Boolean));
    for (const file of files) {
      if (!file?.resource_ref || present.has(file.resource_ref)) continue;
      present.add(file.resource_ref);
      this.userUploads.push({
        local_id: crypto.randomUUID(), file_id: file.resource_ref,
        filename: file.filename || 'a file', file_size: file.file_size || 0,
        file_type: file.file_type || '', status: 'ready', error: '',
        preview: '', linked: true,
      });
    }
    this.userUploads = [...this.userUploads];
  }

  async onFileSelect(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const files = input.files ? Array.from(input.files) : [];
    // allow selecting the same file again later
    input.value = '';
    await this.addFiles(files);
  }

  /** A screenshot, pasted.
   *
   *  Taking a picture of the screen and pressing Ctrl+V is how people
   *  actually show you a thing, so the clipboard is an intake surface
   *  like the paperclip. Only images are taken: pasting text into a
   *  text box must go on meaning what it always meant, so anything
   *  else falls through to the browser untouched.
   *
   *  Nothing is attached before there is a chat to attach it to — the
   *  paperclip is hidden in that state for the same reason — and
   *  swallowing the paste there would look like acceptance. */
  async onPaste(event: ClipboardEvent): Promise<void> {
    if (!this.chatId || this.disabled || this.isUploading) return;
    const items = Array.from(event.clipboardData?.items || []);
    const images = items.filter((item) => item.kind === 'file'
      && item.type.startsWith('image/'));
    if (!images.length) return;

    // Only now: the text path is still the browser's.
    event.preventDefault();
    const files: File[] = [];
    for (const item of images) {
      const blob = item.getAsFile();
      if (blob) files.push(this.named(blob));
    }
    await this.addFiles(files);
  }

  /** A clipboard image arrives unnamed, or as a bare "image.png" that
   *  every paste would share. The stored file's type is guessed from
   *  its NAME, so one without a proper extension would land as
   *  octet-stream and stop looking like a picture — hence a name of
   *  our own, stamped so two pastes are two files. */
  private named(blob: File | Blob): File {
    const type = blob.type || 'image/png';
    const extension = (type.split('/')[1] || 'png').split('+')[0];
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    return new File([blob], `screenshot-${stamp}.${extension}`, { type });
  }

  /** One way in for everything attached, however it arrived. */
  private async addFiles(files: File[]): Promise<void> {
    if (!files.length || !this.chatId) return;
    this.isUploading = true;
    try {
      for (const file of files) {
        const item = { local_id: crypto.randomUUID(), file, filename: file.name,
          file_size: file.size, file_type: file.type, status: 'uploading', error: '',
          preview: file.type.startsWith('image/')
            ? URL.createObjectURL(file) : '' };
        this.userUploads.push(item);
        await this.uploadItem(item);
      }
    } catch (err) {
      console.error('File selection exception:', err);
      this.userUploads.push({ local_id: crypto.randomUUID(), filename: 'Attachment',
        status: 'failed', error: 'Unable to select or read this file.' });
    } finally {
      this.isUploading = false;
    }
  }

  async retryUpload(item: any): Promise<void> { await this.uploadItem(item); }

  private async uploadItem(item: any): Promise<void> {
    if (!this.chatId || !item.file) return;
    item.status = 'uploading'; item.error = '';
    const resp = await this.aiSession.uploadChatFile(this.chatId, item.file);
    if (!this.userUploads.includes(item)) {
      // The chip was removed, or the message sent, while the file was
      // still going up: nothing names it now, so it is not kept.
      const ref = resp.data?.resource?.resource_ref;
      if (ref) void this.request.gateway('Files:File:Delete', { resource_ref: ref });
      return;
    }
    if (!resp.error && resp.data?.resource) {
      const resource = resp.data.resource;
      Object.assign(item, { file_id: resource.resource_ref,
        filename: resource.values?.filename || item.filename,
        file_size: resource.values?.file_size || item.file_size,
        file_type: resource.values?.file_type || item.file_type, status: 'ready' });
    } else {
      item.status = 'failed'; item.error = resp.error || `Failed to upload ${item.filename}`;
    }
    this.userUploads = [...this.userUploads];
  }

  /** A click anywhere on the composer's surface, outside its buttons,
   *  puts the cursor in the text. */
  focusInput(event: MouseEvent): void {
    if ((event.target as HTMLElement).closest('button, textarea, input')) return;
    this.messageInput?.nativeElement?.focus();
  }

  async removeUpload(item: any): Promise<void> {
    try {
      // Nothing was stored (the upload failed or never finished), so there
      // is nothing to delete on the server — just let go of it here. A
      // linked file is the platform's already, not this chat's upload:
      // letting go of the chip must not delete it.
      if (!item?.file_id || item.linked) {
        this.userUploads = this.userUploads.filter((u) => u !== item);
        this.releasePreviews([item]);
        return;
      }

      const response = await this.request.gateway('Files:File:Delete', {
        resource_ref: item.file_id,
      });

      if (!response?.error) {
        this.userUploads = this.userUploads.filter(
          (u) => u.file_id !== item.file_id,
        );
        this.releasePreviews([item]);
        return;
      }

      item.error = response.error; item.status = 'failed';
    } catch (err) {
      console.error('Delete upload failed:', err);
      item.error = 'Failed to remove file.'; item.status = 'failed';
    }
  }

  sendMessage(): void {
    const query = this.userInput.trim();
    if (!query) return;

    if (this.disabled || this.isUploading) return;

    // Pass a copy so clearing the composer below can't mutate what the parent
    // just received.
    this.promptSent.emit({ query, uploads: this.userUploads.filter((item) => item.status === 'ready') });
    // Clear right away: the query and its attachments now belong to the sent
    // message, so they should leave the composer immediately rather than
    // lingering in it for the whole (possibly long) response.
    this.clearComposer();
    queueMicrotask(() => this.messageInput?.nativeElement?.focus());
  }

  onInput(event: Event): void {
    this.grow(event.target as HTMLTextAreaElement);
  }

  /** The box takes the height of its words, up to the stylesheet's cap,
   *  and scrolls past it. */
  private grow(el: HTMLTextAreaElement): void {
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
    el.style.overflowY = el.scrollHeight > el.offsetHeight ? 'auto' : 'hidden';
  }

  onPrompt(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  /** The previews are object URLs this document owns; dropping a chip
   *  without releasing its own leaks the image bytes for the life of
   *  the page. */
  private releasePreviews(items: any[]): void {
    for (const item of items) {
      if (item?.preview) URL.revokeObjectURL(item.preview);
    }
  }

  private clearComposer(): void {
    this.userInput = '';
    this.releasePreviews(this.userUploads);
    this.userUploads = [];

    const el = this.messageInput?.nativeElement;
    if (el) {
      el.style.height = 'auto';
      el.style.overflowY = 'hidden';
    }
  }

}
