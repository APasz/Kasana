(() => {
  'use strict';

  const {
    escapeHtml,
    localArtworkUrl,
    providerDisplayName,
    providerEntryUrl,
    publishKanvasToast,
    requestKanvasConfirmation,
    tmdbEntryReferenceFromUrl,
    tmdbEntryReferenceFromValue,
  } = window.kanvasInternals;

  const ITEM_EDITOR_KINDS = ['movie', 'series', 'season', 'episode', 'special', 'extra'];
  const ITEM_EDITOR_KIND_LABELS = {
    movie: 'Movie',
    series: 'Series',
    season: 'Season',
    episode: 'Episode',
    special: 'Special',
    extra: 'Extra'
  };
  const ITEM_EDITOR_LOCK_FIELDS = [
    {value: 'title', label: 'Title', kinds: ITEM_EDITOR_KINDS},
    {value: 'sort_title', label: 'Sort title', kinds: ITEM_EDITOR_KINDS},
    {value: 'release_date', label: 'Release date', kinds: ['movie', 'series', 'season', 'episode']},
    {value: 'overview', label: 'Overview', kinds: ITEM_EDITOR_KINDS},
    {value: 'season_number', label: 'Season number', kinds: ['season', 'episode']},
    {value: 'episode_number', label: 'Episode number', kinds: ['episode']}
  ];
  const ITEM_EDITOR_NUMBER_FIELDS = {
    season: [{name: 'seasonNumber', value: 'season_number', placeholder: 'Season', label: 'Season number'}],
    episode: [
      {name: 'seasonNumber', value: 'season_number', placeholder: 'Season', label: 'Season number'},
      {name: 'episodeNumber', value: 'episode_number', placeholder: 'Episode', label: 'Episode number'}
    ]
  };
  const ITEM_EDITOR_PARENT_KINDS = new Set(['season', 'episode', 'special', 'extra']);
  const ITEM_EDITOR_MATCHABLE_KINDS = new Set(['movie', 'series']);
  const ITEM_EDITOR_TABS = [
    {id: 'details', label: 'Details'},
    {id: 'match', label: 'Match'},
    {id: 'organise', label: 'Organise'},
    {id: 'artwork', label: 'Artwork'},
    {id: 'playback', label: 'Playback'},
    {id: 'history', label: 'History'}
  ];
  const ITEM_EDITOR_DEFAULT_TAB = 'details';

  const itemEditorKind = (value) => ITEM_EDITOR_KINDS.includes(value) ? value : 'movie';
  const itemEditorTab = (value) => ITEM_EDITOR_TABS.some((tab) => tab.id === value)
    ? value
    : ITEM_EDITOR_DEFAULT_TAB;
  const itemEditorRelevantLocks = (kind) => ITEM_EDITOR_LOCK_FIELDS.filter((field) => field.kinds.includes(kind));
  const itemEditorNumberFields = (kind) => ITEM_EDITOR_NUMBER_FIELDS[kind] || [];
  const itemEditorItemValue = (item, value) => item?.[value] ?? '';
  const normaliseItemEditorParentChoice = (value) => {
    if (!value || typeof value !== 'object') return null;
    const choice = value;
    if (!Number.isSafeInteger(choice.id) || choice.id <= 0) return null;
    if (typeof choice.title !== 'string' || !choice.title.trim()) return null;
    if (!ITEM_EDITOR_KINDS.includes(choice.kind)) return null;
    if (choice.season_number != null && (!Number.isSafeInteger(choice.season_number) || choice.season_number < 0)) return null;
    return choice;
  };
  const normaliseItemEditorMetadataBinding = (value) => {
    if (!value || typeof value !== 'object') return null;
    const binding = value;
    if (typeof binding.provider !== 'string' || !binding.provider.trim()) return null;
    if (typeof binding.provider_id !== 'string' || !binding.provider_id.trim()) return null;
    if (!ITEM_EDITOR_MATCHABLE_KINDS.has(binding.kind)) return null;
    if (binding.title != null && (typeof binding.title !== 'string' || !binding.title.trim())) return null;
    if (binding.year != null && (!Number.isSafeInteger(binding.year) || binding.year < 1 || binding.year > 9999)) return null;
    return binding;
  };
  const normaliseItemEditorMetadataSearchResult = (value) => {
    const result = normaliseItemEditorMetadataBinding(value);
    if (!result || typeof result.title !== 'string' || !result.title.trim()) return null;
    if (typeof value.confidence !== 'number' || !Number.isFinite(value.confidence) || value.confidence < 0 || value.confidence > 1) return null;
    return value;
  };

  class KanvasItemEditor extends HTMLElement {
    constructor() {
      super();
      this.dialog = null;
      this.status = null;
      this.controller = null;
      this.lockedMetadataFields = new Set();
      this.initialSelectedArtwork = new Map();
      this.currentItem = null;
      this.currentMetadataBinding = null;
      this.metadataSearchResults = [];
      this.selectedMetadataMatch = null;
      this.parentChoices = [];
      this.parentChoiceLoad = 0;
      this.isDirty = false;
      this.isSaving = false;
      this.activeTab = ITEM_EDITOR_DEFAULT_TAB;
    }

    connectedCallback() {
      this.activeTab = itemEditorTab(this.getAttribute('initial-tab'));
      this.innerHTML = '<button type="button" class="k-button" data-item-edit-open>Edit Details</button><dialog class="k-kanvas-dialog k-item-editor"><div class="k-picker" data-item-editor-content></div></dialog><kanvas-confirmation-dialog data-item-editor-confirmation></kanvas-confirmation-dialog>';
      this.dialog = this.querySelector('dialog');
      this.querySelector('[data-item-edit-open]')?.addEventListener('click', () => this.open());
      this.dialog?.addEventListener('cancel', (event) => {
        event.preventDefault();
        void this.requestClose();
      });
      this.dialog?.addEventListener('close', () => {
        this.controller?.abort();
        this.isDirty = false;
        this.isSaving = false;
      });
      if (this.getAttribute('open-on-load') === 'true') void this.open();
    }

    disconnectedCallback() { this.controller?.abort(); }

    async open() {
      if (!this.dialog) return;
      if (!this.dialog.open) this.dialog.showModal();
      await this.load();
    }

    requestConfirmation(confirmation) {
      return requestKanvasConfirmation(
        this.querySelector('[data-item-editor-confirmation]'), confirmation
      );
    }

    confirmDiscard() {
      if (this.isSaving || !this.isDirty) return !this.isSaving;
      return this.requestConfirmation({
        title: 'Discard unsaved changes?',
        message: 'Your local edits to this item will be lost.',
        confirmLabel: 'Discard changes',
        destructive: true,
      });
    }

    async requestClose() {
      if (!(await this.confirmDiscard())) return;
      this.dialog?.close();
    }

    async load() {
      const content = this.querySelector('[data-item-editor-content]');
      if (!content) return;
      content.innerHTML = '<div class="k-picker__status" aria-live="polite">Loading editable metadata…</div>';
      this.controller?.abort();
      this.controller = new AbortController();
      const source = this.getAttribute('source');
      if (!source) {
        content.innerHTML = '<div class="k-picker__status">This item cannot be edited because its data source is unavailable.</div>';
        return;
      }
      try {
        const response = await fetch(source, {headers: {'Accept': 'application/json'}, credentials: 'same-origin', signal: this.controller.signal});
        if (!response.ok) throw new Error('Item editor request failed');
        const payload = await response.json();
        if (!payload.item || typeof payload.item !== 'object') throw new Error('Item editor response was invalid');
        this.render(
          payload.item,
          Array.isArray(payload.audit) ? payload.audit : [],
          Array.isArray(payload.collectionChoices) ? payload.collectionChoices : [],
          Array.isArray(payload.collectionRelationships) ? payload.collectionRelationships : [],
          Array.isArray(payload.parentChoices) ? payload.parentChoices : [],
          payload.metadataBinding
        );
      } catch (error) {
        if (error?.name !== 'AbortError') content.innerHTML = '<div class="k-picker__status">This item could not be loaded for editing. Close and try again.</div>';
      }
    }

    render(item, audit, collectionChoices, collectionRelationships, parentChoices, metadataBinding) {
      const content = this.querySelector('[data-item-editor-content]');
      if (!content) return;
      this.currentItem = item;
      this.currentMetadataBinding = normaliseItemEditorMetadataBinding(metadataBinding);
      this.metadataSearchResults = [];
      this.selectedMetadataMatch = null;
      const selected = new Map((Array.isArray(item.selected_artwork) ? item.selected_artwork : []).map((entry) => [entry.kind, entry.artwork_id]));
      this.initialSelectedArtwork = selected;
      const locks = new Set(Array.isArray(item.locked_metadata_fields) ? item.locked_metadata_fields : []);
      this.lockedMetadataFields = locks;
      const artworks = Array.isArray(item.artwork) ? item.artwork : [];
      const artworkKinds = [...new Set(artworks.map((artwork) => artwork.kind))];
      const artworkRows = this.renderArtworkRows(artworks, artworkKinds, selected);
      const auditRows = audit.length ? audit.map((entry) => `<li>${escapeHtml(entry.actor || 'administrator')} · ${escapeHtml((entry.changed_fields || []).join(', ') || 'updated')} · ${escapeHtml(entry.occurred_at || '')}</li>`).join('') : '<li>No local edits have been recorded.</li>';
      const kind = itemEditorKind(item.kind);
      const collectionControls = this.renderCollectionControls(
        item, collectionChoices, collectionRelationships
      );
      this.parentChoices = parentChoices.map(normaliseItemEditorParentChoice).filter(Boolean);
      const playbackControls = this.renderPlaybackDefaults(item);
      const tabs = this.editorTabs(Boolean(playbackControls));
      this.activeTab = this.availableTab(this.activeTab, tabs);
      content.innerHTML = `<form class="k-item-editor__form" data-item-editor-form><div class="k-picker__header"><div class="k-item-editor__heading"><strong title="${escapeHtml(item.title || `Item ${item.id || ''}`)}">Edit ${escapeHtml(item.title || `Item ${item.id || ''}`)}</strong><span>${escapeHtml(ITEM_EDITOR_KIND_LABELS[kind])}</span></div><button type="button" class="k-button" data-item-editor-close>Close</button></div>${this.renderTabNavigation(tabs)}<div class="k-item-editor__tab-panels">${this.renderTabPanel('details', this.renderDetailsTab(kind, item), 'Details')}${this.renderTabPanel('match', this.renderMatchTab(kind, locks, this.currentMetadataBinding, item.title), 'Match')}${this.renderTabPanel('organise', this.renderOrganiseTab(kind, item, collectionControls), 'Organise')}${this.renderTabPanel('artwork', this.renderArtworkTab(artworkRows, kind, this.currentMetadataBinding, item.show_artwork_label !== false), 'Artwork')}${playbackControls ? this.renderTabPanel('playback', playbackControls, 'Playback') : ''}${this.renderTabPanel('history', this.renderHistoryTab(auditRows), 'History')}</div><div class="k-picker__status" data-item-editor-status aria-live="polite"></div><div class="k-action-row"><button type="submit" class="k-button k-button--primary">Save local edits</button></div></form>`;
      this.status = content.querySelector('[data-item-editor-status]');
      content.querySelector('[data-item-editor-close]')?.addEventListener('click', () => this.requestClose());
      const form = content.querySelector('[data-item-editor-form]');
      form?.addEventListener('submit', (event) => this.submit(event));
      form?.addEventListener('input', (event) => {
        if (!event.target?.closest?.('[data-item-match-workflow]')) this.isDirty = true;
      });
      form?.addEventListener('change', (event) => {
        if (!event.target?.closest?.('[data-item-match-workflow]')) this.isDirty = true;
      });
      form?.addEventListener('invalid', (event) => this.showTabForControl(event.target), true);
      content.querySelector('[data-item-editor-kind]')?.addEventListener('change', (event) => {
        void this.updateKindFields(event.currentTarget?.value);
      });
      this.bindTabs(content);
      this.bindMetadataMatchControls(content);
      this.bindArtworkFetchControl(content);
      this.bindCollectionControls(content);
      this.bindPlaybackForceControls(content);
      this.addVisibleFieldLabels(content);
      this.isDirty = false;
    }

    editorTabs(hasPlaybackControls) {
      return ITEM_EDITOR_TABS.filter((tab) => tab.id !== 'playback' || hasPlaybackControls);
    }

    availableTab(value, tabs) {
      const tab = itemEditorTab(value);
      return tabs.some((entry) => entry.id === tab) ? tab : ITEM_EDITOR_DEFAULT_TAB;
    }

    renderTabNavigation(tabs) {
      return `<div class="k-item-editor__tabs" role="tablist" aria-label="Edit sections">${tabs.map((tab) => {
        const active = tab.id === this.activeTab;
        return `<button type="button" class="k-item-editor__tab${active ? ' k-item-editor__tab--active' : ''}" role="tab" id="item-editor-tab-${tab.id}" aria-controls="item-editor-panel-${tab.id}" aria-selected="${active}" tabindex="${active ? '0' : '-1'}" data-item-editor-tab="${tab.id}">${tab.label}</button>`;
      }).join('')}</div>`;
    }

    renderTabPanel(id, content, label) {
      const active = id === this.activeTab;
      return `<section class="k-item-editor__tab-panel" role="tabpanel" id="item-editor-panel-${id}" aria-labelledby="item-editor-tab-${id}" data-item-editor-tab-panel="${id}"${active ? '' : ' hidden'}><h2 class="k-item-editor__panel-heading">${label}</h2>${content}</section>`;
    }

    renderDetailsTab(kind, item) {
      return `<section class="k-item-editor__section"><label class="k-control-shell k-input-shell"><input class="k-input" name="title" value="${escapeHtml(item.title || '')}" aria-label="Title" required></label><label class="k-control-shell k-input-shell"><input class="k-input" name="sortTitle" value="${escapeHtml(item.sort_title || '')}" aria-label="Sort title" required></label><label class="k-control-shell k-textarea-shell"><textarea class="k-textarea" name="overview" aria-label="Overview">${escapeHtml(item.overview || '')}</textarea></label></section><section class="k-item-editor__section"><div class="k-item-editor__grid"><label class="k-control-shell k-input-shell"><input class="k-input" type="date" name="releaseDate" value="${escapeHtml(item.release_date || '')}" aria-label="Release date"></label><label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="1" max="9999" name="releaseYear" value="${item.year || ''}" placeholder="Year" aria-label="Release year"></label></div><label class="k-control-shell k-input-shell"><input class="k-input" name="tags" value="${escapeHtml((item.tags || []).join(', '))}" aria-label="Tags" placeholder="Tags, comma separated"></label></section><section class="k-item-editor__section" data-item-editor-kind-fields>${this.renderKindFields(kind, item)}</section>`;
    }

    renderMatchTab(kind, locks, binding, defaultQuery) {
      const locksSection = `<section class="k-item-editor__section"><div><h3 class="k-item-editor__section-heading">Protect local edits</h3><p class="k-item-editor__muted">Protected fields are preserved when metadata is refreshed or a new match is applied.</p></div><div class="k-item-editor__checks" data-item-editor-locks>${this.renderLockRows(kind, locks)}</div></section>`;
      if (!ITEM_EDITOR_MATCHABLE_KINDS.has(kind)) {
        return `<section class="k-item-editor__match-card"><p>${escapeHtml(ITEM_EDITOR_KIND_LABELS[kind])} items inherit their metadata association from their parent record.</p></section>${locksSection}`;
      }
      const currentTitle = this.renderMetadataEntryTitle(
        binding,
        binding?.title || 'No metadata record selected'
      );
      const currentFacts = binding
        ? `${binding.year || '—'} · ${binding.provider} · ${binding.provider_id}`
        : 'Choose a provider record to apply its unlocked metadata.';
      return `<section class="k-item-editor__match-card"><div class="k-item-editor__match-current"><div><h3 class="k-item-editor__section-heading">Current metadata match</h3>${currentTitle}<small>${escapeHtml(currentFacts)}</small></div><button type="button" class="k-button" data-item-match-change>Change match</button></div><div class="k-item-editor__match-workflow" data-item-match-workflow hidden><div class="k-item-editor__match-input-row"><label class="k-item-editor__match-input"><span>Search database records</span><input class="k-input" value="${escapeHtml(defaultQuery || '')}" aria-label="Search database records" data-item-match-query></label><button type="button" class="k-button" data-item-match-search>Search database</button></div><div class="k-item-editor__match-input-row"><label class="k-item-editor__match-input"><span>Or paste a TMDB link</span><input class="k-input" inputmode="url" placeholder="https://www.themoviedb.org/movie/..." aria-label="Paste a TMDB record link" data-item-match-link></label><button type="button" class="k-button" data-item-match-link-use>Review link match</button></div><p class="k-item-editor__muted">After pasting a link, choose Review link match, then Apply selected match below. Save local edits does not change the metadata association.</p><div class="k-picker__status" data-item-match-status aria-live="polite"></div><div class="k-item-editor__match-results" data-item-match-results></div><section class="k-item-editor__match-confirmation" data-item-match-confirmation hidden></section></div></section>${locksSection}`;
    }

    renderMetadataEntryTitle(entry, fallback) {
      const title = typeof entry?.title === 'string' && entry.title.trim() ? entry.title : fallback;
      const url = providerEntryUrl(entry);
      if (!url) return `<strong class="k-item-editor__match-title">${escapeHtml(title)}</strong>`;
      const provider = providerDisplayName(entry.provider);
      return `<a class="k-item-editor__match-title" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(title)} on ${escapeHtml(provider)}">${escapeHtml(title)}</a>`;
    }

    bindMetadataMatchControls(content) {
      const workflow = content.querySelector('[data-item-match-workflow]');
      const query = content.querySelector('[data-item-match-query]');
      const link = content.querySelector('[data-item-match-link]');
      content.querySelector('[data-item-match-change]')?.addEventListener('click', () => {
        if (!workflow) return;
        workflow.hidden = false;
        query?.focus();
      });
      content.querySelector('[data-item-match-search]')?.addEventListener('click', () => {
        void this.searchMetadata(query?.value);
      });
      query?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        void this.searchMetadata(query.value);
      });
      content.querySelector('[data-item-match-link-use]')?.addEventListener('click', () => {
        this.selectMetadataMatchFromLink(link?.value);
      });
      link?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        this.selectMetadataMatchFromLink(link.value);
      });
    }

    selectMetadataMatchFromLink(value) {
      const content = this.querySelector('[data-item-editor-content]');
      const status = content?.querySelector('[data-item-match-status]');
      if (!status) return;
      const match = this.metadataMatchFromLink(value);
      if (!match) {
        status.textContent = 'Enter a TMDB movie or TV link that matches this item type.';
        return;
      }
      this.selectMetadataMatchResult(match);
      status.textContent = 'Review the link match below, then apply it.';
    }

    metadataMatchFromLink(value) {
      const kind = itemEditorKind(this.currentItem?.kind);
      const reference = tmdbEntryReferenceFromUrl(value, kind);
      if (!reference) return null;
      return {
        ...reference,
        title: `${providerDisplayName(reference.provider)} record ${reference.provider_id}`,
        year: null
      };
    }

    async searchMetadata(rawQuery) {
      const content = this.querySelector('[data-item-editor-content]');
      const status = content?.querySelector('[data-item-match-status]');
      const results = content?.querySelector('[data-item-match-results]');
      const confirmation = content?.querySelector('[data-item-match-confirmation]');
      const query = typeof rawQuery === 'string' ? rawQuery.trim() : '';
      if (!content || !status || !results || !confirmation) return;
      if (!query) {
        status.textContent = 'Enter a title to search.';
        return;
      }
      const source = this.getAttribute('metadata-search-source');
      if (!source) {
        status.textContent = 'Metadata search is unavailable for this item.';
        return;
      }
      const searchButton = content.querySelector('[data-item-match-search]');
      if (searchButton) searchButton.disabled = true;
      this.metadataSearchResults = [];
      this.selectedMetadataMatch = null;
      confirmation.hidden = true;
      confirmation.replaceChildren();
      results.replaceChildren();
      status.textContent = 'Searching database records…';
      try {
        const url = new URL(source, window.location.origin);
        url.searchParams.set('query', query);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.error === 'string' ? payload.error : 'Metadata search failed.');
        if (!Array.isArray(payload.results)) throw new Error('Metadata search response was invalid.');
        this.metadataSearchResults = payload.results.map(normaliseItemEditorMetadataSearchResult).filter(Boolean);
        if (!this.metadataSearchResults.length) {
          status.textContent = 'No matching database records were found.';
          return;
        }
        status.textContent = `${this.metadataSearchResults.length} database records found.`;
        this.renderMetadataSearchResults(results);
      } catch (error) {
        status.textContent = error?.message || 'Metadata search failed.';
      } finally {
        if (searchButton) searchButton.disabled = false;
      }
    }

    renderMetadataSearchResults(container) {
      container.innerHTML = this.metadataSearchResults.map((result, index) => `<article class="k-item-editor__match-result"><div>${this.renderMetadataEntryTitle(result, result.title)}<small>${escapeHtml(`${result.year || '—'} · ${result.kind} · ${result.provider}`)}</small></div><small>${Math.round(result.confidence * 100)}% similarity</small><button type="button" class="k-button" data-item-match-result="${index}">Select</button></article>`).join('');
      container.querySelectorAll('[data-item-match-result]').forEach((button) => {
        button.addEventListener('click', () => this.selectMetadataMatch(Number(button.dataset.itemMatchResult)));
      });
    }

    selectMetadataMatch(index) {
      this.selectMetadataMatchResult(this.metadataSearchResults[index]);
    }

    selectMetadataMatchResult(result) {
      const content = this.querySelector('[data-item-editor-content]');
      const confirmation = content?.querySelector('[data-item-match-confirmation]');
      if (!result || !confirmation) return;
      this.selectedMetadataMatch = result;
      confirmation.hidden = false;
      confirmation.innerHTML = `<h3 class="k-item-editor__section-heading">Apply ${this.renderMetadataEntryTitle(result, result.title)}?</h3><p>${escapeHtml(`${result.year || '—'} · ${result.provider} · ${result.provider_id}`)}</p><p class="k-item-editor__muted">This replaces the current metadata match and updates every field not protected above.</p><button type="button" class="k-button k-button--primary" data-item-match-apply>Apply selected match</button>`;
      confirmation.querySelector('[data-item-match-apply]')?.addEventListener('click', () => {
        void this.applyMetadataMatch();
      });
    }

    async applyMetadataMatch() {
      const match = this.selectedMetadataMatch;
      const content = this.querySelector('[data-item-editor-content]');
      const form = content?.querySelector('[data-item-editor-form]');
      const applyButton = content?.querySelector('[data-item-match-apply]');
      if (!match || !(form instanceof HTMLFormElement) || !this.status || this.isSaving) return;
      if (typeof form.reportValidity === 'function' && !form.reportValidity()) return;
      const source = this.getAttribute('metadata-match-source');
      if (!source) {
        this.status.textContent = 'Metadata reassignment is unavailable for this item.';
        return;
      }
      if (applyButton) applyButton.disabled = true;
      this.isSaving = true;
      try {
        if (this.isDirty) {
          this.status.textContent = 'Saving changes before reassignment…';
          await this.saveItemMetadata(form);
          this.isDirty = false;
        }
        this.status.textContent = 'Applying metadata match…';
        const response = await fetch(source, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({provider: match.provider, providerId: match.provider_id, confirmed: true})
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.error === 'string' ? payload.error : 'Metadata reassignment failed.');
        this.status.textContent = `Matched ${match.title}.`;
        window.setTimeout(() => window.location.reload(), 450);
      } catch (error) {
        const message = error?.message || 'Metadata reassignment could not be applied.';
        this.status.textContent = message;
        publishKanvasToast({
          severity: 'error',
          title: 'Metadata match could not be applied',
          detail: message,
        });
        if (applyButton) applyButton.disabled = false;
        this.isSaving = false;
      }
    }

    renderOrganiseTab(kind, item, collectionControls) {
      return `<section class="k-item-editor__section"><div><h3 class="k-item-editor__section-heading">Library organisation</h3><p class="k-item-editor__muted">Set the item type and its place in the library hierarchy.</p></div><div class="k-item-editor__grid"><label class="k-control-shell k-select-wrap"><select class="k-select" name="kind" aria-label="Kind" data-item-editor-kind>${ITEM_EDITOR_KINDS.map((kindOption) => `<option value="${kindOption}"${kindOption === kind ? ' selected' : ''}>${ITEM_EDITOR_KIND_LABELS[kindOption]}</option>`).join('')}</select></label><span data-item-editor-hierarchy-fields>${this.renderHierarchyFields(kind, item)}</span></div></section>${collectionControls}`;
    }

    renderArtworkTab(artworkRows, kind, binding, showArtworkLabel = true) {
      const canFetchArtwork = (ITEM_EDITOR_MATCHABLE_KINDS.has(kind) && binding)
        || kind === 'season'
        || kind === 'episode';
      const fetchControl = canFetchArtwork
        ? `<div class="k-action-row"><button type="button" class="k-button" data-item-artwork-fetch>Load artwork choices</button></div><div class="k-picker__status" data-item-artwork-status aria-live="polite"></div>`
        : '';
      const labelControl = `<div><h3 class="k-item-editor__section-heading">Artwork label</h3><p class="k-item-editor__muted">Show edition and episode labels, such as Remastered or S01 E03, over artwork.</p></div><label class="k-check"><input type="checkbox" name="showArtworkLabel"${showArtworkLabel ? ' checked' : ''}> Show label on artwork</label>`;
      return `<section class="k-item-editor__section">${labelControl}${fetchControl}<div class="k-item-editor__artwork-grid" data-item-editor-artwork-grid>${artworkRows}</div></section>`;
    }

    bindArtworkFetchControl(content) {
      const button = content.querySelector('[data-item-artwork-fetch]');
      const status = content.querySelector('[data-item-artwork-status]');
      button?.addEventListener('click', async () => {
        const source = this.getAttribute('artwork-fetch-source');
        if (!source || !status || this.isSaving) {
          if (status && !source) status.textContent = 'Artwork fetch is unavailable for this item.';
          return;
        }
        button.disabled = true;
        status.textContent = 'Loading artwork choices from the current match…';
        try {
          const response = await fetch(source, {
            method: 'POST',
            headers: {'Accept': 'application/json'},
            credentials: 'same-origin'
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(typeof payload.error === 'string' ? payload.error : 'Artwork fetch failed.');
          if (!Array.isArray(payload.artwork)) throw new Error('Artwork fetch response was invalid.');
          const grid = content.querySelector('[data-item-editor-artwork-grid]');
          const form = content.querySelector('[data-item-editor-form]');
          if (!grid || !(form instanceof HTMLFormElement) || !this.currentItem) {
            throw new Error('Artwork could not be displayed.');
          }
          const selected = this.selectedArtworkFromForm(form);
          this.currentItem.artwork = payload.artwork;
          const artworkKinds = [...new Set(payload.artwork.map((artwork) => artwork.kind))];
          grid.innerHTML = this.renderArtworkRows(payload.artwork, artworkKinds, selected);
          status.textContent = payload.artwork.length
            ? `${payload.artwork.length} artwork choice${payload.artwork.length === 1 ? '' : 's'} loaded. Choose one below, then save local edits.`
            : 'No artwork was available for the current metadata match.';
        } catch (error) {
          status.textContent = error?.message || 'Artwork could not be fetched.';
        } finally {
          button.disabled = false;
        }
      });
    }

    renderHistoryTab(auditRows) {
      return `<section class="k-item-editor__section"><p class="k-item-editor__muted">Changes made in this editor are recorded here.</p><ul class="k-item-editor__audit">${auditRows}</ul></section>`;
    }

    bindTabs(content) {
      const buttons = Array.from(content.querySelectorAll('[data-item-editor-tab]'));
      buttons.forEach((button) => {
        button.addEventListener('click', () => this.setActiveTab(button.dataset.itemEditorTab));
        button.addEventListener('keydown', (event) => {
          const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
          if (!keys.includes(event.key)) return;
          event.preventDefault();
          const currentIndex = buttons.indexOf(button);
          const nextIndex = event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? buttons.length - 1
              : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
          this.setActiveTab(buttons[nextIndex]?.dataset.itemEditorTab, true);
        });
      });
    }

    setActiveTab(value, focus = false) {
      const content = this.querySelector('[data-item-editor-content]');
      if (!content) return;
      const tabs = this.editorTabs(Boolean(content.querySelector('[data-item-editor-tab="playback"]')));
      const activeTab = this.availableTab(value, tabs);
      this.activeTab = activeTab;
      let activeButton = null;
      content.querySelectorAll('[data-item-editor-tab]').forEach((button) => {
        const active = button.dataset.itemEditorTab === activeTab;
        button.classList.toggle('k-item-editor__tab--active', active);
        button.setAttribute('aria-selected', String(active));
        button.tabIndex = active ? 0 : -1;
        if (active) activeButton = button;
      });
      content.querySelectorAll('[data-item-editor-tab-panel]').forEach((panel) => {
        panel.hidden = panel.dataset.itemEditorTabPanel !== activeTab;
      });
      if (focus) activeButton?.focus();
    }

    showTabForControl(control) {
      const panel = control?.closest?.('[data-item-editor-tab-panel]');
      if (panel?.dataset.itemEditorTabPanel) this.setActiveTab(panel.dataset.itemEditorTabPanel);
    }

    addVisibleFieldLabels(content) {
      const labels = {
        title: 'Title',
        sortTitle: 'Sort title',
        overview: 'Overview',
        releaseDate: 'Release date',
        releaseYear: 'Release year',
        tags: 'Tags',
        seasonNumber: 'Season number',
        episodeNumber: 'Episode number',
        kind: 'Item type',
        defaultAudioStreamIndex: 'Default audio',
        defaultSubtitleTrackId: 'Default subtitles',
        defaultSubtitleTimingOffsetMilliseconds: 'Subtitle timing',
        defaultSubtitleFontScalePercent: 'Subtitle size',
      };
      Object.entries(labels).forEach(([name, label]) => {
        const control = content.querySelector(`[name="${name}"]`);
        const shell = control?.closest('.k-control-shell');
        if (!shell || shell.querySelector('.k-item-editor__field-label')) return;
        const fieldLabel = document.createElement('span');
        fieldLabel.className = 'k-item-editor__field-label';
        fieldLabel.textContent = label;
        shell.classList.add('k-item-editor__field');
        shell.prepend(fieldLabel);
      });
    }

    renderCollectionControls(item, collectionChoices, collectionRelationships) {
      const isCollection = (collection) => collection
        && Number.isSafeInteger(collection.id)
        && collection.id > 0
        && typeof collection.name === 'string'
        && collection.name.trim()
        && Number.isSafeInteger(collection.revision)
        && collection.revision > 0;
      const included = (Array.isArray(item.collections) ? item.collections : []).filter(isCollection);
      const choices = collectionChoices.filter(isCollection);
      const relationships = collectionRelationships.filter((relationship) => (
        typeof relationship === 'string' && relationship
      ));
      const memberRows = included.length
        ? included.map((collection) => `<div class="k-member-editor-row"><a href="/collections/${collection.id}" class="k-member-editor-row__title">${escapeHtml(collection.name)}</a>${collection.relationship ? `<span class="k-member-editor-row__relationship">${escapeHtml(String(collection.relationship).replaceAll('_', ' '))}</span>` : ''}<button type="button" class="k-button" data-item-collection-remove="${collection.id}" data-item-collection-revision="${collection.revision}">Remove</button></div>`).join('')
        : '<p class="k-item-editor__muted">This item is not in a collection.</p>';
      const relationshipOptions = ['<option value="">No relationship</option>']
        .concat(relationships.map((relationship) => `<option value="${escapeHtml(relationship)}">${escapeHtml(relationship.replaceAll('_', ' '))}</option>`))
        .join('');
      const addControl = choices.length
        ? `<div class="k-item-editor__grid"><label class="k-control-shell k-select-wrap"><select class="k-select" aria-label="Add to collection" data-item-collection-target>${choices.map((collection) => `<option value="${collection.id}:${collection.revision}">${escapeHtml(collection.name)}</option>`).join('')}</select></label><label class="k-control-shell k-select-wrap"><select class="k-select" aria-label="Collection relationship" data-item-collection-relationship>${relationshipOptions}</select></label><button type="button" class="k-button" data-item-collection-add>Add to collection</button></div>`
        : '<p class="k-item-editor__muted">No other collections are available.</p>';
      return `<section class="k-item-editor__section"><div><h3 class="k-item-editor__section-heading">Collections</h3><p class="k-item-editor__muted">Place this item in one or more collections.</p></div>${memberRows}${addControl}</section>`;
    }

    renderPlaybackDefaults(item) {
      const defaults = item.playback_defaults && typeof item.playback_defaults === 'object'
        ? item.playback_defaults
        : {};
      const audioStreams = Array.isArray(item.playback_audio_streams)
        ? item.playback_audio_streams
        : [];
      const subtitleTracks = Array.isArray(item.playback_subtitle_tracks)
        ? item.playback_subtitle_tracks
        : [];
      if (!audioStreams.length && !subtitleTracks.length) return '';
      const audioOptions = audioStreams.map((stream, index) => {
        const label = [stream.language, stream.title, stream.codec].filter(Boolean).join(' · ') || `Audio ${index + 1}`;
        return `<option value="${index}"${defaults.audio_stream_index === index ? ' selected' : ''}>${escapeHtml(label)}</option>`;
      }).join('');
      const subtitleOptions = subtitleTracks.map((track) => {
        const label = [track.language, track.title, track.codec].filter(Boolean).join(' · ') || 'Subtitle';
        return `<option value="${escapeHtml(track.id)}"${defaults.subtitle_track_id === track.id ? ' selected' : ''}>${escapeHtml(label)}</option>`;
      }).join('');
      const timing = defaults.subtitle_timing_offset_milliseconds ?? '';
      const fontScale = defaults.subtitle_font_scale_percent ?? '';
      return `<section class="k-item-editor__section"><p class="k-item-editor__muted">Profile defaults take precedence unless the matching item control is forced. Playback-session choices remain changeable.</p><div class="k-item-editor__playback-option"><label class="k-control-shell k-select-wrap"><select class="k-select" name="defaultAudioStreamIndex" aria-label="Default audio track"><option value="">Automatic audio</option>${audioOptions}</select></label><label class="k-check"><input type="checkbox" name="forceDefaultAudioStream"${defaults.force_audio_stream ? ' checked' : ''}> Force this audio track</label></div><div class="k-item-editor__playback-option"><label class="k-control-shell k-select-wrap"><select class="k-select" name="defaultSubtitleTrackId" aria-label="Default subtitle track"><option value="">Automatic subtitles</option>${subtitleOptions}</select></label><label class="k-check"><input type="checkbox" name="forceDefaultSubtitleTrack"${defaults.force_subtitle_track ? ' checked' : ''}> Force this subtitle track</label></div><label class="k-control-shell k-input-shell"><input class="k-input" type="number" min="-30000" max="30000" step="100" name="defaultSubtitleTimingOffsetMilliseconds" value="${timing}" aria-label="Default subtitle timing offset in milliseconds" placeholder="Timing offset in milliseconds"></label><div class="k-item-editor__playback-option"><label class="k-control-shell k-select-wrap"><select class="k-select" name="defaultSubtitleFontScalePercent" aria-label="Default subtitle font size"><option value="">Use profile default</option>${[75, 100, 125, 150, 175, 200].map((value) => `<option value="${value}"${fontScale === value ? ' selected' : ''}>${value}%</option>`).join('')}</select></label><label class="k-check"><input type="checkbox" name="forceDefaultSubtitleFontScale"${defaults.force_subtitle_font_scale ? ' checked' : ''}> Force this subtitle font size</label></div></section>`;
    }

    bindCollectionControls(content) {
      content.querySelectorAll('[data-item-collection-remove]').forEach((button) => {
        button.addEventListener('click', async () => {
          const collectionId = Number(button.dataset.itemCollectionRemove);
          const revision = Number(button.dataset.itemCollectionRevision);
          if (!Number.isSafeInteger(collectionId) || collectionId <= 0 || !Number.isSafeInteger(revision) || revision <= 0) return;
          const action = this.collectionActionSource();
          if (!action) return;
          await this.mutateCollection(
            `${action}/${collectionId}/remove`,
            {revision: String(revision)}
          );
        });
      });
      content.querySelector('[data-item-collection-add]')?.addEventListener('click', async () => {
        const target = content.querySelector('[data-item-collection-target]');
        const relationship = content.querySelector('[data-item-collection-relationship]');
        if (!(target instanceof HTMLSelectElement) || !(relationship instanceof HTMLSelectElement)) return;
        if (!/^([1-9]\d*):([1-9]\d*)$/.test(target.value)) return;
        await this.mutateCollection(this.collectionActionSource(), {
          collection_target: target.value,
          relationship: relationship.value
        });
      });
    }

    collectionActionSource() {
      const action = this.getAttribute('action-source');
      return action ? `${action}/collections` : '';
    }

    async mutateCollection(action, values) {
      if (!action || !this.status) return;
      this.status.textContent = 'Saving collection membership…';
      try {
        const response = await fetch(action, {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          credentials: 'same-origin',
          body: new URLSearchParams(values)
        });
        if (!response.ok) throw new Error('Collection membership could not be saved.');
        window.location.reload();
      } catch (error) {
        const message = error?.message || 'Collection membership could not be saved.';
        this.status.textContent = message;
        publishKanvasToast({
          severity: 'error',
          title: 'Collection membership could not be saved',
          detail: message,
        });
      }
    }

    renderKindFields(kind, item, values = new Map()) {
      const fields = itemEditorNumberFields(kind);
      if (!fields.length) return '';
      return `<div class="k-item-editor__grid">${fields.map((field) => {
        const value = values.has(field.name)
          ? values.get(field.name)
          : itemEditorItemValue(item, field.value);
        return `<label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="0" name="${field.name}" value="${escapeHtml(String(value ?? ''))}" placeholder="${field.placeholder}" aria-label="${field.label}"></label>`;
      }).join('')}</div>`;
    }

    renderHierarchyFields(kind, item, parentId, parentChoices = this.parentChoices) {
      if (!ITEM_EDITOR_PARENT_KINDS.has(kind)) return '<span class="k-item-editor__muted">Top-level item</span>';
      const value = parentId === undefined ? item.parent_id : parentId;
      const selectedId = value == null ? '' : String(value);
      const choices = Array.isArray(parentChoices) ? parentChoices : [];
      const options = choices.map((choice) => {
        const context = choice.kind === 'season' && choice.season_number != null
          ? `Season ${choice.season_number}`
          : ITEM_EDITOR_KIND_LABELS[choice.kind];
        return `<option value="${choice.id}"${String(choice.id) === selectedId ? ' selected' : ''}>${escapeHtml(choice.title)} · ${escapeHtml(context)}</option>`;
      }).join('');
      const emptyOption = choices.length
        ? '<option value="">Select a parent</option>'
        : '<option value="">No eligible parents are available</option>';
      return `<label class="k-item-editor__field"><span class="k-item-editor__field-label">Parent</span><span class="k-control-shell k-select-wrap"><select class="k-select" name="parentId" aria-label="Parent item" required>${emptyOption}${options}</select></span></label>`;
    }

    renderLockRows(kind, locks) {
      return itemEditorRelevantLocks(kind).map((field) => `<label class="k-check"><input type="checkbox" name="lock" value="${field.value}"${locks.has(field.value) ? ' checked' : ''}> ${field.label}</label>`).join('');
    }

    renderArtworkRows(artworks, artworkKinds, selected) {
      if (!artworks.length) return '<p class="k-quiet-copy">No cached artwork is available to select.</p>';
      return artworkKinds.map((kind) => {
        const choicesForKind = artworks.filter((artwork) => artwork.kind === kind);
        const primary = choicesForKind.find((artwork) => artwork.is_primary) || choicesForKind[0];
        const kindLabel = kind === 'still' ? 'Episode still' : kind === 'poster' ? 'Poster' : 'Artwork';
        const artworkUrl = (artwork) => typeof artwork?.url === 'string'
          ? artwork.url.replace(/^\/api\/v1\/library\/items\/(\d+)\/artwork\/(\d+)$/, '/kanvas/artwork/$1/$2')
          : null;
        const image = (artwork) => {
          const url = artworkUrl(artwork);
          const shapeClass = artwork?.kind === 'still'
            ? ' k-item-editor__artwork-image--landscape'
            : '';
          return url && localArtworkUrl(url)
            ? `<img class="k-item-editor__artwork-image${shapeClass}" src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async">`
            : `<span class="k-item-editor__artwork-placeholder${shapeClass}" aria-hidden="true"></span>`;
        };
        const details = (artwork) => {
          const values = [];
          if (typeof artwork.language === 'string' && artwork.language.trim()) values.push(artwork.language.toUpperCase());
          if (Number.isSafeInteger(artwork.width) && Number.isSafeInteger(artwork.height)) values.push(`${artwork.width} × ${artwork.height}`);
          if (typeof artwork.vote_average === 'number' && Number.isFinite(artwork.vote_average) && Number.isSafeInteger(artwork.vote_count) && artwork.vote_count > 0) values.push(`${artwork.vote_average.toFixed(1)} · ${artwork.vote_count} votes`);
          return values.length ? values.join(' · ') : kindLabel;
        };
        const automatic = `<label class="k-item-editor__artwork"><input type="radio" name="artwork-${escapeHtml(kind)}" value="" data-artwork-kind="${escapeHtml(kind)}"${selected.has(kind) ? '' : ' checked'}><span class="k-item-editor__artwork-card">${image(primary)}<span class="k-item-editor__artwork-title">Automatic</span><small>Provider default</small></span></label>`;
        const choices = choicesForKind.map((artwork) => `<label class="k-item-editor__artwork"><input type="radio" name="artwork-${escapeHtml(artwork.kind)}" value="${artwork.id}" data-artwork-kind="${escapeHtml(artwork.kind)}"${selected.get(artwork.kind) === artwork.id ? ' checked' : ''}><span class="k-item-editor__artwork-card">${image(artwork)}<span class="k-item-editor__artwork-title">${artwork.is_primary ? 'Provider default' : kindLabel}</span><small>${escapeHtml(details(artwork))}</small></span></label>`).join('');
        return `<fieldset class="k-item-editor__artwork-group"><legend>${escapeHtml(kindLabel)}</legend>${automatic}${choices}</fieldset>`;
      }).join('');
    }

    selectedArtworkFromForm(form) {
      return new Map(
        Array.from(form.querySelectorAll('[data-artwork-kind]:checked'))
          .filter((input) => input.value)
          .map((input) => [input.dataset.artworkKind, Number(input.value)])
      );
    }

    async updateKindFields(value) {
      const kind = itemEditorKind(value);
      const content = this.querySelector('[data-item-editor-content]');
      const item = this.currentItem || {};
      const fields = content?.querySelector('[data-item-editor-kind-fields]');
      const locks = content?.querySelector('[data-item-editor-locks]');
      const hierarchy = content?.querySelector('[data-item-editor-hierarchy-fields]');
      const fieldValues = new Map(
        Array.from(content?.querySelectorAll('[data-item-editor-kind-fields] input[name]') || [])
          .filter((input) => typeof input.name === 'string' && typeof input.value === 'string')
          .map((input) => [input.name, input.value])
      );
      const parentInput = content?.querySelector('[data-item-editor-hierarchy-fields] [name="parentId"]');
      const parentId = parentInput && typeof parentInput.value === 'string'
        ? parentInput.value
        : undefined;
      this.lockedMetadataFields = this.lockedMetadataFieldsFromForm(content);
      if (fields) fields.innerHTML = this.renderKindFields(kind, item, fieldValues);
      if (locks) locks.innerHTML = this.renderLockRows(kind, this.lockedMetadataFields);
      this.addVisibleFieldLabels(content);
      const load = ++this.parentChoiceLoad;
      const saveButton = content.querySelector('button[type="submit"]');
      if (!hierarchy) return;
      if (!ITEM_EDITOR_PARENT_KINDS.has(kind)) {
        this.parentChoices = [];
        hierarchy.innerHTML = this.renderHierarchyFields(kind, item, parentId);
        if (saveButton) saveButton.disabled = false;
        return;
      }
      if (saveButton) saveButton.disabled = true;
      hierarchy.innerHTML = '<span class="k-item-editor__muted">Loading eligible parents…</span>';
      try {
        const choices = await this.loadParentChoices(kind);
        if (load !== this.parentChoiceLoad) return;
        this.parentChoices = choices;
        hierarchy.innerHTML = this.renderHierarchyFields(kind, item, parentId);
        if (saveButton) saveButton.disabled = false;
      } catch (_) {
        if (load !== this.parentChoiceLoad) return;
        this.parentChoices = [];
        hierarchy.innerHTML = `${this.renderHierarchyFields(kind, item, parentId)}<span class="k-item-editor__muted">Eligible parents could not be loaded.</span>`;
        if (saveButton) saveButton.disabled = false;
      }
    }

    async loadParentChoices(kind) {
      const source = this.getAttribute('parent-choices-source');
      if (!source) throw new Error('Missing parent choices source');
      const url = new URL(source, window.location.origin);
      url.searchParams.set('kind', kind);
      const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
      if (!response.ok) throw new Error('Parent choices request failed');
      const payload = await response.json();
      if (!Array.isArray(payload.parentChoices)) throw new Error('Parent choices response was invalid');
      return payload.parentChoices.map(normaliseItemEditorParentChoice).filter(Boolean);
    }

    bindPlaybackForceControls(content) {
      const pairs = [
        ['defaultAudioStreamIndex', 'forceDefaultAudioStream'],
        ['defaultSubtitleTrackId', 'forceDefaultSubtitleTrack'],
        ['defaultSubtitleFontScalePercent', 'forceDefaultSubtitleFontScale'],
      ];
      pairs.forEach(([choiceName, forceName]) => {
        const choice = content.querySelector(`[name="${choiceName}"]`);
        const force = content.querySelector(`[name="${forceName}"]`);
        const forceControl = force?.closest('.k-check');
        if (!(choice instanceof HTMLSelectElement) || !(force instanceof HTMLInputElement) || !forceControl) return;
        const sync = () => {
          const visible = choice.value !== '';
          forceControl.hidden = !visible;
          force.disabled = !visible;
          if (!visible) force.checked = false;
        };
        choice.addEventListener('change', sync);
        sync();
      });
    }

    lockedMetadataFieldsFromForm(form) {
      const locks = new Set(this.lockedMetadataFields);
      form?.querySelectorAll('input[name="lock"]').forEach((input) => {
        if (typeof input.value !== 'string' || typeof input.checked !== 'boolean') return;
        if (input.checked) locks.add(input.value);
        else locks.delete(input.value);
      });
      return locks;
    }

    async submit(event) {
      event.preventDefault();
      const form = event.currentTarget;
      if (!(form instanceof HTMLFormElement) || !this.status) return;
      const button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      this.isSaving = true;
      this.status.textContent = 'Saving metadata…';
      try {
        const result = await this.saveItemMetadata(form);
        this.status.textContent = `Saved ${result.audit?.changed_fields?.join(', ') || 'metadata'}.`;
        window.setTimeout(() => window.location.reload(), 450);
      } catch (error) {
        const message = error?.message || 'Item edit could not be applied.';
        this.status.textContent = message;
        publishKanvasToast({
          severity: 'error',
          title: 'Item could not be saved',
          detail: message,
        });
        if (button) button.disabled = false;
        this.isSaving = false;
      }
    }

    async saveItemMetadata(form) {
      const source = this.getAttribute('action-source');
      if (!source) throw new Error('Missing item edit action');
      const payload = this.payloadFromForm(form, new FormData(form));
      const response = await fetch(source, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify(payload)
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof result.error === 'string' ? result.error : 'Item edit failed');
      }
      return result;
    }

    payloadFromForm(form, values) {
      const toNullableNumber = (name) => {
        if (!values.has(name)) return undefined;
        const raw = String(values.get(name) || '').trim();
        return raw ? Number(raw) : null;
      };
      const selectedArtwork = Array.from(form.querySelectorAll('[data-artwork-kind]:checked'))
        .filter((input) => input.value)
        .map((input) => ({kind: input.dataset.artworkKind, artworkId: Number(input.value)}));
      const visibleArtworkKinds = new Set(Array.from(form.querySelectorAll('[data-artwork-kind]')).map((input) => input.dataset.artworkKind));
      for (const [kind, artworkId] of this.initialSelectedArtwork.entries()) {
        if (!visibleArtworkKinds.has(kind)) selectedArtwork.push({kind, artworkId});
      }
      const lockedMetadataFields = Array.from(this.lockedMetadataFieldsFromForm(form));
      const payload = {
        title: String(values.get('title') || ''),
        sortTitle: String(values.get('sortTitle') || ''),
        overview: String(values.get('overview') || '').trim() || null,
        releaseDate: String(values.get('releaseDate') || '').trim() || null,
        releaseYear: toNullableNumber('releaseYear'),
        tags: String(values.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean),
        lockedMetadataFields,
        kind: String(values.get('kind') || ''),
        showArtworkLabel: values.has('showArtworkLabel')
      };
      const kind = itemEditorKind(payload.kind);
      const current = this.currentItem || {};
      const numberNames = new Set(itemEditorNumberFields(kind).map((field) => field.name));
      if (values.has('seasonNumber')) payload.seasonNumber = toNullableNumber('seasonNumber');
      else if (!numberNames.has('seasonNumber') && current.season_number !== null && current.season_number !== undefined) payload.seasonNumber = null;
      if (values.has('episodeNumber')) payload.episodeNumber = toNullableNumber('episodeNumber');
      else if (!numberNames.has('episodeNumber') && current.episode_number !== null && current.episode_number !== undefined) payload.episodeNumber = null;
      if (values.has('parentId')) payload.parentId = toNullableNumber('parentId');
      else if (!ITEM_EDITOR_PARENT_KINDS.has(kind)) payload.parentId = null;
      if (values.has('defaultAudioStreamIndex')) {
        payload.defaultAudioStreamIndex = toNullableNumber('defaultAudioStreamIndex');
        payload.forceDefaultAudioStream = (
          payload.defaultAudioStreamIndex !== null && values.has('forceDefaultAudioStream')
        );
      }
      if (values.has('defaultSubtitleTrackId')) {
        payload.defaultSubtitleTrackId = String(values.get('defaultSubtitleTrackId') || '') || null;
        payload.forceDefaultSubtitleTrack = (
          payload.defaultSubtitleTrackId !== null && values.has('forceDefaultSubtitleTrack')
        );
      }
      if (values.has('defaultSubtitleTimingOffsetMilliseconds')) {
        payload.defaultSubtitleTimingOffsetMilliseconds = toNullableNumber(
          'defaultSubtitleTimingOffsetMilliseconds'
        );
      }
      if (values.has('defaultSubtitleFontScalePercent')) {
        payload.defaultSubtitleFontScalePercent = toNullableNumber(
          'defaultSubtitleFontScalePercent'
        );
        payload.forceDefaultSubtitleFontScale = (
          payload.defaultSubtitleFontScalePercent !== null
          && values.has('forceDefaultSubtitleFontScale')
        );
      }
      if (visibleArtworkKinds.size) payload.selectedArtwork = selectedArtwork;
      return payload;
    }
  }

  if (!customElements.get('kanvas-item-editor')) customElements.define('kanvas-item-editor', KanvasItemEditor);

})();
