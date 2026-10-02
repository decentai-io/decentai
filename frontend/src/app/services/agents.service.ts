import { Injectable } from '@angular/core';

import { AiSessionService } from './ai-session.service';

export interface AgentFunction {
  name: string;
  tool: string;
  description: string;
  permission_level: number | null;
  timeout_seconds: number | null;
  /** Whether it thinks with the deployment's model. */
  llm?: boolean;
}

export interface AgentResource {
  id: string;
  label: string;
  description: string;
  fields: string[];
}

/** What an agent's sample sheet would load, as the catalog listed it. */
export interface SampleSummary {
  story: string;
  records: number;
  files: number;
  shapes: string[];
  errors: string[];
}

/** What a runtime last said about an installed version's code being
 *  ready to run: building since the install, ready, or why not. */
export interface AgentReadiness {
  state: 'preparing' | 'ready' | 'failed' | '';
  error?: string;
  /** What the runtime that spoke holds the agent to. Absent until one
   *  has said. */
  confined?: { user: boolean; files: boolean; network: boolean };
}

/** One installed agent: what was approved, and where it stands against
 *  the source it came from — current, an update waiting there, or no
 *  longer listed by it. */
export interface AgentOffer {
  agent_id: string;
  name: string;
  description: string;
  status: 'installed' | 'update_available' | 'removed';
  /** Absent or null until a runtime has spoken about this version. */
  prepared?: AgentReadiness | null;
  /** Prompts to try first, as the manifest declared them. */
  examples?: { title: string; prompt: string }[];
  /** Sample data the agent ships; null or absent when it ships none. */
  samples?: SampleSummary | null;
  loaded_version: string | null;
  installed_version: string | null;
  functions: AgentFunction[];
  resources: { secrets?: AgentResource[]; data?: AgentResource[]; files?: AgentResource[] };
  dependencies: string[];
  /** Where it connects, as the manifest declared it. Absent on a row
   *  read from the plain list, which does not carry it. */
  network?: {
    declared: boolean; any: boolean; hosts: string[]; from_secrets: string[];
    proposed?: boolean;
  };
  /** Slots a saved credential was lent to. */
  granted_secrets?: string[];
  /** The vocabulary a grant for this agent can be narrowed by. */
  scopes?: string[];
  /** Repository agents carry where their code came from. */
  source?: { type: string; url?: string; ref?: string; sha?: string };
  /** What code this IS — sha256 of the stored package. The repository
   *  and commit say where it came from; this is what was kept. */
  package_digest?: string;
  /** What approval derived, by kind: {secrets: {connection: "agt_x__connection/v1"}}.
   *  A credential for this agent has to be created from exactly these. */
  resource_refs?: {
    secrets?: Record<string, string>;
    data?: Record<string, string>;
    files?: Record<string, string>;
  };
}

/** One saved credential an agent may use. A manifest declares the SHAPE
 *  it needs; a grant is what hands over a filled-in one, and it can be
 *  taken back. */
export interface AgentSecretGrant {
  grant_id: string;
  agent_ref: string;
  resource_id: string;
  secret_ref: string;
  created_by: string;
  created_at?: string | null;
  /** The lent credential's name. */
  secret_name?: string;
  /** False when the agent was updated since and now expects a
   *  credential of another shape: it is refused this one at use. */
  fits?: boolean;
}

/** An installed agent a connected account could be lent to: which
 *  agent, and which of its slots. */
export interface LendableAgent {
  agent_id: string;
  name: string;
  resource_id: string;
  label: string;
}

export interface LendableSkipped extends LendableAgent {
  reason: string;
}

/** One credential slot an agent declared, with what is granted to it and
 *  what could be — the candidates are the caller's own secrets whose
 *  definition matches the declared shape exactly. */
export interface AgentSecretSlot {
  resource_id: string;
  label: string;
  description: string;
  definition_ref: string;
  grant: AgentSecretGrant | null;
  candidates: Array<{
    resource_ref: string;
    name: string;
    definition_ref: string;
  }>;
}

/** Who may call one agent's functions. Allow-only: absence is denial. */
export interface AgentGrant {
  grant_id: string;
  agent_ref: string;
  owner: { groups: string[]; users: string[] };
  /** '*' follows the agent as its manifest grows; a list stays as agreed. */
  functions: string[] | '*';
  constraints: Record<string, string[]>;
  created_by: string;
  created_at?: string | null;
}

