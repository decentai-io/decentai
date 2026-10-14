import { Component, OnDestroy, OnInit } from '@angular/core';

import { AuthService } from '../../services/auth.service';
import { DataStoreService } from '../../services/datastore.service';
import { NavigatorService } from '../../services/navigator.service';

type Mode =
  | 'opening' | 'login' | 'invitation' | 'forgot' | 'reset' | 'first'
  | 'setup' | 'signup' | 'signup-confirm';

/**
 * Signing in is one step: email and password. An address belongs to one
 * account, and a person joins an organization that exists by invitation.
 *
 * `setup` is the first-run page: a deployment nobody is in yet asks the
 * person who opens it to name the organization and become its
 * administrator, once. `signup` asks for an organization of one's own,
 * where the deployment opened it; `signup-confirm` is how its emailed
 * link lands. `invitation` and `reset` are opened from emailed URLs,
 * `forgot` asks for a reset link. `opening` is the moment the page asks
 * which of these it is.
 */
@Component({
  selector: 'app-auth',
  templateUrl: './auth.component.html',
  styleUrls: ['./auth.component.css'],
  standalone: false,
})
export class AuthComponent implements OnInit, OnDestroy {
  mode: Mode = 'opening';

  email = '';
  password = '';
  /** Keep the sign-in on this device. What was chosen last is offered
   *  again, with the email it was chosen for. */
  remember = false;
  name = '';
  orgName = '';

  /** The first-run page asks for the deployment's setup code. */
  codeRequired = false;
  code = '';
  /** A new organization may be asked for from the sign-in page. */
  signupOpen = false;
  /** After asking for one: where the link went. */
  signupMessage = '';
  /** Set when the page was opened from a sign-up link. */
  signupToken = '';
  signupValid = false;
  checkingSignup = false;
  /** The field the server refused, for the error under it. */
  refusedField = '';

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
  /** Seconds until a locked door opens, counted down; 0 when it is open. */
  lockedFor = 0;
  private lockTimer: ReturnType<typeof setInterval> | null = null;
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

    const signup = params.get('signup');
    if (signup) {
      this.openSignup(signup);
      return;
    }

