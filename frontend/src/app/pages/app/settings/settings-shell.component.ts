import { AsyncPipe, NgFor, NgIf } from '@angular/common';
import { Component, OnDestroy } from '@angular/core';
import { MatMenuModule } from '@angular/material/menu';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';
import { PageComponent } from 'src/app/components/ui/page.component';
import { AuthService } from 'src/app/services/auth.service';
import { SettingsLlmService } from 'src/app/services/settings-llm.service';
import { SettingsPage, settingsPages } from 'src/app/website/config/config';

/** A group of the nav with the pages this person may open. */
interface NavGroup {
  name: SettingsPage['group'];
  pages: SettingsPage[];
}

/**
 * The frame of Settings: at its left the pages a person may open, in
 * two groups (their own, the organization's); at its right the open
 * page under its title and one sentence. Each page is a child route
 * (routes/app/settings.routes.ts), so moving between pages keeps this
 * frame as it is. On a narrow screen the nav is a button that opens
 * the same groups as a menu. Model providers carries the number of
 * connections, kept by the service every page lists them through.
 */
@Component({
  selector: 'app-settings-shell',
  standalone: true,
  imports: [
    AsyncPipe, NgFor, NgIf, RouterLink, RouterLinkActive, RouterOutlet, MatMenuModule,
    LucideAngularModule, PageComponent,
  ],
  templateUrl: './settings-shell.component.html',
  styleUrl: './settings-shell.component.css',
})
export class SettingsShellComponent implements OnDestroy {
  /** The groups with something in them for this person. */
  readonly groups: NavGroup[];
  /** The page that is open. */
  current: SettingsPage | undefined;

  private readonly navigation: Subscription;

  constructor(auth: AuthService, private router: Router, readonly llm: SettingsLlmService) {
    const allowed = settingsPages.filter((page) => auth.can(page.action));
    if (auth.can('settings:llm:list') && llm.count$.value === null) void llm.list();
    this.groups = (['You', 'Organization'] as const)
      .map((name) => ({ name, pages: allowed.filter((page) => page.group === name) }))
      .filter((group) => group.pages.length > 0);

    this.readCurrent();
    this.navigation = this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => this.readCurrent());
  }

  ngOnDestroy(): void {
    this.navigation.unsubscribe();
  }

  /** The open page, from the address: /settings/<path>. */
  private readCurrent(): void {
    const path = this.router.url.split(/[?#]/)[0].split('/')[2] ?? '';
    this.current = settingsPages.find((page) => page.path === path);
  }
}
