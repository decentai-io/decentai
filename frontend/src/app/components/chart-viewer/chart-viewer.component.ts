import { Component, Input, OnChanges, OnDestroy } from '@angular/core';
import { ChartType } from 'chart.js';
import { Subscription } from 'rxjs';

import { ThemeService } from 'src/app/services/theme.service';

export interface ChartInput {
  labels: string[];
  datasets: Array<{
    label?: string;
    data: number[];
  }>;
}

/* DecentAI three-color system: emerald focal series + graded neutral tints
   (tints of black only — no other hues). Highlight the key series in
   emerald; the rest stay muted so one insight leads each view.

   Read from the theme rather than written down here: the neutral tints
   invert between light and dark, so a fixed '#111111' second series is a
   near-invisible bar on a dark canvas. */
const SERIES_TOKENS = [
  '--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5',
];

const FALLBACK_PALETTE = ['#10b981', '#6b6b6b', '#9a9a9a', '#cfcfcf', '#3f3f3f'];

@Component({
  selector: 'app-chart-viewer',
  standalone: false,
  templateUrl: './chart-viewer.component.html',
  styleUrls: ['./chart-viewer.component.css'],
})
export class ChartViewerComponent implements OnChanges, OnDestroy {
  @Input() data: ChartInput | null = null;
  @Input() title: string = '';
  @Input() chartType: string = 'bar';

  activeType: ChartType = 'bar';
  chartData: any = { labels: [], datasets: [] };
  chartOptions: any = {};

  private themeSub?: Subscription;

  readonly types: Array<{ value: ChartType; icon: string; label: string }> = [
    { value: 'bar',  icon: 'chart-column', label: 'Bar'  },
    { value: 'line', icon: 'chart-line',   label: 'Line' },
    { value: 'pie',  icon: 'chart-pie',    label: 'Pie'  },
  ];

  constructor(private theme: ThemeService) {
    // Canvas cannot inherit a CSS variable: the colours are copied into
    // the chart at build time, so flipping the theme has to rebuild it.
    this.themeSub = this.theme.theme$.subscribe(() => this.buildChart());
  }

  ngOnDestroy(): void {
    this.themeSub?.unsubscribe();
  }

  /** A theme colour, resolved now. */
  private token(name: string, fallback: string): string {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  }

  private palette(): string[] {
    return SERIES_TOKENS.map(
      (name, i) => this.token(name, FALLBACK_PALETTE[i]),
    );
  }

  ngOnChanges(): void {
    this.activeType = (this.chartType as ChartType) || 'bar';
    this.buildChart();
  }

  setType(type: ChartType): void {
    this.activeType = type;
    this.buildChart();
  }

  private buildChart(): void {
    if (!this.data) return;
    const isPie = this.activeType === 'pie';
    const isLine = this.activeType === 'line';
    const palette = this.palette();

    this.chartData = {
      labels: this.data.labels,
      datasets: this.data.datasets.map((ds, i) => ({
        label: ds.label,
        data: ds.data,
        backgroundColor: isPie
          ? this.data!.labels.map((_, j) => this.rgba(palette[j % palette.length], 0.82))
          : this.rgba(palette[i % palette.length], isLine ? 0.12 : 0.82),
        borderColor: isPie
          ? this.data!.labels.map((_, j) => palette[j % palette.length])
          : palette[i % palette.length],
        borderWidth: 2,
        borderRadius: this.activeType === 'bar' ? 7 : 0,
        tension: isLine ? 0.4 : undefined,
        fill: isLine,
        pointRadius: isLine ? 4 : 0,
        pointHoverRadius: isLine ? 7 : 0,
        pointBackgroundColor: palette[i % palette.length],
        hoverOffset: isPie ? 8 : undefined,
      })),
    };

    const isCartesian = !isPie;

    // Axis text, gridlines and the tooltip follow the theme too — a fixed
    // '#444' label and a black 4%-opacity grid vanish on a dark canvas.
    const family = this.token('--chat-font', 'system-ui');
    const ink = this.token('--foreground', '#0a0a0a');
    const muted = this.token('--muted-foreground', '#6b6b6b');
    // Composed here rather than with color-mix(): this string is handed to
    // a canvas context, which does not parse CSS colour functions.
    const grid = this.rgba(muted, 0.22);
    const font = { family, size: 12 };

    this.chartOptions = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 450, easing: 'easeInOutQuart' },
      plugins: {
        legend: {
          position: 'bottom' as const,
          labels: {
            font,
            padding: 18,
            usePointStyle: true,
            pointStyleWidth: 10,
            color: ink,
          },
        },
        tooltip: {
          backgroundColor: this.token('--popover', '#111111'),
          titleColor: this.token('--popover-foreground', '#ffffff'),
          bodyColor: this.token('--popover-foreground', '#ffffff'),
          borderColor: this.token('--border', '#222222'),
          borderWidth: 1,
          titleFont: { family, size: 13, weight: 'bold' as const },
          bodyFont: font,
          padding: 12,
          cornerRadius: 8,
          displayColors: true,
          boxPadding: 5,
        },
      },
      scales: isCartesian ? {
        x: {
          grid: { color: grid, drawTicks: false },
          border: { display: false },
          ticks: { font, color: muted, padding: 8 },
        },
        y: {
          grid: { color: grid, drawTicks: false },
          border: { display: false },
          ticks: { font, color: muted, padding: 8 },
          beginAtZero: true,
        },
      } : {},
    };
  }

  private rgba(color: string, a: number): string {
    // Theme tokens are hex today; anything else is handed through opaque
    // rather than parsed into NaN and painted as nothing.
    if (!/^#[0-9a-f]{6}$/i.test(color)) return color;
    const r = parseInt(color.slice(1, 3), 16);
    const g = parseInt(color.slice(3, 5), 16);
    const b = parseInt(color.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${a})`;
  }
}
