import { NgModule, isDevMode, SecurityContext } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';
import { BrowserAnimationsModule } from '@angular/platform-browser/animations';
import {
  HttpClient,
  provideHttpClient,
  withInterceptorsFromDi,
} from '@angular/common/http';
import { FormsModule } from '@angular/forms';

import { AppRoutingModule } from './app-routing.module';

import { MatPaginatorModule } from '@angular/material/paginator';
import { MatSortModule } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';
import { MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatMenuModule } from '@angular/material/menu';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MarkdownModule, SANITIZE } from 'ngx-markdown';
import {
  LucideAngularModule,
  Mail,
  MessageSquare,
  ChartLine,
  ChartPie,
  CircleDot,
  CircleQuestionMark,
  CloudOff,
  CloudUpload,
  ExternalLink,
  FileArchive,
  FileBraces,
  FileCode,
  FileImage,
  FileMusic,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  MousePointerClick,
  Minus,
  Monitor,
  FileVideoCamera,
  List,
  ListChecks,
  LoaderCircle,
  Presentation,
  SearchX,
  Timer,
  Activity,
  Archive,
  ArchiveRestore,
  Asterisk,
  ArrowLeft,
  Calendar,
  CalendarClock,
  CircleAlert,
  CircleDashed,
  CircleCheck,
  CircleStop,
  Camera,
  Mic,
  Square,
  CircleX,
  Clock,
  CloudCheck,
  Copy,
  Gauge,
  GitBranch,
  Globe,
  Handshake,
  Gavel,
  Headset,
  History,
  Info,
  Lock,
  Pause,
  Play,
  MailPlus,
  Network,
  Code,
  Paperclip,
  PencilLine,
  Pin,
  ReceiptText,
  ScrollText,
  Send,
  Share,
  ShieldAlert,
  Sparkles,
  Star,
  StarOff,
  Store,
  SquarePen,
  SquareTerminal,
  Terminal,
  TriangleAlert,
  Users,
  Inbox,
  EllipsisVertical,
  ChevronUp,
  ChevronLeft,
  ArrowUpDown,
  Bell,
  BookOpen,
  Bot,
  BrainCircuit,
  Building2,
  CalendarCheck,
  Circle,
  Hourglass,
  Loader,
  OctagonAlert,
  OctagonX,
  CalendarDays,
  ChartColumn,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Cloud,
  Database,
  Download,
  Eye,
  Filter,
  Flame,
  House,
  KeyRound,
  Lightbulb,
  LogIn,
  LogOut,
  Menu,
  MessageSquareText,
  Moon,
  Package,
  Pencil,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  Server,
  Settings,
  ShieldCheck,
  ShieldQuestion,
  Sun,
  Table,
  Trash2,
  User,
  Upload,
  UserCog,
  X, ArrowRight, ArrowDown, ArrowUp, ArrowRightLeft, UserX, UserPlus,
} from 'lucide-angular';
import {
  BaseChartDirective,
  provideCharts,
  withDefaultRegisterables,
} from 'ng2-charts';

// services
import { NavigatorService } from './services/navigator.service';
import { RequestService } from './services/request.service';

// components
import { AppComponent } from './app.component';
import { SidebarComponent } from './website/sidebar/sidebar.component';
import { ContentComponent } from './website/content/content.component';
import { MainComponent } from './website/main/main.component';
import { AlertComponent } from './components/alert/alert.component';
import { InformativeTableComponent } from './components/informative-table/informative-table.component';
import { TransferDialogComponent } from './components/transfer-dialog/transfer-dialog.component';
import { SharingSelectorComponent } from './components/sharing-selector/sharing-selector.component';
import { DataStoreService } from './services/datastore.service';

// routes components
// admin
import { MembersComponent } from './pages/admin/members/members.component';

// app
import { NoAccessComponent } from './website/no-access/no-access.component';
import { PageNotFoundComponent } from './website/page-not-found/page-not-found.component';

