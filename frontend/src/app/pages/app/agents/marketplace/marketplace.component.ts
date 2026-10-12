import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';
import {
  AgentSource, AgentsService, CatalogEntry,
} from 'src/app/services/agents.service';
import { DataPageBase } from '../../data-page-base';
import { CardChip } from '../agent-card.component';
import { agentFacts, networkOf } from '../agent-shared';
import { SourceDraft } from './source-form.component';
import { AuthService } from 'src/app/services/auth.service';
import { Profile, ProfileService } from 'src/app/services/profile.service';

type EntryStatus = 'broken' | 'installed' | 'update_available' | 'available';

/**
 * The marketplace: where agents come FROM.
 *
 * Catalogs are listed first, then their agents, then one agent's
 * install details. Each level has its own search and pagination so the
 * page stays useful when organizations have hundreds of catalogs.
 *
 * Nothing on this page is running. Everything here is a proposal until
 * an agent is approved by name, at which point it belongs to the
 * Installed Agents page instead. Keeping the two apart is the point:
 * shopping and operating are different jobs.
 *
 * Each view has an address under /agents/marketplace: a source's
 * catalog is /<source>, the review of one of its agents
 * /<source>/<agent>, the source form /sources/new and /<source>/edit.
 * They are views of this one page (routes/app/agents.routes.ts): what
 * a person presses changes the view and the address together, and the
 * page reads the address after each navigation, so Back and a pasted
 * link both work.
 */
@Component({
  selector: 'app-agent-marketplace',
  standalone: false,
  templateUrl: './marketplace.component.html',
  styleUrls: [
    '../../data-shared.css',
    '../../../admin/iam-shared.css',
    '../agent-shared.css',
    './marketplace.component.css',
  ],
})
export class MarketplaceComponent extends DataPageBase implements OnInit, OnDestroy {
  private address = new Subscription();
  loading = true;
  /** The one source or agent an action is in flight for. */
  busyId = '';

  sources: AgentSource[] = [];
  referenceCatalogUrl = '';

  /** Which source is open. */
  selectedId = '';
  sourceQuery = '';
  sourcePage = 1;
  readonly sourcePageSize = 12;
  query = '';
  page = 1;
  readonly pageSize = 12;

  formOpen = false;
  editing: AgentSource | null = null;
  saving = false;

  review: { source: AgentSource; entry: CatalogEntry } | null = null;
  /** Entries ticked for a bulk install, keyed per source so one card's
   *  "install selected" never drags another card's ticks along. */
  selected = new Set<string>();
  reviewApproved: any = null;
  reviewLoading = false;
  private catalogScrollY = 0;

  removing: AgentSource | null = null;

  constructor(
    private service: AgentsService,
    private profiles: ProfileService,
    public auth: AuthService,
    private route?: ActivatedRoute,
    private router?: Router,
  ) {
    super();
  }

  ngOnDestroy(): void {
    this.address.unsubscribe();
  }

  /** Say the view in the address: the segments after /agents/marketplace. */
  private go(...segments: string[]): void {
    void this.router?.navigate(['/agents/marketplace', ...segments]);
  }

  /** Show what the address names. Each part is left alone where the
   *  page already shows it, so pressing something here, which changes
   *  the view and then the address, does not do the work twice. */
  private async readAddress(): Promise<void> {
    const child = this.route?.snapshot.firstChild;
    const path = child?.routeConfig?.path ?? '';
    const named = child?.paramMap.get('source') || '';
    const source = this.sources.find((candidate) => candidate.source_id === named) || null;

    if (path === 'sources/new') {
      if (!this.formOpen || this.editing) this.showForm(null);
      return;
    }
    if (path === ':source/edit' && source) {
      if (this.editing !== source) this.showForm(source);
      return;
    }
    if (this.formOpen) this.hideForm();
    if (!source) {
      if (this.review) this.hideReview();
      if (this.selectedId) this.showSources();
      if (named) {
        this.fail('That source is not one of this organization\'s any more.');
        this.go();
      }
      return;
    }
    if (this.selectedId !== source.source_id) this.showSource(source);
    const agent = child?.paramMap.get('agent') || '';
    if (!agent) {
      if (this.review) this.hideReview();
      return;
    }
    const entry = this.entriesFor(source).find(
      (candidate) => candidate.id === agent || candidate.agent_ref === agent);
    if (!entry) {
      this.fail('That agent is not in this source\'s catalog.');
      this.go(source.source_id);
      return;
    }
    if (this.review?.entry !== entry) await this.showReview(source, entry);
  }

