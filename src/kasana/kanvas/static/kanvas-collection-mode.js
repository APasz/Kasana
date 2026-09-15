(() => {
  'use strict';

  const STORAGE_KEY = 'kanvas-collection-mode';
  const CHANGED_EVENT = 'kanvas:collection-changed';
  const positiveInteger = (value) => Number.isSafeInteger(value) && value > 0;

  /** @typedef {{itemId: number, state: 'absent'|'direct'|'inherited', relationship: string|null, inheritedFromId: number|null, inheritedFromTitle: string|null}} Membership */
  /** @typedef {{id: number, name: string, itemCount: number, revision: number, artworkItemId: number|null, items: Membership[]}} CollectionState */
  /** @typedef {{collectionId: number, revision: number, itemId: number, title: string, added: boolean, relationship: string|null, artwork: boolean}} MembershipUndo */

  const collectionState = (value) => {
    if (!value || !positiveInteger(value.id) || !positiveInteger(value.revision)
        || typeof value.name !== 'string' || !value.name.trim()
        || !Number.isSafeInteger(value.itemCount) || value.itemCount < 0
        || (value.artworkItemId !== null && !positiveInteger(value.artworkItemId))
        || !Array.isArray(value.items)) throw new TypeError('Invalid collection state.');
    const seen = new Set();
    for (const item of value.items) {
      if (!item || !positiveInteger(item.itemId) || seen.has(item.itemId)
          || !['absent', 'direct', 'inherited'].includes(item.state)
          || (item.relationship !== null && typeof item.relationship !== 'string')
          || (item.inheritedFromId !== null && !positiveInteger(item.inheritedFromId))
          || (item.inheritedFromTitle !== null && typeof item.inheritedFromTitle !== 'string')
          || (item.state === 'inherited' && (!item.inheritedFromId || !item.inheritedFromTitle))) {
        throw new TypeError('Invalid collection membership.');
      }
      seen.add(item.itemId);
    }
    return value;
  };

  const membershipUndo = (value) => value && positiveInteger(value.collectionId)
    && positiveInteger(value.revision) && positiveInteger(value.itemId)
    && typeof value.title === 'string' && typeof value.added === 'boolean'
    && typeof value.artwork === 'boolean'
    && (value.relationship === null || typeof value.relationship === 'string') ? value : null;

  class CollectionRequestError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  class KanvasCollectionMode extends HTMLElement {
    constructor() {
      super();
      this.activeId = null;
      this.undo = null;
      this.message = '';
      this.busy = false;
      this.loading = false;
      this.controls = new Set();
      this.contexts = new Map();
      this.timer = null;
      this.generation = 0;
      this.onProfileChange = () => { this.enabled = false; this.clear(); };
      this.onFocus = () => { if (!document.hidden) this.refresh(); };
      this.onPageShow = (event) => { if (event.persisted) this.refresh(); };
      this.onBeforeUnload = (event) => {
        if (!this.busy) return;
        event.preventDefault();
        event.returnValue = '';
      };
      this.onCollectionChange = (event) => {
        if (event.detail?.source === this || !positiveInteger(event.detail?.collectionId)) return;
        this.contexts.delete(event.detail.collectionId);
        if (this.undo?.collectionId === event.detail.collectionId) { this.undo = null; this.persist(); }
        this.refresh();
      };
    }

    connectedCallback() {
      this.profileId = Number(this.getAttribute('profile-id'));
      this.enabled = this.getAttribute('enabled') === 'true' && positiveInteger(this.profileId);
      this.lookupLimit = Number(this.getAttribute('lookup-limit'));
      if (!positiveInteger(this.lookupLimit)) throw new TypeError('Missing membership lookup limit.');
      window.kanvas.collectionMode = this;
      this.innerHTML = `<div class="k-collection-mode__bar"><span class="k-collection-mode__identity"><span data-mode-name></span><span data-mode-count></span></span><div class="k-collection-mode__actions"><a class="k-button" data-mode-view>View collection</a><button type="button" class="k-button" data-mode-done>Done</button></div></div><div class="k-collection-mode__feedback" role="status" aria-live="polite"><span data-mode-message></span><button type="button" class="k-button" data-mode-undo>Undo</button><button type="button" class="k-button" data-mode-retry>Retry</button></div>`;
      this.querySelector('[data-mode-done]').addEventListener('click', () => this.finish());
      this.querySelector('[data-mode-undo]').addEventListener('click', () => { void this.undoChange(); });
      this.querySelector('[data-mode-retry]').addEventListener('click', () => this.refresh());
      window.addEventListener('kanvas:profile-changed', this.onProfileChange);
      window.addEventListener(CHANGED_EVENT, this.onCollectionChange);
      window.addEventListener('focus', this.onFocus);
      window.addEventListener('pageshow', this.onPageShow);
      window.addEventListener('beforeunload', this.onBeforeUnload);
      document.addEventListener('visibilitychange', this.onFocus);
      if (!this.enabled) { this.clear(); return; }
      try {
        const saved = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || 'null');
        if (saved?.profileId === this.profileId) {
          this.activeId = positiveInteger(saved.activeId) ? saved.activeId : null;
          this.undo = membershipUndo(saved.undo);
        } else window.sessionStorage.removeItem(STORAGE_KEY);
      } catch (_) { this.activeId = null; this.undo = null; this.persist(); }
      const url = new URL(window.location.href);
      if (url.searchParams.has('editCollection')) {
        const requested = Number(url.searchParams.get('editCollection'));
        url.searchParams.delete('editCollection');
        window.history.replaceState(window.history.state, '', url);
        if (positiveInteger(requested)) {
          this.activeId = requested;
          if (this.undo?.collectionId !== requested) this.undo = null;
          this.persist();
        }
      }
      for (const control of document.querySelectorAll('kanvas-collection-toggle')) this.register(control);
      this.render();
      this.schedule();
    }

    disconnectedCallback() {
      window.removeEventListener('kanvas:profile-changed', this.onProfileChange);
      window.removeEventListener(CHANGED_EVENT, this.onCollectionChange);
      window.removeEventListener('focus', this.onFocus);
      window.removeEventListener('pageshow', this.onPageShow);
      window.removeEventListener('beforeunload', this.onBeforeUnload);
      document.removeEventListener('visibilitychange', this.onFocus);
      window.clearTimeout(this.timer);
      this.generation += 1;
      if (window.kanvas.collectionMode === this) delete window.kanvas.collectionMode;
    }

    persist() {
      try {
        if (!this.activeId && !this.undo) window.sessionStorage.removeItem(STORAGE_KEY);
        else window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({profileId: this.profileId, activeId: this.activeId, undo: this.undo}));
      } catch (_) {
        this.message = 'This browser could not keep collection mode across pages.';
      }
    }

    clear() {
      this.generation += 1;
      this.activeId = null;
      this.undo = null;
      this.message = '';
      this.contexts.clear();
      this.persist();
      this.render();
    }

    finish() {
      if (this.busy) return;
      const destination = this.activeId;
      this.clear();
      if (destination) window.location.assign(`/collections/${destination}`);
      else this.schedule();
    }

    register(control) { this.controls.add(control); control.render?.(); this.schedule(); }
    unregister(control) { this.controls.delete(control); }
    collectionId(control) { return Number(control.getAttribute('collection-id')) || this.activeId; }

    schedule() {
      window.clearTimeout(this.timer);
      this.timer = window.setTimeout(() => { void this.loadControls(); }, 30);
    }

    refresh() {
      if (this.busy) return;
      this.generation += 1;
      this.contexts.clear();
      this.message = '';
      this.render();
      this.schedule();
    }

    async request(collectionId, itemIds = []) {
      const response = await fetch(`/kanvas/data/collections/${collectionId}/mode`, {
        credentials: 'same-origin', headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
        ...(itemIds.length ? {method: 'POST', body: JSON.stringify({library_item_ids: itemIds})} : {})
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new CollectionRequestError(payload?.error || 'Could not load this collection.', response.status);
      const state = collectionState(payload);
      if (state.id !== collectionId || itemIds.some((id) => !state.items.some((item) => item.itemId === id))) {
        throw new TypeError('Incomplete collection membership state.');
      }
      return state;
    }

    applyState(state) {
      const current = this.contexts.get(state.id);
      if (current && current.revision > state.revision) return;
      const memberships = current?.revision === state.revision ? current.memberships : new Map();
      for (const item of state.items) memberships.set(item.itemId, item);
      this.contexts.set(state.id, {...state, memberships, error: false});
    }

    async loadControls() {
      if (!this.enabled || this.busy || this.loading) return;
      this.loading = true;
      const generation = this.generation;
      const groups = new Map();
      if (this.activeId) groups.set(this.activeId, new Set());
      if (this.undo) groups.set(this.undo.collectionId, new Set());
      for (const control of this.controls) {
        const collectionId = this.collectionId(control);
        const itemId = Number(control.getAttribute('item-id'));
        if (!positiveInteger(collectionId) || !positiveInteger(itemId)) continue;
        if (!groups.has(collectionId)) groups.set(collectionId, new Set());
        groups.get(collectionId).add(itemId);
      }
      let changed = false;
      try {
        for (const [id, ids] of groups) {
          const context = this.contexts.get(id);
          if (context?.error) continue;
          const missing = [...ids].filter((itemId) => !context?.memberships.has(itemId));
          if (context && !missing.length) continue;
          try {
            do {
              const batch = missing.splice(0, this.lookupLimit);
              const state = await this.request(id, batch);
              if (generation !== this.generation) return;
              this.applyState(state);
              changed = true;
            } while (missing.length);
          } catch (error) {
            if (generation !== this.generation) return;
            if (error instanceof CollectionRequestError && [401, 403].includes(error.status)) { this.onProfileChange(); return; }
            if (error instanceof CollectionRequestError && error.status === 404) {
              if (this.activeId === id) this.activeId = null;
              if (this.undo?.collectionId === id) this.undo = null;
              this.persist();
            }
            this.contexts.set(id, {...context, error: true, memberships: new Map()});
            this.message = error instanceof Error ? error.message : 'Could not load this collection.';
          }
          this.render();
        }
        for (const [id, context] of this.contexts) {
          if (!groups.has(id)) { this.contexts.delete(id); continue; }
          for (const itemId of context.memberships.keys()) {
            if (!groups.get(id).has(itemId)) context.memberships.delete(itemId);
          }
        }
      } finally {
        this.loading = false;
        this.render();
        if (changed || generation !== this.generation) this.schedule();
      }
    }

    async change(control) {
      const id = this.collectionId(control);
      const itemId = Number(control.getAttribute('item-id'));
      const context = this.contexts.get(id);
      const member = context?.memberships.get(itemId);
      if (!this.enabled || this.busy || control.hasAttribute('disabled') || !member || member.state === 'inherited') return;
      const added = member.state === 'absent';
      const undo = {collectionId: id, revision: context.revision, itemId,
        title: control.getAttribute('item-title') || `Item ${itemId}`, added,
        relationship: member.relationship, artwork: context.artworkItemId === itemId};
      const payload = added ? {additions: [{library_item_id: itemId}]} : {removals: [itemId]};
      await this.mutate(id, context.revision, payload, undo);
    }

    async undoChange() {
      if (!this.undo || this.busy) return;
      const undo = this.undo;
      const payload = undo.added ? {removals: [undo.itemId]} : {
        additions: [{library_item_id: undo.itemId, relationship: undo.relationship}],
        ...(undo.artwork ? {details: {artwork_item_id: undo.itemId}} : {})
      };
      await this.mutate(undo.collectionId, undo.revision, payload, null, undo.itemId);
    }

    async mutate(id, revision, changes, undo, undoneItemId = null) {
      if (!this.enabled || this.busy) return;
      this.busy = true;
      this.generation += 1;
      this.message = 'Saving…';
      this.render();
      try {
        const response = await fetch(`/kanvas/actions/collections/${id}/members/batch`, {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
          body: JSON.stringify({expected_revision: revision, ...changes})
        });
        const result = await response.json().catch(() => null);
        if (!this.enabled) return;
        if (!response.ok || !positiveInteger(result?.revision)) {
          if (response.status === 409) {
            if (undoneItemId !== null) this.undo = null;
            this.persist();
            throw new CollectionRequestError('This collection changed. Review it and try again.', 409);
          }
          throw new CollectionRequestError(result?.error || 'Could not save. Try again.', response.status);
        }
        this.undo = undo ? {...undo, revision: result.revision} : null;
        this.persist();
        this.message = undo ? `${undo.added ? 'Added' : 'Removed'} ${undo.title}` : 'Undone';
        const warnings = Array.isArray(result.warnings) ? result.warnings.filter((value) => typeof value === 'string') : [];
        if (warnings.length) window.kanvas.toast({severity: 'warning', title: 'Collection updated', detail: warnings.join(' ')});
        window.dispatchEvent(new CustomEvent(CHANGED_EVENT, {detail: {
          source: this, collectionId: id, revision: result.revision,
          previousRevision: revision, itemId: undo?.itemId ?? undoneItemId,
          member: undo ? undo.added : Boolean(changes.additions?.length),
          artworkItemId: changes.details?.artwork_item_id ?? (
            undo?.artwork ? null : this.contexts.get(id)?.artworkItemId ?? null
          ),
        }}));
      } catch (error) {
        if (error instanceof CollectionRequestError && [401, 403].includes(error.status)) {
          this.onProfileChange();
        }
        this.message = error instanceof Error ? error.message : 'Could not save. Try again.';
      } finally {
        this.contexts.delete(id);
        this.busy = false;
        this.render();
        this.schedule();
      }
    }

    render() {
      document.documentElement.toggleAttribute('data-collection-mode', this.enabled && this.activeId !== null);
      for (const control of this.controls) control.render?.();
      if (!this.querySelector('[data-mode-name]')) return;
      const id = this.activeId || this.undo?.collectionId;
      const context = this.contexts.get(id);
      this.hidden = !this.enabled || (!id && !this.message);
      this.querySelector('[data-mode-name]').textContent = context?.name
        ? `${this.activeId ? 'Editing: ' : ''}${context.name}` : this.activeId ? 'Loading collection…' : '';
      this.querySelector('[data-mode-count]').textContent = Number.isSafeInteger(context?.itemCount)
        ? `${context.itemCount} ${context.itemCount === 1 ? 'title' : 'titles'}` : '';
      const view = this.querySelector('[data-mode-view]');
      view.hidden = !id;
      if (id) view.href = `/collections/${id}`;
      const done = this.querySelector('[data-mode-done]');
      done.textContent = this.activeId ? 'Done' : 'Close';
      done.disabled = this.busy;
      this.querySelector('[data-mode-message]').textContent = this.message || (this.undo ? `${this.undo.added ? 'Added' : 'Removed'} ${this.undo.title}` : '');
      const undoButton = this.querySelector('[data-mode-undo]');
      undoButton.hidden = !this.undo;
      undoButton.disabled = this.busy;
      this.querySelector('[data-mode-retry]').hidden = ![...this.contexts.values()].some((value) => value.error);
    }
  }

  class KanvasCollectionToggle extends HTMLElement {
    connectedCallback() {
      this.innerHTML = '<button type="button" class="k-button" data-collection-toggle></button><a class="k-collection-inherited" data-collection-inherited></a>';
      this.querySelector('button').addEventListener('click', () => {
        const mode = window.kanvas.collectionMode;
        if (mode?.contexts.get(mode.collectionId(this))?.error) mode.refresh();
        else void mode?.change(this);
      });
      window.kanvas.collectionMode?.register(this);
      this.render();
    }

    disconnectedCallback() { window.kanvas.collectionMode?.unregister(this); }

    render() {
      const mode = window.kanvas.collectionMode;
      const id = mode?.collectionId(this);
      this.hidden = !mode?.enabled || !positiveInteger(id);
      const button = this.querySelector('button');
      if (!button || this.hidden) return;
      const context = mode.contexts.get(id);
      const itemId = Number(this.getAttribute('item-id'));
      const member = context?.memberships.get(itemId);
      const inherited = this.querySelector('[data-collection-inherited]');
      inherited.hidden = member?.state !== 'inherited';
      button.hidden = !inherited.hidden;
      if (member?.state === 'inherited') {
        inherited.textContent = `Included via ${member.inheritedFromTitle}`;
        inherited.title = inherited.textContent;
        inherited.href = `/item/${member.inheritedFromId}`;
      }
      button.textContent = context?.error ? 'Retry' : !member ? 'Loading…' : member.state === 'direct' ? 'Remove' : 'Add';
      button.disabled = mode.busy || this.hasAttribute('disabled') || (!member && !context?.error);
      button.setAttribute('aria-label', `${button.textContent} ${this.getAttribute('item-title') || 'item'} ${member?.state === 'direct' ? 'from' : 'to'} ${context?.name || 'collection'}`);
      this.toggleAttribute('data-member', member?.state === 'direct');
    }
  }

  customElements.define('kanvas-collection-mode', KanvasCollectionMode);
  customElements.define('kanvas-collection-toggle', KanvasCollectionToggle);
})();
