const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.attributes = new Map();
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.listeners = new Map();
    this.parentElement = null;
    this.textContent = '';
    this.className = '';
    this.type = '';
    this.innerHTML = '';
    this.isConnected = true;
    this.columnCount = 5;
    this.rowHeight = 100;
    this.viewportOffset = 0;
  }

  get firstElementChild() {
    return this.children[0] || null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) || null;
  }

  append(...nodes) {
    for (const node of nodes) {
      if (node instanceof FakeFragment) {
        this.append(...node.children);
        continue;
      }
      node.parentElement = this;
      this.children.push(node);
    }
  }

  prepend(...nodes) {
    for (const node of [...nodes].reverse()) {
      if (node instanceof FakeFragment) {
        this.prepend(...node.children);
        continue;
      }
      if (node.parentElement) node.remove();
      node.parentElement = this;
      this.children.unshift(node);
    }
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }

  remove() {
    if (!this.parentElement) return;
    const index = this.parentElement.children.indexOf(this);
    if (index >= 0) this.parentElement.children.splice(index, 1);
    this.parentElement = null;
  }

  addEventListener(name, listener, options = {}) {
    const listeners = this.listeners.get(name) || [];
    listeners.push({listener, once: Boolean(options.once)});
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    this.listeners.set(name, listeners.filter((entry) => entry.listener !== listener));
  }

  click() {
    const listeners = this.listeners.get('click') || [];
    for (const entry of [...listeners]) {
      entry.listener();
      if (entry.once) listeners.splice(listeners.indexOf(entry), 1);
    }
  }

  contains(candidate) {
    return candidate === this || this.children.some((child) => child.contains(candidate));
  }

  getBoundingClientRect() {
    const index = this.parentElement ? this.parentElement.children.indexOf(this) : 0;
    const row = index >= 0 ? Math.floor(index / (this.parentElement?.columnCount || this.columnCount)) : 0;
    const top = row * this.rowHeight - (this.parentElement?.viewportOffset || 0);
    return {bottom: top + this.rowHeight, height: this.rowHeight, width: 100, top};
  }

  querySelector() {
    return null;
  }
}

class FakeFragment extends FakeElement {}
class FakeHTMLElement extends FakeElement {}

const elementRegistry = new Map();
const storage = new Map();
const consoleErrors = [];
const railElements = [];
const documentListeners = new Map();
const timerDelays = [];
let throwPosterCreation = false;

global.Element = FakeElement;
global.HTMLElement = FakeHTMLElement;
global.HTMLFormElement = class extends FakeHTMLElement {};
global.HTMLInputElement = class extends FakeHTMLElement {};
global.HTMLTextAreaElement = class extends FakeHTMLElement {};
global.HTMLSelectElement = class extends FakeHTMLElement {};
global.HTMLDialogElement = class extends FakeHTMLElement {};
global.customElements = {
  define(name, constructor) {
    elementRegistry.set(name, constructor);
  },
  get(name) {
    return elementRegistry.get(name);
  }
};
global.document = {
  scripts: [{src: 'http://kanvas.test/_kanvas/kanvas.js?v=test-asset'}],
  activeElement: null,
  addEventListener(name, listener) {
    const listeners = documentListeners.get(name) || [];
    listeners.push(listener);
    documentListeners.set(name, listeners);
  },
  querySelector() {
    return null;
  },
  querySelectorAll(selector) {
    return selector === '.k-rail' ? railElements : [];
  },
  createElement(name) {
    if (name === 'kanvas-poster' && throwPosterCreation) throw new Error('rendering failed');
    const Constructor = elementRegistry.get(name);
    return Constructor ? new Constructor() : new FakeElement(name);
  },
  createDocumentFragment() {
    return new FakeFragment();
  }
};
global.window = {
  location: {origin: 'http://kanvas.test', pathname: '/library'},
  innerHeight: 800,
  scrollY: 42,
  scrollByCalls: [],
  addEventListener() {},
  removeEventListener() {},
  scrollBy(...values) {
    this.scrollByCalls.push(values);
  },
  scrollTo() {},
  history: {back() {}},
  clearTimeout() {},
  setTimeout(_callback, delay) {
    timerDelays.push(delay);
    return timerDelays.length;
  }
};
global.navigator = {getGamepads: () => []};
global.sessionStorage = {
  getItem(key) {
    return storage.get(key) || null;
  },
  setItem(key, value) {
    storage.set(key, value);
  },
  removeItem(key) {
    storage.delete(key);
  }
};
global.IntersectionObserver = class {
  observe() {}
  disconnect() {}
};
global.requestAnimationFrame = (callback) => callback();
console.error = (...values) => consoleErrors.push(values);

const source = [
  fs.readFileSync('src/kasana/kanvas/static/kanvas.js', 'utf8'),
  fs.readFileSync('src/kasana/kanvas/static/kanvas-administration.js', 'utf8')
].join('\n');
const exposed = source.replace(
  "if (!customElements.get('kanvas-poster-grid')) customElements.define('kanvas-poster-grid', KanvasPosterGrid);",
  "globalThis.__libraryTest = {KanvasPosterGrid, LibraryPageDirection, normalisePoster, posterMarkup, libraryGridPayload, updateRailControls, libraryFilterUrl, libraryGridLayout, libraryGridMarkup};\n  if (!customElements.get('kanvas-poster-grid')) customElements.define('kanvas-poster-grid', KanvasPosterGrid);"
).replace(
  "if (!customElements.get('kanvas-watch-order-workspace')) customElements.define('kanvas-watch-order-workspace', KanvasWatchOrderWorkspace);",
  "globalThis.__watchOrderTest = {KanvasWatchOrderWorkspace};\n  if (!customElements.get('kanvas-watch-order-workspace')) customElements.define('kanvas-watch-order-workspace', KanvasWatchOrderWorkspace);"
).replace(
  "if (!customElements.get('kanvas-administration')) customElements.define('kanvas-administration', KanvasAdministration);",
  "globalThis.__administrationTest = {KanvasAdministration};\n  if (!customElements.get('kanvas-administration')) customElements.define('kanvas-administration', KanvasAdministration);"
).replace(
  "if (!customElements.get('kanvas-item-editor')) customElements.define('kanvas-item-editor', KanvasItemEditor);",
  "globalThis.__itemEditorTest = {KanvasItemEditor};\n  if (!customElements.get('kanvas-item-editor')) customElements.define('kanvas-item-editor', KanvasItemEditor);"
);
vm.runInThisContext(exposed, {filename: 'kanvas.js'});

const validPoster = (id = 7) => ({
  id,
  title: `Poster ${id}`,
  href: `/item/${id}`,
  posterUrl: `/kanvas/artwork/${id}/${id + 1}`,
  progressPercent: null,
  state: 'normal',
  available: true
});

const validEnvelope = (items = [validPoster()]) => ({
  schemaVersion: 2,
  items,
  previousCursor: null,
  nextCursor: null,
  requestId: 'request-123'
});

const response = ({status = 200, contentType = 'application/json', body = validEnvelope(), jsonError = null}) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: {get: (name) => ({'content-type': contentType, 'x-request-id': 'header-request'}[name.toLowerCase()] || null)},
  json: async () => {
    if (jsonError) throw jsonError;
    return body;
  }
});

