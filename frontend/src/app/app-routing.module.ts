import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { AdminRoutes } from './routes/admin/admin.routes';
import { AgentsRoutes } from './routes/app/agents.routes';
import { AIRoutes } from './routes/app/ai.routes';
import { DataRoutes } from './routes/app/data.routes';
import { SecretsRoutes } from './routes/app/secrets.routes';
import { SettingsRoutes } from './routes/app/settings.routes';
import { GuideComponent } from './pages/app/guide/guide.component';
import { NoAccessComponent } from './website/no-access/no-access.component';
import { PageNotFoundComponent } from './website/page-not-found/page-not-found.component';
import { permissionGuard } from './guards/permission.guard';

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

  // The guide: how to use the platform, chapter by chapter.
  // Open to anyone signed in; a chapter is addressable.
  {
    path: 'guide',
    component: GuideComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'account:profile:get' },
  },
  {
    path: 'guide/:chapter',
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
  { path: 'no-access', component: NoAccessComponent },

  { path: '**', component: PageNotFoundComponent },
];

@NgModule({
  imports: [RouterModule.forRoot(routes, { onSameUrlNavigation: 'reload' })],
  exports: [RouterModule],
})
export class AppRoutingModule {}
