import { inject, Type } from '@angular/core';
import { CanActivateFn, Router, Routes } from '@angular/router';
// What the platform itself is configured with, and what a person has
// configured of it: one frame (the shell) and a page per concern. The
// pages, their names and the actions that open them are listed once,
// in website/config/config.ts.
import { ApiKeysComponent } from '../../pages/app/settings/api-keys/api-keys.component';
import { ConnectedAppsComponent } from '../../pages/app/settings/connected-apps/connected-apps.component';
import { MemoryComponent } from '../../pages/app/settings/memory/memory.component';
import { SafetyComponent } from '../../pages/app/settings/safety/safety.component';
import { SettingsComponent } from '../../pages/app/settings/settings.component';
import { SettingsShellComponent } from '../../pages/app/settings/settings-shell.component';
import { permissionGuard } from '../../guards/permission.guard';
import { unsavedChangesGuard } from '../../guards/unsaved-changes.guard';
import { AuthService } from '../../services/auth.service';
import { firstVisiblePage, settingsPages } from '../../website/config/config';

/**
 * /settings itself is no page: it opens the first page the person may
 * open. With none, they are sent where anyone without a page is.
 */
const settingsLandingGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  await auth.ensureLoaded();

  const page = settingsPages.find((candidate) => auth.can(candidate.action));
  if (page) {
    return router.parseUrl(`/settings/${page.path}`);
  }
  return router.parseUrl(firstVisiblePage((action) => auth.can(action)) || 'no-access');
};

/** The component of each page, by the page's path. */
const pageComponents: Record<string, Type<unknown>> = {
  chat: SettingsComponent,
  notifications: SettingsComponent,
  memory: MemoryComponent,
  keys: ApiKeysComponent,
  providers: SettingsComponent,
  speech: SettingsComponent,
  routing: SettingsComponent,
  apps: ConnectedAppsComponent,
  safety: SafetyComponent,
};

export const SettingsRoutes: Routes = [
  // Monitoring and Audit were tabs here; they are pages of their own.
  { path: 'monitoring', redirectTo: '/monitoring', pathMatch: 'full' },
  { path: 'audit', redirectTo: '/audit', pathMatch: 'full' },
  { path: 'audit/org', redirectTo: '/audit/org', pathMatch: 'full' },
  {
    path: '',
    component: SettingsShellComponent,
    children: [
      { path: '', pathMatch: 'full', canActivate: [settingsLandingGuard], children: [] },
      ...settingsPages.map((page) => ({
        path: page.path,
        title: page.name,
        component: pageComponents[page.path],
        canActivate: [permissionGuard],
        canDeactivate: [unsavedChangesGuard],
        data: { requiredAction: page.action },
      })),
    ],
  },
];