const grid = (developmentMode = true) => {
  const instance = new globalThis.__libraryTest.KanvasPosterGrid();
  instance.setAttribute('source', '/kanvas/data/library?kind=movie&search=alpha');
  instance.setAttribute('state-user', '4');
  instance.setAttribute('catalogue-revision', '1:2026-07-24T11:50:00+00:00');
  instance.setAttribute('development-mode', String(developmentMode));
  instance.grid = new FakeHTMLElement('div');
  instance.previousStatus = new FakeHTMLElement('div');
  instance.nextStatus = new FakeHTMLElement('div');
  instance.leadingSpacer = new FakeHTMLElement('div');
  instance.trailingSpacer = new FakeHTMLElement('div');
  instance.previousSentinel = new FakeHTMLElement('div');
  instance.nextSentinel = new FakeHTMLElement('div');
  const distantBounds = () => ({bottom: 10_001, height: 1, top: 10_000, width: 100});
  instance.previousSentinel.getBoundingClientRect = distantBounds;
  instance.nextSentinel.getBoundingClientRect = distantBounds;
  instance.stateKey = instance.buildStateKey(instance.getAttribute('source'));
  instance.generation = 1;
  return instance;
};

const nextTick = () => new Promise((resolve) => setImmediate(resolve));

const dispatchDocumentEvent = (name, event) => {
  for (const listener of documentListeners.get(name) || []) listener(event);
};

async function testValidPageRetainsAvailable() {
  const instance = grid();
  global.fetch = async () => response({});
  await instance.load(globalThis.__libraryTest.LibraryPageDirection.INITIAL);
  assert.equal(instance.pages.length, 1);
  assert.equal(instance.pages[0].items[0].available, true);
  assert.equal(instance.grid.children.length, 1);
  assert.equal(instance.requestId, 'request-123');
  assert.equal(instance.nextStatus.textContent, 'End of library.');
}

function testPosterArtworkLabelNormalisation() {
  const poster = globalThis.__libraryTest.normalisePoster({
    ...validPoster(11),
    context: ' The show ',
    posterUrl: null,
    artworkLabel: ' S01 E02 ',
    placeholder: {lines: [' Main title ', '', 'Subtitle']}
  });

  assert.equal(poster.posterUrl, null);
  assert.equal(poster.context, 'The show');
  assert.deepEqual(poster.placeholder.lines, ['Main title', 'Subtitle']);
  assert.equal(poster.artworkLabel, 'S01 E02');
  assert.deepEqual(
    globalThis.__libraryTest.normalisePoster(validPoster(12)).placeholder.lines,
    ['Poster 12']
  );
}

function testPosterArtworkLabelMarkup() {
  const poster = globalThis.__libraryTest.normalisePoster({
    ...validPoster(13),
    title: 'Bad Boys',
    artworkLabel: 'Remastered'
  });
  const markup = globalThis.__libraryTest.posterMarkup(poster);

  assert.match(
    markup,
    /class="k-poster__artwork-label-banner" aria-hidden="true"><\/span><span class="k-poster__artwork-label-text">Remastered/
  );
  assert.match(markup, /aria-label="Bad Boys — Remastered"/);
  assert.equal((markup.match(/class="k-poster__artwork-label"/g) || []).length, 1);
}

function testPosterPartialWatchNormalisation() {
  const poster = globalThis.__libraryTest.normalisePoster({
    ...validPoster(16),
    partiallyWatched: true
  });
  assert.equal(poster.partiallyWatched, true);
  assert.equal(
    globalThis.__libraryTest.normalisePoster({...validPoster(17), partiallyWatched: 'true'}),
    null
  );
}

function testLandscapePosterMarkup() {
  const poster = globalThis.__libraryTest.normalisePoster({
    ...validPoster(24),
    artworkShape: 'landscape'
  });

  assert.equal(poster.artworkShape, 'landscape');
  assert.match(globalThis.__libraryTest.posterMarkup(poster), /k-poster--landscape/);
  assert.equal(
    globalThis.__libraryTest.normalisePoster({...validPoster(25), artworkShape: 'square'}),
    null
  );
}

function testLibraryFilterUrlKeepsOnlyActiveUrlState() {
  assert.equal(
    globalThis.__libraryTest.libraryFilterUrl('/library', [
      ['search', ' Ghost '],
      ['kind', 'all'],
      ['tag', 'anime'],
      ['tag', 'favourite'],
      ['watched', ''],
      ['year', '2001']
    ]),
    '/library?search=Ghost&kind=all&tag=anime&tag=favourite&year=2001'
  );
}

function testLibraryFilterInputsWaitForCommit() {
  const form = new global.HTMLFormElement('form');
  const input = new global.HTMLInputElement('input');
  input.closest = (selector) => (
    selector === 'form[data-kanvas-library-filters="true"]' ? form : null
  );
  const timersBefore = timerDelays.length;

  input.type = 'number';
  dispatchDocumentEvent('input', {target: input});
  assert.equal(timerDelays.length, timersBefore);

  input.type = 'search';
  dispatchDocumentEvent('input', {target: input});
  assert.equal(timerDelays.length, timersBefore);

  dispatchDocumentEvent('change', {target: input});
  assert.deepEqual(timerDelays.slice(timersBefore), [0]);
}

function testLibraryGridKeepsOneCardGeometryPerResultSet() {
  const portrait = globalThis.__libraryTest.libraryGridMarkup('portrait');
  const landscape = globalThis.__libraryTest.libraryGridMarkup('landscape');

  assert.match(portrait, /k-grid--portrait/);
  assert.doesNotMatch(portrait, /k-grid--landscape/);
  assert.match(landscape, /k-grid--landscape/);
  assert.doesNotMatch(landscape, /k-grid--portrait/);
  assert.doesNotMatch(portrait, /mixed/);
  assert.equal(globalThis.__libraryTest.libraryGridLayout('landscape'), 'landscape');
  assert.equal(globalThis.__libraryTest.libraryGridLayout('invalid'), 'portrait');
}

function testLibraryGridMarkupUsesOneGeometryPerFocusedResult() {
  const portrait = globalThis.__libraryTest.libraryGridMarkup('portrait');
  const landscape = globalThis.__libraryTest.libraryGridMarkup('landscape');

  assert.match(portrait, /data-library-grid="portrait"/);
  assert.doesNotMatch(portrait, /data-library-grid="landscape"/);
  assert.match(landscape, /data-library-grid="landscape"/);
  assert.doesNotMatch(landscape, /data-library-grid="portrait"/);
  assert.match(landscape, /k-library-grid__loading--landscape/);
  assert.match(portrait, /k-grid-status--tail/);
  assert.match(portrait, /data-library-sentinel="previous"/);
  assert.match(portrait, /data-library-sentinel="next"/);
  assert.ok(portrait.indexOf('k-grid-status--tail') > portrait.indexOf('data-library-grid="portrait"'));
}

function testGridLayoutDoesNotConflictWithFrameworkProperties() {
  const instance = grid();
  instance.setAttribute('grid-layout', 'landscape');
  instance.layout = 'portrait';

  assert.equal(instance.gridLayout(), 'landscape');
}