  entryName(entry: CatalogEntry): string {
    return entry.manifest?.agent?.name || entry.id;
  }

  async ngOnInit(): Promise<void> {
    await this.load();
    this.loading = false;
    const wanted = this.route?.snapshot.queryParamMap.get('review');
    if (wanted) await this.openReviewOf(wanted);
    else await this.readAddress();
    if (this.router) {
      this.address.add(this.router.events
        .pipe(filter((event) => event instanceof NavigationEnd))
        .subscribe(() => void this.readAddress()));
    }
  }

  /** An installed agent's own page asked for its update to be read
   *  (`?review=<the agent's ref>`): its source is opened and the review
   *  with it. An agent no source lists any more has nothing to review,
   *  and the page says so. */
  async openReviewOf(agentRef: string): Promise<void> {
    for (const source of this.sources) {
      const entry = this.entriesFor(source)
        .find((candidate) => candidate.agent_ref === agentRef);
      if (entry) {
        this.select(source);
        await this.openReview(source, entry);
        return;
      }
    }
    this.fail('No source lists that agent any more: there is nothing to review.');
  }

  private async load(): Promise<void> {
    if (!this.profile) {
      const [profile, peers] = await Promise.all([
        this.profiles.get(), this.profiles.peers(),
      ]);
      this.profile = profile;
      this.peers = peers;
      this.myGroups = (profile?.groups ?? []).filter(
        (group) => group.group_id !== 'everyone',
      );
    }
    const page = await this.service.sources();
    this.sources = page.sources;
    this.referenceCatalogUrl = page.referenceCatalogUrl;
  }

  // ── The list ────────────────────────────────────────────────────────

  /** Every catalog this organization saved — there is no other kind. */
  get visible(): AgentSource[] {
    return this.sources;
  }

  get current(): AgentSource | null {
    return this.visible.find((source) => source.source_id === this.selectedId) || null;
  }

  select(source: AgentSource): void {
    this.go(source.source_id);
    this.showSource(source);
  }

  backToSources(): void {
    this.go();
    this.showSources();
  }

  private showSource(source: AgentSource): void {
    this.selectedId = source.source_id;
    this.query = '';
    this.page = 1;
  }

  private showSources(): void {
    this.selectedId = '';
    this.query = '';
    this.page = 1;
  }

  get filteredSources(): AgentSource[] {
    const query = this.sourceQuery.trim().toLowerCase();
    if (!query) return this.visible;
    return this.visible.filter((source) => `${source.name} ${source.url} ${source.ref || ''}`
      .toLowerCase().includes(query));
  }

  get pagedSources(): AgentSource[] {
    const start = (this.sourcePage - 1) * this.sourcePageSize;
    return this.filteredSources.slice(start, start + this.sourcePageSize);
  }

  get sourcePageCount(): number {
    return Math.max(1, Math.ceil(this.filteredSources.length / this.sourcePageSize));
  }

  searchSources(value: string): void {
    this.sourceQuery = value;
    this.sourcePage = 1;
  }

  /** The open source's agents, narrowed by the filter box. */
  get shownEntries(): CatalogEntry[] {
    const source = this.current;
    if (!source) return [];
    const query = this.query.trim().toLowerCase();
    const entries = this.entriesFor(source);
    if (!query) return entries;
    // The parentheses are the point: without them `.toLowerCase()` binds
    // to the description alone and the whole expression is a string, so
    // every entry passed and the box filtered nothing.
    return entries.filter((entry) => (
      `${entry.id} ${this.entryName(entry)} `
      + `${entry.manifest?.agent?.description || ''}`
    ).toLowerCase().includes(query));
  }

  get pagedEntries(): CatalogEntry[] {
    const start = (this.page - 1) * this.pageSize;
    return this.shownEntries.slice(start, start + this.pageSize);
  }

  get pageCount(): number {
    return Math.max(1, Math.ceil(this.shownEntries.length / this.pageSize));
  }

  search(value: string): void {
    this.query = value;
    this.page = 1;
  }

  entryDescription(entry: CatalogEntry): string {
    return entry.manifest?.agent?.description || '';
  }

  entriesFor(source: AgentSource): CatalogEntry[] {
    return source.catalog?.agents || [];
  }

  // ── What state an offered agent is in ───────────────────────────────

  entryStatus(entry: CatalogEntry): EntryStatus {
    if ((entry.errors || []).length) return 'broken';
    if (!entry.installed_version) return 'available';
    return entry.installed_version === entry.manifest?.agent?.version
      ? 'installed' : 'update_available';
  }

