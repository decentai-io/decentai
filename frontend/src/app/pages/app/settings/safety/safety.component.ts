import { Component, OnInit } from '@angular/core';

import { Draft } from 'src/app/components/ui/draft';
import { Choice } from 'src/app/components/ui/radio-keys';
import { HasUnsavedChanges } from 'src/app/guards/unsaved-changes.guard';
import { AuthService } from 'src/app/services/auth.service';
import { GuardrailSettings, SettingsGuardrailService } from 'src/app/services/settings-guardrail.service';
import { SafetySettings, SettingsSafetyService } from 'src/app/services/settings-safety.service';

/** Where the platform stood before this page existed: every script and
 *  program asked about, any package a card names, MCP servers and
 *  webhooks allowed, notifications saying what happened. */
const STANDARD: SafetySettings = {
  mcp: 'allowed', webhooks: 'allowed', notification_text: 'shown', blocked_sites: [],
  scripts: 'always', programs: 'always', packages: 'any', allowed_packages: [],
};

/**
 * Settings › Safety: what no agent may do, and what is always shown to
 * a person before it runs.
 *
 * Two agents do things nobody can list beforehand — the Browser opens
 * any site and runs scripts in its pages, the Code agent writes
 * programs — and each asks before every one, on a code card. How often
 * to be asked is set here, with the sites no agent may open at all and
 * the packages a program may install.
 *
 * A looser choice about asking applies only to code the assistant read
 * and found to do what it says, and the chat is still told what ran.
 *
 * Below them, the organization's own rules for the assistant
 * (Settings:Guardrail): a grant of their own to read and to change,
 * so a person may see this page for them alone, or for the rest
 * without them. One form, saved with the save bar: each part that
 * changed is saved by its own door.
 */
@Component({
  selector: 'app-safety',
  standalone: false,
  templateUrl: './safety.component.html',
  styleUrl: './safety.component.css',
})
export class SafetyComponent implements OnInit, HasUnsavedChanges {
  loading = true;
  loadError = '';
  saving = false;
  saveError = '';

  readonly draft = new Draft<SafetySettings>(STANDARD);
  /** The organization's own rules, as the form edits them. */
  readonly rules = new Draft<{ text: string }>({ text: '' });
  /** Who last wrote them and when, and how long they may be. */
  rulesSaved: Pick<GuardrailSettings, 'updated_by' | 'updated_at' | 'limit'> = {
    updated_by: '', updated_at: null, limit: 4000,
  };

  readonly scripts: Choice<SafetySettings['scripts']>[] = [
    { value: 'always', name: 'Ask every time',
      hint: 'Every script an agent wants to run in a page is shown to you first.' },
    { value: 'once_per_site', name: 'Ask once for a site in a chat',
      hint: 'After you allow one on a site, later scripts on that site in the same chat run without a card.' },
  ];

  readonly programs: Choice<SafetySettings['programs']>[] = [
    { value: 'always', name: 'Ask every time',
      hint: 'Every program is shown to you first, a corrected one included.' },
    { value: 'corrections', name: 'Do not ask again for a correction',
      hint: 'A corrected program runs without a card unless it needs something you had not allowed.' },
    { value: 'quiet', name: 'Ask only when it reaches a site or uses a credential',
      hint: 'A program that works only on the files it was given runs without a card.' },
  ];

  readonly packages: Choice<SafetySettings['packages']>[] = [
    { value: 'any', name: 'Any package the card names',
      hint: 'You see the packages on the card and decide there.' },
    { value: 'listed', name: 'Only packages on my list',
      hint: 'A program that needs a package off the list is refused before you are asked.' },
  ];

  readonly mcp: Choice<SafetySettings['mcp']>[] = [
    { value: 'allowed', name: 'People may add' },
    { value: 'blocked', name: 'Nobody may' },
  ];

  readonly webhooks: Choice<SafetySettings['webhooks']>[] = [
    { value: 'allowed', name: 'People may receive webhooks' },
    { value: 'blocked', name: 'Nobody may' },
  ];

  readonly notificationText: Choice<SafetySettings['notification_text']>[] = [
    { value: 'shown', name: 'Say what happened' },
    { value: 'hidden', name: 'Only that something did' },
  ];

  constructor(
    private service: SettingsSafetyService,
    public auth: AuthService,
    private guardrail: SettingsGuardrailService,
  ) {}

  /** The Safety rows: who may read them, who may change them. */
  get canSee(): boolean { return this.auth.can('settings:safety:get'); }
  get canEdit(): boolean { return this.canSee && this.auth.can('settings:safety:update'); }
  /** The organization's rules: a grant of their own. */
  get canSeeRules(): boolean { return this.auth.can('settings:guardrail:get'); }
  get canEditRules(): boolean { return this.canSeeRules && this.auth.can('settings:guardrail:update'); }

