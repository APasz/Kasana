const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeElement {
  constructor() {
    this.attributes = new Map();
    const classNames = new Set();
    this.classList = {
      add: (...names) => names.forEach((name) => classNames.add(name)),
      contains: (name) => classNames.has(name),
      remove: (...names) => names.forEach((name) => classNames.delete(name))
    };
    this.dataset = {};
    this.hidden = true;
    this.children = [];
    this.listeners = new Map();
    this.focusCalls = 0;
    this.bounds = {bottom: 180, height: 180, left: 0, right: 320, top: 0, width: 320};
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

  removeEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    this.listeners.set(name, listeners.filter((candidate) => candidate !== listener));
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  contains(candidate) {
    return candidate === this || this.children.includes(candidate) || candidate?.parentElement === this;
  }

  closest() {
    return null;
  }

  emit(name, event = {}) {
    for (const listener of this.listeners.get(name) || []) listener(event);
  }

  focus() {
    this.focusCalls += 1;
  }

  getAttribute(name) {
    return this.attributes.get(name) || null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  getBoundingClientRect() {
    return this.bounds;
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
    this.videoHeight = 1080;
    this.videoWidth = 1920;
    this.volume = 1;
    this.buffered = new FakeTimeRanges();
    this.children = [];
    this.playCalls = 0;
    this.pauseCalls = 0;
  }

  canPlayType() {
    return 'probably';
  }

  load() {}

  play() {
    this.playCalls += 1;
    return Promise.resolve();
  }

  pause() {
    this.pauseCalls += 1;
    this.paused = true;
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
let activePlaybackQueue = null;
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
    if (selector === '[data-player-queue]') return activePlaybackQueue;
    return null;
  },
  fullscreenElement: null
};
global.navigator = {getGamepads() { return []; }};
global.CSS = {supports() { return true; }};
const scheduledTimeouts = new Map();
let nextTimeoutId = 1;
const scheduledIntervals = new Map();
let nextIntervalId = 1;
const assignedLocations = [];
const replacedLocations = [];
global.window = {
  addEventListener(name, listener) {
    const listeners = windowListeners.get(name) || [];
    listeners.push(listener);
    windowListeners.set(name, listeners);
  },
  clearInterval(id) { scheduledIntervals.delete(id); },
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
  },
  setInterval(callback) {
    const id = nextIntervalId;
    nextIntervalId += 1;
    scheduledIntervals.set(id, callback);
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

for (const filename of ['kanvas.js', 'kanvas-playback.js']) {
  vm.runInThisContext(fs.readFileSync(`src/kasana/kanvas/static/${filename}`, 'utf8'), {
    filename
  });
}

const nextTick = () => new Promise((resolve) => setImmediate(resolve));

function runScheduledTimeouts() {
  const callbacks = Array.from(scheduledTimeouts.values());
  scheduledTimeouts.clear();
  for (const callback of callbacks) callback();
}

function runScheduledIntervals() {
  for (const callback of scheduledIntervals.values()) callback();
}

function createPlaybackQueue(entryCount) {
  const queue = new FakeElement();
  const entries = Array.from({length: entryCount}, () => new FakeElement());
  entries.forEach((entry) => {
    entry.remove = () => {
      const index = entries.indexOf(entry);
      if (index >= 0) entries.splice(index, 1);
    };
  });
  queue.querySelectorAll = (selector) => (
    selector === '.k-playback-queue__entry' ? entries : []
  );
  queue.remove = () => { activePlaybackQueue = null; };
  return queue;
}

function createPlayer({
  durationSeconds = 0,
  hasFullscreenQueueNext = false,
  hasQueuedItem = false,
  queuedItemCount = 0,
  resumePosition = 0,
  subtitlesDisabled = false
} = {}) {
  const Player = customElements.get('kanvas-playback-player');
  const player = new Player();
  activeProfileMenu = subtitlesDisabled ? new FakeElement() : null;
  activeProfileMenu?.setAttribute('data-preferred-subtitle-language', 'none');
  activeQueueNext = hasQueuedItem ? new FakeElement() : null;
  activePlaybackQueue = queuedItemCount > 0 ? createPlaybackQueue(queuedItemCount) : null;
  const video = new FakeVideo();
  const status = new FakeElement();
  const controls = new FakeElement();
  const playerAction = (action) => {
    const button = new FakeElement();
    button.setAttribute('data-player-action', action);
    button.closest = (selector) => (
      selector === '[data-player-action]' || selector === 'button' ? button : null
    );
    return button;
  };
  const overflowControl = playerAction('overflow');
  const settingsControl = playerAction('menu');
  const audioControl = playerAction('audio');
  const subtitlesControl = playerAction('subtitles');
  const toggleControl = playerAction('toggle');
  const frameToggle = playerAction('toggle');
  frameToggle.setAttribute('data-player-frame-toggle', '');
  const frameTogglePlayIcon = new FakeElement();
  const frameTogglePauseIcon = new FakeElement();
  frameTogglePlayIcon.closest = (selector) => selector === 'button' ? frameToggle : null;
  frameTogglePauseIcon.closest = (selector) => selector === 'button' ? frameToggle : null;
  frameToggle.querySelector = (selector) => {
    if (selector === '.k-player__control-icon--default .k-icon') return frameTogglePlayIcon;
    if (selector === '.k-player__control-icon--alternate .k-icon') return frameTogglePauseIcon;
    return null;
  };
  const theatreControl = playerAction('theatre');
  const fullscreenQueueNext = hasFullscreenQueueNext ? new FakeElement() : null;
  if (fullscreenQueueNext) {
    fullscreenQueueNext.setAttribute('data-player-action', 'next');
    fullscreenQueueNext.closest = (selector) => (
      selector === '[data-player-action]' ? fullscreenQueueNext : null
    );
  }
  controls.appendChild(overflowControl);
  controls.appendChild(settingsControl);
  controls.appendChild(audioControl);
  controls.appendChild(subtitlesControl);
  controls.appendChild(theatreControl);
  controls.querySelector = (selector) => {
    if (selector === '[data-player-action="next"]') return fullscreenQueueNext;
    if (selector === '[data-player-action="overflow"]') return overflowControl;
    return null;
  };
  const timeline = new FakeElement();
  const timelinePreview = new FakeElement();
  const bufferedIndicator = new FakeElement();
  const currentTime = new FakeElement();
  const remainingTime = new FakeElement();
  const volume = new FakeElement();
  const mobileVolume = new FakeElement();
  const volumeValue = new FakeElement();
  const mobileVolumeValue = new FakeElement();
  const contextMenu = new FakeElement();
  const audioMenu = new FakeElement();
  const subtitleMenu = new FakeElement();
  const mobileMenu = new FakeElement();
  const playerTooltip = new FakeElement();
  const mobileSettings = playerAction('menu');
  const mobileSubtitles = playerAction('subtitles');
  const mobileAudio = playerAction('audio');
  const mobileMute = playerAction('mute');
  const mobileTheatre = playerAction('theatre');
  const mobileFullscreen = playerAction('fullscreen');
  const mobileMuteLabel = new FakeElement();
  mobileMuteLabel.setAttribute('data-player-action-label', 'mute');
  const mobileFullscreenLabel = new FakeElement();
  mobileFullscreenLabel.setAttribute('data-player-action-label', 'fullscreen');
  const mobileTheatreLabel = new FakeElement();
  mobileTheatreLabel.setAttribute('data-player-action-label', 'theatre');
  mobileMenu.appendChild(mobileSettings);
  mobileMenu.appendChild(mobileSubtitles);
  mobileMenu.appendChild(mobileAudio);
  mobileMenu.appendChild(mobileMute);
  mobileMenu.appendChild(mobileTheatre);
  mobileMenu.appendChild(mobileFullscreen);
  mobileMute.appendChild(mobileMuteLabel);
  mobileTheatre.appendChild(mobileTheatreLabel);
  mobileFullscreen.appendChild(mobileFullscreenLabel);
  const subtitleTimingLabel = new FakeElement();
  const subtitleFontScaleLabel = new FakeElement();
  const subtitleAppearance = new FakeElement();
  const nativeControls = new FakeElement();
  const autoplayNextOption = hasQueuedItem ? new FakeElement() : null;
  const autoplayNextControl = hasQueuedItem ? new FakeElement() : null;
  if (autoplayNextOption) autoplayNextOption.hidden = false;
  if (autoplayNextControl) autoplayNextControl.checked = true;
  const fullscreenTitle = new FakeElement();
  const fullscreenSpecialInfo = new FakeElement();
  const fullscreenTime = new FakeElement();
  const fullscreenFrameAlignment = new FakeElement();
  const frameAlignmentOption = (alignment, label) => {
    const option = new FakeElement();
    option.setAttribute('data-player-frame-alignment-option', alignment);
    option.setAttribute('aria-label', label);
    option.setAttribute('aria-pressed', String(alignment === 'centred'));
    option.closest = (selector) => (
      selector === '[data-player-frame-alignment-option]' ? option : null
    );
    return option;
  };
  const frameAlignmentStart = frameAlignmentOption('start', 'Left');
  const frameAlignmentCentred = frameAlignmentOption('centred', 'Centred');
  const frameAlignmentEnd = frameAlignmentOption('end', 'Right');
  const frameAlignmentOptions = [frameAlignmentStart, frameAlignmentCentred, frameAlignmentEnd];
  frameAlignmentOptions.forEach((option) => fullscreenFrameAlignment.appendChild(option));
  fullscreenFrameAlignment.querySelectorAll = (selector) => (
    selector === '[data-player-frame-alignment-option]' ? frameAlignmentOptions : []
  );
  const kestrelLink = new HTMLAnchorElement();
  const elements = new Map([
    ['video', video],
    ['.k-player__status', status],
    ['.k-player__controls', controls],
    ['[data-player-timeline]', timeline],
    ['[data-player-timeline-preview]', timelinePreview],
    ['[data-player-buffered]', bufferedIndicator],
    ['[data-player-current-time]', currentTime],
    ['[data-player-remaining-time]', remainingTime],
    ['[data-player-volume]', volume],
    ['[data-player-mobile-volume]', mobileVolume],
    ['[data-player-context-menu]', contextMenu],
    ['[data-player-audio-menu]', audioMenu],
    ['[data-player-subtitle-menu]', subtitleMenu],
    ['[data-player-mobile-menu]', mobileMenu],
    ['[data-player-tooltip-host]', playerTooltip],
    ['[data-player-native-controls]', nativeControls],
    ['[data-player-autoplay-next]', autoplayNextControl],
    ['[data-player-autoplay-next-option]', autoplayNextOption],
    ['[data-player-fullscreen-title]', fullscreenTitle],
    ['[data-player-fullscreen-special-info]', fullscreenSpecialInfo],
    ['[data-player-fullscreen-time]', fullscreenTime],
    ['[data-player-frame-alignment-controls]', fullscreenFrameAlignment],
    ['[data-player-frame-toggle]', frameToggle],
    ['[data-player-kestrel]', kestrelLink]
  ]);
  const subtitleTrack = new FakeElement();
  subtitleTrack.setAttribute('aria-pressed', 'true');
  subtitleTrack.setAttribute('data-player-subtitle-format', 'webvtt');
  subtitleTrack.setAttribute('data-player-subtitle-track', 'sidecar-0');
  const timingEarlier = new FakeElement();
  timingEarlier.setAttribute('data-player-subtitle-timing-step', '-500');
  timingEarlier.setAttribute('aria-label', 'Show subtitles 0.5 seconds earlier');
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
  timingEarlier.closest = (selector) => (
    selector === 'button' || selector.includes('[data-player-subtitle-timing-step]')
      ? timingEarlier
      : null
  );
  subtitleBackground.closest = (selector) => selector.includes('[data-player-subtitle-background]') ? subtitleBackground : null;
  subtitlePositionTop.closest = (selector) => selector.includes('[data-player-subtitle-position]') ? subtitlePositionTop : null;
  audioOption.closest = (selector) => selector === '[data-player-audio-stream]' ? audioOption : null;
  player.querySelector = (selector) => elements.get(selector) || null;
  const actionControls = [
    overflowControl,
    settingsControl,
    audioControl,
    subtitlesControl,
    frameToggle,
    toggleControl,
    theatreControl,
    fullscreenQueueNext,
    mobileSettings,
    mobileSubtitles,
    mobileAudio,
    mobileMute,
    mobileTheatre,
    mobileFullscreen,
  ].filter(Boolean);
  const actionLabels = [mobileMuteLabel, mobileTheatreLabel, mobileFullscreenLabel];
  player.querySelectorAll = (selector) => {
    if (selector === '[data-player-volume-value]') return [volumeValue, mobileVolumeValue];
    const action = selector.match(/^\[data-player-action="(.+)"\]$/)?.[1];
    if (action) return actionControls.filter((control) => control.getAttribute('data-player-action') === action);
    const labelAction = selector.match(/^\[data-player-action-label="(.+)"\]$/)?.[1];
    if (labelAction) {
      return actionLabels.filter((label) => label.getAttribute('data-player-action-label') === labelAction);
    }
    return [];
  };
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
    audioControl,
    audioMenu,
    audioOption,
    autoplayNextControl,
    autoplayNextOption,
    bufferedIndicator,
    controls,
    contextMenu,
    currentTime,
    fullscreenSpecialInfo,
    fullscreenTime,
    fullscreenTitle,
    fullscreenFrameAlignment,
    fullscreenQueueNext,
    frameAlignmentCentred,
    frameAlignmentEnd,
    frameAlignmentStart,
    frameTogglePauseIcon,
    frameTogglePlayIcon,
    mobileAudio,
    mobileFullscreen,
    mobileFullscreenLabel,
    mobileMenu,
    mobileMute,
    mobileMuteLabel,
    mobileTheatre,
    mobileTheatreLabel,
    mobileSettings,
    mobileSubtitles,
    mobileVolume,
    mobileVolumeValue,
    overflowControl,
    frameToggle,
    player,
    playerTooltip,
    queueNext: activeQueueNext,
    remainingTime,
    settingsControl,
    status,
    subtitleBackground,
    subtitleMenu,
    subtitlesControl,
    subtitlePositionTop,
    timeline,
    timelinePreview,
    timingEarlier,
    theatreControl,
    toggleControl,
    volumeValue,
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

async function testTimelinePreviewTracksHoverAndDrag() {
  const {player, timeline, timelinePreview} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  timeline.emit('pointerenter', {clientX: 80});
  assert.equal(timelinePreview.hidden, false);
  assert.equal(timelinePreview.textContent, '0:30');
  assert.equal(
    timelinePreview.style.getPropertyValue('--k-player-timeline-preview-offset'),
    '80px'
  );

  timeline.emit('pointerdown', {clientX: 200});
  timeline.emit('pointerleave');
  assert.equal(timelinePreview.hidden, false);

  timeline.value = '75';
  timeline.emit('input');
  assert.equal(timelinePreview.textContent, '1:15');

  timeline.emit('pointerup');
  assert.equal(timelinePreview.hidden, true);
}

async function testMobileOverflowKeepsSecondaryPlaybackControlsTogether() {
  const {
    controls,
    mobileMenu,
    mobileMute,
    mobileMuteLabel,
    mobileVolume,
    mobileVolumeValue,
    overflowControl,
    player,
    volumeValue,
    video,
  } = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  controls.emit('click', {target: overflowControl});
  assert.equal(mobileMenu.hidden, false);
  assert.equal(overflowControl.getAttribute('aria-expanded'), 'true');
  assert.equal(volumeValue.textContent, '100%');
  assert.equal(mobileVolumeValue.textContent, '100%');

  mobileMenu.emit('click', {target: mobileMute});
  assert.equal(video.muted, true);
  assert.equal(mobileMute.dataset.playerIconState, 'alternate');
  assert.equal(mobileMuteLabel.textContent, 'Unmute');
  assert.equal(volumeValue.textContent, '0%');
  assert.equal(mobileVolumeValue.textContent, '0%');

  mobileVolume.value = '0.4';
  mobileVolume.emit('input');
  assert.equal(video.volume, 0.4);
  assert.equal(video.muted, false);
  assert.equal(volumeValue.textContent, '40%');
  assert.equal(mobileVolumeValue.textContent, '40%');
  assert.equal(mobileVolume.getAttribute('aria-valuetext'), '40%');

  mobileVolume.value = '0';
  mobileVolume.emit('input');
  assert.equal(video.muted, true);
  assert.equal(volumeValue.textContent, '0%');

  mobileMenu.emit('click', {target: mobileMute});
  assert.equal(video.muted, false);
  assert.equal(video.volume, 0.4);
  assert.equal(volumeValue.textContent, '40%');

  document.emit('pointerdown', {target: new FakeElement()});
  assert.equal(mobileMenu.hidden, true);
  assert.equal(overflowControl.getAttribute('aria-expanded'), 'false');
}

async function testPlayerPopupsShareToggleAndDismissalRules() {
  const {
    audioControl,
    audioMenu,
    autoplayNextControl,
    contextMenu,
    controls,
    mobileMenu,
    mobileSettings,
    overflowControl,
    player,
    settingsControl,
    subtitleMenu,
    subtitlesControl,
  } = createPlayer({hasQueuedItem: true});
  audioControl.hidden = false;
  settingsControl.hidden = false;
  subtitlesControl.hidden = false;
  player.connectedCallback();
  await nextTick();
  await nextTick();

  controls.emit('click', {target: settingsControl});
  assert.equal(contextMenu.hidden, false);
  assert.equal(settingsControl.getAttribute('aria-expanded'), 'true');
  assert.equal(mobileSettings.getAttribute('aria-expanded'), 'true');

  controls.emit('click', {target: audioControl});
  assert.equal(contextMenu.hidden, true);
  assert.equal(audioMenu.hidden, false);
  assert.equal(settingsControl.getAttribute('aria-expanded'), 'false');
  assert.equal(audioControl.getAttribute('aria-expanded'), 'true');

  window.emit('resize');
  assert.equal(audioMenu.hidden, true);
  assert.equal(audioControl.getAttribute('aria-expanded'), 'false');
  assert.equal(audioControl.focusCalls, 1);

  controls.emit('click', {target: audioControl});
  assert.equal(audioMenu.hidden, false);
  player.emit('focusout', {target: audioMenu, relatedTarget: settingsControl});
  assert.equal(audioMenu.hidden, false);
  player.emit('focusout', {target: settingsControl, relatedTarget: new FakeElement()});
  assert.equal(audioMenu.hidden, true);

  controls.emit('click', {target: audioControl});
  assert.equal(audioMenu.hidden, false);
  document.emit('pointerdown', {target: audioControl});
  assert.equal(audioMenu.hidden, false);
  controls.emit('click', {target: audioControl});
  assert.equal(audioMenu.hidden, true);
  assert.equal(audioControl.getAttribute('aria-expanded'), 'false');

  controls.emit('click', {target: subtitlesControl});
  assert.equal(subtitleMenu.hidden, false);
  const keyboardEvent = {
    key: 'Escape',
    prevented: false,
    propagationStopped: false,
    preventDefault() { this.prevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  };
  player.emit('keydown', keyboardEvent);
  assert.equal(keyboardEvent.prevented, true);
  assert.equal(keyboardEvent.propagationStopped, true);
  assert.equal(contextMenu.hidden, true);
  assert.equal(audioMenu.hidden, true);
  assert.equal(subtitleMenu.hidden, true);
  assert.equal(mobileMenu.hidden, true);
  assert.equal(subtitlesControl.getAttribute('aria-expanded'), 'false');
  assert.equal(subtitlesControl.focusCalls, 1);

  controls.emit('click', {target: overflowControl});
  assert.equal(mobileMenu.hidden, false);
  player.emit('focusout', {target: mobileMenu, relatedTarget: new FakeElement()});
  assert.equal(mobileMenu.hidden, true);
  assert.equal(overflowControl.getAttribute('aria-expanded'), 'false');

  controls.emit('click', {target: settingsControl});
  assert.equal(contextMenu.hidden, false);
  assert.ok(autoplayNextControl);
  autoplayNextControl.checked = false;
  autoplayNextControl.emit('change');
  assert.equal(contextMenu.hidden, true);

  const settingsFocusCalls = settingsControl.focusCalls;
  player.emit('contextmenu', {
    clientX: 160,
    clientY: 90,
    preventDefault() {},
  });
  assert.equal(contextMenu.hidden, false);
  player.emit('keydown', {
    key: 'Escape',
    preventDefault() {},
    stopPropagation() {},
  });
  assert.equal(contextMenu.hidden, true);
  assert.equal(settingsControl.focusCalls, settingsFocusCalls);
}

async function testTheatreModeExpandsThePlayerAndUpdatesBothControls() {
  const {
    controls,
    mobileMenu,
    mobileTheatre,
    mobileTheatreLabel,
    player,
    theatreControl,
  } = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  assert.equal(player.hasAttribute('data-player-theatre-mode'), false);
  assert.equal(theatreControl.getAttribute('aria-label'), 'Theatre mode');
  assert.equal(theatreControl.getAttribute('aria-pressed'), 'false');

  controls.emit('click', {target: theatreControl});
  assert.equal(player.hasAttribute('data-player-theatre-mode'), true);
  assert.equal(theatreControl.getAttribute('aria-label'), 'Exit theatre mode');
  assert.equal(theatreControl.getAttribute('aria-pressed'), 'true');
  assert.equal(theatreControl.dataset.playerIconState, 'alternate');
  assert.equal(mobileTheatreLabel.textContent, 'Exit theatre mode');

  mobileMenu.hidden = false;
  mobileMenu.emit('click', {target: mobileTheatre});
  assert.equal(player.hasAttribute('data-player-theatre-mode'), false);
  assert.equal(mobileMenu.hidden, true);
  assert.equal(mobileTheatre.dataset.playerIconState, 'default');
}

async function testPlayerTooltipsFollowTheCurrentButtonState() {
  const {
    frameToggle,
    frameTogglePauseIcon,
    frameTogglePlayIcon,
    player,
    playerTooltip,
    timingEarlier,
    toggleControl,
    video
  } = createPlayer();
  player.bounds = {bottom: 410, height: 360, left: 100, right: 740, top: 50, width: 640};
  playerTooltip.bounds = {bottom: 24, height: 24, left: 0, right: 110, top: 0, width: 110};
  toggleControl.bounds = {bottom: 400, height: 30, left: 700, right: 730, top: 370, width: 30};
  frameToggle.bounds = {bottom: 390, height: 320, left: 260, right: 580, top: 70, width: 320};
  frameTogglePlayIcon.bounds = {bottom: 242, height: 24, left: 408, right: 432, top: 218, width: 24};
  frameTogglePauseIcon.bounds = {bottom: 254, height: 24, left: 408, right: 432, top: 230, width: 24};
  player.connectedCallback();
  await nextTick();
  await nextTick();

  player.emit('pointerover', {target: toggleControl});
  assert.equal(playerTooltip.hidden, false);
  assert.equal(playerTooltip.textContent, 'Play');
  const left = Number.parseFloat(playerTooltip.style.left);
  const top = Number.parseFloat(playerTooltip.style.top);
  assert.ok(left - playerTooltip.bounds.width / 2 >= 8);
  assert.ok(left + playerTooltip.bounds.width / 2 <= player.bounds.width - 8);
  assert.ok(top >= 8);
  assert.ok(top + playerTooltip.bounds.height <= player.bounds.height - 8);

  video.paused = false;
  video.emit('play');
  assert.equal(playerTooltip.textContent, 'Pause');

  player.emit('pointerout', {target: toggleControl, relatedTarget: frameToggle});
  assert.equal(playerTooltip.hidden, true);

  player.emit('pointerover', {target: frameToggle});
  assert.equal(playerTooltip.hidden, true);

  player.emit('focusin', {target: frameToggle});
  assert.equal(playerTooltip.hidden, false);
  player.emit('focusout', {target: frameToggle, relatedTarget: new FakeElement()});
  assert.equal(playerTooltip.hidden, true);

  player.emit('pointerover', {target: frameTogglePauseIcon});
  assert.equal(playerTooltip.hidden, false);
  assert.equal(playerTooltip.textContent, 'Pause');
  assert.equal(playerTooltip.style.left, '320px');
  assert.equal(playerTooltip.style.top, '148px');

  player.emit('pointerout', {target: frameTogglePauseIcon, relatedTarget: frameToggle});
  assert.equal(playerTooltip.hidden, true);

  player.emit('pointerover', {target: timingEarlier});
  assert.equal(playerTooltip.textContent, 'Show subtitles 0.5 seconds earlier');

  player.emit('pointerout', {target: timingEarlier, relatedTarget: new FakeElement()});
  assert.equal(playerTooltip.hidden, true);
}

async function testFrameToggleUsesTheSharedPlayerControlsTimer() {
  scheduledTimeouts.clear();
  const {controls, player, theatreControl, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  video.paused = false;
  video.emit('play');
  assert.equal(scheduledTimeouts.size, 1);
  runScheduledTimeouts();
  assert.equal(player.classList.contains('k-player--controls-hidden'), true);

  player.emit('pointerenter');
  assert.equal(player.classList.contains('k-player--controls-hidden'), false);
  assert.equal(scheduledTimeouts.size, 1);

  controls.emit('click', {target: theatreControl});
  assert.equal(player.hasAttribute('data-player-theatre-mode'), true);
  assert.equal(scheduledTimeouts.size, 1);
  runScheduledTimeouts();
  assert.equal(player.classList.contains('k-player--controls-hidden'), true);

  document.fullscreenElement = new FakeElement();
  document.emit('fullscreenchange');
  assert.equal(player.classList.contains('k-player--controls-hidden'), true);
  assert.equal(scheduledTimeouts.size, 0);

  document.fullscreenElement = player;
  document.emit('fullscreenchange');
  assert.equal(player.classList.contains('k-player--controls-hidden'), false);
  assert.equal(scheduledTimeouts.size, 1);
  runScheduledTimeouts();
  assert.equal(player.classList.contains('k-player--controls-hidden'), true);

  document.fullscreenElement = null;
  document.emit('fullscreenchange');
  scheduledTimeouts.clear();
}

function assertFloatingMenuFitsPlayer(menu, player) {
  const playerBounds = player.getBoundingClientRect();
  const menuBounds = menu.getBoundingClientRect();
  const left = Number.parseFloat(menu.style.left);
  const top = Number.parseFloat(menu.style.top);

  assert.ok(left >= 8);
  assert.ok(left + menuBounds.width <= playerBounds.width - 8);
  assert.ok(top >= 8);
  assert.ok(top + menuBounds.height <= playerBounds.height - 8);
}

function assertPopupAnchorsToControl(menu, control, player) {
  const playerBounds = player.getBoundingClientRect();
  const controlBounds = control.getBoundingClientRect();
  const menuBounds = menu.getBoundingClientRect();
  const maximumLeft = playerBounds.width - menuBounds.width - 8;
  const expectedLeft = Math.max(
    8,
    Math.min(controlBounds.left - playerBounds.left, Math.max(8, maximumLeft))
  );
  const maximumTop = playerBounds.height - menuBounds.height - 8;
  const preferredTop = controlBounds.top - playerBounds.top - menuBounds.height - 8;
  const alternateTop = controlBounds.bottom - playerBounds.top + 8;
  const preferredTopFits = preferredTop >= 8 && preferredTop <= maximumTop;
  const alternateTopFits = alternateTop >= 8 && alternateTop <= maximumTop;
  const expectedTop = preferredTopFits || !alternateTopFits ? preferredTop : alternateTop;

  assert.equal(Number.parseFloat(menu.style.left), expectedLeft);
  assert.equal(Number.parseFloat(menu.style.top), expectedTop);
}

async function testFloatingMenusAnchorToControlsAndStayWithinTheFullscreenPlayer() {
  const {
    audioControl,
    audioMenu,
    contextMenu,
    controls,
    mobileMenu,
    mobileSettings,
    overflowControl,
    player,
    subtitleMenu,
    subtitlesControl,
  } = createPlayer();
  player.bounds = {left: 100, top: 50, width: 640, height: 600};
  audioControl.bounds = {left: 360, top: 500, width: 30, height: 30, bottom: 530};
  subtitlesControl.bounds = {left: 480, top: 500, width: 30, height: 30, bottom: 530};
  overflowControl.bounds = {left: 660, top: 500, width: 30, height: 30, bottom: 530};
  mobileSettings.bounds = {left: 520, top: 100, width: 160, height: 34, bottom: 134};
  contextMenu.bounds = {left: 0, top: 0, width: 180, height: 200};
  audioMenu.bounds = {left: 0, top: 0, width: 180, height: 280};
  subtitleMenu.bounds = {left: 0, top: 0, width: 180, height: 344};
  mobileMenu.bounds = {left: 0, top: 0, width: 220, height: 190};
  player.connectedCallback();
  await nextTick();
  await nextTick();
  document.fullscreenElement = player;

  try {
    controls.emit('click', {target: subtitlesControl});
    assert.equal(subtitleMenu.hidden, false);
    assertPopupAnchorsToControl(subtitleMenu, subtitlesControl, player);
    assertFloatingMenuFitsPlayer(subtitleMenu, player);

    controls.emit('click', {target: audioControl});
    assert.equal(audioMenu.hidden, false);
    assertPopupAnchorsToControl(audioMenu, audioControl, player);
    assertFloatingMenuFitsPlayer(audioMenu, player);

    controls.emit('click', {target: overflowControl});
    assert.equal(mobileMenu.hidden, false);
    assertPopupAnchorsToControl(mobileMenu, overflowControl, player);
    assertFloatingMenuFitsPlayer(mobileMenu, player);

    mobileMenu.emit('click', {target: mobileSettings});
    assert.equal(contextMenu.hidden, false);
    assertPopupAnchorsToControl(contextMenu, mobileSettings, player);
    assertFloatingMenuFitsPlayer(contextMenu, player);
  } finally {
    document.fullscreenElement = null;
  }
}

async function testFullscreenFrameAlignmentPillAppearsAtOrAboveFivePercentDifference() {
  const {
    frameAlignmentCentred,
    frameAlignmentEnd,
    frameAlignmentStart,
    fullscreenFrameAlignment,
    player,
  } = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  document.fullscreenElement = player;
  document.emit('fullscreenchange');
  assert.equal(fullscreenFrameAlignment.hidden, true);

  player.bounds = {bottom: 348, height: 348, left: 0, right: 640, top: 0, width: 640};
  window.emit('resize');
  assert.equal(fullscreenFrameAlignment.hidden, true);

  player.bounds = {bottom: 342, height: 342, left: 0, right: 640, top: 0, width: 640};
  window.emit('resize');
  assert.equal(fullscreenFrameAlignment.hidden, false);
  assert.equal(frameAlignmentCentred.getAttribute('aria-pressed'), 'true');
  assert.equal(frameAlignmentStart.getAttribute('aria-label'), 'Left');
  assert.equal(frameAlignmentCentred.getAttribute('aria-label'), 'Centred');
  assert.equal(frameAlignmentEnd.getAttribute('aria-label'), 'Right');

  fullscreenFrameAlignment.emit('click', {target: frameAlignmentStart});
  assert.equal(player.getAttribute('data-player-frame-axis'), 'horizontal');
  assert.equal(player.getAttribute('data-player-frame-alignment'), 'start');
  assert.equal(frameAlignmentStart.getAttribute('aria-pressed'), 'true');
  assert.equal(frameAlignmentCentred.getAttribute('aria-pressed'), 'false');

  player.bounds = {bottom: 320, height: 320, left: 0, right: 360, top: 0, width: 360};
  window.emit('resize');
  assert.equal(player.getAttribute('data-player-frame-axis'), 'vertical');
  assert.equal(player.getAttribute('data-player-frame-alignment'), 'centred');
  assert.equal(frameAlignmentCentred.getAttribute('aria-label'), 'Centred');
  assert.equal(frameAlignmentStart.getAttribute('aria-label'), 'Top');
  assert.equal(frameAlignmentEnd.getAttribute('aria-label'), 'Bottom');

  player.bounds = {bottom: 360, height: 360, left: 0, right: 640, top: 0, width: 640};
  window.emit('resize');
  assert.equal(fullscreenFrameAlignment.hidden, true);
  assert.equal(player.hasAttribute('data-player-frame-axis'), false);
  assert.equal(player.hasAttribute('data-player-frame-alignment'), false);
  document.fullscreenElement = null;
  document.emit('fullscreenchange');
}

async function testSelectPlayStatusClearsWhenPlaybackStarts() {
  const {controls, player, status, toggleControl, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();
  assert.equal(toggleControl.dataset.playerIconState, 'default');

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
  assert.equal(toggleControl.dataset.playerIconState, 'alternate');

  video.paused = true;
  controls.emit('click', {target: playButton});
  video.paused = false;
  video.emit('play');
  rejectPlay(new Error('A stale play attempt failed.'));
  await nextTick();
  assert.equal(status.textContent, '');
}

async function testFrameToggleMatchesVideoStateAndFrameSize() {
  const {frameToggle, player, video} = createPlayer();
  video.bounds = {bottom: 300, height: 300, left: 0, right: 480, top: 0, width: 480};
  player.connectedCallback();
  await nextTick();
  await nextTick();

  assert.equal(
    frameToggle.style.getPropertyValue('--k-player-frame-toggle-size'),
    '150px'
  );
  assert.equal(frameToggle.getAttribute('aria-label'), 'Play');
  assert.equal(frameToggle.dataset.playerIconState, 'default');

  frameToggle.emit('click', {target: frameToggle});
  assert.equal(video.playCalls, 1);

  video.paused = false;
  video.emit('play');
  assert.equal(frameToggle.getAttribute('aria-label'), 'Pause');
  assert.equal(frameToggle.dataset.playerIconState, 'alternate');

  frameToggle.emit('click', {target: frameToggle});
  assert.equal(video.pauseCalls, 1);
  assert.equal(frameToggle.getAttribute('aria-label'), 'Play');
  assert.equal(frameToggle.dataset.playerIconState, 'default');
}

async function testFrameToggleListenerDoesNotSurviveAReconnect() {
  const {frameToggle, player, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  player.disconnectedCallback();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  frameToggle.emit('click', {target: frameToggle});
  assert.equal(video.playCalls, 1);
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
  video.emit('loadedmetadata');

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
  video.emit('loadedmetadata');

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
  video.emit('loadedmetadata');

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

async function testQueueTransitionDoesNotSaveThePreviousEntryPosition() {
  const {player, video} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();
  video.emit('loadedmetadata');
  video.currentTime = 84;

  completionPayload = {
    nextEntry: queuedEntry(),
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  document.fullscreenElement = player;
  video.emit('ended');
  await nextTick();
  await nextTick();
  await nextTick();

  const start = fetchCalls.length;
  window.emit('pagehide');
  await nextTick();

  assert.equal(progressCallsSince(start).length, 0);
  completionPayload = null;
  document.fullscreenElement = null;
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

async function testAutoplayNextIsScopedToTheCurrentQueueItem() {
  const {
    autoplayNextControl,
    autoplayNextOption,
    player,
    queueNext,
    status,
    video,
  } = createPlayer({hasQueuedItem: true, queuedItemCount: 2});
  assert.ok(autoplayNextControl);
  assert.ok(autoplayNextOption);
  assert.ok(queueNext);
  player.connectedCallback();
  await nextTick();
  await nextTick();

  assert.equal(autoplayNextControl.checked, true);
  assert.equal(autoplayNextOption.hidden, false);
  autoplayNextControl.checked = false;
  autoplayNextControl.emit('change');
  assert.equal(autoplayNextControl.checked, false);

  const completionCallsBeforeEnd = fetchCalls.filter(
    (call) => String(call.url).endsWith('/complete-current')
  ).length;
  video.emit('ended');
  await nextTick();
  await nextTick();
  assert.equal(
    fetchCalls.filter((call) => String(call.url).endsWith('/complete-current')).length,
    completionCallsBeforeEnd + 1
  );
  assert.equal(status.textContent, 'Playback complete. Select Play next to continue.');

  completionPayload = {
    nextEntry: queuedEntry(),
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  document.fullscreenElement = player;
  queueNext.emit('click', {preventDefault() {}, stopPropagation() {}});
  await nextTick();
  await nextTick();
  await nextTick();

  assert.equal(player.getAttribute('entry-position'), '1');
  assert.equal(autoplayNextControl.checked, true);
  assert.equal(autoplayNextControl.disabled, false);
  assert.equal(autoplayNextOption.hidden, false);

  completionPayload = {
    nextEntry: {
      ...queuedEntry(),
      displayTitle: 'Final episode',
      fullscreenTitle: 'Example Show · Final episode',
      itemId: 3,
      position: 2,
    },
    nextUrl: `/item/3?playbackSession=${'s'.repeat(32)}`
  };
  queueNext.emit('click', {preventDefault() {}, stopPropagation() {}});
  await nextTick();
  await nextTick();
  await nextTick();

  assert.equal(player.getAttribute('entry-position'), '2');
  assert.equal(autoplayNextControl.checked, false);
  assert.equal(autoplayNextOption.hidden, true);
  document.fullscreenElement = null;
  completionPayload = null;
  activePlaybackQueue = null;
}

async function testFullscreenQueueControlAdvancesPlayback() {
  const {controls, fullscreenQueueNext, player} = createPlayer({
    hasFullscreenQueueNext: true,
    hasQueuedItem: true
  });
  assert.ok(fullscreenQueueNext);
  player.connectedCallback();
  await nextTick();
  await nextTick();

  completionPayload = {
    nextEntry: queuedEntry(),
    nextUrl: `/item/2?playbackSession=${'s'.repeat(32)}`
  };
  document.fullscreenElement = player;
  controls.emit('click', {target: fullscreenQueueNext});
  await nextTick();
  await nextTick();
  await nextTick();

  assert.equal(player.getAttribute('entry-position'), '1');
  assert.equal(document.fullscreenElement, player);
  assert.equal(assignedLocations.length, 0);
  document.fullscreenElement = null;
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

async function testFullscreenClockShowsLocalTimeWithSeconds() {
  const {fullscreenTime, player} = createPlayer();
  player.connectedCallback();
  await nextTick();
  await nextTick();

  document.fullscreenElement = player;
  document.emit('fullscreenchange');

  assert.match(fullscreenTime.textContent, /\d{1,2}:\d{2}:\d{2}/);
  assert.equal(scheduledIntervals.size, 1);
  runScheduledIntervals();
  assert.match(fullscreenTime.textContent, /\d{1,2}:\d{2}:\d{2}/);

  document.fullscreenElement = null;
  document.emit('fullscreenchange');
  assert.equal(fullscreenTime.textContent, '');
  assert.equal(scheduledIntervals.size, 0);
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
  await testTimelinePreviewTracksHoverAndDrag();
  await testMobileOverflowKeepsSecondaryPlaybackControlsTogether();
  await testPlayerPopupsShareToggleAndDismissalRules();
  await testTheatreModeExpandsThePlayerAndUpdatesBothControls();
  await testPlayerTooltipsFollowTheCurrentButtonState();
  await testFrameToggleUsesTheSharedPlayerControlsTimer();
  await testFloatingMenusAnchorToControlsAndStayWithinTheFullscreenPlayer();
  await testFullscreenFrameAlignmentPillAppearsAtOrAboveFivePercentDifference();
  await testSelectPlayStatusClearsWhenPlaybackStarts();
  await testFrameToggleMatchesVideoStateAndFrameSize();
  await testFrameToggleListenerDoesNotSurviveAReconnect();
  await testWebVttSettingsApplyWithoutReloadingTheTrack();
  await testSubtitleSettingsSavesCollapseToTheLatestState();
  await testBackwardSeekSavesAsASeekWhenPaused();
  await testBackwardSeekIsQueuedUntilTheActiveSaveFinishes();
  await testProgressUsesTheAuthoritativeCatalogueDuration();
  await testGeneratedResumeKeepsTheFullEpisodeTimeline();
  await testPageHideDoesNotSaveBeforeTheResumeSeekIsApplied();
  await testQueueTransitionDoesNotSaveThePreviousEntryPosition();
  await testProfileSubtitlePreferenceDisablesTheInitialTrack();
  await testPlaybackErrorReconnectsInsteadOfClaimingTheFormatIsUnsupported();
  await testEachQueuedEntryGetsItsOwnStreamRecoveryAttempt();
  await testAudioSelectionCannotOutliveAQueueTransition();
  await testQueueControlAdvancesPlayback();
  await testAutoplayNextIsScopedToTheCurrentQueueItem();
  await testFullscreenQueueControlAdvancesPlayback();
  await testQueueAdvanceAutoplaysWithoutLeavingFullscreen();
  await testFullscreenClockShowsLocalTimeWithSeconds();
  await testFullscreenExitNavigatesWithoutWaitingForProgress();
  await testQueueAdvanceNavigatesToTheNextItemOutsideFullscreen();
})()
  .then(() => process.stdout.write('browser playback status checks passed\n'))
  .catch((error) => {
    process.stderr.write(`${error.stack}\n`);
    process.exitCode = 1;
  });
