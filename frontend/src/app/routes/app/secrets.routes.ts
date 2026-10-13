import { Routes } from '@angular/router';
// Secrets are practitioner work, not platform administration: the values a
// user stores for agents to act with. The shapes those values take are
// never written here: each is derived from what an installed agent's
// manifest declares, or from a login an agent asked for as it worked.
import { SecretsComponent } from '../../pages/app/secrets/secrets.component';
import { permissionGuard } from '../../guards/permission.guard';

export const SecretsRoutes: Routes = [
  // The page used to live at /secrets/values; links to it still work.
  { path: 'values', redirectTo: '', pathMatch: 'full' },
  {
    path: '',
    title: 'Secrets',
    component: SecretsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'secrets:secret:list' },
    // One secret, opened, is a view of the list's page.
    children: [
      { path: '', pathMatch: 'full', children: [] },
      { path: ':ref', children: [] },
    ],
  },
];