  /** Whether the save bar has anything to save: a part this person may
   *  change, changed. */
  get dirty(): boolean {
    return (this.canEdit && this.draft.dirty) || (this.canEditRules && this.rules.dirty);
  }

  /** The counter under the rules turns to a warning in the last tenth. */
  get rulesNearLimit(): boolean {
    return this.rules.value.text.length > this.rulesSaved.limit * 0.9;
  }

  /** Whether anything is looser than the platform's own start. */
  get loosened(): boolean {
    const chosen = this.draft.value;
    return chosen.scripts !== 'always' || chosen.programs !== 'always';
  }

  /** What the MCP choice means, in one line. */
  get mcpMeaning(): string {
    return this.draft.value.mcp === 'allowed'
      ? 'Each person adds remote servers for their own chats, and decides which tools are on.'
      : 'Nobody can add one, and no chat can call a tool of one already added; those stay, switched off.';
  }

  /** What the webhooks choice means, in one line. */
  get webhooksMeaning(): string {
    return this.draft.value.webhooks === 'allowed'
      ? 'Each person makes addresses that other services post events to, each heard in a chat of their own.'
      : 'No webhook takes an event: each answers as if it did not exist. Those already made are kept.';
  }

  /** What the notification choice means, in one line. */
  get notificationTextMeaning(): string {
    return this.draft.value.notification_text === 'shown'
      ? 'A notification names the chat and what is waiting or was said; each person may still hide it for their own.'
      : 'Every notification says only "Something is waiting for your decision" or "A chat of yours has an answer", with a link to the chat.';
  }

  ngOnInit(): Promise<void> {
    return this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      const [saved, rules] = await Promise.all([
        this.canSee ? this.service.get() : Promise.resolve(STANDARD),
        this.canSeeRules ? this.guardrail.get() : Promise.resolve(undefined),
      ]);
      if (!saved || rules === null) throw new Error('no settings');
      this.draft.take(saved);
      if (rules) this.takeRules(rules);
    } catch {
      this.loadError = 'Could not reach the server. Check your connection.';
    } finally {
      this.loading = false;
    }
  }

  /** A site as a person types it: a whole address is read for its
   *  name, since that is what they will have copied. */
  readonly siteName = (typed: string): string => {
    let name = typed.toLowerCase();
    name = name.replace(/^[a-z]+:\/\//, '').split(/[/?#]/)[0].split('@').pop() || '';
    return name.replace(/:\d+$/, '').replace(/^\*\./, '').replace(/\.$/, '');
  };

  /** A package by its name alone: a version or an extra typed after it
   *  is dropped. */
  readonly packageName = (typed: string): string =>
    typed.toLowerCase().split(/[=<>!~\[\s]/)[0];

  private takeRules(saved: Partial<GuardrailSettings>): void {
    this.rules.take({ text: saved.text ?? '' });
    this.rulesSaved = {
      updated_by: saved.updated_by ?? '',
      updated_at: saved.updated_at ?? null,
      limit: saved.limit || this.rulesSaved.limit,
    };
  }

  /** Each part that changed, by its own door. One refused leaves its
   *  changes in the form, and the other's saved. */
  async save(): Promise<void> {
    if (this.saving) return;
    this.saving = true;
    this.saveError = '';
    const errors: string[] = [];
    try {
      const safety = this.canEdit && this.draft.dirty
        ? this.service.update(this.draft.value) : null;
      const rules = this.canEditRules && this.rules.dirty
        ? this.guardrail.update(this.rules.value.text) : null;
      const [safetySaved, rulesSaved] = await Promise.all([safety, rules]);
      if (safetySaved) {
        if (safetySaved.error || !safetySaved.safety) {
          errors.push(safetySaved.error || 'The setting could not be saved.');
        } else {
          this.draft.take(safetySaved.safety);
        }
      }
      if (rulesSaved) {
        if (rulesSaved.error || typeof rulesSaved.text !== 'string') {
          errors.push(rulesSaved.error || 'The rules could not be saved.');
        } else {
          this.takeRules(rulesSaved);
        }
      }
      this.saveError = errors.join(' ');
    } finally {
      this.saving = false;
    }
  }

  discard(): void {
    this.draft.discard();
    this.rules.discard();
    this.saveError = '';
  }

  unsavedChangesTo(): string {
    return this.dirty ? 'Safety' : '';
  }
}