import { ChatHomeComponent } from './pages/app/ai/chat/chat-home/chat-home.component';
import { ChatDetailComponent } from './pages/app/ai/chat/chat-detail/chat-detail.component';
import { ChatComposerComponent } from './pages/app/ai/chat/chat-composer/chat-composer.component';
import { ChatMessageComponent } from './pages/app/ai/chat/chat-message/chat-message.component';
import { ChatMessagesContainerComponent } from './pages/app/ai/chat/chat-messages-container/chat-messages-container.component';
import { ChatActivityDialogComponent } from './pages/app/ai/chat/chat-activity-dialog/chat-activity-dialog.component';
import { ChatActivityPanelComponent } from './pages/app/ai/chat/chat-activity-panel/chat-activity-panel.component';
import { ChatAgentsDialogComponent } from './pages/app/ai/chat/chat-agents-dialog/chat-agents-dialog.component';
import { ChatSkillsDialogComponent } from './pages/app/ai/chat/chat-skills-dialog/chat-skills-dialog.component';
import { ChatModelPickerComponent } from './pages/app/ai/chat/chat-model-picker/chat-model-picker.component';
import { ChatApprovalCardComponent } from './pages/app/ai/chat/chat-approval-card/chat-approval-card.component';
import { FilePickerComponent } from './pages/app/ai/chat/file-picker/file-picker.component';
import { ChatFilePickerDialogComponent } from './pages/app/ai/chat/chat-file-picker-dialog/chat-file-picker-dialog.component';
import { ChatScreenComponent } from './pages/app/ai/chat/chat-screen/chat-screen.component';

import { LoadingComponent } from './website/loading/loading.component';
import { LogoutComponent } from './pages/logout/logout.component';
import { AuthComponent } from './pages/auth/auth.component';


import { ChartViewerComponent } from './components/chart-viewer/chart-viewer.component';

import { GroupsComponent } from './pages/admin/groups/groups.component';
import { RolesComponent } from './pages/admin/roles/roles.component';
import { PoliciesComponent } from './pages/admin/policies/policies.component';
import { ProfileComponent } from './pages/admin/profile/profile.component';
import { AgentsComponent } from './pages/app/agents/agents.component';
import { AgentDetailComponent } from './pages/app/agents/agent-detail.component';
import { AgentCredentialsComponent } from './pages/app/agents/agent-credentials.component';
import { AgentFunctionsComponent } from './pages/app/agents/agent-functions.component';
import { AgentAccessComponent } from './pages/app/agents/agent-access.component';
import { MarketplaceComponent } from './pages/app/agents/marketplace/marketplace.component';
import { SourceFormComponent } from './pages/app/agents/marketplace/source-form.component';
import { InstallReviewComponent } from './pages/app/agents/marketplace/install-review.component';
import { SkillsComponent } from './pages/app/data/skills/skills.component';
import { McpComponent } from './pages/app/data/mcp/mcp.component';
import { MemoryComponent } from './pages/app/settings/memory/memory.component';
import { ApiKeysComponent } from './pages/app/settings/api-keys/api-keys.component';
import { SafetyComponent } from './pages/app/settings/safety/safety.component';
import { MonitoringComponent } from './pages/app/settings/monitoring/monitoring.component';
import { ModelProvidersComponent } from './pages/app/settings/model-providers/model-providers.component';
import { ModelSelectComponent } from './components/model-select/model-select.component';
import { ConnectedAppsComponent } from './pages/app/settings/connected-apps/connected-apps.component';
import { SchedulesComponent } from './pages/app/ai/schedules/schedules.component';
import { AuditComponent } from './pages/app/ai/audit/audit.component';
import { FilesComponent } from './pages/app/data/files/files.component';
import { RecordsComponent } from './pages/app/data/records/records.component';
import { SecretsComponent } from './pages/app/secrets/secrets.component';
import { SettingsComponent } from './pages/app/settings/settings.component';
import { GuideComponent } from './pages/app/guide/guide.component';
import { OrganizationsComponent } from './pages/admin/organizations/organizations.component';
import { ServiceWorkerModule } from '@angular/service-worker';

