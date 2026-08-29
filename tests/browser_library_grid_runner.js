const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.attributes = new Map();
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

  click() {
    const listeners = this.listeners.get('click') || [];
    for (const entry of [...listeners]) {
      entry.listener();
      if (entry.once) listeners.splice(listeners.indexOf(entry), 1);
    }
  }

  contains() {
    return false;
  }

  getBoundingClientRect() {
    const index = this.parentElement ? this.parentElement.children.indexOf(this) : 0;
    const row = index >= 0 ? Math.floor(index / (this.parentElement?.columnCount || this.columnCount)) : 0;
    return {height: this.rowHeight, width: 100, top: row * this.rowHeight};
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
let throwPosterCreation = false;

global.HTMLElement = FakeHTMLElement;
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
  addEventListener() {},
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
  setTimeout() {}
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
  "globalThis.__libraryTest = {KanvasPosterGrid, normalisePoster, posterMarkup, libraryGridPayload, updateRailControls};\n  if (!customElements.get('kanvas-poster-grid')) customElements.define('kanvas-poster-grid', KanvasPosterGrid);"
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
  schemaVersion: 1,
  items,
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
  instance.grid = new FakeElement('div');
  instance.status = new FakeElement('div');
  instance.sentinel = new FakeElement('div');
  instance.stateKey = instance.buildStateKey(instance.getAttribute('source'));
  instance.generation = 1;
  return instance;
};

const nextTick = () => new Promise((resolve) => setImmediate(resolve));

async function testValidPageRetainsAvailable() {
  const instance = grid();
  global.fetch = async () => response({});
  await instance.loadNext();
  assert.equal(instance.posters.length, 1);
  assert.equal(instance.posters[0].available, true);
  assert.equal(instance.grid.children.length, 1);
  assert.equal(instance.requestId, 'request-123');
  assert.equal(instance.status.textContent, 'End of library.');
}

function testPosterPlaceholderNormalisation() {
  const poster = globalThis.__libraryTest.normalisePoster({
    ...validPoster(11),
    context: ' The show ',
    posterUrl: null,
    placeholder: {lines: [' Main title ', '', 'Subtitle'], footer: ' S01 E02 '}
  });

  assert.equal(poster.posterUrl, null);
  assert.equal(poster.context, 'The show');
  assert.deepEqual(poster.placeholder.lines, ['Main title', 'Subtitle']);
  assert.equal(poster.placeholder.footer, 'S01 E02');
  assert.deepEqual(
    globalThis.__libraryTest.normalisePoster(validPoster(12)).placeholder.lines,
    ['Poster 12']
  );
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
  let calls = 0;
  global.fetch = async () => {
    calls += 1;
    return calls === 1
      ? response({status: 503, body: {error: {requestId: 'retry-request'}}})
      : response({});
  };
  await instance.loadNext();
  assert.equal(instance.retryRequired, true);
  assert.equal(instance.status.textContent, 'Could not load this part of the library.');
  const diagnostic = instance.status.children.find((child) => child.tagName === 'details');
  assert.match(diagnostic.children[1].textContent, /Category: http_failure/);
  assert.match(diagnostic.children[1].textContent, /HTTP status: 503/);
  assert.match(diagnostic.children[1].textContent, /Request ID: retry-request/);
  instance.status.children.find((child) => child.tagName === 'button').click();
  await nextTick();
  assert.equal(calls, 2);
  assert.equal(instance.posters.length, 1);
  assert.equal(instance.retryRequired, false);
}

async function testMalformedResponsesAndPosters() {
  const invalidContentType = grid();
  global.fetch = async () => response({contentType: 'text/html'});
  await invalidContentType.loadNext();
  assert.match(invalidContentType.status.children.find((child) => child.tagName === 'details').children[1].textContent, /invalid_content_type/);

  const invalidJson = grid();
  global.fetch = async () => response({jsonError: new SyntaxError('bad json')});
  await invalidJson.loadNext();
  assert.match(invalidJson.status.children.find((child) => child.tagName === 'details').children[1].textContent, /invalid_json/);

  const invalidEnvelope = grid();
  global.fetch = async () => response({body: {items: []}});
  await invalidEnvelope.loadNext();
  assert.match(invalidEnvelope.status.children.find((child) => child.tagName === 'details').children[1].textContent, /invalid_envelope/);

  const oneMalformed = grid();
  global.fetch = async () => response({body: validEnvelope([validPoster(7), {id: 8, title: 'Broken'}])});
  await oneMalformed.loadNext();
  assert.equal(oneMalformed.posters.length, 1);
  assert.equal(oneMalformed.invalidPosterCount, 1);
  assert.match(oneMalformed.status.textContent, /1 item could not be displayed/);
  assert.deepEqual(consoleErrors.at(-1)[1], {itemIds: [8]});

  const allMalformed = grid();
  global.fetch = async () => response({body: validEnvelope([{id: 9, title: 'Broken'}])});
  await allMalformed.loadNext();
  assert.equal(allMalformed.posters.length, 0);
  assert.equal(allMalformed.done, true);
  assert.equal(allMalformed.retryRequired, false);
  assert.match(allMalformed.status.textContent, /1 item could not be displayed/);
}