/** One agent as its repository's catalog offers it — no code has run. */
export interface CatalogEntry {
  id: string;
  path: string;
  /** Empty until this organization installs it — a ref names an
   *  approval, and listing a catalog approves nothing. */
  agent_ref: string;
  manifest: any;
  errors?: string[];
  installed_version?: string;
  /** Sample data the agent ships; null or absent when it ships none. */
  samples?: SampleSummary | null;
}

/** A private repository's read credential, typed once and stored on
 *  the source — encrypted at rest, never returned. */
export interface SourceCredential {
  username: string;
  token: string;
}

export interface AgentSource {
  source_id: string; name: string; url: string; ref: string;
  /** Whether the caller may edit, refresh or delete it. A source a
   *  colleague shared org-wide is readable and installable, and nothing
   *  more. */
  owned?: boolean;
  /** Whether the caller created it; `owned` is whether they may change it. */
  mine?: boolean;
  has_credential?: boolean; credential_user?: string;
  /** Who inside the org may see it — the same owner map connections
   *  and secrets use. A source never reaches outside its own org. */
  owner?: { groups: string[]; users: string[] };
  created_by_id?: string;
  status: string; sha: string; last_error?: string;
  /** When its catalog was last read, so staleness is visible. */
  last_checked_at?: string | null;
  /** Installed agents that came from here: what blocks its removal. */
  installed_agents?: Array<{ agent_ref: string; name: string; version: string }>;
  catalog?: { catalog?: any; agents?: CatalogEntry[] };
}

/**
 * Client for the agent lifecycle. An agent is installed from a saved
 * source: the backend reads its manifest at one commit, an administrator
 * approves that, and the backend keeps the code it approved.
 */
@Injectable({ providedIn: 'root' })
export class AgentsService {
  constructor(private ai: AiSessionService) {}

  /** The installed agents, each with what was approved and what is
   *  left to do about it. Requires agents:agent:available. */
  async available(): Promise<AgentOffer[]> {
    const res = await this.ai.ai('Agents:Agent:Available', {});
    return res.data?.agents || [];
  }

  /** The approved set alone — all a reader needs. */
  async installed(): Promise<any[]> {
    const res = await this.ai.ai('Agents:Agent:List', {});
    return res.data?.agents || [];
  }

  async install(agentId: string): Promise<{ data?: any; error?: string }> {
    return this.ai.ai('Agents:Agent:Install', { agent_id: agentId });
  }

  /** The saved sources, and the repository this deployment is willing to
   *  suggest (empty unless REFERENCE_CATALOG_URL is configured). */
  async sources(): Promise<{ sources: AgentSource[]; referenceCatalogUrl: string }> {
    const res = await this.ai.ai('Agents:Agent:Sources');
    return {
      sources: res.data?.sources || [],
      referenceCatalogUrl: String(res.data?.reference_catalog_url || ''),
    };
  }

  async createSource(name: string, url: string, ref: string,
                     credential?: SourceCredential | null,
                     owner?: { groups: string[]; users: string[] }) {
    return this.ai.ai('Agents:Agent:SourceCreate', { name, url, ref,
      ...(credential?.token ? { credential } : {}),
      ...(owner ? { owner } : {}) });
  }

  async refreshSource(sourceId: string) {
    return this.ai.ai('Agents:Agent:SourceRefresh', { source_id: sourceId });
  }
  /** `credential` semantics: undefined leaves the stored credential
   *  alone, null clears it, a value sets it (blank token keeps the
   *  stored one while updating the username). */
  async updateSource(sourceId: string, name: string, url: string, ref: string,
                     credential?: SourceCredential | null,
                     owner?: { groups: string[]; users: string[] }) {
    return this.ai.ai('Agents:Agent:SourceUpdate', { source_id: sourceId,
      name, url, ref,
      ...(credential !== undefined ? { credential } : {}),
      ...(owner ? { owner } : {}) });
  }

  async deleteSource(sourceId: string) {
    return this.ai.ai('Agents:Agent:SourceDelete', { source_id: sourceId });
  }

  /** Uninstall every agent installed from a source, then remove it. */
  async purgeSource(sourceId: string) {
    return this.ai.ai('Agents:Agent:SourcePurge', { source_id: sourceId });
  }

  /** Hand a source to another member. */
  async transferSource(sourceId: string, userId: string) {
    return this.ai.ai('Agents:Agent:SourceTransfer', { source_id: sourceId, user_id: userId });
  }

  /** Install one entry of a saved source's catalog. Naming the source is
   *  the whole request: where it points, and which credential reads it,
   *  are the backend's to take from the record. */
  async installCatalogAgent(source: AgentSource, entry: any) {
    return this.ai.ai('Agents:Agent:Install', {
      source_id: source.source_id,
      local_agent_id: entry.id, catalog_path: entry.path,
    });
  }

