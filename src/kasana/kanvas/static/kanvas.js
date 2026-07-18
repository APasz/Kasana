(() => {
  'use strict';

  const MAX_MOUNTED_POSTERS = 144;
  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);

  const posterMarkup = (poster) => {
    const progress = poster.progressPercent == null ? '' :
      `<span class="k-progress" aria-label="Playback progress"><span class="k-progress__value" style="--k-progress:${poster.progressPercent}%"></span></span>`;
    const artwork = poster.posterUrl
      ? `<img class="k-poster__image" src="${escapeHtml(poster.posterUrl)}" alt="" loading="lazy" decoding="async">`
      : `<span class="k-poster__fallback">${escapeHtml(poster.title.slice(0, 1).toUpperCase())}</span>`;
    const watched = poster.state === 'watched' ? '<span class="k-poster__watched">Watched</span>' : '';
    const subtitle = poster.subtitle ? `<span class="k-poster__subtitle">${escapeHtml(poster.subtitle)}</span>` : '';
    return `<a class="k-poster k-poster--${escapeHtml(poster.state)}" href="${escapeHtml(poster.href)}" aria-label="${escapeHtml(poster.title)}" data-kanvas-poster="${poster.id}">
      <span class="k-poster__art">${artwork}${progress}${watched}</span>
      <span class="k-poster__meta"><span class="k-poster__title">${escapeHtml(poster.title)}</span>${subtitle}</span>
    </a>`;
  };

  class KanvasPosterGrid extends HTMLElement {
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
      this.onPageHide = () => this.saveState();
    }

    connectedCallback() {
      const source = this.getAttribute('source');
      if (!source) return;
      this.stateKey = `kanvas:grid:${source}`;
      this.innerHTML = '<div class="k-grid-status" aria-live="polite"></div><div class="k-grid" aria-busy="true"></div><div class="k-grid-sentinel" aria-hidden="true"></div>';
      this.status = this.querySelector('.k-grid-status');
      this.grid = this.querySelector('.k-grid');
      this.sentinel = this.querySelector('.k-grid-sentinel');
      this.observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) this.loadNext();
      }, {rootMargin: '640px 0px'});
      this.observer.observe(this.sentinel);
      window.addEventListener('pagehide', this.onPageHide);
      if (!this.restoreState()) this.loadNext();
    }

    disconnectedCallback() {
      this.observer?.disconnect();
      window.removeEventListener('pagehide', this.onPageHide);
    }

    async loadNext() {
      if (this.loading || this.done || !this.grid || !this.status) return;
      const source = this.getAttribute('source');
      if (!source) return;
      this.loading = true;
      this.grid.setAttribute('aria-busy', 'true');
      this.status.textContent = this.grid.children.length ? 'Loading more…' : 'Loading library…';
      try {
        const url = new URL(source, window.location.origin);
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        if (!response.ok) throw new Error(`Library request failed (${response.status})`);
        const payload = await response.json();
        const items = Array.isArray(payload.items) ? payload.items : [];
        if (!items.length && !this.grid.children.length) {
          this.status.textContent = 'No items match these filters.';
        } else {
          const fragment = document.createDocumentFragment();
          for (const item of items) {
            const template = document.createElement('template');
            template.innerHTML = posterMarkup(item).trim();
            fragment.append(template.content.firstChild);
          }
          this.grid.append(fragment);
          this.trimMountedPosters();
          this.status.textContent = payload.nextCursor ? '' : 'End of library.';
        }
        this.cursor = typeof payload.nextCursor === 'string' ? payload.nextCursor : null;
        this.done = this.cursor === null;
      } catch (error) {
        this.status.textContent = 'Could not load this part of the library.';
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

    trimMountedPosters() {
      if (!this.grid) return;
      while (this.grid.children.length > MAX_MOUNTED_POSTERS) {
        const first = this.grid.firstElementChild;
        if (!first || first.contains(document.activeElement)) return;
        const height = first.getBoundingClientRect().height;
        first.remove();
        window.scrollBy(0, -height);
      }
    }

    saveState() {
      if (!this.stateKey || !this.grid) return;
      sessionStorage.setItem(this.stateKey, JSON.stringify({
        cursor: this.cursor,
        done: this.done,
        posters: this.grid.innerHTML,
        scrollY: window.scrollY
      }));
    }

    restoreState() {
      if (!this.stateKey || !this.grid || !this.status) return false;
      const stored = sessionStorage.getItem(this.stateKey);
      if (!stored) return false;
      try {
        const state = JSON.parse(stored);
        if (typeof state.posters !== 'string' || typeof state.done !== 'boolean') return false;
        this.grid.innerHTML = state.posters;
        this.cursor = typeof state.cursor === 'string' ? state.cursor : null;
        this.done = state.done;
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

  if (!customElements.get('kanvas-poster-grid')) customElements.define('kanvas-poster-grid', KanvasPosterGrid);

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

  window.kanvas = window.kanvas || {};
  window.kanvas.launch = (uri) => new Promise((resolve) => {
    let hidden = false;
    const onVisibility = () => { hidden = document.visibilityState === 'hidden'; };
    document.addEventListener('visibilitychange', onVisibility, {once: true});
    window.location.assign(uri);
    window.setTimeout(() => resolve(hidden || document.hasFocus() === false), 850);
  });
})();
