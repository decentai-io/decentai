/**
 * Shared shell-layer types.
 *
 * Centralises the string-literal unions and route shape used by the root
 * component, the layout shell (main/content/sidebar) and the navigator /
 * datastore services so we stop passing `any` around the navigation core.
 */

/** Top-level view rendered by the root component. */
export type AppView =
  | 'loading'
  | 'auth'
  | 'app'
  | 'logout';

/** Which workspace the sidebar/header is currently showing. */
export type ContentView = 'main' | 'admin';

/** Active route descriptor used to drive sidebar highlighting. */
export interface ActiveRoute {
  primary: string;
  secondary: string;
  tertiary: string;
}

/** Authenticated user, as returned by the `status` endpoint. */
export interface UserInfo {
  name: string;
  group: string;
  [key: string]: unknown;
}

/**
 * A node in the sidebar navigation tree. The same shape is reused for
 * primary (module), secondary and tertiary entries.
 */
export interface RouteNode {
  name: string;
  url: string;
  icon: string;
  /**
   * Catalog action (e.g. `iam:role:list`) the session must hold for the
   * node to appear. Section headers declare none and are judged by their
   * children.
   */
  requiredAction?: string;
  /**
   * For a page that is a set of tabs, each with an action of its own:
   * holding any one of these shows the node. Takes the place of
   * `requiredAction`.
   */
  anyAction?: string[];
  domain: string;
  /** Whether the node is itself navigable (vs. a non-clickable grouping). */
  active: boolean;
  /** Page title shown in the header when this node is selected. */
  title?: string;
  children?: RouteNode[];
}
