import { Component, OnDestroy, OnInit } from '@angular/core';

import { ModelChoice } from 'src/app/components/model-select/model-select.component';
import { Draft } from 'src/app/components/ui/draft';
import { Choice } from 'src/app/components/ui/radio-keys';
import { HasUnsavedChanges } from 'src/app/guards/unsaved-changes.guard';
import { AuthService } from 'src/app/services/auth.service';
import { ReadAloudService } from 'src/app/services/read-aloud.service';
import { LlmConnection, SettingsLlmService } from 'src/app/services/settings-llm.service';
import {
  LocalSpeech, SettingsSpeechService, SpeechSettings, SpeechSource,
} from 'src/app/services/settings-speech.service';

/** What an organization that has chosen nothing has: its messages
 *  written down by the platform's own model, its replies not said. */
const STANDARD: SpeechSettings = {
  transcription_source: 'local', transcription_connection_id: '', transcription_model: '',
  speech_source: 'off', speech_connection_id: '', speech_model: '', speech_voice: '',
};

/** How often the page asks again while a model downloads. */
const WATCH_EVERY_MS = 3000;

/**
 * Settings › Speech: for the whole organization, what writes a spoken
 * message down and what says a reply aloud. Each is the platform's own
 * model on this machine, a provider's, or off. One form, saved with
 * the save bar.
 */
@Component({
  selector: 'app-speech',
  standalone: false,
  templateUrl: './speech.component.html',
})
export class SpeechComponent implements OnInit, OnDestroy, HasUnsavedChanges {
  loading = true;
  loadError = '';
  saving = false;
  saveError = '';

  readonly draft = new Draft<SpeechSettings>(STANDARD);
  /** Where the platform's own models stand on this machine. */
  local: LocalSpeech = { reachable: false };
  /** The providers shared with the whole organization: everyone's chat
   *  uses what is chosen here, so only those may be. */
  shared: LlmConnection[] = [];
  providerChoices: Choice[] = [];

  readonly sourceChoices: Choice<SpeechSource>[] = [
    { value: 'local', name: 'This machine' },
    { value: 'connection', name: 'A provider' },
    { value: 'off', name: 'Off' },
  ];

  private watch: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private service: SettingsSpeechService,
    private llm: SettingsLlmService,
    private auth: AuthService,
    private readAloud: ReadAloudService,
  ) {}

  get canEdit(): boolean {
    return this.auth.can('settings:speech:update');
  }

  ngOnInit(): Promise<void> {
    return this.load();
  }

  ngOnDestroy(): void {
    this.stopWatching();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      const [answer, connections] = await Promise.all([
        this.service.get(),
        this.auth.can('settings:llm:list') ? this.llm.list() : Promise.resolve([]),
      ]);
      if (!answer?.speech) throw new Error('no settings');
      this.shared = connections.filter((connection) =>
        (connection.owner?.groups || []).includes('everyone'));
      this.providerChoices = this.shared.map((connection) =>
        ({ value: connection.resource_ref, name: connection.name }));
      this.take(answer);
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  private take(answer: { speech?: SpeechSettings; local?: LocalSpeech }): void {
    if (answer.speech) this.draft.take({ ...STANDARD, ...answer.speech });
    if (answer.local) this.local = answer.local;
    this.watchLocal();
  }

  chooseTranscription(choice: ModelChoice): void {
    this.draft.value.transcription_connection_id = choice.connectionId;
    this.draft.value.transcription_model = choice.model;
  }

  // ── What is missing, under the field it belongs to ──────────────────

  /** A provider alone does not say which of its models writes speech down. */
  get transcriptionError(): string {
    const speech = this.draft.value;
    return speech.transcription_source === 'connection'
      && !(speech.transcription_connection_id && speech.transcription_model)
      ? 'Choose the transcription model.' : '';
  }

  get providerError(): string {
    const speech = this.draft.value;
    return speech.speech_source === 'connection' && !speech.speech_connection_id
      ? 'Choose the provider that speaks.' : '';
  }

  get modelError(): string {
    const speech = this.draft.value;
    return speech.speech_source === 'connection' && !speech.speech_model.trim()
      ? 'Name the speech model.' : '';
  }

  get voiceError(): string {
    const speech = this.draft.value;
    return speech.speech_source === 'connection' && !speech.speech_voice.trim()
      ? 'Name the voice.' : '';
  }

  /** The first thing in the way of saving, or ''. */
  get blocker(): string {
    return this.transcriptionError || this.providerError || this.modelError || this.voiceError;
  }

  // ── The platform's own models ───────────────────────────────────────

  /** One of the platform's own models, in a line: there, on its way and
   *  how far, or what is in the way. */
  localStanding(what: 'transcription' | 'speech'): string {
    if (!this.local.reachable) return 'Not running on this machine.';
    const model = this.local[what];
    if (!model) return '';
    const megabytes = `${Math.round(model.of / (1024 * 1024))} MB`;
    if (model.state === 'ready') return `Ready · ${megabytes} on this machine.`;
    if (model.state === 'fetching') {
      const percent = model.of ? Math.floor(model.bytes * 100 / model.of) : 0;
      return `Downloading ${percent}% of ${megabytes}.`;
    }
    if (model.state === 'failed') {
      return `The download did not finish (${model.error || 'no reason given'}). It is tried again when next used.`;
    }
    return `${megabytes}, downloaded once when this is saved.`;
  }

  /** While one of the platform's own models is on its way, the page
   *  asks again every few seconds, so the figure moves. */
  private watchLocal(): void {
    this.stopWatching();
    const fetching = [this.local.transcription, this.local.speech]
      .some((model) => model?.state === 'fetching');
    if (!fetching) return;
    this.watch = setTimeout(async () => {
      try {
        this.local = (await this.service.get()).local;
      } catch {
        return;
      }
      this.watchLocal();
    }, WATCH_EVERY_MS);
  }

  private stopWatching(): void {
    if (this.watch) clearTimeout(this.watch);
    this.watch = null;
  }

  // ── Saving ──────────────────────────────────────────────────────────

  async save(): Promise<void> {
    if (this.saving) return;
    if (this.blocker) {
      this.saveError = this.blocker;
      return;
    }
    this.saving = true;
    this.saveError = '';
    try {
      const result = await this.service.update(this.draft.value);
      if (result.error) {
        this.saveError = result.error;
        return;
      }
      this.take(result);
      // Whether replies can be said has just been decided.
      void this.readAloud.refresh();
    } finally {
      this.saving = false;
    }
  }

  discard(): void {
    this.draft.discard();
    this.saveError = '';
  }

  unsavedChangesTo(): string {
    return this.draft.dirty ? 'Speech' : '';
  }
}
