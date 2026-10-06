/**
 * Plain English for the audit trail's event vocabulary, shared by the
 * chat's activity dialog and the Audit page so the two never disagree
 * about what an event is called. Anything unrecognised falls through
 * to its own name rather than being hidden: a trail that quietly drops
 * events it does not know is worse than an ugly one.
 */

const LABELS: Record<string, string> = {
  'execution': 'Function ran',
  'approval.requested': 'Approval requested',
  'approval.resolved': 'Approval answered',
  'secret.use': 'Credential read',
  'llm.use': 'Model key read',
  'agent.installed': 'Agent installed',
  'agent.deleted': 'Agent uninstalled',
  'agent.granted': 'Agent granted',
  'agent.revoked': 'Agent grant revoked',
  'agent.secret_granted': 'Agent lent a credential',
  'agent.secret_revoked': 'Credential taken back from agent',
  'agent.samples_loaded': 'Sample data loaded',
  'agent.samples_removed': 'Sample data removed',
  'agent_source.created': 'Agent source added',
  'agent_source.updated': 'Agent source changed',
  'agent_source.refreshed': 'Agent source refreshed',
  'agent_source.deleted': 'Agent source removed',
  'agent_source.transferred': 'Agent source handed over',
  'owner.users': 'Sharing changed',
  'owner.groups': 'Sharing changed',
  'user.disabled': 'Person disabled',
  'user.enabled': 'Person enabled',
  'user.deleted': 'Person deleted',
  'mcp.added': 'MCP server added',
  'platform.action': 'Platform action',
  'settings.safety': 'Safety setting changed',
};

const STATUS_WORDS: Record<string, string> = {
  success: 'succeeded',
  error: 'failed',
  denied: 'denied by you',
  refused: 'refused by the platform',
};

const SAFETY_ROWS: Record<string, string> = {
  blocked_sites: 'blocked sites',
  scripts: 'scripts in a page',
  programs: 'programs',
  packages: 'packages',
  allowed_packages: 'the list of packages',
};

/** Where a call connected, as the platform's proxy counted it: the
 *  first few names, and how many more. */
function reachedWords(details: any): string {
  const reached: Array<{ host?: string }> = Array.isArray(details?.reached) ? details.reached : [];
  if (!reached.length) return '';
  const named = reached.slice(0, 3).map((site) => site.host).filter(Boolean);
  const more = reached.length - named.length + Number(details?.reached_more || 0);
  return `reached ${named.join(', ')}${more > 0 ? ` and ${more} more` : ''}`;
}

export function auditLabel(event: any): string {
  const type = String(event?.event_type || '');
  if (type === 'execution') {
    const details = event?.details || {};
    const agent = details.agent_name ? `${details.agent_name} · ` : '';
    return `${agent}${event?.function || 'function'}`;
  }
  return LABELS[type] || type || 'Event';
}

/** One line under the label: the outcome, the level, the duration, or
 *  the decision — what a person scanning the trail wants first. */
export function auditSummary(event: any): string {
  const type = String(event?.event_type || '');
  const details = event?.details || {};
  if (type === 'execution') {
    const parts: string[] = [STATUS_WORDS[details.status] || String(details.status || '')];
    if (details.permission_level != null) parts.push(`level ${details.permission_level}`);
    if (details.duration_ms != null) parts.push(durationLabel(details.duration_ms));
    if (details.resumed) parts.push('after approval');
    if (details.error) parts.push(String(details.error));
    parts.push(reachedWords(details));
    return parts.filter(Boolean).join(' · ');
  }
  if (type === 'approval.requested') {
    return [event?.function, details.permission_level != null ? `level ${details.permission_level}` : '']
      .filter(Boolean).join(' · ');
  }
  if (type === 'approval.resolved') {
    // A card the Safety setting settled was decided by nobody: the
    // runtime opened it, and the organization's own choice answered.
    if (details.decision === 'setting') {
      return [event?.function, 'allowed by the Safety setting'].filter(Boolean).join(' · ');
    }
    return [event?.function, details.decision ? `${details.decision}` : '', event?.actor ? `by ${event.actor}` : '']
      .filter(Boolean).join(' · ');
  }
  if (type === 'settings.safety') {
    return Object.keys(details).map((row) => SAFETY_ROWS[row] || row).join(', ');
  }
  if (type === 'secret.use' || type === 'llm.use') {
    return (event?.resource_refs || []).join(', ');
  }
  if (type === 'platform.action') {
    return [event?.function, details.outcome, details.reason]
      .filter(Boolean).join(' · ');
  }
  const rest: string[] = [];
  for (const [key, value] of Object.entries(details)) {
    if (typeof value !== 'object') rest.push(`${key}: ${value}`);
  }
  return rest.join(' · ');
}

/** ok | bad | muted | live — the chip colour an outcome deserves. */
export function auditTone(event: any): string {
  const type = String(event?.event_type || '');
  const details = event?.details || {};
  if (type === 'execution') {
    if (details.status === 'success') return 'ok';
    if (details.status === 'error') return 'bad';
    return 'muted';
  }
  if (type === 'approval.resolved') return details.decision === 'approve' ? 'ok' : 'muted';
  if (type === 'approval.requested') return 'live';
  if (type === 'platform.action') return details.outcome === 'success' ? 'ok'
    : details.outcome === 'denied' || details.outcome === 'failed' ? 'bad' : 'muted';
  return 'muted';
}

export function durationLabel(ms: number): string {
  const n = Number(ms) || 0;
  if (n < 1000) return `${n} ms`;
  if (n < 60000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)} s`;
  return `${Math.floor(n / 60000)}m ${Math.round((n % 60000) / 1000)}s`;
}
