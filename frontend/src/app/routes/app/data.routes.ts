import { Routes } from '@angular/router';
// The data layer as the user sees it: the files stored on their behalf,
// the records agents keep for them, and the knowledge they write for the
// assistant — one shape, one place, whoever writes into it.
import { FilesComponent } from '../../pages/app/data/files/files.component';
import { RecordsComponent } from '../../pages/app/data/records/records.component';
import { SkillsComponent } from '../../pages/app/data/skills/skills.component';
import { SkillEditorComponent } from '../../pages/app/data/skills/skill-editor.component';
import { McpComponent } from '../../pages/app/data/mcp/mcp.component';
import { McpServerComponent } from '../../pages/app/data/mcp/mcp-server.component';
import { permissionGuard } from '../../guards/permission.guard';
import { unsavedChangesGuard } from '../../guards/unsaved-changes.guard';

export const DataRoutes: Routes = [
  { path: '', redirectTo: 'files', pathMatch: 'full' },
  // One file, opened, is a view of the list's page: a child route with
  // no component of its own, so the list keeps what it has read.
  {
    path: 'files',
    title: 'Files',
    component: FilesComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'files:file:list' },
    children: [
      { path: '', pathMatch: 'full', children: [] },
      { path: ':ref', children: [] },
    ],
  },
  // Records shows one kind of record at a time, and the address
  // names it. The kind is a child route with no component of its own,
  // so moving between kinds keeps the page and what it has read.
  {
    path: 'records',
    title: 'Records',
    component: RecordsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'data:record:list' },
    children: [
      { path: '', pathMatch: 'full', children: [] },
      { path: ':agent/:kind', children: [] },
      // One record of the kind, opened beside its list.
      { path: ':agent/:kind/:ref', children: [] },
    ],
  },
  // Writing a skill, or changing one, is a page of its own. Before the
  // list's `:ref`, which would take "new" for a skill. Leaving with
  // changes not saved asks first.
  {
    path: 'skills/new',
    title: 'Write a skill',
    component: SkillEditorComponent,
    canActivate: [permissionGuard],
    canDeactivate: [unsavedChangesGuard],
    data: { requiredAction: 'skills:skill:create' },
  },
  {
    path: 'skills/:ref/edit',
    title: 'Edit skill',
    component: SkillEditorComponent,
    canActivate: [permissionGuard],
    canDeactivate: [unsavedChangesGuard],
    data: { requiredAction: 'skills:skill:update' },
  },
  {
    path: 'skills',
    title: 'Skills',
    component: SkillsComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'skills:skill:list' },
    // One skill, read, is a view of the list's page.
    children: [
      { path: '', pathMatch: 'full', children: [] },
      { path: ':ref', children: [] },
    ],
  },
  // One server, looked over, is a page of its own: its tool table
  // wants the width.
  {
    path: 'mcp/:ref',
    title: 'MCP server',
    component: McpServerComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'mcp:server:get' },
  },
  {
    path: 'mcp',
    title: 'Tools',
    component: McpComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'mcp:server:list' },
  },
];
