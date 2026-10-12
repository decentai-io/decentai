import { Routes } from '@angular/router';
// Managing agents: what is installed and who may use it, and where
// agents come from. Split from `ai` — chats and memory are things a
// person USES; this is how agents are MANAGED.
//
// Every view has an address. The views of one page are children with
// no component, so the page stays, with what it has loaded, while the
// view changes; the page reads which one from the address.
import { AgentsComponent } from '../../pages/app/agents/agents.component';
import { MarketplaceComponent } from '../../pages/app/agents/marketplace/marketplace.component';
import { permissionGuard } from '../../guards/permission.guard';

export const AgentsRoutes: Routes = [
  { path: 'installed', redirectTo: '', pathMatch: 'full' },
  {
    // Finding agents and running them are different jobs. Installing is
    // the grant that gates the marketplace; reading the approved set is
    // not enough to go shopping. Before the installed list's `:ref`,
    // which would take "marketplace" for an agent.
    path: 'marketplace',
    title: 'Marketplace',
    component: MarketplaceComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'agents:agent:install' },
    children: [
      { path: '', pathMatch: 'full', children: [] },
      // The source form: a new source, or one being changed.
      { path: 'sources/new', children: [] },
      { path: ':source/edit', children: [] },
      // A source's catalog, and the review of one agent in it.
      { path: ':source', children: [] },
      { path: ':source/:agent', children: [] },
    ],
  },
  {
    path: '',
    title: 'Agents',
    component: AgentsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'agents:agent:list' },
    children: [
      { path: '', pathMatch: 'full', children: [] },
      // An agent's page, and one of its tabs.
      { path: ':ref', children: [] },
      { path: ':ref/:tab', children: [] },
    ],
  },
];
