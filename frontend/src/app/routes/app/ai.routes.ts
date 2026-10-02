import { Routes } from '@angular/router';
import { ChatHomeComponent } from '../../pages/app/ai/chat/chat-home/chat-home.component';
import { ChatDetailComponent } from '../../pages/app/ai/chat/chat-detail/chat-detail.component';
import { SchedulesComponent } from '../../pages/app/ai/schedules/schedules.component';
import { permissionGuard } from '../../guards/permission.guard';

export const AIRoutes: Routes = [
  { path: '', redirectTo: 'chats', pathMatch: 'full' },
  // Managing agents moved to its own section; bookmarks follow.
  { path: 'agents', redirectTo: '/agents/installed', pathMatch: 'full' },
  { path: 'marketplace', redirectTo: '/agents/marketplace', pathMatch: 'full' },
  {
    path: 'chats',
    component: ChatHomeComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'ai:chat:list' },
  },
  // Every conversation now lists under the composer on the chats page;
  // the old history page's bookmarks land there.
  { path: 'chats/history', redirectTo: 'chats', pathMatch: 'full' },
  {
    path: 'chats/:chat_id',
    component: ChatDetailComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'ai:chat:get' },
  },
  // Memory is the person's own configuration of the assistant; it
  // lives under Settings. Bookmarks follow.
  { path: 'memory', redirectTo: '/settings/memory', pathMatch: 'full' },
  // Schedules: what every chat keeps on the clock, read at once. Cards
  // and background work live in their chats, where the badges point;
  // the old activity and tasks bookmarks land here.
  {
    path: 'schedules',
    component: SchedulesComponent,
    canActivate: [permissionGuard],
    data: { requiredAction: 'ai:activity:list' },
  },
  { path: 'activity', redirectTo: '/ai/schedules', pathMatch: 'full' },
  { path: 'activity/:tab', redirectTo: '/ai/schedules' },
  { path: 'tasks', redirectTo: '/ai/schedules', pathMatch: 'full' },
  { path: 'audit', redirectTo: '/settings/audit', pathMatch: 'full' },
  { path: 'audit/org', redirectTo: '/settings/audit/org', pathMatch: 'full' },
];
