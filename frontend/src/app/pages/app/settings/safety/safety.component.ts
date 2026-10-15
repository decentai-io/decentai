import { Component, OnInit } from '@angular/core';

import { Draft } from 'src/app/components/ui/draft';
import { Choice } from 'src/app/components/ui/radio-keys';
import { HasUnsavedChanges } from 'src/app/guards/unsaved-changes.guard';
import { AuthService } from 'src/app/services/auth.service';
import { SafetySettings, SettingsSafetyService } from 'src/app/services/settings-safety.service';

/** Where the platform stood before this page existed: every script and
 *  program asked about, any package a card names, MCP servers and
 *  webhooks allowed. */
const STANDARD: SafetySettings = {
  mcp: 'allowed', webhooks: 'allowed', blocked_sites: [], scripts: 'always', programs: 'always',
  packages: 'any', allowed_packages: [],
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
 * One form, saved with the save bar.
 */
@Component({
  selector: 'app-safety',
  standalone: false,
  templateUrl: './safety.component.html',
})
export class SafetyComponent implements OnInit, HasUnsavedChanges {
  loading = true;
  loadError = '';
  saving = false;
  saveError = '';

  readonly draft = new Draft<SafetySettings>(STANDARD);

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

  constructor(
    private service: SettingsSafetyService,
    public auth: AuthService,
  ) {}

  get canEdit(): boolean { return this.auth.can('settings:safety:update'); }

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

  ngOnInit(): Promise<void> {
    return this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.loadError = '';
    try {
      const saved = await this.service.get();
      if (!saved) throw new Error('no settings');
      this.draft.take(saved);
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

  async save(): Promise<void> {
    if (this.saving) return;
    this.saving = true;
    this.saveError = '';
    try {
      const result = await this.service.update(this.draft.value);
      if (result.error || !result.safety) {
        this.saveError = result.error || 'The setting could not be saved.';
        return;
      }
      this.draft.take(result.safety);
    } finally {
      this.saving = false;
    }
  }

  discard(): void {
    this.draft.discard();
    this.saveError = '';
  }

  unsavedChangesTo(): string {
    return this.draft.dirty ? 'Safety' : '';
  }
}
