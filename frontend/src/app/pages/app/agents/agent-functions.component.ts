import { Component, Input, OnChanges } from '@angular/core';

import { AgentOffer, AgentsService } from 'src/app/services/agents.service';
import { LEVEL_MEANINGS, PERMISSION_LEVELS, levelLabel } from './agent-shared';

/** One input a function accepts, read from its JSON schema. */
interface Param {
  name: string;
  type: string;
  required: boolean;
}

/** One scope this function is checked against, and where the value it is
 *  checked on comes from. */
interface FunctionScope {
  name: string;
  from: string;
}

interface ReviewedFunction {
  id: string;
  qualified: string;
  name: string;
  description: string;
  level: number | null;
  timeout: number | null;
  params: Param[];
  touches: string[];
  scopes: FunctionScope[];
  /** What it gives back, read from its `outputs` schema. */
  returns: Param[];
  /** A schedule may run it with nobody there. */
  schedulable: boolean;
  /** It asks the model of the chat that calls it. */
  llm: boolean;
}

interface ToolGroup {
  id: string;
  name: string;
  description: string;
  functions: ReviewedFunction[];
}

/**
 * Everything this agent can be asked to do.
 *
 * A flat list of dotted names is a list of strings. What an administrator
 * needs to know about a function is how much of it runs without asking,
 * what it takes and returns, what it touches, which authorization scopes
 * gate it, and whether it runs on a schedule or asks a model — so each is
 * read out of the approved manifest and shown together.
 *
 * The manifest is the source. `available()` carries only names, levels
 * and descriptions, so the full document is fetched for the approval and
 * the flat list is the fallback when there is no approval to fetch.
 */
@Component({
  selector: 'app-agent-functions',
  standalone: false,
  templateUrl: './agent-functions.component.html',
  styleUrls: ['./agent-shared.css', './agent-functions.component.css'],
})
export class AgentFunctionsComponent implements OnChanges {
  @Input() agent!: AgentOffer;

  tools: ToolGroup[] = [];
  loading = false;
  /** True once the manifest answered: the detail below is real rather
   *  than the name-only fallback. */
  detailed = false;

  readonly levels = PERMISSION_LEVELS;
  levelLabel = levelLabel;

  readonly meanings = LEVEL_MEANINGS;

  constructor(private service: AgentsService) {}

  /** The approval last read: the same agent at the same version, handed
   *  in again as its row is refreshed, is not read again. */
  private read = '';

  async ngOnChanges(): Promise<void> {
    const approval = `${this.agent?.agent_id}@${this.agent?.installed_version}`;
    if (approval === this.read) return;
    this.read = approval;
    this.tools = this.fromNames();
    this.detailed = false;
    if (!this.agent?.agent_id) return;

    this.loading = true;
    try {
      const approved = await this.service.get(this.agent.agent_id);
      if (approved?.manifest?.tools) {
        this.tools = this.fromManifest(approved.manifest);
        this.detailed = true;
      }
    } catch {
      // No approval to read (an offer nobody installed yet), or no grant
      // to read it. The names are still worth showing.
    } finally {
      this.loading = false;
    }
  }

  /** How many functions sit at each level — what was agreed to, counted
   *  by the thing that matters. */
  get summary(): Array<{ name: string; count: number }> {
    const counts = new Map<string, number>();
    for (const tool of this.tools) {
      for (const fn of tool.functions) {
        if (fn.level === null) continue;
        const name = levelLabel(fn.level);
        counts.set(name, (counts.get(name) || 0) + 1);
      }
    }
    return PERMISSION_LEVELS
      .filter((name) => counts.has(name))
      .map((name) => ({ name, count: counts.get(name)! }));
  }

  get total(): number {
    return this.tools.reduce((n, tool) => n + tool.functions.length, 0);
  }

  /** The levels this agent actually uses — a legend for four when it only
   *  reaches two is noise. */
  get usedLevels(): string[] {
    return this.summary.map((entry) => entry.name);
  }

  // ── Reading the manifest ────────────────────────────────────────────

  private fromManifest(manifest: any): ToolGroup[] {
    return (manifest.tools || []).map((tool: any) => ({
      id: tool.id || tool.name || 'functions',
      name: tool.name || tool.id || 'Functions',
      description: tool.description || '',
      functions: (tool.functions || []).map((fn: any) => ({
        id: fn.id || fn.name,
        qualified: `${tool.id || tool.name}.${fn.id || fn.name}`,
        name: fn.name || fn.id,
        description: fn.description || '',
        level: fn.permission_level ?? tool.permission_level ?? null,
        timeout: fn.timeout_seconds ?? null,
        params: this.paramsOf(fn.inputs),
        touches: this.touchesOf(fn.resources),
        scopes: this.scopesOf(fn.authorization),
        returns: this.paramsOf(fn.outputs),
        schedulable: fn.schedulable === true,
        llm: fn.llm === true,
      })),
    }));
  }

  private paramsOf(inputs: any): Param[] {
    const properties = inputs?.properties || {};
    const required: string[] = inputs?.required || [];
    return Object.entries(properties).map(([name, schema]: [string, any]) => ({
      name,
      type: String(schema?.type || 'any'),
      required: required.includes(name),
    }));
  }

  /** `{data: {note: [create, update]}}` → `note: create, update`. What the
   *  function reaches, in the manifest's own words. */
  private touchesOf(resources: any): string[] {
    const lines: string[] = [];
    for (const kind of ['data', 'files', 'secrets']) {
      const declared = resources?.[kind];
      if (!declared) continue;
      if (Array.isArray(declared)) {
        lines.push(...declared.map((id: string) => String(id)));
        continue;
      }
      for (const [id, verbs] of Object.entries(declared)) {
        const listed = Array.isArray(verbs) ? verbs.join(', ') : String(verbs);
        lines.push(`${id}: ${listed}`);
      }
    }
    return lines;
  }

  /** Which scopes gate this function, and which input carries the value
   *  they are checked on. This is the other half of a grant's limits: a
   *  grant narrows a scope, and this says where the scope gets its value. */
  private scopesOf(authorization: any): FunctionScope[] {
    return Object.entries(authorization?.scopes || {}).map(
      ([name, source]: [string, any]) => ({
        name,
        from: String(source?.from_input || source?.from || ''),
      }));
  }

  /** The name-only view: all a reader gets, and all there is before an
   *  agent is approved. */
  private fromNames(): ToolGroup[] {
    const grouped = new Map<string, ReviewedFunction[]>();
    for (const fn of this.agent?.functions || []) {
      const parts = String(fn.name || '').split('.');
      const tool = fn.tool || (parts.length === 3 ? parts[1] : '') || 'functions';
      const short = parts.length === 3 ? `${parts[1]}.${parts[2]}` : String(fn.name || '');
      grouped.set(tool, [...(grouped.get(tool) || []), {
        id: short.split('.').pop() || short,
        qualified: short,
        name: short,
        description: fn.description || '',
        level: fn.permission_level,
        timeout: fn.timeout_seconds,
        params: [], touches: [], scopes: [], returns: [],
        schedulable: false, llm: fn.llm === true,
      }]);
    }
    return [...grouped.entries()].map(([id, functions]) => ({
      id, name: id, description: '', functions,
    }));
  }
}