function testLibraryGridStylesUseResponsiveGeometryWithoutCardSpans() {
  const stylesheet = fs.readFileSync('src/kasana/kanvas/static/kanvas.css', 'utf8');

  assert.match(stylesheet, /\.k-grid--portrait \{ grid-template-columns: repeat\(auto-fill, minmax\(138px, 1fr\)\)/);
  assert.match(stylesheet, /\.k-grid--landscape \{/);
  assert.match(stylesheet, /\.k-grid--portrait \.k-poster__art \{ aspect-ratio: 2 \/ 3; \}/);
  assert.match(stylesheet, /\.k-grid--landscape \.k-poster__art \{ aspect-ratio: 16 \/ 9; \}/);
  assert.match(stylesheet, /\.k-grid > kanvas-poster:has\(.k-poster--landscape\) \{ width: 100%; \}/);
  assert.match(stylesheet, /@media \(max-width: 700px\)/);
  assert.match(stylesheet, /@media \(max-width: 440px\)/);
  assert.doesNotMatch(
    stylesheet,
    /\.k-grid > kanvas-poster:has\(.+?\) \{ grid-column: span 2; \}/
  );
}

function testPosterMosaicAndHomeActionNormalisation() {
  const poster = globalThis.__libraryTest.normalisePoster({
    ...validPoster(18),
    posterUrl: null,
    mosaicUrls: ['/kanvas/artwork/18/19', '/kanvas/artwork/20/21'],
    action: 'play_next',
    detail: 'Next: Pilot'
  });

  assert.deepEqual(poster.mosaicUrls, ['/kanvas/artwork/18/19', '/kanvas/artwork/20/21']);
  assert.equal(poster.action, 'play_next');
  const markup = globalThis.__libraryTest.posterMarkup(poster);
  assert.match(markup, /k-poster-mosaic/);
  assert.match(markup, /k-poster__action/);
  assert.match(markup, /k-poster__action-icon/);
  assert.doesNotMatch(markup, /k-poster__cue/);
  assert.equal(
    globalThis.__libraryTest.normalisePoster({...validPoster(19), action: 'watch_now'}),
    null
  );
  assert.equal(
    globalThis.__libraryTest.normalisePoster({
      ...validPoster(20),
      mosaicUrls: ['/kanvas/artwork/20/21']
    }),
    null
  );
}

function testPosterStatusBadgeMarkup() {
  const watched = globalThis.__libraryTest.posterMarkup(
    globalThis.__libraryTest.normalisePoster({...validPoster(21), watched: true})
  );
  const partiallyWatched = globalThis.__libraryTest.posterMarkup(
    globalThis.__libraryTest.normalisePoster({...validPoster(22), partiallyWatched: true})
  );
  const unavailable = globalThis.__libraryTest.posterMarkup(
    globalThis.__libraryTest.normalisePoster({...validPoster(23), available: false, state: 'unavailable'})
  );

  assert.match(watched, /k-poster__completion--watched/);
  assert.match(partiallyWatched, /k-poster__completion--partial/);
  assert.match(unavailable, /k-poster__status--unavailable/);
}

function testRailControlsHideWhenViewportDoesNotOverflow() {
  const buildRail = (clientWidth, scrollWidth) => {
    const viewport = new FakeHTMLElement();
    viewport.clientWidth = clientWidth;
    viewport.scrollWidth = scrollWidth;
    const controls = new FakeHTMLElement();
    const rail = new FakeHTMLElement();
    rail.querySelector = (selector) => {
      if (selector === '[data-kanvas-rail-viewport="true"]') return viewport;
      return selector === '.k-rail__controls' ? controls : null;
    };
    return {controls, rail};
  };
  const short = buildRail(600, 600);
  const long = buildRail(600, 602);
  railElements.push(short.rail, long.rail);

  try {
    globalThis.__libraryTest.updateRailControls();
    assert.equal(short.controls.hidden, true);
    assert.equal(long.controls.hidden, false);
  } finally {
    railElements.length = 0;
  }
}

function testPosterNormalisationAllowsOnlySafeItemAndResumeLinks() {
  assert.equal(
    globalThis.__libraryTest.normalisePoster({...validPoster(13), href: '/item/13'}).href,
    '/item/13'
  );
  assert.equal(
    globalThis.__libraryTest.normalisePoster({...validPoster(14), href: '/play/watch-orders/4?resume=true&onDeck=true'}).href,
    '/play/watch-orders/4?resume=true&onDeck=true'
  );
  assert.equal(
    globalThis.__libraryTest.normalisePoster({...validPoster(15), href: '/play/item/15?resume=true&onDeck=true'}).href,
    '/play/item/15?resume=true&onDeck=true'
  );
  assert.equal(
    globalThis.__libraryTest.normalisePoster({...validPoster(16), href: '/play/watch-orders/4'}),
    null
  );
  assert.equal(
    globalThis.__libraryTest.normalisePoster({
      ...validPoster(17),
      href: '/play/watch-orders/4?itemId=17'
    }),
    null
  );
}

function testWatchOrderInsertionSlotsRejectOnlyNoOpMoves() {
  const workspace = new globalThis.__watchOrderTest.KanvasWatchOrderWorkspace();
  workspace.entries = [{id: 1}, {id: 2}, {id: 3}];

  assert.equal(workspace.isNoopMove('1', '2'), true);
  assert.equal(workspace.isNoopMove('1', '3'), false);
  assert.equal(workspace.isNoopMove('3', null), true);
  assert.equal(workspace.isNoopMove('2', '1'), false);
  assert.match(workspace.insertionSlot(2), /data-insert-before="2"/);
  assert.match(workspace.insertionSlot(null), /Add to end of order/);

  workspace.order = {scrollLeft: 12};
  workspace.activeSlot = {};
  workspace.isDragging = true;
  let prevented = false;
  workspace.onOrderWheel({
    deltaX: 0,
    deltaY: 28,
    preventDefault() { prevented = true; }
  });
  assert.equal(workspace.order.scrollLeft, 40);
  assert.equal(prevented, true);

  workspace.revision = 9;
  let boundaryIntent = null;
  workspace.mutate = (intent) => { boundaryIntent = intent; };
  workspace.moveBoundary('2', 'start');
  assert.deepEqual(boundaryIntent, {
    operation: 'move', entryId: 2, boundary: 'start', revision: 9
  });

  workspace.addSources([4, 5], 2);
  assert.deepEqual(boundaryIntent, {
    operation: 'add_sources', sourceItemIds: [4, 5], beforeEntryId: 2, revision: 9
  });
}
async function testCategorisedFailureAndRetry() {
  const instance = grid();
  const initial = globalThis.__libraryTest.LibraryPageDirection.INITIAL;
  let calls = 0;
  global.fetch = async () => {
    calls += 1;
    return calls === 1
      ? response({status: 503, body: {error: {requestId: 'retry-request'}}})
      : response({});
  };
  await instance.load(initial);
  assert.equal(instance.retryDirection, initial);
  assert.equal(instance.nextStatus.textContent, 'Could not load this part of the library.');
  const diagnostic = instance.nextStatus.children.find((child) => child.tagName === 'details');
  assert.match(diagnostic.children[1].textContent, /Category: http_failure/);
  assert.match(diagnostic.children[1].textContent, /HTTP status: 503/);
  assert.match(diagnostic.children[1].textContent, /Request ID: retry-request/);
  instance.nextStatus.children.find((child) => child.tagName === 'button').click();
  await nextTick();
  assert.equal(calls, 2);
  assert.equal(instance.pages[0].items.length, 1);
  assert.equal(instance.retryDirection, null);
}

async function testMalformedResponsesAndPosters() {
  const initial = globalThis.__libraryTest.LibraryPageDirection.INITIAL;

  const invalidContentType = grid();
  global.fetch = async () => response({contentType: 'text/html'});
  await invalidContentType.load(initial);
  assert.match(invalidContentType.nextStatus.children.find((child) => child.tagName === 'details').children[1].textContent, /invalid_content_type/);

  const invalidJson = grid();
  global.fetch = async () => response({jsonError: new SyntaxError('bad json')});
  await invalidJson.load(initial);
  assert.match(invalidJson.nextStatus.children.find((child) => child.tagName === 'details').children[1].textContent, /invalid_json/);

  const invalidEnvelope = grid();
  global.fetch = async () => response({body: {items: []}});
  await invalidEnvelope.load(initial);
  assert.match(invalidEnvelope.nextStatus.children.find((child) => child.tagName === 'details').children[1].textContent, /invalid_envelope/);

  const oneMalformed = grid();
  global.fetch = async () => response({body: validEnvelope([validPoster(7), {id: 8, title: 'Broken'}])});
  await oneMalformed.load(initial);
  assert.equal(oneMalformed.pages[0].items.length, 1);
  assert.equal(oneMalformed.invalidPosterCount, 1);
  assert.match(oneMalformed.nextStatus.textContent, /1 item could not be displayed/);
  assert.deepEqual(consoleErrors.at(-1)[1], {itemIds: [8]});

  const allMalformed = grid();
  global.fetch = async () => response({body: validEnvelope([{id: 9, title: 'Broken'}])});
  await allMalformed.load(initial);
  assert.equal(allMalformed.mountedPosterCount(), 0);
  assert.equal(allMalformed.retryDirection, null);
  assert.match(allMalformed.nextStatus.textContent, /1 item could not be displayed/);
}

async function testCancellationStateAndDevelopmentDiagnostics() {
  const initial = globalThis.__libraryTest.LibraryPageDirection.INITIAL;
  const stale = grid();
  global.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
  });
  const pending = stale.load(initial);
  await nextTick();
  stale.generation += 1;
  stale.requestController.abort();
  await pending;
  assert.notEqual(stale.nextStatus.textContent, 'Could not load this part of the library.');

  const production = grid(false);
  const errorsBefore = consoleErrors.length;
  global.fetch = async () => { throw new TypeError('offline'); };
  await production.load(initial);
  assert.equal(consoleErrors.length, errorsBefore);

  const development = grid(true);
  await development.load(initial);
  assert.equal(consoleErrors.at(-1)[1].category, 'network_failure');
}

