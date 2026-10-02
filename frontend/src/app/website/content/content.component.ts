import { Component, ElementRef, OnInit, OnDestroy, ViewChild } from '@angular/core';
import {
  NavigationCancel,
  NavigationEnd,
  NavigationError,
  NavigationStart,
  Router,
} from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';

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

  /** True while a route navigation is in flight (drives the progress bar). */
  navigating = false;

  constructor(private router: Router) {}

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
          if (event instanceof NavigationEnd) {
            // This element, rather than the window, owns page scrolling.
            // Route navigation must not carry a previous page's offset into
            // the next page and make its first rows appear to be missing.
            this.pageContainer.nativeElement.scrollTo({ top: 0, left: 0 });
          }
        }),
    );
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }
}
