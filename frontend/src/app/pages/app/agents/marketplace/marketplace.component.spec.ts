import { MarketplaceComponent } from './marketplace.component';

describe('MarketplaceComponent', () => {
  function entry(id: string, overrides: any = {}) {
    return {
      id,
      path: `agents/${id}`,
      // Empty until this organization installs it: a ref names an
      // approval, and listing a catalog approves nothing.
      agent_ref: '',
      manifest: {
        agent: { id, name: id, version: '1.0.0', description: `The ${id} agent.` },
        tools: [{ id: 'main', functions: [{ id: 'run', description: 'Runs.', permission_level: 1 }] }],
        implementation: { dependencies: ['requests'] },
        resources: { secrets: [{ id: 'connection', label: 'Connection' }] },
      },
      ...overrides,
    };
  }

  function source(id: string, entries: any[], overrides: any = {}) {
    return {
      source_id: id,
      name: `Source ${id}`,
      url: `https://example.test/${id}.git`,
      ref: 'main',
      has_credential: false,
      status: 'ready',
      sha: 'abc1234567',
      owned: true,
      catalog: { catalog: { name: `Catalog ${id}` }, agents: entries },
      installed_agents: [],
      ...overrides,
    } as any;
  }

  function createComponent(service: any = {}) {
    return new MarketplaceComponent(
      {
        sources: async () => ({ sources: [], referenceCatalogUrl: '' }),
        get: async () => null,
        installCatalogAgent: async () => ({ data: {} }),
        deleteSource: async () => ({}),
        refreshSource: async () => ({}),
        ...service,
      } as any,
      { get: async () => ({ user_id: 'me', groups: [] }),
        peers: async () => [] } as any,
      { can: () => true } as any,
    );
  }

  it('keeps agents under the source they came from', () => {
    const component = createComponent();
    component.sources = [
      source('one', [entry('jira'), entry('invoices')]),
      source('two', [entry('jira')]),
    ];

    expect(component.entriesFor(component.sources[0]).map((e) => e.id))
      .toEqual(['jira', 'invoices']);
    expect(component.entriesFor(component.sources[1]).map((e) => e.id))
      .toEqual(['jira']);
  });

  // ── One roster: every catalog is the organization's own ─────────────

  it('shows a colleague\'s source without handing it over', () => {
    // Not mine to change, but my organization's to install from, and
    // `owned` alone decides the buttons. There is no other kind of
    // source: none of them reaches outside the organization.
    const component = createComponent();
    component.sources = [
      source('mine', []),
      source('colleagues', [], { owned: false }),
    ];

    expect(component.visible.map((s) => s.source_id))
      .toEqual(['mine', 'colleagues']);
    expect(component.visible[1].owned).toBe(false);
  });

  it('filters catalogs by their own name, and agents inside the open one', () => {
    // Two searches, two levels: the catalog box narrows the list of
    // catalogs, the agent box narrows what the open one offers.
    const component = createComponent();
    component.sources = [
      source('one', [entry('invoices')]),
      source('two', [entry('payroll')]),
    ];

    component.searchSources('Source two');
    expect(component.filteredSources.map((s) => s.source_id)).toEqual(['two']);

    component.searchSources('nothing here');
    expect(component.filteredSources).toEqual([]);

    component.searchSources('');
    component.select(component.sources[0]);
    component.search('invoice');
    expect(component.shownEntries.map((e) => e.id)).toEqual(['invoices']);

    component.search('payroll');
    expect(component.shownEntries).toEqual([]);
  });

  it('names the four states an offered agent can be in', () => {
    const component = createComponent();

    expect(component.entryStatus(entry('a') as any)).toBe('available');
    expect(component.entryStatus(entry('b', { installed_version: '1.0.0' }) as any))
      .toBe('installed');
    expect(component.entryStatus(entry('c', { installed_version: '0.9.0' }) as any))
      .toBe('update_available');
    expect(component.entryStatus(entry('d', { errors: ['bad manifest'] }) as any))
      .toBe('broken');
  });

  it('leaves "installed" to the row and keeps the label to versions', () => {
    // The row's other half already says it is installed; saying it twice
    // reads as two different facts.
    const component = createComponent();

    expect(component.entryLabel(entry('b', { installed_version: '1.0.0' }) as any))
      .toBe('v1.0.0');
    expect(component.entryLabel(entry('c', { installed_version: '0.9.0' }) as any))
      .toContain('v0.9.0 installed');
  });

  it('says how stale a source is, and when it has never been read', () => {
    const component = createComponent();
    const now = Date.now();

    expect(component.freshness(source('x', [], { last_checked_at: null })))
      .toBe('never read');
    expect(component.isStale(source('x', [], { last_checked_at: null }))).toBeTrue();

    const fresh = source('x', [], { last_checked_at: new Date(now - 5 * 60000).toISOString() });
    expect(component.freshness(fresh)).toBe('5 min ago');
    expect(component.isStale(fresh)).toBeFalse();

    const old = source('x', [], { last_checked_at: new Date(now - 3 * 86400000).toISOString() });
    expect(component.freshness(old)).toBe('3 days ago');
    expect(component.isStale(old)).toBeTrue();
  });

  it('refuses to offer removal of a source whose agents are installed', () => {
    const component = createComponent();
    const busy = source('one', [entry('jira')], {
      installed_agents: [{ agent_ref: 'agt_jira', name: 'Jira Agent', version: '1.0.0' }],
    });

    expect(component.removalBlocker(busy)).toContain('Jira Agent');
    expect(component.removalBlocker(source('two', [entry('payroll')]))).toBe('');
  });

  // ── Installing ──────────────────────────────────────────────────────

  it('reads what was approved before, but only for an update', async () => {
    let reads = 0;
    const component = createComponent({
      get: async () => {
        reads += 1;
        return { manifest: {} };
      },
    });

    await component.openReview(source('one', []), entry('fresh') as any);
    expect(reads).toBe(0);
    expect(component.reviewApproved).toBeNull();

    await component.openReview(
      source('one', []),
      entry('older', { installed_version: '0.9.0', agent_ref: 'agt_1' }) as any,
    );
    expect(reads).toBe(1);
  });

  it('names an offered agent before it has a ref to name it by', () => {
    const component = createComponent();

    // A platform ref is minted on approval; until then the catalog's own
    // coordinates are all there is.
    expect(component.entryKey(entry('jira') as any)).toBe('agents/jira::jira');
    expect(component.entryKey(entry('jira', { agent_ref: 'agt_9' }) as any))
      .toBe('agt_9');
  });

  it('offers a reference catalog only when the deployment sets one', async () => {
    const bare = createComponent();
    await bare.ngOnInit();
    expect(bare.referenceCatalogUrl).toBe('');

    const offered = createComponent({
      sources: async () => ({
        sources: [],
        referenceCatalogUrl: 'https://example.test/decentai.git',
      }),
    });
    await offered.ngOnInit();
    expect(offered.referenceCatalogUrl).toBe('https://example.test/decentai.git');
  });
});