async function testCancellationStateAndDevelopmentDiagnostics() {
  const stale = grid();
  global.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
  });
  const pending = stale.loadNext();
  await nextTick();
  stale.generation += 1;
  stale.requestController.abort();
  await pending;
  assert.notEqual(stale.status.textContent, 'Could not load this part of the library.');

  const production = grid(false);
  const errorsBefore = consoleErrors.length;
  global.fetch = async () => { throw new TypeError('offline'); };
  await production.loadNext();
  assert.equal(consoleErrors.length, errorsBefore);

  const development = grid(true);
  await development.loadNext();
  assert.equal(consoleErrors.at(-1)[1].category, 'network_failure');
}

async function testStateInvalidationAndRenderingFailure() {
  const instance = grid();
  assert.match(instance.stateKey, /v5:asset=test-asset:catalogue=1%3A2026-07-24T11%3A50%3A00%2B00%3A00:user=4:filters=/);
  assert.match(decodeURIComponent(instance.stateKey), /kind=movie&search=alpha/);
  const previousKey = instance.stateKey;
  instance.setAttribute('catalogue-revision', '1:2026-07-24T12:00:00+00:00');
  assert.notEqual(instance.buildStateKey(instance.getAttribute('source')), previousKey);
  storage.set(instance.stateKey, JSON.stringify({
    schemaVersion: 5,
    asset: 'test-asset',
    filters: '/kanvas/data/library?kind=movie&search=alpha',
    user: '4',
    cursor: null,
    completed: true,
    outcome: 'success',
    posters: [validPoster()],
    scrollY: 0
  }));
  assert.equal(instance.restoreState(), false);
  assert.equal(storage.has(instance.stateKey), false);

  const renderer = grid();
  throwPosterCreation = true;
  global.fetch = async () => response({});
  await renderer.loadNext();
  throwPosterCreation = false;
  const diagnostic = renderer.status.children.find((child) => child.tagName === 'details');
  assert.match(diagnostic.children[1].textContent, /rendering_failure/);
}

async function testRowAwareTrimPreservesScrollAnchor() {
  const instance = grid();
  for (let index = 1; index <= 150; index += 1) {
    instance.grid.append(new FakeElement('kanvas-poster'));
  }
  const scrollByCallsBefore = window.scrollByCalls.length;
  instance.trimMountedPosters();
  assert.equal(instance.grid.children.length, 140);
  assert.equal(instance.mountedStart, 10);
  assert.equal(window.scrollByCalls.length, scrollByCallsBefore + 1);
  assert.deepEqual(window.scrollByCalls.at(-1), [0, -200]);
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

async function testAdministrationReplacesPriorCompletionWithActionFailure() {
  const instance = new globalThis.__administrationTest.KanvasAdministration();
  instance.setAttribute('action-source', '/kanvas/actions/administration');
  instance.activity = {state: 'complete', message: 'Resolved 11 duplicate catalogue records.'};
  let renders = 0;
  instance.render = () => { renders += 1; };
  global.fetch = async () => response({status: 422, body: {error: 'A batch may not contain duplicate sources.'}});

  const succeeded = await instance.operation('duplicate-resolve-batch', {resolutions: []});

  assert.equal(succeeded, false);
  assert.equal(renders, 1);
  assert.deepEqual(instance.activity, {
    state: 'error',
    message: 'A batch may not contain duplicate sources.'
  });
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
  assert.match(editor.renderArtworkTab('', 'season', null), /Load artwork choices/);
  assert.match(editor.renderArtworkTab('', 'episode', null), /Load artwork choices/);
}

function testMetadataProviderLinksSupportDirectReassignment() {
  const {tmdbEntryReferenceFromUrl, providerEntryUrl} = window.kanvasInternals;
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
  editor.currentItem = {kind: 'episode', season_number: 1, episode_number: 2, parent_id: 8};
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
  assert.deepEqual(payload.lockedMetadataFields.sort(), ['episode_number', 'title']);
  assert.deepEqual(payload.selectedArtwork.sort((left, right) => left.kind.localeCompare(right.kind)), [
    {kind: 'backdrop', artworkId: 12},
    {kind: 'still', artworkId: 10}
  ]);
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
  testPosterPlaceholderNormalisation();
  testPosterPartialWatchNormalisation();
  testLandscapePosterMarkup();
  testPosterMosaicAndHomeActionNormalisation();
  testPosterStatusBadgeMarkup();
  testRailControlsHideWhenViewportDoesNotOverflow();
  testPosterNormalisationAllowsOnlySafeItemAndResumeLinks();
  testWatchOrderInsertionSlotsRejectOnlyNoOpMoves();
  await testCategorisedFailureAndRetry();
  await testMalformedResponsesAndPosters();
  await testCancellationStateAndDevelopmentDiagnostics();
  await testStateInvalidationAndRenderingFailure();
  await testRowAwareTrimPreservesScrollAnchor();
  await testAdministrationPollingWaitsForOpenDialog();
  await testAdministrationReportsTrackedJobProgressWithoutChangingTab();
  testAdministrationPollsTrackedJobsFrequently();
  await testAdministrationReplacesPriorCompletionWithActionFailure();
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