async function testStateInvalidationAndRenderingFailure() {
  const initial = globalThis.__libraryTest.LibraryPageDirection.INITIAL;
  const instance = grid();
  assert.match(instance.stateKey, /v10:asset=test-asset:catalogue=1%3A2026-07-24T11%3A50%3A00%2B00%3A00:user=4:max-mounted=144:filters=/);
  assert.match(decodeURIComponent(instance.stateKey), /kind=movie&search=alpha/);
  const previousKey = instance.stateKey;
  instance.setAttribute('catalogue-revision', '1:2026-07-24T12:00:00+00:00');
  assert.notEqual(instance.buildStateKey(instance.getAttribute('source')), previousKey);
  instance.setAttribute('catalogue-revision', '1:2026-07-24T11:50:00+00:00');
  instance.setAttribute('max-mounted', '72');
  assert.notEqual(instance.buildStateKey(instance.getAttribute('source')), previousKey);
  storage.set(instance.stateKey, JSON.stringify({
    schemaVersion: 5,
    asset: 'test-asset',
    filters: '/kanvas/data/library?kind=movie&search=alpha',
    user: '4',
    pages: [],
    outcome: 'success',
    scrollY: 0
  }));
  assert.equal(instance.restoreState(), false);
  assert.equal(storage.has(instance.stateKey), false);

  const renderer = grid();
  throwPosterCreation = true;
  global.fetch = async () => response({});
  await renderer.load(initial);
  throwPosterCreation = false;
  const diagnostic = renderer.nextStatus.children.find((child) => child.tagName === 'details');
  assert.match(diagnostic.children[1].textContent, /rendering_failure/);
}

async function testBidirectionalVirtualPagesRehydrateEvictedCards() {
  const initial = globalThis.__libraryTest.LibraryPageDirection.INITIAL;
  const next = globalThis.__libraryTest.LibraryPageDirection.NEXT;
  const previous = globalThis.__libraryTest.LibraryPageDirection.PREVIOUS;
  const instance = grid();
  instance.setAttribute('max-mounted', '5');
  instance.grid.viewportOffset = 1200;
  const requests = [];
  const first = Array.from({length: 5}, (_value, index) => validPoster(index + 1));
  const second = Array.from({length: 5}, (_value, index) => validPoster(index + 6));
  const pages = [
    validEnvelope(first),
    {...validEnvelope(second), previousCursor: 'before-6', nextCursor: 'after-10'},
    {...validEnvelope(first), previousCursor: null, nextCursor: 'after-5'}
  ];
  pages[0].nextCursor = 'after-5';
  global.fetch = async (url) => {
    requests.push(new URL(url).searchParams.get('cursor'));
    return response({body: pages.shift()});
  };

  await instance.load(initial);
  await instance.load(next);

  assert.equal(instance.pages.length, 1);
  assert.deepEqual(instance.pages[0].items.map((item) => item.id), [6, 7, 8, 9, 10]);
  assert.equal(instance.leadingHeight, 100);
  instance.hasSuccessfulPage = true;
  instance.saveState();
  const savedState = JSON.parse(storage.get(instance.stateKey));
  assert.equal(savedState.maxMounted, 5);
  assert.equal(savedState.pages.length, 1);

  instance.grid.viewportOffset = 0;
  await instance.load(previous);

  assert.deepEqual(requests, [null, 'after-5', 'before-6']);
  assert.deepEqual(instance.pages.flatMap((page) => page.items.map((item) => item.id)), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.equal(instance.leadingHeight, 0);
}

async function testVisibleTailLoadsTheNextPageWithoutObserverChurn() {
  const initial = globalThis.__libraryTest.LibraryPageDirection.INITIAL;
  const instance = grid();
  instance.nextSentinel.getBoundingClientRect = () => ({bottom: 101, height: 1, top: 100, width: 100});
  const pages = [
    {...validEnvelope([validPoster(1)]), nextCursor: 'after-1'},
    validEnvelope([validPoster(2)])
  ];
  global.fetch = async () => response({body: pages.shift()});

  await instance.load(initial);
  await nextTick();

  assert.deepEqual(instance.pages.flatMap((page) => page.items.map((item) => item.id)), [1, 2]);
}

function testResponsiveVirtualSpacersTrackMountedGridGeometry() {
  const instance = grid();
  instance.gridHeight = 100;
  instance.setLeadingHeight(240);
  instance.setTrailingHeight(360);
  instance.grid.getBoundingClientRect = () => ({bottom: 200, height: 200, top: 0});

  instance.handleResize();

  assert.equal(instance.leadingHeight, 480);
  assert.equal(instance.trailingHeight, 720);
}

function testKeyboardNavigationLoadsAcrossVirtualPageEdges() {
  const next = globalThis.__libraryTest.LibraryPageDirection.NEXT;
  const instance = grid();
  instance.pages = [{items: Array.from({length: 5}, (_value, index) => validPoster(index + 1)), previousCursor: null, nextCursor: 'after-5'}];
  const cards = Array.from({length: 5}, () => new FakeHTMLElement('kanvas-poster'));
  for (const card of cards) {
    card.closest = (selector) => selector === 'kanvas-poster' ? card : null;
    instance.grid.append(card);
  }
  let request = null;
  instance.load = (direction, options) => { request = {direction, options}; return Promise.resolve(); };
  let prevented = false;

  instance.handleKeyDown({
    key: 'ArrowDown',
    preventDefault() { prevented = true; },
    target: cards[4]
  });

  assert.equal(prevented, true);
  assert.deepEqual(request, {direction: next, options: {focusColumn: 4}});
}

async function testAdministrationPollingWaitsForOpenDialog() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  let fetches = 0;
  let renders = 0;
  let schedules = 0;
  instance.section = 'libraries';
  instance.fetchJson = async () => {
    fetches += 1;
    return {items: []};
  };
  instance.render = () => { renders += 1; };
  instance.schedule = () => { schedules += 1; };
  instance.querySelector = (selector) => selector === 'dialog[open]' ? new HTMLDialogElement('dialog') : null;

  await instance.load();

  assert.equal(fetches, 0);
  assert.equal(renders, 0);
  assert.equal(schedules, 1);
  assert.equal(instance.inFlight, false);
}

