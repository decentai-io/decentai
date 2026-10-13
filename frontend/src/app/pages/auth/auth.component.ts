import { Component, OnInit } from '@angular/core';

import { AuthService } from '../../services/auth.service';
import { DataStoreService } from '../../services/datastore.service';
import { NavigatorService } from '../../services/navigator.service';

type Mode = 'login' | 'invitation' | 'forgot' | 'reset' | 'first' | 'entering';

/**
 * Signing in is one step: email and password. One organization per
 * deployment, and no signup — the first administrator is seeded at launch,
 * everyone else arrives through an invitation link.
 *
 * The other modes are how those links land: `invitation` and `reset` are
 * opened from emailed URLs, `forgot` asks for a reset link.
 *
 * `entering` is a sign-in handed to the page: whoever set the install
 * up on a person's own computer (bootstrap/setup.py) opens it at
 * `#enter=…`, an email and a password in the part of an address that is
 * never sent anywhere, and the page signs in with them through the same
 * door as a person typing them.
 */
@Component({
  selector: 'app-auth',
  templateUrl: './auth.component.html',
  styleUrls: ['./auth.component.css'],
  standalone: false,
})
export class AuthComponent implements OnInit {
  mode: Mode = 'login';

  email = '';
  password = '';
  /** Keep the sign-in on this device. What was chosen last is offered
   *  again, with the email it was chosen for. */
  remember = false;
  name = '';
  orgName = '';

  /** Set when the page was opened from an invitation link. */
  inviteToken = '';
  inviteValid = false;
  checkingInvite = false;

  /** Set when the page was opened from a password-reset link. */
  resetToken = '';
  resetValid = false;
  checkingReset = false;

  /** After asking for a reset: what to tell them, and the link when there is
   * no email service to send it. */
  forgotMessage = '';
  /** Whether a link was sent, or the message says who to ask instead. */
  private forgotSent = true;

  busy = false;
  /** What the server refused, said in one block above the button. */
  error = '';
  /** The refusal is a lockout, not a wrong password. */
  throttled = false;
  /** The button was pressed: what a field still needs is said under it. */
  tried = false;
  /** The password is shown as typed. */
  shown = false;

  constructor(
    private auth: AuthService,
    private datastore: DataStoreService,
    private navigator: NavigatorService,
  ) {}

  ngOnInit(): void {
    const handed = this.handedSignIn();
    if (handed) {
      this.enter(handed.email, handed.password);
      return;
    }

    const params = new URLSearchParams(window.location.search);

    const invite = params.get('invite');
    if (invite) {
      this.openInvitation(invite);
      return;
    }

    const reset = params.get('reset');
    if (reset) {
      this.openReset(reset);
      return;
    }

    this.recallChoice();
  }

  // ------------------------------------------------------------------
  // A sign-in handed over
  // ------------------------------------------------------------------

  /** The sign-in in the address's fragment, if there is one — and out
   *  of the address before anything else is done with it: it is a
   *  password, and must not stay where a history could keep it. */
  private handedSignIn(): { email: string; password: string } | null {
    const found = /^#enter=([A-Za-z0-9_-]+)$/.exec(window.location.hash);
    if (!found) {
      return null;
    }
    if (window.history?.replaceState) {
      window.history.replaceState(
        {}, '', window.location.pathname + window.location.search);
    }
    try {
      const bytes = Uint8Array.from(
        atob(found[1].replace(/-/g, '+').replace(/_/g, '/')),
        (letter) => letter.charCodeAt(0));
      const said = JSON.parse(new TextDecoder().decode(bytes));
      const email = String(said?.email || '');
      const password = String(said?.password || '');
      return email && password ? { email, password } : null;
    } catch {
      return null;
    }
  }

  /** Sign in with what was handed over. Where it is refused the form
   *  is shown, with the reason, for a person to sign in themselves. */
  private async enter(email: string, password: string): Promise<void> {
    this.mode = 'entering';
    this.busy = true;
    try {
      const result = await this.auth.login({ email, password, remember: true });
      if (result.ok) {
        this.enterApp();
        return;
      }
      this.error = result.error || 'That sign-in was not accepted.';
    } catch {
      this.error = 'DecentAI could not be reached.';
    } finally {
      this.busy = false;
    }
    this.email = email;
    this.mode = 'login';
  }

  // ------------------------------------------------------------------
  // Login
  // ------------------------------------------------------------------

