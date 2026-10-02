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
    component: FilesComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'files:file:list' },
  },
  {
    path: 'records',
    component: RecordsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'data:record:list' },
  },
  {
    path: 'skills',
    component: SkillsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'skills:skill:list' },
  },
  {
    path: 'mcp',
    component: McpComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'mcp:server:list' },
  },
];
