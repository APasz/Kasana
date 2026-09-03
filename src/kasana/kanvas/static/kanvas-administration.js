(() => {
  'use strict';

  const {escapeHtml, jobDetail, providerEntryUrl} = window.kanvasInternals;
  const adminDate = (value) => {
    if (typeof value !== 'string') return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? '—' : parsed.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});
  };
  const lastScanLabel = (value) => typeof value === 'string' ? adminDate(value) : 'Not scanned';
  const adminBytes = (value) => {
    if (!Number.isFinite(value)) return '—';
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  };
  const plural = (count, singular, pluralLabel = `${singular}s`) => `${count} ${count === 1 ? singular : pluralLabel}`;
  const adminOperationLabel = (operation) => ({
    scan: 'Library scan',
    'library-consistency': 'Consistency check',
    'artwork-fetch': 'Artwork fetch',
    'hierarchy-repair': 'Structural repair',
    'duplicate-resolve': 'Duplicate merge',
    'duplicate-resolve-batch': 'Duplicate merge',
    'duplicate-resolution': 'Duplicate merge',
    'root-create': 'Library root saved',
    'root-update': 'Library root saved',
    'root-delete': 'Library root removed',
    'cancel-job': 'Job cancellation',
    match: 'Metadata match',
    reject: 'Candidate rejected',
    ignore: 'Metadata item ignored',
    refresh: 'Metadata search',
  }[operation] || 'Administration action');
  const jobProgress = (job) => {
    const total = Number.isInteger(job?.progressTotal) ? job.progressTotal : null;
    const current = Number.isInteger(job?.progressCurrent) ? job.progressCurrent : 0;
    if (total !== null && total > 0) return `${current}/${total} ${job.progressUnit || 'items'}`;
    if (current > 0) return `${current} ${job.progressUnit || 'items'}`;
    return '';
  };
  const hierarchyActionLabel = (kind) => ({
    create: 'Create',
    merge: 'Merge',
    rename: 'Rename',
    reparent: 'Move',
    reassign_media: 'Reassign media',
    retype: 'Change type',
    remove_empty: 'Remove empty record',
  }[kind] || 'Other change');
  const jobAnchorId = (jobId) => `job-${encodeURIComponent(String(jobId))}`;
  const isActiveJob = (job) => job?.status === 'queued' || job?.status === 'running';
  const isProblemJob = (job) => job?.status === 'failed' || job?.status === 'interrupted';
  const currentHashTarget = () => String(window.location.hash || '').slice(1);

  class KanvasAdministration extends HTMLElement {
    constructor() {
      super();
      this.section = 'overview';
      this.subsection = null;
      this.overview = null;
      this.hierarchy = null;
      this.duplicates = null;
      this.selectedDuplicatePairs = new Set();
      this.jobs = [];
      this.roots = [];
      this.reviewItems = [];
      this.reviewIndex = 0;
      this.candidateIndex = 0;
      this.cursor = null;
      this.submittedJobId = null;
      this.activity = null;
      this.reviewedItemCount = 0;
      this.lastMatchedItemId = null;
      this.manualSearchOpen = false;
      this.manualSearchQuery = '';
      this.manualSearchResults = [];
      this.manualSearchStatus = '';
      this.manualSelection = null;
      this.inFlight = false;
      this.timer = null;
      this.abort = null;
      this.manualAbort = null;
      this.onVisibility = () => this.visibilityChanged();
      this.onKeyDown = (event) => this.keyDown(event);
    }

    connectedCallback() {
      this.section = this.getAttribute('data-section') || 'overview';
      this.subsection = this.getAttribute('data-subsection') || null;
      document.addEventListener('visibilitychange', this.onVisibility);
      document.addEventListener('keydown', this.onKeyDown);
      this.load();
    }

    disconnectedCallback() {
      document.removeEventListener('visibilitychange', this.onVisibility);
      document.removeEventListener('keydown', this.onKeyDown);
      window.clearTimeout(this.timer);
      this.abort?.abort();
      this.manualAbort?.abort();
    }

    source(name) { return this.getAttribute(name); }

    hasOpenDialog() {
      return this.querySelector('dialog[open]') instanceof HTMLDialogElement;
    }

    async fetchJson(source, suffix = '') {
      if (!source) throw new Error('Missing administration source');
      const response = await fetch(`${source}${suffix}`, {
        headers: {'Accept': 'application/json'},
        credentials: 'same-origin',
        signal: this.abort?.signal,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error || `Administration request failed (${response.status})`);
      return payload;
    }

    async postJson(source, payload) {
      const response = await fetch(source, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });
      const responsePayload = await response.json();
      if (!response.ok) {
        const message = responsePayload?.error || 'Action could not be applied.';
        const requestId = typeof responsePayload?.requestId === 'string' ? responsePayload.requestId : null;
        throw new Error(requestId ? `${message} (Katalog request ID: ${requestId})` : message);
      }
      return responsePayload;
    }

    async load() {
      if (this.inFlight || document.visibilityState === 'hidden') return;
      if (this.hasOpenDialog()) {
        this.schedule();
        return;
      }
      this.inFlight = true;
      this.abort?.abort();
      this.abort = new AbortController();
      this.renderLoading();
      try {
        let jobPage = null;
        if (this.section === 'overview') {
          this.overview = await this.fetchJson(this.source('overview-source'));
        } else if (this.section === 'libraries') {
          const [roots, hierarchy, duplicates] = await Promise.all([
            this.fetchJson(this.source('roots-source')),
            this.fetchJson(this.source('hierarchy-source')),
            this.fetchJson(this.source('duplicates-source')),
          ]);
          this.roots = Array.isArray(roots.items) ? roots.items : [];
          this.hierarchy = hierarchy;
          this.duplicates = duplicates;
        } else if (this.section === 'metadata') {
          if (this.subsection === 'artwork') {
            this.overview = await this.fetchJson(this.source('overview-source'));
          } else {
            const page = await this.fetchJson(this.source('metadata-source'));
            this.reviewItems = Array.isArray(page.items) ? page.items : [];
            this.cursor = typeof page.nextCursor === 'string' ? page.nextCursor : null;
            this.reviewIndex = Math.min(this.reviewIndex, Math.max(0, this.reviewItems.length - 1));
          }
        } else if (this.section === 'jobs') {
          const page = await this.fetchJson(this.source('jobs-source'));
          this.jobs = Array.isArray(page.items) ? page.items : [];
          this.cursor = typeof page.nextCursor === 'string' ? page.nextCursor : null;
          jobPage = page;
        }
        await this.checkSubmittedJob(jobPage);
        if (!this.hasOpenDialog()) this.render();
      } catch (error) {
        if (error?.name !== 'AbortError') this.renderError(error);
      } finally {
        this.inFlight = false;
        this.schedule();
      }
    }

    schedule() {
      window.clearTimeout(this.timer);
      if (document.visibilityState === 'hidden') return;
      const hasActiveJobs = Number(this.overview?.activeJobCount || 0) > 0
        || this.jobs.some(isActiveJob);
      const interval = this.submittedJobId ? 2000 : hasActiveJobs ? 5000 : 30000;
      this.timer = window.setTimeout(() => this.load(), interval);
    }

    visibilityChanged() {
      if (document.visibilityState === 'hidden') {
        window.clearTimeout(this.timer);
        this.abort?.abort();
      } else {
        this.load();
      }
    }

    renderLoading() {
      if (!this.children.length) {
        this.innerHTML = '<div class="k-admin-status" aria-live="polite">Loading administration…</div>';
      }
    }

    renderError(error) {
      const detail = typeof error?.message === 'string' && error.message
        ? ` ${escapeHtml(error.message)}`
        : '';
      this.innerHTML = `<div class="k-admin-status k-admin-status--error" aria-live="assertive">Could not load this area.${detail} <button type="button" class="k-button" data-admin-retry>Retry</button></div>`;
      this.querySelector('[data-admin-retry]')?.addEventListener('click', () => this.load());
    }

    render() {
      if (this.section === 'libraries') this.renderLibraries();
      else if (this.section === 'metadata') {
        if (this.subsection === 'artwork') this.renderArtworkMaintenance();
        else this.renderMetadata();
      } else if (this.section === 'jobs') this.renderJobs();
      else this.renderOverview();
      this.renderActivity();
    }

    renderActivity() {
      if (!this.activity?.message) return;
      const status = document.createElement('div');
      status.className = `k-admin-status k-admin-status--${this.activity.state || 'active'} k-admin-activity`;
      status.setAttribute('aria-live', this.activity.state === 'error' ? 'assertive' : 'polite');
      status.textContent = this.activity.message;
      (this.querySelector('section') || this).prepend(status);
    }

    sectionHeader(title, detail = '', actions = '') {
      return `<header class="k-admin-section-header"><div><h2>${escapeHtml(title)}</h2>${detail ? `<small>${escapeHtml(detail)}</small>` : ''}</div>${actions ? `<div class="k-row-actions">${actions}</div>` : ''}</header>`;
    }

    statusRow(label, value, action = '', destination = '') {
      const button = action
        ? `<a class="k-button" href="${escapeHtml(destination)}">${escapeHtml(action)}</a>`
        : '';
      return `<div class="k-admin-row"><span>${escapeHtml(label)}</span><span class="k-admin-row__value">${escapeHtml(String(value))}</span>${button}</div>`;
    }

    renderOverview() {
      const data = this.overview;
      if (!data) return this.renderError();
      const unavailableProviders = Array.isArray(data.providers)
        ? data.providers.filter((provider) => provider.configured && !provider.available)
        : [];
      const attention = [];
      const coreNeedsAttention = !data.connected || !data.databaseHealthy;
      if (coreNeedsAttention) {
        attention.push({label: 'Core service', value: data.connected ? 'Database needs attention' : 'Katalog unavailable', action: '', href: ''});
      }
      if (data.unavailableRootCount) {
        attention.push({label: 'Library roots', value: plural(data.unavailableRootCount, 'root', 'roots') + ' unavailable', action: 'Open libraries', href: '/administration/libraries'});
      }
      if (data.unresolvedMetadataCount) {
        attention.push({label: 'Metadata', value: plural(data.unresolvedMetadataCount, 'item', 'items') + ' unresolved', action: 'Review', href: '/administration/metadata'});
      }
      const problemJobs = Number(data.failedJobCount || 0) + Number(data.interruptedJobCount || 0);
      if (problemJobs) {
        attention.push({label: 'Jobs', value: plural(problemJobs, 'job', 'jobs') + ' need attention', action: 'Inspect', href: '/administration/jobs#problem-jobs'});
      }
      if (unavailableProviders.length) {
        attention.push({label: 'Metadata providers', value: plural(unavailableProviders.length, 'provider', 'providers') + ' unavailable', action: 'Review', href: '/administration/metadata'});
      }
      const health = data.connected && data.databaseHealthy ? 'Core services healthy' : 'Core service needs attention';
      const next = coreNeedsAttention
        ? {value: data.connected ? 'Restore database health' : 'Restore Katalog connectivity', action: '', href: ''}
        : attention.find((entry) => entry.action) || (
          data.enabledRootCount
            ? {value: 'Run a fresh library scan', action: 'Scan libraries', href: ''}
            : {value: 'Add your first library root', action: 'Add root', href: '/administration/libraries'}
        );
      const nextAction = !next.action
        ? ''
        : next.href
          ? `<a class="k-button k-button--primary" href="${escapeHtml(next.href)}">${escapeHtml(next.action)}</a>`
          : `<button type="button" class="k-button k-button--primary" data-admin-operation="scan">${escapeHtml(next.action)}</button>`;
      const providerRows = unavailableProviders.map((provider) => this.statusRow(
        provider.name,
        provider.detail || 'Unavailable',
      )).join('');
      this.innerHTML = `<section class="k-admin-panel k-admin-overview" aria-live="polite">
        ${this.statusRow('Health', health)}
        <section class="k-admin-overview__group" aria-label="Needs attention">
          ${this.sectionHeader('Needs attention')}
          ${attention.length ? attention.map((entry) => this.statusRow(entry.label, entry.value, entry.action, entry.href)).join('') : '<div class="k-admin-status">Nothing needs action.</div>'}
        </section>
        <div class="k-admin-row k-admin-row--next"><span>Next</span><span class="k-admin-row__value">${escapeHtml(next.value)}</span>${nextAction}</div>
        <details class="k-admin-details"><summary>System details</summary>
          <div class="k-admin-details__content">
            ${this.statusRow('Last successful scan', adminDate(data.lastSuccessfulScanAt))}
            ${this.statusRow('Database revision', data.databaseRevision || 'Unavailable')}
            ${this.statusRow('Artwork cache', `${adminBytes(data.artworkCacheSizeBytes)} · ${data.artworkCacheFileCount || 0} files`, 'Maintain', '/administration/metadata/artwork')}
            ${providerRows || this.statusRow('Metadata providers', 'Available')}
          </div>
        </details>
      </section>`;
      this.bindActions();
    }

    rootRow(root) {
      const name = root.displayName || `Root ${root.id}`;
      const state = root.enabled ? (root.available ? 'Ready' : 'Unavailable') : 'Disabled';
      const stateClass = root.enabled && root.available
        ? ' k-admin-state--ready'
        : root.enabled
          ? ' k-admin-state--attention'
          : '';
      const tags = Array.isArray(root.tags) && root.tags.length ? root.tags.map(escapeHtml).join(', ') : 'No tags';
      const itemCount = Number(root.itemCount || 0);
      return `<article class="k-root-row" data-root-id="${Number(root.id)}">
        <div><strong>${escapeHtml(name)}</strong><small>${escapeHtml(root.kind)} library · ${itemCount} items · ${Number(root.mediaFileCount || 0)} files</small></div>
        <div><span class="k-admin-state${stateClass}">${state}</span><small>Last scan ${lastScanLabel(root.lastScanCompletedAt)}</small><details class="k-admin-row__details"><summary>Settings</summary><div><code>${escapeHtml(root.path || 'Path unavailable')}</code><span>${tags}</span><span>Audio ${escapeHtml(root.preferredAudioLanguage || 'stream default')} · Subtitles ${escapeHtml(root.preferredSubtitleLanguage || 'stream default')}</span></div></details></div>
        <div class="k-row-actions"><button type="button" class="k-button k-button--primary" data-admin-operation="scan" data-root-id="${Number(root.id)}">Scan</button><button type="button" class="k-button" data-admin-root-edit="${Number(root.id)}">Edit</button><details class="k-admin-danger-menu"><summary>Remove</summary><button type="button" class="k-button k-button--danger" data-admin-root-delete="${Number(root.id)}">Remove root</button></details></div>
      </article>`;
    }

    renderLibraries() {
      const structureOpen = this.subsection === 'hierarchy' || Boolean(this.querySelector('[data-admin-library-structure]')?.open);
      const duplicatesOpen = this.subsection === 'duplicates' || Boolean(this.querySelector('[data-admin-library-duplicates]')?.open);
      const roots = this.roots.map((root) => this.rootRow(root)).join('');
      const actions = '<button type="button" class="k-button k-button--primary" data-admin-root-add>Add root</button><button type="button" class="k-button" data-admin-operation="scan">Scan all</button>';
      this.innerHTML = `<section class="k-admin-workspace k-admin-libraries" aria-live="polite">
        ${this.sectionHeader('Library roots', 'State, last scan, and scan controls', actions)}
        <section class="k-admin-list">${roots || '<div class="k-admin-status">No library roots. Add one to begin scanning.</div>'}</section>
        ${this.renderHierarchySection(structureOpen)}
        ${this.renderDuplicatesSection(duplicatesOpen)}
        <details class="k-admin-maintenance"><summary>More maintenance</summary><div><span>Queue a non-destructive consistency check.</span><button type="button" class="k-button" data-admin-operation="library-consistency" data-admin-dry-run="true">Check consistency</button></div></details>
      </section><dialog class="k-kanvas-dialog" data-admin-root-dialog></dialog>`;
      this.bindActions();
    }

    renderHierarchySection(open) {
      const data = this.hierarchy;
      if (!data || !Array.isArray(data.actions) || !Array.isArray(data.manual_reviews)) {
        return '<section class="k-admin-issue-panel"><div class="k-admin-status">Structural issues could not be loaded.</div></section>';
      }
      const impact = data.impact || {};
      const actionGroups = new Map();
      data.actions.forEach((action) => {
        const kind = typeof action.kind === 'string' ? action.kind : 'other';
        const entries = actionGroups.get(kind) || [];
        entries.push(action);
        actionGroups.set(kind, entries);
      });
      const actionGroupsMarkup = [...actionGroups.entries()].map(([kind, entries]) => {
        const rows = entries.map((action) => {
          const itemId = Number(action.item_id);
          const itemLabel = typeof action.item_label === 'string' ? action.item_label : itemId ? `Item ${itemId}` : 'New catalogue record';
          const source = itemId ? `<a href="/item/${itemId}">${escapeHtml(itemLabel)}</a>` : escapeHtml(itemLabel);
          const targetId = Number(action.target_item_id);
          const targetLabel = typeof action.target_label === 'string' ? action.target_label : null;
          const target = targetLabel ? (targetId ? `<a href="/item/${targetId}">${escapeHtml(targetLabel)}</a>` : escapeHtml(targetLabel)) : '';
          return `<li class="k-hierarchy-action"><div><strong>${source}</strong>${target ? ` <span aria-hidden="true">→</span> ${target}` : ''}</div><small>${escapeHtml(action.explanation || 'No explanation provided.')}</small></li>`;
        }).join('');
        return `<details class="k-hierarchy-group"><summary>${escapeHtml(hierarchyActionLabel(kind))} · ${entries.length}</summary><ol class="k-admin-detail-list">${rows}</ol></details>`;
      }).join('');
      const reviews = data.manual_reviews.map((review) => {
        const itemId = Number(review.item_id);
        const itemLabel = typeof review.item_label === 'string' ? review.item_label : itemId ? `Item ${itemId}` : null;
        const item = itemLabel ? (itemId ? `<a href="/item/${itemId}">${escapeHtml(itemLabel)}</a> · ` : `${escapeHtml(itemLabel)} · `) : '';
        return `<li>${item}${escapeHtml(review.reason || 'Manual review required.')}</li>`;
      }).join('');
      const summary = `${plural(data.actions.length, 'planned change')} · ${plural(data.manual_reviews.length, 'manual review')}`;
      return `<details class="k-admin-issue-panel" data-admin-library-structure id="library-structure"${open ? ' open' : ''}>
        <summary><span>Structural issues</span><small>${summary}</small></summary>
        <div class="k-admin-issue-panel__content">
          ${this.sectionHeader('Structural issues', summary, `<button type="button" class="k-button k-button--repair" data-admin-hierarchy-apply${data.actions.length ? '' : ' disabled'}>Apply repair</button>`)}
          ${data.actions.length ? `<details><summary>Planned changes (${data.actions.length})</summary><section class="k-hierarchy-groups">${actionGroupsMarkup}</section></details>` : '<div class="k-admin-status">No automatic repairs are ready.</div>'}
          <details><summary>Manual review (${data.manual_reviews.length})</summary><ul class="k-admin-detail-list">${reviews || '<li>No manual review is needed.</li>'}</ul></details>
          <details><summary>Affected library state</summary><div class="k-admin-impact">${Number(impact.playback_states || 0)} playback · ${Number(impact.metadata_bindings || 0)} metadata · ${Number(impact.collection_memberships || 0)} collections · ${Number(impact.watch_order_entries || 0)} watch-order entries</div></details>
        </div>
      </details>`;
    }

    renderDuplicatesSection(open) {
      const candidates = Array.isArray(this.duplicates?.candidates) ? this.duplicates.candidates : [];
      const fileIssues = Array.isArray(this.duplicates?.fileIssues) ? this.duplicates.fileIssues : [];
      const candidateKeys = new Set(candidates.map((candidate) => `${Number(candidate.source_item_id)}:${Number(candidate.target_item_id)}`));
      this.selectedDuplicatePairs = new Set([...this.selectedDuplicatePairs].filter((key) => candidateKeys.has(key)));
      const rows = candidates.map((candidate) => {
        const key = `${Number(candidate.source_item_id)}:${Number(candidate.target_item_id)}`;
        const source = `${candidate.source_title || 'Untitled'}${candidate.source_year ? ` (${candidate.source_year})` : ''}`;
        const target = `${candidate.target_title || 'Untitled'}${candidate.target_year ? ` (${candidate.target_year})` : ''}`;
        const impact = candidate.impact || {};
        const references = `${Number(impact.playback_states || 0)} playback · ${Number(impact.metadata_bindings || 0)} metadata · ${Number(impact.collection_memberships || 0)} collections · ${Number(impact.watch_order_entries || 0)} watch-order entries`;
        return `<article class="k-root-row k-duplicate-row"><div><span class="k-admin-row__eyebrow">Duplicate record</span><strong><a href="/item/${Number(candidate.source_item_id)}">${escapeHtml(source)}</a></strong></div><div><span class="k-admin-row__eyebrow">Keep</span><strong><a href="/item/${Number(candidate.target_item_id)}">${escapeHtml(target)}</a></strong><small>${escapeHtml(candidate.provider || 'provider')} ${escapeHtml(candidate.provider_id || '')}</small><details class="k-admin-row__details"><summary>Transferred state</summary><div>${escapeHtml(references)}</div></details></div><div class="k-row-actions"><label class="k-check"><input type="checkbox" data-admin-duplicate-select="${escapeHtml(key)}"${this.selectedDuplicatePairs.has(key) ? ' checked' : ''}> Select</label><button type="button" class="k-button k-button--danger" data-admin-duplicate-resolve data-admin-duplicate-source="${Number(candidate.source_item_id)}" data-admin-duplicate-target="${Number(candidate.target_item_id)}">Merge</button></div></article>`;
      }).join('');
      const fileRows = fileIssues.map((issue) => `<article class="k-root-row k-duplicate-file-row"><div><strong>Duplicate episode file</strong><small>${escapeHtml(issue.path || '')}</small></div><div><small>${escapeHtml(issue.message || 'This file was not catalogued.')}</small></div></article>`).join('');
      const selectedCount = this.selectedDuplicatePairs.size;
      const mergeSelected = candidates.length > 1
        ? `<button type="button" class="k-button k-button--danger" data-admin-duplicates-merge${selectedCount ? '' : ' disabled'}>Merge selected (${selectedCount})</button>`
        : '';
      const summary = `${plural(candidates.length, 'record duplicate')} · ${plural(fileIssues.length, 'file issue')}`;
      return `<details class="k-admin-issue-panel" data-admin-library-duplicates id="library-duplicates"${open ? ' open' : ''}>
        <summary><span>Duplicate issues</span><small>${summary}</small></summary>
        <div class="k-admin-issue-panel__content">
          ${this.sectionHeader('Duplicate records', candidates.length ? 'Merge only the reviewed record pairs' : 'No record duplicates are ready to merge', mergeSelected)}
          <section class="k-admin-list">${rows || '<div class="k-admin-status">No record duplicates need action.</div>'}</section>
          <section class="k-admin-file-issues">
            ${this.sectionHeader('Duplicate episode files', fileIssues.length ? 'Resolve the file, then scan again' : 'No duplicate episode files')}
            ${fileIssues.length ? `<div class="k-action-row"><button type="button" class="k-button" data-admin-operation="scan">Scan after file changes</button></div><section class="k-admin-list">${fileRows}</section>` : ''}
          </section>
        </div>
      </details>`;
    }

    renderArtworkMaintenance() {
      const data = this.overview;
      if (!data) return this.renderError();
      this.innerHTML = `<section class="k-admin-workspace" aria-live="polite">
        ${this.sectionHeader('Artwork maintenance', 'Fetch missing artwork for matched items', '<a class="k-button" href="/administration/metadata">Metadata review</a>')}
        <section class="k-admin-panel"><div class="k-admin-row"><span>Artwork cache</span><span class="k-admin-row__value">${adminBytes(data.artworkCacheSizeBytes)} · ${data.artworkCacheFileCount || 0} files</span></div><div class="k-admin-action-row"><div><strong>Missing artwork</strong><small>Queues a background fetch for matched library items.</small></div><button type="button" class="k-button k-button--primary" data-admin-operation="artwork-fetch">Fetch missing artwork</button></div></section>
      </section>`;
      this.bindActions();
    }

    metadataArtworkActions() {
      const selection = Number.isInteger(this.lastMatchedItemId)
        ? `<a class="k-button" href="/item/${this.lastMatchedItemId}?edit=artwork">Select artwork</a>`
        : '';
      return `${selection}<a class="k-button" href="/administration/metadata/artwork">Artwork maintenance</a>`;
    }

    renderMetadata() {
      const item = this.reviewItems[this.reviewIndex];
      if (!item) {
        const reviewed = this.reviewedItemCount ? ` ${plural(this.reviewedItemCount, 'item')} reviewed this session.` : '';
        const continuation = this.cursor
          ? '<button type="button" class="k-button" data-admin-more-review>Continue review</button>'
          : '';
        const message = this.cursor
          ? `This page is clear.${reviewed} More items are ready to review.`
          : `Metadata review is clear.${reviewed}`;
        this.innerHTML = `<section class="k-admin-status" aria-live="polite">${message} ${continuation} ${this.metadataArtworkActions()}</section>`;
        this.bindActions();
        return;
      }
      const candidates = Array.isArray(item.candidates) ? item.candidates : [];
      this.candidateIndex = Math.min(this.candidateIndex, Math.max(0, candidates.length - 1));
      const candidate = candidates[this.candidateIndex];
      const reviewPosition = `${this.reviewIndex + 1} of ${this.reviewItems.length}`;
      const candidateRows = candidates.map((entry, index) => `<button type="button" class="k-metadata-candidate${index === this.candidateIndex ? ' k-metadata-candidate--selected' : ''}" aria-pressed="${index === this.candidateIndex}" data-admin-candidate="${index}"><span>${escapeHtml(entry.title)}</span><small>${escapeHtml(entry.provider)} · ${entry.year || '—'} · ${Math.round(Number(entry.confidence || 0) * 100)}%</small><span class="k-progress-edge"><span style="--k-progress:${Math.round(Number(entry.confidence || 0) * 100)}%"></span></span></button>`).join('');
      const selectedUrl = providerEntryUrl(candidate);
      const selectedTitle = candidate
        ? selectedUrl
          ? `<a class="k-metadata-selected__title" href="${escapeHtml(selectedUrl)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(candidate.title)} on ${escapeHtml(candidate.provider)}">${escapeHtml(candidate.title)}</a>`
          : `<strong>${escapeHtml(candidate.title)}</strong>`
        : '';
      const selected = candidate
        ? `<div class="k-metadata-selected">${selectedTitle}<small>${escapeHtml(candidate.provider)} · ${candidate.year || '—'} · ${escapeHtml(candidate.kind)} · ${Math.round(Number(candidate.confidence || 0) * 100)}%</small></div>`
        : '<div class="k-admin-status">No suggested matches. Find another match or ignore this item.</div>';
      const matchDisabled = candidate ? '' : ' disabled';
      this.innerHTML = `<section class="k-admin-workspace" aria-live="polite">
        ${this.sectionHeader('Metadata review', `${reviewPosition}${this.reviewedItemCount ? ` · ${plural(this.reviewedItemCount, 'item')} reviewed` : ''}`, this.metadataArtworkActions())}
        <section class="k-metadata-review"><div class="k-metadata-local"><span class="k-metadata-panel__heading">Unmatched item</span><div class="k-metadata-local__body">${item.posterUrl ? `<img src="${escapeHtml(item.posterUrl)}" alt="">` : '<span class="k-metadata-poster">?</span>'}<div><strong>${escapeHtml(item.title)}</strong><small>${item.year || '—'} · ${escapeHtml(item.kind)}</small><a class="k-button" href="/item/${Number(item.itemId)}">Open item</a></div></div></div><div class="k-metadata-candidates"><div class="k-metadata-panel__heading"><span>Suggested matches</span><small>${plural(candidates.length, 'candidate')}</small></div><div class="k-metadata-candidate-list">${candidateRows || '<div class="k-admin-status">No suggestions yet.</div>'}</div></div><div class="k-metadata-actions"><div><span class="k-metadata-panel__heading">Selected match</span>${selected}</div><div class="k-metadata-decision"><span class="k-metadata-panel__heading">Review</span><div class="k-action-row"><button type="button" class="k-button k-button--primary" data-admin-metadata="match"${matchDisabled}>Match</button><button type="button" class="k-button" data-admin-metadata="reject"${matchDisabled}>Not this match</button><button type="button" class="k-button" data-admin-metadata="ignore">Ignore item</button></div></div>${this.renderManualMatch(item)}<div class="k-metadata-navigation"><span>Review ${reviewPosition}</span><div class="k-action-row"><button type="button" class="k-button" data-admin-review-nav="previous"${this.reviewIndex ? '' : ' disabled'}>Previous</button><button type="button" class="k-button" data-admin-review-nav="next"${this.reviewIndex < this.reviewItems.length - 1 ? '' : ' disabled'}>Next</button></div></div></div></section>
      </section>`;
      this.bindActions();
    }

    renderManualMatch(item) {
      if (!this.manualSearchOpen) {
        return '<div class="k-metadata-manual"><button type="button" class="k-button" data-admin-manual-toggle>Find another match</button><button type="button" class="k-button" data-admin-metadata="refresh">Search providers again</button></div>';
      }
      const results = this.manualSearchResults.map((entry, index) => {
        const selected = index === this.manualSelection;
        const title = typeof entry.title === 'string' ? entry.title : 'Untitled';
        const provider = typeof entry.provider === 'string' ? entry.provider : 'provider';
        return `<button type="button" class="k-metadata-candidate${selected ? ' k-metadata-candidate--selected' : ''}" aria-pressed="${selected}" data-admin-manual-select="${index}"><span>${escapeHtml(title)}</span><small>${escapeHtml(provider)} · ${entry.year || '—'} · ${Math.round(Number(entry.confidence || 0) * 100)}%</small></button>`;
      }).join('');
      const selected = this.manualSelection === null ? null : this.manualSearchResults[this.manualSelection];
      const selection = selected
        ? `<div class="k-admin-manual-confirmation"><span>Use <strong>${escapeHtml(selected.title || 'this record')}</strong> for ${escapeHtml(item.title)}?</span><div class="k-action-row"><button type="button" class="k-button k-button--primary" data-admin-manual-apply>Apply match</button><button type="button" class="k-button" data-admin-manual-clear>Choose another</button></div></div>`
        : '';
      return `<section class="k-metadata-manual"><div class="k-metadata-panel__heading"><span>Manual match</span><button type="button" class="k-button" data-admin-manual-close>Close</button></div><form class="k-admin-manual-search" data-admin-manual-search><label class="k-control-shell k-input-shell"><input class="k-input" name="query" value="${escapeHtml(this.manualSearchQuery || item.title || '')}" aria-label="Search metadata records" required></label><button type="submit" class="k-button">Search</button></form><div class="k-picker__status" aria-live="polite">${escapeHtml(this.manualSearchStatus)}</div><div class="k-metadata-candidate-list">${results}</div>${selection}</section>`;
    }

    renderJob(job) {
      const total = Number.isInteger(job.progressTotal) ? job.progressTotal : null;
      const current = Number.isInteger(job.progressCurrent) ? job.progressCurrent : 0;
      const percent = total && total > 0 ? Math.min(100, Math.round((current / total) * 100)) : null;
      const progress = total === null ? (job.phase ? `${current} ${job.progressUnit || ''}` : '—') : `${current}/${total} ${job.progressUnit || ''}`;
      const counters = Array.isArray(job.counters) ? job.counters.map(([key, value]) => `${key}: ${value}`).join(' · ') : '';
      const jobId = String(job.id || 'unknown');
      const anchorId = jobAnchorId(jobId);
      const targeted = currentHashTarget() === anchorId;
      const failure = job.failure || (isProblemJob(job) ? job.message : '');
      const details = `<details class="k-job-row__details"${targeted ? ' open' : ''}><summary>Details</summary><div>${escapeHtml(jobDetail(job, counters) || 'No additional job details.')}<br>${escapeHtml(`Submitted ${adminDate(job.submittedAt)}${job.startedAt ? ` · Started ${adminDate(job.startedAt)}` : ''}${job.completedAt ? ` · Finished ${adminDate(job.completedAt)}` : ''}`)}${failure ? `<br><strong>${escapeHtml(failure)}</strong>` : ''}</div></details>`;
      return `<article id="${escapeHtml(anchorId)}" class="k-job-row${targeted ? ' k-job-row--target' : ''}" tabindex="-1" data-job-id="${escapeHtml(jobId)}"><div><strong>${escapeHtml(adminOperationLabel(job.kind))}</strong><small>${escapeHtml(job.status)}${job.phase ? ` · ${escapeHtml(job.phase)}` : ''}</small>${failure ? `<small class="k-job-row__failure">${escapeHtml(failure)}</small>` : ''}</div><div class="k-job-row__progress">${percent === null ? '<span class="k-progress-edge k-progress-edge--unknown"></span>' : `<span class="k-progress-edge"><span style="--k-progress:${percent}%"></span></span>`}<small>${escapeHtml(progress)}</small></div><div>${details}</div>${job.cancellable ? `<button type="button" class="k-button" data-admin-cancel="${escapeHtml(jobId)}">Cancel</button>` : ''}</article>`;
    }

    renderJobs() {
      const active = this.jobs.filter(isActiveJob);
      const problems = this.jobs.filter(isProblemJob);
      const completed = this.jobs.filter((job) => job.status === 'completed');
      const cancelled = this.jobs.filter((job) => job.status === 'cancelled');
      const target = currentHashTarget();
      const completedOpen = target === 'completed-jobs' || completed.some((job) => jobAnchorId(job.id) === target);
      const cancelledOpen = cancelled.some((job) => jobAnchorId(job.id) === target);
      const primaryRows = [...active, ...problems].map((job) => this.renderJob(job)).join('');
      this.innerHTML = `<section class="k-admin-workspace k-admin-jobs" aria-live="polite">
        ${this.sectionHeader('Jobs', 'Active and problem jobs first')}
        <section class="k-admin-list" id="problem-jobs">${primaryRows || '<div class="k-admin-status">No active or problem jobs.</div>'}</section>
        <details class="k-admin-job-history" id="completed-jobs"${completedOpen ? ' open' : ''}><summary>Completed history (${completed.length})</summary><section class="k-admin-list">${completed.map((job) => this.renderJob(job)).join('') || '<div class="k-admin-status">No completed jobs yet.</div>'}</section></details>
        ${cancelled.length ? `<details class="k-admin-job-history"${cancelledOpen ? ' open' : ''}><summary>Cancelled jobs (${cancelled.length})</summary><section class="k-admin-list">${cancelled.map((job) => this.renderJob(job)).join('')}</section></details>` : ''}
        ${this.cursor ? '<button type="button" class="k-button" data-admin-more>Load earlier jobs</button>' : ''}
      </section>`;
      this.bindActions();
    }

    bindActions() {
      this.querySelectorAll('[data-admin-operation]').forEach((button) => button.addEventListener('click', () => {
        const extra = {rootId: button.dataset.rootId ? Number(button.dataset.rootId) : null};
        if (button.dataset.adminDryRun === 'true') extra.dryRun = true;
        this.operation(button.dataset.adminOperation, extra);
      }));
      this.querySelector('[data-admin-hierarchy-apply]')?.addEventListener('click', () => {
        if (window.confirm('Apply this structural repair? A database backup is created first. Media files are unchanged.')) {
          this.operation('hierarchy-repair', {apply: true, confirmed: true});
        }
      });
      this.querySelectorAll('[data-admin-duplicate-resolve]').forEach((button) => button.addEventListener('click', () => {
        const sourceItemId = Number(button.dataset.adminDuplicateSource);
        const targetItemId = Number(button.dataset.adminDuplicateTarget);
        if (!Number.isInteger(sourceItemId) || !Number.isInteger(targetItemId)) return;
        if (window.confirm('Merge this duplicate record? Its library state moves to the kept record, then the duplicate record is deleted. A database backup is created first. Media files are unchanged.')) {
          this.operation('duplicate-resolve', {sourceItemId, targetItemId, confirmed: true});
        }
      }));
      this.querySelectorAll('[data-admin-duplicate-select]').forEach((input) => input.addEventListener('change', () => {
        const key = input.dataset.adminDuplicateSelect;
        if (!key) return;
        if (input.checked) this.selectedDuplicatePairs.add(key);
        else this.selectedDuplicatePairs.delete(key);
        this.renderLibraries();
      }));
      this.querySelector('[data-admin-duplicates-merge]')?.addEventListener('click', () => {
        const resolutions = [...this.selectedDuplicatePairs].map((key) => {
          const [source_item_id, target_item_id] = key.split(':').map(Number);
          return {source_item_id, target_item_id};
        });
        if (!resolutions.length) return;
        if (window.confirm(`Merge ${resolutions.length} selected duplicate records? Their library state moves to the kept records, then the duplicates are deleted. One database backup is created first. Media files are unchanged.`)) {
          this.operation('duplicate-resolve-batch', {resolutions, confirmed: true});
        }
      });
      this.querySelectorAll('[data-admin-cancel]').forEach((button) => button.addEventListener('click', () => {
        if (window.confirm('Cancel this job? Work already completed will be kept.')) this.operation('cancel-job', {jobId: button.dataset.adminCancel});
      }));
      this.querySelector('[data-admin-more]')?.addEventListener('click', () => this.moreJobs());
      this.querySelector('[data-admin-more-review]')?.addEventListener('click', () => this.moreReviewItems());
      this.querySelectorAll('[data-admin-candidate]').forEach((button) => button.addEventListener('click', () => {
        this.candidateIndex = Number(button.dataset.adminCandidate);
        this.renderMetadata();
      }));
      this.querySelectorAll('[data-admin-metadata]').forEach((button) => button.addEventListener('click', () => this.metadataAction(button.dataset.adminMetadata)));
      this.querySelectorAll('[data-admin-review-nav]').forEach((button) => button.addEventListener('click', () => this.moveReview(button.dataset.adminReviewNav === 'next' ? 1 : -1)));
      this.querySelector('[data-admin-manual-toggle]')?.addEventListener('click', () => {
        this.manualSearchOpen = true;
        this.manualSearchQuery = this.reviewItems[this.reviewIndex]?.title || '';
        this.renderMetadata();
      });
      this.querySelector('[data-admin-manual-close]')?.addEventListener('click', () => this.closeManualMatch());
      this.querySelector('[data-admin-manual-clear]')?.addEventListener('click', () => {
        this.manualSelection = null;
        this.renderMetadata();
      });
      this.querySelector('[data-admin-manual-search]')?.addEventListener('submit', (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        this.searchManualMatches(String(form.get('query') || ''));
      });
      this.querySelectorAll('[data-admin-manual-select]').forEach((button) => button.addEventListener('click', () => {
        this.manualSelection = Number(button.dataset.adminManualSelect);
        this.renderMetadata();
      }));
      this.querySelector('[data-admin-manual-apply]')?.addEventListener('click', () => this.applyManualMatch());
      this.querySelector('[data-admin-root-add]')?.addEventListener('click', () => this.rootDialog(null));
      this.querySelectorAll('[data-admin-root-edit]').forEach((button) => button.addEventListener('click', () => this.rootDialog(this.roots.find((root) => root.id === Number(button.dataset.adminRootEdit)) || null)));
      this.querySelectorAll('[data-admin-root-delete]').forEach((button) => button.addEventListener('click', () => {
        const root = this.roots.find((entry) => entry.id === Number(button.dataset.adminRootDelete));
        if (!root) return;
        const name = root.displayName || `Root ${root.id}`;
        const itemCount = Number(root.itemCount || 0);
        const itemScope = itemCount ? ` and ${plural(itemCount, 'catalogued item')}` : '';
        if (window.confirm(`Remove ${name}? This deletes the root configuration${itemScope}. Media files are unchanged.`)) {
          this.operation('root-delete', {rootId: root.id, confirm: true});
        }
      }));
    }

    async operation(operation, extra = {}, refresh = true) {
      const source = this.getAttribute('action-source');
      if (!source) return false;
      this.activity = {state: 'active', message: `${adminOperationLabel(operation)} starting…`};
      this.render();
      try {
        const payload = await this.postJson(source, {operation, ...extra});
        if (typeof payload.job?.id === 'string' && payload.job.id) {
          this.submittedJobId = payload.job.id;
          this.activity = {state: 'active', message: `${adminOperationLabel(operation)} queued.`};
        } else {
          this.activity = {state: 'complete', message: `${adminOperationLabel(operation)} completed.`};
        }
        if (refresh) this.load();
        else this.render();
        return true;
      } catch (error) {
        this.activity = {state: 'error', message: error?.message || 'Action could not be applied.'};
        this.render();
        return false;
      }
    }

    async checkSubmittedJob(jobPage = null) {
      if (!this.submittedJobId) return;
      const page = jobPage || await this.fetchJson(this.source('jobs-source'));
      const jobs = Array.isArray(page.items) ? page.items : [];
      const job = jobs.find((entry) => entry?.id === this.submittedJobId);
      if (!job) {
        this.activity = {state: 'active', message: 'Action submitted. Waiting for Katalog to report progress.'};
        return;
      }
      const label = adminOperationLabel(job.kind);
      const phase = typeof job.phase === 'string' && job.phase ? ` · ${job.phase}` : '';
      const progress = jobProgress(job);
      if (isProblemJob(job)) {
        this.activity = {state: 'error', message: job.failure || job.message || `${label} ${job.status}. Opening job details.`};
        window.location.assign(`/administration/jobs#${jobAnchorId(this.submittedJobId)}`);
        return;
      }
      if (job.status === 'completed') {
        this.activity = {state: 'complete', message: job.message || `${label} completed.`};
        this.submittedJobId = null;
        return;
      }
      if (job.status === 'cancelled') {
        this.activity = {state: 'cancelled', message: job.message || `${label} was cancelled.`};
        this.submittedJobId = null;
        return;
      }
      this.activity = {state: 'active', message: `${label} ${job.status || 'in progress'}${phase}${progress ? ` · ${progress}` : ''}`};
    }

    resetManualMatch() {
      this.manualAbort?.abort();
      this.manualSearchOpen = false;
      this.manualSearchQuery = '';
      this.manualSearchResults = [];
      this.manualSearchStatus = '';
      this.manualSelection = null;
    }

    closeManualMatch() {
      this.resetManualMatch();
      this.renderMetadata();
    }

    completeReviewItem() {
      this.reviewItems.splice(this.reviewIndex, 1);
      this.reviewedItemCount += 1;
      this.reviewIndex = Math.min(this.reviewIndex, Math.max(0, this.reviewItems.length - 1));
      this.candidateIndex = 0;
      this.resetManualMatch();
    }

    async metadataAction(action) {
      const item = this.reviewItems[this.reviewIndex];
      const candidate = item?.candidates?.[this.candidateIndex];
      if (!item || ((action === 'match' || action === 'reject') && !candidate)) return;
      const payload = {itemId: item.itemId, ...(candidate ? {provider: candidate.provider, providerId: candidate.providerId} : {})};
      const succeeded = await this.operation(action, payload, action === 'refresh');
      if (!succeeded) return;
      if (action === 'refresh') return;
      if (action === 'match' || action === 'ignore') {
        if (action === 'match' && Number.isInteger(Number(item.itemId))) {
          this.lastMatchedItemId = Number(item.itemId);
        }
        this.completeReviewItem();
        this.render();
        return;
      }
      if (action === 'reject') {
        item.candidates.splice(this.candidateIndex, 1);
        this.candidateIndex = Math.min(this.candidateIndex, Math.max(0, item.candidates.length - 1));
        this.render();
        return;
      }
    }

    async searchManualMatches(query) {
      const item = this.reviewItems[this.reviewIndex];
      const search = query.trim();
      if (!item || !search) return;
      this.manualSearchQuery = search;
      this.manualSearchStatus = 'Searching metadata records…';
      this.manualSearchResults = [];
      this.manualSelection = null;
      this.renderMetadata();
      this.manualAbort?.abort();
      this.manualAbort = new AbortController();
      try {
        const response = await fetch(`/kanvas/data/items/${Number(item.itemId)}/metadata-search?query=${encodeURIComponent(search)}`, {
          headers: {'Accept': 'application/json'},
          credentials: 'same-origin',
          signal: this.manualAbort.signal,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload?.error || 'Metadata search could not be completed.');
        this.manualSearchResults = Array.isArray(payload.results) ? payload.results : [];
        this.manualSearchStatus = this.manualSearchResults.length ? '' : 'No matches found. Try another title.';
      } catch (error) {
        if (error?.name === 'AbortError') return;
        this.manualSearchStatus = error?.message || 'Metadata search could not be completed.';
      }
      this.renderMetadata();
    }

    async applyManualMatch() {
      const item = this.reviewItems[this.reviewIndex];
      const candidate = this.manualSelection === null ? null : this.manualSearchResults[this.manualSelection];
      const providerId = candidate?.providerId || candidate?.provider_id;
      if (!item || !candidate || typeof candidate.provider !== 'string' || typeof providerId !== 'string') return;
      if (!window.confirm(`Use ${candidate.title || 'this record'} as the metadata match for ${item.title}? Unlocked local metadata may be updated.`)) return;
      this.activity = {state: 'active', message: 'Applying manual metadata match…'};
      this.render();
      try {
        await this.postJson(`/kanvas/actions/items/${Number(item.itemId)}/metadata-match`, {
          provider: candidate.provider,
          providerId,
          confirmed: true,
        });
        this.activity = {state: 'complete', message: 'Manual metadata match completed.'};
        this.lastMatchedItemId = Number(item.itemId);
        this.completeReviewItem();
        this.render();
      } catch (error) {
        this.activity = {state: 'error', message: error?.message || 'Manual metadata match could not be applied.'};
        this.render();
      }
    }

    moveCandidate(offset) {
      const candidates = this.reviewItems[this.reviewIndex]?.candidates || [];
      if (!candidates.length) return;
      this.candidateIndex = Math.min(Math.max(0, this.candidateIndex + offset), candidates.length - 1);
      this.renderMetadata();
    }

    moveReview(offset) {
      if (!this.reviewItems.length) return;
      this.reviewIndex = Math.min(Math.max(0, this.reviewIndex + offset), this.reviewItems.length - 1);
      this.candidateIndex = 0;
      this.resetManualMatch();
      this.renderMetadata();
    }

    async loadMore(source, appendItems, errorMessage) {
      if (!this.cursor || this.inFlight) return;
      this.inFlight = true;
      try {
        const page = await this.fetchJson(source, `?cursor=${encodeURIComponent(this.cursor)}`);
        appendItems(Array.isArray(page.items) ? page.items : []);
        this.cursor = typeof page.nextCursor === 'string' ? page.nextCursor : null;
        this.render();
      } catch (error) {
        if (error?.name !== 'AbortError') {
          this.activity = {state: 'error', message: error?.message || errorMessage};
          this.render();
        }
      } finally {
        this.inFlight = false;
      }
    }

    moreJobs() {
      return this.loadMore(
        this.source('jobs-source'),
        (items) => this.jobs.push(...items),
        'Earlier jobs could not be loaded.'
      );
    }

    moreReviewItems() {
      return this.loadMore(
        this.source('metadata-source'),
        (items) => this.reviewItems.push(...items),
        'More metadata items could not be loaded.'
      );
    }

    rootDialog(root) {
      const dialog = this.querySelector('[data-admin-root-dialog]');
      if (!(dialog instanceof HTMLDialogElement)) return;
      dialog.innerHTML = `<form method="dialog" class="k-picker k-admin-root-form" data-admin-root-form><div class="k-picker__header"><strong>${root ? 'Edit library root' : 'Add library root'}</strong></div><label class="k-control-shell k-input-shell"><input class="k-input" name="displayName" value="${escapeHtml(root?.displayName || '')}" placeholder="Name" aria-label="Root name"></label><div class="k-admin-root-path-row"><label class="k-control-shell k-input-shell"><input class="k-input" name="path" value="${escapeHtml(root?.path || '')}" placeholder="Path" aria-label="Root path" data-admin-root-path required></label><button type="button" class="k-button" data-admin-root-browse>Browse</button></div><div class="k-directory-picker" data-admin-directory-picker hidden></div><label class="k-control-shell k-select-wrap"><select class="k-select" name="kind" aria-label="Root kind"><option value="movie"${root?.kind === 'movie' ? ' selected' : ''}>Movie</option><option value="series"${root?.kind === 'series' ? ' selected' : ''}>Series</option></select></label><label class="k-control-shell k-input-shell"><input class="k-input" name="tags" value="${escapeHtml((root?.tags || []).join(', '))}" placeholder="Tags" aria-label="Root tags"></label><label class="k-control-shell k-input-shell"><input class="k-input" name="preferredAudioLanguage" value="${escapeHtml(root?.preferredAudioLanguage || '')}" placeholder="Preferred audio language (for example, en)" aria-label="Preferred audio language"></label><label class="k-control-shell k-input-shell"><input class="k-input" name="preferredSubtitleLanguage" value="${escapeHtml(root?.preferredSubtitleLanguage || '')}" placeholder="Preferred subtitle language (for example, en)" aria-label="Preferred subtitle language"></label><label class="k-control-shell k-check"><input type="checkbox" name="enabled"${root?.enabled !== false ? ' checked' : ''}> Enabled</label><div class="k-action-row"><button type="submit" class="k-button k-button--primary">Save root</button><button type="button" class="k-button" data-admin-root-close>Cancel</button></div></form>`;
      const pathInput = dialog.querySelector('[data-admin-root-path]');
      dialog.querySelector('[data-admin-root-browse]')?.addEventListener('click', () => this.browseRootDirectory(dialog, pathInput));
      dialog.querySelector('[data-admin-root-close]')?.addEventListener('click', () => dialog.close());
      dialog.querySelector('[data-admin-root-form]')?.addEventListener('submit', (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        this.saveRoot(root, form);
        dialog.close();
      });
      dialog.showModal();
    }

    saveRoot(root, form) {
      return this.operation(root ? 'root-update' : 'root-create', {
        rootId: root?.id || null,
        displayName: form.get('displayName'),
        path: form.get('path'),
        kind: form.get('kind'),
        tags: String(form.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean),
        preferredAudioLanguage: form.get('preferredAudioLanguage'),
        preferredSubtitleLanguage: form.get('preferredSubtitleLanguage'),
        enabled: form.get('enabled') === 'on',
      });
    }

    async browseRootDirectory(dialog, pathInput, path = null) {
      if (!(dialog instanceof HTMLDialogElement) || !(pathInput instanceof HTMLInputElement)) return;
      const panel = dialog.querySelector('[data-admin-directory-picker]');
      const source = this.source('directories-source');
      if (!panel || !source) return;
      const requested = path || pathInput.value || null;
      panel.hidden = false;
      panel.innerHTML = '<div class="k-picker__status">Loading directories…</div>';
      try {
        const suffix = requested ? `?path=${encodeURIComponent(requested)}` : '';
        const response = await fetch(`${source}${suffix}`, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Directory could not be loaded.');
        this.renderDirectoryPicker(panel, pathInput, payload);
      } catch (error) {
        panel.innerHTML = `<div class="k-admin-status k-admin-status--error">${escapeHtml(error?.message || 'Directory could not be loaded.')}</div>`;
      }
    }

    renderDirectoryPicker(panel, pathInput, listing) {
      const entries = Array.isArray(listing.entries) ? listing.entries : [];
      const path = typeof listing.path === 'string' ? listing.path : '';
      const parent = typeof listing.parent_path === 'string' ? listing.parent_path : null;
      const rows = entries.map((entry) => {
        const entryPath = typeof entry.path === 'string' ? entry.path : '';
        const name = typeof entry.name === 'string' ? entry.name : entryPath;
        return `<button type="button" class="k-directory-picker__entry" data-admin-directory-open="${escapeHtml(entryPath)}">${escapeHtml(name)}</button>`;
      }).join('');
      panel.innerHTML = `<div class="k-directory-picker__header"><button type="button" class="k-button k-button--primary" data-admin-directory-use>Use this folder</button>${parent ? `<button type="button" class="k-button" data-admin-directory-parent="${escapeHtml(parent)}">Up</button>` : ''}<span class="k-directory-picker__path" title="${escapeHtml(path)}">${escapeHtml(path)}</span></div><div class="k-directory-picker__entries">${rows || '<div class="k-picker__status">No readable child directories.</div>'}</div>`;
      panel.querySelector('[data-admin-directory-use]')?.addEventListener('click', () => {
        pathInput.value = path;
        panel.hidden = true;
      });
      panel.querySelector('[data-admin-directory-parent]')?.addEventListener('click', (event) => this.browseRootDirectory(pathInput.closest('dialog'), pathInput, event.currentTarget.dataset.adminDirectoryParent));
      panel.querySelectorAll('[data-admin-directory-open]').forEach((button) => button.addEventListener('click', () => this.browseRootDirectory(pathInput.closest('dialog'), pathInput, button.dataset.adminDirectoryOpen)));
    }

    keyDown(event) {
      if (this.section !== 'metadata' || this.subsection === 'artwork' || this.hasOpenDialog()) return;
      const target = event.target;
      const editable = target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || target instanceof HTMLSelectElement
        || Boolean(target?.isContentEditable)
        || Boolean(target?.closest?.('button, a, [contenteditable="true"]'));
      if (editable) return;
      if (event.key === 'Enter') {
        event.preventDefault();
        this.metadataAction('match');
      } else if (event.key.toLowerCase() === 'r') {
        this.metadataAction('reject');
      } else if (event.key.toLowerCase() === 'i') {
        this.metadataAction('ignore');
      } else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
        event.preventDefault();
        this.moveCandidate(1);
      } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
        event.preventDefault();
        this.moveCandidate(-1);
      } else if (event.key.toLowerCase() === 'j') {
        this.moveReview(1);
      } else if (event.key.toLowerCase() === 'k') {
        this.moveReview(-1);
      }
    }
  }

  if (!customElements.get('kanvas-administration')) customElements.define('kanvas-administration', KanvasAdministration);

})();