async function testAdministrationReportsTrackedJobProgressWithoutChangingTab() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.submittedJobId = 'job-17';
  instance.fetchJson = async () => ({items: [{
    id: 'job-17',
    kind: 'scan',
    status: 'running',
    phase: 'classifying',
    progressCurrent: 12,
    progressTotal: 40,
    progressUnit: 'files'
  }]});

  await instance.checkSubmittedJob();

  assert.equal(instance.submittedJobId, 'job-17');
  assert.equal(instance.activity.state, 'active');
  assert.equal(instance.activity.message, 'Library scan running · classifying · 12/40 files');

  instance.fetchJson = async () => ({items: [{
    id: 'job-17',
    kind: 'scan',
    status: 'completed',
    message: '42 files scanned.'
  }]});
  await instance.checkSubmittedJob();

  assert.equal(instance.submittedJobId, null);
  assert.deepEqual(instance.activity, {state: 'complete', message: '42 files scanned.'});
}

async function testAdministrationStopsTrackingProblemJobs() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.submittedJobId = 'job-18';
  instance.fetchJson = async () => ({items: [{
    id: 'job-18',
    kind: 'scan',
    status: 'failed',
    failure: 'Provider unavailable.'
  }]});
  const originalAssign = window.location.assign;
  let destination = null;
  window.location.assign = (value) => { destination = value; };

  try {
    await instance.checkSubmittedJob();
  } finally {
    if (originalAssign) window.location.assign = originalAssign;
    else delete window.location.assign;
  }

  assert.equal(instance.submittedJobId, null);
  assert.deepEqual(instance.activity, {state: 'error', message: 'Provider unavailable.'});
  assert.equal(destination, '/administration/jobs#job-job-18');
}

async function testAdministrationReusesLoadedJobPageForTracking() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.section = 'jobs';
  instance.submittedJobId = 'job-17';
  instance.setAttribute('jobs-source', '/kanvas/data/administration/jobs');
  let fetches = 0;
  instance.fetchJson = async () => {
    fetches += 1;
    return {items: [{id: 'job-17', kind: 'scan', status: 'completed', message: '42 files scanned.'}], nextCursor: null};
  };
  instance.render = () => {};
  instance.schedule = () => {};

  await instance.load();

  assert.equal(fetches, 1);
  assert.equal(instance.submittedJobId, null);
}

function testAdministrationPollsTrackedJobsFrequently() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.submittedJobId = 'job-17';
  let interval = null;
  const setTimeout = window.setTimeout;
  window.setTimeout = (_callback, delay) => {
    interval = delay;
    return 1;
  };

  instance.schedule();

  window.setTimeout = setTimeout;
  assert.equal(interval, 2000);
}

function testAdministrationPollsActiveJobsFrequently() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.jobs = [{status: 'running'}];
  let interval = null;
  const setTimeout = window.setTimeout;
  window.setTimeout = (_callback, delay) => {
    interval = delay;
    return 1;
  };

  instance.schedule();

  window.setTimeout = setTimeout;
  assert.equal(interval, 5000);
}

async function testAdministrationContinuesMetadataReviewPages() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.setAttribute('metadata-source', '/kanvas/data/administration/metadata');
  instance.cursor = 'after-1';
  instance.reviewItems = [{itemId: 1}];
  instance.fetchJson = async (source, suffix) => {
    assert.equal(source, '/kanvas/data/administration/metadata');
    assert.equal(suffix, '?cursor=after-1');
    return {items: [{itemId: 2}], nextCursor: null};
  };
  let renders = 0;
  instance.render = () => { renders += 1; };

  await instance.moreReviewItems();

  assert.deepEqual(instance.reviewItems, [{itemId: 1}, {itemId: 2}]);
  assert.equal(instance.cursor, null);
  assert.equal(renders, 1);
}

function testAdministrationKeepsEncodedJobAnchorsTargetable() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.querySelectorAll = () => [];
  instance.jobs = [{
    id: 'repair 1',
    kind: 'hierarchy-repair',
    status: 'failed',
    phase: null,
    progressCurrent: 0,
    progressTotal: null,
    progressUnit: null,
    counters: [],
    message: null,
    failure: 'Repair could not run.',
    submittedAt: '2026-09-03T10:00:00Z',
    startedAt: null,
    completedAt: null,
    cancellable: false,
    clearable: true
  }];
  const hash = window.location.hash;
  window.location.hash = '#job-repair%201';

  instance.renderJobs();

  window.location.hash = hash;
  assert.match(instance.innerHTML, /id="job-repair%201"/);
  assert.match(instance.innerHTML, /k-job-row--target/);
  assert.match(instance.innerHTML, /data-admin-job-details="repair 1"/);
  assert.match(instance.innerHTML, /aria-expanded="true"/);
  assert.match(instance.innerHTML, /class="k-job-row__details"/);
  assert.match(instance.innerHTML, /data-admin-clear="repair 1"/);
}

function testAdministrationJobCardsKeepActionsAndDetailsSeparate() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.querySelectorAll = () => [];
  instance.jobs = [{
    id: 'failure-1',
    kind: 'artwork-fetch',
    status: 'failed',
    phase: 'fetching',
    progressCurrent: 0,
    progressTotal: null,
    progressUnit: 'artwork',
    counters: [],
    message: 'Maintenance job failed.',
    failure: 'TMDB returned HTTP 404.',
    submittedAt: '2026-09-03T10:00:00Z',
    startedAt: '2026-09-03T10:01:00Z',
    completedAt: '2026-09-03T10:02:00Z',
    cancellable: false,
    clearable: true
  }];

  instance.renderJobs();

  assert.match(instance.innerHTML, /class="k-job-row__progress"/);
  assert.match(instance.innerHTML, /class="k-job-row__actions"><button[^>]*>Details<\/button><button[^>]*data-admin-clear="failure-1"/);
  assert.doesNotMatch(instance.innerHTML, /k-job-row__failure/);
  assert.match(instance.innerHTML, /class="k-job-row__details"[^>]* hidden>/);
  assert.equal((instance.innerHTML.match(/TMDB returned HTTP 404\./g) || []).length, 1);

  instance.toggleJobDetails('failure-1');

  assert.match(instance.innerHTML, /class="k-job-row__details"/);
  assert.doesNotMatch(instance.innerHTML, /class="k-job-row__details"[^>]* hidden>/);
  assert.match(instance.innerHTML, /<small>Submitted /);
}

async function testAdministrationReplacesPriorCompletionWithActionFailure() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.setAttribute('action-source', '/kanvas/actions/administration');
  instance.activity = {state: 'complete', message: 'Resolved 11 duplicate catalogue records.'};
  let renders = 0;
  instance.render = () => { renders += 1; };
  global.fetch = async () => response({status: 422, body: {error: 'A batch may not contain duplicate sources.'}});

  const succeeded = await instance.operation('duplicate-resolve-batch', {resolutions: []});

  assert.equal(succeeded, false);
  assert.equal(renders, 2);
  assert.deepEqual(instance.activity, {
    state: 'error',
    message: 'A batch may not contain duplicate sources.'
  });
}

