import { Component, OnDestroy, OnInit } from '@angular/core';
import { DataStoreService } from './services/datastore.service';
import { NavigatorService } from './services/navigator.service';
import { Subscription } from 'rxjs';
import { AppView } from './website/config/config.model';
import { AuthService } from './services/auth.service';
import { ThemeService } from './services/theme.service';

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.css'],
  standalone: false,
})
export class AppComponent implements OnInit, OnDestroy {
  private subscriptions = new Subscription();

  view: AppView = 'loading';
  /** The backend has not answered for a while: the loading card says so. */
  unreachable = false;
  /** Which run of asking is the current one: "Try now" starts another. */
  private round = 0;

  constructor(
    public datastore: DataStoreService,
    public navigator: NavigatorService,
    private auth: AuthService,
    private theme: ThemeService, // instantiating applies the saved theme
  ) {}

  ngOnInit(): void {
    this.subscriptions.add(
      this.datastore.view$.subscribe((view) => {
        this.view = view;
      }),
    );

    // An invitation or reset link wins over an existing session. Someone
    // already signed in as one person may well be opening a link addressed to
    // another (a shared machine, or a second organization) — showing them the
    // app instead would swallow the link silently. It matters even more for a
    // reset: the usual reason to follow one is that the session in this
    // browser is not the account you are trying to recover. A sign-up
    // link makes a new organization and signs its administrator in, so
    // it wins for the same reason.
    const params = new URLSearchParams(window.location.search);
    if (params.has('invite') || params.has('reset') || params.has('signup')) {
      this.datastore.setview('auth');
      return;
    }

    // Otherwise resolve the session before showing anything. Without one the
    // sign-in screen takes over the whole shell — there is no sidebar or
    // content to render for someone we cannot identify.
    this.resolveSession();
  }

  /**
   * Decide what to show: the app, or the sign-in screen.
   *
   * A failed refresh has two very different causes, and /status is what
   * tells them apart. An expired session is the user's problem to solve
   * and the sign-in screen is the right answer. A backend that is not
   * answering is not — offering a sign-in form that cannot possibly
   * succeed invites someone to type a password and blame themselves. So
   * an unreachable backend is waited on (a restart takes seconds): in
   * silence for the first tries, then saying so on the loading card,
   * with a way to try at once.
   */
  private async resolveSession(attempt = 0, round = this.round): Promise<void> {
    if (round !== this.round) return;
    if (await this.readSession()) {
      this.datastore.setview('app');
      return;
    }

    const reachable = await this.navigator.checkStatus();
    // "Try now" started a round of its own while this one was asking.
    if (round !== this.round) return;

    // The probe found the session after all, so the read failed for some
    // other reason and nobody is signed out. The app is no use without the
    // permissions that read carries, though: it is asked for again, and
    // the app opens only once it answers.
    if (reachable === 'signed-in' && (await this.readSession())) {
      this.datastore.setview('app');
      return;
    }

    if (reachable !== 'signed-out') {
      this.unreachable = attempt + 1 >= AppComponent.QUIET_TRIES;
      await new Promise((resolve) =>
        setTimeout(resolve, AppComponent.BOOT_RETRY_MS),
      );
      return this.resolveSession(attempt + 1, round);
    }

    // Signed out: the sign-in screen.
    this.datastore.setview('auth');
  }

  /** The loading card's button: ask again now, not in two seconds. */
  tryNow(): void {
    this.round += 1;
    void this.resolveSession(AppComponent.QUIET_TRIES);
  }

  private async readSession(): Promise<boolean> {
    try {
      return await this.auth.refresh();
    } catch {
      return false;
    }
  }

  /** A backend that is restarting is asked again every two seconds;
   *  after three tries the loading card says what it is waiting for. */
  private static readonly QUIET_TRIES = 3;
  private static readonly BOOT_RETRY_MS = 2000;

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }
}
