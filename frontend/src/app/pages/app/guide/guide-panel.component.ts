import { HttpClient } from '@angular/common/http';
import { Component, ElementRef, Inject, OnInit, ViewChild } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { MarkdownService } from 'ngx-markdown';
import { firstValueFrom } from 'rxjs';

import { headingsDown } from 'src/app/components/ui/markdown-levels';
import { ChatMarkdownService } from '../ai/chat/chat-message/chat-markdown.service';
import { GuideChapter, addressHeadings, headingsOf } from './guide-text';

/** What the panel is opened on: a chapter, and a heading in it. */
export interface GuidePanelData {
  chapter: string;
  heading?: string;
}

/**
 * A chapter of the Guide beside the page it explains: at the right of
 * a wide screen, a sheet from the bottom of a narrow one. The page
 * behind stays as it was, so what is read can be tried at once. The
 * whole chapter is here, opened at the heading the page asked for,
 * with the way to the Guide itself at its foot.
 */
@Component({
  selector: 'app-guide-panel',
  standalone: false,
  templateUrl: './guide-panel.component.html',
  styleUrl: './guide-panel.component.css',
  providers: [{ provide: MarkdownService, useClass: ChatMarkdownService }],
})
export class GuidePanelComponent implements OnInit {
  loading = true;
  chapter: GuideChapter | null = null;
  /** The chapter's text, its title one level under the panel's own. */
  body = '';
  private text = '';

  @ViewChild('reading') private reading?: ElementRef<HTMLElement>;

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: GuidePanelData,
    private dialogRef: MatDialogRef<GuidePanelComponent>,
    private http: HttpClient,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      const index = await firstValueFrom(this.http.get<{ chapters: GuideChapter[] }>('assets/guide/index.json'));
      this.chapter = (index?.chapters || []).find((chapter) => chapter.id === this.data.chapter) || null;
      if (this.chapter) {
        this.text = await firstValueFrom(this.http.get(`assets/guide/${this.chapter.file}`, { responseType: 'text' }));
        // The chapter's own title is the panel's: its text starts under it.
        this.body = headingsDown(this.text.replace(/^#[^\n]*\n+/, ''), 1);
      }
    } catch {
      this.chapter = null;
    }
    this.loading = false;
  }

  /** The renderer is done: the headings get their addresses, and the
   *  one the page asked for is brought to the top. */
  rendered(): void {
    const page = this.reading?.nativeElement;
    if (!page || !this.chapter) return;
    addressHeadings(page, headingsOf(this.text), this.chapter.id);
    // Once the panel has slid in: before that there is nowhere to scroll.
    const heading = this.data.heading;
    if (heading) setTimeout(() => document.getElementById(heading)?.scrollIntoView({ block: 'start' }), 250);
  }

  close(): void {
    this.dialogRef.close();
  }
}
