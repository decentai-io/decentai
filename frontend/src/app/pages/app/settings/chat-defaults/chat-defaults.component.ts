import { Component, OnInit } from '@angular/core';

import { ModelChoice } from 'src/app/components/model-select/model-select.component';
import { Draft } from 'src/app/components/ui/draft';
import { Choice } from 'src/app/components/ui/radio-keys';
import { HasUnsavedChanges } from 'src/app/guards/unsaved-changes.guard';
import { AuthService } from 'src/app/services/auth.service';
import { ProfileService } from 'src/app/services/profile.service';
import { ReadAloudService } from 'src/app/services/read-aloud.service';
import { LlmConnection, SettingsLlmService } from 'src/app/services/settings-llm.service';
import { SKILLS_LISTED_OPTIONS, STANDARD_SKILLS_LISTED, TRUST_CHOICES } from '../../ai/chat/chat-defaults';

/** What a new chat of this person starts with. */
interface ChatDefaults {
  /** The provider, or '' for the organization's default. */
  llm_secret_ref: string;
  llm_model: string;
  trust_level: number;
  max_turns: number;
  max_skills: number;
}

const STANDARD: ChatDefaults = {
  llm_secret_ref: '', llm_model: '', trust_level: 1, max_turns: 20, max_skills: STANDARD_SKILLS_LISTED,
};

/** How long "Saved" shows beside a switch that saves itself. */
const NOTE_FOR_MS = 2000;

/**
 * Settings › Chat defaults: the model, the trust level, the turn budget
 * and the number of skills listed that a new chat of this person starts
 * with. Any chat can still change each from its own menu. One form,
 * saved with the save bar; reading replies aloud is this device's alone
 * and saves itself.
 */
@Component({
  selector: 'app-chat-defaults',
  standalone: false,
  templateUrl: './chat-defaults.component.html',
})
export class ChatDefaultsComponent implements OnInit, HasUnsavedChanges {
  loading = true;
  /** Why the page could not be read. */
  loadError = '';
  saving = false;
  saveError = '';

  readonly draft = new Draft<ChatDefaults>(STANDARD);
  /** The providers this person can use: what the model is chosen among. */
  connections: LlmConnection[] = [];
  /** "Saved", for a moment, beside the read-aloud switch. */
  readAloudNote = '';

  readonly trustChoices: Choice<number>[] = TRUST_CHOICES;
  readonly turnChoices: Choice<number>[] = [
    { value: 20, name: 'Standard', hint: '20 steps' },
    { value: 40, name: 'Long', hint: '40 steps' },
    { value: 60, name: 'Very long', hint: '60 steps' },
    { value: 100, name: 'Longest', hint: '100 steps' },
    { value: 0, name: 'Unlimited', hint: 'Runs until done, or until you stop it.' },
  ];
  readonly skillsChoices: Choice<number>[] = SKILLS_LISTED_OPTIONS
    .map((option) => ({ value: option.rows, name: option.label, hint: option.hint }));

  constructor(
    private profiles: ProfileService,
    private llm: SettingsLlmService,
    public auth: AuthService,
    public readAloud: ReadAloudService,
  ) {}

  /** The model is chosen among providers, which not everyone may list. */
  get canChooseModel(): boolean {
    return this.auth.can('settings:llm:list');
  }

  ngOnInit(): Promise<void> {
    return this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      const [profile, connections] = await Promise.all([
        this.profiles.get(),
        this.canChooseModel ? this.llm.list() : Promise.resolve([]),
      ]);
      // No profile is no answer: the gateway reports a failure as one.
      if (!profile) throw new Error('no profile');
      this.connections = connections;
      this.draft.take(this.read(profile?.preferences?.chat ?? {}));
      void this.readAloud.refresh();
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  /** The saved preferences as the form's values; what was never chosen
   *  is the platform's standard. */
  private read(chat: Record<string, unknown>): ChatDefaults {
    const whole = (value: unknown, otherwise: number) =>
      Number.isInteger(value) ? (value as number) : otherwise;
    const provider = typeof chat['llm_secret_ref'] === 'string' ? chat['llm_secret_ref'] : '';
    return {
      llm_secret_ref: provider,
      llm_model: provider && typeof chat['llm_model'] === 'string' ? chat['llm_model'] : '',
      trust_level: whole(chat['trust_level'], STANDARD.trust_level),
      max_turns: whole(chat['max_turns'], STANDARD.max_turns),
      max_skills: whole(chat['max_skills'], STANDARD.max_skills),
    };
  }

  /** The model a new chat starts with: a provider and one of its
   *  models, or neither for the organization's default. */
  chooseModel(choice: ModelChoice): void {
    this.draft.value.llm_secret_ref = choice.connectionId;
    this.draft.value.llm_model = choice.model;
  }

  async save(): Promise<void> {
    if (this.saving) return;
    this.saving = true;
    this.saveError = '';
    try {
      const chosen = this.draft.value;
      const result = await this.profiles.saveChatDefaults({
        llm_secret_ref: chosen.llm_secret_ref || null,
        llm_model: chosen.llm_secret_ref ? chosen.llm_model : '',
        trust_level: chosen.trust_level,
        max_turns: chosen.max_turns,
        max_skills: chosen.max_skills,
      });
      if (result.error) {
        this.saveError = result.error;
        return;
      }
      this.draft.take(this.read(result.profile?.preferences?.chat ?? { ...chosen }));
    } finally {
      this.saving = false;
    }
  }

  /** Kept on this device, with nothing to save: said so for a moment. */
  setReadAloud(on: boolean): void {
    this.readAloud.automatic = on;
    this.readAloudNote = 'Saved';
    setTimeout(() => (this.readAloudNote = ''), NOTE_FOR_MS);
  }

  unsavedChangesTo(): string {
    return this.draft.dirty ? 'Chat defaults' : '';
  }
}