async function testAdministrationClearingTrackedJobStopsTracking() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.setAttribute('action-source', '/kanvas/actions/administration');
  instance.submittedJobId = 'job-17';
  instance.expandedJobIds.add('job-17');
  instance.render = () => {};
  let loads = 0;
  instance.load = () => { loads += 1; };
  instance.postJson = async (source, payload) => {
    assert.equal(source, '/kanvas/actions/administration');
    assert.deepEqual(payload, {operation: 'clear-job', jobId: 'job-17', confirmed: true});
    return {jobId: 'job-17', action: 'cleared'};
  };

  const succeeded = await instance.operation('clear-job', {jobId: 'job-17', confirmed: true});

  assert.equal(succeeded, true);
  assert.equal(instance.submittedJobId, null);
  assert.equal(instance.expandedJobIds.has('job-17'), false);
  assert.equal(loads, 1);
  assert.deepEqual(instance.activity, {state: 'complete', message: 'Clear job completed.'});
}

async function testAdministrationDirectReferenceSupersedesPendingSearch() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.reviewItems = [{itemId: 17, kind: 'series', title: 'Automan'}];
  instance.renderMetadata = () => {};
  const originalFetch = global.fetch;
  let aborts = 0;
  global.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => {
      aborts += 1;
      const error = new Error('Aborted');
      error.name = 'AbortError';
      reject(error);
    });
  });

  try {
    const search = instance.searchManualMatches('Automan');
    instance.selectManualTmdbReference('12751');
    await search;
  } finally {
    global.fetch = originalFetch;
  }

  assert.equal(aborts, 1);
  assert.equal(instance.manualAbort, null);
  assert.equal(instance.manualSelection, 0);
  assert.deepEqual(instance.manualSearchResults, [{
    provider: 'tmdb',
    provider_id: '12751',
    kind: 'series',
    title: 'TMDB record 12751',
    year: null,
    directReference: true
  }]);
}

function fakeFormValues(values) {
  return {
    get(name) {
      return Object.hasOwn(values, name) ? values[name] : null;
    },
    has(name) {
      return Object.hasOwn(values, name);
    }
  };
}

async function testAdministrationPrimaryFlowKeepsWorkInFourAreas() {
  const operations = [];
  const libraries = new globalThis.__administrationTest.KanvasAdministration();
  libraries.querySelectorAll = () => [];
  libraries.section = 'libraries';
  libraries.roots = [{
    id: 4,
    displayName: 'Films',
    path: '/media/films',
    kind: 'movie',
    tags: ['film'],
    preferredAudioLanguage: 'en',
    preferredSubtitleLanguage: null,
    enabled: true,
    available: true,
    itemCount: 12,
    mediaFileCount: 12,
    lastScanCompletedAt: '2026-09-03T10:00:00Z'
  }];
  libraries.hierarchy = {
    actions: [{kind: 'rename', item_id: 17, item_label: 'Broken title', target_item_id: null, target_label: null, explanation: 'Normalise the title.'}],
    manual_reviews: [],
    impact: {playback_states: 1, metadata_bindings: 1, collection_memberships: 0, watch_order_entries: 0}
  };
  libraries.duplicates = {
    candidates: [{
      source_item_id: 18,
      source_title: 'Old title',
      source_year: 2000,
      target_item_id: 19,
      target_title: 'New title',
      target_year: 2000,
      provider: 'tmdb',
      provider_id: '19',
      impact: {playback_states: 1, metadata_bindings: 0, collection_memberships: 0, watch_order_entries: 0}
    }],
    fileIssues: []
  };
  libraries.operation = async (operation, payload) => {
    operations.push({operation, payload});
    return true;
  };

  libraries.renderLibraries();

  assert.match(libraries.innerHTML, /Add root/);
  assert.match(libraries.innerHTML, /Scan all/);
  assert.match(libraries.innerHTML, /Structural issues/);
  assert.match(libraries.innerHTML, /Duplicate issues/);
  await libraries.saveRoot(null, fakeFormValues({
    displayName: 'Series',
    path: '/media/series',
    kind: 'series',
    tags: 'tv, sci-fi',
    preferredAudioLanguage: 'en',
    preferredSubtitleLanguage: '',
    enabled: 'on'
  }));
  await libraries.operation('scan', {rootId: 4});
  await libraries.operation('hierarchy-repair', {apply: true, confirmed: true});
  await libraries.operation('duplicate-resolve', {sourceItemId: 18, targetItemId: 19, confirmed: true});
  assert.deepEqual(operations.slice(0, 4), [
    {
      operation: 'root-create',
      payload: {
        rootId: null,
        displayName: 'Series',
        path: '/media/series',
        kind: 'series',
        tags: ['tv', 'sci-fi'],
        preferredAudioLanguage: 'en',
        preferredSubtitleLanguage: '',
        enabled: true
      }
    },
    {operation: 'scan', payload: {rootId: 4}},
    {operation: 'hierarchy-repair', payload: {apply: true, confirmed: true}},
    {operation: 'duplicate-resolve', payload: {sourceItemId: 18, targetItemId: 19, confirmed: true}}
  ]);

  const metadata = new globalThis.__administrationTest.KanvasAdministration();
  metadata.querySelectorAll = () => [];
  metadata.section = 'metadata';
  metadata.reviewItems = [{
    itemId: 19,
    title: 'New title',
    year: 2000,
    kind: 'movie',
    posterUrl: null,
    candidates: [{id: 3, provider: 'tmdb', providerId: '19', title: 'New title', year: 2000, kind: 'movie', confidence: 0.92, status: 'suggested'}]
  }];
  const metadataOperations = [];
  metadata.operation = async (operation, payload) => {
    metadataOperations.push({operation, payload});
    return true;
  };

  metadata.renderMetadata();

  assert.match(metadata.innerHTML, /Find or enter a match/);
  await metadata.metadataAction('match');
  assert.deepEqual(metadataOperations, [{
    operation: 'match',
    payload: {itemId: 19, provider: 'tmdb', providerId: '19'}
  }]);
  assert.equal(metadata.reviewedItemCount, 1);
  assert.match(metadata.innerHTML, /Metadata review is clear/);
  assert.match(metadata.innerHTML, /Select artwork/);

  metadata.reviewItems = [{
    itemId: 20,
    title: 'No suggestion',
    year: null,
    kind: 'movie',
    posterUrl: null,
    candidates: []
  }];
  metadata.reviewIndex = 0;
  metadata.manualSearchOpen = true;
  metadata.selectManualTmdbReference('https://www.themoviedb.org/movie/20-manual-title');
  assert.match(metadata.innerHTML, /TMDB link or ID/);
  assert.match(metadata.innerHTML, /TMDB record 20/);
  assert.match(metadata.innerHTML, /Direct record/);
  metadata.postJson = async (source, payload) => {
    assert.equal(source, '/kanvas/actions/items/20/metadata-match');
    assert.deepEqual(payload, {provider: 'tmdb', providerId: '20', confirmed: true});
    return {itemId: 20};
  };
  const confirm = window.confirm;
  window.confirm = () => true;
  await metadata.applyManualMatch();
  window.confirm = confirm;
  assert.equal(metadata.reviewedItemCount, 2);

  metadata.subsection = 'artwork';
  metadata.overview = {artworkCacheSizeBytes: 1024, artworkCacheFileCount: 2};
  metadata.renderArtworkMaintenance();
  assert.match(metadata.innerHTML, /Fetch missing artwork/);

  const jobs = new globalThis.__administrationTest.KanvasAdministration();
  jobs.querySelectorAll = () => [];
  jobs.section = 'jobs';
  jobs.jobs = [
    {id: 'scan-1', kind: 'scan', status: 'running', phase: 'classifying', progressCurrent: 4, progressTotal: 10, progressUnit: 'files', counters: [], submittedAt: '2026-09-03T10:00:00Z', startedAt: null, completedAt: null, cancellable: true},
    {id: 'repair-1', kind: 'hierarchy-repair', status: 'failed', phase: null, progressCurrent: 0, progressTotal: null, progressUnit: null, counters: [], message: null, failure: 'Repair could not run.', submittedAt: '2026-09-03T10:00:00Z', startedAt: null, completedAt: null, cancellable: false},
    {id: 'scan-0', kind: 'scan', status: 'completed', phase: null, progressCurrent: 10, progressTotal: 10, progressUnit: 'files', counters: [], submittedAt: '2026-09-03T09:00:00Z', startedAt: null, completedAt: '2026-09-03T09:10:00Z', cancellable: false}
  ];
  jobs.renderJobs();
  assert.match(jobs.innerHTML, /Active and problem jobs first/);
  assert.doesNotMatch(jobs.innerHTML, /No active or problem jobs/);
  assert.match(jobs.innerHTML, /Completed history \(1\)/);
  assert.ok(jobs.innerHTML.indexOf('scan-1') < jobs.innerHTML.indexOf('scan-0'));
}

