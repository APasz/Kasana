(() => {
  'use strict';

  const {escapeHtml, jobDetail, providerEntryUrl} = window.kanvasInternals;
  const adminDate = (value) => {
    if (typeof value !== 'string') return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? '—' : parsed.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});
  };
  const adminBytes = (value) => {
    if (!Number.isFinite(value)) return '—';
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  };
  const adminOperationLabel = (operation) => ({
    scan: 'Library scan',
    'library-consistency': 'Library cleanup',
    'artwork-fetch': 'Artwork fetch',
    'hierarchy-repair': 'Hierarchy repair',
    'duplicate-resolve': 'Duplicate merge',
    'duplicate-resolve-batch': 'Duplicate merge',
    'duplicate-resolution': 'Duplicate merge',
    'root-create': 'Library root update',
    'root-update': 'Library root update',
    'root-delete': 'Library root update',
    'cancel-job': 'Job cancellation',
    match: 'Metadata match',
    reject: 'Metadata rejection',
    ignore: 'Metadata update',
    refresh: 'Metadata refresh',
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
  class KanvasAdministration extends HTMLElement {
    constructor() {
      super();
      this.section = 'overview';
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
      this.inFlight = false;
      this.timer = null;
      this.abort = null;
      this.onVisibility = () => this.visibilityChanged();
      this.onKeyDown = (event) => this.keyDown(event);
    }

    connectedCallback() {
      this.section = this.getAttribute('data-section') || 'overview';
      document.addEventListener('visibilitychange', this.onVisibility);
      document.addEventListener('keydown', this.onKeyDown);
      this.load();
    }

    disconnectedCallback() {
      document.removeEventListener('visibilitychange', this.onVisibility);
      document.removeEventListener('keydown', this.onKeyDown);
      window.clearTimeout(this.timer);
      this.abort?.abort();
    }

    source(name) { return this.getAttribute(name); }

    hasOpenDialog() {
      return this.querySelector('dialog[open]') instanceof HTMLDialogElement;
    }

    async fetchJson(source, suffix = '') {
      if (!source) throw new Error('Missing administration source');
      const response = await fetch(`${source}${suffix}`, {headers: {'Accept': 'application/json'}, credentials: 'same-origin', signal: this.abort?.signal});
      if (!response.ok) throw new Error(`Administration request failed (${response.status})`);
      return response.json();
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
        if (this.section === 'overview' || this.section === 'artwork') {
          this.overview = await this.fetchJson(this.source('overview-source'));
        }
        if (this.section === 'jobs') {
          const page = await this.fetchJson(this.source('jobs-source'));
          this.jobs = Array.isArray(page.items) ? page.items : [];
          this.cursor = typeof page.nextCursor === 'string' ? page.nextCursor : null;
        }
        if (this.section === 'libraries') {
          const page = await this.fetchJson(this.source('roots-source'));
          this.roots = Array.isArray(page.items) ? page.items : [];
        }
        if (this.section === 'metadata') {
          const page = await this.fetchJson(this.source('metadata-source'));
          this.reviewItems = Array.isArray(page.items) ? page.items : [];
          this.cursor = typeof page.nextCursor === 'string' ? page.nextCursor : null;
          this.reviewIndex = Math.min(this.reviewIndex, Math.max(0, this.reviewItems.length - 1));
        }
        if (this.section === 'hierarchy') {
          this.hierarchy = await this.fetchJson(this.source('hierarchy-source'));
        }
        if (this.section === 'duplicates') {
          this.duplicates = await this.fetchJson(this.source('duplicates-source'));
        }
        await this.checkSubmittedJob();
        if (!this.hasOpenDialog()) this.render();
      } catch (error) {
        if (error?.name !== 'AbortError') this.renderError();
      } finally {
        this.inFlight = false;
        this.schedule();
      }
    }

    schedule() {
      window.clearTimeout(this.timer);
      if (document.visibilityState === 'hidden') return;
      const active = Number(this.overview?.activeJobCount || 0);
      const interval = this.submittedJobId ? 2000 : active ? 5000 : 30000;
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
      if (!this.children.length) this.innerHTML = '<div class="k-admin-status" aria-live="polite">Loading administration…</div>';
    }

    renderError() {
      this.innerHTML = '<div class="k-admin-status" aria-live="polite">Katalog is unavailable. <button type="button" class="k-button" data-admin-retry>Retry</button></div>';
      this.querySelector('[data-admin-retry]')?.addEventListener('click', () => this.load());
    }

    render() {
      if (this.section === 'metadata') this.renderMetadata();
      else if (this.section === 'libraries') this.renderLibraries();
      else if (this.section === 'jobs') this.renderJobs();
      else if (this.section === 'artwork') this.renderArtwork();
      else if (this.section === 'hierarchy') this.renderHierarchy();
      else if (this.section === 'duplicates') this.renderDuplicates();
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

    statusRow(label, value, action, destination) {
      const button = action ? `<a class="k-button" href="${escapeHtml(destination)}">${escapeHtml(action)}</a>` : '';
      return `<div class="k-admin-row"><span>${escapeHtml(label)}</span><span class="k-admin-row__value">${escapeHtml(String(value))}</span>${button}</div>`;
    }

    renderOverview() {
      const data = this.overview;
      if (!data) return this.renderError();
      const providers = Array.isArray(data.providers) ? data.providers : [];
      const providerRows = providers.length ? providers.map((provider) => this.statusRow(provider.name, provider.available ? 'Available' : 'Unavailable', !provider.available ? 'Review' : '', '/administration/metadata')).join('') : this.statusRow('Provider', 'Not configured', '', '');
      this.innerHTML = `<section class="k-admin-panel" aria-live="polite">
        ${this.statusRow('Katalog', data.connected ? 'Connected' : 'Unavailable', '', '')}
        ${this.statusRow('Database', data.databaseHealthy ? (data.databaseRevision || 'Healthy') : 'Unhealthy', '', '')}
        ${this.statusRow('Library roots', `${data.enabledRootCount} enabled · ${data.unavailableRootCount} unavailable`, data.unavailableRootCount ? 'Configure' : '', '/administration/libraries')}
        ${this.statusRow('Metadata', `${data.unresolvedMetadataCount} unresolved`, data.unresolvedMetadataCount ? 'Review' : '', '/administration/metadata')}
        ${this.statusRow('Jobs', `${data.activeJobCount} active · ${data.failedJobCount} failed · ${data.interruptedJobCount} interrupted`, (data.failedJobCount || data.interruptedJobCount) ? 'Inspect' : '', '/administration/jobs')}
        ${this.statusRow('Last scan', adminDate(data.lastSuccessfulScanAt), '', '')}
        ${this.statusRow('Artwork cache', `${adminBytes(data.artworkCacheSizeBytes)} · ${data.artworkCacheFileCount || 0} files`, 'Maintain', '/administration/artwork')}
        <div class="k-admin-row"><span>Scan</span><button type="button" class="k-button k-button--primary" data-admin-operation="scan">Scan library</button></div>
        <div class="k-admin-row"><span>Consistency</span><button type="button" class="k-button" data-admin-operation="library-consistency">Clean library</button></div>
        <div class="k-admin-provider-list">${providerRows}</div>
      </section>`;
      this.bindActions();
    }

    renderJobs() {
      const rows = this.jobs.map((job) => {
        const total = Number.isInteger(job.progressTotal) ? job.progressTotal : null;
        const current = Number.isInteger(job.progressCurrent) ? job.progressCurrent : 0;
        const percent = total && total > 0 ? Math.min(100, Math.round((current / total) * 100)) : null;
        const progress = total === null ? (job.phase ? `${current} ${job.progressUnit || ''}` : '—') : `${current}/${total} ${job.progressUnit || ''}`;
        const counters = Array.isArray(job.counters) ? job.counters.map(([key, value]) => `${key}: ${value}`).join(' · ') : '';
        return `<article class="k-job-row" data-job-id="${escapeHtml(job.id)}"><div><strong>${escapeHtml(job.kind)}</strong><small>${escapeHtml(job.status)}${job.phase ? ` · ${escapeHtml(job.phase)}` : ''}</small></div><div class="k-job-row__progress">${percent === null ? '<span class="k-progress-edge k-progress-edge--unknown"></span>' : `<span class="k-progress-edge"><span style="--k-progress:${percent}%"></span></span>`}<small>${escapeHtml(progress)}</small></div><div><small>${escapeHtml(jobDetail(job, counters))}</small><small>${adminDate(job.completedAt || job.startedAt || job.submittedAt)}</small></div>${job.cancellable ? `<button type="button" class="k-button" data-admin-cancel="${escapeHtml(job.id)}">Cancel</button>` : ''}</article>`;
      }).join('');
      this.innerHTML = `<section class="k-admin-list" aria-live="polite">${rows || '<div class="k-admin-status">No recent jobs.</div>'}${this.cursor ? '<button type="button" class="k-button" data-admin-more>More</button>' : ''}</section>`;
      this.bindActions();
    }

    renderLibraries() {
      const rows = this.roots.map((root) => `<article class="k-root-row" data-root-id="${root.id}"><div><strong>${escapeHtml(root.displayName || `Root ${root.id}`)}</strong><small>${escapeHtml(root.kind)} · ${(root.tags || []).map(escapeHtml).join(', ') || 'No tags'}</small></div><div><small>${root.enabled ? 'Enabled' : 'Disabled'} · ${root.available ? 'Available' : 'Unavailable'}</small><small>${root.itemCount || 0} items · ${root.mediaFileCount || 0} files · ${adminDate(root.lastScanCompletedAt)}</small><small>Audio: ${escapeHtml(root.preferredAudioLanguage || 'stream default')} · Subtitles: ${escapeHtml(root.preferredSubtitleLanguage || 'stream default')}</small></div><div class="k-row-actions"><button type="button" class="k-button" data-admin-operation="scan" data-root-id="${root.id}">Scan</button><button type="button" class="k-button" data-admin-root-edit="${root.id}">Edit</button><button type="button" class="k-button" data-admin-root-delete="${root.id}">Remove</button></div></article>`).join('');
      this.innerHTML = `<section class="k-admin-list"><div class="k-action-row"><button type="button" class="k-button k-button--primary" data-admin-root-add>Add root</button></div>${rows || '<div class="k-admin-status">No library roots.</div>'}</section><dialog class="k-kanvas-dialog" data-admin-root-dialog></dialog>`;
      this.bindActions();
    }

    renderArtwork() {
      const data = this.overview;
      if (!data) return this.renderError();
      this.innerHTML = `<section class="k-admin-panel"><div class="k-admin-row"><span>Cache</span><span class="k-admin-row__value">${adminBytes(data.artworkCacheSizeBytes)} · ${data.artworkCacheFileCount || 0} files</span></div><div class="k-action-row"><button type="button" class="k-button k-button--primary" data-admin-operation="artwork-fetch">Fetch missing artwork</button></div></section>`;
      this.bindActions();
    }

    renderHierarchy() {
      const data = this.hierarchy;
      if (!data || !Array.isArray(data.actions) || !Array.isArray(data.manual_reviews)) return this.renderError();
      const impact = data.impact || {};
      const actionGroups = new Map();
      data.actions.forEach((action) => {
        const kind = typeof action.kind === 'string' ? action.kind : 'other';
        const entries = actionGroups.get(kind) || [];
        entries.push(action);
        actionGroups.set(kind, entries);
      });
      const actionCounts = [...actionGroups.entries()].map(([kind, entries]) => `<span><strong>${entries.length}</strong> ${escapeHtml(hierarchyActionLabel(kind).toLowerCase())}</span>`).join(' · ');
      const showActionDetails = data.actions.length <= 100 ? ' open' : '';
      const actionGroupsMarkup = [...actionGroups.entries()].map(([kind, entries]) => {
        const rows = entries.map((action) => {
          const itemId = Number(action.item_id);
          const itemLabel = typeof action.item_label === 'string' ? action.item_label : itemId ? `Item ${itemId}` : 'New catalogue record';
          const source = itemId ? `<a href="/item/${itemId}">${escapeHtml(itemLabel)}</a>` : escapeHtml(itemLabel);
          const targetId = Number(action.target_item_id);
          const targetLabel = typeof action.target_label === 'string' ? action.target_label : null;
          const target = targetLabel ? (targetId ? `<a href="/item/${targetId}">${escapeHtml(targetLabel)}</a>` : escapeHtml(targetLabel)) : '';
          return `<li class="k-hierarchy-action"><div><strong>${source}</strong>${target ? ` <span aria-hidden="true">→</span> ${target}` : ''}</div><small>${escapeHtml(action.explanation || 'No explanation.')}</small></li>`;
        }).join('');
        return `<details class="k-hierarchy-group"${showActionDetails}><summary>${escapeHtml(hierarchyActionLabel(kind))} · ${entries.length}</summary><ol class="k-admin-detail-list">${rows}</ol></details>`;
      }).join('');
      const reviews = data.manual_reviews.map((review) => {
        const itemId = Number(review.item_id);
        const itemLabel = typeof review.item_label === 'string' ? review.item_label : itemId ? `Item ${itemId}` : null;
        const item = itemLabel ? (itemId ? `<a href="/item/${itemId}">${escapeHtml(itemLabel)}</a> · ` : `${escapeHtml(itemLabel)} · `) : '';
        return `<li>${item}${escapeHtml(review.reason || 'Manual review required.')}</li>`;
      }).join('');
      this.innerHTML = `<section class="k-admin-panel" aria-live="polite">
        <div class="k-admin-row"><span>Planned changes</span><span class="k-admin-row__value">${data.actions.length} total${actionCounts ? ` · ${actionCounts}` : ''}</span></div>
        <div class="k-admin-row"><span>Manual review</span><span class="k-admin-row__value">${data.manual_reviews.length}</span></div>
        <div class="k-admin-row"><span>Affected references</span><span class="k-admin-row__value">${Number(impact.playback_states || 0)} playback · ${Number(impact.metadata_bindings || 0)} metadata · ${Number(impact.collection_memberships || 0)} collections · ${Number(impact.watch_order_entries || 0)} watch-order entries</span></div>
        <div class="k-action-row"><button type="button" class="k-button" data-admin-hierarchy-dry>Run durable dry run</button><button type="button" class="k-button k-button--primary" data-admin-hierarchy-apply>Apply repair</button></div>
        <p class="k-quiet-copy">This is a preview of planned hierarchy changes. Expand a group to inspect each affected catalogue record and its destination. Apply affects every listed change; media files are never changed.</p>
        <section class="k-hierarchy-groups">${actionGroupsMarkup || '<div class="k-admin-status">No automatic repairs are currently safe.</div>'}</section>
        <details><summary>Detected structural issues requiring review (${data.manual_reviews.length})</summary><ul class="k-admin-detail-list">${reviews || '<li>No ambiguous structural issues were detected.</li>'}</ul></details>
        <p class="k-quiet-copy">Apply creates a database backup and runs as a durable administration job. Media files are never changed.</p>
      </section>`;
      this.bindActions();
    }

    renderDuplicates() {
      const candidates = this.duplicates?.candidates;
      if (!Array.isArray(candidates)) return this.renderError();
      const fileIssues = Array.isArray(this.duplicates?.fileIssues) ? this.duplicates.fileIssues : [];
      const candidateKeys = new Set(candidates.map((candidate) => `${Number(candidate.source_item_id)}:${Number(candidate.target_item_id)}`));
      this.selectedDuplicatePairs = new Set([...this.selectedDuplicatePairs].filter((key) => candidateKeys.has(key)));
      const rows = candidates.map((candidate) => {
        const impact = candidate.impact || {};
        const key = `${Number(candidate.source_item_id)}:${Number(candidate.target_item_id)}`;
        const source = `${candidate.source_title || 'Untitled'}${candidate.source_year ? ` (${candidate.source_year})` : ''}`;
        const target = `${candidate.target_title || 'Untitled'}${candidate.target_year ? ` (${candidate.target_year})` : ''}`;
        const references = `${Number(impact.playback_states || 0)} playback · ${Number(impact.metadata_bindings || 0)} metadata · ${Number(impact.collection_memberships || 0)} collections · ${Number(impact.watch_order_entries || 0)} watch-order entries`;
        return `<article class="k-root-row"><div><strong>Media-less record</strong><small><a href="/item/${Number(candidate.source_item_id)}">${escapeHtml(source)}</a></small></div><div><strong>File-backed record</strong><small><a href="/item/${Number(candidate.target_item_id)}">${escapeHtml(target)}</a> · ${escapeHtml(candidate.provider || 'provider')} ${escapeHtml(candidate.provider_id || '')}</small><small>${escapeHtml(references)}</small></div><div class="k-row-actions"><label class="k-check"><input type="checkbox" data-admin-duplicate-select="${escapeHtml(key)}"${this.selectedDuplicatePairs.has(key) ? ' checked' : ''}> Select</label><button type="button" class="k-button k-button--primary" data-admin-duplicate-resolve data-admin-duplicate-source="${Number(candidate.source_item_id)}" data-admin-duplicate-target="${Number(candidate.target_item_id)}">Merge</button></div></article>`;
      }).join('');
      const fileRows = fileIssues.map((issue) => `<article class="k-root-row k-duplicate-file-row"><div><strong>Duplicate episode file</strong><small>${escapeHtml(issue.path || '')}</small></div><div><small>${escapeHtml(issue.message || 'This file was not catalogued.')}</small></div></article>`).join('');
      const selectedCount = this.selectedDuplicatePairs.size;
      const batchAction = candidates.length > 1 ? `<div class="k-action-row"><button type="button" class="k-button k-button--primary" data-admin-duplicates-merge${selectedCount ? '' : ' disabled'}>Merge selected (${selectedCount})</button></div>` : '';
      this.innerHTML = `<section class="k-admin-list" aria-live="polite"><p class="k-quiet-copy">Only one-to-one matches are shown. Merging transfers catalogue state and matching empty hierarchy records to the file-backed item, creates one database backup, then deletes the media-less duplicates. Media files are never changed.</p>${batchAction}${rows || '<div class="k-admin-status">No unambiguous media-less record duplicates are currently ready to merge.</div>'}<section class="k-admin-list"><p class="k-quiet-copy">Duplicate episode files are left uncatalogued. Rename, move, or remove the unwanted file, then scan the library again.</p><div class="k-action-row"><button type="button" class="k-button" data-admin-operation="scan">Scan after resolving</button></div>${fileRows || '<div class="k-admin-status">No duplicate episode files need attention.</div>'}</section></section>`;
      this.bindActions();
    }

    renderMetadata() {
      const item = this.reviewItems[this.reviewIndex];
      if (!item) {
        this.innerHTML = '<div class="k-admin-status">No unresolved metadata items.</div>';
        return;
      }
      const candidates = Array.isArray(item.candidates) ? item.candidates : [];
      this.candidateIndex = Math.min(this.candidateIndex, Math.max(0, candidates.length - 1));
      const candidate = candidates[this.candidateIndex];
      const reviewPosition = `${this.reviewIndex + 1} of ${this.reviewItems.length}`;
      const candidateRows = candidates.map((entry, index) => `<button type="button" class="k-metadata-candidate${index === this.candidateIndex ? ' k-metadata-candidate--selected' : ''}" data-admin-candidate="${index}"><span>${escapeHtml(entry.title)}</span><small>${escapeHtml(entry.provider)} · ${Math.round(Number(entry.confidence || 0) * 100)}%</small><span class="k-progress-edge"><span style="--k-progress:${Math.round(Number(entry.confidence || 0) * 100)}%"></span></span></button>`).join('');
      const selectedUrl = providerEntryUrl(candidate);
      const selectedTitle = candidate
        ? selectedUrl
          ? `<a class="k-metadata-selected__title" href="${escapeHtml(selectedUrl)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(candidate.title)} on ${escapeHtml(candidate.provider)}">${escapeHtml(candidate.title)}</a>`
          : `<strong>${escapeHtml(candidate.title)}</strong>`
        : '';
      const selected = candidate ? `<div class="k-metadata-selected">${selectedTitle}<small>${escapeHtml(candidate.provider)} · ${candidate.year || '—'} · ${Math.round(Number(candidate.confidence || 0) * 100)}%</small><details><summary>Scoring</summary><p>Confidence is supplied by ${escapeHtml(candidate.provider)}. Match only when the local title, year, and kind agree.</p></details></div>` : '<div class="k-admin-status">No candidates.</div>';
      this.innerHTML = `<section class="k-metadata-review" aria-live="polite"><div class="k-metadata-local"><span class="k-metadata-panel__heading">Library item</span><div class="k-metadata-local__body">${item.posterUrl ? `<img src="${escapeHtml(item.posterUrl)}" alt="">` : '<span class="k-metadata-poster">?</span>'}<div><strong>${escapeHtml(item.title)}</strong><small>${item.year || '—'} · ${escapeHtml(item.kind)}</small></div></div></div><div class="k-metadata-candidates"><div class="k-metadata-panel__heading"><span>Candidate matches</span><small>${candidates.length} available</small></div><div class="k-metadata-candidate-list">${candidateRows}</div></div><div class="k-metadata-actions"><div><span class="k-metadata-panel__heading">Selected match</span>${selected}</div><div class="k-metadata-decision"><span class="k-metadata-panel__heading">Decision</span><div class="k-action-row"><button type="button" class="k-button k-button--primary" data-admin-metadata="match">Match</button><button type="button" class="k-button" data-admin-metadata="reject">Reject</button><button type="button" class="k-button" data-admin-metadata="ignore">Ignore</button><button type="button" class="k-button" data-admin-metadata="refresh">Refresh</button></div></div><div class="k-metadata-navigation"><span>Review ${reviewPosition}</span><div class="k-action-row"><button type="button" class="k-button" data-admin-review-nav="previous">Previous</button><button type="button" class="k-button" data-admin-review-nav="next">Next</button></div></div></div></section>`;
      this.bindActions();
    }

    bindActions() {
      this.querySelectorAll('[data-admin-operation]').forEach((button) => button.addEventListener('click', () => {
        if (button.dataset.adminOperation === 'library-consistency' && !window.confirm('Clean the library catalogue? A database backup is created before hierarchy repair.')) return;
        this.operation(button.dataset.adminOperation, {rootId: button.dataset.rootId ? Number(button.dataset.rootId) : null});
      }));
      this.querySelector('[data-admin-hierarchy-dry]')?.addEventListener('click', () => this.operation('hierarchy-repair', {apply: false}));
      this.querySelector('[data-admin-hierarchy-apply]')?.addEventListener('click', () => {
        if (window.confirm('Apply the proposed hierarchy repair? A database backup will be created first.')) {
          this.operation('hierarchy-repair', {apply: true, confirmed: true});
        }
      });
      this.querySelectorAll('[data-admin-duplicate-resolve]').forEach((button) => button.addEventListener('click', () => {
        const sourceItemId = Number(button.dataset.adminDuplicateSource);
        const targetItemId = Number(button.dataset.adminDuplicateTarget);
        if (!Number.isInteger(sourceItemId) || !Number.isInteger(targetItemId)) return;
        if (window.confirm('Merge this duplicate? The media-less catalogue record and matching empty hierarchy will be deleted after their metadata, collections, and watch state are transferred. A database backup will be created first.')) {
          this.operation('duplicate-resolve', {sourceItemId, targetItemId, confirmed: true});
        }
      }));
      this.querySelectorAll('[data-admin-duplicate-select]').forEach((input) => input.addEventListener('change', () => {
        const key = input.dataset.adminDuplicateSelect;
        if (!key) return;
        if (input.checked) this.selectedDuplicatePairs.add(key);
        else this.selectedDuplicatePairs.delete(key);
        this.renderDuplicates();
      }));
      this.querySelector('[data-admin-duplicates-merge]')?.addEventListener('click', () => {
        const resolutions = [...this.selectedDuplicatePairs].map((key) => {
          const [source_item_id, target_item_id] = key.split(':').map(Number);
          return {source_item_id, target_item_id};
        });
        if (!resolutions.length) return;
        if (window.confirm(`Merge ${resolutions.length} selected duplicates? The media-less catalogue records and matching empty hierarchy will be deleted after their metadata, collections, and watch state are transferred. One database backup will be created first.`)) {
          this.operation('duplicate-resolve-batch', {resolutions, confirmed: true});
        }
      });
      this.querySelectorAll('[data-admin-cancel]').forEach((button) => button.addEventListener('click', () => { if (window.confirm('Cancel this job?')) this.operation('cancel-job', {jobId: button.dataset.adminCancel}); }));
      this.querySelector('[data-admin-more]')?.addEventListener('click', () => this.moreJobs());
      this.querySelectorAll('[data-admin-candidate]').forEach((button) => button.addEventListener('click', () => { this.candidateIndex = Number(button.dataset.adminCandidate); this.renderMetadata(); }));
      this.querySelectorAll('[data-admin-metadata]').forEach((button) => this.querySelector('[data-admin-metadata]') && button.addEventListener('click', () => this.metadataAction(button.dataset.adminMetadata)));
      this.querySelectorAll('[data-admin-review-nav]').forEach((button) => button.addEventListener('click', () => this.moveReview(button.dataset.adminReviewNav === 'next' ? 1 : -1)));
      this.querySelector('[data-admin-root-add]')?.addEventListener('click', () => this.rootDialog(null));
      this.querySelectorAll('[data-admin-root-edit]').forEach((button) => button.addEventListener('click', () => this.rootDialog(this.roots.find((root) => root.id === Number(button.dataset.adminRootEdit)) || null)));
      this.querySelectorAll('[data-admin-root-delete]').forEach((button) => button.addEventListener('click', () => { if (window.confirm('Remove this root configuration? Catalogued items require confirmation.')) this.operation('root-delete', {rootId: Number(button.dataset.adminRootDelete), confirm: true}); }));
    }

    async operation(operation, extra = {}, refresh = true) {
      const source = this.getAttribute('action-source');
      if (!source) return;
      try {
        const response = await fetch(source, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify({operation, ...extra})});
        const payload = await response.json();
        if (!response.ok) {
          const message = payload.error || 'Action failed';
          const requestId = typeof payload.requestId === 'string' ? payload.requestId : null;
          throw new Error(requestId ? `${message} (Katalog request ID: ${requestId})` : message);
        }
        if (typeof payload.job?.id === 'string' && payload.job.id) {
          this.submittedJobId = payload.job.id;
          this.activity = {state: 'active', message: `${adminOperationLabel(operation)} queued. Waiting for Katalog to start it.`};
        } else {
          this.activity = {state: 'complete', message: `${adminOperationLabel(operation)} completed.`};
        }
        if (refresh) this.load();
        return true;
      } catch (error) {
        this.activity = {state: 'error', message: error?.message || 'Action could not be applied.'};
        this.render();
        return false;
      }
    }

    renderInlineError(message) {
      const status = this.querySelector('section') || this;
      const error = document.createElement('div');
      error.className = 'k-admin-status k-admin-status--error';
      error.textContent = message;
      status.prepend(error);
    }

    async checkSubmittedJob() {
      if (!this.submittedJobId) return;
      const page = await this.fetchJson(this.source('jobs-source'));
      const jobs = Array.isArray(page.items) ? page.items : [];
      const job = jobs.find((entry) => entry?.id === this.submittedJobId);
      if (!job) {
        this.activity = {state: 'active', message: 'Action submitted. Waiting for Katalog to report its progress.'};
        return;
      }
      const label = adminOperationLabel(job.kind);
      const phase = typeof job.phase === 'string' && job.phase ? ` · ${job.phase}` : '';
      const progress = jobProgress(job);
      if (job.status === 'failed' || job.status === 'interrupted') {
        this.activity = {state: 'error', message: job.failure || job.message || `${label} ${job.status}. Opening job details.`};
        window.location.assign(`/administration/jobs#${encodeURIComponent(this.submittedJobId)}`);
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

    async metadataAction(action) {
      const item = this.reviewItems[this.reviewIndex];
      const candidate = item?.candidates?.[this.candidateIndex];
      if (!item || ((action === 'match' || action === 'reject') && !candidate)) return;
      const payload = {itemId: item.itemId, ...(candidate ? {provider: candidate.provider, providerId: candidate.providerId} : {})};
      try {
        const succeeded = await this.operation(action, payload, false);
        if (succeeded && action !== 'refresh') {
          this.reviewItems.splice(this.reviewIndex, 1);
          this.reviewIndex = Math.min(this.reviewIndex, Math.max(0, this.reviewItems.length - 1));
          this.candidateIndex = 0;
          this.renderMetadata();
        }
      } catch (_) { /* operation renders the inline failure */ }
    }

    moveReview(offset) {
      if (!this.reviewItems.length) return;
      this.reviewIndex = Math.min(Math.max(0, this.reviewIndex + offset), this.reviewItems.length - 1);
      this.candidateIndex = 0;
      this.renderMetadata();
    }

    async moreJobs() {
      if (!this.cursor || this.inFlight) return;
      this.inFlight = true;
      try {
        const page = await this.fetchJson(this.source('jobs-source'), `?cursor=${encodeURIComponent(this.cursor)}`);
        this.jobs.push(...(Array.isArray(page.items) ? page.items : []));
        this.cursor = typeof page.nextCursor === 'string' ? page.nextCursor : null;
        this.renderJobs();
      } finally { this.inFlight = false; }
    }

    rootDialog(root) {
      const dialog = this.querySelector('[data-admin-root-dialog]');
      if (!(dialog instanceof HTMLDialogElement)) return;
      dialog.innerHTML = `<form method="dialog" class="k-picker k-admin-root-form" data-admin-root-form><div class="k-picker__header"><strong>${root ? 'Edit root' : 'Add root'}</strong></div><label class="k-control-shell k-input-shell"><input class="k-input" name="displayName" value="${escapeHtml(root?.displayName || '')}" placeholder="Name" aria-label="Root name"></label><div class="k-admin-root-path-row"><label class="k-control-shell k-input-shell"><input class="k-input" name="path" value="${escapeHtml(root?.path || '')}" placeholder="Path" aria-label="Root path" data-admin-root-path></label><button type="button" class="k-button" data-admin-root-browse>Browse</button></div><div class="k-directory-picker" data-admin-directory-picker hidden></div><label class="k-control-shell k-select-wrap"><select class="k-select" name="kind" aria-label="Root kind"><option value="movie"${root?.kind === 'movie' ? ' selected' : ''}>Movie</option><option value="series"${root?.kind === 'series' ? ' selected' : ''}>Series</option></select></label><label class="k-control-shell k-input-shell"><input class="k-input" name="tags" value="${escapeHtml((root?.tags || []).join(', '))}" placeholder="Tags" aria-label="Root tags"></label><label class="k-control-shell k-input-shell"><input class="k-input" name="preferredAudioLanguage" value="${escapeHtml(root?.preferredAudioLanguage || '')}" placeholder="Preferred audio language (for example, en)" aria-label="Preferred audio language"></label><label class="k-control-shell k-input-shell"><input class="k-input" name="preferredSubtitleLanguage" value="${escapeHtml(root?.preferredSubtitleLanguage || '')}" placeholder="Preferred subtitle language (for example, en)" aria-label="Preferred subtitle language"></label><label class="k-control-shell k-check"><input type="checkbox" name="enabled"${root?.enabled !== false ? ' checked' : ''}> Enabled</label><div class="k-action-row"><button type="submit" class="k-button k-button--primary">Save</button><button type="button" class="k-button" data-admin-root-close>Cancel</button></div></form>`;
      const pathInput = dialog.querySelector('[data-admin-root-path]');
      dialog.querySelector('[data-admin-root-browse]')?.addEventListener('click', () => this.browseRootDirectory(dialog, pathInput));
      dialog.querySelector('[data-admin-root-close]')?.addEventListener('click', () => dialog.close());
      dialog.querySelector('[data-admin-root-form]')?.addEventListener('submit', (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        this.operation(root ? 'root-update' : 'root-create', {rootId: root?.id || null, displayName: form.get('displayName'), path: form.get('path'), kind: form.get('kind'), tags: String(form.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean), preferredAudioLanguage: form.get('preferredAudioLanguage'), preferredSubtitleLanguage: form.get('preferredSubtitleLanguage'), enabled: form.get('enabled') === 'on'});
        dialog.close();
      });
      dialog.showModal();
    }

    async browseRootDirectory(dialog, pathInput, path = null) {
      if (!(dialog instanceof HTMLDialogElement)) return;
      if (!(pathInput instanceof HTMLInputElement)) return;
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
      if (this.section !== 'metadata') return;
      const editable = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement;
      if (editable) return;
      if (event.key === 'Enter') { event.preventDefault(); this.metadataAction('match'); }
      else if (event.key.toLowerCase() === 'r') this.metadataAction('reject');
      else if (event.key.toLowerCase() === 'i') this.metadataAction('ignore');
      else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') { event.preventDefault(); this.candidateIndex += 1; this.renderMetadata(); }
      else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') { event.preventDefault(); this.candidateIndex = Math.max(0, this.candidateIndex - 1); this.renderMetadata(); }
      else if (event.key.toLowerCase() === 'j') this.moveReview(1);
      else if (event.key.toLowerCase() === 'k') this.moveReview(-1);
      else if (event.key === 'Escape') this.querySelector('dialog[open]')?.close();
    }
  }

  if (!customElements.get('kanvas-administration')) customElements.define('kanvas-administration', KanvasAdministration);

})();