  async signIn(): Promise<void> {
    this.tried = true;
    this.error = '';
    if (this.emailProblem || !this.password) return;

    this.busy = true;
    this.throttled = false;

    try {
      const result = await this.auth.login({
        email: this.email.trim(),
        password: this.password,
        remember: this.remember,
      });

      if (result.ok) {
        this.keepChoice();
        this.enterApp();
        return;
      }
      if (result.changeRequired) {
        // Held until their own is chosen: it is asked for again then.
        this.handedPassword = this.password;
        this.password = '';
        this.tried = false;
        this.mode = 'first';
        return;
      }
      this.throttled = !!result.throttled;
      this.error = result.error || 'Email or password is incorrect.';
    } finally {
      this.busy = false;
    }
  }

  // ------------------------------------------------------------------
  // Forgotten password
  // ------------------------------------------------------------------

  /** The password an administrator handed over, between signing in
   *  with it and choosing one's own. */
  private handedPassword = '';

  async chooseOwnPassword(): Promise<void> {
    this.tried = true;
    this.error = '';
    if (this.passwordProblem) return;

    this.busy = true;

    try {
      const result = await this.auth.firstPassword({
        email: this.email.trim(),
        current_password: this.handedPassword,
        new_password: this.password,
        remember: this.remember,
      });

      if (result.ok) {
        this.handedPassword = '';
        this.keepChoice();
        this.enterApp();
        return;
      }

      this.error = result.error || 'Could not set your password.';
    } finally {
      this.busy = false;
    }
  }

  /** The key the remembered email is kept under on this device. */
  private static readonly REMEMBERED = 'decentai.remembered-email';

  /** Offer again what was chosen at the last sign-in on this device. */
  private recallChoice(): void {
    try {
      const email = localStorage.getItem(AuthComponent.REMEMBERED) || '';
      if (email && !this.email) {
        this.email = email;
        this.remember = true;
      }
    } catch {
      // Storage that cannot be read: the form starts empty, as it did.
    }
  }

  private keepChoice(): void {
    try {
      if (this.remember) {
        localStorage.setItem(AuthComponent.REMEMBERED, this.email.trim());
      } else {
        localStorage.removeItem(AuthComponent.REMEMBERED);
      }
    } catch {
      // Nothing is lost but the convenience.
    }
  }

  startForgot(): void {
    this.mode = 'forgot';
    this.clearForm();
    this.forgotMessage = '';
  }

  /** A mode starts clean: nothing refused, nothing pressed, no password. */
  private clearForm(): void {
    this.error = '';
    this.throttled = false;
    this.tried = false;
    this.shown = false;
    this.password = '';
  }

  async requestReset(): Promise<void> {
    this.tried = true;
    this.error = '';
    if (this.emailProblem) return;

    this.busy = true;

    try {
      const result = await this.auth.forgotPassword(this.email.trim());

      if (!result.ok) {
        this.error = result.error || 'Could not send a reset link.';
        return;
      }

      // Deliberately the same words whether or not the account exists.
      this.forgotMessage = result.message ?? '';
      this.forgotSent = result.sent !== false;
    } finally {
      this.busy = false;
    }
  }

  /** Resolve a reset link before asking for a new password. */
  private async openReset(token: string): Promise<void> {
    this.mode = 'reset';
    this.resetToken = token;
    this.checkingReset = true;

    try {
      const result = await this.auth.resetInfo(token);

      if (result.ok) {
        this.resetValid = true;
        this.email = result.email ?? '';
        this.orgName = result.orgName ?? '';
      } else {
        this.resetValid = false;
        this.error = result.error ?? '';
      }
    } finally {
      this.checkingReset = false;
    }
  }

  async setNewPassword(): Promise<void> {
    this.tried = true;
    this.error = '';
    if (this.passwordProblem) return;

    this.busy = true;

    try {
      const result = await this.auth.resetPassword({
        token: this.resetToken,
        password: this.password,
      });

      if (result.ok) {
        this.enterApp();
        return;
      }

      this.error = result.error || 'Could not set your password.';
    } finally {
      this.busy = false;
    }
  }

  /** Leave a reset behind and sign in normally. */
  backToLogin(): void {
    this.clearTokenFromUrl();
    this.resetToken = '';
    this.resetValid = false;
    this.handedPassword = '';
    this.clearForm();
    this.forgotMessage = '';
    this.mode = 'login';
  }

  // ------------------------------------------------------------------
  // Invitation
  // ------------------------------------------------------------------

