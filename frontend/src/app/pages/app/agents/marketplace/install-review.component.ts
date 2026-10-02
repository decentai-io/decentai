import { Component, EventEmitter, Input, Output } from '@angular/core';

import { AgentSource, CatalogEntry } from 'src/app/services/agents.service';
import {
  AgentNetwork, PERMISSION_LEVELS, credentialHost, levelLabel, networkNote, networkOf,
} from '../agent-shared';

/** One function an agent declares, as the review reads it. */
interface ReviewedFunction {
  name: string;
  description: string;
  level: number | null;
}

/**
 * What installing an agent would agree to.
 *
 * The whole approval step: every function the manifest declares and the
 * permission each one needs, what the agent wants to keep, and what it
 * would install to run. On an update it also says what CHANGED — a
 * version number says something is different, this says what, which is
 * the only form of the question an administrator can actually answer.
 *
 * Nothing here has run. A catalog is read without executing a line of
 * it, and approving is what turns a proposal into an install.
 */
@Component({
  selector: 'app-install-review',
  standalone: false,
  templateUrl: './install-review.component.html',
  styleUrls: [
    '../../data-shared.css',
    '../agent-shared.css',
    './marketplace.component.css',
  ],
})
export class InstallReviewComponent {
  @Input() source!: AgentSource;
  @Input() entry!: CatalogEntry;
  /** The manifest as it was approved before, when this is an update.
   *  Absent for a first install — there is nothing to compare with. */
  @Input() approved: any = null;
  /** Reading an agent that is already installed and current: the page
   *  shows what was agreed to, and offers nothing to approve. */
  @Input() installed = false;
  @Input() loading = false;
  @Input() busy = false;

  @Output() approve = new EventEmitter<void>();
  @Output() close = new EventEmitter<void>();

  /** The same naming the installed agent's Functions tab uses. */
  levelLabel = levelLabel;
  /** What each level means. Whether a function asks first depends on
   *  the chat that calls it: it asks when its level is above that
   *  chat's trust level. */
  readonly permissionMeaning: Record<string, { label: string; title: string; description: string }> = {
    read: {
      label: 'Level 0 · reads',
      title: 'Reads information',
      description: 'Looks up data available to the agent and changes nothing. Runs in every chat without asking.',
    },
    change: {
      label: 'Level 1 · ordinary change',
      title: 'Changes data inside the platform',
      description: 'Creates or updates records this agent keeps. Asks first in a chat whose trust level is 0.',
    },
    sandboxed: {
      label: 'Level 2 · wider change',
      title: 'Makes a change with wider reach',
      description: 'Still inside the platform. Asks first in a chat whose trust level is below 2.',
    },
    external: {
      label: 'Level 3 · outside action',
      title: 'Acts outside DecentAI',
      description: 'Contacts or changes an outside service. Asks first in every chat whose trust level is below 3.',
    },
  };

  get manifest(): any {
    return this.entry?.manifest || {};
  }

  get name(): string {
    return this.manifest.agent?.name || this.entry?.id || 'This agent';
  }

  get version(): string {
    return this.manifest.agent?.version || '';
  }

  get isUpdate(): boolean {
    return !!this.entry?.installed_version;
  }

  get functions(): ReviewedFunction[] {
    return this.functionsOf(this.manifest);
  }