function testItemEditorShowsOnlyRelevantKindFields() {
  const editor = new globalThis.__itemEditorTest.KanvasItemEditor();
  const item = {season_number: 1, episode_number: 2, parent_id: 8};

  assert.equal(editor.renderKindFields('movie', item), '');
  assert.match(editor.renderKindFields('season', item), /name="seasonNumber"/);
  assert.doesNotMatch(editor.renderKindFields('season', item), /name="episodeNumber"/);
  assert.match(editor.renderKindFields('episode', item), /name="seasonNumber"/);
  assert.match(editor.renderKindFields('episode', item), /name="episodeNumber"/);
  assert.match(editor.renderHierarchyFields('movie', item), /Top-level item/);
  editor.parentChoices = [{id: 8, title: 'Stargate', kind: 'series'}];
  const hierarchy = editor.renderHierarchyFields('episode', item);
  assert.match(hierarchy, /name="parentId"/);
  assert.match(hierarchy, /Stargate · Series/);
  assert.doesNotMatch(hierarchy, /Parent ID/);
  assert.doesNotMatch(editor.renderLockRows('movie', new Set()), /Episode number/);
  assert.match(editor.renderLockRows('episode', new Set(['episode_number'])), /Episode number/);
}

function testItemEditorUsesTaskFocusedTabs() {
  const editor = new globalThis.__itemEditorTest.KanvasItemEditor();

  assert.deepEqual(
    editor.editorTabs(false).map((tab) => tab.id),
    ['details', 'match', 'organise', 'artwork', 'history']
  );
  assert.deepEqual(
    editor.editorTabs(true).map((tab) => tab.id),
    ['details', 'match', 'organise', 'artwork', 'playback', 'history']
  );
  assert.equal(editor.availableTab('playback', editor.editorTabs(false)), 'details');
  assert.equal(editor.availableTab('history', editor.editorTabs(false)), 'history');
  editor.activeTab = 'match';
  assert.match(editor.renderTabNavigation(editor.editorTabs(false)), /role="tablist"/);
  assert.match(editor.renderTabNavigation(editor.editorTabs(false)), /aria-selected="true"/);
  assert.match(
    editor.renderMatchTab(
      'movie',
      new Set(),
      {provider: 'tmdb', provider_id: '314', title: 'Pan’s Labyrinth', year: 2006, kind: 'movie'},
      'Pan’s Labyrinth'
    ),
    /Current metadata match/
  );
  assert.match(
    editor.renderMatchTab('movie', new Set(), null, 'Pan’s Labyrinth'),
    /Search database/
  );
  assert.match(editor.renderOrganiseTab('movie', {}, ''), /Library organisation/);
  assert.match(
    editor.renderArtworkTab('', 'series', {provider: 'tmdb', provider_id: '63712'}),
    /Load artwork choices/
  );
  assert.match(
    editor.renderArtworkTab('', 'movie', null, true),
    /name="showArtworkLabel"[^>]* checked/
  );
  assert.doesNotMatch(
    editor.renderArtworkTab('', 'movie', null, false),
    /name="showArtworkLabel"[^>]* checked/
  );
  assert.match(editor.renderArtworkTab('', 'season', null), /Load artwork choices/);
  assert.match(editor.renderArtworkTab('', 'episode', null), /Load artwork choices/);
}

function testMetadataProviderLinksSupportDirectReassignment() {
  const {tmdbEntryReferenceFromUrl, tmdbEntryReferenceFromValue, providerEntryUrl} = window.kanvasInternals;
  const editor = new globalThis.__itemEditorTest.KanvasItemEditor();

  assert.equal(
    providerEntryUrl({provider: 'tmdb', provider_id: '550', kind: 'movie'}),
    'https://www.themoviedb.org/movie/550'
  );
  assert.equal(
    providerEntryUrl({provider: 'tmdb', providerId: '1399', kind: 'series'}),
    'https://www.themoviedb.org/tv/1399'
  );
  assert.deepEqual(
    tmdbEntryReferenceFromUrl('https://www.themoviedb.org/en-AU/movie/550-fight-club', 'movie'),
    {provider: 'tmdb', provider_id: '550', kind: 'movie'}
  );
  assert.equal(tmdbEntryReferenceFromUrl('https://www.imdb.com/title/tt0137523/', 'movie'), null);
  assert.equal(
    tmdbEntryReferenceFromUrl('https://www.themoviedb.org/tv/1399-game-of-thrones', 'movie'),
    null
  );
  assert.equal(tmdbEntryReferenceFromUrl('https://example.com/movie/550', 'movie'), null);
  assert.deepEqual(
    tmdbEntryReferenceFromValue('12751', 'series'),
    {provider: 'tmdb', provider_id: '12751', kind: 'series'}
  );
  assert.deepEqual(
    tmdbEntryReferenceFromValue('https://www.themoviedb.org/tv/12751-automan', 'series'),
    {provider: 'tmdb', provider_id: '12751', kind: 'series'}
  );
  assert.equal(tmdbEntryReferenceFromValue('12751', 'season'), null);
  editor.currentItem = {kind: 'series'};
  const directMatch = editor.metadataMatchFromLink(
    'https://www.themoviedb.org/tv/63712-acchi-kocchi'
  );
  assert.deepEqual(
    directMatch,
    {provider: 'tmdb', provider_id: '63712', kind: 'series', title: 'TMDB record 63712', year: null}
  );
  assert.equal(editor.metadataMatchFromLink('https://www.themoviedb.org/movie/550-fight-club'), null);
  const confirmation = new FakeElement('section');
  const applyButton = new FakeElement('button');
  confirmation.hidden = true;
  confirmation.querySelector = () => applyButton;
  editor.querySelector = (selector) => selector === '[data-item-editor-content]'
    ? {querySelector: (target) => target === '[data-item-match-confirmation]' ? confirmation : null}
    : null;
  editor.selectMetadataMatchResult(directMatch);
  assert.equal(confirmation.hidden, false);
  assert.match(confirmation.innerHTML, /Apply selected match/);
  assert.match(
    editor.renderMatchTab(
      'movie',
      new Set(),
      {provider: 'tmdb', provider_id: '550', title: 'Fight Club', year: 1999, kind: 'movie'},
      'Fight Club'
    ),
    /href="https:\/\/www\.themoviedb\.org\/movie\/550"/
  );
  const matchTab = editor.renderMatchTab('movie', new Set(), null, 'Fight Club');
  assert.match(matchTab, /Or paste a TMDB link/);
  assert.match(matchTab, /Review link match/);
  assert.match(matchTab, /Save local edits does not change the metadata association/);
}

