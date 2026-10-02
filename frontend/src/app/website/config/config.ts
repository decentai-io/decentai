import { RouteNode } from './config.model';

/**
 * The settings page is a set of tabs, each opened by an action of its
 * own. Listed once, in the order the page is entered: the sidebar shows
 * Settings to whoever holds any of these, and the page opens on the
 * first tab the person may use. `path` is the tab's address under
 * /settings; '' is the page itself.
 */
export const settingsTabs: { path: string; action: string }[] = [
  { path: '', action: 'settings:llm:list' },
  { path: 'chat', action: 'ai:chat:create' },
  { path: 'memory', action: 'settings:memory:list' },
  { path: 'keys', action: 'settings:apikey:list' },
  { path: 'apps', action: 'settings:oauth:list' },
  { path: 'safety', action: 'settings:safety:get' },
  { path: 'audit', action: 'ai:audit:list' },
  { path: 'audit/org', action: 'ai:audit:list_all' },
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
        url: 'agents/installed',
        icon: 'bot',
        domain: 'Global',
        active: true,
        requiredAction: 'agents:agent:list',
        title: 'Installed agents',
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
        icon: 'file-text',
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
        url: 'secrets/values',
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
    // What the platform itself is configured with — its own module,
    // its own collections. First section: the LLM connections the
    // organization's chats think with.
    name: 'Settings',
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
        anyAction: settingsTabs.map((tab) => tab.action),
        title: 'Settings',
        children: [],
      },
    ],
  },
  {
    // The guide: how to use all of the above, and how to build an
    // agent of your own. Open to anyone signed in.
    name: 'Help',
    url: 'guide',
    icon: 'book-open',
    domain: 'Global',
    active: false,
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
        icon: 'user',
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
        icon: 'file-text',
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
