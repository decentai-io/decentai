import { RouteNode } from './config.model';

/** One page of Settings. */
export interface SettingsPage {
  /** Whose setting it is: the person's own, or the organization's. */
  group: 'You' | 'Organization';
  /** The page's address under /settings. */
  path: string;
  /** Its name: in the nav, the title and the browser's tab. */
  name: string;
  /** The one sentence under the title. */
  sentence: string;
  /** The action that opens it. */
  action: string;
}

/**
 * The pages of Settings, in the order of the nav. Listed once: the nav
 * shows the pages a person may open, a group with none is not shown,
 * the sidebar shows Settings to whoever may open any, and /settings
 * opens on the first.
 */
export const settingsPages: SettingsPage[] = [
  {
    group: 'You', path: 'chat', name: 'Chat defaults', action: 'ai:chat:create',
    sentence: 'What a new chat starts with. Any chat can change these from its own menu.',
  },
  {
    group: 'You', path: 'notifications', name: 'Notifications', action: 'settings:notifications:get',
    sentence: 'When a chat needs you after you close it, where you hear about it.',
  },
  {
    group: 'You', path: 'memory', name: 'Memory', action: 'settings:memory:list',
    sentence: 'Facts the assistant saved during conversations, or you wrote here. Yours to correct and delete.',
  },
  {
    group: 'You', path: 'keys', name: 'API keys', action: 'settings:apikey:list',
    sentence: 'A script acting as you, with your permissions and no more.',
  },
  {
    group: 'Organization', path: 'providers', name: 'Model providers', action: 'settings:llm:list',
    sentence: 'The providers this organization has a key for. One key serves every model its provider has.',
  },
  {
    group: 'Organization', path: 'speech', name: 'Speech', action: 'settings:speech:get',
    sentence: 'Spoken messages written down, and replies said aloud, for everyone in the organization.',
  },
  {
    group: 'Organization', path: 'routing', name: 'Agent routing', action: 'settings:routing:get',
    sentence: 'How a chat finds the right agent when many are installed.',
  },
  {
    group: 'Organization', path: 'apps', name: 'Connected apps', action: 'settings:oauth:list',
    sentence: 'Apps registered with Google, Microsoft and Slack, so people connect their accounts with a click.',
  },
  {
    group: 'Organization', path: 'safety', name: 'Safety', action: 'settings:safety:get',
    sentence: 'What no agent may do, and what is always shown to you before it runs.',
  },
];

// The main workspace: what a user works in day to day. Pages are role-gated
// (requiredAction), so each appears only for people whose policy grants it.
export const mainRoutes: RouteNode[] = [
  {
    name: 'AI',
    url: 'ai',
    icon: 'bot',
    domain: 'Global',
    active: false,
    children: [
      {
        name: 'Chats',
        url: 'ai/chats',
        icon: 'message-square-text',
        domain: 'Global',
        active: true,
        requiredAction: 'ai:chat:list',
        title: 'Chats',
        children: [],
      },
      {
        name: 'Schedules',
        url: 'ai/schedules',
        icon: 'calendar-clock',
        domain: 'Global',
        active: true,
        requiredAction: 'ai:activity:list',
        title: 'Schedules',
        children: [],
      },
    ],
  },

  {
    // Managing agents, apart from using them: what is installed and who
    // may call it, where agents come from, and the tools for writing
    // one. Chats stay under AI — different job, different audience.
    name: 'Agents',
    url: 'agents',
    icon: 'bot',
    domain: 'Global',
    active: false,
    children: [
      {
        name: 'Installed',
        url: 'agents',
        icon: 'bot',
        domain: 'Global',
        active: true,
        requiredAction: 'agents:agent:list',
        title: 'Agents',
        children: [],
      },
      {
        // Only for people who may install: browsing a catalog you cannot
        // approve from is a page of buttons that refuse.
        name: 'Marketplace',
        url: 'agents/marketplace',
        icon: 'store',
        domain: 'Global',
        active: true,
        requiredAction: 'agents:agent:install',
        title: 'Agent marketplace',
        children: [],
      },
    ],
  },

  {
    name: 'Data',
    url: 'data',
    icon: 'database',
    domain: 'Global',
    active: false,
    children: [
      {
        name: 'Files',
        url: 'data/files',
        icon: 'folder',
        domain: 'Global',
        active: true,
        requiredAction: 'files:file:list',
        title: 'Files',
        children: [],
      },
      {
        name: 'Saved data',
        url: 'data/records',
        icon: 'database',
        domain: 'Global',
        active: true,
        requiredAction: 'data:record:list',
        title: 'Saved data',
        children: [],
      },
      {
        // Knowledge a person writes for the assistant. It is a data-layer
        // domain like the two above — same document, same sharing — so it
        // belongs here rather than beside the chats that happen to read it.
        name: 'Skills',
        url: 'data/skills',
        icon: 'sparkles',
        domain: 'Global',
        active: true,
        requiredAction: 'skills:skill:list',
        title: 'Skills',
        children: [],
      },
      {
        // Remote tool servers a person adds for their own chats: what
        // the assistant can call, beside what it can read.
        name: 'MCP servers',
        url: 'data/mcp',
        icon: 'server',
        domain: 'Global',
        active: true,
        requiredAction: 'mcp:server:list',
        title: 'MCP servers',
        children: [],
      },
      {
        // Credentials sit with the rest of what a person keeps here.
        // Definitions have no page of their own any more: a shape is an
        // attribute of an agent's credential slot, shown where it is
        // used — on the secret's row and in the new-secret picker.
        name: 'Secrets',
        url: 'secrets',
        icon: 'key-round',
        domain: 'Global',
        active: true,
        requiredAction: 'secrets:secret:list',
        title: 'Secrets',
        children: [],
      },
    ],
  },

  {
    // What the platform is configured with, and the two records of
    // what it did: what agents use and were seen doing, and the
    // permanent trail of changes, access and approvals.
    name: 'Organization',
    url: 'settings',
    icon: 'settings',
    domain: 'Global',
    active: false,
    children: [
      {
        name: 'Settings',
        url: 'settings',
        icon: 'settings',
        domain: 'Global',
        active: true,
        anyAction: settingsPages.map((page) => page.action),
        title: 'Settings',
        children: [],
      },
      {
        name: 'Monitoring',
        url: 'monitoring',
        icon: 'activity',
        domain: 'Global',
        active: true,
        requiredAction: 'agents:monitor:usage',
        title: 'Monitoring',
        children: [],
      },
      {
        name: 'Audit',
        url: 'audit',
        icon: 'scroll-text',
        domain: 'Global',
        active: true,
        requiredAction: 'ai:audit:list',
        title: 'Audit',
        children: [],
      },
    ],
  },
  {
    // The guide: how to use all of the above, and how to build an
    // agent of your own. Open to anyone signed in. It sits in the
    // sidebar's footer, beside Sign out.
    name: 'Help',
    url: 'guide',
    icon: 'book-open',
    domain: 'Global',
    active: false,
    footer: true,
    children: [
      {
        name: 'Guide',
        url: 'guide',
        icon: 'book-open',
        domain: 'Global',
        active: true,
        requiredAction: 'account:profile:get',
        title: 'Guide',
        children: [],
      },
    ],
  },
];

