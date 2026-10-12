import {
  afterNextRender,
  Component,
  ElementRef,
  Injector,
  OnDestroy,
  OnInit,
  ViewChild,
} from '@angular/core';
import { Title } from '@angular/platform-browser';
import {
  NavigationCancel,
  NavigationEnd,
  NavigationError,
  NavigationStart,
  Router,
} from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';
import { TITLE_SUFFIX } from 'src/app/title.strategy';

/** A page's address without its query and fragment. */
function pathOf(url: string): string {
  return url.split(/[?#]/)[0];
}

/**
 * The frame every page is shown in. It owns the page's scrolling, so it
 * does for it what the browser does for a window: a new page opens at
 * its top, Back and Forward return to where the person was. It also
 * tells a keyboard and a screen reader that the page changed, by moving
 * the focus to the page's title and announcing its name.
 */
@Component({
  selector: 'app-content',
  templateUrl: './content.component.html',
  styleUrls: ['./content.component.css'],
  standalone: false,
})
export class ContentComponent implements OnInit, OnDestroy {
  @ViewChild('pageContainer', { static: true })
  private pageContainer!: ElementRef<HTMLElement>;

  private readonly subscriptions = new Subscription();

  /** The live region that says the page's name after a navigation. */
  @ViewChild('announcer', { static: true })
  private announcer!: ElementRef<HTMLElement>;

  /** True while a route navigation is in flight (drives the progress bar). */
  navigating = false;

  /** Where each visited page was scrolled to, by its navigation's id. */
  private readonly scrolledTo = new Map<number, number>();
  /** The id of the navigation that brought the page now shown. */
  private shownId = 0;
  private shownPath = '';
  /** The earlier navigation Back or Forward is returning to, if any. */
  private returningTo: number | null = null;
  /** Watches a page that is still growing toward the place to return to. */
  private growth?: ResizeObserver;

  constructor(
    private router: Router,
    private title: Title,
    private injector: Injector,
  ) {}

  ngOnInit(): void {
    this.subscriptions.add(
      this.router.events
        .pipe(
          filter(
            (event) =>
              event instanceof NavigationStart ||
              event instanceof NavigationEnd ||
              event instanceof NavigationCancel ||
              event instanceof NavigationError,
          ),
        )
        .subscribe((event) => {
          this.navigating = event instanceof NavigationStart;
          if (event instanceof NavigationStart) {
            this.growth?.disconnect();
            this.scrolledTo.set(this.shownId, this.pageContainer.nativeElement.scrollTop);
            this.returningTo = event.restoredState?.navigationId ?? null;
          }
          if (event instanceof NavigationEnd) {
            this.onArrived(event);
          }
        }),
    );
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
    this.growth?.disconnect();
  }

  private onArrived(event: NavigationEnd): void {
    const top = this.returningTo === null ? 0 : (this.scrolledTo.get(this.returningTo) ?? 0);
    const path = pathOf(event.urlAfterRedirects);
    // The first page is not a change, and a new query on the same page
    // (a filter, an open row) is not a new page.
    const pageChanged = this.shownId !== 0 && path !== this.shownPath;
    this.shownId = event.id;
    this.shownPath = path;

    // Once the page is drawn: its title exists to be focused, and it is
    // tall enough to be scrolled back to where it was.
    afterNextRender(
      () => {
        const container = this.pageContainer.nativeElement;
        this.scrollTo(top);
        if (!pageChanged) return;
        this.announcer.nativeElement.textContent = this.title.getTitle().replace(TITLE_SUFFIX, '');
        const heading = container.querySelector<HTMLElement>('h1') ?? container;
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      },
      { injector: this.injector },
    );
  }

  /** Scrolls the page to `top`. A page that loads what makes it tall
   *  cannot be scrolled that far yet, so it is followed as it grows,
   *  for a few seconds, and scrolled once it can be. */
  private scrollTo(top: number): void {
    const container = this.pageContainer.nativeElement;
    const arrived = () => {
      container.scrollTo({ top, left: 0 });
      return container.scrollTop >= top - 1;
    };
    if (arrived()) return;
    const growth = new ResizeObserver(() => {
      if (arrived()) growth.disconnect();
    });
    this.growth = growth;
    growth.observe(container.querySelector('.content') ?? container);
    setTimeout(() => growth.disconnect(), 3000);
  }
}