function testItemEditorConfirmsDiscardingUnsavedChanges() {
  const editor = new globalThis.__itemEditorTest.KanvasItemEditor();
  const confirm = window.confirm;
  let prompts = 0;
  window.confirm = () => { prompts += 1; return false; };
  editor.isDirty = true;
  assert.equal(editor.confirmDiscard(), false);
  editor.isSaving = true;
  assert.equal(editor.confirmDiscard(), false);
  assert.equal(prompts, 1);
  window.confirm = confirm;
}

function testItemEditorHidesForceControlsForAutomaticDefaults() {
  const editor = new globalThis.__itemEditorTest.KanvasItemEditor();
  const choice = new HTMLSelectElement();
  choice.value = '';
  const force = new HTMLInputElement();
  force.checked = true;
  const forceControl = {hidden: false};
  force.closest = () => forceControl;
  const content = {
    querySelector(selector) {
      if (selector === '[name="defaultAudioStreamIndex"]') return choice;
      if (selector === '[name="forceDefaultAudioStream"]') return force;
      return null;
    }
  };

  editor.bindPlaybackForceControls(content);

  assert.equal(forceControl.hidden, true);
  assert.equal(force.disabled, true);
  assert.equal(force.checked, false);
  choice.value = '1';
  choice.listeners.get('change')[0].listener();
  assert.equal(forceControl.hidden, false);
  assert.equal(force.disabled, false);
}

function testItemEditorPayloadPreservesHiddenState() {
  const editor = new globalThis.__itemEditorTest.KanvasItemEditor();
  editor.currentItem = {
    kind: 'episode',
    season_number: 1,
    episode_number: 2,
    parent_id: 8,
    show_artwork_label: true
  };
  editor.lockedMetadataFields = new Set(['overview', 'episode_number']);
  editor.initialSelectedArtwork = new Map([['poster', 8], ['still', 10]]);
  const form = {
    querySelectorAll(selector) {
      if (selector === '[data-artwork-kind]:checked') {
        return [
          {value: '', dataset: {artworkKind: 'poster'}},
          {value: '12', dataset: {artworkKind: 'backdrop'}}
        ];
      }
      if (selector === '[data-artwork-kind]') {
        return [
          {dataset: {artworkKind: 'poster'}},
          {dataset: {artworkKind: 'backdrop'}}
        ];
      }
      if (selector === 'input[name="lock"]') {
        return [
          {value: 'title', checked: true},
          {value: 'overview', checked: false}
        ];
      }
      if (selector === 'input[name="lock"]:checked') {
        return [{value: 'title'}];
      }
      return [];
    }
  };

  const payload = editor.payloadFromForm(form, fakeFormValues({
    title: 'Movie',
    sortTitle: 'Movie',
    overview: '',
    releaseDate: '',
    releaseYear: '',
    tags: 'anime, favourite',
    kind: 'movie'
  }));

  assert.equal(payload.parentId, null);
  assert.equal(payload.seasonNumber, null);
  assert.equal(payload.episodeNumber, null);
  assert.equal(payload.showArtworkLabel, false);
  assert.deepEqual(payload.lockedMetadataFields.sort(), ['episode_number', 'title']);
  assert.deepEqual(payload.selectedArtwork.sort((left, right) => left.kind.localeCompare(right.kind)), [
    {kind: 'backdrop', artworkId: 12},
    {kind: 'still', artworkId: 10}
  ]);

  const labelledPayload = editor.payloadFromForm(form, fakeFormValues({
    title: 'Movie',
    sortTitle: 'Movie',
    overview: '',
    releaseDate: '',
    releaseYear: '',
    tags: 'anime, favourite',
    kind: 'movie',
    showArtworkLabel: 'on'
  }));

  assert.equal(labelledPayload.showArtworkLabel, true);
}

function testItemEditorPayloadDoesNotForceAutomaticPlaybackDefaults() {
  const editor = new globalThis.__itemEditorTest.KanvasItemEditor();
  editor.lockedMetadataFields = new Set();
  const form = {querySelectorAll: () => []};

  const payload = editor.payloadFromForm(form, fakeFormValues({
    title: 'Movie',
    sortTitle: 'Movie',
    overview: '',
    releaseDate: '',
    releaseYear: '',
    tags: '',
    kind: 'movie',
    defaultAudioStreamIndex: '',
    forceDefaultAudioStream: 'on',
    defaultSubtitleTrackId: '',
    forceDefaultSubtitleTrack: 'on',
    defaultSubtitleTimingOffsetMilliseconds: '',
    defaultSubtitleFontScalePercent: '',
    forceDefaultSubtitleFontScale: 'on'
  }));

  assert.equal(payload.defaultAudioStreamIndex, null);
  assert.equal(payload.forceDefaultAudioStream, false);
  assert.equal(payload.defaultSubtitleTrackId, null);
  assert.equal(payload.forceDefaultSubtitleTrack, false);
  assert.equal(payload.defaultSubtitleFontScalePercent, null);
  assert.equal(payload.forceDefaultSubtitleFontScale, false);
}

async function main() {
  await testValidPageRetainsAvailable();
  testPosterArtworkLabelNormalisation();
  testPosterArtworkLabelMarkup();
  testPosterPartialWatchNormalisation();
  testLandscapePosterMarkup();
  testLibraryFilterUrlKeepsOnlyActiveUrlState();
  testLibraryFilterInputsWaitForCommit();
  testLibraryGridKeepsOneCardGeometryPerResultSet();
  testLibraryGridMarkupUsesOneGeometryPerFocusedResult();
  testGridLayoutDoesNotConflictWithFrameworkProperties();
  testLibraryGridStylesUseResponsiveGeometryWithoutCardSpans();
  testPosterMosaicAndHomeActionNormalisation();
  testPosterStatusBadgeMarkup();
  testRailControlsHideWhenViewportDoesNotOverflow();
  testPosterNormalisationAllowsOnlySafeItemAndResumeLinks();
  testWatchOrderInsertionSlotsRejectOnlyNoOpMoves();
  await testCategorisedFailureAndRetry();
  await testMalformedResponsesAndPosters();
  await testCancellationStateAndDevelopmentDiagnostics();
  await testStateInvalidationAndRenderingFailure();
  await testBidirectionalVirtualPagesRehydrateEvictedCards();
  await testVisibleTailLoadsTheNextPageWithoutObserverChurn();
  testResponsiveVirtualSpacersTrackMountedGridGeometry();
  testKeyboardNavigationLoadsAcrossVirtualPageEdges();
  await testAdministrationPollingWaitsForOpenDialog();
  await testAdministrationReportsTrackedJobProgressWithoutChangingTab();
  await testAdministrationStopsTrackingProblemJobs();
  await testAdministrationReusesLoadedJobPageForTracking();
  testAdministrationPollsTrackedJobsFrequently();
  testAdministrationPollsActiveJobsFrequently();
  await testAdministrationContinuesMetadataReviewPages();
  testAdministrationKeepsEncodedJobAnchorsTargetable();
  testAdministrationJobCardsKeepActionsAndDetailsSeparate();
  await testAdministrationReplacesPriorCompletionWithActionFailure();
  await testAdministrationClearingTrackedJobStopsTracking();
  await testAdministrationDirectReferenceSupersedesPendingSearch();
  await testAdministrationPrimaryFlowKeepsWorkInFourAreas();
  testItemEditorShowsOnlyRelevantKindFields();
  testItemEditorUsesTaskFocusedTabs();
  testMetadataProviderLinksSupportDirectReassignment();
  testItemEditorConfirmsDiscardingUnsavedChanges();
  testItemEditorHidesForceControlsForAutomaticDefaults();
  testItemEditorPayloadPreservesHiddenState();
  testItemEditorPayloadDoesNotForceAutomaticPlaybackDefaults();
  process.stdout.write('browser library grid checks passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
