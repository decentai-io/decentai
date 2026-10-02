import { AgentFunction, AgentOffer } from 'src/app/services/agents.service';

/** Permission levels 0–3, named rather than numbered. How much of an
 *  agent runs without asking is the thing an administrator is actually
 *  deciding, and "3" does not say it. */
export const PERMISSION_LEVELS = ['read', 'change', 'sandboxed', 'external'];

export function levelLabel(level: number | null | undefined): string {
  return level === null || level === undefined
    ? '' : PERMISSION_LEVELS[level] || `level ${level}`;
}

/** `agt_x.note.save` → `note.save`. A grant already names the agent, so
 *  its functions are stored and shown the short way. */
export function localFunction(fn: AgentFunction): string {
  const parts = String(fn?.name || '').split('.');
  return parts.length === 3 ? `${parts[1]}.${parts[2]}` : String(fn?.name || '');
}

/** `<org>:agt_x__connection/v1` → `agt_x__connection`.
 *
 *  A pinned definition ref carries its organization and version; the
 *  FAMILY — what secrets record as their `resource_id`, what lists and
 *  defaults are keyed by — is the bare slug between the two. Keeping
 *  the org prefix here made every list come back empty. */
export function familyOf(ref: string): string {
  const base = String(ref || '').split('/v')[0];
  const colon = base.indexOf(':');
  return colon >= 0 ? base.slice(colon + 1) : base;
}

/** One credential type an agent declared, and whether any secret exists
 *  for it. Named by the manifest's own label — "Jira Connection" — never
 *  the derived slug, which is the platform's bookkeeping. */
export interface AgentCredential {
  id: string;
  family: string;
  label: string;
  saved: number;
  /** Somebody granted an existing credential to this slot — satisfied
   *  without a secret of the agent's own. */
  granted?: boolean;
}

/** Where an agent connects, as its manifest declared it. */
export interface AgentNetwork {
  /** Whether the document carries a network block. Every installed
   *  agent's does: a manifest without one is refused. */
  declared: boolean;
  /** Its work is the open web: it declared the one word `any`. */
  any: boolean;
  /** Names; a leading `*.` is every host under one, and `:<port>` is
   *  the port it is reached on where that is not the web's. */
  hosts: string[];
  /** `<secret>.<field>`: a host the granted credential names, with
   *  `:<port>` where a port was declared. */
  from_secrets: string[];
  /** It runs code a person allows on a card (a function with
   *  `code: true`), and reaches what that card named, for that run. */
  proposed?: boolean;
}

/** Whether a function of the manifest runs code a person allows. */
function runsCode(manifest: any): boolean {
  return (manifest?.tools || []).some((tool: any) =>
    (tool?.functions || []).some((fn: any) => fn?.code === true));
}

/** A manifest's `network` block, read the way the platform reads it. A
 *  manifest must have one; a document without it reaches nothing. */
export function networkOf(manifest: any): AgentNetwork {
  const block = manifest?.network;
  if (!block || typeof block !== 'object' || Array.isArray(block)) {
    return { declared: false, any: false, hosts: [], from_secrets: [] };
  }
  const proposed = runsCode(manifest) ? { proposed: true } : {};
  if (!Array.isArray(block.hosts)) {
    return { declared: true, any: block.hosts === 'any', hosts: [], from_secrets: [], ...proposed };
  }
  return {
    declared: true,
    any: false,
    hosts: block.hosts.filter((host: any) => typeof host === 'string'),
    from_secrets: block.hosts
      .filter((host: any) => host && typeof host === 'object')
      .map((host: any) => String(host.from_secret || '') + (host.port ? `:${host.port}` : '')),
    ...proposed,
  };
}

/** A host read from a credential, in words: the field, and the port
 *  where one was declared. */
export function credentialHost(declared: string): string {
  const [field, port] = String(declared || '').split(':');
  return port ? `the address in ${field}, on port ${port}` : `the address in ${field}`;
}

/** What is said under the hosts: the one sentence that tells an
 *  administrator how much the list means. */
export function networkNote(network: AgentNetwork): string {
  if (!network.declared) {
    return 'Its manifest does not say where it connects, so it cannot be installed.';
  }
  if (network.any) {
    return 'It declared that its work is the open web. Addresses inside your own network stay refused.';
  }
  if (network.proposed) {
    // The sentence that matters most for an agent that runs code: what
    // it reaches is decided a run at a time, by whoever is asked.
    const own = network.hosts.length || network.from_secrets.length
      ? 'Beyond the hosts it declared, it' : 'By itself it connects to nothing. It';
    return `${own} runs code it shows you first: each time, a card names the sites `
      + 'that code reaches and the packages it installs, and only what you allow '
      + 'is opened, for that run. Addresses inside your own network stay refused.';
  }
  if (!network.hosts.length && !network.from_secrets.length) {
    return 'It declared that it connects to nothing outside the platform.';
  }
  return network.from_secrets.length
    ? 'A host read from a credential is whatever address that credential holds.'
    : 'These are the only hosts it declared.';
}

/** What this install does NOT hold an agent to, one sentence a part —
 *  empty when it holds it to everything, or when no runtime has said.
 *  The platform confines an agent three ways; where one of them is
 *  missing, whoever approved the agent is owed the fact. */
export function notEnforced(agent: AgentOffer): string[] {
  const confined = agent.prepared?.confined;
  if (!confined) return [];
  const lines: string[] = [];
  if (!confined.user) {
    lines.push('It runs as the same user as the platform here: nothing separates '
      + 'it from the platform, or from other agents.');
  } else if (!confined.files) {
    lines.push('Its files are not fenced here: it can read what any program on '
      + 'the same machine can.');
  }
  if (!confined.network) {
    lines.push('Nothing holds it to the hosts it declared here: it can connect '
      + 'anywhere the runtime can.');
  }
  return lines;
}

/** The installed pill's words: what a runtime has said about this
 *  version's code being ready to run, or plain Installed when nothing
 *  has been said. */
export function installedLabel(agent: AgentOffer): string {
  const version = `v${agent.installed_version}`;
  switch (agent.prepared?.state) {
    case 'preparing': return `Preparing · ${version}`;
    case 'ready': return `Ready · ${version}`;
    case 'failed': return `Could not prepare · ${version}`;
    default: return `Installed · ${version}`;
  }
}
