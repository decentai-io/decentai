import { inject } from '@angular/core';
import { CanActivateFn, Router, Routes } from '@angular/router';
// What the platform itself is configured with — its own module, apart
// from secrets: an LLM connection is the organization's configuration,
// not somebody's credential record.
import { SettingsComponent } from '../../pages/app/settings/settings.component';
import { permissionGuard } from '../../guards/permission.guard';
import { AuthService } from '../../services/auth.service';
import { settingsTabs } from '../../website/config/config';

/**
 * Entering Settings without the first tab's action: go to the first tab
 * the person does hold, rather than be turned away from a page that has
 * something for them. With none, the permission guard that follows
 * answers as it does for any page.
 */
const settingsLandingGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  await auth.ensureLoaded();

  const tab = settingsTabs.find((candidate) => auth.can(candidate.action));
  if (tab && tab.path) {
    return router.parseUrl(`/settings/${tab.path}`);
  }
  return true;
};

export const SettingsRoutes: Routes = [
  {
    path: '',
    component: SettingsComponent,
    canActivate: [settingsLandingGuard, permissionGuard],
    data: { requiredAction: 'settings:llm:list' },
  },
  // The memory tab, addressable: what the assistant remembers about
  // the person is theirs to review from a link as well as a click.
  {
    path: 'memory',
    component: SettingsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'settings:memory:list', tab: 'memory' },
  },
  // How chats behave: the person's own defaults, and the organization's
  // agent routing. Anyone who can open a chat may set their defaults.
  {
    path: 'chat',
    component: SettingsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'ai:chat:create', tab: 'chat' },
  },
  // The person's own API keys, addressable for the same reason.
  {
    path: 'keys',
    component: SettingsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'settings:apikey:list', tab: 'keys' },
  },
  // The organization's provider apps — what an administrator is sent
  // to when a Connect button says nothing is registered yet.
  {
    path: 'apps',
    component: SettingsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'settings:oauth:list', tab: 'apps' },
  },
  // What agents may do without asking: the organization's, and on a
  // desktop the person's own.
  {
    path: 'safety',
    component: SettingsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'settings:safety:get', tab: 'safety' },
  },
  // What agents use and did: read-only, and a grant of its own.
  {
    path: 'monitoring',
    component: SettingsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'agents:monitor:usage', tab: 'monitoring' },
  },
  {
    path: 'audit',
    component: SettingsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'ai:audit:list', tab: 'audit', auditView: 'mine' },
  },
  {
    path: 'audit/org',
    component: SettingsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'ai:audit:list_all', tab: 'audit', auditView: 'org' },
  },
];
