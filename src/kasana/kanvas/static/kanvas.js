(() => {
  'use strict';

  const MAX_MOUNTED_POSTERS = 144;
  const GRID_ROW_TOP_TOLERANCE_PX = 1;
  const PROFILE_ACCENT_DEFAULT = '#e8e8e8';
  const PROFILE_PIN_MIN_LENGTH = 2;
  const PROFILE_PIN_MAX_LENGTH = 16;
  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);

  const POSTER_STATES = new Set([
    'normal', 'in_progress', 'watched', 'unavailable', 'selected', 'loading', 'missing_artwork'
  ]);

  const localArtworkUrl = (value) => typeof value === 'string' && /^\/kanvas\/artwork\/\d+\/\d+$/.test(value);
  const safeRequestId = (value) => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value) ? value : null;
  const safePosterId = (value) => value && typeof value === 'object' && Number.isSafeInteger(value.id) && value.id > 0 ? value.id : null;
  const jobDetail = (job, counters) => (job.status === 'failed' || job.status === 'interrupted')
    ? (job.failure || job.message || counters || '—')
    : (counters || job.message || job.failure || '—');
  const normalisePlaceholder = (value, title) => {
    const lines = value && typeof value === 'object' && Array.isArray(value.lines)
      ? value.lines
      : [title];
    const footer = value && typeof value === 'object' && typeof value.footer === 'string'
      ? value.footer.trim().slice(0, 80)
      : null;
    const safeLines = lines
      .filter((line) => typeof line === 'string' && line.trim())
      .slice(0, 3)
      .map((line) => line.trim().slice(0, 160));
    return {lines: safeLines.length ? safeLines : [title], footer: footer || null};
  };
  const normaliseHexColour = (value) => (
    typeof value === 'string' && /^#[0-9A-Fa-f]{6}$/.test(value) ? value : null
  );
  const profileMenuMarkup = (name, accentColour) => `
    <button type="button" class="k-profile-switcher" aria-haspopup="dialog" aria-label="Open profile settings" title="Profile settings">${escapeHtml(name)}</button>
    <dialog class="k-kanvas-dialog k-profile-dialog">
      <div class="k-picker k-profile-form" role="document">
        <div class="k-picker__header">
          <strong>Profile</strong>
          <button type="button" class="k-icon-action" data-profile-close aria-label="Close profile settings" title="Close">×</button>
        </div>
        <form data-profile-form>
          <label class="k-control-shell k-input-shell">
            <span class="k-sr-only">Profile name</span>
            <input class="k-input" name="displayName" value="${escapeHtml(name)}" aria-label="Profile name" placeholder="Profile name">
          </label>
          <div class="k-profile-pin-field">
            <label class="k-control-shell k-input-shell">
              <span class="k-sr-only">New PIN</span>
              <input class="k-input" name="pin" type="text" autocomplete="off" inputmode="numeric" minlength="${PROFILE_PIN_MIN_LENGTH}" maxlength="${PROFILE_PIN_MAX_LENGTH}" aria-label="New PIN" placeholder="New PIN">
            </label>
            <button type="button" class="k-button" data-profile-clear-pin>Clear PIN</button>
          </div>
          <label class="k-colour-field">
            <span>Accent colour</span>
            <input name="accentColour" type="color" value="${escapeHtml(accentColour)}" aria-label="Accent colour">
          </label>
          <div class="k-picker__status" data-profile-status aria-live="polite"></div>
          <div class="k-profile-actions">
            <button type="submit" class="k-button k-button--primary" data-profile-save>Save changes</button>
            <button type="submit" class="k-button" formaction="/profiles/sign-out" formmethod="post" data-profile-logout>Log out</button>
          </div>
        </form>
      </div>
    </dialog>`;

  class KanvasProfileMenu extends HTMLElement {
    constructor() {
      super();
      this.dialog = null;
      this.status = null;
      this.saveButton = null;
      this.pinClearRequested = false;
    }

    connectedCallback() {
      const name = this.profileName();
      const accentColour = normaliseHexColour(this.getAttribute('data-accent-colour')) || PROFILE_ACCENT_DEFAULT;
      this.innerHTML = profileMenuMarkup(name, accentColour);
      this.dialog = this.querySelector('dialog');
      this.status = this.querySelector('[data-profile-status]');
      this.saveButton = this.querySelector('[data-profile-save]');
      const closeDialog = () => this.dialog?.close();
      this.querySelector('.k-profile-switcher')?.addEventListener('click', () => this.open());
      this.querySelector('[data-profile-close]')?.addEventListener('click', closeDialog);
      this.querySelector('[data-profile-form]')?.addEventListener('submit', (event) => this.handleSubmit(event));
      this.querySelector('[data-profile-clear-pin]')?.addEventListener('click', () => this.requestPinClear());
      this.querySelector('input[name="pin"]')?.addEventListener('input', () => this.cancelPinClearWhenReplacing());
      this.dialog?.addEventListener('click', (event) => this.closeFromBackdrop(event));
      this.dialog?.addEventListener('close', () => this.resetForm());
    }

    profileName() {
      const name = this.getAttribute('data-name');
      return typeof name === 'string' && name.trim() ? name.trim() : 'Profile';
    }

    open() {
      if (!this.dialog) return;
      if (!this.dialog.open) this.dialog.showModal();
      this.querySelector('input[name="displayName"]')?.focus();
    }

    closeFromBackdrop(event) {
      if (event.target === this.dialog) this.dialog?.close();
    }

    handleSubmit(event) {
      const submitter = event.submitter;
      if (submitter instanceof HTMLButtonElement && submitter.hasAttribute('data-profile-logout')) return;
      this.save(event);
    }

    requestPinClear() {
      const pinInput = this.querySelector('input[name="pin"]');
      if (!(pinInput instanceof HTMLInputElement)) return;
      pinInput.value = '';
      this.pinClearRequested = true;
      this.setStatus('PIN will be cleared when you save changes.');
      pinInput.focus();
    }

    cancelPinClearWhenReplacing() {
      const pinInput = this.querySelector('input[name="pin"]');
      if (pinInput instanceof HTMLInputElement && pinInput.value.trim()) this.pinClearRequested = false;
    }

    resetForm() {
      const form = this.querySelector('[data-profile-form]');
      if (!(form instanceof HTMLFormElement)) return;
      form.reset();
      const nameInput = form.elements.namedItem('displayName');
      if (nameInput instanceof HTMLInputElement) nameInput.value = this.profileName();
      const accentInput = form.elements.namedItem('accentColour');
      if (accentInput instanceof HTMLInputElement) {
        accentInput.value = normaliseHexColour(this.getAttribute('data-accent-colour')) || PROFILE_ACCENT_DEFAULT;
      }
      this.pinClearRequested = false;
      this.setStatus('');
    }

    setStatus(message, error = false) {
      if (!this.status) return;
      this.status.textContent = message;
      this.status.classList.toggle('k-picker__status--error', error);
    }

    async save(event) {
      event.preventDefault();
      const form = event.currentTarget;
      if (!(form instanceof HTMLFormElement)) return;
      const data = new FormData(form);
      const accentColour = normaliseHexColour(String(data.get('accentColour') || ''));
      const pin = String(data.get('pin') || '').trim();
      if (!accentColour) {
        this.setStatus('Choose a valid accent colour.', true);
        return;
      }
      if (pin && (pin.length < PROFILE_PIN_MIN_LENGTH || pin.length > PROFILE_PIN_MAX_LENGTH)) {
        this.setStatus(`PIN must be ${PROFILE_PIN_MIN_LENGTH}-${PROFILE_PIN_MAX_LENGTH} characters.`, true);
        return;
      }
      const profilePayload = {displayName: String(data.get('displayName') || '').trim(), accent_colour: accentColour};
      if (pin) profilePayload.pin = pin;
      else if (this.pinClearRequested) profilePayload.pin = null;
      this.saveButton?.setAttribute('disabled', 'disabled');
      this.setStatus('Saving...');
      try {
        const profile = await this.patchJson('/profiles/current', profilePayload);
        const nextName = profile.display_name || profile.username || profilePayload.displayName || this.profileName();
        this.setAttribute('data-name', nextName);
        const button = this.querySelector('.k-profile-switcher');
        if (button instanceof HTMLButtonElement) button.textContent = nextName;
        const savedColour = normaliseHexColour(profile.accent_colour) || accentColour;
        this.setAttribute('data-accent-colour', savedColour);
        document.documentElement.style.setProperty('--k-accent', savedColour);
        const pinInput = form.elements.namedItem('pin');
        if (pinInput instanceof HTMLInputElement) pinInput.value = '';
        this.pinClearRequested = false;
        this.setStatus('Saved.');
      } catch (error) {
        this.setStatus(error?.message || 'Changes could not be saved.', true);
      } finally {
        this.saveButton?.removeAttribute('disabled');
      }
    }

    async patchJson(url, payload) {
      const response = await fetch(url, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify(payload)
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Changes could not be saved.');
      return body;
    }
  }

  if (!customElements.get('kanvas-profile-menu')) customElements.define('kanvas-profile-menu', KanvasProfileMenu);

  class LibraryLoadError extends Error {
    constructor(category, {status = null, requestId = null, cause = null} = {}) {
      super(category);
      this.category = category;
      this.status = status;
      this.requestId = safeRequestId(requestId);
      this.cause = cause;
    }
  }

  const normalisePoster = (value) => {
    if (!value || typeof value !== 'object') return null;
    const poster = value;
    if (typeof poster.id !== 'number' || !Number.isSafeInteger(poster.id) || poster.id <= 0) return null;
    if (typeof poster.title !== 'string' || !poster.title) return null;
    if (typeof poster.href !== 'string' || !/^\/item\/\d+$/.test(poster.href)) return null;
    if (typeof poster.available !== 'boolean') return null;
    if (poster.posterUrl != null && !localArtworkUrl(poster.posterUrl)) return null;
    const placeholder = normalisePlaceholder(poster.placeholder, poster.title);
    if (poster.subtitle != null && typeof poster.subtitle !== 'string') return null;
    if (poster.progressPercent != null && (!Number.isInteger(poster.progressPercent) || poster.progressPercent < 0 || poster.progressPercent > 100)) return null;
    if (typeof poster.state !== 'string' || !POSTER_STATES.has(poster.state)) return null;
    return {
      id: poster.id,
      title: poster.title,
      href: poster.href,
      posterUrl: poster.posterUrl ?? null,
      placeholder,
      subtitle: poster.subtitle ?? null,
      progressPercent: poster.progressPercent ?? null,
      state: poster.state,
      available: poster.available
    };
  };

  const posterMarkup = (poster) => {
    const progress = poster.progressPercent == null ? '' :
      `<span class="k-progress" aria-label="Playback progress"><span class="k-progress__value" style="--k-progress:${poster.progressPercent}%"></span></span>`;
    const placeholderLines = poster.placeholder.lines
      .map((line) => `<span class="k-poster__fallback-line">${escapeHtml(line)}</span>`)
      .join('');
    const placeholderFooter = poster.placeholder.footer
      ? `<span class="k-poster__fallback-footer">${escapeHtml(poster.placeholder.footer)}</span>`
      : '';
    const artwork = poster.posterUrl
      ? `<img class="k-poster__image" src="${escapeHtml(poster.posterUrl)}" alt="" loading="lazy" decoding="async">`
      : `<span class="k-poster__fallback" aria-hidden="true">${placeholderLines}${placeholderFooter}</span>`;
    const watched = poster.state === 'watched' ? '<span class="k-poster__watched">Watched</span>' : '';
    const subtitle = poster.subtitle ? `<span class="k-poster__subtitle">${escapeHtml(poster.subtitle)}</span>` : '';
    return `<a class="k-poster k-poster--${escapeHtml(poster.state)}" href="${escapeHtml(poster.href)}" aria-label="${escapeHtml(poster.title)}" title="${escapeHtml(poster.title)}" data-kanvas-poster="${poster.id}">
      <span class="k-poster__art">${artwork}${progress}${watched}</span>
      <span class="k-poster__meta"><span class="k-poster__title">${escapeHtml(poster.title)}</span>${subtitle}</span>
    </a>`;
  };

  class KanvasPoster extends HTMLElement {
    static get observedAttributes() {
      return ['poster'];
    }

    connectedCallback() {
      this.render();
    }

    attributeChangedCallback() {
      if (this.isConnected) this.render();
    }

    set poster(value) {
      this.setAttribute('poster', typeof value === 'string' ? value : JSON.stringify(value));
    }

    render() {
      const rawPoster = this.getAttribute('poster');
      if (!rawPoster) {
        this.replaceChildren();
        return;
      }
      try {
        const poster = normalisePoster(JSON.parse(rawPoster));
        if (!poster) throw new TypeError('Invalid poster payload');
        this.innerHTML = posterMarkup(poster);
      } catch (_) {
        this.replaceChildren();
      }
    }
  }

  const posterElement = (value) => {
    const poster = normalisePoster(value);
    if (!poster) throw new TypeError('Invalid poster payload');
    const element = document.createElement('kanvas-poster');
    element.setAttribute('poster', JSON.stringify(poster));
    return element;
  };

  const gridColumnCount = (grid) => {
    const children = Array.from(grid.children);
    if (!children.length) return 1;
    const firstTop = children[0].getBoundingClientRect().top;
    const sameRow = children.findIndex((child) => Math.abs(child.getBoundingClientRect().top - firstTop) > GRID_ROW_TOP_TOLERANCE_PX);
    return Math.max(1, sameRow === -1 ? children.length : sameRow);
  };

  const trimOldestGridRows = (grid, maxMounted) => {
    const overflow = grid.children.length - maxMounted;
    if (overflow <= 0) return 0;
    const children = Array.from(grid.children);
    const columns = gridColumnCount(grid);
    const requestedCount = columns >= children.length ? overflow : Math.ceil(overflow / columns) * columns;
    const removeCount = Math.min(children.length - 1, requestedCount);
    const removed = children.slice(0, removeCount);
    if (!removed.length || removed.some((child) => child.contains(document.activeElement))) {
      return 0;
    }
    const anchor = children[removeCount] || null;
    const anchorTop = anchor?.getBoundingClientRect().top ?? null;
    for (const child of removed) child.remove();
    if (anchor && anchorTop !== null) window.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
    return removed.length;
  };

  const LIBRARY_GRID_SCHEMA_VERSION = 4;
  const LIBRARY_RESPONSE_SCHEMA_VERSION = 1;
  const libraryAssetVersion = () => {
    const scripts = Array.from(document.scripts);
    const script = scripts.find((candidate) => candidate.src.includes('/_kanvas/kanvas.js'));
    if (!script) return 'unversioned';
    return new URL(script.src, window.location.origin).searchParams.get('v') || 'unversioned';
  };

  const normalisedGridSource = (source) => {
    const url = new URL(source, window.location.origin);
    url.searchParams.delete('cursor');
    const entries = Array.from(url.searchParams.entries())
      .sort(([leftName, leftValue], [rightName, rightValue]) => leftName.localeCompare(rightName) || leftValue.localeCompare(rightValue));
    url.search = new URLSearchParams(entries).toString();
    return `${url.pathname}${url.search}`;
  };

  const libraryGridPayload = (payload) => {
    if (!payload || typeof payload !== 'object' || payload.schemaVersion !== LIBRARY_RESPONSE_SCHEMA_VERSION || !Array.isArray(payload.items)) {
      throw new LibraryLoadError('invalid_envelope');
    }
    if (payload.nextCursor != null && typeof payload.nextCursor !== 'string') {
      throw new LibraryLoadError('invalid_envelope');
    }
    const requestId = safeRequestId(payload.requestId);
    if (!requestId) throw new LibraryLoadError('invalid_envelope');
    const items = [];
    const invalidPosterIds = [];
    for (const item of payload.items) {
      const poster = normalisePoster(item);
      if (poster) items.push(poster);
      else invalidPosterIds.push(safePosterId(item));
    }
    return {items, invalidPosterIds, nextCursor: payload.nextCursor ?? null, requestId};
  };

  class KanvasPosterGrid extends HTMLElement {
    static get observedAttributes() {
      return ['source'];
    }

    constructor() {
      super();
      this.cursor = null;
      this.loading = false;
      this.done = false;
      this.observer = null;
      this.grid = null;
      this.status = null;
      this.sentinel = null;
      this.stateKey = null;
      this.posters = [];
      this.mountedStart = 0;
      this.requestController = null;
      this.generation = 0;
      this.requestId = null;
      this.invalidPosterCount = 0;
      this.retryRequired = false;
      this.hasSuccessfulPage = false;
      this.onPageHide = () => this.saveState();
    }

    connectedCallback() {
      this.initialise();
    }

    attributeChangedCallback(name, previous, current) {
      if (name === 'source' && this.isConnected && previous !== current) this.initialise();
    }

    disconnectedCallback() {
      this.generation += 1;
      this.requestController?.abort();
      this.requestController = null;
      this.observer?.disconnect();
      this.observer = null;
      window.removeEventListener('pagehide', this.onPageHide);
    }

    initialise() {
      this.generation += 1;
      this.requestController?.abort();
      this.requestController = null;
      this.observer?.disconnect();
      const source = this.getAttribute('source');
      this.cursor = null;
      this.done = false;
      this.loading = false;
      this.posters = [];
      this.mountedStart = 0;
      this.requestId = null;
      this.invalidPosterCount = 0;
      this.retryRequired = false;
      this.hasSuccessfulPage = false;
      this.stateKey = source ? this.buildStateKey(source) : null;
      this.innerHTML = '<div class="k-grid-status" aria-live="polite">Loading library…</div><div class="k-grid" aria-busy="true"></div><div class="k-grid-sentinel" aria-hidden="true"></div>';
      this.status = this.querySelector('.k-grid-status');
      this.grid = this.querySelector('.k-grid');
      this.sentinel = this.querySelector('.k-grid-sentinel');
      if (!source || !this.grid || !this.status || !this.sentinel) {
        if (this.status) this.status.textContent = 'The library grid could not be configured.';
        return;
      }
      this.observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) this.loadNext();
      }, {rootMargin: '640px 0px'});
      this.observer.observe(this.sentinel);
      window.removeEventListener('pagehide', this.onPageHide);
      window.addEventListener('pagehide', this.onPageHide);
      if (!this.restoreState()) this.loadNext();
    }

    buildStateKey(source) {
      const user = this.getAttribute('state-user') || 'anonymous';
      return `kanvas:grid:v${LIBRARY_GRID_SCHEMA_VERSION}:asset=${libraryAssetVersion()}:user=${encodeURIComponent(user)}:filters=${encodeURIComponent(normalisedGridSource(source))}`;
    }

    async loadNext({retry = false} = {}) {
      if (this.loading || this.done || this.retryRequired && !retry || !this.grid || !this.status) return;
      const source = this.getAttribute('source');
      if (!source) return;
      if (retry) this.retryRequired = false;
      const generation = this.generation;
      const controller = new AbortController();
      this.requestController?.abort();
      this.requestController = controller;
      this.loading = true;
      this.grid.setAttribute('aria-busy', 'true');
      this.status.textContent = this.posters.length ? 'Loading more…' : 'Loading library…';
      try {
        const url = new URL(source, window.location.origin);
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {
          headers: {'Accept': 'application/json'},
          credentials: 'same-origin',
          signal: controller.signal
        });
        const responseRequestId = safeRequestId(response.headers.get('X-Request-ID'));
        if (!response.ok) {
          throw await this.httpFailure(response, responseRequestId);
        }
        const contentType = response.headers.get('content-type') || '';
        if (!/^application\/json(?:\s*;|$)/i.test(contentType)) {
          throw new LibraryLoadError('invalid_content_type', {
            status: response.status,
            requestId: responseRequestId
          });
        }
        let documentPayload;
        try {
          documentPayload = await response.json();
        } catch (error) {
          throw new LibraryLoadError('invalid_json', {
            status: response.status,
            requestId: responseRequestId,
            cause: error
          });
        }
        let payload;
        try {
          payload = libraryGridPayload(documentPayload);
        } catch (error) {
          if (error instanceof LibraryLoadError) {
            error.status = response.status;
            error.requestId = error.requestId || responseRequestId;
            throw error;
          }
          throw new LibraryLoadError('invalid_envelope', {
            status: response.status,
            requestId: responseRequestId,
            cause: error
          });
        }
        if (generation !== this.generation) return;
        this.requestId = payload.requestId;
        if (payload.invalidPosterIds.length) this.reportInvalidPosters(payload.invalidPosterIds);
        this.invalidPosterCount += payload.invalidPosterIds.length;
        if (!payload.items.length && !this.posters.length && !payload.invalidPosterIds.length) {
          this.status.textContent = 'No items match these filters.';
        } else {
          try {
            const fragment = document.createDocumentFragment();
            for (const item of payload.items) fragment.append(posterElement(item));
            this.posters.push(...payload.items);
            this.grid.append(fragment);
            this.trimMountedPosters();
          } catch (error) {
            throw new LibraryLoadError('rendering_failure', {
              status: response.status,
              requestId: payload.requestId,
              cause: error
            });
          }
          this.status.textContent = this.pageStatus(payload.nextCursor);
        }
        this.cursor = payload.nextCursor;
        this.done = this.cursor === null;
        this.hasSuccessfulPage = true;
        this.retryRequired = false;
      } catch (error) {
        if (controller.signal.aborted || generation !== this.generation) return;
        const failure = error instanceof LibraryLoadError
          ? error
          : new LibraryLoadError('network_failure', {cause: error});
        this.requestId = failure.requestId || this.requestId;
        this.retryRequired = true;
        this.showFailure(failure);
        this.reportFailure(failure);
      } finally {
        if (generation !== this.generation) return;
        this.loading = false;
        this.requestController = null;
        this.grid?.setAttribute('aria-busy', 'false');
      }
    }

    async httpFailure(response, responseRequestId) {
      const contentType = response.headers.get('content-type') || '';
      let requestId = responseRequestId;
      if (/^application\/json(?:\s*;|$)/i.test(contentType)) {
        try {
          const body = await response.json();
          if (body && typeof body === 'object' && body.error && typeof body.error === 'object') {
            requestId = safeRequestId(body.error.requestId) || requestId;
          }
        } catch (error) {
          this.reportFailure(new LibraryLoadError('invalid_json', {
            status: response.status,
            requestId,
            cause: error
          }));
        }
      }
      return new LibraryLoadError('http_failure', {status: response.status, requestId});
    }

    pageStatus(nextCursor) {
      const invalid = this.invalidPosterCount
        ? `${this.invalidPosterCount} item${this.invalidPosterCount === 1 ? '' : 's'} could not be displayed.`
        : '';
      if (nextCursor !== null) return invalid;
      return invalid ? `${invalid} End of library.` : 'End of library.';
    }

    showFailure(failure) {
      if (!this.status) return;
      this.status.textContent = 'Could not load this part of the library.';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'k-button k-grid-retry';
      retry.textContent = 'Retry';
      retry.addEventListener('click', () => {
        retry.remove();
        this.loadNext({retry: true});
      }, {once: true});
      const diagnostic = document.createElement('details');
      diagnostic.className = 'k-grid-diagnostic';
      const summary = document.createElement('summary');
      summary.textContent = 'Details';
      const content = document.createElement('div');
      content.textContent = `Category: ${failure.category}\nHTTP status: ${failure.status ?? '—'}\nRequest ID: ${failure.requestId ?? '—'}`;
      diagnostic.append(summary, content);
      this.status.append(retry, diagnostic);
    }

    reportFailure(failure) {
      if (this.getAttribute('development-mode') === 'true') {
        console.error('Kanvas library load failed', {
          category: failure.category,
          status: failure.status,
          requestId: failure.requestId
        }, failure.cause || failure);
      }
    }

    reportInvalidPosters(itemIds) {
      if (this.getAttribute('development-mode') === 'true') {
        console.error('Kanvas library posters rejected', {itemIds: itemIds.filter((itemId) => itemId !== null)});
      }
    }

    trimMountedPosters() {
      if (!this.grid) return;
      this.mountedStart += trimOldestGridRows(this.grid, MAX_MOUNTED_POSTERS);
    }

    saveState() {
      if (!this.stateKey || !this.posters.length || !this.hasSuccessfulPage || this.retryRequired) {
        if (this.stateKey) sessionStorage.removeItem(this.stateKey);
        return;
      }
      sessionStorage.setItem(this.stateKey, JSON.stringify({
        schemaVersion: LIBRARY_GRID_SCHEMA_VERSION,
        asset: libraryAssetVersion(),
        filters: normalisedGridSource(this.getAttribute('source') || ''),
        user: this.getAttribute('state-user') || 'anonymous',
        cursor: this.cursor,
        completed: this.done,
        outcome: 'success',
        posters: this.posters,
        scrollY: window.scrollY
      }));
    }

    restoreState() {
      if (!this.stateKey || !this.grid || !this.status) return false;
      const stored = sessionStorage.getItem(this.stateKey);
      if (!stored) return false;
      try {
        const state = JSON.parse(stored);
        const expectedFilters = normalisedGridSource(this.getAttribute('source') || '');
        if (
          state.schemaVersion !== LIBRARY_GRID_SCHEMA_VERSION ||
          state.asset !== libraryAssetVersion() ||
          state.filters !== expectedFilters ||
          state.user !== (this.getAttribute('state-user') || 'anonymous') ||
          !Array.isArray(state.posters) ||
          !state.posters.length ||
          state.outcome !== 'success' ||
          typeof state.completed !== 'boolean' ||
          (state.cursor != null && typeof state.cursor !== 'string')
        ) throw new TypeError('Incompatible library grid state');
        const posters = state.posters.map(normalisePoster);
        if (posters.some((poster) => poster === null)) throw new TypeError('Invalid saved poster');
        this.posters = posters;
        this.mountedStart = Math.max(0, posters.length - MAX_MOUNTED_POSTERS);
        const mounted = document.createDocumentFragment();
        for (const poster of posters.slice(this.mountedStart)) mounted.append(posterElement(poster));
        this.grid.replaceChildren(mounted);
        this.cursor = state.cursor ?? null;
        this.done = state.completed;
        this.hasSuccessfulPage = true;
        this.retryRequired = false;
        this.grid.setAttribute('aria-busy', 'false');
        this.status.textContent = this.done ? 'End of library.' : '';
        if (Number.isFinite(state.scrollY)) requestAnimationFrame(() => window.scrollTo(0, state.scrollY));
        return true;
      } catch (_) {
        sessionStorage.removeItem(this.stateKey);
        return false;
      }
    }
  }

  if (!customElements.get('kanvas-poster')) customElements.define('kanvas-poster', KanvasPoster);
  if (!customElements.get('kanvas-poster-grid')) customElements.define('kanvas-poster-grid', KanvasPosterGrid);

  class KanvasOnboarding extends HTMLElement {
    connectedCallback() {
      const key = this.getAttribute('state-key') || 'default';
      const storageKey = `kanvas:onboarding:${key}`;
      if (sessionStorage.getItem(storageKey) === 'dismissed') {
        this.replaceChildren();
        return;
      }
      this.innerHTML = '<section class="k-onboarding" role="status"><div><strong>Artwork is not configured yet</strong><p>Your scanned library is ready to review. Configure TMDB, review scanner issues, then choose when to match and fetch artwork.</p><span class="k-action-row"><a class="k-button" href="/administration/hierarchy">Review scanner issues</a><a class="k-button" href="/administration/metadata">Configure TMDB</a><a class="k-button" href="/administration/artwork">Fetch artwork</a></span></div><button type="button" class="k-button" data-onboarding-dismiss>Dismiss</button></section>';
      this.querySelector('[data-onboarding-dismiss]')?.addEventListener('click', () => {
        sessionStorage.setItem(storageKey, 'dismissed');
        this.replaceChildren();
      });
    }
  }

  if (!customElements.get('kanvas-onboarding')) customElements.define('kanvas-onboarding', KanvasOnboarding);

  const movePosterFocus = (current, key) => {
    const grid = current.closest('.k-grid, .k-child-grid');
    if (!grid) return false;
    const posters = Array.from(grid.querySelectorAll('.k-poster'));
    const index = posters.indexOf(current);
    if (index < 0) return false;
    const columns = Math.max(1, Math.round(grid.clientWidth / Math.max(1, current.getBoundingClientRect().width + 10)));
    const offsets = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -columns, ArrowDown: columns};
    const target = posters[index + offsets[key]];
    if (!target) return false;
    target.focus();
    return true;
  };

  document.addEventListener('keydown', (event) => {
    const target = event.target;
    const editable = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
    if (event.key === '/' && !editable) {
      const search = document.querySelector('[data-kanvas-search]');
      if (search instanceof HTMLElement) { event.preventDefault(); search.focus(); }
      return;
    }
    if (event.key === 'Escape' && !editable) {
      const openDialog = document.querySelector('dialog[open]');
      if (openDialog instanceof HTMLDialogElement) openDialog.close();
      else if (window.location.pathname !== '/') window.history.back();
      return;
    }
    if (target instanceof HTMLElement && /^Arrow/.test(event.key) && target.matches('.k-poster')) {
      if (movePosterFocus(target, event.key)) event.preventDefault();
    }
    if (target instanceof HTMLElement && /^Arrow(Left|Right)$/.test(event.key) && target.matches('.k-rail__viewport')) {
      target.scrollBy({left: event.key === 'ArrowRight' ? 180 : -180, behavior: 'smooth'});
      event.preventDefault();
    }
  });

  const gamepadPrevious = new Map();
  const pollGamepads = () => {
    for (const gamepad of navigator.getGamepads?.() || []) {
      if (!gamepad) continue;
      const active = gamepad.buttons.map((button) => button.pressed);
      const previous = gamepadPrevious.get(gamepad.index) || [];
      const focus = document.activeElement;
      const send = (key) => focus?.dispatchEvent(new KeyboardEvent('keydown', {key, bubbles: true}));
      if (active[12] && !previous[12]) send('ArrowUp');
      if (active[13] && !previous[13]) send('ArrowDown');
      if (active[14] && !previous[14]) send('ArrowLeft');
      if (active[15] && !previous[15]) send('ArrowRight');
      if (active[0] && !previous[0] && focus instanceof HTMLElement) focus.click();
      if (active[1] && !previous[1]) send('Escape');
      gamepadPrevious.set(gamepad.index, active);
    }
    window.setTimeout(pollGamepads, 90);
  };
  if (Array.from(navigator.getGamepads?.() || []).some(Boolean)) pollGamepads();
  else window.addEventListener('gamepadconnected', pollGamepads, {once: true});

  const normaliseCollection = (value) => {
    if (!value || typeof value !== 'object') return null;
    const collection = value;
    if (!Number.isSafeInteger(collection.id) || collection.id <= 0) return null;
    if (typeof collection.name !== 'string' || !collection.name) return null;
    if (!Number.isInteger(collection.itemCount) || collection.itemCount < 0) return null;
    if (!Number.isInteger(collection.watchOrderCount) || collection.watchOrderCount < 0) return null;
    if (collection.artworkUrl != null && !localArtworkUrl(collection.artworkUrl)) return null;
    const mosaic = Array.isArray(collection.mosaicUrls) ? collection.mosaicUrls : [];
    if (mosaic.length > 4 || mosaic.some((url) => !localArtworkUrl(url))) return null;
    return {
      id: collection.id,
      name: collection.name,
      itemCount: collection.itemCount,
      watchOrderCount: collection.watchOrderCount,
      artworkUrl: collection.artworkUrl ?? null,
      mosaicUrls: mosaic
    };
  };

  const collectionMarkup = (collection) => {
    const art = collection.artworkUrl
      ? `<img class="k-collection-art__image" src="${escapeHtml(collection.artworkUrl)}" alt="" loading="lazy" decoding="async">`
      : collection.mosaicUrls.length
        ? `<span class="k-poster-mosaic" aria-hidden="true">${collection.mosaicUrls.map((url) => `<img class="k-poster-mosaic__image" src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async">`).join('')}</span>`
        : `<span class="k-collection-art__fallback">${escapeHtml(collection.name.slice(0, 1).toUpperCase())}</span>`;
    return `<a class="k-collection-tile" href="/collections/${collection.id}" aria-label="${escapeHtml(collection.name)}" data-kanvas-collection="${collection.id}">
      <span class="k-collection-art">${art}</span>
      <span class="k-collection-tile__meta"><span class="k-collection-tile__title">${escapeHtml(collection.name)}</span><span class="k-collection-tile__facts">${collection.itemCount} items · ${collection.watchOrderCount} orders</span></span>
    </a>`;
  };

  class KanvasCollectionGrid extends HTMLElement {
    constructor() {
      super();
      this.cursor = null;
      this.loading = false;
      this.done = false;
      this.observer = null;
      this.grid = null;
      this.status = null;
      this.sentinel = null;
    }

    connectedCallback() {
      if (!this.getAttribute('source')) return;
      this.innerHTML = '<div class="k-grid-status" aria-live="polite"></div><div class="k-collection-grid" aria-busy="true"></div><div class="k-grid-sentinel" aria-hidden="true"></div>';
      this.status = this.querySelector('.k-grid-status');
      this.grid = this.querySelector('.k-collection-grid');
      this.sentinel = this.querySelector('.k-grid-sentinel');
      this.observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) this.loadNext();
      }, {rootMargin: '640px 0px'});
      this.observer.observe(this.sentinel);
      this.loadNext();
    }

    disconnectedCallback() { this.observer?.disconnect(); }

    async loadNext() {
      if (this.loading || this.done || !this.grid || !this.status) return;
      const source = this.getAttribute('source');
      if (!source) return;
      this.loading = true;
      this.grid.setAttribute('aria-busy', 'true');
      this.status.textContent = this.grid.children.length ? 'Loading more…' : 'Loading collections…';
      try {
        const url = new URL(source, window.location.origin);
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        if (!response.ok) throw new Error(`Collection request failed (${response.status})`);
        const payload = await response.json();
        const collections = Array.isArray(payload.items) ? payload.items.map(normaliseCollection).filter(Boolean) : [];
        if (!collections.length && !this.grid.children.length) {
          this.status.textContent = 'No collections match this search.';
        } else {
          this.grid.insertAdjacentHTML('beforeend', collections.map(collectionMarkup).join(''));
          this.trimMountedCollections();
          this.status.textContent = payload.nextCursor ? '' : 'End of collections.';
        }
        this.cursor = typeof payload.nextCursor === 'string' ? payload.nextCursor : null;
        this.done = this.cursor === null;
      } catch (_) {
        this.status.textContent = 'Could not load collections.';
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'k-button k-grid-retry';
        retry.textContent = 'Retry';
        retry.addEventListener('click', () => { retry.remove(); this.loadNext(); }, {once: true});
        this.status.append(retry);
      } finally {
        this.loading = false;
        this.grid.setAttribute('aria-busy', 'false');
      }
    }

    trimMountedCollections() {
      if (!this.grid) return;
      trimOldestGridRows(this.grid, MAX_MOUNTED_POSTERS);
    }
  }

  const normalisePickerItem = (value) => {
    if (!value || typeof value !== 'object') return null;
    const item = value;
    if (!Number.isSafeInteger(item.id) || item.id <= 0 || typeof item.title !== 'string' || !item.title) return null;
    if (typeof item.kind !== 'string' || typeof item.available !== 'boolean' || typeof item.alreadyMember !== 'boolean') return null;
    if (item.year != null && (!Number.isInteger(item.year) || item.year < 1)) return null;
    if (item.posterUrl != null && !localArtworkUrl(item.posterUrl)) return null;
    return item;
  };

  class KanvasItemPicker extends HTMLElement {
    constructor() {
      super();
      this.cursor = null;
      this.revision = Number(this.getAttribute('revision')) || 0;
      this.pendingIntent = null;
      this.searchTimer = null;
      this.dialog = null;
      this.results = null;
      this.status = null;
    }

    connectedCallback() {
      const label = this.getAttribute('label') || 'Add item';
      this.innerHTML = `<button type="button" class="k-button" aria-haspopup="dialog">${escapeHtml(label)}</button><dialog class="k-kanvas-dialog"><div class="k-picker" role="document"><div class="k-picker__header"><label class="k-control-shell k-input-shell"><span class="k-sr-only">Search library</span><input class="k-input" type="search" data-picker-search aria-label="Search library" placeholder="Search library"></label><button type="button" class="k-button" data-picker-close>Close</button></div><div class="k-picker__status" aria-live="polite"></div><div class="k-picker__results" role="list"></div><button type="button" class="k-button" data-picker-more>Load more</button><div class="k-conflict-state" hidden aria-live="assertive"></div></div></dialog>`;
      this.dialog = this.querySelector('dialog');
      this.results = this.querySelector('.k-picker__results');
      this.status = this.querySelector('.k-picker__status');
      const open = this.querySelector('button');
      const close = this.querySelector('[data-picker-close]');
      const search = this.querySelector('[data-picker-search]');
      const more = this.querySelector('[data-picker-more]');
      open?.addEventListener('click', () => this.open());
      close?.addEventListener('click', () => this.dialog?.close());
      search?.addEventListener('input', () => {
        window.clearTimeout(this.searchTimer);
        this.searchTimer = window.setTimeout(() => this.resetAndLoad(), 180);
      });
      more?.addEventListener('click', () => this.loadNext());
      this.results?.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target.closest('[data-picker-add]') : null;
        if (target instanceof HTMLButtonElement) this.addItem(Number(target.dataset.pickerAdd));
      });
      window.kanvas = window.kanvas || {};
      window.kanvas.openPicker = () => this.open();
    }

    open() {
      if (!this.dialog) return;
      if (!this.dialog.open) this.dialog.showModal();
      this.resetAndLoad();
      this.querySelector('[data-picker-search]')?.focus();
    }

    resetAndLoad() {
      this.cursor = null;
      if (this.results) this.results.replaceChildren();
      this.loadNext();
    }

    async loadNext() {
      const source = this.getAttribute('source');
      if (!source || !this.results || !this.status) return;
      this.status.textContent = 'Loading items…';
      try {
        const url = new URL(source, window.location.origin);
        const search = this.querySelector('[data-picker-search]');
        if (search instanceof HTMLInputElement && search.value.trim()) url.searchParams.set('search', search.value.trim());
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        if (!response.ok) throw new Error('Picker request failed');
        const payload = await response.json();
        const items = Array.isArray(payload.items) ? payload.items.map(normalisePickerItem).filter(Boolean) : [];
        const fragment = document.createDocumentFragment();
        for (const item of items) {
          const row = document.createElement('div');
          row.className = 'k-picker-row';
          row.setAttribute('role', 'listitem');
          const year = item.year ? ` · ${item.year}` : '';
          const availability = item.available ? '' : ' · unavailable';
          row.innerHTML = `<span class="k-picker-row__title">${escapeHtml(item.title)}</span><span class="k-picker-row__facts">${escapeHtml(item.kind)}${year}${availability}</span><button type="button" class="k-button" data-picker-add="${item.id}" ${item.alreadyMember ? 'disabled aria-disabled="true"' : ''}>${item.alreadyMember ? 'Added' : 'Add'}</button>`;
          fragment.append(row);
        }
        this.results.append(fragment);
        this.cursor = typeof payload.nextCursor === 'string' ? payload.nextCursor : null;
        this.status.textContent = items.length ? '' : 'No matching library items.';
        const more = this.querySelector('[data-picker-more]');
        if (more instanceof HTMLButtonElement) more.hidden = this.cursor === null;
      } catch (_) {
        this.status.textContent = 'Could not load library items.';
      }
    }

    async addItem(itemId) {
      if (!Number.isSafeInteger(itemId) || itemId <= 0) return;
      const intent = {operation: 'add', itemId, revision: this.revision};
      const success = await this.mutate(intent);
      if (success) window.location.reload();
    }

    async mutate(intent) {
      const action = this.getAttribute('action');
      if (!action || !this.status) return false;
      this.status.textContent = 'Saving…';
      try {
        const response = await fetch(action, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify(intent)});
        const payload = await response.json();
        if (response.status === 409) {
          this.showConflict(payload, intent);
          return false;
        }
        if (!response.ok || !Number.isInteger(payload.revision)) throw new Error(payload.error || 'Action failed');
        this.revision = payload.revision;
        this.status.textContent = '';
        return true;
      } catch (_) {
        this.status.textContent = 'Could not save this change.';
        return false;
      }
    }

    showConflict(payload, intent) {
      this.pendingIntent = intent;
      const state = this.querySelector('.k-conflict-state');
      if (!state) return;
      const revision = Number.isInteger(payload.currentRevision) ? payload.currentRevision : null;
      state.hidden = false;
      state.innerHTML = '<span>This collection changed elsewhere.</span><button type="button" class="k-button" data-conflict-reload>Reload</button><button type="button" class="k-button" data-conflict-reapply>Reapply</button>';
      state.querySelector('[data-conflict-reload]')?.addEventListener('click', () => window.location.reload());
      state.querySelector('[data-conflict-reapply]')?.addEventListener('click', async () => {
        if (!this.pendingIntent || revision === null) return;
        const replay = {...this.pendingIntent, revision};
        if (await this.mutate(replay)) window.location.reload();
      });
    }
  }

  const normaliseWatchRow = (value) => {
    if (!value || typeof value !== 'object') return null;
    const row = value;
    if (!Number.isSafeInteger(row.id) || row.id <= 0 || !Number.isSafeInteger(row.itemId) || row.itemId <= 0) return null;
    if (!Number.isInteger(row.position) || row.position < 0 || typeof row.title !== 'string' || !row.title) return null;
    if (typeof row.kind !== 'string' || typeof row.available !== 'boolean') return null;
    if (row.year != null && (!Number.isInteger(row.year) || row.year < 1)) return null;
    if (row.posterUrl != null && !localArtworkUrl(row.posterUrl)) return null;
    return row;
  };

  class KanvasWatchOrderList extends HTMLElement {
    constructor() {
      super();
      this.cursor = null;
      this.revision = Number(this.getAttribute('revision')) || 0;
      this.loading = false;
      this.done = false;
      this.list = null;
      this.status = null;
      this.pendingIntent = null;
      this.draggedId = null;
    }

    connectedCallback() {
      this.innerHTML = '<div class="k-watch-list-status" aria-live="polite"></div><div class="k-watch-order-list" role="list" aria-label="Watch order"></div><button type="button" class="k-button k-watch-list-more">Load more</button><div class="k-conflict-state" hidden aria-live="assertive"></div>';
      this.list = this.querySelector('.k-watch-order-list');
      this.status = this.querySelector('.k-watch-list-status');
      this.querySelector('.k-watch-list-more')?.addEventListener('click', () => this.loadNext());
      this.list?.addEventListener('click', (event) => this.onClick(event));
      this.list?.addEventListener('keydown', (event) => this.onKeydown(event));
      this.list?.addEventListener('dragstart', (event) => this.onDragStart(event));
      this.list?.addEventListener('dragover', (event) => event.preventDefault());
      this.list?.addEventListener('drop', (event) => this.onDrop(event));
      this.loadNext();
    }

    async loadNext() {
      const source = this.getAttribute('source');
      if (!source || !this.list || !this.status || this.loading || this.done) return;
      this.loading = true;
      this.status.textContent = this.list.children.length ? 'Loading more…' : 'Loading entries…';
      try {
        const url = new URL(source, window.location.origin);
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        if (!response.ok) throw new Error('Watch order request failed');
        const payload = await response.json();
        const rows = Array.isArray(payload.items) ? payload.items.map(normaliseWatchRow).filter(Boolean) : [];
        if (Number.isInteger(payload.revision)) this.revision = payload.revision;
        this.list.insertAdjacentHTML('beforeend', rows.map((row) => this.rowMarkup(row)).join(''));
        this.trimRows();
        this.cursor = typeof payload.nextCursor === 'string' ? payload.nextCursor : null;
        this.done = this.cursor === null;
        this.status.textContent = rows.length ? '' : 'This watch order is empty.';
        const more = this.querySelector('.k-watch-list-more');
        if (more instanceof HTMLButtonElement) more.hidden = this.done;
      } catch (_) {
        this.status.textContent = 'Could not load watch-order entries.';
      } finally {
        this.loading = false;
      }
    }

    rowMarkup(row) {
      const year = row.year ? ` · ${row.year}` : '';
      const unavailable = row.available ? '' : '<span class="k-watch-row__warning">Unavailable</span>';
      return `<div class="k-watch-row" role="listitem" tabindex="0" draggable="true" data-entry-id="${row.id}" data-item-id="${row.itemId}"><span class="k-watch-row__position">${row.position + 1}</span><a class="k-watch-row__detail" href="/item/${row.itemId}"><span class="k-watch-row__title">${escapeHtml(row.title)}</span><span class="k-watch-row__facts">${escapeHtml(row.kind)}${year}</span></a>${unavailable}<span class="k-watch-row__actions"><button type="button" class="k-row-button" data-row-action="up" aria-label="Move entry up">↑</button><button type="button" class="k-row-button" data-row-action="down" aria-label="Move entry down">↓</button><button type="button" class="k-row-button" data-row-action="start" aria-label="Move entry to start">⇤</button><button type="button" class="k-row-button" data-row-action="end" aria-label="Move entry to end">⇥</button><button type="button" class="k-row-button" data-row-action="play" aria-label="Play from here">▶</button><button type="button" class="k-row-button" data-row-action="remove" aria-label="Remove entry">×</button></span></div>`;
    }

    trimRows() {
      if (!this.list) return;
      while (this.list.children.length > 120) {
        const first = this.list.firstElementChild;
        if (!first || first.contains(document.activeElement)) return;
        first.remove();
      }
    }

    onClick(event) {
      const target = event.target instanceof Element ? event.target.closest('[data-row-action]') : null;
      if (!(target instanceof HTMLButtonElement)) return;
      const row = target.closest('.k-watch-row');
      if (!(row instanceof HTMLElement)) return;
      const action = target.dataset.rowAction;
      if (action === 'up') this.moveRelative(row, -1);
      if (action === 'down') this.moveRelative(row, 1);
      if (action === 'start' || action === 'end') this.moveBoundary(row, action);
      if (action === 'remove') this.removeRow(row);
      if (action === 'play') this.playFromHere(row);
    }

    onKeydown(event) {
      const target = event.target;
      const row = target instanceof Element ? target.closest('.k-watch-row') : null;
      if (!(row instanceof HTMLElement) || target instanceof HTMLButtonElement) return;
      if (event.key === 'ArrowUp') { event.preventDefault(); this.moveRelative(row, -1); }
      if (event.key === 'ArrowDown') { event.preventDefault(); this.moveRelative(row, 1); }
      if (event.key === 'Home') { event.preventDefault(); this.moveBoundary(row, 'start'); }
      if (event.key === 'End') { event.preventDefault(); this.moveBoundary(row, 'end'); }
      if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); this.removeRow(row); }
      if (event.key === 'Enter') { event.preventDefault(); window.location.assign(`/item/${row.dataset.itemId}`); }
    }

    onDragStart(event) {
      const row = event.target instanceof Element ? event.target.closest('.k-watch-row') : null;
      if (!(row instanceof HTMLElement)) return;
      this.draggedId = row.dataset.entryId || null;
      event.dataTransfer?.setData('text/plain', this.draggedId || '');
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
    }

    onDrop(event) {
      event.preventDefault();
      const target = event.target instanceof Element ? event.target.closest('.k-watch-row') : null;
      if (!(target instanceof HTMLElement) || !this.draggedId || !this.list) return;
      const source = this.list.querySelector(`[data-entry-id="${CSS.escape(this.draggedId)}"]`);
      if (!(source instanceof HTMLElement) || source === target) return;
      const rows = Array.from(this.list.children);
      const targetIndex = rows.indexOf(target);
      if (targetIndex >= 0) this.moveToIndex(source, targetIndex);
      this.draggedId = null;
    }

    moveRelative(row, offset) {
      if (!this.list) return;
      const rows = Array.from(this.list.children);
      const index = rows.indexOf(row);
      const targetIndex = index + offset;
      if (index < 0 || targetIndex < 0 || targetIndex >= rows.length) return;
      this.moveToIndex(row, targetIndex);
    }

    async moveToIndex(row, targetIndex) {
      if (!this.list) return;
      const previousRows = Array.from(this.list.children);
      const sourceIndex = previousRows.indexOf(row);
      if (sourceIndex < 0 || sourceIndex === targetIndex) return;
      const reordered = [...previousRows];
      reordered.splice(sourceIndex, 1);
      reordered.splice(targetIndex, 0, row);
      this.list.replaceChildren(...reordered);
      const before = reordered[targetIndex + 1];
      const intent = {operation: 'move', entryId: Number(row.dataset.entryId), beforeEntryId: before ? Number(before.dataset.entryId) : null, afterEntryId: null, revision: this.revision};
      const success = await this.mutate(intent);
      if (!success && !this.pendingIntent) this.list.replaceChildren(...previousRows);
    }

    async moveBoundary(row, boundary) {
      const intent = {operation: 'move', entryId: Number(row.dataset.entryId), boundary, revision: this.revision};
      const success = await this.mutate(intent);
      if (success) window.location.reload();
    }

    async removeRow(row) {
      if (!this.list) return;
      const previousSibling = row.previousElementSibling;
      const nextSibling = row.nextElementSibling;
      row.remove();
      const success = await this.mutate({operation: 'remove', entryId: Number(row.dataset.entryId), revision: this.revision});
      if (!success && !this.pendingIntent) {
        if (nextSibling) this.list.insertBefore(row, nextSibling);
        else if (previousSibling) previousSibling.after(row);
        else this.list.append(row);
      }
    }

    async playFromHere(row) {
      const action = this.getAttribute('launch-action');
      if (!action || !this.status) return;
      this.status.textContent = 'Opening player…';
      try {
        const response = await fetch(action, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify({itemId: Number(row.dataset.itemId)})});
        const payload = await response.json();
        if (!response.ok || typeof payload.playbackUrl !== 'string' || !payload.playbackUrl.startsWith('/play/watch-orders/')) throw new Error('Launch failed');
        window.location.assign(payload.playbackUrl);
      } catch (_) {
        this.status.textContent = 'Could not start browser playback.';
      }
    }

    async mutate(intent) {
      const action = this.getAttribute('action');
      if (!action || !this.status) return false;
      this.setAttribute('aria-busy', 'true');
      this.status.textContent = 'Saving change…';
      try {
        const response = await fetch(action, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify(intent)});
        const payload = await response.json();
        if (response.status === 409) {
          this.showConflict(payload, intent);
          return false;
        }
        if (!response.ok || !Number.isInteger(payload.revision)) throw new Error(payload.error || 'Action failed');
        this.revision = payload.revision;
        this.status.textContent = '';
        return true;
      } catch (_) {
        this.status.textContent = 'Could not save this change.';
        return false;
      } finally {
        this.removeAttribute('aria-busy');
      }
    }

    showConflict(payload, intent) {
      this.pendingIntent = intent;
      const state = this.querySelector('.k-conflict-state');
      if (!state) return;
      const revision = Number.isInteger(payload.currentRevision) ? payload.currentRevision : null;
      state.hidden = false;
      state.innerHTML = '<span>This watch order changed elsewhere. Your local operation is still ready.</span><button type="button" class="k-button" data-conflict-reload>Reload</button><button type="button" class="k-button" data-conflict-reapply>Reapply</button>';
      state.querySelector('[data-conflict-reload]')?.addEventListener('click', () => window.location.reload());
      state.querySelector('[data-conflict-reapply]')?.addEventListener('click', async () => {
        if (!this.pendingIntent || revision === null) return;
        const replay = {...this.pendingIntent, revision};
        if (await this.mutate(replay)) window.location.reload();
      });
    }
  }

  if (!customElements.get('kanvas-collection-grid')) customElements.define('kanvas-collection-grid', KanvasCollectionGrid);
  if (!customElements.get('kanvas-item-picker')) customElements.define('kanvas-item-picker', KanvasItemPicker);
  if (!customElements.get('kanvas-watch-order-list')) customElements.define('kanvas-watch-order-list', KanvasWatchOrderList);

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

  const itemEditorKind = (value) => ITEM_EDITOR_KINDS.includes(value) ? value : 'movie';
  const itemEditorRelevantLocks = (kind) => ITEM_EDITOR_LOCK_FIELDS.filter((field) => field.kinds.includes(kind));
  const itemEditorNumberFields = (kind) => ITEM_EDITOR_NUMBER_FIELDS[kind] || [];
  const itemEditorItemValue = (item, value) => item?.[value] ?? '';

  class KanvasItemEditor extends HTMLElement {
    constructor() {
      super();
      this.dialog = null;
      this.status = null;
      this.controller = null;
      this.initialLocks = new Set();
      this.initialSelectedArtwork = new Map();
      this.currentItem = null;
    }

    connectedCallback() {
      this.innerHTML = '<button type="button" class="k-button" data-item-edit-open>Edit Details</button><dialog class="k-kanvas-dialog k-item-editor"><div class="k-picker" data-item-editor-content></div></dialog>';
      this.dialog = this.querySelector('dialog');
      this.querySelector('[data-item-edit-open]')?.addEventListener('click', () => this.open());
    }

    disconnectedCallback() { this.controller?.abort(); }

    async open() {
      if (!this.dialog) return;
      this.dialog.showModal();
      const content = this.querySelector('[data-item-editor-content]');
      if (!content) return;
      content.innerHTML = '<div class="k-picker__status" aria-live="polite">Loading editable metadata…</div>';
      this.controller?.abort();
      this.controller = new AbortController();
      const source = this.getAttribute('source');
      if (!source) return;
      try {
        const response = await fetch(source, {headers: {'Accept': 'application/json'}, credentials: 'same-origin', signal: this.controller.signal});
        if (!response.ok) throw new Error('Item editor request failed');
        const payload = await response.json();
        if (!payload.item || typeof payload.item !== 'object') throw new Error('Item editor response was invalid');
        this.render(payload.item, Array.isArray(payload.audit) ? payload.audit : []);
      } catch (error) {
        if (error?.name !== 'AbortError') content.innerHTML = '<div class="k-picker__status">This item could not be loaded for editing. Close and try again.</div>';
      }
    }

    render(item, audit) {
      const content = this.querySelector('[data-item-editor-content]');
      if (!content) return;
      this.currentItem = item;
      const selected = new Map((Array.isArray(item.selected_artwork) ? item.selected_artwork : []).map((entry) => [entry.kind, entry.artwork_id]));
      this.initialSelectedArtwork = selected;
      const locks = new Set(Array.isArray(item.locked_metadata_fields) ? item.locked_metadata_fields : []);
      this.initialLocks = locks;
      const artworks = Array.isArray(item.artwork) ? item.artwork : [];
      const artworkKinds = [...new Set(artworks.map((artwork) => artwork.kind))];
      const artworkRows = this.renderArtworkRows(artworks, artworkKinds, selected);
      const auditRows = audit.length ? audit.map((entry) => `<li>${escapeHtml(entry.actor || 'administrator')} · ${escapeHtml((entry.changed_fields || []).join(', ') || 'updated')} · ${escapeHtml(entry.occurred_at || '')}</li>`).join('') : '<li>No local edits have been recorded.</li>';
      const kind = itemEditorKind(item.kind);
      content.innerHTML = `<form class="k-item-editor__form" data-item-editor-form><div class="k-picker__header"><strong>Edit details</strong><button type="button" class="k-button" data-item-editor-close>Close</button></div><div class="k-item-editor__summary"><span>${escapeHtml(ITEM_EDITOR_KIND_LABELS[kind])}</span><span>${escapeHtml(item.title || `Item ${item.id || ''}`)}</span></div><section class="k-item-editor__section"><label class="k-control-shell k-input-shell"><input class="k-input" name="title" value="${escapeHtml(item.title || '')}" aria-label="Title" required></label><label class="k-control-shell k-input-shell"><input class="k-input" name="sortTitle" value="${escapeHtml(item.sort_title || '')}" aria-label="Sort title" required></label><label class="k-control-shell k-textarea-shell"><textarea class="k-textarea" name="overview" aria-label="Overview">${escapeHtml(item.overview || '')}</textarea></label></section><section class="k-item-editor__section"><div class="k-item-editor__grid"><label class="k-control-shell k-input-shell"><input class="k-input" type="date" name="releaseDate" value="${escapeHtml(item.release_date || '')}" aria-label="Release date"></label><label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="1" max="9999" name="releaseYear" value="${item.year || ''}" placeholder="Year" aria-label="Release year"></label></div><label class="k-control-shell k-input-shell"><input class="k-input" name="tags" value="${escapeHtml((item.tags || []).join(', '))}" aria-label="Tags" placeholder="Tags, comma separated"></label></section><section class="k-item-editor__section" data-item-editor-kind-fields>${this.renderKindFields(kind, item)}</section><details><summary>Metadata locks</summary><div class="k-item-editor__checks" data-item-editor-locks>${this.renderLockRows(kind, locks)}</div></details><details><summary>Selected artwork</summary><div class="k-item-editor__artwork-grid">${artworkRows}</div></details><details><summary>Advanced hierarchy</summary><div class="k-item-editor__grid"><label class="k-control-shell k-select-wrap"><select class="k-select" name="kind" aria-label="Kind" data-item-editor-kind>${ITEM_EDITOR_KINDS.map((kindOption) => `<option value="${kindOption}"${kindOption === kind ? ' selected' : ''}>${ITEM_EDITOR_KIND_LABELS[kindOption]}</option>`).join('')}</select></label><span data-item-editor-hierarchy-fields>${this.renderHierarchyFields(kind, item)}</span></div></details><details><summary>Edit audit</summary><ul class="k-item-editor__audit">${auditRows}</ul></details><div class="k-picker__status" data-item-editor-status aria-live="polite"></div><div class="k-action-row"><button type="submit" class="k-button k-button--primary">Save metadata</button></div></form>`;
      this.status = content.querySelector('[data-item-editor-status]');
      content.querySelector('[data-item-editor-close]')?.addEventListener('click', () => this.dialog?.close());
      content.querySelector('[data-item-editor-form]')?.addEventListener('submit', (event) => this.submit(event));
      content.querySelector('[data-item-editor-kind]')?.addEventListener('change', (event) => this.updateKindFields(event.currentTarget?.value));
    }

    renderKindFields(kind, item) {
      const fields = itemEditorNumberFields(kind);
      if (!fields.length) return '';
      return `<div class="k-item-editor__grid">${fields.map((field) => `<label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="0" name="${field.name}" value="${itemEditorItemValue(item, field.value)}" placeholder="${field.placeholder}" aria-label="${field.label}"></label>`).join('')}</div>`;
    }

    renderHierarchyFields(kind, item) {
      if (!ITEM_EDITOR_PARENT_KINDS.has(kind)) return '<span class="k-item-editor__muted">Top-level item</span>';
      return `<label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="1" name="parentId" value="${item.parent_id || ''}" placeholder="Parent ID" aria-label="Parent item ID"></label>`;
    }

    renderLockRows(kind, locks) {
      return itemEditorRelevantLocks(kind).map((field) => `<label class="k-check"><input type="checkbox" name="lock" value="${field.value}"${locks.has(field.value) ? ' checked' : ''}> ${field.label}</label>`).join('');
    }

    renderArtworkRows(artworks, artworkKinds, selected) {
      if (!artworks.length) return '<p class="k-quiet-copy">No cached artwork is available to select.</p>';
      return artworkKinds.map((kind) => {
        const automatic = `<label class="k-item-editor__artwork"><input type="radio" name="artwork-${escapeHtml(kind)}" value="" data-artwork-kind="${escapeHtml(kind)}"${selected.has(kind) ? '' : ' checked'}><span>Automatic ${escapeHtml(kind)}</span></label>`;
        const choices = artworks.filter((artwork) => artwork.kind === kind).map((artwork) => {
          const artworkUrl = typeof artwork.url === 'string'
            ? artwork.url.replace(/^\/api\/v1\/library\/items\/(\d+)\/artwork\/(\d+)$/, '/kanvas/artwork/$1/$2')
            : null;
          const image = artworkUrl && localArtworkUrl(artworkUrl) ? `<img src="${escapeHtml(artworkUrl)}" alt="">` : '';
          return `<label class="k-item-editor__artwork"><input type="radio" name="artwork-${escapeHtml(artwork.kind)}" value="${artwork.id}" data-artwork-kind="${escapeHtml(artwork.kind)}"${selected.get(artwork.kind) === artwork.id ? ' checked' : ''}><span>${image}${escapeHtml(artwork.kind)} #${artwork.id}</span></label>`;
        }).join('');
        return automatic + choices;
      }).join('');
    }

    updateKindFields(value) {
      const kind = itemEditorKind(value);
      const content = this.querySelector('[data-item-editor-content]');
      const item = this.currentItem || {};
      const fields = content?.querySelector('[data-item-editor-kind-fields]');
      const locks = content?.querySelector('[data-item-editor-locks]');
      const hierarchy = content?.querySelector('[data-item-editor-hierarchy-fields]');
      if (fields) fields.innerHTML = this.renderKindFields(kind, item);
      if (locks) locks.innerHTML = this.renderLockRows(kind, this.initialLocks);
      if (hierarchy) hierarchy.innerHTML = this.renderHierarchyFields(kind, item);
    }

    async submit(event) {
      event.preventDefault();
      const form = event.currentTarget;
      if (!(form instanceof HTMLFormElement) || !this.status) return;
      const values = new FormData(form);
      const payload = this.payloadFromForm(form, values);
      const button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      this.status.textContent = 'Saving metadata…';
      try {
        const source = this.getAttribute('action-source');
        if (!source) throw new Error('Missing item edit action');
        const response = await fetch(source, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify(payload)});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Item edit failed');
        this.status.textContent = `Saved ${result.audit?.changed_fields?.join(', ') || 'metadata'}.`;
        window.setTimeout(() => window.location.reload(), 450);
      } catch (error) {
        this.status.textContent = error?.message || 'Item edit could not be applied.';
        if (button) button.disabled = false;
      }
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
      const visibleLockValues = new Set(Array.from(form.querySelectorAll('input[name="lock"]')).map((input) => input.value));
      const lockedMetadataFields = Array.from(form.querySelectorAll('input[name="lock"]:checked')).map((input) => input.value);
      for (const lock of this.initialLocks) {
        if (!visibleLockValues.has(lock)) lockedMetadataFields.push(lock);
      }
      const payload = {
        title: String(values.get('title') || ''),
        sortTitle: String(values.get('sortTitle') || ''),
        overview: String(values.get('overview') || '').trim() || null,
        releaseDate: String(values.get('releaseDate') || '').trim() || null,
        releaseYear: toNullableNumber('releaseYear'),
        tags: String(values.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean),
        lockedMetadataFields,
        kind: String(values.get('kind') || '')
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
      if (visibleArtworkKinds.size) payload.selectedArtwork = selectedArtwork;
      return payload;
    }
  }

  if (!customElements.get('kanvas-item-editor')) customElements.define('kanvas-item-editor', KanvasItemEditor);

  class KanvasPlaybackPlayer extends HTMLElement {
    connectedCallback() {
      const video = this.querySelector('video');
      const status = this.querySelector('.k-player__status');
      const controls = this.querySelector('.k-player__controls');
      const timeline = this.querySelector('[data-player-timeline]');
      const currentTime = this.querySelector('[data-player-current-time]');
      const remainingTime = this.querySelector('[data-player-remaining-time]');
      const volume = this.querySelector('[data-player-volume]');
      const contextMenu = this.querySelector('[data-player-context-menu]');
      const nativeControls = this.querySelector('[data-player-native-controls]');
      const sessionId = this.getAttribute('session-id');
      const resumePosition = Number(this.getAttribute('resume-position') || '0');
      if (!video || !status || !controls || !timeline || !currentTime || !remainingTime || !volume || !contextMenu || !nativeControls || !sessionId || !Number.isFinite(resumePosition)) return;
      let lastReportedPosition = -1;
      let resumeApplied = false;
      let seeking = false;
      let completing = false;
      let reporting = false;
      let fullscreenHideTimer = null;
      const formatTime = (seconds) => {
        if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
        const totalSeconds = Math.floor(seconds);
        const minutes = Math.floor(totalSeconds / 60);
        const remainingSeconds = totalSeconds % 60;
        if (minutes < 60) return `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
        const hours = Math.floor(minutes / 60);
        return `${hours}:${String(minutes % 60).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
      };
      const actionButton = (action) => controls.querySelector(`[data-player-action="${action}"]`);
      const updateControls = () => {
        const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
        const position = Math.min(Math.max(video.currentTime || 0, 0), duration);
        timeline.max = String(duration);
        timeline.value = String(position);
        timeline.disabled = duration === 0;
        timeline.style.setProperty(
          '--progress-percent', `${duration === 0 ? 0 : (position / duration) * 100}%`
        );
        currentTime.textContent = formatTime(position);
        remainingTime.textContent = `-${formatTime(Math.max(duration - position, 0))}`;
        const toggle = actionButton('toggle');
        if (toggle) {
          toggle.innerHTML = video.paused ? '&#9654;' : '&#10074;&#10074;';
          toggle.setAttribute('aria-label', video.paused ? 'Play' : 'Pause');
        }
        const mute = actionButton('mute');
        if (mute) {
          mute.innerHTML = video.muted || video.volume === 0 ? '&#128263;' : '&#128266;';
          mute.setAttribute('aria-label', video.muted || video.volume === 0 ? 'Unmute' : 'Mute');
        }
        const fullscreen = actionButton('fullscreen');
        if (fullscreen) {
          const isFullscreen = document.fullscreenElement === this || document.fullscreenElement === video;
          fullscreen.innerHTML = isFullscreen ? '&#10005;' : '&#9974;';
          fullscreen.setAttribute('aria-label', isFullscreen ? 'Exit fullscreen' : 'Fullscreen');
        }
        contextMenu.querySelectorAll('[data-player-rate]').forEach((option) => {
          const rate = Number(option.getAttribute('data-player-rate'));
          option.setAttribute('aria-pressed', String(Math.abs(rate - video.playbackRate) < 0.01));
        });
        volume.value = String(video.muted ? 0 : video.volume);
        volume.style.setProperty('--volume-percent', `${video.muted ? 0 : video.volume * 100}%`);
      };
      const isCardFullscreen = () => document.fullscreenElement === this;
      const clearFullscreenHideTimer = () => {
        if (fullscreenHideTimer !== null) window.clearTimeout(fullscreenHideTimer);
        fullscreenHideTimer = null;
      };
      const showFullscreenControls = () => {
        if (!isCardFullscreen()) return;
        this.classList.remove('k-player--controls-hidden');
        clearFullscreenHideTimer();
        if (!video.paused && contextMenu.hidden) {
          fullscreenHideTimer = window.setTimeout(() => {
            if (isCardFullscreen() && !video.paused && contextMenu.hidden) {
              this.classList.add('k-player--controls-hidden');
            }
          }, 2600);
        }
      };
      const hideContextMenu = () => {
        contextMenu.hidden = true;
        showFullscreenControls();
      };
      const showContextMenu = (clientX, clientY) => {
        const bounds = this.getBoundingClientRect();
        contextMenu.hidden = false;
        showFullscreenControls();
        contextMenu.style.left = `${Math.max(8, Math.min(clientX - bounds.left, bounds.width - 210))}px`;
        contextMenu.style.top = `${Math.max(8, clientY - bounds.top)}px`;
      };
      const toggleFullscreen = async () => {
        try {
          const fullscreenElement = document.fullscreenElement;
          if (fullscreenElement === this || fullscreenElement === video) {
            await document.exitFullscreen();
          } else if (typeof this.requestFullscreen === 'function') {
            await this.requestFullscreen();
          } else if (typeof video.webkitEnterFullscreen === 'function') {
            video.controls = true;
            video.webkitEnterFullscreen();
          } else {
            status.textContent = 'Fullscreen is not available in this browser.';
          }
        } catch (_) {
          status.textContent = 'Could not enter fullscreen.';
        } finally {
          updateControls();
        }
      };
      const reportProgress = async (force, seek) => {
        if (!Number.isFinite(video.currentTime) || video.currentTime < 0) return;
        if (resumePosition > 0 && !resumeApplied) return;
        if (reporting) return;
        if (!force && video.currentTime - lastReportedPosition < 10) return;
        reporting = true;
        try {
          const response = await fetch(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/progress`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({positionSeconds: video.currentTime, seek}),
          });
          if (!response.ok) throw new Error('Progress failed');
          lastReportedPosition = video.currentTime;
        } catch (_) {
          status.textContent = 'Playback progress could not be saved.';
        } finally {
          reporting = false;
        }
      };
      controls.addEventListener('click', (event) => {
        showFullscreenControls();
        const element = event.target instanceof Element ? event.target : null;
        const target = element?.closest('[data-player-action]');
        if (!target) return;
        const action = target.getAttribute('data-player-action');
        if (action === 'toggle') {
          if (video.paused) void video.play().catch(() => { status.textContent = 'Select Play to start this video.'; });
          else video.pause();
        } else if (action === 'rewind' || action === 'forward') {
          const offset = action === 'rewind' ? -10 : 10;
          if (Number.isFinite(video.duration)) video.currentTime = Math.min(Math.max(video.currentTime + offset, 0), video.duration);
        } else if (action === 'menu') {
          const bounds = target.getBoundingClientRect();
          showContextMenu(bounds.left + bounds.width / 2, bounds.bottom);
        } else if (action === 'mute') {
          video.muted = !video.muted;
        } else if (action === 'fullscreen') {
          void toggleFullscreen();
        }
        updateControls();
      });
      contextMenu.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null;
        const option = element?.closest('[data-player-rate]');
        if (!option) return;
        const rate = Number(option.getAttribute('data-player-rate'));
        if (!Number.isFinite(rate)) return;
        video.playbackRate = rate;
        updateControls();
        hideContextMenu();
      });
      timeline.addEventListener('input', () => {
        showFullscreenControls();
        const position = Number(timeline.value);
        if (Number.isFinite(position)) video.currentTime = position;
        updateControls();
      });
      volume.addEventListener('input', () => {
        showFullscreenControls();
        const nextVolume = Number(volume.value);
        if (!Number.isFinite(nextVolume)) return;
        video.volume = Math.min(Math.max(nextVolume, 0), 1);
        video.muted = video.volume === 0;
        updateControls();
      });
      nativeControls.addEventListener('change', () => {
        video.controls = nativeControls.checked;
        hideContextMenu();
      });
      this.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        showContextMenu(event.clientX, event.clientY);
      });
      this.addEventListener('pointermove', showFullscreenControls);
      this.addEventListener('pointerdown', showFullscreenControls);
      this.addEventListener('touchstart', showFullscreenControls, {passive: true});
      this.addEventListener('keydown', showFullscreenControls);
      this.addEventListener('focusin', showFullscreenControls);
      const onPointerDown = (event) => {
        if (!contextMenu.contains(event.target)) hideContextMenu();
      };
      document.addEventListener('pointerdown', onPointerDown);
      this._dispose = () => {
        clearFullscreenHideTimer();
        document.removeEventListener('pointerdown', onPointerDown);
        document.removeEventListener('fullscreenchange', onFullscreenChange);
      };
      video.addEventListener('loadedmetadata', () => {
        if (!resumeApplied && resumePosition > 0 && Number.isFinite(video.duration)) {
          resumeApplied = true;
          video.currentTime = Math.min(resumePosition, video.duration);
        }
        status.textContent = '';
        updateControls();
      });
      video.addEventListener('play', () => {
        updateControls();
        showFullscreenControls();
      });
      video.addEventListener('pause', () => {
        updateControls();
        showFullscreenControls();
      });
      video.addEventListener('ratechange', updateControls);
      video.addEventListener('volumechange', updateControls);
      const onFullscreenChange = () => {
        updateControls();
        if (isCardFullscreen()) showFullscreenControls();
        else {
          clearFullscreenHideTimer();
          this.classList.remove('k-player--controls-hidden');
        }
      };
      document.addEventListener('fullscreenchange', onFullscreenChange);
      video.addEventListener('webkitbeginfullscreen', updateControls);
      video.addEventListener('webkitendfullscreen', updateControls);
      video.addEventListener('timeupdate', () => {
        updateControls();
        void reportProgress(false, false);
      });
      video.addEventListener('seeking', () => { seeking = true; });
      video.addEventListener('seeked', () => {
        void reportProgress(true, seeking);
        seeking = false;
      });
      video.addEventListener('pause', () => { void reportProgress(true, false); });
      video.addEventListener('error', () => {
        status.textContent = 'This video format is not supported by this browser.';
      });
      video.addEventListener('ended', async () => {
        if (completing) return;
        completing = true;
        status.textContent = 'Completing playback…';
        try {
          const response = await fetch(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/complete`, {
            method: 'POST', headers: {'Accept': 'application/json'}, credentials: 'same-origin'
          });
          const payload = await response.json();
          if (!response.ok) throw new Error('Completion failed');
          if (typeof payload.nextUrl === 'string') window.location.assign(payload.nextUrl);
          else status.textContent = 'Playback complete.';
        } catch (_) {
          completing = false;
          status.textContent = 'Playback completion could not be saved.';
        }
      });
      updateControls();
    }

    disconnectedCallback() {
      if (this._dispose) this._dispose();
    }
  }

  if (!customElements.get('kanvas-playback-player')) customElements.define('kanvas-playback-player', KanvasPlaybackPlayer);

  window.kanvasInternals = {escapeHtml, jobDetail};

})();
