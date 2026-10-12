import { Routes } from '@angular/router';
// The data layer as the user sees it: the files stored on their behalf,
// the records agents keep for them, and the knowledge they write for the
// assistant — one shape, one place, whoever writes into it.
import { FilesComponent } from '../../pages/app/data/files/files.component';
import { RecordsComponent } from '../../pages/app/data/records/records.component';
import { SkillsComponent } from '../../pages/app/data/skills/skills.component';
import { McpComponent } from '../../pages/app/data/mcp/mcp.component';
import { permissionGuard } from '../../guards/permission.guard';

export const DataRoutes: Routes = [
  { path: '', redirectTo: 'files', pathMatch: 'full' },
  {
    path: 'files',
    title: 'Files',
    component: FilesComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'files:file:list' },
  },
  // Saved data shows one kind of record at a time, and the address
  // names it. The kind is a child route with no component of its own,
  // so moving between kinds keeps the page and what it has read.
  {
    path: 'records',
    title: 'Saved data',
    component: RecordsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'data:record:list' },
    children: [
      { path: '', pathMatch: 'full', children: [] },
      { path: ':agent/:kind', children: [] },
    ],
  },
  {
    path: 'skills',
    title: 'Skills',
    component: SkillsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'skills:skill:list' },
  },
  {
    path: 'mcp',
    title: 'MCP servers',
    component: McpComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'mcp:server:list' },
  },
];
