import {
  TemplateRef,
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
import { ThemeChoice, ThemeService } from 'src/app/services/theme.service';
import { MicrophoneService } from 'src/app/services/microphone.service';
import { WorkSwitchService } from 'src/app/services/work-switch.service';
import { AlertComponent } from 'src/app/components/alert/alert.component';
import { ContentView } from '../config/config.model';
import { adminConsoleActions } from '../config/config';
import { AttentionService } from 'src/app/services/attention.service';
import { ShellStatusService } from 'src/app/services/shell-status.service';
import { TopBarService } from 'src/app/services/top-bar.service';

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

/** The key that saves, as this keyboard calls it. */
const SAVE_KEY = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';

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

  contentView: ContentView = 'main';


  userName = '';
  userEmail = '';
  initials = '?';

  /** Whether the admin console toggle is offered: the admin tier, or any
   *  policy action that unlocks at least one console page. */
  hasAdminConsoleAccess = false;

  themeChoice: ThemeChoice = 'system';
  /** The three themes, as the user menu lists them. */
  readonly themes: { value: ThemeChoice; name: string; icon: string }[] = [
    { value: 'light', name: 'Light', icon: 'sun' },
    { value: 'dark', name: 'Dark', icon: 'moon' },
    { value: 'system', name: 'System', icon: 'monitor' },
  ];

  /** Whether this person has stopped everything of theirs (AI:Switch). */
  workStopped = false;
  /** A stop or a resume is on its way. */
  switching = false;

  /** A newer version of the app has been fetched and waits for a reload. */
  updateReady = false;
  /** The backend answered that nobody is signed in any more. */
  sessionEnded = false;
  offline = false;

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
    readonly topBar: TopBarService,
    private shellStatus: ShellStatusService,
    private attention: AttentionService,
  ) {}

  /** The drawer leaves the tab order while it is closed on a phone.
   *  On a wide screen it is always there: open, or as a rail. */
  get drawerInert(): boolean {
    return this.isMobile && !this.isSidebarOpen;
  }

  /** The person's own page and the Guide: open to whoever is signed in
   *  with the action everybody holds. */
  get canSeeProfile(): boolean {
    return this.auth.can('account:profile:get');
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

    this.subscriptions.add(
      this.theme.choice$.subscribe((choice) => (this.themeChoice = choice)),
    );
    this.subscriptions.add(
      this.workSwitch.stopped$.subscribe((stopped) => (this.workStopped = stopped)),
    );
    this.subscriptions.add(this.topBar.stopAsked.subscribe(() => this.stopEverything()));
    this.subscriptions.add(this.shellStatus.offline$.subscribe((offline) => (this.offline = offline)));
    this.subscriptions.add(this.shellStatus.sessionEnded$.subscribe((ended) => {
      this.sessionEnded = ended;
      // Nothing more is asked of a backend that knows nobody.
      if (ended) this.attention.stop();
    }));
    void this.workSwitch.refresh();

    // The service worker keeps serving the version it has until the page
    // is loaded again. The person is told, and chooses the moment: a
    // reload of its own accord would take a half-written message with it.
    this.subscriptions.add(
      this.updates.versionUpdates
        .pipe(filter((event) => event.type === 'VERSION_READY'))
        .subscribe(() => (this.updateReady = true)),
    );

    this.subscriptions.add(
      this.datastore.currentContentView$.subscribe((view) => {
        queueMicrotask(() => {
          this.contentView = view;
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

  /** The drawer on a phone; on a wide screen, the sidebar or its rail. */
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

  /** The skip link: past the bar and the sidebar, to the page. The
   *  link's address is not followed, since the page is not a route. */
  skipToContent(event: Event): void {
    event.preventDefault();
    const page = document.getElementById('page_container');
    const heading = page?.querySelector<HTMLElement>('h1') ?? page;
    if (!heading) return;
    heading.tabIndex = -1;
    heading.focus();
  }

  setTheme(choice: ThemeChoice): void {
    this.theme.set(choice);
  }

  /** The person's own switch: end everything their chats are doing,
   *  at once, and hold it stopped. Asked first — it cannot be undone
   *  for the work it ends. */
  stopEverything(): void {
    const dialogRef = this.dialog.open(AlertComponent, {
      width: 'auto',
      data: {
        type: 'confirm',
        confirm: 'Stop everything',
        danger: true,
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

  /** The one line under the top bar, the most pressing first. */
  get bar(): 'session' | 'offline' | 'stopped' | 'update' | '' {
    if (this.sessionEnded) return 'session';
    if (this.offline) return 'offline';
    if (this.workStopped) return 'stopped';
    return this.updateReady ? 'update' : '';
  }

  /** The app is loaded again on the address it is at: it finds no
   *  session, shows the sign-in, and comes back to this page after. */
  signInAgain(): void {
    document.location.reload();
  }

  logout(): void {
    const dialogRef = this.dialog.open(AlertComponent, {
      width: 'auto',
      data: {
        type: 'confirm',
        title: 'Sign out?',
        messages: [{ description: 'You will be returned to the sign-in screen.' }],
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

  /** The keys the app answers to, as the sheet lists them. */
  readonly shortcutList: { keys: string[]; does: string }[] = [
    { keys: ['/'], does: 'Go to the page’s search' },
    { keys: ['?'], does: 'Show this list' },
    { keys: ['Esc'], does: 'Close a menu, a dialog or the sidebar' },
    { keys: [SAVE_KEY, 'S'], does: 'Save, on a page with unsaved changes' },
    { keys: ['Enter'], does: 'Send the message, in a chat' },
    { keys: ['Shift', 'Enter'], does: 'Start a new line, in a chat' },
  ];

  @ViewChild('shortcuts') private shortcuts!: TemplateRef<unknown>;

  showShortcuts(): void {
    this.dialog.open(this.shortcuts, { width: '400px', maxWidth: '100vw' });
  }

  /** "/" goes to the page's search and "?" lists the keys, unless the
   *  person is typing somewhere or a dialog is open. */
  private onShortcut(event: KeyboardEvent): void {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target as HTMLElement | null;
    if (target?.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return;
    if (this.dialog.openDialogs.length) return;
    if (event.key === '/') {
      const search = document.querySelector<HTMLInputElement>('main input[type="search"]');
      if (!search) return;
      event.preventDefault();
      search.focus();
    } else if (event.key === '?') {
      event.preventDefault();
      this.showShortcuts();
    }
  }

  /** Keep Tab focus inside the open drawer (a focus trap). */
  @HostListener('document:keydown', ['$event'])
  onDocumentKeydown(event: KeyboardEvent): void {
    this.onShortcut(event);
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
