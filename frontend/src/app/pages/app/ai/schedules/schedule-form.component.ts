import { Component, Inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

import { Choice } from 'src/app/components/ui/radio-keys';
import {
  ActivitySchedule, ActivityService, ScheduleChanges, SchedulableFunction,
} from 'src/app/services/activity.service';
import { AiSessionService } from 'src/app/services/ai-session.service';
import {
  CadenceDraft, CadenceKind, PeriodUnit, WEEK, blankCadence, cadenceOf, cadenceProblem, cronWords,
  momentWords, upcomingOf, whenOf,
} from './cadence-words';
import { functionLabel } from './schedule-shared';

/** A chat a schedule can live in. */
export interface ScheduleChat {
  chat_id: string;
  title: string;
}

/** What the page hands the dialog. */
export interface ScheduleFormData {
  chats: ScheduleChat[];
  functions: SchedulableFunction[];
  /** The schedule to change; absent to make one. */
  schedule?: ActivitySchedule;
  /** The chat a new one starts in, when the list is narrowed to one. */
  chatId?: string;
}

/** What it closes with when something was saved. */
export interface ScheduleFormResult {
  notice: string;
  id: string;
}

/** One declared input of a function, as a box in the form. */
interface InputBox {
  key: string;
  label: string;
  help: string;
  kind: 'text' | 'number' | 'switch' | 'select' | 'json';
  choices: Choice[];
}

/** The chat every schedule made here goes to unless another is chosen:
 *  made the first time, found by its name after that. */
const HOME_CHAT = 'Schedules';
/** Where this device keeps which chat that is. The assistant gives a
 *  chat a name of its own once it has spoken in it, so the name alone
 *  would lose the chat after its first run. */
const HOME_CHAT_KEY = 'decentai.schedules-chat';

function rememberedHome(): string {
  try {
    return localStorage.getItem(HOME_CHAT_KEY) || '';
  } catch {
    return '';
  }
}
const NOTE_LIMIT = 300;

/**
 * A schedule, made or changed: which chat it lives in, what it does (a
 * note that wakes the assistant, or a function run unattended, with
 * its inputs as a form made from what the function declares), and when
 * — every so often, on days at a time, once, or a cron — with the next
 * runs worked out as the person types, in the zone they choose.
 *
 * A change sends only what changed: the same "when" is not sent again,
 * so its next run is not counted afresh.
 */
@Component({
  selector: 'app-schedule-form',
  standalone: false,
  templateUrl: './schedule-form.component.html',
  styleUrl: './schedule-form.component.css',
})
export class ScheduleFormComponent {
  readonly editing: ActivitySchedule | null;
  readonly week = WEEK;
  readonly noteLimit = NOTE_LIMIT;

  // ── Which chat ──────────────────────────────────────────────────────
  /** '' is the chat called "Schedules", made when it is first needed. */
  chatId = '';
  chatQuery = '';
  private readonly allChats: Choice[];
  private shownChats: Choice[] = [];
  private shownFor = '';

  // ── What ────────────────────────────────────────────────────────────
  kind: 'note' | 'function' = 'note';
  readonly kinds: Choice<'note' | 'function'>[];
  note = '';
  fn = '';
  readonly functionChoices: Choice[];
  boxes: InputBox[] = [];
  values: Record<string, any> = {};
  /** The inputs written as JSON instead of box by box. */
  asJson = false;
  inputsText = '{}';
  wakeField = '';
  wakeChoices: Choice[] = [];
  /** What it does, as the schedule held it when the form opened. */
  private whatAtOpen = '';

  // ── When ────────────────────────────────────────────────────────────
  cadence: CadenceDraft;
  readonly cadenceKinds: Choice<CadenceKind>[] = [
    { value: 'every', name: 'Every' },
    { value: 'days', name: 'On days' },
    { value: 'once', name: 'Once' },
    { value: 'custom', name: 'Custom' },
  ];
  readonly units: Choice<PeriodUnit>[] = [
    { value: 'minutes', name: 'minutes' },
    { value: 'hours', name: 'hours' },
    { value: 'days', name: 'days' },
  ];
  zone: string;
  readonly zones: string[];
  private readonly whenAtOpen: string;

  /** Save was pressed: what is missing is said under its field. */
  tried = false;
  saving = false;
  problem = '';

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: ScheduleFormData,
    private dialogRef: MatDialogRef<ScheduleFormComponent, ScheduleFormResult | null>,
    private service: ActivityService,
    private session: AiSessionService,
  ) {
    const schedule = data.schedule || null;
    this.editing = schedule;
    this.zones = ScheduleFormComponent.knownZones();
    this.zone = schedule?.timezone || AiSessionService.browserTimezone() || 'UTC';
    this.cadence = schedule ? cadenceOf(schedule) : blankCadence();
    this.whenAtOpen = JSON.stringify(whenOf(this.cadence));
    // A one-off starts at tomorrow, the same time: a date to change,
    // not an empty box.
    if (!this.cadence.date) this.cadence.date = cadenceOf({
      cron: '', every_seconds: null, timezone: this.zone, next_run_at: Date.now() / 1000 + 86400,
    }).date;

    // The home chat first: by its name once it exists, as an offer to
    // make it until then.
    const remembered = rememberedHome();
    const home = data.chats.find((chat) => chat.chat_id === remembered)
      || data.chats.find((chat) => chat.title === HOME_CHAT);
    this.allChats = [
      home ? { value: home.chat_id, name: home.title, hint: 'Where schedules made here go' }
        : { value: '', name: `A new chat called “${HOME_CHAT}”` },
      ...data.chats.filter((chat) => chat !== home).map((chat) => ({ value: chat.chat_id, name: chat.title })),
    ];
    this.chatId = schedule?.chat_id || data.chatId || home?.chat_id || '';

    const none = !data.functions.length;
    this.kinds = [
      { value: 'note', name: 'Wake the assistant with a note', hint: 'It reads the note in the chat and does what it says.' },
      {
        value: 'function', name: 'Run a function unattended', disabled: none,
        hint: none ? 'No installed agent offers a function that can run on its own.'
          : 'An agent’s function runs by itself, and can wake the assistant with what it finds.',
      },
    ];
    this.functionChoices = data.functions.map((fn) => ({ value: fn.value, name: fn.label }));
    // A function its agent no longer offers is still what the schedule
    // runs: it is named, and stays until another is chosen.
    if (schedule?.function && !data.functions.some((fn) => fn.value === schedule.function)) {
      this.functionChoices.push({
        value: schedule.function, name: functionLabel(schedule.function, []), hint: 'No longer offered by its agent',
      });
    }

    if (schedule) {
      this.note = schedule.note || '';
      if (schedule.function) {
        this.kind = 'function';
        this.fn = schedule.function;
        this.readFunction(schedule.inputs || {}, schedule.wake_field || '');
      }
      this.whatAtOpen = JSON.stringify(this.what);
    }
  }

  /** The zones a browser knows, with UTC, which some leave out. */
  private static knownZones(): string[] {
    const known: string[] = (Intl as any).supportedValuesOf?.('timeZone') || [];
    return known.includes('UTC') ? known : ['UTC', ...known];
  }

  // ── Which chat ──────────────────────────────────────────────────────

  /** A long list of chats gets a search above it. */
  get manyChats(): boolean { return this.allChats.length > 12; }

  /** The chats the search leaves, the chosen one always among them. */
  get chatChoices(): Choice[] {
    const key = `${this.chatQuery}|${this.chatId}`;
    if (key !== this.shownFor || !this.shownChats.length) {
      const query = this.chatQuery.trim().toLowerCase();
      this.shownFor = key;
      this.shownChats = this.allChats.filter((chat) =>
        !query || chat.value === this.chatId || chat.name.toLowerCase().includes(query));
    }
    return this.shownChats;
  }

  // ── What ────────────────────────────────────────────────────────────

  get chosenFunction(): SchedulableFunction | null {
    return this.data.functions.find((fn) => fn.value === this.fn) || null;
  }

  onFunctionChosen(): void {
    this.readFunction({}, '');
  }

  /** The chosen function's inputs as boxes, holding what a schedule
   *  already gives them. */
  private readFunction(held: Record<string, unknown>, wakeField: string): void {
    const fn = this.chosenFunction;
    const declared = (fn?.inputs || {}) as Record<string, any>;
    this.boxes = Object.entries(declared).map(([key, shape]) => {
      const type = String(shape?.type || 'string');
      const options: unknown[] = Array.isArray(shape?.enum) ? shape.enum : [];
      return {
        key,
        label: String(shape?.title || key),
        help: String(shape?.description || ''),
        kind: options.length ? 'select' : type === 'boolean' ? 'switch'
          : type === 'number' || type === 'integer' ? 'number'
          : type === 'string' ? 'text' : 'json',
        choices: options.map((option) => ({ value: String(option), name: String(option) })),
      } as InputBox;
    });
    this.values = {};
    for (const box of this.boxes) {
      const value = held[box.key] ?? declared[box.key]?.default;
      this.values[box.key] = box.kind === 'switch' ? !!value
        : value === undefined || value === null ? ''
        : box.kind === 'json' ? JSON.stringify(value) : value;
    }
    // Inputs the function no longer declares are kept, as JSON.
    const strays = Object.keys(held).some((key) => !(key in declared));
    this.asJson = strays || (!this.boxes.length && Object.keys(held).length > 0);
    this.inputsText = JSON.stringify(strays || this.asJson ? held : this.inputsFromBoxes().inputs, null, 2);
    this.wakeChoices = [
      { value: '', name: 'Never: it runs quietly' },
      ...(fn?.outputs || []).map((field) => ({ value: field, name: `When ${field} comes back` })),
    ];
    this.wakeField = wakeField && this.wakeChoices.some((choice) => choice.value === wakeField)
      ? wakeField : !this.editing ? fn?.outputs[0] || '' : '';
  }

  /** The boxes as the function takes them; the first box that does not
   *  read as what it should be is named. */
  private inputsFromBoxes(): { inputs: Record<string, unknown>; problem: string } {
    const inputs: Record<string, unknown> = {};
    for (const box of this.boxes) {
      const value = this.values[box.key];
      if (box.kind === 'switch') { inputs[box.key] = !!value; continue; }
      if (value === '' || value === null || value === undefined) continue;
      if (box.kind === 'number') {
        if (Number.isNaN(Number(value))) return { inputs, problem: `${box.label} must be a number.` };
        inputs[box.key] = Number(value);
      } else if (box.kind === 'json') {
        try { inputs[box.key] = JSON.parse(value); }
        catch { return { inputs, problem: `${box.label} must be valid JSON.` }; }
      } else {
        inputs[box.key] = value;
      }
    }
    return { inputs, problem: '' };
  }

  /** Box by box, or as JSON: each way starts from what the other holds. */
  setAsJson(on: boolean): void {
    if (on) {
      this.inputsText = JSON.stringify(this.inputsFromBoxes().inputs, null, 2);
    } else {
      const held = this.inputsFromText();
      if (held) for (const box of this.boxes) {
        const value = held[box.key];
        this.values[box.key] = box.kind === 'switch' ? !!value
          : value === undefined ? '' : box.kind === 'json' ? JSON.stringify(value) : value;
      }
    }
    this.asJson = on;
  }

  private inputsFromText(): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(this.inputsText || '{}');
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }

  /** What it does, as the backend takes it. */
  private get what(): ScheduleChanges {
    if (this.kind === 'note') return { note: this.note.trim(), function: '' };
    return {
      function: this.fn,
      note: this.note.trim(),
      inputs: this.asJson ? this.inputsFromText() || {} : this.inputsFromBoxes().inputs,
      wake_field: this.wakeField,
    };
  }

  /** Left as it was, what it does is not sent again, so a schedule
   *  whose function is no longer offered can still be moved in time. */
  private get whatChanged(): boolean {
    return !this.editing || JSON.stringify(this.what) !== this.whatAtOpen;
  }

  get inputsProblem(): string {
    if (this.kind !== 'function' || !this.fn) return '';
    if (this.asJson) return this.inputsFromText() ? '' : 'The inputs must be a JSON object: { "name": value }.';
    return this.inputsFromBoxes().problem;
  }

  get whatProblem(): string {
    if (this.kind === 'note') return this.note.trim() ? '' : 'Write the note the assistant wakes with.';
    return this.fn ? '' : 'Choose the function to run.';
  }

  // ── When ────────────────────────────────────────────────────────────

  toggleDay(day: number): void {
    const days = this.cadence.days;
    this.cadence.days = days.includes(day) ? days.filter((each) => each !== day) : [...days, day];
  }

  get zoneProblem(): string {
    return this.zones.includes(this.zone.trim()) ? '' : 'Choose a time zone from the list, such as Europe/London.';
  }

  get whenProblem(): string {
    // A one-off left as it was may be in the past: it is not being set.
    if (this.editing && !this.whenChanged) return '';
    return this.zoneProblem ? '' : cadenceProblem(this.cadence, this.zone.trim());
  }

  private get whenChanged(): boolean {
    return JSON.stringify(whenOf(this.cadence)) !== this.whenAtOpen;
  }

  /** The five parts of the cron being typed, each under its name. */
  get cronParts(): { value: string; name: string }[] {
    const parts = this.cadence.cron.trim().split(/\s+/).filter(Boolean);
    return ['minute', 'hour', 'day of month', 'month', 'day of week']
      .map((name, index) => ({ name, value: parts[index] || '·' }));
  }

  /** What the cadence comes to: the next runs, or the reason there are
   *  none to show. */
  get preview(): string {
    if (this.zoneProblem) return '';
    const zone = this.zone.trim();
    if (this.cadence.kind === 'custom') {
      if (cadenceProblem(this.cadence, zone)) return '';
      const words = cronWords(this.cadence.cron);
      return words.startsWith('Custom') ? 'Its next runs are counted when it is saved.'
        : `${words}. Its next runs are counted when it is saved.`;
    }
    if (cadenceProblem(this.cadence, zone)) return '';
    const next = upcomingOf(this.cadence, zone).map((seconds) => momentWords(seconds, zone));
    return next.length ? `Next: ${next.join(' · ')}` : '';
  }

  // ── Saving ──────────────────────────────────────────────────────────

  close(): void {
    if (!this.saving) this.dialogRef.close(null);
  }

  async save(): Promise<void> {
    this.tried = true;
    this.problem = '';
    if (this.whatProblem || this.inputsProblem || this.zoneProblem || this.whenProblem) return;

    const zone = this.zone.trim();
    const what = this.what;

    this.saving = true;
    try {
      if (this.editing) {
        const changes: ScheduleChanges = this.whatChanged ? { ...what } : {};
        if (this.whenChanged) Object.assign(changes, whenOf(this.cadence));
        if (zone !== (this.editing.timezone || '')) changes.timezone = zone;
        if (!Object.keys(changes).length) return this.dialogRef.close(null);
        const result = await this.service.update(this.editing.chat_id, this.editing.schedule_id, changes);
        if (result.error) { this.problem = result.error; return; }
        this.dialogRef.close({ notice: 'Saved.', id: this.editing.schedule_id });
        return;
      }

      // Everything the form can check is checked before a chat is made
      // for the schedule: a refused form must not leave a chat behind.
      let chatId = this.chatId;
      if (!chatId) {
        const created = await this.session.createChat(HOME_CHAT, {});
        chatId = created.data?.chat?.chat_id || '';
        if (!chatId) { this.problem = created.error || 'Could not make a chat for it.'; return; }
        // The chat exists now: a second try goes into it.
        this.allChats[0] = { value: chatId, name: HOME_CHAT };
        try {
          localStorage.setItem(HOME_CHAT_KEY, chatId);
        } catch {
          // Without storage it is found by its name, while it keeps it.
        }
        this.shownChats = [];
        this.chatId = chatId;
      }
      const result = await this.service.create({
        chat_id: chatId, ...what, ...whenOf(this.cadence), timezone: zone,
      });
      if (result.error) { this.problem = result.error; return; }
      this.dialogRef.close({ notice: 'On the clock.', id: result.data?.schedule?.schedule_id || '' });
    } finally {
      this.saving = false;
    }
  }
}
