(() => {
  'use strict';
  if (customElements.get('kanvas-watch-order-workspace')) return;
  const {escapeHtml, publishKanvasToast} = window.kanvasInternals;
  const PAGE_SIZE = 100;
  const pad = (value) => String(value).padStart(2, '0');
  const button = (action, label, attributes = '') => `<button type="button" class="k-button${action === 'save' ? ' k-button--primary' : ''}" data-order-action="${action}" ${attributes}>${label}</button>`;

  /** @typedef {{id: number, title: string, kind: 'movie'|'series'|'season'|'episode'|'special'|'extra', available: boolean, parentId: number|null, seriesTitle: string|null, seasonNumber: number|null, episodeNumber: number|null, episodeEndNumber: number|null, episodeEndSeasonNumber: number|null, year: number|null, itemIds?: number[]}} OrderItem */
  /** @typedef {{ids: number[], position: number}} OrderGroup */

  const episodeCode = (item) => item.episodeNumber == null ? '' :
    `${item.seasonNumber == null ? '' : `S${pad(item.seasonNumber)} `}E${pad(item.episodeNumber)}${item.episodeEndNumber != null && (item.episodeEndNumber !== item.episodeNumber || (item.episodeEndSeasonNumber != null && item.episodeEndSeasonNumber !== item.seasonNumber)) ? `–${item.episodeEndSeasonNumber != null && item.episodeEndSeasonNumber !== item.seasonNumber ? `S${pad(item.episodeEndSeasonNumber)} E` : ''}${pad(item.episodeEndNumber)}` : ''}`;
  const itemContext = (item) => item.kind === 'series' ? String(item.year || '') : item.kind === 'season' ? item.seriesTitle || '' : [item.seriesTitle, episodeCode(item) || item.year].filter(Boolean).join(' · ');
  const itemLabel = (item) => [itemContext(item), item.title].filter(Boolean).join(' · ');

  /** Keep only adjacent, consecutive files together; dates never change a saved sequence. */
  const consecutive = (previous, next) => previous.kind === 'episode' && next.kind === 'episode'
    && previous.parentId != null && previous.parentId === next.parentId
    && previous.seasonNumber != null
    && previous.seasonNumber === next.seasonNumber
    && (previous.episodeEndSeasonNumber == null || previous.episodeEndSeasonNumber === previous.seasonNumber)
    && previous.episodeNumber != null && next.episodeNumber != null
    && (previous.episodeEndNumber ?? previous.episodeNumber) + 1 === next.episodeNumber;

  /** @param {number[]} ids @param {Map<number, OrderItem>} items @returns {OrderGroup[]} */
  const groupOrder = (ids, items) => {
    const groups = [];
    for (const [position, id] of ids.entries()) {
      const last = groups.at(-1);
      if (last && consecutive(items.get(last.ids.at(-1)), items.get(id))) last.ids.push(id);
      else groups.push({ids: [id], position});
    }
    return groups;
  };

  const groupLabel = (group, items) => {
    const first = items.get(group.ids[0]);
    if (group.ids.length === 1) return {title: first.title, detail: itemContext(first) || first.kind};
    const last = items.get(group.ids.at(-1));
    return {title: first.seriesTitle || first.title, detail: `S${pad(first.seasonNumber ?? 0)} E${pad(first.episodeNumber)}–${pad(last.episodeEndNumber ?? last.episodeNumber)} · ${group.ids.length} files`};
  };

  /** @param {number[]} ids @param {Set<number>} selected @param {number|null} before */
  const moveItems = (ids, selected, before) => {
    if (before !== null && selected.has(before)) return ids;
    const moving = ids.filter((id) => selected.has(id));
    const remaining = ids.filter((id) => !selected.has(id));
    const index = before === null ? remaining.length : remaining.indexOf(before);
    if (index < 0) throw new Error('The destination is no longer in this order.');
    return [...remaining.slice(0, index), ...moving, ...remaining.slice(index)];
  };

  const parseItem = (value, entry = false) => {
    if (!value || !Number.isSafeInteger(entry ? value.itemId : value.id) || (entry ? value.itemId : value.id) <= 0
      || typeof value.title !== 'string' || !value.title || typeof value.kind !== 'string' || typeof value.available !== 'boolean') {
      throw new Error('Invalid watch-order data. Reload and try again.');
    }
    return {...value, id: entry ? value.itemId : value.id};
  };

  const sequenceMarkup = (ids, items) => `<ol class="k-order-preview-list">${groupOrder(ids, items).map((group) => {
    const label = groupLabel(group, items);
    const unavailable = group.ids.filter((id) => !items.get(id).available).length;
    return `<li value="${group.position + 1}">${escapeHtml(label.title)} <small>${escapeHtml(label.detail)}${unavailable ? ` · ${unavailable} unavailable` : ''}</small></li>`;
  }).join('')}</ol>`;

  class OrderConflictError extends Error {
    constructor() { super('This order changed elsewhere. Your draft is here. Review the saved order before retrying.'); }
  }

  async function orderRequest(url, payload) {
    const response = await fetch(url, {
      method: payload ? 'POST' : 'GET', credentials: 'same-origin',
      headers: {'Accept': 'application/json', ...(payload ? {'Content-Type': 'application/json'} : {})},
      ...(payload ? {body: JSON.stringify(payload)} : {})
    });
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error('Could not load or save this order. Reload and try again.'); }
    if (response.status === 409) throw new OrderConflictError();
    if (!response.ok) throw new Error(result.error || 'Could not load or save this order.');
    return result;
  }

  class KanvasWatchOrderList extends HTMLElement {
    constructor() {
      super();
      this.cursors = [null];
      this.page = 0;
      this.loading = false;
    }

    connectedCallback() {
      this.innerHTML = `<p class="k-watch-list-status" role="status"></p>${button('retry', 'Retry', 'hidden')}<div class="k-watch-order-list" role="list" aria-label="Watch order"></div><div class="k-order-pagination">${button('previous', 'Previous')}${button('next', 'Next')}</div>`;
      this.addEventListener('click', (event) => {
        const target = event.target.closest('[data-order-action]');
        if (!target || this.loading) return;
        if (target.dataset.orderAction === 'previous' && this.page > 0) void this.load(this.page - 1);
        if (target.dataset.orderAction === 'next' && this.cursors[this.page + 1]) void this.load(this.page + 1);
        if (target.dataset.orderAction === 'retry') void this.load(this.retryPage ?? 0);
      });
      void this.load(0);
    }

    async load(page) {
      if (this.loading) return;
      this.loading = true;
      const status = this.querySelector('[role="status"]');
      status.textContent = 'Loading…';
      this.querySelectorAll('button').forEach((element) => { element.disabled = true; });
      const retry = this.querySelector('[data-order-action="retry"]');
      retry.hidden = true;
      try {
        const url = new URL(this.getAttribute('source'), window.location.origin);
        if (this.cursors[page]) url.searchParams.set('cursor', this.cursors[page]);
        const payload = await orderRequest(url);
        if (this.revision != null && payload.revision !== this.revision && page !== 0) {
          this.cursors = [null];
          this.page = 0;
          status.textContent = 'This order changed. Return to the first page.';
          this.querySelector('.k-watch-order-list').replaceChildren();
          this.retryPage = 0;
          retry.hidden = false;
          retry.disabled = false;
          return;
        }
        this.revision = payload.revision;
        this.page = page;
        this.cursors[page + 1] = payload.nextCursor || null;
        const rows = payload.items.map((value) => ({...parseItem(value, true), position: value.position}));
        const orderId = this.getAttribute('source').split('/').at(-1);
        this.querySelector('.k-watch-order-list').innerHTML = rows.map((item) => `<div class="k-watch-row" role="listitem">
          <span class="k-watch-row__position">${item.position + 1}</span><a class="k-order-identity" href="/item/${item.id}"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(itemContext(item) || item.kind)}</small></a>
          ${item.available ? `<a class="k-button" href="/play/watch-orders/${orderId}?itemId=${item.id}" aria-label="Play from ${escapeHtml(itemLabel(item))}">Play</a>` : '<small class="k-watch-row__warning">Unavailable</small>'}</div>`).join('');
        status.textContent = rows.length ? `${rows[0].position + 1}–${rows.at(-1).position + 1}` : 'No entries yet.';
        this.querySelector('[data-order-action="previous"]').disabled = page === 0;
        this.querySelector('[data-order-action="next"]').disabled = !payload.nextCursor;
        this.querySelector('.k-order-pagination').hidden = page === 0 && !payload.nextCursor;
      } catch (error) {
        status.textContent = error.message;
        this.retryPage = page;
        retry.hidden = false;
        retry.disabled = false;
      } finally { this.loading = false; }
    }
  }

  class KanvasWatchOrderWorkspace extends HTMLElement {
    constructor() {
      super();
      /** @type {Map<number, OrderItem>} */
      this.items = new Map();
      this.sources = [];
      this.ids = [];
      this.selected = new Set();
      this.expanded = new Set();
      this.history = [];
      this.page = 0;
      this.sourceParent = null;
      this.sourcePage = 0;
      this.busy = false;
      this.baseline = null;
      this.preview = null;
      this.failed = false;
      this.conflict = false;
      this.reviewedRevision = null;
      this.beforeUnload = (event) => {
        if (!this.dirty()) return;
        event.preventDefault();
        event.returnValue = '';
      };
    }

    connectedCallback() {
      this.innerHTML = `<section class="k-order-editor" aria-label="Watch-order editor">
        <div class="k-order-toolbar"><label class="k-order-name"><span class="k-sr-only">Order name</span><input class="k-input" data-order-name maxlength="1000" aria-label="Order name" required></label>
          <label><span class="k-sr-only">Order kind</span><select class="k-select" data-order-kind aria-label="Order kind"></select></label>
          ${button('undo', 'Undo')}${button('save', 'Save')}
        </div>
        <div class="k-order-message"><p class="k-order-status" data-order-status role="status">Loading…</p>${button('reload', 'Reload', 'hidden')}${button('compare', 'Review saved order', 'hidden')}</div>
        <section data-order-conflict hidden aria-label="Saved order"><div data-saved-order></div>${button('retry-save', 'Save my draft')}</section>
        <div class="k-order-columns">
          <details class="k-order-picker" open><summary>Add titles</summary>
            <input class="k-input" type="search" placeholder="Search collection" aria-label="Search collection" data-source-search>
            <div class="k-order-source-path"></div><div class="k-order-sources"></div>
            <div class="k-order-pagination">${button('source-previous', 'Previous')}${button('source-next', 'Next')}</div>
            <form class="k-order-range" hidden><strong>Add episode range</strong>
              <label>From<select class="k-select" data-range-from></select></label><label>To<select class="k-select" data-range-to></select></label>
              <button type="submit" class="k-button">Add range</button>
            </form>
          </details>
          <section class="k-order-sequence" aria-label="Sequence">
            <div class="k-order-selection"><span data-order-count></span><span data-order-selected></span>${button('clear-selection', 'Clear')}${button('up', '↑', 'aria-label="Move selection up"')}${button('down', '↓', 'aria-label="Move selection down"')}${button('remove', 'Remove')}
              <label class="k-order-destination">Move before<select class="k-select" aria-label="Move before" data-order-destination></select></label>${button('move', 'Move')}
            </div>
            <p class="k-order-hint">Add before a selection. Expand ranges to insert between episodes.</p>
            <div class="k-order-rows" role="list" aria-label="Draft watch order"></div>
            <div class="k-order-pagination">${button('previous', 'Previous')}<span data-order-page></span>${button('next', 'Next')}</div>
          </section>
        </div>
        <details class="k-order-dates"><summary>Use dates</summary><div class="k-action-row">
          <select class="k-select" data-date-mode aria-label="Date order"><option value="release">Release dates</option><option value="air">Air dates</option></select>
          <select class="k-select" data-date-apply aria-label="Use generated order"><option value="replace">Replace order</option><option value="merge">Add missing</option></select>${button('preview', 'Preview')}
        </div><div data-order-preview hidden></div></details>
      </section>`;
      this.addEventListener('click', (event) => this.onClick(event));
      this.addEventListener('change', (event) => this.onChange(event));
      this.querySelector('[data-order-name]').addEventListener('input', () => this.renderState());
      this.querySelector('[data-source-search]').addEventListener('input', () => { this.sourcePage = 0; this.renderSources(); });
      this.querySelector('.k-order-range').addEventListener('submit', (event) => { event.preventDefault(); this.addRange(); });
      this.addEventListener('keydown', (event) => {
        if (event.altKey && ['ArrowUp', 'ArrowDown'].includes(event.key) && !this.busy) {
          event.preventDefault(); this.moveStep(event.key === 'ArrowUp' ? -1 : 1);
        }
      });
      this.addEventListener('dragstart', (event) => {
        const row = event.target.closest('[data-group]');
        if (!row || this.busy) return;
        const group = this.visibleRows.find((candidate) => candidate.ids[0] === Number(row.dataset.group));
        if (!group) return;
        if (!group.ids.every((id) => this.selected.has(id))) this.selected = new Set(group.ids);
        event.dataTransfer.setData('application/x-kanvas-order', 'selection');
        event.dataTransfer.effectAllowed = 'move';
      });
      this.addEventListener('dragover', (event) => {
        if (event.target.closest('[data-group]') && event.dataTransfer.types.includes('application/x-kanvas-order')) event.preventDefault();
      });
      this.addEventListener('drop', (event) => {
        const row = event.target.closest('[data-group]');
        if (!row || !event.dataTransfer.types.includes('application/x-kanvas-order') || this.busy) return;
        event.preventDefault(); this.moveBefore(Number(row.dataset.group));
      });
      window.addEventListener('beforeunload', this.beforeUnload);
      void this.load();
    }

    disconnectedCallback() { window.removeEventListener('beforeunload', this.beforeUnload); }
    snapshot() { return {ids: [...this.ids], name: this.querySelector('[data-order-name]').value, kind: this.querySelector('[data-order-kind]').value}; }
    dirty() { return this.baseline !== null && JSON.stringify(this.snapshot()) !== this.baseline; }
    status(message) { this.querySelector('[data-order-status]').textContent = message; }

    async load() {
      this.busy = true;
      this.renderState();
      try {
        const payload = await orderRequest(this.getAttribute('source'));
        this.revision = payload.revision;
        this.entryLimit = payload.entryLimit;
        this.sources = payload.sources.map((item) => parseItem(item));
        for (const source of this.sources) this.items.set(source.id, source);
        this.ids = payload.entries.map((entry) => {
          const item = parseItem(entry, true); this.items.set(item.id, {...this.items.get(item.id), ...item}); return item.id;
        });
        this.querySelector('[data-order-name]').value = payload.name;
        this.querySelector('[data-order-kind]').innerHTML = payload.kinds.map((kind) => `<option value="${escapeHtml(kind)}">${escapeHtml(kind[0].toUpperCase() + kind.slice(1))}</option>`).join('');
        this.querySelector('[data-order-kind]').value = payload.kind;
        this.baseline = JSON.stringify(this.snapshot());
        if (this.ids.length && window.matchMedia('(max-width: 1000px)').matches) this.querySelector('.k-order-picker').open = false;
        this.status('');
      } catch (error) { this.failed = true; this.status(error.message); }
      finally { this.busy = false; this.render(); }
    }

    setSequence(ids) {
      if (ids.length > this.entryLimit) { this.status(`Orders support up to ${this.entryLimit.toLocaleString()} entries.`); return false; }
      if (JSON.stringify(ids) === JSON.stringify(this.ids)) return false;
      this.history.push(this.snapshot());
      if (this.history.length > 50) this.history.shift();
      this.ids = ids;
      this.preview = null;
      this.querySelector('[data-order-preview]').hidden = true;
      this.selected = new Set([...this.selected].filter((id) => ids.includes(id)));
      this.status('Unsaved changes');
      this.render();
      return true;
    }

    render() { this.renderOrder(); this.renderSources(); this.renderState(); }

    renderState() {
      const disabled = this.busy || this.baseline === null;
      this.querySelectorAll('input, select, button').forEach((element) => { element.disabled = disabled; });
      this.querySelector('[data-order-action="save"]').disabled = disabled || !this.dirty();
      this.querySelector('[data-order-action="save"]').disabled ||= this.conflict;
      this.querySelector('[data-order-action="reload"]').hidden = !this.failed;
      this.querySelector('[data-order-action="reload"]').disabled = this.busy;
      this.querySelector('[data-order-action="compare"]').hidden = !this.conflict;
      this.querySelector('[data-order-action="retry-save"]').disabled = disabled || this.reviewedRevision === null;
      this.querySelector('[data-order-action="undo"]').disabled = disabled || !this.history.length;
      for (const action of ['clear-selection', 'up', 'down', 'move', 'remove']) this.querySelector(`[data-order-action="${action}"]`).disabled = disabled || !this.selected.size;
      this.querySelector('[data-order-action="previous"]').disabled = disabled || this.page === 0;
      this.querySelector('[data-order-action="next"]').disabled = disabled || (this.page + 1) * PAGE_SIZE >= (this.visibleRows?.length || 0);
      this.querySelector('[data-order-action="source-previous"]').disabled = disabled || this.sourcePage === 0;
      this.querySelector('[data-order-action="source-next"]').disabled = disabled || (this.sourcePage + 1) * PAGE_SIZE >= (this.filteredSources?.length || 0);
      this.querySelector('[data-order-action="previous"]').parentElement.hidden = (this.visibleRows?.length || 0) <= PAGE_SIZE;
      this.querySelector('[data-order-action="source-previous"]').parentElement.hidden = (this.filteredSources?.length || 0) <= PAGE_SIZE;
      this.querySelectorAll('[data-source-empty]').forEach((element) => { element.disabled = true; });
      this.setAttribute('aria-busy', String(this.busy));
    }

    renderOrder() {
      const active = document.activeElement;
      const focusGroup = active?.closest('[data-group]');
      const focusId = focusGroup?.dataset.group;
      const focusChild = focusGroup?.classList.contains('k-order-row--child');
      const focusSelect = active?.matches('[data-order-select]');
      this.groups = groupOrder(this.ids, this.items);
      this.visibleRows = this.groups.flatMap((group) => this.expanded.has(group.ids[0]) && group.ids.length > 1
        ? [{...group, heading: true}, ...group.ids.map((id, index) => ({ids: [id], position: group.position + index, child: true}))] : [group]);
      this.page = Math.min(this.page, Math.max(0, Math.ceil(this.visibleRows.length / PAGE_SIZE) - 1));
      const rows = this.visibleRows.slice(this.page * PAGE_SIZE, (this.page + 1) * PAGE_SIZE);
      this.querySelector('[data-order-count]').textContent = `${this.ids.length} entries`;
      this.querySelector('[data-order-selected]').textContent = this.selected.size ? `${this.selected.size} selected` : '';
      this.querySelector('[data-order-page]').textContent = this.visibleRows.length > PAGE_SIZE ? `${this.page + 1} / ${Math.ceil(this.visibleRows.length / PAGE_SIZE)}` : '';
      this.querySelector('.k-order-rows').innerHTML = rows.length ? rows.map((group) => {
        const label = groupLabel(group, this.items);
        const selected = group.ids.every((id) => this.selected.has(id));
        const missing = group.ids.filter((id) => !this.items.get(id).available).length;
        return `<div class="k-order-row${group.child ? ' k-order-row--child' : ''}" role="listitem" data-group="${group.ids[0]}" ${group.heading ? 'data-heading' : ''} draggable="true">
          <input type="checkbox" data-order-select="${group.ids[0]}" aria-label="Select ${escapeHtml(label.title + ' · ' + label.detail)}" ${selected ? 'checked' : ''}>
          <span class="k-watch-row__position">${group.position + 1}${group.ids.length > 1 ? `–${group.position + group.ids.length}` : ''}</span>
          <div class="k-order-identity"><strong>${escapeHtml(label.title)}</strong><small>${escapeHtml(label.detail)}${missing ? ` · ${missing === group.ids.length ? 'Unavailable' : `${missing} unavailable`}` : ''}</small></div>
          ${group.ids.length > 1 ? button('expand', group.heading ? 'Collapse' : 'Expand', `aria-expanded="${Boolean(group.heading)}" data-group-id="${group.ids[0]}"`) : ''}</div>`;
      }).join('') : '<p class="k-order-hint">Add titles from this collection, or start with release dates.</p>';
      for (const [index, row] of this.querySelectorAll('[data-group]').entries()) {
        const group = rows[index];
        row.querySelector('input').indeterminate = group.ids.some((id) => this.selected.has(id)) && !group.ids.every((id) => this.selected.has(id));
      }
      const destination = this.querySelector('[data-order-destination]');
      const previousDestination = destination.value;
      destination.innerHTML = this.visibleRows.filter((group) => !group.heading && !group.ids.some((id) => this.selected.has(id))).map((group) => {
        const label = groupLabel(group, this.items);
        return `<option value="${group.ids[0]}">${group.position + 1}. ${escapeHtml(label.title)} · ${escapeHtml(label.detail)}</option>`;
      }).join('') + '<option value="end">End</option>';
      if ([...destination.options].some((option) => option.value === previousDestination)) destination.value = previousDestination;
      if (focusId) {
        const row = this.querySelector(`${focusChild ? '.k-order-row--child' : '.k-order-row:not(.k-order-row--child)'}[data-group="${focusId}"]`) || this.querySelector(`[data-group="${focusId}"]`);
        row?.querySelector(focusSelect ? 'input' : 'button')?.focus({preventScroll: true});
      }
    }

    renderSources() {
      const active = document.activeElement;
      const focusId = active?.dataset.sourceId;
      const focusAction = active?.dataset.orderAction;
      const search = this.querySelector('[data-source-search]').value.trim().toLocaleLowerCase();
      const knownSources = new Set(this.sources.map((source) => source.id));
      this.filteredSources = this.sources.filter((source) => search
        ? itemLabel(source).toLocaleLowerCase().includes(search)
        : this.sourceParent === null ? !knownSources.has(source.parentId) : source.parentId === this.sourceParent);
      this.sourcePage = Math.min(this.sourcePage, Math.max(0, Math.ceil(this.filteredSources.length / PAGE_SIZE) - 1));
      const selectedParent = this.items.get(this.sourceParent);
      this.querySelector('.k-order-source-path').innerHTML = selectedParent && !search
        ? `${button('source-back', 'Back')}<strong>${escapeHtml(itemLabel(selectedParent))}</strong>` : '';
      const current = new Set(this.ids);
      this.querySelector('.k-order-sources').innerHTML = this.filteredSources.slice(this.sourcePage * PAGE_SIZE, (this.sourcePage + 1) * PAGE_SIZE).map((source) => {
        const remaining = source.itemIds.filter((id) => !current.has(id)).length;
        const children = this.sources.some((candidate) => candidate.parentId === source.id);
        return `<div class="k-order-source"><div class="k-order-identity"><strong>${escapeHtml(source.title)}</strong><small>${escapeHtml(itemContext(source) || source.kind)}${source.itemIds.length > 1 ? ` · ${remaining} to add` : ''}</small></div>
          ${children ? button('browse', 'Browse', `data-source-id="${source.id}"`) : ''}
          ${button('add', remaining ? 'Add' : source.itemIds.length ? 'Added' : 'Empty', `data-source-id="${source.id}"${remaining ? '' : ' data-source-empty'}`)}</div>`;
      }).join('') || '<p class="k-order-hint">No matching titles.</p>';
      const range = this.querySelector('.k-order-range');
      range.hidden = !selectedParent || selectedParent.kind !== 'season' || Boolean(search) || !selectedParent.itemIds?.length;
      if (!range.hidden) {
        const options = selectedParent.itemIds.map((id) => `<option value="${id}">${escapeHtml(episodeCode(this.items.get(id)) + ' · ' + this.items.get(id).title)}</option>`).join('');
        for (const selector of ['[data-range-from]', '[data-range-to]']) {
          const input = range.querySelector(selector);
          const previous = input.value;
          input.innerHTML = options;
          input.value = selectedParent.itemIds.includes(Number(previous)) ? previous : String(selector.includes('from') ? selectedParent.itemIds[0] : selectedParent.itemIds.at(-1));
        }
      }
      if (focusId && focusAction) this.querySelector(`[data-source-id="${focusId}"][data-order-action="${focusAction}"]`)?.focus({preventScroll: true});
    }

    onChange(event) {
      if (this.busy) return;
      const input = event.target;
      if (input.matches('[data-order-select]')) {
        const group = this.visibleRows.find((row) => row.ids[0] === Number(input.dataset.orderSelect) && Boolean(row.heading) === input.closest('[data-group]').hasAttribute('data-heading'));
        if (!group) return;
        const anchor = this.ids.indexOf(this.selectionAnchor);
        const targets = this.extendSelection && anchor >= 0
          ? this.ids.slice(Math.min(anchor, group.position), Math.max(anchor, group.position + group.ids.length - 1) + 1)
          : group.ids;
        for (const id of targets) { if (input.checked) this.selected.add(id); else this.selected.delete(id); }
        this.renderOrder();
      }
      if (input.matches('[data-date-mode], [data-date-apply]')) {
        this.preview = null;
        this.querySelector('[data-order-preview]').hidden = true;
      }
      this.renderState();
    }

    onClick(event) {
      if (event.target.matches('[data-order-select]')) {
        this.extendSelection = event.shiftKey;
        if (!event.shiftKey) this.selectionAnchor = Number(event.target.dataset.orderSelect);
        return;
      }
      const target = event.target.closest('[data-order-action]');
      if (!target || this.busy) return;
      const action = target.dataset.orderAction;
      if (action === 'reload') { window.location.reload(); return; }
      if (action === 'compare') { void this.reviewSaved(); return; }
      if (action === 'retry-save' && this.reviewedRevision !== null) {
        this.revision = this.reviewedRevision;
        this.reviewedRevision = null;
        this.conflict = false;
        void this.save(); return;
      }
      if (action === 'save') { void this.save(); return; }
      if (action === 'preview') { void this.previewDates(); return; }
      if (action === 'use-preview' && this.preview) this.setSequence(this.preview);
      if (action === 'cancel-preview') this.querySelector('[data-order-preview]').hidden = true;
      if (action === 'undo' && this.history.length) {
        const snapshot = this.history.pop();
        this.ids = snapshot.ids; this.querySelector('[data-order-name]').value = snapshot.name; this.querySelector('[data-order-kind]').value = snapshot.kind;
        this.selected.clear(); this.preview = null; this.querySelector('[data-order-preview]').hidden = true; this.status(this.dirty() ? 'Unsaved changes' : '');
      }
      if (action === 'expand') { const id = Number(target.dataset.groupId); if (this.expanded.has(id)) this.expanded.delete(id); else this.expanded.add(id); }
      if (action === 'clear-selection') this.selected.clear();
      if (action === 'previous') this.page = Math.max(0, this.page - 1);
      if (action === 'next') this.page += 1;
      if (action === 'source-previous') this.sourcePage = Math.max(0, this.sourcePage - 1);
      if (action === 'source-next') this.sourcePage += 1;
      if (action === 'browse') { this.sourceParent = Number(target.dataset.sourceId); this.sourcePage = 0; this.querySelector('[data-source-search]').value = ''; }
      if (action === 'source-back') { this.sourceParent = this.sources.some((source) => source.id === this.items.get(this.sourceParent)?.parentId) ? this.items.get(this.sourceParent).parentId : null; this.sourcePage = 0; }
      if (action === 'add') this.addItems(this.items.get(Number(target.dataset.sourceId)).itemIds);
      if (action === 'remove') this.setSequence(this.ids.filter((id) => !this.selected.has(id)));
      if (action === 'up' || action === 'down') this.moveStep(action === 'up' ? -1 : 1);
      if (action === 'move') { const value = this.querySelector('[data-order-destination]').value; this.moveBefore(value === 'end' ? null : Number(value)); }
      this.render();
    }

    addItems(ids) {
      const current = new Set(this.ids);
      const additions = ids.filter((id) => !current.has(id));
      if (!additions.length) { this.status('Already in this order.'); return; }
      const before = this.ids.findIndex((id) => this.selected.has(id));
      const index = before < 0 ? this.ids.length : before;
      if (!this.setSequence([...this.ids.slice(0, index), ...additions, ...this.ids.slice(index)])) return;
      this.status(`${additions.length} added${before < 0 ? '' : ' before selection'} · Unsaved changes`);
    }

    addRange() {
      if (this.busy) return;
      const ids = this.items.get(this.sourceParent)?.itemIds || [];
      const first = ids.indexOf(Number(this.querySelector('[data-range-from]').value));
      const last = ids.indexOf(Number(this.querySelector('[data-range-to]').value));
      if (first < 0 || last < first) { this.status('Choose a range in episode order.'); return; }
      this.addItems(ids.slice(first, last + 1)); this.render();
    }

    moveBefore(before) { if (this.selected.size) this.setSequence(moveItems(this.ids, this.selected, before)); }

    moveStep(direction) {
      if (!this.selected.size) return;
      const rows = this.visibleRows.filter((row) => !row.heading);
      const selectedIndexes = rows.flatMap((row, index) => row.ids.some((id) => this.selected.has(id)) ? [index] : []);
      const edge = direction < 0 ? selectedIndexes[0] : selectedIndexes.at(-1);
      if (edge == null || (direction < 0 && edge === 0) || (direction > 0 && edge === rows.length - 1)) return;
      this.moveBefore(direction < 0 ? rows[edge - 1].ids[0] : rows[edge + 2]?.ids[0] ?? null);
    }

    async save() {
      if (this.busy || !this.dirty()) return;
      const name = this.querySelector('[data-order-name]');
      if (!name.value.trim()) { name.reportValidity(); name.focus(); return; }
      const draft = this.snapshot();
      this.busy = true; this.renderState(); this.status('Saving…');
      try {
        const result = await orderRequest(this.getAttribute('action'), {operation: 'save', revision: this.revision, name: draft.name, kind: draft.kind, itemIds: draft.ids});
        this.revision = result.revision;
        this.baseline = JSON.stringify(draft);
        this.history = [];
        this.failed = false;
        this.conflict = false;
        this.querySelector('[data-order-conflict]').hidden = true;
        document.querySelectorAll('input[data-watch-order-revision]').forEach((input) => { input.value = String(result.revision); });
        this.status('Saved'); publishKanvasToast({severity: 'success', title: 'Watch order saved'});
        const title = document.querySelector('[data-watch-order-title]');
        if (title) title.textContent = draft.name.trim();
        const facts = document.querySelector('[data-watch-order-facts]');
        if (facts) facts.textContent = `${draft.ids.length} entries · ${draft.kind}`;
      } catch (error) {
        this.failed = true;
        this.conflict = error instanceof OrderConflictError;
        this.reviewedRevision = null;
        this.querySelector('[data-order-conflict]').hidden = true;
        this.status(error.message);
      }
      finally { this.busy = false; this.renderState(); }
    }

    async reviewSaved() {
      if (this.busy) return;
      this.busy = true; this.renderState();
      try {
        const saved = await orderRequest(this.getAttribute('source'));
        const items = new Map(saved.entries.map((entry) => { const item = parseItem(entry, true); return [item.id, item]; }));
        this.reviewedRevision = saved.revision;
        this.querySelector('[data-saved-order]').innerHTML = `<h3>${escapeHtml(saved.name)}</h3><p>${items.size} saved entries · ${escapeHtml(saved.kind)}</p>${sequenceMarkup(saved.entries.map((entry) => entry.itemId), items)}<p>Save my draft replaces this saved order with your current draft.</p>`;
        this.querySelector('[data-order-conflict]').hidden = false;
      } catch (error) { this.status(error.message); }
      finally { this.busy = false; this.renderState(); }
    }

    async previewDates() {
      if (this.busy) return;
      this.busy = true; this.renderState(); this.status('Loading dates…');
      try {
        const mode = this.querySelector('[data-date-mode]').value;
        const result = await orderRequest(this.getAttribute('action'), {operation: 'preview', mode, revision: this.revision});
        const generatedIds = result.entries.map((entry) => { const item = parseItem(entry, true); this.items.set(item.id, {...this.items.get(item.id), ...item}); return item.id; });
        const current = new Set(this.ids);
        this.preview = this.querySelector('[data-date-apply]').value === 'merge' ? [...this.ids, ...generatedIds.filter((id) => !current.has(id))] : generatedIds;
        const preview = this.querySelector('[data-order-preview]');
        preview.hidden = false;
        preview.innerHTML = `<p>${this.preview.length} entries${result.undatedTitles.length ? ` · ${result.undatedTitles.length} without dates, placed last` : ''}${result.unavailableTitles.length ? ` · ${result.unavailableTitles.length} unavailable` : ''}</p>
          ${sequenceMarkup(this.preview, this.items)}
          ${result.undatedItemIds.length ? `<details><summary>Missing dates (${result.undatedItemIds.length})</summary><ul class="k-order-preview-list">${result.undatedItemIds.map((id) => `<li>${escapeHtml(itemLabel(this.items.get(id)))}</li>`).join('')}</ul></details>` : ''}
          <div class="k-action-row">${button('use-preview', 'Use this order')}${button('cancel-preview', 'Cancel')}</div>`;
        this.status('Preview ready. Use this order, then Save.');
      } catch (error) { this.status(error.message); }
      finally { this.busy = false; this.renderState(); }
    }
  }

  window.kanvasOrderInternals = {groupOrder, groupLabel, moveItems, consecutive, episodeCode};
  customElements.define('kanvas-watch-order-list', KanvasWatchOrderList);
  customElements.define('kanvas-watch-order-workspace', KanvasWatchOrderWorkspace);
})();
