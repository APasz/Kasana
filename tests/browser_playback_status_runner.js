const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeElement {
  constructor() {
    this.attributes = new Map();
    this.classList = {add() {}, remove() {}};
    this.dataset = {};
    this.hidden = true;
    this.listeners = new Map();
    this.style = {
      properties: new Map(),
      getPropertyValue(name) { return this.properties.get(name) || ''; },
      removeProperty(name) { this.properties.delete(name); },
      setProperty(name, value) { this.properties.set(name, String(value)); }
    };
    this.textContent = '';
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }

  emit(name, event = {}) {
    for (const listener of this.listeners.get(name) || []) listener(event);
  }

  getAttribute(name) {
    return this.attributes.get(name) || null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  getBoundingClientRect() {
    return {height: 180, width: 320, top: 0, left: 0};
  }

  querySelector() {
    return null;
  }

  querySelectorAll() {
    return [];
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  remove() {
    if (this.parentElement) {
      this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
    }
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  toggleAttribute(name, force) {
    if (force) this.setAttribute(name, '');
    else this.removeAttribute(name);
  }
}

class FakeVideo extends FakeElement {
  constructor() {
    super();
    this.currentTime = 0;
    this.duration = 120;
    this.muted = false;
    this.paused = true;
    this.playbackRate = 1;
    this.volume = 1;
    this.buffered = new FakeTimeRanges();
    this.children = [];
    this.playCalls = 0;
  }

  canPlayType() {
    return 'probably';
  }

  load() {}

  play() {
    this.playCalls += 1;
    return Promise.resolve();
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  querySelectorAll(selector) {
    if (selector === 'track[data-player-subtitle]') {
      return this.children.filter((child) => child.dataset.playerSubtitle === 'true');
    }
    return [];
  }
}

class FakeCue {
  constructor(startTime, endTime) {
    this.endTime = endTime;
    this.line = 'auto';
    this.snapToLines = true;
    this.startTime = startTime;
  }
}

class FakeTextTrack {
  constructor() {
    this.cues = [new FakeCue(10, 20)];
    this.mode = 'disabled';
  }

  addCue(cue) {
    if (!this.cues.includes(cue)) this.cues.push(cue);
  }

  removeCue(cue) {
    this.cues = this.cues.filter((candidate) => candidate !== cue);
  }
}

class FakeTrack extends FakeElement {
  constructor() {
    super();
    this.track = new FakeTextTrack();
  }
}

class FakeTimeRanges {
  constructor(ranges = []) {
    this.ranges = ranges;
  }

  get length() {
    return this.ranges.length;
  }

  start(index) {
    return this.ranges[index][0];
  }

  end(index) {
    return this.ranges[index][1];
  }
}

global.Element = FakeElement;
global.HTMLElement = FakeElement;
global.HTMLAnchorElement = FakeElement;
global.HTMLInputElement = FakeElement;
global.customElements = {
  constructors: new Map(),
  define(name, constructor) {
    this.constructors.set(name, constructor);
  },
  get(name) {
    return this.constructors.get(name);
  }
};
global.document = {
  addEventListener() {},
  createElement(name) {
    if (name === 'track') return new FakeTrack();
    return new FakeElement();
  },
  removeEventListener() {},
  querySelector() { return null; },
  fullscreenElement: null
};
global.navigator = {getGamepads() { return []; }};
global.CSS = {supports() { return true; }};
const scheduledTimeouts = new Map();
let nextTimeoutId = 1;
global.window = {
  addEventListener() {},
  clearTimeout(id) { scheduledTimeouts.delete(id); },
  history: {replaceState() {}},
  location: {origin: 'http://kanvas.test'},
  removeEventListener() {},
  setTimeout(callback) {
    const id = nextTimeoutId;
    nextTimeoutId += 1;
    scheduledTimeouts.set(id, callback);
    return id;
  }
};
const fetchCalls = [];
let completionPayload = null;
global.fetch = async (url, options = {}) => {
  fetchCalls.push({options, url});
  if (String(url).includes('/complete')) {
    return {ok: true, json: async () => completionPayload || {nextEntry: null, nextUrl: null}};
  }
  if (String(url).includes('/tracks')) {
    return {
      ok: true,
      json: async () => ({
        audioStream: 0,
        subtitleBackground: false,
        subtitleFontScalePercent: 100,
        subtitleOffsetMilliseconds: 0,
        subtitleShadow: false,
        subtitleTrack: 'sidecar-0',
        subtitleVerticalPosition: 'author'
      })
    };
  }
  return {ok: true, json: async () => ({mode: 'direct', mediaUrl: '/media/video.mp4'})};
};

vm.runInThisContext(fs.readFileSync('src/kasana/kanvas/static/kanvas.js', 'utf8'), {
  filename: 'kanvas.js'
});

const nextTick = () => new Promise((resolve) => setImmediate(resolve));

function runScheduledTimeouts() {
  const callbacks = Array.from(scheduledTimeouts.values());
  scheduledTimeouts.clear();
  for (const callback of callbacks) callback();
}

function createPlayer() {
  const Player = customElements.get('kanvas-playback-player');
  const player = new Player();
  const video = new FakeVideo();
  const status = new FakeElement();
  const controls = new FakeElement();
  const timeline = new FakeElement();
  const bufferedIndicator = new FakeElement();
  const currentTime = new FakeElement();
  const remainingTime = new FakeElement();
  const volume = new FakeElement();
  const contextMenu = new FakeElement();
  const audioMenu = new FakeElement();
  const subtitleMenu = new FakeElement();
  const subtitleTimingLabel = new FakeElement();
  const subtitleFontScaleLabel = new FakeElement();
  const subtitleAppearance = new FakeElement();
  const nativeControls = new FakeElement();
  const kestrelLink = new HTMLAnchorElement();
  const elements = new Map([
    ['video', video],
    ['.k-player__status', status],
    ['.k-player__controls', controls],
    ['[data-player-timeline]', timeline],
    ['[data-player-buffered]', bufferedIndicator],
    ['[data-player-current-time]', currentTime],
    ['[data-player-remaining-time]', remainingTime],
    ['[data-player-volume]', volume],
    ['[data-player-context-menu]', contextMenu],
    ['[data-player-audio-menu]', audioMenu],
    ['[data-player-subtitle-menu]', subtitleMenu],
    ['[data-player-native-controls]', nativeControls],
    ['[data-player-kestrel]', kestrelLink]
  ]);
  const subtitleTrack = new FakeElement();
  subtitleTrack.setAttribute('aria-pressed', 'true');
  subtitleTrack.setAttribute('data-player-subtitle-format', 'webvtt');
  subtitleTrack.setAttribute('data-player-subtitle-track', 'sidecar-0');
  const timingEarlier = new FakeElement();
  timingEarlier.setAttribute('data-player-subtitle-timing-step', '-500');
  const subtitleBackground = new FakeElement();
  subtitleBackground.setAttribute('data-player-subtitle-background', '');
  const subtitlePositionTop = new FakeElement();
  subtitlePositionTop.setAttribute('data-player-subtitle-position', 'top');
  subtitleMenu.querySelector = (selector) => {
    if (selector === '[data-player-subtitle-timing-label]') return subtitleTimingLabel;
    if (selector === '[data-player-subtitle-font-scale-label]') return subtitleFontScaleLabel;
    if (selector === '[data-player-subtitle-appearance]') return subtitleAppearance;
    if (selector === '[data-player-subtitle-track][aria-pressed="true"]') return subtitleTrack;
    if (selector === '[data-player-subtitle-background]') return subtitleBackground;
    const trackId = selector.match(/^\[data-player-subtitle-track="(.+)"\]$/)?.[1];
    return trackId === 'sidecar-0' ? subtitleTrack : null;
  };
  subtitleMenu.querySelectorAll = (selector) => {
    if (selector.includes('[data-player-subtitle-track]')) return [subtitleTrack];
    if (selector.includes('[data-player-subtitle-timing-step]')) return [timingEarlier];
    if (selector.includes('[data-player-subtitle-background]')) return [subtitleBackground, subtitlePositionTop];
    if (selector.includes('[data-player-subtitle-position]')) return [subtitlePositionTop];
    return [];
  };
  subtitleTrack.closest = (selector) => selector === '[data-player-subtitle-track]' ? subtitleTrack : null;
  timingEarlier.closest = (selector) => selector.includes('[data-player-subtitle-timing-step]') ? timingEarlier : null;
  subtitleBackground.closest = (selector) => selector.includes('[data-player-subtitle-background]') ? subtitleBackground : null;
  subtitlePositionTop.closest = (selector) => selector.includes('[data-player-subtitle-position]') ? subtitlePositionTop : null;
  player.querySelector = (selector) => elements.get(selector) || null;
  player.querySelectorAll = () => [];
  player.setAttribute('session-id', 's'.repeat(32));
  player.setAttribute('entry-position', '0');
  player.setAttribute('resume-position', '0');
  player.setAttribute('subtitle-timing-offset-milliseconds', '0');
  player.setAttribute('subtitle-font-scale-percent', '100');
  player.setAttribute('subtitle-background', 'false');
  player.setAttribute('subtitle-shadow', 'false');
  player.setAttribute('subtitle-vertical-position', 'author');
  return {
    bufferedIndicator,
    controls,
    player,
    status,
    subtitleBackground,
    subtitleMenu,
    subtitlePositionTop,
    timingEarlier,
    video
  };
}

async function testBufferedRangeMarksTheTimelineEdges() {
  const {bufferedIndicator, player, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  video.currentTime = 30;
  video.buffered = new FakeTimeRanges([[0, 75]]);
  video.emit('progress');

  assert.equal(bufferedIndicator.style.getPropertyValue('--buffered-start-percent'), '25%');
  assert.equal(bufferedIndicator.style.getPropertyValue('--buffered-end-percent'), '62.5%');
}

async function testSelectPlayStatusClearsWhenPlaybackStarts() {
  const {controls, player, status, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  const playButton = new FakeElement();
  playButton.setAttribute('data-player-action', 'toggle');
  playButton.closest = (selector) => selector === '[data-player-action]' ? playButton : null;

  let rejectPlay;
  video.play = () => new Promise((_resolve, reject) => { rejectPlay = reject; });
  controls.emit('click', {target: playButton});
  rejectPlay(new Error('Autoplay is not allowed.'));
  await nextTick();
  assert.equal(status.textContent, 'Select Play to start this video.');

  video.paused = false;
  video.emit('play');
  assert.equal(status.textContent, '');

  video.paused = true;
  controls.emit('click', {target: playButton});
  video.paused = false;
  video.emit('play');
  rejectPlay(new Error('A stale play attempt failed.'));
  await nextTick();
  assert.equal(status.textContent, '');
}

async function testWebVttSettingsApplyWithoutReloadingTheTrack() {
  const {player, subtitleBackground, subtitleMenu, subtitlePositionTop, timingEarlier, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  const track = video.children[0];
  track.emit('load');
  const cue = track.track.cues[0];
  const trackCount = video.children.length;

  subtitleMenu.emit('click', {target: subtitleBackground});
  assert.equal(player.dataset.subtitleBackground, 'true');
  assert.equal(video.children.length, trackCount);

  subtitleMenu.emit('click', {target: subtitlePositionTop});
  assert.equal(cue.snapToLines, false);
  assert.equal(cue.line, 10);
  assert.equal(video.children.length, trackCount);

  subtitleMenu.emit('click', {target: timingEarlier});
  assert.equal(cue.startTime, 9.5);
  assert.equal(cue.endTime, 19.5);
  assert.equal(video.children.length, trackCount);

  runScheduledTimeouts();
  await nextTick();
  await nextTick();
}

async function testSubtitleSettingsSavesCollapseToTheLatestState() {
  const {player, subtitleBackground, subtitleMenu, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();
  video.children[0].emit('load');
  fetchCalls.length = 0;

  subtitleMenu.emit('click', {target: subtitleBackground});
  subtitleMenu.emit('click', {target: subtitleBackground});
  runScheduledTimeouts();
  await nextTick();
  await nextTick();

  const saves = fetchCalls.filter((call) => String(call.url).includes('/tracks'));
  assert.equal(saves.length, 1);
  const payload = JSON.parse(saves[0].options.body);
  assert.equal(payload.subtitleBackground, false);
}

async function testQueueAdvanceAutoplaysWithoutLeavingFullscreen() {
  const {player, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  completionPayload = {
    nextEntry: {
      position: 1,
      itemId: 2,
      displayTitle: 'Next episode',
      durationSeconds: 120,
      savedResumePositionSeconds: 0,
      audioStreams: [{codec: 'aac', language: 'en', title: null}],
      subtitleTracks: [],
      subtitleFontIds: [],
      selectedAudioStream: 0,
      selectedSubtitleTrack: null,
      subtitleTimingOffsetMilliseconds: 0,
      subtitleFontScalePercent: 100,
      subtitleBackground: false,
      subtitleShadow: false,
      subtitleVerticalPosition: 'author'
    },
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  document.fullscreenElement = player;
  video.paused = false;
  video.emit('ended');
  await nextTick();
  await nextTick();
  await nextTick();
  await nextTick();

  assert.equal(player.getAttribute('entry-position'), '1');
  assert.equal(video.playCalls, 1);
  assert.equal(document.fullscreenElement, player);
  completionPayload = null;
}

(async () => {
  await testBufferedRangeMarksTheTimelineEdges();
  await testSelectPlayStatusClearsWhenPlaybackStarts();
  await testWebVttSettingsApplyWithoutReloadingTheTrack();
  await testSubtitleSettingsSavesCollapseToTheLatestState();
  await testQueueAdvanceAutoplaysWithoutLeavingFullscreen();
})()
  .then(() => process.stdout.write('browser playback status checks passed\n'))
  .catch((error) => {
    process.stderr.write(`${error.stack}\n`);
    process.exitCode = 1;
  });
