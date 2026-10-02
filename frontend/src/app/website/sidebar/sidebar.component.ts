import {
  Component,
  EventEmitter,
  OnDestroy,
  OnInit,
  Output,
} from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';
import { NavigatorService } from '../../services/navigator.service';
import { DataStoreService } from 'src/app/services/datastore.service';
import { AuthService } from 'src/app/services/auth.service';
import { AttentionService } from 'src/app/services/attention.service';
import { ActiveRoute, RouteNode } from '../config/config.model';
import { pageAllowed } from '../config/config';

@Component({
  selector: 'app-sidebar',
  templateUrl: './sidebar.component.html',
  styleUrls: ['./sidebar.component.css'],
  standalone: false,
})
export class SidebarComponent implements OnInit, OnDestroy {
  @Output() navigationSelected = new EventEmitter<void>();
  @Output() collapseClicked = new EventEmitter<void>();
  @Output() signOutClicked = new EventEmitter<void>();

  /** The sections of the workspace being shown: main, or the admin console. */
  routes: RouteNode[] = [];
  expandedRouteKeys: string[] = [];

  private readonly subscriptions = new Subscription();

  constructor(
    private navigator: NavigatorService,
    private router: Router,
    public datastore: DataStoreService,
    private auth: AuthService,
    public attention: AttentionService,
  ) {
    this.routes = this.datastore.getRoutes();
    this.attention.start();
  }

  /** Active route is owned by the navigator (kept in sync with the URL). */
  get currentRoute(): ActiveRoute {
    return this.navigator.getCurrentRoute();
  }

  ngOnInit(): void {
    this.initExpandedUrls();

    // Workspace switch (main <-> admin) swaps the route set.
    this.subscriptions.add(
      this.datastore.currentContentView$.subscribe(() => {
        this.routes = this.datastore.getRoutes();
        this.initExpandedUrls();
      }),
    );

    // Re-expand the active group after navigation, back/forward or refresh.
    this.subscriptions.add(
      this.router.events
        .pipe(filter((event) => event instanceof NavigationEnd))
        .subscribe(() => this.initExpandedUrls()),
    );
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  private initExpandedUrls(): void {
    this.expandedRouteKeys = [];

    for (const primary of this.routes) {
      for (const secondary of primary.children ?? []) {
        if (
          (secondary.children?.length ?? 0) > 0 &&
          this.isSecondaryActive(secondary.url)
        ) {
          this.expandedRouteKeys.push(
            this.getRouteKey(primary.url, secondary.url),
          );
        }
      }
    }
  }

  /**
   * A navigable page is visible only when its action is granted — there is
   * no "public by omission": a page that declares none is hidden, so the
   * only way to show it is to grant its action from the admin page.
   *
   * Section headers declare nothing and are judged by their children.
   */
  canAccess(routeNode: RouteNode): boolean {
    if (
      routeNode.active &&
      !pageAllowed(routeNode, (action) => this.auth.can(action))
    ) {
      return false;
    }

    // A non-navigable grouping with nothing visible under it disappears too,
    // so a member with a handful of permissions only sees the sections that
    // actually contain something for them.
    if (
      !routeNode.active &&
      (routeNode.children?.length ?? 0) > 0 &&
      this.getVisibleChildCount(routeNode) === 0
    ) {
      return false;
    }

    return true;
  }

  primaryRouteClicked(primaryRoute: any, active: boolean, pageTitle: string) {
    if (!active) {
      return;
    }

    this.navigator.primaryClicked(primaryRoute);
    this.datastore.changePageTitle(pageTitle);
    this.navigationSelected.emit();
  }

  secondaryRouteClicked(
    secondaryRoute: string,
    primaryRoute: string,
    active: boolean,
    pageTitle: string,
    hasChildren: boolean,
  ) {
    if (hasChildren) {
      this.expandSecondaryRoute(secondaryRoute, primaryRoute);
    }

    if (!active) {
      return;
    }

    this.navigator.secondaryClicked(secondaryRoute, primaryRoute);
    this.datastore.changePageTitle(pageTitle);
    this.navigationSelected.emit();
  }

  expandSecondaryRoute(secondaryRoute: string, primaryRoute: string) {
    const routeKey = this.getRouteKey(primaryRoute, secondaryRoute);

    if (this.expandedRouteKeys.includes(routeKey)) {
      this.expandedRouteKeys = this.expandedRouteKeys.filter(
        (url: string) => url !== routeKey,
      );
    } else {
      this.expandedRouteKeys.push(routeKey);
    }
  }

  toggleSecondaryRoute(
    event: Event,
    secondaryRoute: string,
    primaryRoute: string,
  ) {
    event.stopPropagation();
    this.expandSecondaryRoute(secondaryRoute, primaryRoute);
  }

  tertiaryRouteClicked(
    event: Event,
    tertiaryRoute: string,
    secondaryRoute: string,
    primaryRoute: string,
    active: boolean,
    pageTitle: string,
  ) {
    event.stopPropagation();

    if (!active) {
      return;
    }

    this.navigator.tertiaryClicked(tertiaryRoute, secondaryRoute, primaryRoute);
    this.datastore.changePageTitle(pageTitle);
    this.navigationSelected.emit();
  }

  isExpanded(secondaryRoute: string, primaryRoute: string) {
    return this.expandedRouteKeys.includes(
      this.getRouteKey(primaryRoute, secondaryRoute),
    );
  }

  isPrimaryActive(primaryRoute: string): boolean {
    return this.currentRoute?.primary === primaryRoute;
  }

  isSecondaryActive(secondaryRoute: string): boolean {
    return (
      this.currentRoute?.secondary === secondaryRoute ||
      this.currentRoute?.tertiary?.startsWith(`${secondaryRoute}/`)
    );
  }

  isTertiaryActive(tertiaryRoute: string): boolean {
    return this.currentRoute?.tertiary === tertiaryRoute;
  }

  getVisibleChildCount(routeNode: RouteNode): number {
    return (routeNode?.children ?? []).filter((child) =>
      this.canAccess(child),
    ).length;
  }

  private getRouteKey(primaryRoute: string, secondaryRoute: string): string {
    return `${primaryRoute}::${secondaryRoute}`;
  }
}
