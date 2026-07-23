(() => {
  'use strict';

  const {escapeHtml, jobDetail} = window.kanvasInternals;
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
  const providerEntryUrl = (candidate) => {
    const provider = typeof candidate?.provider === 'string' ? candidate.provider.toLowerCase() : '';
    const providerId = typeof candidate?.providerId === 'string' ? candidate.providerId : '';
    if (provider === 'tmdb' && /^\d+$/.test(providerId)) {
      const section = candidate.kind === 'movie' ? 'movie' : candidate.kind === 'series' ? 'tv' : null;
      return section ? `https://www.themoviedb.org/${section}/${providerId}` : null;
    }
    if ((provider === 'imdb' || provider === 'omdb') && /^tt\d+$/i.test(providerId)) {
      return `https://www.imdb.com/title/${providerId}/`;
    }
    if (provider === 'tvmaze' && candidate.kind === 'series' && /^\d+$/.test(providerId)) {
      return `https://www.tvmaze.com/shows/${providerId}`;
    }
    return null;
  };

  class KanvasAdministration extends HTMLElement {
    constructor() {
      super();
      this.section = 'overview';
      this.overview = null;
      this.hierarchy = null;
      this.jobs = [];
      this.roots = [];
      this.reviewItems = [];
      this.reviewIndex = 0;
      this.candidateIndex = 0;
      this.cursor = null;
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
      this.timer = window.setTimeout(() => this.load(), active ? 5000 : 30000);
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
      if (this.section === 'metadata') return this.renderMetadata();
      if (this.section === 'libraries') return this.renderLibraries();
      if (this.section === 'jobs') return this.renderJobs();
      if (this.section === 'artwork') return this.renderArtwork();
      if (this.section === 'hierarchy') return this.renderHierarchy();
      this.renderOverview();
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
      const rows = this.roots.map((root) => `<article class="k-root-row" data-root-id="${root.id}"><div><strong>${escapeHtml(root.displayName || `Root ${root.id}`)}</strong><small>${escapeHtml(root.kind)} · ${(root.tags || []).map(escapeHtml).join(', ') || 'No tags'}</small></div><div><small>${root.enabled ? 'Enabled' : 'Disabled'} · ${root.available ? 'Available' : 'Unavailable'}</small><small>${root.itemCount || 0} items · ${root.mediaFileCount || 0} files · ${adminDate(root.lastScanCompletedAt)}</small></div><div class="k-row-actions"><button type="button" class="k-button" data-admin-operation="scan" data-root-id="${root.id}">Scan</button><button type="button" class="k-button" data-admin-root-edit="${root.id}">Edit</button><button type="button" class="k-button" data-admin-root-delete="${root.id}">Remove</button></div></article>`).join('');
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
      const actions = data.actions.map((action) => `<li><strong>${escapeHtml(action.kind || 'repair')}</strong> · ${escapeHtml(action.explanation || 'No explanation.')}</li>`).join('');
      const reviews = data.manual_reviews.map((review) => `<li>${escapeHtml(review.reason || 'Manual review required.')}</li>`).join('');
      this.innerHTML = `<section class="k-admin-panel" aria-live="polite">
        <div class="k-admin-row"><span>Proposed repairs</span><span class="k-admin-row__value">${data.actions.length}</span></div>
        <div class="k-admin-row"><span>Manual review</span><span class="k-admin-row__value">${data.manual_reviews.length}</span></div>
        <div class="k-admin-row"><span>Affected references</span><span class="k-admin-row__value">${Number(impact.playback_states || 0)} playback · ${Number(impact.metadata_bindings || 0)} metadata · ${Number(impact.collection_memberships || 0)} collections · ${Number(impact.watch_order_entries || 0)} watch-order entries</span></div>
        <div class="k-action-row"><button type="button" class="k-button" data-admin-hierarchy-dry>Run durable dry run</button><button type="button" class="k-button k-button--primary" data-admin-hierarchy-apply>Apply repair</button></div>
        <details open><summary>Proposed repair summary</summary><ul class="k-admin-detail-list">${actions || '<li>No automatic repairs are currently safe.</li>'}</ul></details>
        <details><summary>Detected structural issues requiring review</summary><ul class="k-admin-detail-list">${reviews || '<li>No ambiguous structural issues were detected.</li>'}</ul></details>
        <p class="k-quiet-copy">Apply creates a database backup and runs as a durable administration job. Media files are never changed.</p>
      </section>`;
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
      const candidateRows = candidates.map((entry, index) => `<button type="button" class="k-metadata-candidate${index === this.candidateIndex ? ' k-metadata-candidate--selected' : ''}" data-admin-candidate="${index}"><span>${escapeHtml(entry.title)}</span><small>${escapeHtml(entry.provider)} · ${Math.round(Number(entry.confidence || 0) * 100)}%</small><span class="k-progress-edge"><span style="--k-progress:${Math.round(Number(entry.confidence || 0) * 100)}%"></span></span></button>`).join('');
      const selectedUrl = providerEntryUrl(candidate);
      const selectedTitle = candidate
        ? selectedUrl
          ? `<a class="k-metadata-selected__title" href="${escapeHtml(selectedUrl)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(candidate.title)} on ${escapeHtml(candidate.provider)}">${escapeHtml(candidate.title)}</a>`
          : `<strong>${escapeHtml(candidate.title)}</strong>`
        : '';
      const selected = candidate ? `<div class="k-metadata-selected">${selectedTitle}<small>${escapeHtml(candidate.provider)} · ${candidate.year || '—'} · ${Math.round(Number(candidate.confidence || 0) * 100)}%</small><details><summary>Scoring</summary><p>Confidence is supplied by ${escapeHtml(candidate.provider)}. Match only when the local title, year, and kind agree.</p></details></div>` : '<div class="k-admin-status">No candidates.</div>';
      this.innerHTML = `<section class="k-metadata-review" aria-live="polite"><div class="k-metadata-local">${item.posterUrl ? `<img src="${escapeHtml(item.posterUrl)}" alt="">` : '<span class="k-metadata-poster">?</span>'}<div><strong>${escapeHtml(item.title)}</strong><small>${item.year || '—'} · ${escapeHtml(item.kind)}</small></div></div><div class="k-metadata-candidates">${candidateRows}</div><div class="k-metadata-actions">${selected}<div class="k-action-row"><button type="button" class="k-button k-button--primary" data-admin-metadata="match">Match</button><button type="button" class="k-button" data-admin-metadata="reject">Reject</button><button type="button" class="k-button" data-admin-metadata="ignore">Ignore</button><button type="button" class="k-button" data-admin-metadata="refresh">Refresh</button></div><div class="k-action-row"><button type="button" class="k-button" data-admin-review-nav="previous">Previous</button><button type="button" class="k-button" data-admin-review-nav="next">Next</button></div></div></section>`;
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
        if (!response.ok) throw new Error(payload.error || 'Action failed');
        if (payload.job?.id) window.location.assign(`/administration/jobs#${encodeURIComponent(payload.job.id)}`);
        else if (refresh) this.load();
        return true;
      } catch (error) {
        this.renderInlineError(error?.message || 'Action could not be applied.');
        return false;
      }
    }

    renderInlineError(message) {
      const status = this.querySelector('.k-admin-status') || this.querySelector('.k-admin-panel') || this;
      const error = document.createElement('div');
      error.className = 'k-admin-status k-admin-status--error';
      error.textContent = message;
      status.prepend(error);
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
      dialog.innerHTML = `<form method="dialog" class="k-picker k-admin-root-form" data-admin-root-form><div class="k-picker__header"><strong>${root ? 'Edit root' : 'Add root'}</strong></div><label class="k-control-shell k-input-shell"><input class="k-input" name="displayName" value="${escapeHtml(root?.displayName || '')}" placeholder="Name" aria-label="Root name"></label><div class="k-admin-root-path-row"><label class="k-control-shell k-input-shell"><input class="k-input" name="path" value="${escapeHtml(root?.path || '')}" placeholder="Path" aria-label="Root path" data-admin-root-path></label><button type="button" class="k-button" data-admin-root-browse>Browse</button></div><div class="k-directory-picker" data-admin-directory-picker hidden></div><label class="k-control-shell k-select-wrap"><select class="k-select" name="kind" aria-label="Root kind"><option value="movie"${root?.kind === 'movie' ? ' selected' : ''}>Movie</option><option value="series"${root?.kind === 'series' ? ' selected' : ''}>Series</option></select></label><label class="k-control-shell k-input-shell"><input class="k-input" name="tags" value="${escapeHtml((root?.tags || []).join(', '))}" placeholder="Tags" aria-label="Root tags"></label><label class="k-control-shell k-check"><input type="checkbox" name="enabled"${root?.enabled !== false ? ' checked' : ''}> Enabled</label><div class="k-action-row"><button type="submit" class="k-button k-button--primary">Save</button><button type="button" class="k-button" data-admin-root-close>Cancel</button></div></form>`;
      const pathInput = dialog.querySelector('[data-admin-root-path]');
      dialog.querySelector('[data-admin-root-browse]')?.addEventListener('click', () => this.browseRootDirectory(dialog, pathInput));
      dialog.querySelector('[data-admin-root-close]')?.addEventListener('click', () => dialog.close());
      dialog.querySelector('[data-admin-root-form]')?.addEventListener('submit', (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        this.operation(root ? 'root-update' : 'root-create', {rootId: root?.id || null, displayName: form.get('displayName'), path: form.get('path'), kind: form.get('kind'), tags: String(form.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean), enabled: form.get('enabled') === 'on'});
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
