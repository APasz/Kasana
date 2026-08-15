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
const documentListeners = new Map();
const windowListeners = new Map();
let activeProfileMenu = null;
let activeQueueNext = null;
global.document = {
  addEventListener(name, listener) {
    const listeners = documentListeners.get(name) || [];
    listeners.push(listener);
    documentListeners.set(name, listeners);
  },
  createElement(name) {
    if (name === 'track') return new FakeTrack();
    return new FakeElement();
  },
  emit(name, event = {}) {
    for (const listener of documentListeners.get(name) || []) listener(event);
  },
  removeEventListener(name, listener) {
    const listeners = documentListeners.get(name) || [];
    documentListeners.set(name, listeners.filter((candidate) => candidate !== listener));
  },
  querySelector(selector) {
    if (selector === 'kanvas-profile-menu') return activeProfileMenu;
    if (selector === '[data-player-next]') return activeQueueNext;
    return null;
  },
  fullscreenElement: null
};
global.navigator = {getGamepads() { return []; }};
global.CSS = {supports() { return true; }};
const scheduledTimeouts = new Map();
let nextTimeoutId = 1;
const assignedLocations = [];
const replacedLocations = [];
global.window = {
  addEventListener(name, listener) {
    const listeners = windowListeners.get(name) || [];
    listeners.push(listener);
    windowListeners.set(name, listeners);
  },
  clearTimeout(id) { scheduledTimeouts.delete(id); },
  emit(name, event = {}) {
    for (const listener of windowListeners.get(name) || []) listener(event);
  },
  history: {
    replaceState(_state, _title, url) { replacedLocations.push(url); }
  },
  location: {
    assign(url) { assignedLocations.push(url); },
    origin: 'http://kanvas.test'
  },
  removeEventListener(name, listener) {
    const listeners = windowListeners.get(name) || [];
    windowListeners.set(name, listeners.filter((candidate) => candidate !== listener));
  },
  setTimeout(callback) {
    const id = nextTimeoutId;
    nextTimeoutId += 1;
    scheduledTimeouts.set(id, callback);
    return id;
  }
};
const fetchCalls = [];
let completionPayload = null;
let compatibilityPayload = {mode: 'direct', mediaUrl: '/media/video.mp4'};
let recordedProgressPosition = 0;
let rejectBackwardsProgress = false;
let delayNextProgress = false;
let resolveDelayedProgress = null;
let delayNextTrackSave = false;
let resolveDelayedTrackSave = null;
let delayNextCompatibility = false;
let resolveDelayedCompatibility = null;
const progressResponse = (options) => {
  const payload = JSON.parse(String(options.body));
  const accepted = !rejectBackwardsProgress
    || payload.seek
    || payload.positionSeconds >= recordedProgressPosition;
  if (accepted) recordedProgressPosition = payload.positionSeconds;
  return {ok: accepted, json: async () => ({})};
};
global.fetch = (url, options = {}) => {
  fetchCalls.push({options, url});
  if (String(url).includes('/progress')) {
    const response = progressResponse(options);
    if (delayNextProgress) {
      delayNextProgress = false;
      return new Promise((resolve) => {
        resolveDelayedProgress = () => resolve(response);
      });
    }
    return Promise.resolve(response);
  }
  if (String(url).includes('/complete')) {
    return Promise.resolve({ok: true, json: async () => completionPayload || {nextEntry: null, nextUrl: null}});
  }
  if (String(url).includes('/tracks')) {
    const response = {
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
    if (delayNextTrackSave) {
      delayNextTrackSave = false;
      return new Promise((resolve) => {
        resolveDelayedTrackSave = () => resolve(response);
      });
    }
    return Promise.resolve(response);
  }
  const response = {ok: true, json: async () => compatibilityPayload};
  if (delayNextCompatibility) {
    delayNextCompatibility = false;
    return new Promise((resolve) => {
      resolveDelayedCompatibility = () => resolve(response);
    });
  }
  return Promise.resolve(response);
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

function createPlayer({
  durationSeconds = 0,
  hasQueuedItem = false,
  resumePosition = 0,
  subtitlesDisabled = false
} = {}) {
  const Player = customElements.get('kanvas-playback-player');
  const player = new Player();
  activeProfileMenu = subtitlesDisabled ? new FakeElement() : null;
  activeProfileMenu?.setAttribute('data-preferred-subtitle-language', 'none');
  activeQueueNext = hasQueuedItem ? new FakeElement() : null;
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
  const fullscreenTitle = new FakeElement();
  const fullscreenSpecialInfo = new FakeElement();
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
    ['[data-player-fullscreen-title]', fullscreenTitle],
    ['[data-player-fullscreen-special-info]', fullscreenSpecialInfo],
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
  const audioOption = new FakeElement();
  audioOption.setAttribute('data-player-audio-stream', '1');
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
  audioOption.closest = (selector) => selector === '[data-player-audio-stream]' ? audioOption : null;
  player.querySelector = (selector) => elements.get(selector) || null;
  player.querySelectorAll = () => [];
  player.setAttribute('session-id', 's'.repeat(32));
  player.setAttribute('entry-position', '0');
  player.setAttribute('resume-position', String(resumePosition));
  player.setAttribute('duration-seconds', String(durationSeconds));
  player.setAttribute('subtitle-timing-offset-milliseconds', '0');
  player.setAttribute('subtitle-font-scale-percent', '100');
  player.setAttribute('subtitle-background', 'false');
  player.setAttribute('subtitle-shadow', 'false');
  player.setAttribute('subtitle-vertical-position', 'author');
  return {
    audioMenu,
    audioOption,
    bufferedIndicator,
    controls,
    currentTime,
    fullscreenSpecialInfo,
    fullscreenTitle,
    player,
    queueNext: activeQueueNext,
    remainingTime,
    status,
    subtitleBackground,
    subtitleMenu,
    subtitlePositionTop,
    timeline,
    timingEarlier,
    video
  };
}

function queuedEntry() {
  return {
    position: 1,
    itemId: 2,
    displayTitle: 'Next episode',
    fullscreenTitle: 'Example Show · Next episode',
    specialInfo: 'S01 E02',
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

const progressCallsSince = (index) => fetchCalls
  .slice(index)
  .filter((call) => String(call.url).includes('/progress'));

async function testBackwardSeekSavesAsASeekWhenPaused() {
  const {player, status, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  const start = fetchCalls.length;
  recordedProgressPosition = 0;
  rejectBackwardsProgress = true;
  video.currentTime = 50;
  video.emit('timeupdate');
  await nextTick();
  await nextTick();

  video.currentTime = 10;
  video.emit('seeking');
  video.emit('pause');
  video.emit('seeked');
  await nextTick();
  await nextTick();
  await nextTick();

  const updates = progressCallsSince(start).map((call) => JSON.parse(call.options.body));
  assert.deepEqual(updates, [
    {positionSeconds: 50, seek: false, entryPosition: 0},
    {positionSeconds: 10, seek: true, entryPosition: 0},
  ]);
  assert.notEqual(status.textContent, 'Playback progress could not be saved.');
  rejectBackwardsProgress = false;
}

async function testBackwardSeekIsQueuedUntilTheActiveSaveFinishes() {
  const {player, status, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  const start = fetchCalls.length;
  recordedProgressPosition = 0;
  rejectBackwardsProgress = true;
  delayNextProgress = true;
  video.currentTime = 50;
  video.emit('timeupdate');
  await nextTick();
  assert.ok(resolveDelayedProgress);

  video.currentTime = 10;
  video.emit('seeking');
  video.emit('pause');
  video.emit('seeked');
  assert.equal(progressCallsSince(start).length, 1);

  resolveDelayedProgress();
  await nextTick();
  await nextTick();
  await nextTick();

  const updates = progressCallsSince(start).map((call) => JSON.parse(call.options.body));
  assert.deepEqual(updates, [
    {positionSeconds: 50, seek: false, entryPosition: 0},
    {positionSeconds: 10, seek: true, entryPosition: 0},
  ]);
  assert.notEqual(status.textContent, 'Playback progress could not be saved.');
  rejectBackwardsProgress = false;
  resolveDelayedProgress = null;
}

async function testProgressUsesTheAuthoritativeCatalogueDuration() {
  const {player, video} = createPlayer({durationSeconds: 100});
  player.connectedCallback();
  await nextTick();
  await nextTick();

  const start = fetchCalls.length;
  video.duration = 100.5;
  video.currentTime = 100.5;
  video.emit('timeupdate');
  await nextTick();
  await nextTick();

  const updates = progressCallsSince(start).map((call) => JSON.parse(call.options.body));
  assert.deepEqual(updates, [
    {positionSeconds: 100, seek: false, entryPosition: 0},
  ]);
}

async function testGeneratedResumeKeepsTheFullEpisodeTimeline() {
  compatibilityPayload = {mode: 'audio-transcode', mediaUrl: '/media/video.mp4'};
  const {currentTime, player, remainingTime, timeline, video} = createPlayer({
    durationSeconds: 2582,
    resumePosition: 404
  });
  player.connectedCallback();
  await nextTick();
  await nextTick();

  video.emit('loadedmetadata');
  video.currentTime = 12;
  video.emit('timeupdate');
  await nextTick();
  await nextTick();

  assert.equal(timeline.max, '2582');
  assert.equal(timeline.value, '416');
  assert.equal(currentTime.textContent, '6:56');
  assert.equal(remainingTime.textContent, '-36:06');
  compatibilityPayload = {mode: 'direct', mediaUrl: '/media/video.mp4'};
}

async function testPageHideDoesNotSaveBeforeTheResumeSeekIsApplied() {
  windowListeners.clear();
  const {player} = createPlayer({resumePosition: 30});
  player.connectedCallback();
  await nextTick();
  await nextTick();

  const start = fetchCalls.length;
  window.emit('pagehide');
  await nextTick();

  assert.equal(progressCallsSince(start).length, 0);
}

async function testProfileSubtitlePreferenceDisablesTheInitialTrack() {
  const {player, video} = createPlayer({subtitlesDisabled: true});
  player.connectedCallback();
  await nextTick();
  await nextTick();

  assert.equal(video.children.length, 0);
}

async function testPlaybackErrorReconnectsInsteadOfClaimingTheFormatIsUnsupported() {
  compatibilityPayload = {mode: 'remux', mediaUrl: '/media/video.mp4'};
  const {player, status, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  video.duration = 3600;
  video.currentTime = 1973;
  video.paused = false;
  const compatibilityCallsBeforeError = fetchCalls.filter((call) => (
    String(call.url).includes('/compatibility')
  )).length;
  video.emit('error');
  await nextTick();
  await nextTick();
  await nextTick();

  assert.equal(status.textContent, 'Reconnecting playback…');
  assert.equal(
    fetchCalls.filter((call) => String(call.url).includes('/compatibility')).length,
    compatibilityCallsBeforeError + 1
  );

  video.emit('loadedmetadata');
  video.emit('error');
  await nextTick();
  assert.equal(status.textContent, 'Playback stream stopped. Reload this page to retry.');
  compatibilityPayload = {mode: 'direct', mediaUrl: '/media/video.mp4'};
}

async function testEachQueuedEntryGetsItsOwnStreamRecoveryAttempt() {
  compatibilityPayload = {mode: 'remux', mediaUrl: '/media/video.mp4'};
  const {player, status, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  delayNextCompatibility = true;
  video.emit('error');
  await nextTick();
  assert.ok(resolveDelayedCompatibility);

  completionPayload = {
    nextEntry: queuedEntry(),
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  document.fullscreenElement = player;
  video.emit('ended');
  await nextTick();
  await nextTick();
  await nextTick();
  assert.equal(player.getAttribute('entry-position'), '1');

  const compatibilityCallsBeforeSecondError = fetchCalls.filter((call) => (
    String(call.url).includes('/compatibility')
  )).length;
  video.emit('error');
  await nextTick();
  await nextTick();
  await nextTick();

  assert.equal(status.textContent, 'Reconnecting playback…');
  assert.equal(
    fetchCalls.filter((call) => String(call.url).includes('/compatibility')).length,
    compatibilityCallsBeforeSecondError + 1
  );
  resolveDelayedCompatibility();
  await nextTick();
  await nextTick();
  document.fullscreenElement = null;
  completionPayload = null;
  compatibilityPayload = {mode: 'direct', mediaUrl: '/media/video.mp4'};
  resolveDelayedCompatibility = null;
}

async function testAudioSelectionCannotOutliveAQueueTransition() {
  const {audioMenu, audioOption, player, status, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  delayNextTrackSave = true;
  audioMenu.emit('click', {target: audioOption});
  await nextTick();
  assert.ok(resolveDelayedTrackSave);

  completionPayload = {
    nextEntry: queuedEntry(),
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  document.fullscreenElement = player;
  video.emit('ended');
  await nextTick();
  await nextTick();
  await nextTick();
  await nextTick();
  assert.equal(player.getAttribute('entry-position'), '1');
  const compatibilityCallsAfterTransition = fetchCalls.filter((call) => (
    String(call.url).includes('/compatibility')
  )).length;

  resolveDelayedTrackSave();
  await nextTick();
  await nextTick();
  await nextTick();

  assert.equal(
    fetchCalls.filter((call) => String(call.url).includes('/compatibility')).length,
    compatibilityCallsAfterTransition
  );
  assert.notEqual(status.textContent, 'Audio track could not be changed.');
  document.fullscreenElement = null;
  completionPayload = null;
  resolveDelayedTrackSave = null;
}

async function testQueueControlAdvancesPlayback() {
  const {player, queueNext} = createPlayer({hasQueuedItem: true});
  assert.ok(queueNext);
  player.connectedCallback();
  await nextTick();
  await nextTick();

  completionPayload = {
    nextEntry: queuedEntry(),
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  const prevented = {value: false};
  queueNext.emit('click', {
    preventDefault() { prevented.value = true; },
    stopPropagation() {}
  });
  await nextTick();
  await nextTick();

  assert.equal(prevented.value, true);
  assert.equal(
    assignedLocations.pop(),
    `/item/2?playbackSession=${'s'.repeat(32)}&start=true`
  );
  assert.equal(queueNext.getAttribute('aria-disabled'), 'true');
  completionPayload = null;
}

async function testQueueAdvanceAutoplaysWithoutLeavingFullscreen() {
  const {fullscreenSpecialInfo, fullscreenTitle, player, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  completionPayload = {
    nextEntry: queuedEntry(),
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
  assert.equal(fullscreenTitle.textContent, 'Example Show · Next episode');
  assert.equal(fullscreenSpecialInfo.textContent, 'S01 E02');
  assert.equal(fullscreenSpecialInfo.hidden, false);
  assert.equal(
    replacedLocations.pop(),
    `/item/2?playbackSession=${'s'.repeat(32)}&start=true`
  );

  document.fullscreenElement = null;
  document.emit('fullscreenchange');
  await nextTick();
  await nextTick();
  assert.equal(
    assignedLocations.pop(),
    `/item/2?playbackSession=${'s'.repeat(32)}&start=true`
  );
  completionPayload = null;
}

async function testFullscreenExitNavigatesWithoutWaitingForProgress() {
  const {player, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  completionPayload = {
    nextEntry: queuedEntry(),
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  document.fullscreenElement = player;
  video.emit('ended');
  await nextTick();
  await nextTick();
  await nextTick();

  delayNextProgress = true;
  document.fullscreenElement = null;
  document.emit('fullscreenchange');
  await nextTick();

  assert.equal(
    assignedLocations.pop(),
    `/item/2?playbackSession=${'s'.repeat(32)}&start=true`
  );
  assert.equal(resolveDelayedProgress, null);
  completionPayload = null;
}

async function testQueueAdvanceNavigatesToTheNextItemOutsideFullscreen() {
  const {player, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  completionPayload = {
    nextEntry: queuedEntry(),
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  video.emit('ended');
  await nextTick();
  await nextTick();

  assert.equal(
    assignedLocations.pop(),
    `/item/2?playbackSession=${'s'.repeat(32)}&start=true`
  );
  completionPayload = null;
}

(async () => {
  await testBufferedRangeMarksTheTimelineEdges();
  await testSelectPlayStatusClearsWhenPlaybackStarts();
  await testWebVttSettingsApplyWithoutReloadingTheTrack();
  await testSubtitleSettingsSavesCollapseToTheLatestState();
  await testBackwardSeekSavesAsASeekWhenPaused();
  await testBackwardSeekIsQueuedUntilTheActiveSaveFinishes();
  await testProgressUsesTheAuthoritativeCatalogueDuration();
  await testGeneratedResumeKeepsTheFullEpisodeTimeline();
  await testPageHideDoesNotSaveBeforeTheResumeSeekIsApplied();
  await testProfileSubtitlePreferenceDisablesTheInitialTrack();
  await testPlaybackErrorReconnectsInsteadOfClaimingTheFormatIsUnsupported();
  await testEachQueuedEntryGetsItsOwnStreamRecoveryAttempt();
  await testAudioSelectionCannotOutliveAQueueTransition();
  await testQueueControlAdvancesPlayback();
  await testQueueAdvanceAutoplaysWithoutLeavingFullscreen();
  await testFullscreenExitNavigatesWithoutWaitingForProgress();
  await testQueueAdvanceNavigatesToTheNextItemOutsideFullscreen();
})()
  .then(() => process.stdout.write('browser playback status checks passed\n'))
  .catch((error) => {
    process.stderr.write(`${error.stack}\n`);
    process.exitCode = 1;
  });