// The admin console: governing the platform — who exists and what they may
// do (IAM). Rare, high-blast-radius work, distinct from the daily workspace
// above.
export const adminRoutes: RouteNode[] = [
  // Your own account. Visible to whoever holds account:profile:get — by
  // default everybody, through the built-in Everyone group.
  {
    name: 'Account',
    url: 'admin',
    icon: 'user',
    domain: 'Global',
    active: false,
    children: [
      {
        name: 'Profile',
        url: 'admin/profile',
        icon: 'circle-user',
        domain: 'Global',
        active: true,
        requiredAction: 'account:profile:get',
        title: 'Profile',
        children: [],
      },
    ],
  },

  // Identity and access: who exists, what they may do, and the credentials
  // they use. Ordered outside-in — the organization, then its people, then the
  // groups they sit in, then the policies those grant, then machine access.
  {
    name: 'IAM',
    url: 'admin',
    icon: 'shield-check',
    domain: 'Global',
    active: false,
    children: [
      {
        name: 'Organization',
        url: 'admin/organization',
        icon: 'building-2',
        domain: 'Global',
        active: true,
        requiredAction: 'iam:organization:get',
        title: 'Organization',
        children: [],
      },
      {
        name: 'Users',
        url: 'admin/users',
        icon: 'user',
        domain: 'Global',
        active: true,
        requiredAction: 'iam:user:list',
        title: 'Users',
        children: [],
      },
      {
        name: 'Groups',
        url: 'admin/groups',
        icon: 'users',
        domain: 'Global',
        active: true,
        requiredAction: 'iam:group:list',
        title: 'Groups',
        children: [],
      },
      {
        name: 'Roles',
        url: 'admin/roles',
        icon: 'shield-check',
        domain: 'Global',
        active: true,
        requiredAction: 'iam:role:list',
        title: 'Roles',
        children: [],
      },
      {
        name: 'Policies',
        url: 'admin/policies',
        icon: 'scroll-text',
        domain: 'Global',
        active: true,
        requiredAction: 'iam:policy:list',
        title: 'Policies',
        children: [],
      },
    ],
  },
];

/**
 * Whether a navigable page is open to someone: its action is held, or,
 * for a page of tabs, any one of theirs. A page that declares nothing is
 * closed — there is no "public by omission".
 */
export function pageAllowed(
  page: RouteNode,
  can: (action: string) => boolean,
): boolean {
  if (page.anyAction?.length) {
    return page.anyAction.some((action) => can(action));
  }
  return !!page.requiredAction && can(page.requiredAction);
}

/**
 * The first page this user may actually open, workspace first, or '' when
 * their policy grants them none. Anything that needs somewhere safe to send
 * a user — a denied route, a landing decision — asks here rather than
 * assuming a particular page is always available.
 */
export function firstVisiblePage(
  can: (action: string) => boolean,
): string {
  for (const section of [...mainRoutes, ...adminRoutes]) {
    for (const page of section.children ?? []) {
      if (!page.active) continue;
      if (!pageAllowed(page, can)) continue;
      return page.url;
    }
  }
  return '';
}

/**
 * Actions that unlock at least one admin console page. Holding any of them
 * (or the coarse admin tier) is what makes the console reachable at all —
 * the workspace toggle and the landing route both derive from this list.
 */
export const adminConsoleActions: string[] = adminRoutes.flatMap((section) =>
  (section.children ?? [])
    .map((child) => child.requiredAction)
    .filter((action): action is string => !!action),
);
