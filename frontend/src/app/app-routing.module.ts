import { NgModule } from '@angular/core';
import { RouterModule, Routes, TitleStrategy } from '@angular/router';
import { AdminRoutes } from './routes/admin/admin.routes';
import { AgentsRoutes } from './routes/app/agents.routes';
import { AIRoutes } from './routes/app/ai.routes';
import { DataRoutes } from './routes/app/data.routes';
import { SecretsRoutes } from './routes/app/secrets.routes';
import { SettingsRoutes } from './routes/app/settings.routes';
import { AuditComponent } from './pages/app/ai/audit/audit.component';
import { GuideComponent } from './pages/app/guide/guide.component';
import { MonitoringComponent } from './pages/app/monitoring/monitoring.component';
import { NoAccessComponent } from './website/no-access/no-access.component';
import { PageNotFoundComponent } from './website/page-not-found/page-not-found.component';
import { permissionGuard } from './guards/permission.guard';
import { PageTitleStrategy } from './title.strategy';

const routes: Routes = [
  {
    path: '',
    redirectTo: 'ai/chats',
    pathMatch: 'full',
  },

  {
    path: 'ai',
    children: AIRoutes,
  },

  {
    path: 'agents',
    children: AgentsRoutes,
  },

  {
    path: 'data',
    children: DataRoutes,
  },

  {
    path: 'secrets',
    children: SecretsRoutes,
  },

  {
    path: 'settings',
    children: SettingsRoutes,
  },

  // The two records: what agents use and were seen doing, and the
  // permanent trail. Each view has its own address. Monitoring's are
  // children with no component, so the page stays while the view
  // changes; Audit's view is the route's data, which the router binds
  // to the component's input.
  {
    path: 'monitoring',
    title: 'Monitoring',
    component: MonitoringComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'agents:monitor:usage' },
    children: [
      { path: '', pathMatch: 'full', children: [] },
      { path: 'agents', children: [] },
      { path: 'events', children: [] },
      { path: 'connections', children: [] },
    ],
  },
  {
    path: 'audit',
    title: 'Audit',
    component: AuditComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'ai:audit:list', initialView: 'mine' },
  },
  {
    path: 'audit/org',
    title: 'Audit',
    component: AuditComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'ai:audit:list_all', initialView: 'org' },
  },

  // The guide: how to use the platform, chapter by chapter.
  // Open to anyone signed in; a chapter is addressable.
  {
    path: 'guide',
    title: 'Guide',
    component: GuideComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'account:profile:get' },
  },
  {
    path: 'guide/:chapter',
    title: 'Guide',
    component: GuideComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'account:profile:get' },
  },

  {
    path: 'admin',
    children: AdminRoutes,
  },

  // Deliberately unguarded: it exists for the user a guard has
  // nowhere else to send.
  { path: 'no-access', title: 'No access', component: NoAccessComponent },

  { path: '**', title: 'Page not found', component: PageNotFoundComponent },
];

@NgModule({
  imports: [
    RouterModule.forRoot(routes, { onSameUrlNavigation: 'reload', bindToComponentInputs: true }),
  ],
  exports: [RouterModule],
  // Each route's `title` reaches the browser's tab through this.
  providers: [{ provide: TitleStrategy, useClass: PageTitleStrategy }],
})
export class AppRoutingModule {}