@NgModule({
  declarations: [
    //main website components
    AppComponent,
    AuthComponent,
    LogoutComponent,
    SidebarComponent,
    ContentComponent,
    //reusable components
    AlertComponent,
    InformativeTableComponent,
    TransferDialogComponent,
    SharingSelectorComponent,
    ChartViewerComponent,
    LoadingComponent,

    // route components
    MainComponent,
    NoAccessComponent,
    PageNotFoundComponent,
    MembersComponent,
    GroupsComponent,
    RolesComponent,
    PoliciesComponent,
    ProfileComponent,
    AgentsComponent,
    AgentDetailComponent,
    AgentCredentialsComponent,
    AgentFunctionsComponent,
    AgentAccessComponent,
    MarketplaceComponent,
    SourceFormComponent,
    InstallReviewComponent,
    SkillsComponent,
    McpComponent,
    MemoryComponent,
    ApiKeysComponent,
    SafetyComponent,
    MonitoringComponent,
    ModelProvidersComponent,
    ModelSelectComponent,
    ConnectedAppsComponent,
    SchedulesComponent,
    AuditComponent,
    FilesComponent,
    RecordsComponent,
    SecretsComponent,
    SettingsComponent,
    GuideComponent,
    OrganizationsComponent,
    ChatHomeComponent,
    ChatDetailComponent,
    ChatComposerComponent,
    ChatMessageComponent,
    ChatMessagesContainerComponent,
    ChatActivityDialogComponent,
    ChatActivityPanelComponent,
    ChatAgentsDialogComponent,
    ChatSkillsDialogComponent,
    ChatModelPickerComponent,
    ChatApprovalCardComponent,
    FilePickerComponent,
    ChatFilePickerDialogComponent,
    ChatScreenComponent,
  ],
  bootstrap: [AppComponent],
  imports: [
    //angular modules
    BrowserModule,
    AppRoutingModule,
    BrowserAnimationsModule,
    FormsModule,
    //material modules
    MatPaginatorModule,
    MatSortModule,
    MatTableModule,
    MatDialogModule,
    MatButtonModule,
    MatCheckboxModule,
    MatMenuModule,
    MatTooltipModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,

    //other modules
    // Markdown here is written by a model, an agent or a colleague:
    // its HTML is sanitized, and said so, not left to a default.
    MarkdownModule.forRoot({
      loader: HttpClient,
      sanitize: { provide: SANITIZE, useValue: SecurityContext.HTML },
    }),
    BaseChartDirective,

    // The icon set (Lucide): only the icons named here are bundled.
    LucideAngularModule.pick({
      Mail,
      MessageSquare,
      ChartLine,
      ChartPie,
      CircleDot,
      CircleQuestionMark,
      CloudOff,
      CloudUpload,
      ExternalLink,
      FileArchive,
      FileBraces,
      FileCode,
      FileImage,
      FileMusic,
      FileSpreadsheet,
      FileText,
      FolderOpen,
      MousePointerClick,
      Minus,
      Monitor,
      FileVideoCamera,
      List,
      ListChecks,
      LoaderCircle,
      Presentation,
      SearchX,
      Timer,
      Activity,
      Archive,
      ArchiveRestore,
      Asterisk,
      ArrowLeft,
      Calendar,
      CalendarClock,
      CircleAlert,
      CircleDashed,
      CircleCheck,
      CircleStop,
      Camera,
      Mic,
      Square,
      CircleX,
      Clock,
      CloudCheck,
      Copy,
      Globe,
      Gauge,
      GitBranch,
      Handshake,
      Gavel,
      Headset,
      History,
      Info,
      Lock,
      Pause,
      Play,
      MailPlus,
      Network,
      Code,
      Paperclip,
      PencilLine,
      Pin,
      ReceiptText,
      ScrollText,
      Send,
      Share,
      ShieldAlert,
      Sparkles,
  Store,
      SquarePen,
      SquareTerminal,
      Terminal,
      TriangleAlert,
      Users,
      Inbox,
      EllipsisVertical,
      ChevronUp,
      ChevronLeft,
      ArrowUpDown,
      Bell,
      BookOpen,
      Bot,
      BrainCircuit,
      Building2,
      CalendarCheck,
      Circle,
      Hourglass,
      Loader,
      OctagonAlert,
      OctagonX,
      CalendarDays,
      ChartColumn,
      Check,
      ChevronDown,
      ChevronRight,
      ChevronsLeft,
      ChevronsRight,
      Cloud,
      Database,
      Download,
      Eye,
      Filter,
      Flame,
      House,
      KeyRound,
      Lightbulb,
      LogIn,
      LogOut,
      Menu,
      MessageSquareText,
      Moon,
      Package,
      Pencil,
      Plus,
      RefreshCw,
      Rocket,
      Search,
      Server,
      Settings,
      StarOff,
      Star,
      ShieldCheck,
      ShieldQuestion,
      Sun,
      Table,
      Trash2,
      User,
      Upload,
      UserCog,
      X, ArrowRight, ArrowDown, ArrowUp, ArrowRightLeft, UserX, UserPlus,
    }),
      ServiceWorkerModule.register('ngsw-worker.js', {
        enabled: !isDevMode(),
        // Register the ServiceWorker as soon as the application is stable
        // or after 30 seconds (whichever comes first).
        registrationStrategy: 'registerWhenStable:30000'
      }),
  ],
  providers: [
    NavigatorService,
    RequestService,
    DataStoreService,
    provideHttpClient(withInterceptorsFromDi()),
    provideCharts(withDefaultRegisterables()),
  ],
})
export class AppModule {}
