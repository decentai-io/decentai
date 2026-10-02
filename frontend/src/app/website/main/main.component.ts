import {
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  OnInit,
  Renderer2,
  ViewChild,
} from '@angular/core';
import {
  NavigationCancel,
  NavigationEnd,
  NavigationError,
  NavigationStart,
  Router,
} from '@angular/router';
import { MatDialog } from '@angular/material/dialog';
import { SwUpdate } from '@angular/service-worker';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';
import { NavigatorService } from '../../services/navigator.service';
import { DataStoreService } from 'src/app/services/datastore.service';
import { AuthService } from 'src/app/services/auth.service';
import { ThemeService } from 'src/app/services/theme.service';
import { MicrophoneService } from 'src/app/services/microphone.service';
import { WorkSwitchService } from 'src/app/services/work-switch.service';
import { AlertComponent } from 'src/app/components/alert/alert.component';
import { ContentView } from '../config/config.model';
import { adminConsoleActions } from '../config/config';

/** Below this width the sidebar becomes a modal drawer (see *.css). */
const MOBILE_QUERY = '(max-width: 900px)';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

@Component({
  selector: 'app-main',
  standalone: false,
  templateUrl: './main.component.html',
  styleUrl: './main.component.css',
})
export class MainComponent implements OnInit, OnDestroy {
  @ViewChild('sidebarShell') private sidebarShell?: ElementRef<HTMLElement>;
  @ViewChild('menuButton') private menuButton?: ElementRef<HTMLButtonElement>;

  isSidebarOpen = false;
  isMobile = false;

  /** Desktop: drawer fully slides away when collapsed (M3 modal-rail). */
  isDrawerCollapsed = false;

  currentPageTitle = '';
  contentView: ContentView = 'main';

  /** Name of the active top-level module, shown as the breadcrumb root. */
  moduleName = '';

  userName = '';
  userEmail = '';
  initials = '?';

  /** Whether the admin console toggle is offered: the admin tier, or any
   *  policy action that unlocks at least one console page. */
  hasAdminConsoleAccess = false;

  isDarkTheme = false;

  /** Whether this person has stopped everything of theirs (AI:Switch). */
  workStopped = false;
  /** A stop or a resume is on its way. */
  switching = false;

  /** A newer version of the app has been fetched and waits for a reload. */
  updateReady = false;

  private readonly subscriptions = new Subscription();
  private readonly collapsedPreferenceKey = 'decentai.sidebar-collapsed';

  /** Timestamp of the last permission re-read, to throttle navigation syncs. */
  private lastPermissionSync = 0;

  private readonly mql = window.matchMedia(MOBILE_QUERY);
  private readonly onMediaChange = (event: MediaQueryListEvent): void =>
    this.applyViewport(event.matches);

  constructor(
    private microphone: MicrophoneService,
    private navigator: NavigatorService,
    private renderer: Renderer2,
    private router: Router,
    private dialog: MatDialog,
    private theme: ThemeService,
    public datastore: DataStoreService,
    private auth: AuthService,
    private workSwitch: WorkSwitchService,
    private updates: SwUpdate,
  ) {}

  /** Off-screen drawer must leave the tab order when closed on mobile. */
  get drawerInert(): boolean {
    return this.isMobile ? !this.isSidebarOpen : this.isDrawerCollapsed;
  }

  /** Content behind the drawer is inert while the modal drawer is open. */
  get contentInert(): boolean {
    return this.isMobile && this.isSidebarOpen;
  }

