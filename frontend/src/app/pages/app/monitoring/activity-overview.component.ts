import { Component, OnInit } from '@angular/core';

import { clockTime, fullDate, relativeDate } from 'src/app/components/ui/dates';
import { Choice } from 'src/app/components/ui/radio-keys';
import {
  ActivityStep, ActivitySummary, MonitoringService, SummaryWindow, UsageNow,
} from 'src/app/services/monitoring.service';

/** One column of the calls chart, ready to draw. */
interface Column {
  step: ActivityStep;
  /** Heights, as a share of the chart's top, 0 to 100. */
  okHeight: number;
  badHeight: number;
  label: string;
  tip: string;
}

/**
 * Monitoring's Overview: what the organization's agents did in a window,
 * added up from the audit trail (Agents:Monitor:Summary), beside how
 * many run now (Agents:Monitor:Usage).
 *
 * Read the way the dataviz rules ask: the numbers first, as tiles; then
 * the one chart over time, its calls that succeeded quiet and the ones
 * that did not in the danger hue (an emphasis pair validated for colour
 * vision in both themes), with a legend and a table view; then two
 * single-series bar lists in the accent; then the problems, in words.
 */
@Component({
  selector: 'app-activity-overview',
  standalone: false,
  templateUrl: './activity-overview.component.html',
  styleUrl: './activity-overview.component.css',
})
export class ActivityOverviewComponent implements OnInit {
  window: SummaryWindow = '7d';
  readonly windows: Choice<SummaryWindow>[] = [
    { value: '24h', name: '24 hours' },
    { value: '7d', name: '7 days' },
    { value: '30d', name: '30 days' },
  ];

  summary: ActivitySummary | null = null;
  usage: UsageNow | null = null;
  loading = true;
  error = '';
  /** The chart as its table: every value, without the colour. */
  asTable = false;

  /** The chart's top, rounded up to a number an axis can say. */
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
    // What runs now is the runtime's to say, and a runtime that is slow
    // or away must not hold the rest back: its tile fills in when it
    // answers. The rest is the backend's own database.
    void this.readUsage();
    const summary = await this.monitoring.summary(this.window);
    this.loading = false;
    if (!summary) {
      this.error = 'What the agents did could not be added up. Try again in a moment.';
      return;
    }
    this.summary = summary;
    this.draw(summary);
  }

  private async readUsage(): Promise<void> {
    const usage = await this.monitoring.usage().catch(() => null);
    this.usage = usage && !usage.error ? usage : null;
    this.usageRead = true;
  }

  /** Whether the runtime has answered (or failed to) about what runs. */
  usageRead = false;

  // ── The tiles ───────────────────────────────────────────────────

  get calls() { return this.summary!.calls; }

  /** The calls that did not succeed: failed, or refused by a gate or
   *  a person. */
  get notOk(): number { return this.calls.failed + this.calls.refused; }

  get okShare(): string {
    if (!this.calls.total) return '';
    return `${Math.round((this.calls.ok / this.calls.total) * 100)}% succeeded`;
  }

  get running(): number | null {
    return this.usage?.counted ? this.usage.agents.length : null;
  }

  /** How full the agents' container is, for the running tile. */
  get fullness(): number | null {
    const given = this.usage?.limits?.memory;
    if (!given || this.usage?.memory == null) return null;
    return Math.min(100, Math.round((this.usage.memory / given) * 100));
  }

  // ── The chart ───────────────────────────────────────────────────

  private draw(summary: ActivitySummary): void {
    const highest = Math.max(0, ...summary.series.map((step) => step.ok + step.failed + step.refused));
    this.top = niceTop(highest);
    const every = summary.unit === 'hour' ? 6 : summary.series.length > 10 ? 5 : 1;
    this.columns = summary.series.map((step, index) => {
      const bad = step.failed + step.refused;
      const at = new Date(step.at);
      return {
        step,
        okHeight: this.top ? (step.ok / this.top) * 100 : 0,
        badHeight: this.top ? (bad / this.top) * 100 : 0,
        label: index % every === 0 ? this.stepLabel(at, summary.unit) : '',
        tip: [
          this.stepTitle(at, summary.unit),
          `${count(step.ok + bad, 'call')}`,
          `${step.ok} succeeded · ${step.failed} failed · ${step.refused} refused`,
        ].join('\n'),
      };
    });
  }

  /** What the axis says under a column. */
  private stepLabel(at: Date, unit: 'hour' | 'day'): string {
    if (unit === 'hour') return clockTime(at.getTime());
    return this.window === '7d'
      ? at.toLocaleDateString(undefined, { weekday: 'short' })
      : at.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  /** What the column's tooltip and its table row are headed with. */
  stepTitle(at: Date | string, unit: 'hour' | 'day' = this.summary?.unit || 'day'): string {
    const when = typeof at === 'string' ? new Date(at) : at;
    const day = when.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    return unit === 'hour' ? `${day}, ${clockTime(when.getTime())}` : day;
  }

  /** The axis's three lines: the top, its half, nothing. */
  get ticks(): number[] {
    return this.top ? [this.top, this.top / 2, 0] : [0];
  }

  byStep(_: number, column: Column): string { return column.step.at; }

  // ── The bar lists ───────────────────────────────────────────────

  share(value: number, of: number[]): number {
    const most = Math.max(...of, 0);
    return most ? Math.max(2, (value / most) * 100) : 0;
  }

  get agentCalls(): number[] { return (this.summary?.agents || []).map((agent) => agent.calls); }
  get hostConnections(): number[] { return (this.summary?.hosts || []).map((host) => host.connections); }

  // ── Words ───────────────────────────────────────────────────────

  number(value: number): string {
    return compact(value);
  }

  duration(ms: number): string {
    if (!ms) return '—';
    if (ms < 1000) return `${ms} ms`;
    const seconds = ms / 1000;
    return seconds < 60 ? `${seconds.toFixed(seconds < 10 ? 1 : 0)} s` : `${Math.round(seconds / 60)} min`;
  }

  /** The window, said in words for headings: "in the last 7 days". */
  get inWindow(): string {
    return { '24h': 'in the last 24 hours', '7d': 'in the last 7 days', '30d': 'in the last 30 days' }[this.window];
  }

  when(at: string): string { return relativeDate(at); }
  fullWhen(at: string): string { return fullDate(at); }

  statusWord(status: string): string {
    return status === 'refused' || status === 'denied' ? (status === 'denied' ? 'Denied' : 'Refused') : 'Failed';
  }
}

/** A top an axis can say: 1, 2 or 5 times a power of ten, at or above
 *  the highest value. */
export function niceTop(highest: number): number {
  if (highest <= 0) return 0;
  const power = 10 ** Math.floor(Math.log10(highest));
  for (const step of [1, 2, 5, 10]) {
    if (step * power >= highest) return step * power;
  }
  return 10 * power;
}

/** 1,284 · 12.9K · 4.2M — proportional figures, as tiles want them. */
export function compact(value: number): string {
  if (Math.abs(value) < 10_000) return value.toLocaleString();
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

function count(value: number, word: string): string {
  return `${value.toLocaleString()} ${word}${value === 1 ? '' : 's'}`;
}
