import { Component, Input } from '@angular/core';
import { MarkdownService } from 'ngx-markdown';

import { headingsDown } from 'src/app/components/ui/markdown-levels';

import { ChatMarkdownService } from '../../ai/chat/chat-message/chat-markdown.service';

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

/** How long a README is shown whole; a longer one is folded. */
const README_FOLD_CHARS = 900;

let created = 0;

/** A size in bytes as a person reads it: "812 B", "4.2 KB", "1.3 MB". */
function sizeWords(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * What installing one agent would agree to, in the order a careful
 * person reads it: what it is and its README, what an update changes,
 * what it needs by name, where it connects, what it may do, and the
 * code. The page around it (the title, Approve) is the marketplace's;
 * in a review of several agents each is one of these, in brief.
 *
 * On an update it also says what CHANGED — a
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
  styleUrls: ['../agent-shared.css', './install-review.component.css'],
  // A README is somebody else's markdown: what it would load from
  // elsewhere is taken out, as in a chat.
  providers: [{ provide: MarkdownService, useClass: ChatMarkdownService }],
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
  /** One of several agents under review: its own name at the head,
   *  no README, and the functions folded. */
  @Input() brief = false;

  /** For the ids that tie each section to its title. */
  readonly id = `reading-${++created}`;
  readmeOpen = false;
  /** The files as last read, and as they are shown: made once, since a
   *  list made anew on every pass is drawn anew on every pass. */
  private filesListed: { path: string; size: number }[] = [];
  private filesShown: { path: string; size: string; link: string }[] = [];

  /** The same naming the installed agent's Functions tab uses. */
  levelLabel = levelLabel;
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

  /** The kinds of record or file a group keeps, by name. */
  kindsOf(group: { items: any[] }): string {
    return group.items.map((item) => item.label || item.id).join(', ');
  }

  /** The README with its headings two levels down: the page has the
   *  one `h1`, and "README" is an `h2` above them. */
  get readme(): string {
    const written = this.entry?.readme || '';
    if (written !== this.readmeWritten) {
      this.readmeWritten = written;
      this.readmeShown = headingsDown(written, 2);
    }
    return this.readmeShown;
  }
  private readmeWritten = '';
  private readmeShown = '';

  get readmeLong(): boolean {
    return (this.entry?.readme || '').length > README_FOLD_CHARS;
  }

  /** Where the code is, in words: the repository and the agent's
   *  folder in it. */
  get codePlace(): string {
    const folder = this.entry?.path && this.entry.path !== '.' ? ` · ${this.entry.path}` : '';
    return `${this.source?.url || ''}${folder}`;
  }

  /** The agent's folder at the commit that would be installed, where
   *  the repository is one a browser can open. */
  get codeLink(): string {
    const url = String(this.source?.url || '').replace(/\.git$/, '');
    if (!/^https:\/\//.test(url)) return '';
    if (!this.source.sha) return url;
    const folder = this.entry?.path && this.entry.path !== '.' ? `/${this.entry.path}` : '';
    return `${url}/tree/${this.source.sha}${folder}`;
  }

  /** The package's files, each with the page of it at the commit that
   *  would be installed, where the repository is one a browser opens. */
  get files(): { path: string; size: string; link: string }[] {
    const listed = this.entry?.files || [];
    if (listed !== this.filesListed) {
      this.filesListed = listed;
      const tree = this.source?.sha ? this.codeLink : '';
      this.filesShown = listed.map((file) => ({
        path: file.path,
        size: sizeWords(file.size),
        link: tree ? `${tree.replace('/tree/', '/blob/')}/${file.path}` : '',
      }));
    }
    return this.filesShown;
  }

  /** "12 files · 48 KB", for the fold that holds them. */
  get filesSummary(): string {
    const listed = this.entry?.files || [];
    const count = listed.length + (this.entry?.files_more || 0);
    const bytes = listed.reduce((sum, file) => sum + file.size, 0);
    return `${count} ${count === 1 ? 'file' : 'files'} · ${sizeWords(bytes)}${this.entry?.files_more ? ' and more' : ''}`;
  }

  /** The package kept at the last approval, short enough to read. */
  get digest(): string {
    return String(this.approved?.package_digest || '').replace('sha256:', '').slice(0, 12);
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
}