  /** The version on offer, said once beside the name. */
  entryVersion(entry: CatalogEntry): string {
    const offered = entry.manifest?.agent?.version;
    return offered ? `v${offered}` : '';
  }

  /** What the card's chip says: only what is not the ordinary case.
   *  That it is installed is the card's foot's to say. */
  entryChip(entry: CatalogEntry): CardChip | null {
    switch (this.entryStatus(entry)) {
      case 'update_available': return { word: 'Update available', tone: '' };
      case 'broken': return { word: 'Cannot be read', tone: 'danger' };
      default: return null;
    }
  }

  /** The card's line of facts, from the manifest on offer: its
   *  functions, the credentials it declares, where it connects; then
   *  the version installed where a newer one is offered, and what it
   *  ships to try it on. */
  entryFacts(entry: CatalogEntry): string[] {
    const manifest = entry.manifest || {};
    const functions = (manifest.tools || []).reduce(
      (count: number, tool: any) => count + (tool?.functions || []).length, 0);
    const facts = agentFacts(functions, (manifest.resources?.secrets || []).length,
      networkOf(manifest));
    if (this.entryStatus(entry) === 'update_available') {
      facts.push(`v${entry.installed_version} installed`);
    }
    if (entry.samples?.errors?.length) {
      facts.push('its sample sheet has errors');
    } else if (entry.samples?.records) {
      const records = entry.samples.records;
      facts.push(`${records} sample ${records === 1 ? 'record' : 'records'}`);
    }
    return facts;
  }

  /** Where an entry's card goes: its review, or nowhere new for one
   *  that cannot be read. */
  entryLink(source: AgentSource, entry: CatalogEntry): string[] {
    return this.entryStatus(entry) === 'broken'
      ? ['/agents/marketplace', source.source_id]
      : ['/agents/marketplace', source.source_id, entry.id];
  }

  /** A key for an offered agent that exists before it is installed: its
   *  platform ref is minted on approval, so until then the catalog's own
   *  coordinates are all there is to name it by. */
  entryKey(entry: CatalogEntry): string {
    return entry.agent_ref || `${entry.path}::${entry.id}`;
  }

