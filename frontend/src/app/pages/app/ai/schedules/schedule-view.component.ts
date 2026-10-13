import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from '@angular/core';

import { Choice } from 'src/app/components/ui/radio-keys';
import { ActivitySchedule, ScheduleRun, SchedulableFunction } from 'src/app/services/activity.service';
import { cadenceWords, momentWords, until } from './cadence-words';
import {
  STATE_WORDS, ScheduleState, alsoOf, failuresOf, runLabel, stateOf, took, whatOf,
} from './schedule-shared';

type RunFilter = 'all' | 'failed';

/**
 * One schedule, opened: when it runs and what it does, at reading size;
 * the next few times it will run; and every run it has had, each
 * opening to what it gave back or why it failed.
 *
 * Nothing here changes anything itself: the page owns the doors, and
 * this says which one was asked for.
 */
@Component({
  selector: 'app-schedule-view',
  standalone: false,
  templateUrl: './schedule-view.component.html',
  styleUrl: './schedule-view.component.css',
})
export class ScheduleViewComponent implements OnChanges {
  @Input({ required: true }) schedule!: ActivitySchedule;
  @Input() functions: SchedulableFunction[] = [];
  /** The clock the page ticks, in seconds: "in 14 h" follows it. */
  @Input() now = Date.now() / 1000;
  /** Everything is stopped: nothing below is about to happen. */
  @Input() stopped = false;
  @Input() canUpdate = false;
  @Input() canDelete = false;
  @Input() busy = false;

  @Output() closed = new EventEmitter<void>();
  @Output() edit = new EventEmitter<void>();
  @Output() setEnabled = new EventEmitter<boolean>();
  @Output() remove = new EventEmitter<void>();

  cadence = '';
  what = '';
  also = '';
  state: ScheduleState = 'active';
  failures = '';
  /** Newest first: the last run is the one being asked about. */
  runs: ScheduleRun[] = [];
  upcoming: number[] = [];

  runFilter: RunFilter = 'all';
  runFilters: Choice<RunFilter>[] = [];
  page = 1;
  readonly pageSize = 10;

  ngOnChanges(changes: SimpleChanges): void {
    const schedule = this.schedule;
    this.cadence = cadenceWords(schedule);
    this.what = whatOf(schedule, this.functions);
    this.also = alsoOf(schedule, this.functions);
    this.state = stateOf(schedule);
    this.failures = failuresOf(schedule);
    this.runs = [...(schedule.runs || [])].reverse();
    this.upcoming = schedule.enabled ? (schedule.upcoming || [schedule.next_run_at]).filter(Boolean).slice(0, 3) : [];
    const failed = this.runs.filter((run) => run.status === 'error').length;
    this.runFilters = [
      { value: 'all', name: `All ${this.runs.length}` },
      { value: 'failed', name: `Failed ${failed}` },
    ];
    // Another schedule starts at its first page, with every run shown.
    const before = changes['schedule']?.previousValue as ActivitySchedule | undefined;
    if (before?.schedule_id !== schedule.schedule_id) {
      this.runFilter = 'all';
      this.page = 1;
    }
  }

  get stateWord(): string { return STATE_WORDS[this.state]; }

  get shownRuns(): ScheduleRun[] {
    return this.runFilter === 'failed' ? this.runs.filter((run) => run.status === 'error') : this.runs;
  }

  get pagedRuns(): ScheduleRun[] {
    return this.shownRuns.slice((this.page - 1) * this.pageSize, this.page * this.pageSize);
  }

  showRuns(filter: RunFilter): void {
    this.runFilter = filter;
    this.page = 1;
  }

  moment(seconds: number | null | undefined): string {
    return momentWords(seconds || 0, this.schedule.timezone);
  }

  away(seconds: number): string { return until(seconds, this.now); }
  label(run: ScheduleRun): string { return runLabel(run); }
  took(run: ScheduleRun): string { return took(run); }
  detail(run: ScheduleRun): string { return run.error || run.result || ''; }
}
