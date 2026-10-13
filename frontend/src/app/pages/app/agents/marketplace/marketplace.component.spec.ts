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

    expect(component.sources.map((s) => s.source_id))
      .toEqual(['mine', 'colleagues']);
    expect(component.sources[1].owned).toBe(false);
  });

  it('shows the first source until the address names another', () => {
    const component = createComponent();
    component.sources = [
      source('one', [entry('invoices')]),
      source('two', [entry('payroll')]),
    ];

    expect(component.current?.source_id).toBe('one');
    component.selectedId = 'two';
    expect(component.current?.source_id).toBe('two');
  });

  it('narrows the chosen source by search and by what is installed, and orders it', () => {
    const component = createComponent();
    component.sources = [source('one', [
      entry('payroll'),
      entry('invoices', { installed_version: '1.0.0' }),
      entry('audit', { installed_version: '0.9.0' }),
    ])];

    component.search('invoice');
    expect(component.shownEntries.map((e) => e.id)).toEqual(['invoices']);
    component.search('nothing here');
    expect(component.shownEntries).toEqual([]);

    component.search('');
    expect(component.filters.map((choice) => choice.name))
      .toEqual(['All 3', 'Not installed 1', 'Installed 2', 'Updates 1']);
    // The same list while the counts stand, so the control is not redrawn.
    expect(component.filters).toBe(component.filters);

    component.setFilter('updates');
    expect(component.shownEntries.map((e) => e.id)).toEqual(['audit']);

    component.setFilter('all');
    expect(component.shownEntries.map((e) => e.id)).toEqual(['payroll', 'invoices', 'audit']);
    component.setSort('name');
    expect(component.shownEntries.map((e) => e.id)).toEqual(['audit', 'invoices', 'payroll']);
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

  it('says the version on offer once, and only what is unusual on the chip', () => {
    // That an agent is installed is the card's foot's to say; saying it
    // on the chip too reads as two different facts.
    const component = createComponent();
    const installed = entry('b', { installed_version: '1.0.0' }) as any;
    const behind = entry('c', { installed_version: '0.9.0' }) as any;

    expect(component.entryVersion(installed)).toBe('v1.0.0');
    expect(component.entryChip(installed)).toBeNull();
    expect(component.entryChip(behind)?.word).toBe('Update available');
    expect(component.entryFacts(behind)).toContain('v0.9.0 installed');
    expect(component.entryChip(entry('d', { errors: ['bad manifest'] }) as any)?.tone).toBe('danger');
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

  it('uninstalls a source\'s agents with it, and only once that was agreed to', async () => {
    const calls: string[] = [];
    let answer = false;
    const component = new MarketplaceComponent(
      {
        sources: async () => ({ sources: [], referenceCatalogUrl: '' }),
        deleteSource: async () => { calls.push('remove'); return {}; },
        purgeSource: async () => { calls.push('purge'); return { data: { uninstalled: ['Jira Agent'] } }; },
      } as any,
      { get: async () => ({ user_id: 'me', groups: [] }), peers: async () => [] } as any,
      { can: () => true } as any,
      undefined, undefined, undefined,
      { ask: async () => answer } as any,
    );
    const busy = source('one', [entry('jira')], {
      installed_agents: [{ agent_ref: 'agt_jira', name: 'Jira Agent', version: '1.0.0' }],
    });

    await component.askRemove(busy);
    expect(calls).toEqual([]);

    answer = true;
    await component.askRemove(busy);
    await component.askRemove(source('two', [entry('payroll')]));
    expect(calls).toEqual(['purge', 'remove']);
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

    await component.openReview(source('one', []), [entry('fresh') as any]);
    expect(reads).toBe(0);
    expect(component.reviewApproved).toEqual({});

    await component.openReview(
      source('one', []),
      [entry('older', { installed_version: '0.9.0', agent_ref: 'agt_1' }) as any],
    );
    expect(reads).toBe(1);
    expect(Object.keys(component.reviewApproved)).toEqual(['older']);
  });

  it('puts the ticked agents under one review, and installs none before it is approved', async () => {
    const installed: string[] = [];
    const one = source('one', [entry('gmail'), entry('drive'), entry('forms')]);
    const component = createComponent({
      sources: async () => ({ sources: [one], referenceCatalogUrl: '' }),
      installCatalogAgent: async (_source: any, chosen: any) => {
        if (chosen.id === 'drive') return { error: 'its package could not be built' };
        installed.push(chosen.id);
        return { data: {} };
      },
    });
    component.sources = [one];
    component.toggleSelect(one, one.catalog.agents[0]);
    component.toggleSelect(one, one.catalog.agents[1]);

    await component.reviewSelected(one);
    expect(component.review?.entries.map((e) => e.id)).toEqual(['gmail', 'drive']);
    expect(component.approveLabel).toBe('Approve and install 2');
    expect(installed).toEqual([]);

    await component.approve();
    // One refusal does not stop the rest, and what was refused stays
    // under review with the reason.
    expect(installed).toEqual(['gmail']);
    expect(component.review?.entries.map((e) => e.id)).toEqual(['drive']);
    expect(component.error).toContain('its package could not be built');
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