  /** How long ago this source's catalog was read. A catalog with no age
   *  on it cannot be told apart from one read a week ago. */
  freshness(source: AgentSource): string {
    const checked = source.last_checked_at ? Date.parse(source.last_checked_at) : NaN;
    if (isNaN(checked)) return 'never read';
    const minutes = Math.floor((Date.now() - checked) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    const days = Math.floor(hours / 24);
    return `${days} day${days === 1 ? '' : 's'} ago`;
  }

  isStale(source: AgentSource): boolean {
    const checked = source.last_checked_at ? Date.parse(source.last_checked_at) : NaN;
    return isNaN(checked) || Date.now() - checked > 24 * 60 * 60 * 1000;
  }

  installedCount(source: AgentSource): number {
    return (source.installed_agents || []).length;
  }

  /** Why this source cannot be removed yet, in the words the backend
   *  will use if the button is pressed anyway. */
  removalBlocker(source: AgentSource): string {
    const installed = source.installed_agents || [];
    if (!installed.length) return '';
    return 'Uninstall its agents first: '
      + installed.map((agent) => agent.name).join(', ');
  }

  // ── Saving a source ─────────────────────────────────────────────────

  startCreate(): void {
    this.go('sources', 'new');
    this.showForm(null);
  }

  startEdit(source: AgentSource): void {
    this.go(source.source_id, 'edit');
    this.showForm(source);
  }

  /** Back to where the form was opened from: the source being changed,
   *  the one that was open, or the list. */
  closeForm(): void {
    const back = this.editing?.source_id || this.selectedId;
    this.hideForm();
    this.go(...(back ? [back] : []));
  }

  private showForm(editing: AgentSource | null): void {
    this.editing = editing;
    this.formOpen = true;
    this.error = '';
  }

  private hideForm(): void {
    this.formOpen = false;
    this.editing = null;
  }

  profile: Profile | null = null;
  peers: { user_id: string; user_name: string; email: string }[] = [];
  /** A stored array, deliberately not a getter: as an @Input a fresh
   *  array every change-detection cycle re-fires the form's ngOnChanges
   *  forever. */
  myGroups: { group_id: string; group_name: string }[] = [];

  /** How a row says who can use it — same words as everywhere else. */
  shareLabel(source: { owner?: { groups: string[]; users: string[] };
                       created_by_id?: string }): string {
    const groups = source.owner?.groups ?? ['everyone'];
    if (groups.includes('everyone')) return 'Organization-wide';
    if (groups.length) {
      return `${groups.length} group${groups.length === 1 ? '' : 's'}`;
    }
    const others = (source.owner?.users || [])
      .filter((u) => u !== source.created_by_id);
    if (others.length) {
      return `${others.length} ${others.length === 1 ? 'person' : 'people'}`;
    }
    return 'Private';
  }

  async saveSource(draft: SourceDraft): Promise<void> {
    this.saving = true;
    try {
      const result = this.editing
        ? await this.service.updateSource(this.editing.source_id, draft.name,
            draft.url, draft.ref, draft.credential, draft.owner)
        : await this.service.createSource(draft.name, draft.url, draft.ref,
            draft.credential ?? null, draft.owner);
      if (result.error) return this.fail(result.error);
      this.hideForm();
      await this.load();
      // A source just saved is the one to look at: its catalog opens.
      const saved = this.sources.find(
        (source) => source.source_id === result.data?.source?.source_id);
      if (saved) this.select(saved);
      else this.go(...(this.selectedId ? [this.selectedId] : []));
      this.sayHowTheReadWent(result.data?.source, 'Source saved and its catalog read.');
    } finally {
      this.saving = false;
    }
  }

  async refresh(source: AgentSource): Promise<void> {
    this.busyId = source.source_id;
    try {
      const result = await this.service.refreshSource(source.source_id);
      await this.load();
      if (result.error) return this.fail(result.error);
      this.sayHowTheReadWent(result.data?.source, 'Catalog read again.');
    } finally {
      this.busyId = '';
    }
  }

  /** A source is saved even when its catalog could not be read, and the
   *  answer says which it was: the notice follows the answer. */
  private sayHowTheReadWent(source: AgentSource | undefined, read: string): void {
    if (source?.status === 'error') {
      return this.fail(`"${source.name}" is saved, but its catalog could not be read`
        + (source.last_error ? `: ${source.last_error}` : '.'));
    }
    this.flash(read);
  }

  // ── Installing ──────────────────────────────────────────────────────

  // ── Selection ───────────────────────────────────────────────────────

  private selectionKey(source: AgentSource, entry: CatalogEntry): string {
    return `${source.source_id}::${this.entryKey(entry)}`;
  }

  installable(entry: CatalogEntry): boolean {
    const status = this.entryStatus(entry);
    return status === 'available' || status === 'update_available';
  }

  selectableEntries(source: AgentSource): CatalogEntry[] {
    return this.entriesFor(source).filter((entry) => this.installable(entry));
  }

  isSelected(source: AgentSource, entry: CatalogEntry): boolean {
    return this.selected.has(this.selectionKey(source, entry));
  }

  toggleSelect(source: AgentSource, entry: CatalogEntry): void {
    const key = this.selectionKey(source, entry);
    if (!this.selected.delete(key)) this.selected.add(key);
  }

  selectedCount(source: AgentSource): number {
    return this.selectableEntries(source)
      .filter((entry) => this.isSelected(source, entry)).length;
  }

  allSelected(source: AgentSource): boolean {
    const pool = this.selectableEntries(source);
    return pool.length > 0
      && pool.every((entry) => this.isSelected(source, entry));
  }

  toggleAll(source: AgentSource): void {
    const pool = this.selectableEntries(source);
    const everything = this.allSelected(source);
    for (const entry of pool) {
      const key = this.selectionKey(source, entry);
      if (everything) this.selected.delete(key);
      else this.selected.add(key);
    }
  }

  /** Install every ticked agent, one after another — each gets its own
   *  approval, and one failure never stops the rest. */
  async installSelected(source: AgentSource): Promise<void> {
    const chosen = this.selectableEntries(source)
      .filter((entry) => this.isSelected(source, entry));
    if (!chosen.length || this.busyId) return;

    this.busyId = `bulk:${source.source_id}`;
    const failed: string[] = [];
    try {
      for (const entry of chosen) {
        const result = await this.service.installCatalogAgent(source, entry);
        if (result.error) {
          failed.push(`${entry.manifest?.agent?.name || entry.id}: `
            + result.error);
        } else {
          this.selected.delete(this.selectionKey(source, entry));
        }
      }
      this.busyId = '';
      await this.load();
      if (failed.length) return this.fail(failed.join(' — '));
      this.flash(chosen.length === 1
        ? `${chosen[0].manifest?.agent?.name || chosen[0].id} installed.`
        : `${chosen.length} agents installed — they are on the Installed `
          + `Agents page now.`);
    } finally {
      this.busyId = '';
    }
  }

  async openReview(source: AgentSource, entry: CatalogEntry): Promise<void> {
    this.go(source.source_id, entry.id);
    await this.showReview(source, entry);
  }

  private async showReview(source: AgentSource, entry: CatalogEntry): Promise<void> {
    this.catalogScrollY = window.scrollY;
    this.review = { source, entry };
    this.reviewApproved = null;
    this.error = '';
    requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'auto' }));
    // An update is only reviewable against what was approved before it —
    // and an INSTALLED agent against itself, so the page can say "this
    // is what you agreed to".
    if (this.entryStatus(entry) === 'update_available'
        || this.entryStatus(entry) === 'installed') {
      this.reviewLoading = true;
      try {
        this.reviewApproved = await this.service.get(entry.agent_ref);
      } finally {
        this.reviewLoading = false;
      }
    }
  }

  closeReview(): void {
    if (this.busyId || !this.review) return;
    this.go(this.review.source.source_id);
    this.hideReview();
  }

  private hideReview(): void {
    this.review = null;
    this.reviewApproved = null;
    requestAnimationFrame(() => window.scrollTo({ top: this.catalogScrollY, behavior: 'auto' }));
  }

  async approve(): Promise<void> {
    if (!this.review) return;
    const { source, entry } = this.review;
    this.busyId = this.entryKey(entry);
    try {
      const result = await this.service.installCatalogAgent(source, entry);
      if (result.error) return this.fail(result.error);
      this.busyId = '';
      this.selected.delete(this.selectionKey(source, entry));
      await this.load();
      // Installed: the next thing to do is on the agent's own page,
      // which opens on its credentials when one is missing.
      const ref = this.sources.find((candidate) => candidate.source_id === source.source_id)
        ?.catalog?.agents?.find((candidate) => candidate.id === entry.id
          && candidate.path === entry.path)?.agent_ref;
      if (ref && this.router) {
        void this.router.navigate(['/agents', ref]);
        return;
      }
      this.closeReview();
      this.flash(`${entry.manifest?.agent?.name || entry.id} installed — it is `
        + `on the Installed Agents page now.`);
    } finally {
      this.busyId = '';
    }
  }

  // ── Removing ────────────────────────────────────────────────────────

  requestRemove(source: AgentSource): void {
    this.removing = source;
    this.error = '';
  }

  closeRemove(): void {
    if (!this.busyId) this.removing = null;
  }

  async remove(source: AgentSource): Promise<void> {
    this.busyId = source.source_id;
    try {
      const result = await this.service.deleteSource(source.source_id);
      if (result.error) return this.fail(result.error);
      this.removing = null;
      await this.load();
      this.flash(`Source "${source.name}" removed.`);
    } finally {
      this.busyId = '';
    }
  }


  // ── Handing over ────────────────────────────────────────────────────

  transferTarget: AgentSource | null = null;
  transferring = false;

  requestTransfer(source: AgentSource): void {
    this.transferTarget = source;
    this.error = '';
  }

  closeTransfer(): void {
    if (!this.transferring) this.transferTarget = null;
  }

  async transfer(person: { user_id: string; user_name: string; email: string }): Promise<void> {
    const source = this.transferTarget;
    if (!source) return;
    this.transferring = true;
    try {
      const result = await this.service.transferSource(source.source_id, person.user_id);
      // Closed either way: a refusal is said on the page, where it can
      // be read, not under the dialog.
      this.transferTarget = null;
      if (result.error) return this.fail(result.error);
      await this.load();
      this.flash(`"${source.name}" handed over to ${person.user_name || person.email}.`);
    } finally {
      this.transferring = false;
    }
  }

  /** Uninstall every agent installed from the source, then remove it. */
  async purge(source: AgentSource): Promise<void> {
    this.busyId = source.source_id;
    try {
      const result = await this.service.purgeSource(source.source_id);
      if (result.error) return this.fail(result.error);
      const names: string[] = result.data?.uninstalled || [];
      this.removing = null;
      await this.load();
      this.flash(names.length
        ? `Uninstalled ${names.join(', ')} and removed "${source.name}".`
        : `Source "${source.name}" removed.`);
    } finally {
      this.busyId = '';
    }
  }
}
