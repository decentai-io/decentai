import { Component, OnInit } from '@angular/core';

import { AuthService } from 'src/app/services/auth.service';
import { SafetySettings, SettingsSafetyService } from 'src/app/services/settings-safety.service';
import { DataPageBase } from '../../data-page-base';

/** One choice of a row: the value the platform keeps, and the words a
 *  person chooses by. */
interface Choice { value: string; label: string; help: string; }

/**
 * Safety: what agents may do without asking. A tab of the Settings
 * page.
 *
 * Two agents do things nobody can list beforehand — the Browser opens
 * any site and runs scripts in its pages, the Code agent writes
 * programs — and each asks before every one, on a code card. How often
 * to be asked is set here, with the sites no agent may open at all and
 * the packages a program may install.
 *
 * Every row starts where the platform stood before this page existed:
 * every script and program asked about, any package a card names, MCP
 * servers allowed. A looser choice about asking applies only to code
 * the assistant read and found to do what it says, and the chat is
 * still told what ran.
 */
@Component({
  selector: 'app-safety',
  standalone: false,
  templateUrl: './safety.component.html',
  styleUrls: ['../../data-shared.css', '../../../admin/iam-shared.css', './safety.component.css'],
})
export class SafetyComponent extends DataPageBase implements OnInit {
  loading = true;
  saving = false;
  saved: SafetySettings | null = null;
  draft: SafetySettings | null = null;
  /** What is being typed into each list, before it is added. */
  newSite = '';
  newPackage = '';

  readonly scripts: Choice[] = [
    { value: 'always', label: 'Ask every time',
      help: 'Every script an agent wants to run in a page is shown to you first.' },
    { value: 'once_per_site', label: 'Ask once for a site in a chat',
      help: 'After you allow a script on a site, later scripts on that site in the same chat run without a card.' },
  ];

  readonly programs: Choice[] = [
    { value: 'always', label: 'Ask every time',
      help: 'Every program is shown to you first, a corrected one included.' },
    { value: 'corrections', label: 'Do not ask again for a correction',
      help: 'When a program you allowed fails and is corrected, the correction runs without a card, unless it needs a site, a package, a credential or a file you had not allowed.' },
    { value: 'quiet', label: 'Ask only when it reaches a site or uses a credential',
      help: 'A program that works only on the files it was given runs without a card. Corrections are not asked again either.' },
  ];

  readonly mcp: Choice[] = [
    { value: 'allowed', label: 'People may add MCP servers',
      help: 'Each person adds remote servers for their own chats, and decides which tools are on and what each costs to call.' },
    { value: 'blocked', label: 'No MCP servers',
      help: 'Nobody can add one, and no chat can call a tool of one already added. What was added stays, switched off, until this is allowed again.' },
  ];

  readonly packages: Choice[] = [
    { value: 'any', label: 'Any package the card names',
      help: 'You see the packages on the card and decide there.' },
    { value: 'listed', label: 'Only packages on my list',
      help: 'A program that needs a package off the list is refused before you are asked. What a listed package itself depends on is installed with it.' },
  ];

  constructor(
    private service: SettingsSafetyService,
    public auth: AuthService,
  ) {
    super();
  }

  get canEdit(): boolean { return this.auth.can('settings:safety:update'); }

  get dirty(): boolean {
    return JSON.stringify(this.saved) !== JSON.stringify(this.draft);
  }

  /** Whether anything is looser than the platform's own start. */
  get loosened(): boolean {
    return !!this.draft && (this.draft.scripts !== 'always' || this.draft.programs !== 'always');
  }

  async ngOnInit(): Promise<void> {
    try {
      this.saved = await this.service.get();
      this.draft = this.saved ? this.copy(this.saved) : null;
    } finally {
      this.loading = false;
    }
  }

  private copy(settings: SafetySettings): SafetySettings {
    return { ...settings, blocked_sites: [...settings.blocked_sites],
             allowed_packages: [...settings.allowed_packages] };
  }

  /** A site as a person types it: a whole address is read for its
   *  name, since that is what they will have copied. */
  addSite(): void {
    if (!this.draft) return;
    let name = this.newSite.trim().toLowerCase();
    name = name.replace(/^[a-z]+:\/\//, '').split(/[/?#]/)[0].split('@').pop() || '';
    name = name.replace(/:\d+$/, '').replace(/^\*\./, '').replace(/\.$/, '');
    if (name && !this.draft.blocked_sites.includes(name)) {
      this.draft.blocked_sites = [...this.draft.blocked_sites, name].sort();
    }
    this.newSite = '';
  }

  removeSite(name: string): void {
    if (!this.draft) return;
    this.draft.blocked_sites = this.draft.blocked_sites.filter((site) => site !== name);
  }

  addPackage(): void {
    if (!this.draft) return;
    const name = this.newPackage.trim().toLowerCase().split(/[=<>!~\[\s]/)[0];
    if (name && !this.draft.allowed_packages.includes(name)) {
      this.draft.allowed_packages = [...this.draft.allowed_packages, name].sort();
    }
    this.newPackage = '';
  }

  removePackage(name: string): void {
    if (!this.draft) return;
    this.draft.allowed_packages = this.draft.allowed_packages.filter((one) => one !== name);
  }

  discard(): void {
    if (this.saved) this.draft = this.copy(this.saved);
  }

  async save(): Promise<void> {
    if (!this.draft || this.saving) return;
    this.saving = true;
    try {
      const result = await this.service.update(this.draft);
      if (result.error || !result.safety) {
        this.fail(result.error || 'The setting could not be saved.');
        return;
      }
      this.saved = result.safety;
      this.draft = this.copy(result.safety);
      this.flash('Safety saved. It applies from the next thing an agent does.');
    } finally {
      this.saving = false;
    }
  }
}
