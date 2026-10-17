import { Component, OnInit } from '@angular/core';

import { clockTime } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';
import {
  MonitoringService, SummaryWindow, TokenCounts, TokenPurpose, TokenUsage,
} from 'src/app/services/monitoring.service';
import { compact, niceTop } from './activity-overview.component';

/** One column of the tokens chart, ready to draw. */
interface Column {
  step: TokenCounts & { at: string };
  /** Heights, as a share of the chart's top, 0 to 100. */
  cachedHeight: number;
  restHeight: number;
  label: string;
  tip: string;
}

/** What each kind of call is, in the words a person reads. */
export const PURPOSE_WORDS: Record<TokenPurpose, string> = {
  assistant: 'The assistant',
  agent: "Agents' own model calls",
  title: 'Naming chats',
  summary: 'Folding long chats',
  code_review: 'Reading proposed code',
  rerank: 'Choosing agents',
  embed: 'Embedding for routing',
};

/**
 * Monitoring's Tokens: what the organization's chats and agents had
 * models use in a window, from the record of every model call
 * (Agents:Monitor:Tokens, docs/system/monitoring.md).
 *
 * Laid out as the Overview is, and in its styles: the numbers first, as
 * tiles; then the one chart over time — the tokens read from the cache
 * in the quiet neutral, the rest in the accent — with a legend and a
 * table view; then tables by model, by what the calls were for, by
 * agent and by person. The cost is an estimate everywhere it is said,
 * and says how much of the tokens it could not price.
 */
@Component({
  selector: 'app-token-usage',
  standalone: false,
  templateUrl: './token-usage.component.html',
  styleUrls: ['./activity-overview.component.css', './token-usage.component.css'],
})
export class TokenUsageComponent implements OnInit {
  window: SummaryWindow = '7d';
  readonly windows: Choice<SummaryWindow>[] = [
    { value: '24h', name: '24 hours' },
    { value: '7d', name: '7 days' },
    { value: '30d', name: '30 days' },
  ];

  usage: TokenUsage | null = null;
  loading = true;
  error = '';
  /** The chart as its table: every value, without the colour. */
  asTable = false;

  top = 0;
  columns: Column[] = [];

  constructor(private monitoring: MonitoringService) {}

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  async choose(window: SummaryWindow): Promise<void> {
    this.window = window;
    await this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.error = '';
    const usage = await this.monitoring.tokens(this.window);
    this.loading = false;
    if (!usage) {
      this.error = 'The tokens could not be added up. Try again in a moment.';
      return;
    }
    this.usage = usage;
    this.draw(usage);
  }

  // ── The tiles ───────────────────────────────────────────────────

  get totals(): TokenCounts { return this.usage!.totals; }

  /** Whether some of the tokens have no price: the estimate is then
   *  of the rest only, and the tile says so. */
  get partlyPriced(): boolean { return (this.usage?.cost.unpriced_tokens || 0) > 0; }

  // ── The chart ───────────────────────────────────────────────────

  private draw(usage: TokenUsage): void {
    this.top = niceTop(Math.max(0, ...usage.series.map((step) => step.tokens)));
    const every = usage.unit === 'hour' ? 6 : usage.series.length > 10 ? 5 : 1;
    this.columns = usage.series.map((step, index) => {
      const at = new Date(step.at);
      return {
        step,
        cachedHeight: this.top ? (step.cache_read / this.top) * 100 : 0,
        restHeight: this.top ? ((step.tokens - step.cache_read) / this.top) * 100 : 0,
        label: index % every === 0 ? this.stepLabel(at, usage.unit) : '',
        tip: [
          this.stepTitle(at, usage.unit),
          `${step.tokens.toLocaleString()} tokens in ${count(step.calls, 'call')}`,
          `${step.cache_read.toLocaleString()} read from the cache · ${(step.tokens - step.cache_read).toLocaleString()} not`,
          `${this.money(step.cost)} estimated`,
        ].join('\n'),
      };
    });
  }

  private stepLabel(at: Date, unit: 'hour' | 'day'): string {
    if (unit === 'hour') return clockTime(at.getTime());
    return this.window === '7d'
      ? at.toLocaleDateString(undefined, { weekday: 'short' })
      : at.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  stepTitle(at: Date | string, unit: 'hour' | 'day' = this.usage?.unit || 'day'): string {
    const when = typeof at === 'string' ? new Date(at) : at;
    const day = when.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    return unit === 'hour' ? `${day}, ${clockTime(when.getTime())}` : day;
  }

  get ticks(): number[] {
    return this.top ? [this.top, this.top / 2, 0] : [0];
  }

  byStep(_: number, column: Column): string { return column.step.at; }

  // ── Words ───────────────────────────────────────────────────────

  number(value: number): string { return compact(value); }

  /** A share, 0 to 1, as a whole percentage — or below one, "<1%". */
  percent(share: number): string {
    if (!share) return '0%';
    if (share < 0.01) return '<1%';
    return `${Math.round(share * 100)}%`;
  }

  /** US dollars, as an estimate is said: cents where there are any,
   *  and a fraction of a cent as less than one. */
  money(value: number): string {
    if (!value) return '$0.00';
    if (value < 0.01) return '<$0.01';
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency: 'USD', maximumFractionDigits: value < 100 ? 2 : 0,
    }).format(value);
  }

  /** A row's cost, or a dash where none of its tokens has a price. */
  rowCost(row: TokenCounts): string {
    return row.tokens && row.unpriced_tokens === row.tokens ? '—' : this.money(row.cost);
  }

  purpose(purpose: TokenPurpose): string { return PURPOSE_WORDS[purpose] || purpose; }

  get inWindow(): string {
    return { '24h': 'in the last 24 hours', '7d': 'in the last 7 days', '30d': 'in the last 30 days' }[this.window];
  }
}

function count(value: number, word: string): string {
  return `${value.toLocaleString()} ${word}${value === 1 ? '' : 's'}`;
}
