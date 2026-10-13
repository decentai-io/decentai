import { AgentFunction, AgentOffer } from 'src/app/services/agents.service';

/** Permission levels 0 to 3, in the words every page uses: chips,
 *  functions, the review, a card in a chat. How much of an agent runs
 *  without asking is what an administrator decides, and "3" does not
 *  say it. The number is a tooltip, never the name. */
export const PERMISSION_LEVELS = ['Reads', 'Changes', 'Wider change', 'Outside action'];

export function levelLabel(level: number | null | undefined): string {
  return level === null || level === undefined
    ? '' : PERMISSION_LEVELS[level] || `Level ${level}`;
}

/** What each level means, by its word. Whether a function asks first
 *  is not the level's to say alone: it asks when its level is above
 *  the trust level of the chat that calls it. */
export const LEVEL_MEANINGS: Record<string, string> = {
  'Reads': 'Level 0. Reads only, and changes nothing. Runs in every chat without asking.',
  'Changes': 'Level 1. An ordinary change inside the platform. Asks first in a chat whose trust level is 0.',
  'Wider change': 'Level 2. A change with wider reach, still inside the platform. Asks first in a chat whose trust level is below 2.',
  'Outside action': 'Level 3. Acts outside the platform. Asks first in every chat whose trust level is below 3.',
};

/** Where an installed agent stands: one word, the colour it deserves,
 *  and which of the six it is. */
export interface Standing {
  kind: 'failed' | 'preparing' | 'credential' | 'update' | 'gone' | 'ready';
  word: string;
  /** The chip's variant (styles/controls.scss): '' is the plain one. */
  tone: '' | 'danger' | 'live' | 'warn' | 'accent';
}

/** The standing of an installed agent, for the list, its own page and
 *  the counts alike, so they cannot disagree. The first that holds:
 *  it could not be prepared, it is being prepared, a credential it
 *  declared has nothing usable (`missing` of them), its source offers
 *  newer code, its source lists it no more, or it is ready. */
export function standingOf(agent: AgentOffer, missing: number): Standing {
  if (agent.prepared?.state === 'failed') return { kind: 'failed', word: 'Failed', tone: 'danger' };
  if (agent.prepared?.state === 'preparing') return { kind: 'preparing', word: 'Preparing', tone: 'live' };
  if (missing > 0) return { kind: 'credential', word: 'Needs a credential', tone: 'warn' };
  if (agent.status === 'update_available') return { kind: 'update', word: 'Update available', tone: '' };
  if (agent.status === 'removed') return { kind: 'gone', word: 'Not in its source any more', tone: '' };
  return { kind: 'ready', word: 'Ready', tone: 'accent' };
}

/** The installed version as it is said, once, beside the name: "v2.0.9",
 *  or nothing where there is none to say. */
export function versionLabel(agent: AgentOffer): string {
  return agent.installed_version ? `v${agent.installed_version}` : '';
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

/** One of the three ways the platform confines an agent where it
 *  runs, and whether this install does. */
export interface Confinement {
  held: boolean;
  /** Said when it is held. */
  words: string;
  /** Said when it is not: whoever approved the agent is owed the fact. */
  missing: string;
}

/** What this install holds an agent to, a line a part: a user of its
 *  own, its own files, the hosts it declared. Empty when no runtime
 *  has said. Files cannot be fenced without a user of its own. */
export function confinementOf(agent: AgentOffer): Confinement[] {
  const confined = agent.prepared?.confined;
  if (!confined) return [];
  return [
    {
      held: !!confined.user,
      words: 'It runs as a user of its own.',
      missing: 'It runs as the same user as the platform here: nothing separates it '
        + 'from the platform, or from other agents.',
    },
    {
      held: !!confined.user && !!confined.files,
      words: 'It opens its own files and the system\'s, and nothing else.',
      missing: 'Its files are not fenced here: it can read what any program on the '
        + 'same machine can.',
    },
    {
      held: !!confined.network,
      words: 'It connects only through the platform, which holds it to the hosts it declared.',
      missing: 'Nothing holds it to the hosts it declared here: it can connect '
        + 'anywhere the runtime can.',
    },
  ];
}

/** The line of a card that a person compares agents by: how many
 *  functions, what it needs, and where it connects (the first host,
 *  and how many more). */
export function agentFacts(functions: number, credentials: number,
                           network?: AgentNetwork | null): string[] {
  const facts = [`${functions} ${functions === 1 ? 'function' : 'functions'}`];
  if (credentials) facts.push(`${credentials} ${credentials === 1 ? 'credential' : 'credentials'}`);
  if (network?.any) {
    facts.push('the open web');
  } else if (network?.hosts.length) {
    const more = network.hosts.length - 1;
    facts.push(network.hosts[0] + (more ? ` +${more}` : ''));
  }
  return facts;
}