    this.recallChoice();
    this.openDoor();
  }

  // ------------------------------------------------------------------
  // The first run, and sign-up
  // ------------------------------------------------------------------

  /** Which page this is: the first-run page while nobody is in this
   *  deployment, else the sign-in, offering sign-up where it is open. */
  private async openDoor(): Promise<void> {
    const status = await this.auth.setupStatus();
    this.codeRequired = status.codeRequired;
    this.signupOpen = status.signupOpen;
    this.mode = status.setupRequired ? 'setup' : 'login';
  }

  /** What the organization's form still needs, field by field; '' for
   *  each that reads as done. The server checks the same. */
  get orgNameProblem(): string {
    return this.orgName.trim() ? '' : "Enter the organization's name.";
  }

  get nameProblem(): string {
    return this.name.trim() ? '' : 'Enter your name.';
  }

  get codeProblem(): string {
    return !this.codeRequired || this.code.trim() ? '' : 'Enter the setup code.';
  }

  private get organizationFormProblem(): boolean {
    return !!(this.orgNameProblem || this.nameProblem || this.emailProblem
      || this.passwordProblem || (this.mode === 'setup' && this.codeProblem));
  }

  /** The server's refusal of one field, said under that field. */
  refusedFor(field: string): string {
    return this.refusedField === field ? this.error : '';
  }

  /** Name the organization and become its administrator. */
  async setUp(): Promise<void> {
    this.tried = true;
    this.error = '';
    this.refusedField = '';
    if (this.organizationFormProblem) return;

    this.busy = true;
    try {
      const result = await this.auth.setup({
        org_name: this.orgName.trim(),
        name: this.name.trim(),
        email: this.email.trim(),
        password: this.password,
        code: this.codeRequired ? this.code.trim() : undefined,
        remember: true,
      });
      if (result.ok) {
        this.enterApp();
        return;
      }
      this.error = result.error || 'This DecentAI could not be set up.';
      this.refusedField = result.field || '';
      if (result.retryAfter) {
        this.throttled = true;
        this.lockFor(result.retryAfter);
      }
      // Somebody else finished first: what is left is to sign in.
      const status = await this.auth.setupStatus();
      if (!status.setupRequired) {
        this.signupOpen = status.signupOpen;
        this.clearForm();
        this.error = result.error || '';
        this.mode = 'login';
      }
    } finally {
      this.busy = false;
    }
  }

  startSignup(): void {
    this.clearForm();
    this.signupMessage = '';
    this.mode = 'signup';
  }

  /** Ask for an organization: a link goes to the address. */
  async requestSignup(): Promise<void> {
    this.tried = true;
    this.error = '';
    this.refusedField = '';
    if (this.organizationFormProblem) return;

    this.busy = true;
    try {
      const result = await this.auth.signup({
        org_name: this.orgName.trim(),
        name: this.name.trim(),
        email: this.email.trim(),
        password: this.password,
      });
      if (result.ok) {
        this.password = '';
        this.signupMessage = result.message ?? '';
        return;
      }
      this.error = result.error || 'Could not ask for the organization.';
      this.refusedField = result.field || '';
      if (result.retryAfter) {
        this.throttled = true;
        this.lockFor(result.retryAfter);
      }
    } finally {
      this.busy = false;
    }
  }

  /** Resolve a sign-up link before creating anything, so the page can
   *  say what following it makes. */
  private async openSignup(token: string): Promise<void> {
    this.mode = 'signup-confirm';
    this.signupToken = token;
    this.checkingSignup = true;
    try {
      const result = await this.auth.signupInfo(token);
      this.signupValid = result.ok;
      if (result.ok) {
        this.email = result.email ?? '';
        this.orgName = result.orgName ?? '';
      } else {
        this.error = result.error ?? '';
      }
    } finally {
      this.checkingSignup = false;
    }
  }

  async confirmSignup(): Promise<void> {
    this.error = '';
    this.busy = true;
    try {
      const result = await this.auth.confirmSignup(this.signupToken);
      if (result.ok) {
        this.enterApp();
        return;
      }
      this.signupValid = false;
      this.error = result.error || 'This link is invalid or has expired.';
    } finally {
      this.busy = false;
    }
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
      if (result.retryAfter) this.lockFor(result.retryAfter);
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

  ngOnDestroy(): void {
    this.unlock();
  }

  /** The door is shut for this long: the button waits, and the notice
   *  counts the time down. */
  private lockFor(seconds: number): void {
    this.unlock();
    this.lockedFor = seconds;
    this.lockTimer = setInterval(() => {
      this.lockedFor -= 1;
      if (this.lockedFor > 0) return;
      this.unlock();
      this.error = '';
      this.throttled = false;
    }, 1000);
  }

  private unlock(): void {
    if (this.lockTimer) clearInterval(this.lockTimer);
    this.lockTimer = null;
    this.lockedFor = 0;
  }

  /** What is left of a lockout: "4:59". */
  get lockLeft(): string {
    return `${Math.floor(this.lockedFor / 60)}:${String(this.lockedFor % 60).padStart(2, '0')}`;
  }

  /** A mode starts clean: nothing refused, nothing pressed, no password. */
  private clearForm(): void {
    this.unlock();
    this.error = '';
    this.refusedField = '';
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
    this.signupToken = '';
    this.signupValid = false;
    this.handedPassword = '';
    this.clearForm();
    this.forgotMessage = '';
    this.signupMessage = '';
    this.mode = 'login';
    // Whether sign-up is offered is the deployment's to say, and a page
    // opened from a link has not asked yet.
    this.openDoor();
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
    if (this.mode === 'opening') {
      return 'Opening DecentAIâ€¦';
    }
    if (this.mode === 'setup') {
      return 'Set up DecentAI';
    }
    if (this.mode === 'signup') {
      return this.signupMessage ? 'Check your email' : 'Create an organization';
    }
    if (this.mode === 'signup-confirm') {
      if (this.checkingSignup) {
        return 'Checking your linkâ€¦';
      }
      return this.signupValid ? `Create ${this.orgName}` : 'Link problem';
    }
    if (this.mode === 'invitation') {
      if (this.checkingInvite) {
        return 'Checking your invitationâ€¦';
      }
      return this.inviteValid ? `Join ${this.orgName}` : 'Invitation problem';
    }
    if (this.mode === 'reset') {
      if (this.checkingReset) {
        return 'Checking your linkâ€¦';
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
    if (this.mode === 'opening') {
      return '';
    }
    if (this.mode === 'setup') {
      return 'Nobody is in this DecentAI yet. Name your organization and '
        + 'choose how you sign in: you will be its administrator.';
    }
    if (this.mode === 'signup') {
      return this.signupMessage
        ? this.signupMessage
        : 'Your own organization, with you as its administrator. We will '
          + 'email you a link to confirm the address first.';
    }
    if (this.mode === 'signup-confirm') {
      if (this.checkingSignup) {
        return '';
      }
      return this.signupValid
        ? `You will be its administrator, signed in as ${this.email}.`
        : this.error || 'Ask for a new link and try again.';
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
