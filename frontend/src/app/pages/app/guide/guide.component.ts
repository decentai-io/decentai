import { HttpClient } from '@angular/common/http';
import { Component, ElementRef, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { ActivatedRoute, Router } from '@angular/router';
import { MarkdownService } from 'ngx-markdown';
import { Subscription, firstValueFrom } from 'rxjs';

import { headingsDown } from 'src/app/components/ui/markdown-levels';
import { ChatMarkdownService } from '../ai/chat/chat-message/chat-markdown.service';
import { GuideChapter, GuideHeading, GuideMatch, headingsOf, searchGuide } from './guide-text';

/** Chapters that were renamed: an old link still opens the chapter. */
const RENAMED: Record<string, string> = { activity: 'schedules' };

/**
 * The guide: how to use the platform, chapter by chapter. The chapters
 * are markdown files shipped with the app (assets/guide), listed by
 * assets/guide/index.json, so the words can be corrected without
 * touching code, and rendered by the renderer the chat uses.
 *
 * /guide is the book's cover: what it is, a few places to start, and
 * the contents. /guide/<chapter> is a chapter: the contents beside it,
 * its own headings under its name there, each heading with an address
 * (/guide/<chapter>#<heading>) that the pages' "Learn more" links and
 * the search lead to. The search reads the chapters' text, not only
 * their titles.
 */
@Component({
  selector: 'app-guide',
  standalone: false,
  templateUrl: './guide.component.html',
  styleUrl: './guide.component.css',
  providers: [{ provide: MarkdownService, useClass: ChatMarkdownService }],
})
export class GuideComponent implements OnInit, OnDestroy {
  loading = true;
  error = '';
  chapters: GuideChapter[] = [];
  /** Every chapter's text, by its id: read once, for the page and the search. */
  private texts: Record<string, string> = {};

  /** The chapter the address names; null on the cover. */
  current: GuideChapter | null = null;
  /** Its text, with its title one level under the page's own. */
  body = '';
  headings: GuideHeading[] = [];
  /** The heading the reader has reached. */
  reached = '';

  query = '';
  matches: GuideMatch[] = [];

  @ViewChild('reading') private reading?: ElementRef<HTMLElement>;
  private fragment = '';
  private watcher: IntersectionObserver | null = null;
  private address = new Subscription();

  constructor(
    private http: HttpClient,
    private route: ActivatedRoute,
    private router: Router,
    private title: Title,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      const index = await firstValueFrom(this.http.get<{ chapters: GuideChapter[] }>('assets/guide/index.json'));
      this.chapters = index?.chapters || [];
      const texts = await Promise.all(this.chapters.map((chapter) => firstValueFrom(
        this.http.get(`assets/guide/${chapter.file}`, { responseType: 'text' })).catch(() => '')));
      this.chapters.forEach((chapter, at) => (this.texts[chapter.id] = texts[at]));
    } catch {
      this.error = 'The guide could not be opened.';
    }
    this.loading = false;

    this.address.add(this.route.paramMap.subscribe((params) => this.open(params.get('chapter') || '')));
    this.address.add(this.route.fragment.subscribe((fragment) => {
      this.fragment = fragment || '';
      this.retitle();
      this.goToHeading();
    }));
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
    this.watcher?.disconnect();
  }

  // ── The chapter the address names ───────────────────────────────────

  private open(id: string): void {
    if (RENAMED[id]) {
      void this.router.navigate(['/guide', RENAMED[id]], { replaceUrl: true, preserveFragment: true });
      return;
    }
    this.current = this.chapters.find((chapter) => chapter.id === id) || null;
    if (id && !this.current && this.chapters.length) {
      void this.router.navigate(['/guide'], { replaceUrl: true });
      return;
    }
    const text = this.current ? this.texts[this.current.id] || '' : '';
    this.headings = headingsOf(text);
    // The page's title is the guide's; the chapter's is one level under.
    this.body = headingsDown(text, 1);
    this.reached = '';
    this.query = '';
    this.matches = [];
    this.retitle();
  }

  /** The tab says the chapter: set after the router has set the
   *  route's own title, on every move within the guide. */
  private retitle(): void {
    const title = this.current ? `${this.current.title} · Guide · DecentAI` : 'Guide · DecentAI';
    setTimeout(() => this.title.setTitle(title));
  }

  get index(): number {
    return this.current ? this.chapters.indexOf(this.current) : -1;
  }

  get previous(): GuideChapter | null {
    return this.index > 0 ? this.chapters[this.index - 1] : null;
  }

  get next(): GuideChapter | null {
    return this.index >= 0 && this.index < this.chapters.length - 1 ? this.chapters[this.index + 1] : null;
  }

  get sections(): { name: string; chapters: GuideChapter[] }[] {
    const groups = new Map<string, GuideChapter[]>();
    for (const chapter of this.chapters) {
      const name = chapter.section || 'Guide';
      groups.set(name, [...(groups.get(name) || []), chapter]);
    }
    return Array.from(groups, ([name, chapters]) => ({ name, chapters }));
  }

  numberOf(chapter: GuideChapter): number {
    return this.chapters.indexOf(chapter) + 1;
  }

  // ── The chapter, once it is on the page ─────────────────────────────

  /** The renderer is done: each heading gets its address and a link to
   *  it, and the page follows which one the reader has reached. */
  rendered(): void {
    const page = this.reading?.nativeElement;
    if (!page) return;
    const found = Array.from(page.querySelectorAll<HTMLElement>('h3, h4'));
    found.forEach((element, at) => {
      const heading = this.headings[at];
      if (!heading || element.id) return;
      element.id = heading.slug;
      const link = document.createElement('a');
      link.className = 'heading-anchor';
      link.href = `/guide/${this.current?.id}#${heading.slug}`;
      link.setAttribute('aria-label', `Link to “${heading.text}”`);
      link.textContent = '#';
      element.appendChild(link);
    });

    this.watcher?.disconnect();
    // A heading is reached when it passes the top third of the window.
    this.watcher = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) this.reached = entry.target.id;
      }
    }, { rootMargin: '0px 0px -66% 0px' });
    found.forEach((element) => this.watcher?.observe(element));
    this.goToHeading();
  }

  /** The heading the address names, or the chapter's top. */
  private goToHeading(): void {
    const page = this.reading?.nativeElement;
    if (!page) return;
    const target = this.fragment ? document.getElementById(this.fragment) : null;
    if (target) target.scrollIntoView({ block: 'start' });
    else page.closest('.content-shell, main')?.scrollTo({ top: 0 });
  }

  /** A link inside a chapter that leads into the app goes through the
   *  router: following it as an address would load the app again. */
  followed(event: MouseEvent): void {
    const link = (event.target as HTMLElement).closest('a');
    const href = link?.getAttribute('href') || '';
    if (!link || !href.startsWith('/') || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    void this.router.navigateByUrl(href);
  }

  // ── Search ──────────────────────────────────────────────────────────

  search(query: string): void {
    this.query = query;
    this.matches = searchGuide(query, this.chapters, this.texts);
  }
}
