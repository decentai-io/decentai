import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

/** What a person can choose. */
export type ThemeChoice = 'light' | 'dark' | 'system';
/** What is shown: 'system' resolved against the device. */
export type AppTheme = 'light' | 'dark';

// The same key is read by src/theme-init.js, before the app loads.
const STORAGE_KEY = 'decentai.theme';
const CHOICES: ThemeChoice[] = ['light', 'dark', 'system'];

/**
 * Applies the app theme by toggling the `.dark` class on <html>, which
 * every design token keys off (tailwind.css), and keeps the browser's
 * own chrome (the theme-color meta) on the page's colour. A person who
 * chose nothing, or chose System, follows their device, and follows it
 * again when the device changes at dusk.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly systemDark = window.matchMedia?.('(prefers-color-scheme: dark)');
  private readonly choiceSubject = new BehaviorSubject<ThemeChoice>(this.savedChoice());
  private readonly themeSubject = new BehaviorSubject<AppTheme>(this.resolve(this.choice));

  readonly choice$ = this.choiceSubject.asObservable();
  readonly theme$ = this.themeSubject.asObservable();

  constructor() {
    this.apply();
    this.systemDark?.addEventListener('change', () => this.apply());
  }

  get choice(): ThemeChoice {
    return this.choiceSubject.value;
  }

  get theme(): AppTheme {
    return this.themeSubject.value;
  }

  /** The next choice in the order light, dark, system. */
  toggle(): void {
    this.set(CHOICES[(CHOICES.indexOf(this.choice) + 1) % CHOICES.length]);
  }

  set(choice: ThemeChoice): void {
    localStorage.setItem(STORAGE_KEY, choice);
    this.choiceSubject.next(choice);
    this.apply();
  }

  private apply(): void {
    const theme = this.resolve(this.choice);
    const root = document.documentElement;
    root.classList.toggle('dark', theme === 'dark');
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', getComputedStyle(root).getPropertyValue('--bg').trim());
    if (theme !== this.theme) this.themeSubject.next(theme);
  }

  private resolve(choice: ThemeChoice): AppTheme {
    if (choice !== 'system') return choice;
    return this.systemDark?.matches ? 'dark' : 'light';
  }

  private savedChoice(): ThemeChoice {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved === 'light' || saved === 'dark' ? saved : 'system';
  }
}