  // ── Access ────────────────────────────────────────────────────────
  //
  // Who may call an agent's functions is kept on the agent, not in an
  // IAM policy: its functions are discovered from a manifest and change
  // when it updates, so a policy naming them would rot.

  async grants(agentRef: string): Promise<AgentGrant[]> {
    return (await this.grantsOrError(agentRef)).grants;
  }

  /** The grants, or why they could not be read — so a page can tell
   *  "nobody has access" from "the list was refused". */
  async grantsOrError(agentRef: string): Promise<{ grants: AgentGrant[]; error?: string }> {
    const res = await this.ai.ai('Agents:Agent:Grants', { agent_id: agentRef });
    return { grants: res.data?.grants || [], error: res.error };
  }

  async grant(
    agentRef: string,
    owner: { groups?: string[]; users?: string[] },
    functions: string[] | '*',
    constraints?: Record<string, string[]>,
  ): Promise<{ data?: any; error?: string }> {
    return this.ai.ai('Agents:Agent:Grant', {
      agent_id: agentRef, owner, functions,
      ...(constraints && Object.keys(constraints).length ? { constraints } : {}),
    });
  }

  async revoke(agentRef: string, grantId: string) {
    return this.ai.ai('Agents:Agent:Revoke', {
      agent_id: agentRef, grant_id: grantId,
    });
  }

  // ── Which credential an agent may use ─────────────────────────────
  //
  // Declaring the shape of a credential is the agent's to do; handing
  // over a filled-in one is a person's. Without a grant an agent sees
  // only what was created against its own definition.

  async secretSlots(agentRef: string): Promise<AgentSecretSlot[]> {
    const res = await this.ai.ai('Agents:Agent:SecretGrants', {
      agent_id: agentRef,
    });
    return res.data?.slots || [];
  }

  async grantSecret(agentRef: string, resourceId: string, secretRef: string) {
    return this.ai.ai('Agents:Agent:SecretGrant', {
      agent_id: agentRef, resource_id: resourceId, secret_ref: secretRef,
    });
  }

  async revokeSecret(agentRef: string, grantId: string) {
    return this.ai.ai('Agents:Agent:SecretRevoke', {
      agent_id: agentRef, grant_id: grantId,
    });
  }

  /** The other installed agents one connected account could be lent
   *  to right now, and the ones it could not with the reason — same
   *  provider only, the backend's decision. */
  async lendable(secretRef: string): Promise<{
    eligible: LendableAgent[]; skipped: LendableSkipped[];
  }> {
    const res = await this.ai.ai('Agents:Agent:SecretLendable', {
      secret_ref: secretRef,
    });
    return { eligible: res.data?.eligible || [], skipped: res.data?.skipped || [] };
  }

  /** One grant per agent, in order; stops at the first refusal and
   *  reports what was done before it. */
  async lendMany(secretRef: string, agents: { agent_id: string; resource_id: string }[]) {
    return this.ai.ai('Agents:Agent:SecretLendMany', {
      secret_ref: secretRef, agents,
    });
  }

  /** One approved agent, with the manifest exactly as it was approved —
   *  what an update is compared against. */
  async get(agentRef: string): Promise<any | null> {
    const res = await this.ai.ai('Agents:Agent:Get', { agent_id: agentRef });
    if (!res.data?.agent) return null;
    return { ...res.data.agent, manifest: res.data.manifest };
  }


  // ── Sample data ──────────────────────────────────────────────────

  /** What loading would do, and whether this person already did. */
  async samples(agentRef: string): Promise<{ samples: SampleSummary | null;
                                             loaded: { records: number; files: number; loaded_at: string } | null }> {
    const res = await this.ai.ai('Agents:Agent:Samples', { agent_id: agentRef });
    return { samples: res.data?.samples ?? null, loaded: res.data?.loaded ?? null };
  }

  async loadSamples(agentRef: string): Promise<{ data?: any; error?: string; status?: number }> {
    return this.ai.ai('Agents:Agent:Loadsamples', { agent_id: agentRef });
  }

  async removeSamples(agentRef: string): Promise<{ data?: any; error?: string }> {
    return this.ai.ai('Agents:Agent:Removesamples', { agent_id: agentRef });
  }

  async uninstall(agentId: string): Promise<{ data?: any; error?: string }> {
    return this.ai.ai('Agents:Agent:Delete', { agent_id: agentId });
  }
}
