(() => {
  'use strict';

  const MAX_MOUNTED_POSTERS = 144;
  const GRID_ROW_TOP_TOLERANCE_PX = 1;
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
    if (poster.subtitle != null && typeof poster.subtitle !== 'string') return null;
    if (poster.progressPercent != null && (!Number.isInteger(poster.progressPercent) || poster.progressPercent < 0 || poster.progressPercent > 100)) return null;
    if (typeof poster.state !== 'string' || !POSTER_STATES.has(poster.state)) return null;
    return {
      id: poster.id,
      title: poster.title,
      href: poster.href,
      posterUrl: poster.posterUrl ?? null,
      subtitle: poster.subtitle ?? null,
      progressPercent: poster.progressPercent ?? null,
      state: poster.state,
      available: poster.available
    };
  };

  const fallbackGrapheme = (title) => {
    const graphemes = typeof Intl.Segmenter === 'function'
      ? Array.from(new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(title), ({segment}) => segment)
      : Array.from(title);
    const useful = graphemes.find((grapheme) => /[\p{L}\p{N}]/u.test(grapheme));
    return useful ? useful.toLocaleUpperCase() : '•';
  };

  const fallbackPattern = (id) => ((id * 2654435761) >>> 0) % 4;

  const posterMarkup = (poster) => {
    const progress = poster.progressPercent == null ? '' :
      `<span class="k-progress" aria-label="Playback progress"><span class="k-progress__value" style="--k-progress:${poster.progressPercent}%"></span></span>`;
    const artwork = poster.posterUrl
      ? `<img class="k-poster__image" src="${escapeHtml(poster.posterUrl)}" alt="" loading="lazy" decoding="async">`
      : `<span class="k-poster__fallback k-poster__fallback--${fallbackPattern(poster.id)}" aria-hidden="true">${escapeHtml(fallbackGrapheme(poster.title))}</span>`;
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
        if (!response.ok || typeof payload.launchUri !== 'string' || !payload.launchUri.startsWith('kasana://play/')) throw new Error('Launch failed');
        await window.kanvas.launch(payload.launchUri);
        this.status.textContent = 'Player launch requested.';
      } catch (_) {
        this.status.textContent = 'Could not create a playback plan.';
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

  class KanvasItemEditor extends HTMLElement {
    constructor() {
      super();
      this.dialog = null;
      this.status = null;
      this.controller = null;
    }

    connectedCallback() {
      this.innerHTML = '<button type="button" class="k-button" data-item-edit-open>Edit details</button><dialog class="k-kanvas-dialog k-item-editor"><div class="k-picker" data-item-editor-content></div></dialog>';
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
      const selected = new Map((Array.isArray(item.selected_artwork) ? item.selected_artwork : []).map((entry) => [entry.kind, entry.artwork_id]));
      const locks = new Set(Array.isArray(item.locked_metadata_fields) ? item.locked_metadata_fields : []);
      const artworks = Array.isArray(item.artwork) ? item.artwork : [];
      const artworkKinds = [...new Set(artworks.map((artwork) => artwork.kind))];
      const artworkRows = artworks.length ? artworkKinds.map((kind) => {
        const automatic = `<label class="k-item-editor__artwork"><input type="radio" name="artwork-${escapeHtml(kind)}" value="" data-artwork-kind="${escapeHtml(kind)}"${selected.has(kind) ? '' : ' checked'}><span>Automatic ${escapeHtml(kind)}</span></label>`;
        const choices = artworks.filter((artwork) => artwork.kind === kind).map((artwork) => {
          const artworkUrl = typeof artwork.url === 'string'
            ? artwork.url.replace(/^\/api\/v1\/library\/items\/(\d+)\/artwork\/(\d+)$/, '/kanvas/artwork/$1/$2')
            : null;
          const image = artworkUrl && localArtworkUrl(artworkUrl) ? `<img src="${escapeHtml(artworkUrl)}" alt="">` : '';
          return `<label class="k-item-editor__artwork"><input type="radio" name="artwork-${escapeHtml(artwork.kind)}" value="${artwork.id}" data-artwork-kind="${escapeHtml(artwork.kind)}"${selected.get(artwork.kind) === artwork.id ? ' checked' : ''}><span>${image}${escapeHtml(artwork.kind)} #${artwork.id}</span></label>`;
        }).join('');
        return automatic + choices;
      }).join('') : '<p class="k-quiet-copy">No cached artwork is available to select.</p>';
      const metadataFields = [['title', 'Title'], ['sort_title', 'Sort title'], ['release_date', 'Release date'], ['overview', 'Overview'], ['season_number', 'Season number'], ['episode_number', 'Episode number']];
      const lockRows = metadataFields.map(([value, label]) => `<label class="k-check"><input type="checkbox" name="lock" value="${value}"${locks.has(value) ? ' checked' : ''}> ${label}</label>`).join('');
      const auditRows = audit.length ? audit.map((entry) => `<li>${escapeHtml(entry.actor || 'administrator')} · ${escapeHtml((entry.changed_fields || []).join(', ') || 'updated')} · ${escapeHtml(entry.occurred_at || '')}</li>`).join('') : '<li>No local edits have been recorded.</li>';
      const kinds = ['movie', 'series', 'season', 'episode', 'special', 'extra'];
      content.innerHTML = `<form class="k-item-editor__form" data-item-editor-form><div class="k-picker__header"><strong>Edit catalogue metadata</strong><button type="button" class="k-button" data-item-editor-close>Close</button></div><p class="k-quiet-copy">Edits affect catalogue metadata only. They never rename, move, or delete media files.</p><label class="k-control-shell k-input-shell"><input class="k-input" name="title" value="${escapeHtml(item.title || '')}" aria-label="Title" required></label><label class="k-control-shell k-input-shell"><input class="k-input" name="sortTitle" value="${escapeHtml(item.sort_title || '')}" aria-label="Sort title" required></label><label class="k-control-shell k-textarea-shell"><textarea class="k-textarea" name="overview" aria-label="Overview">${escapeHtml(item.overview || '')}</textarea></label><div class="k-action-row"><label class="k-control-shell k-input-shell"><input class="k-input" type="date" name="releaseDate" value="${escapeHtml(item.release_date || '')}" aria-label="Release date"></label><label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="1" max="9999" name="releaseYear" value="${item.year || ''}" placeholder="Year" aria-label="Release year"></label></div><label class="k-control-shell k-input-shell"><input class="k-input" name="tags" value="${escapeHtml((item.tags || []).join(', '))}" aria-label="Tags" placeholder="Tags, comma separated"></label><div class="k-action-row"><label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="0" name="seasonNumber" value="${item.season_number ?? ''}" placeholder="Season" aria-label="Season number"></label><label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="0" name="episodeNumber" value="${item.episode_number ?? ''}" placeholder="Episode" aria-label="Episode number"></label></div><details><summary>Metadata locks</summary><div class="k-item-editor__checks">${lockRows}</div></details><details><summary>Selected artwork</summary><div class="k-item-editor__artwork-grid">${artworkRows}</div></details><details><summary>Advanced hierarchy</summary><p class="k-quiet-copy">Changing kind or parent is validated before it is saved. Existing media files are preserved.</p><div class="k-action-row"><label class="k-control-shell k-select-wrap"><select class="k-select" name="kind" aria-label="Kind">${kinds.map((kind) => `<option value="${kind}"${kind === item.kind ? ' selected' : ''}>${kind}</option>`).join('')}</select></label><label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="1" name="parentId" value="${item.parent_id || ''}" placeholder="Parent ID" aria-label="Parent item ID"></label></div></details><details><summary>Edit audit</summary><ul class="k-item-editor__audit">${auditRows}</ul></details><div class="k-picker__status" data-item-editor-status aria-live="polite"></div><div class="k-action-row"><button type="submit" class="k-button k-button--primary">Save metadata</button></div></form>`;
      this.status = content.querySelector('[data-item-editor-status]');
      content.querySelector('[data-item-editor-close]')?.addEventListener('click', () => this.dialog?.close());
      content.querySelector('[data-item-editor-form]')?.addEventListener('submit', (event) => this.submit(event));
    }

    async submit(event) {
      event.preventDefault();
      const form = event.currentTarget;
      if (!(form instanceof HTMLFormElement) || !this.status) return;
      const values = new FormData(form);
      const toNullableNumber = (name) => {
        const raw = String(values.get(name) || '').trim();
        return raw ? Number(raw) : null;
      };
      const selectedArtwork = Array.from(form.querySelectorAll('[data-artwork-kind]:checked'))
        .filter((input) => input.value)
        .map((input) => ({kind: input.dataset.artworkKind, artworkId: Number(input.value)}));
      const payload = {
        title: String(values.get('title') || ''),
        sortTitle: String(values.get('sortTitle') || ''),
        overview: String(values.get('overview') || '').trim() || null,
        releaseDate: String(values.get('releaseDate') || '').trim() || null,
        releaseYear: toNullableNumber('releaseYear'),
        tags: String(values.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean),
        seasonNumber: toNullableNumber('seasonNumber'),
        episodeNumber: toNullableNumber('episodeNumber'),
        lockedMetadataFields: Array.from(form.querySelectorAll('input[name="lock"]:checked')).map((input) => input.value),
        selectedArtwork,
        kind: String(values.get('kind') || ''),
        parentId: toNullableNumber('parentId')
      };
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
  }

  if (!customElements.get('kanvas-item-editor')) customElements.define('kanvas-item-editor', KanvasItemEditor);

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

  window.kanvas = window.kanvas || {};
  window.kanvas.launch = (uri) => new Promise((resolve) => {
    let hidden = false;
    const onVisibility = () => { hidden = document.visibilityState === 'hidden'; };
    document.addEventListener('visibilitychange', onVisibility, {once: true});
    window.location.assign(uri);
    window.setTimeout(() => resolve(hidden || document.hasFocus() === false), 850);
  });
})();