  /**
   * Resolve the link before showing a form, so the screen can name the
   * organization and the invited address instead of asking for details the
   * invitation already carries.
   */
  private async openInvitation(token: string): Promise<void> {
    this.mode = 'invitation';
    this.inviteToken = token;
    this.checkingInvite = true;

    try {
      const result = await this.auth.invitation(token);

      if (result.ok) {
        this.inviteValid = true;
        this.email = result.email ?? '';
        this.orgName = result.orgName ?? '';
      } else {
        this.inviteValid = false;
        this.error = result.error ?? '';
      }
    } finally {
      this.checkingInvite = false;
    }
  }

  async acceptInvitation(): Promise<void> {
    this.tried = true;
    this.error = '';
    if (!this.name.trim() || this.passwordProblem) return;

    this.busy = true;

    try {
      const result = await this.auth.acceptInvitation({
        token: this.inviteToken,
        name: this.name.trim(),
        password: this.password,
      });

      if (result.ok) {
        this.enterApp();
        return;
      }

      this.error = result.error || 'Could not accept this invitation.';
    } finally {
      this.busy = false;
    }
  }

  /**
   * Leave the invitation behind.
   *
   * Whoever is already signed in on this browser goes back to the app;
   * everyone else gets the sign-in form.
   */
  async dismissInvitation(): Promise<void> {
    this.clearTokenFromUrl();
    this.inviteToken = '';
    this.inviteValid = false;
    this.clearForm();
    this.name = '';
    this.email = '';

    if (await this.auth.refresh()) {
      this.enterApp();
      return;
    }

    this.mode = 'login';
  }

  /** Drop an invitation or reset token from the address bar: both are live
   * credentials and must not linger in history or a referrer header. */
  private clearTokenFromUrl(): void {
    if (window.history?.replaceState) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }

  // ------------------------------------------------------------------
  // Presentation helpers
  // ------------------------------------------------------------------

  /** What the email box still needs; '' when it reads as an address. */
  get emailProblem(): string {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(this.email.trim()) ? '' : 'Enter a valid email address.';
  }

  /** The rule a chosen password is held to, each part with whether
   *  what is typed meets it: the server checks the same. */
  get passwordRule(): { words: string; met: boolean }[] {
    return [
      { words: 'At least 10 characters', met: this.password.length >= 10 },
      { words: 'A letter', met: /[A-Za-z]/.test(this.password) },
      { words: 'A number', met: /\d/.test(this.password) },
    ];
  }

  get passwordProblem(): string {
    if (!this.password) return 'Choose a password.';
    return this.passwordRule.every((part) => part.met) ? '' : 'It does not meet the rule below yet.';
  }

  get title(): string {
    if (this.mode === 'entering') {
      return 'Opening DecentAI…';
    }
    if (this.mode === 'invitation') {
      if (this.checkingInvite) {
        return 'Checking your invitation…';
      }
      return this.inviteValid ? `Join ${this.orgName}` : 'Invitation problem';
    }
    if (this.mode === 'reset') {
      if (this.checkingReset) {
        return 'Checking your link…';
      }
      return this.resetValid ? 'Choose a new password' : 'Link problem';
    }
    if (this.mode === 'forgot') {
      // What happened: a link on its way, or who to ask where this
      // install sends none.
      if (this.forgotMessage) return this.forgotSent ? 'Check your email' : 'Ask your administrator';
      return 'Reset your password';
    }
    if (this.mode === 'first') {
      return 'Choose your own password';
    }
    return 'Sign in';
  }

  get subtitle(): string {
    if (this.mode === 'entering') {
      return '';
    }
    if (this.mode === 'invitation') {
      if (this.checkingInvite) {
        return '';
      }
      return this.inviteValid
        ? `Set a password for ${this.email}`
        : this.error || 'Ask whoever invited you to send a new link.';
    }
    if (this.mode === 'reset') {
      if (this.checkingReset) {
        return '';
      }
      return this.resetValid
        ? `For ${this.email}${this.orgName ? ' at ' + this.orgName : ''}`
        : this.error || 'Ask for a new link and try again.';
    }
    if (this.mode === 'forgot') {
      return this.forgotMessage
        ? this.forgotMessage
        : 'We will email you a link to set a new one.';
    }
    if (this.mode === 'first') {
      return `The one you signed in with was given to you. Replace it with one only you know, for ${this.email}.`;
    }
    return 'Enter your email and password.';
  }

  // ------------------------------------------------------------------

  private enterApp(): void {
    this.datastore.setview('app');
    this.navigator.navigateToInitialRoute();

    // Strip the token AFTER navigating, not before: the router re-serialises
    // its own URL tree, which still carries the query, so clearing it first
    // just gets it written back. A used invitation token must not be left in
    // the address bar, browser history, or the next referrer header.
    setTimeout(() => this.clearTokenFromUrl(), 0);
  }
}