  ngOnInit(): void {
    this.applyViewport(this.mql.matches);
    this.mql.addEventListener('change', this.onMediaChange);
    this.navigator.navigateToInitialRoute();

    this.isDrawerCollapsed =
      localStorage.getItem(this.collapsedPreferenceKey) === 'true';

    const user = this.datastore.getUserInfo();
    this.userName = user?.name ?? '';
    this.userEmail = (user?.['email'] as string) ?? '';
    this.initials = this.computeInitials(this.userName);
    this.syncAdminAccess();
    this.contentView = this.datastore.getCurrentContentView();
    this.moduleName = this.computeModuleName();

    this.subscriptions.add(
      this.theme.theme$.subscribe((t) => (this.isDarkTheme = t === 'dark')),
    );
    this.subscriptions.add(
      this.workSwitch.stopped$.subscribe((stopped) => (this.workStopped = stopped)),
    );
    void this.workSwitch.refresh();

    // The service worker keeps serving the version it has until the page
    // is loaded again. The person is told, and chooses the moment: a
    // reload of its own accord would take a half-written message with it.
    this.subscriptions.add(
      this.updates.versionUpdates
        .pipe(filter((event) => event.type === 'VERSION_READY'))
        .subscribe(() => (this.updateReady = true)),
    );

    // Title/breadcrumb emissions fire synchronously inside navigation
    // (mid change-detection); defer a microtask to avoid NG0100.
    this.subscriptions.add(
      this.datastore.currentPageTitle$.subscribe((title) => {
        queueMicrotask(() => (this.currentPageTitle = title));
      }),
    );

    this.subscriptions.add(
      this.datastore.currentContentView$.subscribe((view) => {
        queueMicrotask(() => {
          this.contentView = view;
          this.moduleName = this.computeModuleName();
        });
      }),
    );

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
          if (!(event instanceof NavigationStart)) {
            // currentRoute is synced by the navigator once navigation ends.
            this.moduleName = this.computeModuleName();
          }
          if (event instanceof NavigationEnd) {
            // Re-read permissions as the user moves around, so a role change
            // made elsewhere takes effect without a manual reload.
            this.syncPermissions();
          }
        }),
    );
  }

  /** Re-read the session's permissions, throttled so ordinary navigation does
   *  not hammer the endpoint, then refresh what depends on them. */
  private syncPermissions(): void {
    const now = Date.now();
    if (now - this.lastPermissionSync < 10_000) {
      return;
    }
    this.lastPermissionSync = now;
    this.auth.refresh().then(() => this.syncAdminAccess());
  }

  /** Whether the admin-console toggle is offered: the admin tier, or any
   *  policy action that unlocks at least one console page. */
  private syncAdminAccess(): void {
    // Reachable iff at least one console page is granted — no tiers, no
    // hardcoded roles: purely what policy resolved for this session.
    this.hasAdminConsoleAccess = adminConsoleActions.some((action) =>
      this.auth.can(action),
    );
  }

  ngOnDestroy(): void {
    this.mql.removeEventListener('change', this.onMediaChange);
    this.subscriptions.unsubscribe();
    this.unlockBodyScroll();
  }

  /** Top-bar menu button: modal drawer on mobile, collapse on desktop. */
  toggleMenu(): void {
    if (this.isMobile) {
      this.isSidebarOpen ? this.closeSidebar() : this.openSidebar();
      return;
    }
    this.isDrawerCollapsed = !this.isDrawerCollapsed;
    localStorage.setItem(
      this.collapsedPreferenceKey,
      String(this.isDrawerCollapsed),
    );
  }

  toggleTheme(): void {
    this.theme.toggle();
  }

  /** The person's own switch: end everything their chats are doing,
   *  at once, and hold it stopped. Asked first — it cannot be undone
   *  for the work it ends. */
  stopEverything(): void {
    const dialogRef = this.dialog.open(AlertComponent, {
      width: 'auto',
      data: {
        type: 'confirm',
        title: 'Stop everything?',
        messages: [{
          description:
            'Every chat of yours stops at once: running work, helpers, '
            + 'browsers, questions waiting on you and scheduled runs. '
            + 'Nothing starts again until you resume.',
        }],
      },
    });
    dialogRef.afterClosed().subscribe(async (result) => {
      if (!result?.confirmed) {
        return;
      }
      this.switching = true;
      const outcome = await this.workSwitch.stop();
      this.switching = false;
      if (outcome.error) {
        this.dialog.open(AlertComponent, {
          width: 'auto',
          data: { type: 'error', title: 'Not stopped',
                  messages: [{ description: outcome.error }] },
        });
      }
    });
  }

  async resumeEverything(): Promise<void> {
    this.switching = true;
    const outcome = await this.workSwitch.resume();
    this.switching = false;
    if (outcome.error) {
      this.dialog.open(AlertComponent, {
        width: 'auto',
        data: { type: 'error', title: 'Not resumed',
                messages: [{ description: outcome.error }] },
      });
    }
  }

  reloadForUpdate(): void {
    document.location.reload();
  }

  logout(): void {
    const dialogRef = this.dialog.open(AlertComponent, {
      width: 'auto',
      data: {
        type: 'confirm',
        title: 'Log out?',
        messages: [{ description: "You'll be returned to the login screen." }],
      },
    });

    dialogRef.afterClosed().subscribe((result) => {
      if (result?.confirmed) {
        // The microphone kept for the session goes with the session.
        this.microphone.dispose();
        // navigator.logout() clears the datastore on success.
        this.navigator.logout();
      }
    });
  }

  changecurrentContentView(): void {
    this.datastore.changecurrentContentView();
    this.navigator.changecurrentContentView();
  }

  openSidebar(): void {
    if (this.isSidebarOpen) {
      return;
    }
    this.isSidebarOpen = true;
    // Prevent the page behind the drawer from scrolling on mobile.
    this.renderer.setStyle(document.body, 'overflow', 'hidden');
    this.focusDrawer();
  }

  closeSidebar(): void {
    if (!this.isSidebarOpen) {
      return;
    }
    this.isSidebarOpen = false;
    this.unlockBodyScroll();
    // Return focus to the control that opened the drawer.
    this.menuButton?.nativeElement.focus();
  }

  @HostListener('window:keydown.escape')
  closeSidebarOnEscape(): void {
    this.closeSidebar();
  }

  /** Returning to the tab is exactly when a permission change made while the
   *  user was away should catch up, so re-read on becoming visible. */
  @HostListener('document:visibilitychange')
  onVisibilityChange(): void {
    if (document.visibilityState === 'visible') {
      this.syncPermissions();
    }
  }

  /** Keep Tab focus inside the open drawer (a focus trap). */
  @HostListener('document:keydown', ['$event'])
  onDocumentKeydown(event: KeyboardEvent): void {
    if (!this.isSidebarOpen || !this.isMobile || event.key !== 'Tab') {
      return;
    }

    const drawer = this.sidebarShell?.nativeElement;
    if (!drawer) {
      return;
    }

    const focusable = Array.from(
      drawer.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
    ).filter((el) => el.offsetParent !== null);

    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement as HTMLElement | null;
    const inDrawer = !!active && drawer.contains(active);

    if (event.shiftKey) {
      if (!inDrawer || active === first) {
        event.preventDefault();
        last.focus();
      }
    } else if (!inDrawer || active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /** A chat on a phone has its own header with the way back: the
   *  breadcrumb above it would be a third bar saying the same thing. */
  get pageBarHidden(): boolean {
    return this.isMobile && /\/ai\/chats\/[^/?#]+/.test(this.router.url);
  }

  private applyViewport(isMobile: boolean): void {
    this.isMobile = isMobile;
    // Leaving mobile while the drawer was open must not strand the scroll lock.
    if (!isMobile && this.isSidebarOpen) {
      this.isSidebarOpen = false;
      this.unlockBodyScroll();
    }
  }

  private focusDrawer(): void {
    // Defer so the drawer is rendered/visible before moving focus into it.
    setTimeout(() => {
      const drawer = this.sidebarShell?.nativeElement;
      const first = drawer?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      first?.focus();
    });
  }

  private unlockBodyScroll(): void {
    this.renderer.removeStyle(document.body, 'overflow');
  }

  /** The sidebar section the active page sits in. Sections share a primary
   *  url — every admin group is 'admin' — so matching on that alone would
   *  always name the first one; find the section that actually holds the
   *  page, and fall back to the primary match for anything unlisted. */
  private computeModuleName(): string {
    const route = this.navigator.getCurrentRoute();
    const active = [route.tertiary, route.secondary].filter(Boolean);
    const sections = this.datastore.getRoutes();

    for (const section of sections) {
      for (const page of section.children ?? []) {
        if (active.includes(page.url)) {
          return section.name;
        }
        for (const leaf of page.children ?? []) {
          if (active.includes(leaf.url)) {
            return section.name;
          }
        }
      }
    }
    return sections.find((r) => r.url === route.primary)?.name ?? '';
  }

  private computeInitials(name: string): string {
    const parts = name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) {
      return '?';
    }
    if (parts.length === 1) {
      return parts[0].slice(0, 2).toUpperCase();
    }
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
}