  /** How many functions sit at each level. How much of this runs
   *  unattended is the decision being made; a list of names does not
   *  answer it. */
  get tally(): Array<{ name: string; count: number }> {
    const counts = new Map<string, number>();
    for (const fn of this.functions) {
      if (fn.level === null) continue;
      const name = this.levelLabel(fn.level);
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    return PERMISSION_LEVELS
      .filter((name) => counts.has(name))
      .map((name) => ({ name, count: counts.get(name)! }));
  }

  /** What the agent asks to keep, by kind: credentials, data, files. */
  get resources(): Array<{ kind: string; items: any[] }> {
    const declared = this.manifest.resources || {};
    return ['secrets', 'data', 'files']
      .map((kind) => ({ kind, items: declared[kind] || [] }))
      .filter((group) => group.items.length);
  }

  /** The records and files it keeps — everything but credentials, which
   *  get their own card. */
  get keptResources(): Array<{ kind: string; items: any[] }> {
    return this.resources.filter((group) => group.kind !== 'secrets');
  }

  /** The credentials this agent will ask for. */
  get secretSlots(): any[] {
    return this.manifest.resources?.secrets || [];
  }

  get dependencies(): string[] {
    return this.manifest.implementation?.dependencies || [];
  }

  /** Where the agent connects: part of what approving agrees to. */
  get network(): AgentNetwork {
    return networkOf(this.manifest);
  }

  get networkNote(): string {
    return networkNote(this.network);
  }

  readonly credentialHost = credentialHost;

  /** What approving this update actually changes. Empty on a first
   *  install, and empty on an update that changes none of it. */
  get changes(): string[] {
    const approved = this.approved?.manifest;
    if (!approved) return [];

    const before = this.functionsOf(approved);
    const after = this.functions;
    const lines: string[] = [];

    const previous = new Map(before.map((fn) => [fn.name, fn]));
    const current = new Set(after.map((fn) => fn.name));
    for (const fn of after) {
      const was = previous.get(fn.name);
      if (!was) {
        lines.push(`New function: ${fn.name}`
          + (fn.level !== null ? ` (needs ${this.levelLabel(fn.level)})` : ''));
      } else if (was.level !== fn.level) {
        lines.push(`${fn.name}: permission ${this.levelLabel(was.level)} `
          + `→ ${this.levelLabel(fn.level)}`);
      }
    }
    for (const fn of before) {
      if (!current.has(fn.name)) lines.push(`Function removed: ${fn.name}`);
    }

    const had = new Set(approved.implementation?.dependencies || []);
    for (const dependency of this.dependencies) {
      if (!had.has(dependency)) lines.push(`New package: ${dependency}`);
    }

    lines.push(...this.networkChanges(networkOf(approved), this.network));

    const kept = new Set(this.resourceKeys(approved));
    for (const key of this.resourceKeys(this.manifest)) {
      if (!kept.has(key)) {
        const [kind, id] = key.split(':');
        lines.push(`New ${kind.replace(/s$/, '')}: ${id}`);
      }
    }
    return lines;
  }

  /** What an update changes about where the agent connects. Only what
   *  widens is worth a line: a host it did not reach before. */
  private networkChanges(before: AgentNetwork, after: AgentNetwork): string[] {
    if (after.any) {
      return before.any ? [] : ['Now connects to any website'];
    }
    if (before.any) return [];
    const had = new Set([...before.hosts, ...before.from_secrets]);
    return [
      ...(after.proposed && !before.proposed
        ? ['Now runs code you allow on a card, and reaches the sites that card names'] : []),
      ...after.hosts.filter((host) => !had.has(host))
        .map((host) => `New host: ${host}`),
      ...after.from_secrets.filter((field) => !had.has(field))
        .map((field) => `New host, read from its credential: ${field}`),
    ];
  }

  /** Functions grouped by the TOOL that carries them — the manifest's
   *  own structure, and the hierarchy the flat list was missing. */
  get toolGroups(): Array<{
    tool: string; description: string; functions: ReviewedFunction[];
  }> {
    return (this.manifest.tools || []).map((tool: any) => ({
      tool: tool.id || tool.name || 'tool',
      description: tool.description || '',
      functions: (tool.functions || []).map((fn: any) => ({
        name: fn.id || fn.name,
        description: fn.description || '',
        level: fn.permission_level ?? tool.permission_level ?? null,
      })),
    })).filter((group: any) => group.functions.length);
  }

  private functionsOf(manifest: any): ReviewedFunction[] {
    return (manifest?.tools || []).flatMap((tool: any) =>
      (tool.functions || []).map((fn: any) => ({
        name: `${tool.id || tool.name}.${fn.id || fn.name}`,
        description: fn.description || '',
        level: fn.permission_level ?? tool.permission_level ?? null,
      })));
  }

  private resourceKeys(manifest: any): string[] {
    const declared = manifest?.resources || {};
    return ['secrets', 'data', 'files'].flatMap((kind) =>
      (declared[kind] || []).map((item: any) => `${kind}:${item.id}`));
  }

  permissionLabel(level: string): string {
    return this.permissionMeaning[level]?.label || level;
  }

  toolName(value: string): string {
    return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

}
