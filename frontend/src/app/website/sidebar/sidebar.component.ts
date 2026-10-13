import { Component, EventEmitter, Input, OnDestroy, OnInit, Output } from '@angular/core';
import { Subscription } from 'rxjs';

import { AttentionService } from 'src/app/services/attention.service';
import { AuthService } from 'src/app/services/auth.service';
import { DataStoreService } from 'src/app/services/datastore.service';
import { NavigatorService } from '../../services/navigator.service';
import { pageAllowed } from '../config/config';
import { RouteNode } from '../config/config.model';

/**
 * The sidebar: the pages of the workspace being shown (the main one, or
 * the admin console), in their groups, each a link that is lit while
 * the address is on it or under it. A page appears only to whoever may
 * open it, and a group with no page to show is not shown.
 *
 * Collapsed, it is a rail: the same links as icons with their names in
 * tooltips, never nothing.
 */
@Component({
  selector: 'app-sidebar',
  templateUrl: './sidebar.component.html',
  styleUrls: ['./sidebar.component.css'],
  standalone: false,
})
export class SidebarComponent implements OnInit, OnDestroy {
  /** Collapsed to icons. */
  @Input() rail = false;

  @Output() navigationSelected = new EventEmitter<void>();
  @Output() collapseClicked = new EventEmitter<void>();
  @Output() signOutClicked = new EventEmitter<void>();

  private routes: RouteNode[] = [];
  private readonly subscriptions = new Subscription();

  constructor(
    private navigator: NavigatorService,
    private datastore: DataStoreService,
    private auth: AuthService,
    public attention: AttentionService,
  ) {
    this.routes = this.datastore.getRoutes();
    this.attention.start();
  }

  ngOnInit(): void {
    // Switching workspace (main, admin) swaps the pages.
    this.subscriptions.add(
      this.datastore.currentContentView$.subscribe(() => (this.routes = this.datastore.getRoutes())),
    );
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  /** The groups listed in the body; the rest sit in the footer. */
  get sections(): RouteNode[] {
    return this.routes.filter((section) => !section.footer);
  }

  /** The pages beside Sign out: the Guide. */
  get footerPages(): RouteNode[] {
    return this.routes.filter((section) => section.footer).flatMap((section) => this.pagesOf(section));
  }

  /** The pages of a group this person may open. A page that declares
   *  no action is hidden: there is no "public by omission". */
  pagesOf(section: RouteNode): RouteNode[] {
    return (section.children ?? []).filter(
      (page) => page.active && pageAllowed(page, (action) => this.auth.can(action)));
  }

  /** A page's item is lit on the page and on everything under its
   *  address: /settings/speech lights Settings, /audit/org lights Audit.
   *  Where another page's address is under this one's, that page claims
   *  what is under its own: /agents/marketplace is the Marketplace's,
   *  and every other address under /agents is Installed's. */
  isActive(url: string): boolean {
    const { primary, secondary, tertiary } = this.navigator.getCurrentRoute();
    const here = tertiary || secondary || primary;
    const under = (address: string) => here === address || here.startsWith(`${address}/`);
    if (!under(url)) return false;
    return !this.routes.some((section) => (section.children ?? []).some(
      (page) => page.url.length > url.length && under(page.url)));
  }
}
