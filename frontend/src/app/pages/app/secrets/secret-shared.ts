import {
  DefinitionField, OAUTH_FIELD_NAMES, SecretDefinition,
} from 'src/app/services/secret-definitions.service';
import { Secret, SecretUser } from 'src/app/services/secrets.service';

/**
 * What the list, a secret's own page and its form all read off a
 * secret, said once. A secret is one of two kinds: a credential an
 * agent declared a shape for, or a login an agent asked for as it
 * worked on a site.
 */

/** An agent as the backend names it here is its id ("google_drive"):
 *  said as a name, "Google drive". A name that is one already stays. */
export function agentLabel(name: string): string {
  const words = String(name || '').replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : '';
}

/** The platform's bookkeeping on a saved login: its heading and its
 *  consents, never fields of its own. */
const LOGIN_BOOKKEEPING = ['host', 'account', 'status', 'consents', 'sites'];

/** A login an agent asked for as it worked (call.credential): an
 *  ordinary secret under a host's family, used by consent (this agent
 *  on this site) and not by grant. */
export function isSiteLogin(secret: Secret): boolean {
  return String(secret.resource_id || '').startsWith('site__');
}

export function loginHost(secret: Secret): string {
  return String(secret.keys?.['host'] || secret.resource_id.replace(/^site__/, '').replace(/_/g, '.'));
}

/** What a secret is called in a list: its name, or for a login the
 *  site and the account on it. */
export function secretTitle(secret: Secret): string {
  if (!isSiteLogin(secret)) return secret.name;
  const account = String(secret.keys?.['account'] || '');
  return account ? `${loginHost(secret)} · ${account}` : loginHost(secret);
}

/** One agent allowed to use a login on one site. */
export interface Consent {
  agent_ref: string;
  name: string;
  site: string;
  /** As it is stored: `<agent ref>@<site>`. */
  pair: string;
}

export function consentsOf(secret: Secret): Consent[] {
  const names = new Map((secret.used_by_agents || []).map((agent) => [agent.agent_ref, agent.name]));
  return String(secret.keys?.['consents'] || '').split(',')
    .map((pair) => pair.trim()).filter(Boolean)
    .map((pair) => {
      const at = pair.indexOf('@');
      const agent_ref = at > 0 ? pair.slice(0, at) : pair;
      return { agent_ref, name: agentLabel(names.get(agent_ref) || agent_ref), site: at > 0 ? pair.slice(at + 1) : '', pair };
    });
}

/** The agents a secret was lent to by a grant. A consent on a saved
 *  login rides in the same list and is not one: it has no slot. */
export function lentTo(secret: Secret): SecretUser[] {
  return (secret.used_by_agents || []).filter((agent) => !!agent.resource_id)
    .map((agent) => ({ ...agent, name: agentLabel(agent.name) }));
}

/** Every agent that reads a secret: the one whose own slot it was
 *  saved under, which needs no grant, then those it was lent to or
 *  allowed on a site. */
export function agentsUsing(secret: Secret): string[] {
  const names = (secret.used_by_agents || []).map((agent) => agentLabel(agent.name));
  if (secret.home_agent?.name) names.unshift(agentLabel(secret.home_agent.name));
  return [...new Set(names.filter(Boolean))];
}

export function definitionOf(secret: Secret, definitions: SecretDefinition[]): SecretDefinition | undefined {
  return definitions.find((definition) => definition.definition_id === secret.resource_id);
}

export function isOauth(secret: Secret, definitions: SecretDefinition[]): boolean {
  return !!definitionOf(secret, definitions)?.oauth;
}

export function needsReconnect(secret: Secret, definitions: SecretDefinition[]): boolean {
  return isOauth(secret, definitions) && secret.keys?.['status'] === 'needs_reconnect';
}

/** The shape moved on without it: the definition has a newer version
 *  than the one the secret was made under. */
export function isOutdated(secret: Secret, definitions: SecretDefinition[]): boolean {
  const latest = definitionOf(secret, definitions)?.version ?? secret.definition_version;
  return secret.definition_version < latest;
}

/** A provider's id as a name: "google" is Google. */
export function providerName(definition: SecretDefinition | null | undefined): string {
  const id = definition?.oauth?.provider || '';
  return id ? id.charAt(0).toUpperCase() + id.slice(1) : 'the provider';
}

/** The fields a person fills in: for a connected account, only what
 *  the agent declared beyond the platform's own. */
export function editableFields(definition: SecretDefinition): DefinitionField[] {
  if (!definition.oauth) return definition.fields;
  return definition.fields.filter((field) => !OAUTH_FIELD_NAMES.includes(field.name));
}

/** The one plaintext value that says which thing a secret is for: an
 *  endpoint, an account, a site. '' when none does. */
export function identifier(secret: Secret, definitions: SecretDefinition[]): string {
  if (isSiteLogin(secret)) return '';
  const keys = secret.keys || {};
  if (isOauth(secret, definitions)) return String(keys['account'] || '');
  const telling = ['url', 'endpoint', 'host', 'base_url', 'account', 'username', 'email', 'site', 'domain'];
  for (const name of Object.keys(keys)) {
    if (telling.some((word) => name.toLowerCase().includes(word)) && keys[name]) return String(keys[name]);
  }
  return '';
}

/** One field of a secret, as its page lists it. */
export interface ShownField {
  label: string;
  /** The plaintext value; '' for a field that is not shown. */
  value: string;
  /** Encrypted: said to be set, never shown. */
  hidden: boolean;
}

/** A secret's fields in its definition's order: plaintext as text,
 *  encrypted as set and never shown. A connected account's platform
 *  fields (tokens, expiry) are left out: its page says how it stands. */
export function shownFields(secret: Secret, definitions: SecretDefinition[]): ShownField[] {
  const definition = definitionOf(secret, definitions);
  const keys = secret.keys || {};
  const skip = new Set([
    ...(definition?.oauth ? OAUTH_FIELD_NAMES : []),
    ...(isSiteLogin(secret) ? LOGIN_BOOKKEEPING : []),
  ]);
  if (!definition) {
    return Object.entries(keys).filter(([name]) => !skip.has(name))
      .map(([name, value]) => ({ label: name, value: String(value ?? ''), hidden: false }));
  }
  return definition.fields.filter((field) => !skip.has(field.name)).map((field) => ({
    label: field.label || field.name,
    value: field.storage === 'values' ? '' : String(keys[field.name] ?? ''),
    hidden: field.storage === 'values',
  }));
}

/** What deleting a secret is asked with, on the Secrets page and on an
 *  agent's own: what is lost, who is left without it, and whether it
 *  has to be taken back from agents it is lent to first. */
export function deleteQuestion(secret: Secret): { title: string; body: string; confirm: string; takeBack: boolean } {
  const lent = lentTo(secret).map((agent) => agent.name);
  return {
    title: `Delete ${secretTitle(secret)}?`,
    body: [
      'The stored values are destroyed and cannot be read back.',
      secret.home_agent ? `${agentLabel(secret.home_agent.name)} is left without it and will need another.` : '',
      lent.length ? `It is lent to ${lent.join(' and ')}; deleting takes it back from ${lent.length === 1 ? 'it' : 'them'}.` : '',
    ].filter(Boolean).join(' '),
    confirm: lent.length ? 'Take back and delete' : 'Delete',
    takeBack: lent.length > 0,
  };
}
