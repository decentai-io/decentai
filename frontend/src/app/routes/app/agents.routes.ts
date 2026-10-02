import { Routes } from '@angular/router';
// Managing agents: what is installed and who may use it, and where
// agents come from. Split from `ai` — chats and memory are things a
// person USES; this is how agents are MANAGED.
import { AgentsComponent } from '../../pages/app/agents/agents.component';
import { MarketplaceComponent } from '../../pages/app/agents/marketplace/marketplace.component';
import { permissionGuard } from '../../guards/permission.guard';

export const AgentsRoutes: Routes = [
  { path: '', redirectTo: 'installed', pathMatch: 'full' },
  {
    path: 'installed',
    component: AgentsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'agents:agent:list' },
  },
  {
    // Finding agents and running them are different jobs. Installing is
    // the grant that gates the marketplace; reading the approved set is
    // not enough to go shopping.
    path: 'marketplace',
    component: MarketplaceComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'agents:agent:install' },
  },
];
