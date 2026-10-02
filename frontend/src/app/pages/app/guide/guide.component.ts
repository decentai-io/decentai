import { HttpClient } from '@angular/common/http';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription, firstValueFrom } from 'rxjs';

/** One chapter of the guide, as the index names it. */
export interface GuideChapter {
  id: string;
  title: string;
  summary: string;
  file: string;
  section?: string;
}

/**
 * The guide: how to use the platform, chapter by chapter, and how to
 * build an agent for it. The chapters are markdown files shipped with
 * the app (assets/guide), listed by assets/guide/index.json, so the
 * words can be corrected without touching code — and rendered by the
 * same markdown renderer the chat uses, so a table in the guide reads
 * like a table in a reply.
 */
@Component({
  selector: 'app-guide',
  standalone: false,
  templateUrl: './guide.component.html',
  styleUrls: ['../data-shared.css', './guide.component.css'],
})
export class GuideComponent implements OnInit, OnDestroy {
  loading = true;
  chapters: GuideChapter[] = [];
  current: GuideChapter | null = null;
  error = '';
  query = '';
  private routeSub?: Subscription;

  constructor(
    private http: HttpClient,
    private route: ActivatedRoute,
    private router: Router,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      const index = await firstValueFrom(
        this.http.get<{ chapters: GuideChapter[] }>('assets/guide/index.json'));
      this.chapters = index?.chapters || [];
    } catch {
      this.error = 'The guide could not be loaded.';
    }
    this.loading = false;
    this.routeSub = this.route.paramMap.subscribe((params) => {
      const id = params.get('chapter') || '';
      this.current = this.chapters.find((c) => c.id === id) || this.chapters[0] || null;
      const scroller = document.querySelector('.guide-content');
      if (scroller) scroller.scrollTop = 0;
    });
  }

  ngOnDestroy(): void {
    this.routeSub?.unsubscribe();
  }

  open(chapter: GuideChapter): void {
    this.router.navigate(['/guide', chapter.id]);
  }

  get src(): string {
    return this.current ? `assets/guide/${this.current.file}` : '';
  }

  get index(): number {
    return this.current ? this.chapters.indexOf(this.current) : -1;
  }

  get previous(): GuideChapter | null {
    return this.index > 0 ? this.chapters[this.index - 1] : null;
  }

  get next(): GuideChapter | null {
    return this.index >= 0 && this.index < this.chapters.length - 1
      ? this.chapters[this.index + 1] : null;
  }

  get filteredChapters(): GuideChapter[] {
    const query = this.query.trim().toLowerCase();
    if (!query) return this.chapters;
    return this.chapters.filter((chapter) =>
      `${chapter.title} ${chapter.summary} ${chapter.section || ''}`.toLowerCase().includes(query));
  }

  get sections(): Array<{ name: string; chapters: GuideChapter[] }> {
    const groups = new Map<string, GuideChapter[]>();
    for (const chapter of this.filteredChapters) {
      const name = chapter.section || 'Learn';
      groups.set(name, [...(groups.get(name) || []), chapter]);
    }
    return Array.from(groups, ([name, chapters]) => ({ name, chapters }));
  }

  chapterNumber(chapter: GuideChapter): number {
    return this.chapters.indexOf(chapter) + 1;
  }
}
