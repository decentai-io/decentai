import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

/**
 * What the shell has to say on every page, beside what the person
 * asked for: that their session ended, or that the browser is offline.
 * Each is a line under the top bar while it is true.
 */
@Injectable({ providedIn: 'root' })
export class ShellStatusService {
  /** The backend answered that nobody is signed in any more. The page
   *  stays as it is, so nothing typed is lost; signing in again is the
   *  person's to choose the moment for. */
  readonly sessionEnded$ = new BehaviorSubject<boolean>(false);

  /** The browser says it has no network. */
  readonly offline$ = new BehaviorSubject<boolean>(!navigator.onLine);

  constructor() {
    window.addEventListener('online', () => this.offline$.next(false));
    window.addEventListener('offline', () => this.offline$.next(true));
  }

  sessionEnded(): void {
    if (!this.sessionEnded$.value) this.sessionEnded$.next(true);
  }
}
