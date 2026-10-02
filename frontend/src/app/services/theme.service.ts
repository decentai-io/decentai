import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export type AppTheme = 'light' | 'dark';

// The same key is read by src/theme-init.js, before the app loads.
const STORAGE_KEY = 'decentai.theme';

/**
 * Applies the app theme by toggling the `.dark` class on <html> (shadcn /
 * spartan convention — Tailwind's dark variant and all design tokens key
 * off it). The data-md-theme attribute is kept in sync for the remaining
 * Material-based components during the migration.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly themeSubject = new BehaviorSubject<AppTheme>(
    this.resolveInitialTheme(),
  );

  readonly theme$ = this.themeSubject.asObservable();

  constructor() {
    this.apply(this.themeSubject.value);
  }

  get theme(): AppTheme {
    return this.themeSubject.value;
  }

  toggle(): void {
    this.set(this.theme === 'dark' ? 'light' : 'dark');
  }

  set(theme: AppTheme): void {
    localStorage.setItem(STORAGE_KEY, theme);
    this.apply(theme);
    this.themeSubject.next(theme);
  }

  private apply(theme: AppTheme): void {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    document.documentElement.setAttribute('data-md-theme', theme);
  }

  private resolveInitialTheme(): AppTheme {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') {
      return saved;
    }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light';
  }
}
