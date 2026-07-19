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
    return {height: 100, width: 100};
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
  addEventListener() {},
  removeEventListener() {},
  scrollBy() {},
  scrollTo() {},
  history: {back() {}},
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

const source = fs.readFileSync('src/kasana/kanvas/static/kanvas.js', 'utf8');
const exposed = source.replace(
  "if (!customElements.get('kanvas-poster-grid')) customElements.define('kanvas-poster-grid', KanvasPosterGrid);",
  "globalThis.__libraryTest = {KanvasPosterGrid, normalisePoster, libraryGridPayload};\n  if (!customElements.get('kanvas-poster-grid')) customElements.define('kanvas-poster-grid', KanvasPosterGrid);"
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
  assert.match(instance.stateKey, /v3:asset=test-asset:user=4:filters=/);
  assert.match(decodeURIComponent(instance.stateKey), /kind=movie&search=alpha/);
  storage.set(instance.stateKey, JSON.stringify({
    schemaVersion: 2,
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

async function main() {
  await testValidPageRetainsAvailable();
  await testCategorisedFailureAndRetry();
  await testMalformedResponsesAndPosters();
  await testCancellationStateAndDevelopmentDiagnostics();
  await testStateInvalidationAndRenderingFailure();
  process.stdout.write('browser library grid checks passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
