(() => {
  'use strict';

  const {escapeHtml} = window.kanvasInternals;

  class KanvasPlaybackPlayer extends HTMLElement {
    connectedCallback() {
      const video = this.querySelector('video');
      const status = this.querySelector('.k-player__status');
      const controls = this.querySelector('.k-player__controls');
      const timeline = this.querySelector('[data-player-timeline]');
      const timelinePreview = this.querySelector('[data-player-timeline-preview]');
      const bufferedIndicator = this.querySelector('[data-player-buffered]');
      const currentTime = this.querySelector('[data-player-current-time]');
      const remainingTime = this.querySelector('[data-player-remaining-time]');
      const volume = this.querySelector('[data-player-volume]');
      const contextMenu = this.querySelector('[data-player-context-menu]');
      const audioMenu = this.querySelector('[data-player-audio-menu]');
      const subtitleMenu = this.querySelector('[data-player-subtitle-menu]');
      const mobileMenu = this.querySelector('[data-player-mobile-menu]');
      const playerTooltip = this.querySelector('[data-player-tooltip-host]');
      const subtitleTimingLabel = subtitleMenu?.querySelector('[data-player-subtitle-timing-label]');
      const subtitleFontScaleLabel = subtitleMenu?.querySelector('[data-player-subtitle-font-scale-label]');
      const subtitleAppearance = subtitleMenu?.querySelector('[data-player-subtitle-appearance]');
      const nativeControls = this.querySelector('[data-player-native-controls]');
      const volumeBoostControl = this.querySelector('[data-player-volume-boost]');
      const autoplayNextControl = this.querySelector('[data-player-autoplay-next]');
      const autoplayNextOption = this.querySelector('[data-player-autoplay-next-option]');
      const mobileVolume = this.querySelector('[data-player-mobile-volume]');
      const volumeValueLabels = Array.from(
        this.querySelectorAll('[data-player-volume-value]')
      ).filter((label) => label instanceof Element);
      const kestrelLink = this.querySelector('[data-player-kestrel]');
      const audioOptions = audioMenu?.querySelector('[data-player-audio-options]');
      const subtitleOptions = subtitleMenu?.querySelector('[data-player-subtitle-options]');
      const subtitleFonts = this.querySelector('[data-player-ass-fonts]');
      const fullscreenTitle = this.querySelector('[data-player-fullscreen-title]');
      const fullscreenSpecialInfo = this.querySelector('[data-player-fullscreen-special-info]');
      const fullscreenTime = this.querySelector('[data-player-fullscreen-time]');
      const fullscreenFrameAlignment = this.querySelector('[data-player-frame-alignment-controls]');
      const frameToggle = this.querySelector('[data-player-frame-toggle]');
      const sessionId = this.getAttribute('session-id');
      const queueNext = document.querySelector('[data-player-next]');
      const fullscreenQueueNext = controls?.querySelector('[data-player-action="next"]');
      const queueNextControls = [queueNext, fullscreenQueueNext].filter(
        (control) => control instanceof Element
      );
      let entryPosition = Number(this.getAttribute('entry-position') || '0');
      let resumePosition = Number(this.getAttribute('resume-position') || '0');
      const autoplayOnResume = this.getAttribute('autoplay-on-resume') === 'true';
      const playOnLoad = this.getAttribute('play-on-load') === 'true';
      let catalogueDuration = Number(this.getAttribute('duration-seconds') || '0');
      let subtitleTimingOffsetMilliseconds = Number(this.getAttribute('subtitle-timing-offset-milliseconds') || '0');
      let subtitleFontScalePercent = Number(this.getAttribute('subtitle-font-scale-percent') || '100');
      let subtitleBackground = this.getAttribute('subtitle-background') === 'true';
      let subtitleShadow = this.getAttribute('subtitle-shadow') === 'true';
      let subtitleVerticalPosition = this.getAttribute('subtitle-vertical-position') || 'author';
      if (!video || !status || !controls || !timeline || !timelinePreview || !bufferedIndicator || !currentTime || !remainingTime || !volume || !contextMenu || !audioMenu || !subtitleMenu || !mobileMenu || !playerTooltip || !subtitleTimingLabel || !subtitleFontScaleLabel || !subtitleAppearance || !nativeControls || !volumeBoostControl || !mobileVolume || !fullscreenTitle || !fullscreenSpecialInfo || !fullscreenTime || !fullscreenFrameAlignment || !frameToggle || !sessionId || !Number.isSafeInteger(entryPosition) || entryPosition < 0 || !Number.isFinite(resumePosition) || !Number.isSafeInteger(subtitleTimingOffsetMilliseconds) || Math.abs(subtitleTimingOffsetMilliseconds) > 30000 || !Number.isSafeInteger(subtitleFontScalePercent) || subtitleFontScalePercent < 75 || subtitleFontScalePercent > 200 || subtitleFontScalePercent % 25 !== 0 || !['author', 'top', 'middle', 'bottom'].includes(subtitleVerticalPosition)) return;
      const frameAlignmentOptions = Array.from(
        fullscreenFrameAlignment.querySelectorAll('[data-player-frame-alignment-option]')
      ).filter((option) => option instanceof Element);
      if (frameAlignmentOptions.length !== 3) return;
      const updateFrameToggleSize = () => {
        const {height, width} = video.getBoundingClientRect();
        if (!Number.isFinite(width) || !Number.isFinite(height)) return;
        frameToggle.style.setProperty(
          '--k-player-frame-toggle-size', `${Math.max(0, Math.min(width, height) * 0.5)}px`
        );
      };
      const frameToggleResizeObserver = typeof ResizeObserver === 'function'
        ? new ResizeObserver(updateFrameToggleSize)
        : null;
      frameToggleResizeObserver?.observe(video);
      video.loop = false;
      video.removeAttribute('loop');
      let lastReportedPosition = -1;
      let resumeApplied = false;
      let seeking = false;
      let completing = false;
      let reporting = false;
      let activeProgress = null;
      let pendingProgress = null;
      let progressReportPromise = null;
      let playerControlsHideTimer = null;
      let fullscreenClockTimer = null;
      let deliveryMode = 'direct';
      let streamStartSeconds = 0;
      let generatedStreamSeekPending = false;
      let pendingDirectSeek = null;
      let streamRecoveryAttemptCount = 0;
      let activeStreamRecoveryId = null;
      let nextStreamRecoveryId = 0;
      let assRenderer = null;
      let nativeSubtitleTrack = null;
      let nativeSubtitleTrackLoaded = false;
      let nativeSubtitleTimingOffsetMilliseconds = 0;
      const nativeSubtitleCueStates = new Map();
      let pendingTrackSelectionSave = null;
      let trackSelectionSaveTimer = null;
      let trackSelectionSaveSequence = Promise.resolve();
      let latestTrackSelectionSaveVersion = 0;
      let audioSelectionVersion = 0;
      let playbackAttemptVersion = 0;
      let deliveryRequestVersion = 0;
      let pendingItemPageUrl = null;
      let entryPlaybackReady = false;
      let mediaEntryPosition = null;
      let webkitFullscreenActive = false;
      let wasPlayerFullscreen = false;
      let timelinePointerDown = false;
      let activePlayerTooltipButton = null;
      let selectedFrameAlignment = 'centred';
      let activeFullscreenFrameAxis = null;
      let hasQueuedNextItem = autoplayNextControl instanceof HTMLInputElement;
      let autoplayNext = hasQueuedNextItem && autoplayNextControl.checked;
      let volumeBoostEnabled = volumeBoostControl.checked === true;
      let selectedAudioStream = Number(audioMenu.querySelector('[data-player-audio-stream][aria-pressed="true"]')?.getAttribute('data-player-audio-stream') || '0');
      let selectedSubtitleTrack = subtitleMenu.querySelector('[data-player-subtitle-track][aria-pressed="true"]')?.getAttribute('data-player-subtitle-track') || null;
      const profileSubtitlePreference = typeof document.querySelector === 'function'
        ? document.querySelector('kanvas-profile-menu')?.getAttribute('data-preferred-subtitle-language')
        : null;
      const subtitlesDisabledByProfile = profileSubtitlePreference?.trim().toLowerCase() === 'none';
      if (subtitlesDisabledByProfile) selectedSubtitleTrack = null;
      const maxSubtitleTimingOffsetMilliseconds = 30000;
      const minSubtitleFontScalePercent = 75;
      const maxSubtitleFontScalePercent = 200;
      const selectPlayStatus = 'Select Play to start this video.';
      const invalidatePlaybackAttempts = () => {
        playbackAttemptVersion += 1;
      };
      const invalidateDeliveryRequests = () => {
        deliveryRequestVersion += 1;
        invalidatePlaybackAttempts();
      };
      const requestPlayback = () => {
        const attemptVersion = ++playbackAttemptVersion;
        void video.play().catch(() => {
          if (attemptVersion !== playbackAttemptVersion || !video.paused) return;
          status.textContent = selectPlayStatus;
        });
      };
      const clearSelectPlayStatus = () => {
        if (status.textContent === selectPlayStatus) status.textContent = '';
      };
      const isOptionalString = (value) => value === null || typeof value === 'string';
      const subtitleTrackLabel = (track) => {
        const label = [track.language, track.title, track.codec].filter(Boolean).join(' · ') || 'Subtitle';
        const flags = [track.default ? 'Default' : '', track.forced ? 'Forced' : ''].filter(Boolean).join(' ');
        return flags ? `${label} · ${flags}` : label;
      };
      const audioTrackLabel = (track, index) => (
        [track.language, track.title, track.codec].filter(Boolean).join(' · ') || `Audio ${index + 1}`
      );
      const parseNextEntry = (candidate) => {
        if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
          throw new Error('Playback queue entry is invalid');
        }
        const entry = candidate;
        const validFormat = (value) => ['webvtt', 'ass', 'unsupported'].includes(value);
        if (
          !Number.isSafeInteger(entry.position) || entry.position < 0
          || !Number.isSafeInteger(entry.itemId) || entry.itemId < 1
          || typeof entry.displayTitle !== 'string' || entry.displayTitle.length === 0
          || typeof entry.fullscreenTitle !== 'string' || entry.fullscreenTitle.length === 0
          || !isOptionalString(entry.specialInfo)
          || (entry.durationSeconds !== null && (!Number.isFinite(entry.durationSeconds) || entry.durationSeconds < 0))
          || !Number.isFinite(entry.savedResumePositionSeconds) || entry.savedResumePositionSeconds < 0
          || !Array.isArray(entry.audioStreams) || !Array.isArray(entry.subtitleTracks)
          || !Array.isArray(entry.subtitleFontIds)
          || !Number.isSafeInteger(entry.selectedAudioStream) || entry.selectedAudioStream < 0
          || !isOptionalString(entry.selectedSubtitleTrack)
          || !Number.isSafeInteger(entry.subtitleTimingOffsetMilliseconds) || Math.abs(entry.subtitleTimingOffsetMilliseconds) > 30000
          || !Number.isSafeInteger(entry.subtitleFontScalePercent) || entry.subtitleFontScalePercent < 75 || entry.subtitleFontScalePercent > 200 || entry.subtitleFontScalePercent % 25 !== 0
          || typeof entry.subtitleBackground !== 'boolean' || typeof entry.subtitleShadow !== 'boolean'
          || !['author', 'top', 'middle', 'bottom'].includes(entry.subtitleVerticalPosition)
        ) {
          throw new Error('Playback queue entry is invalid');
        }
        const audioStreams = entry.audioStreams.map((track) => {
          if (!track || typeof track !== 'object' || Array.isArray(track) || !isOptionalString(track.codec) || !isOptionalString(track.language) || !isOptionalString(track.title)) {
            throw new Error('Playback audio track is invalid');
          }
          return {codec: track.codec, language: track.language, title: track.title};
        });
        const subtitleTracks = entry.subtitleTracks.map((track) => {
          if (
            !track || typeof track !== 'object' || Array.isArray(track)
            || typeof track.id !== 'string' || !/^(?:embedded|sidecar)-\d+$/.test(track.id)
            || !isOptionalString(track.codec) || !isOptionalString(track.language) || !isOptionalString(track.title)
            || typeof track.default !== 'boolean' || typeof track.forced !== 'boolean'
            || !validFormat(track.format)
          ) {
            throw new Error('Playback subtitle track is invalid');
          }
          return {
            codec: track.codec,
            default: track.default,
            forced: track.forced,
            format: track.format,
            id: track.id,
            language: track.language,
            title: track.title
          };
        });
        if (
          entry.selectedAudioStream >= audioStreams.length && audioStreams.length > 0
          || (entry.selectedSubtitleTrack !== null && !subtitleTracks.some((track) => track.id === entry.selectedSubtitleTrack))
          || entry.subtitleFontIds.some((fontId) => typeof fontId !== 'string' || !/^embedded-font-\d+$/.test(fontId))
        ) {
          throw new Error('Playback queue selection is invalid');
        }
        return {
          audioStreams,
          displayTitle: entry.displayTitle,
          durationSeconds: entry.durationSeconds,
          fullscreenTitle: entry.fullscreenTitle,
          itemId: entry.itemId,
          position: entry.position,
          savedResumePositionSeconds: entry.savedResumePositionSeconds,
          selectedAudioStream: entry.selectedAudioStream,
          selectedSubtitleTrack: entry.selectedSubtitleTrack,
          specialInfo: entry.specialInfo,
          subtitleBackground: entry.subtitleBackground,
          subtitleFontIds: entry.subtitleFontIds,
          subtitleFontScalePercent: entry.subtitleFontScalePercent,
          subtitleShadow: entry.subtitleShadow,
          subtitleTimingOffsetMilliseconds: entry.subtitleTimingOffsetMilliseconds,
          subtitleTracks,
          subtitleVerticalPosition: entry.subtitleVerticalPosition
        };
      };
      const updateFullscreenInfo = (entry) => {
        fullscreenTitle.textContent = entry.fullscreenTitle;
        fullscreenSpecialInfo.textContent = entry.specialInfo || '';
        fullscreenSpecialInfo.hidden = entry.specialInfo === null;
      };
      const applyEntryTrackOptions = (entry) => {
        selectedAudioStream = entry.selectedAudioStream;
        selectedSubtitleTrack = subtitlesDisabledByProfile ? null : entry.selectedSubtitleTrack;
        subtitleTimingOffsetMilliseconds = entry.subtitleTimingOffsetMilliseconds;
        subtitleFontScalePercent = entry.subtitleFontScalePercent;
        subtitleBackground = entry.subtitleBackground;
        subtitleShadow = entry.subtitleShadow;
        subtitleVerticalPosition = entry.subtitleVerticalPosition;
        this.setAttribute('subtitle-timing-offset-milliseconds', String(subtitleTimingOffsetMilliseconds));
        this.setAttribute('subtitle-font-scale-percent', String(subtitleFontScalePercent));
        this.setAttribute('subtitle-background', String(subtitleBackground));
        this.setAttribute('subtitle-shadow', String(subtitleShadow));
        this.setAttribute('subtitle-vertical-position', subtitleVerticalPosition);
        if (audioOptions instanceof Element) {
          audioOptions.innerHTML = entry.audioStreams.map((track, index) => (
            `<button class="k-player__track-option" type="button" data-player-audio-stream="${index}" aria-pressed="${String(index === selectedAudioStream)}">${escapeHtml(audioTrackLabel(track, index))}</button>`
          )).join('');
        }
        if (subtitleOptions instanceof Element) {
          const offOption = `<button class="k-player__track-option" type="button" data-player-subtitle-track="" aria-pressed="${String(selectedSubtitleTrack === null)}">Off</button>`;
          const trackOptions = entry.subtitleTracks.map((track) => {
            const unsupported = track.format === 'unsupported' ? ' data-player-subtitle-unsupported' : '';
            return `<button class="k-player__track-option" type="button" data-player-subtitle-track="${track.id}" data-player-subtitle-format="${track.format}"${unsupported} aria-pressed="${String(track.id === selectedSubtitleTrack)}">${escapeHtml(subtitleTrackLabel(track))}</button>`;
          }).join('');
          subtitleOptions.innerHTML = offOption + trackOptions;
        }
        if (subtitleFonts instanceof Element) {
          subtitleFonts.innerHTML = entry.subtitleFontIds.map((fontId) => (
            `<span data-player-ass-font="${fontId}"></span>`
          )).join('');
        }
        updateTrackOptions();
      };
      const updateAutoplayNextControl = () => {
        if (!(autoplayNextControl instanceof HTMLInputElement)) return;
        autoplayNextControl.checked = autoplayNext;
      };
      const setAutoplayNextAvailability = (available) => {
        hasQueuedNextItem = available;
        autoplayNext = available;
        if (autoplayNextOption instanceof Element) autoplayNextOption.hidden = !available;
        updateAutoplayNextControl();
      };
      const updatePlaybackQueue = () => {
        if (typeof document.querySelector !== 'function') return false;
        const queue = document.querySelector('[data-player-queue]');
        if (!(queue instanceof Element)) {
          if (fullscreenQueueNext instanceof Element) fullscreenQueueNext.hidden = true;
          return false;
        }
        const queueEntries = Array.from(queue.querySelectorAll('.k-playback-queue__entry'));
        queueEntries[0]?.remove();
        const remainingEntries = Array.from(queue.querySelectorAll('.k-playback-queue__entry'));
        if (remainingEntries.length === 0) {
          queue.remove();
          if (fullscreenQueueNext instanceof Element) fullscreenQueueNext.hidden = true;
          return false;
        }
        const countLabel = remainingEntries.length === 1 ? 'item' : 'items';
        const heading = queue.querySelector('.k-playback-queue__heading');
        if (heading) heading.textContent = `Queue · ${remainingEntries.length} ${countLabel}`;
        const nextEntry = remainingEntries[0];
        const nextTitle = nextEntry.querySelector('.k-playback-queue__title');
        const nextContext = nextEntry.querySelector('.k-playback-queue__context');
        const summaryTitle = queue.querySelector('.k-playback-queue__next .k-playback-queue__title');
        const summaryContext = queue.querySelector('.k-playback-queue__next .k-playback-queue__context');
        if (summaryTitle && nextTitle) summaryTitle.textContent = nextTitle.textContent;
        if (summaryContext) summaryContext.textContent = nextContext?.textContent || '';
        return true;
      };
      const setQueueNextBusy = (busy) => {
        queueNextControls.forEach((control) => {
          control.toggleAttribute('disabled', busy);
          control.setAttribute('aria-disabled', String(busy));
        });
        if (autoplayNextControl instanceof HTMLInputElement) autoplayNextControl.disabled = busy;
      };
      const itemPageUrl = (nextUrl) => (
        typeof nextUrl === 'string' && /^\/item\/\d+\?playbackSession=[A-Za-z0-9_-]+$/.test(nextUrl)
          ? nextUrl
          : null
      );
      const itemPageAutoplayUrl = (nextUrl) => `${nextUrl}&start=true`;
      const synchroniseItemPageUrl = (nextUrl) => {
        if (nextUrl === null || typeof window.history?.replaceState !== 'function') return;
        window.history.replaceState(null, '', itemPageAutoplayUrl(nextUrl));
      };
      const preserveVideoHeight = () => {
        const height = video.getBoundingClientRect().height;
        if (height <= 0) return;
        this.style.setProperty('--k-player-video-height', `${height}px`);
        this.classList.add('k-player--preparing');
      };
      const releaseVideoHeight = () => {
        this.classList.remove('k-player--preparing');
        this.style.removeProperty('--k-player-video-height');
      };
      const capabilityTypes = [
        'video/mp4; codecs="avc1.42E01E, mp4a.40.2"',
        'video/mp4; codecs="hvc1.1.6.L93.B0, mp4a.40.2"',
        'video/mp4; codecs="hev1.1.6.L93.B0, mp4a.40.2"'
      ];
      const browserCapabilities = async () => Promise.all(capabilityTypes.map(async (contentType) => {
        let mediaCapabilitiesSupported = false;
        try {
          if (navigator.mediaCapabilities?.decodingInfo) {
            const result = await navigator.mediaCapabilities.decodingInfo({type: 'file', video: {contentType}});
            mediaCapabilitiesSupported = result.supported === true;
          }
        } catch (_) {}
        return {
          content_type: contentType,
          media_capabilities_supported: mediaCapabilitiesSupported,
          can_play_type: video.canPlayType(contentType)
        };
      }));
      const selectedSubtitleButton = () => selectedSubtitleTrack === null
        ? null
        : subtitleMenu.querySelector(`[data-player-subtitle-track="${selectedSubtitleTrack}"]`);
      const floatingMenuInset = 8;
      const playerPopupOffset = floatingMenuInset;
      const clampFloatingMenuOffset = (offset, maximum) => {
        const boundedMaximum = Math.max(floatingMenuInset, maximum);
        return Math.max(floatingMenuInset, Math.min(offset, boundedMaximum));
      };
      const playerTooltipText = (button) => (
        button.getAttribute('aria-label')
        || button.getAttribute('data-player-tooltip')
        || button.textContent.trim()
      );
      const hidePlayerTooltip = () => {
        activePlayerTooltipButton = null;
        if (playerTooltip.hidden) return;
        playerTooltip.hidden = true;
        showPlayerControls();
      };
      const frameToggleIcon = () => {
        const iconSelector = frameToggle.dataset.playerIconState === 'alternate'
          ? '.k-player__control-icon--alternate .k-icon'
          : '.k-player__control-icon--default .k-icon';
        const icon = frameToggle.querySelector(iconSelector);
        return icon instanceof Element ? icon : null;
      };
      const playerTooltipAnchor = (button) => (
        button === frameToggle ? frameToggleIcon() || button : button
      );
      const showPlayerTooltip = (button) => {
        if (button.hasAttribute('disabled')) return;
        const text = playerTooltipText(button);
        if (!text) return;
        activePlayerTooltipButton = button;
        playerTooltip.textContent = text;
        playerTooltip.hidden = false;
        const playerBounds = this.getBoundingClientRect();
        const anchorBounds = playerTooltipAnchor(button).getBoundingClientRect();
        const tooltipBounds = playerTooltip.getBoundingClientRect();
        const minimumCenter = tooltipBounds.width / 2 + floatingMenuInset;
        const maximumCenter = playerBounds.width - minimumCenter;
        const center = anchorBounds.left - playerBounds.left + anchorBounds.width / 2;
        const left = Math.max(minimumCenter, Math.min(center, Math.max(minimumCenter, maximumCenter)));
        const above = anchorBounds.top - playerBounds.top - tooltipBounds.height - floatingMenuInset;
        const below = anchorBounds.bottom - playerBounds.top + floatingMenuInset;
        const top = above >= floatingMenuInset
          ? above
          : clampFloatingMenuOffset(
            below,
            playerBounds.height - tooltipBounds.height - floatingMenuInset
          );
        playerTooltip.style.left = `${left}px`;
        playerTooltip.style.top = `${top}px`;
        showPlayerControls();
      };
      const positionFloatingMenu = (
        menu,
        playerBounds,
        menuBounds,
        preferredClientLeft,
        preferredTop,
        alternateTop
      ) => {
        const maximumLeft = playerBounds.width - menuBounds.width - floatingMenuInset;
        const maximumTop = playerBounds.height - menuBounds.height - floatingMenuInset;
        const topFits = (top) => top >= floatingMenuInset && top <= maximumTop;
        const top = topFits(preferredTop) || !topFits(alternateTop)
          ? preferredTop
          : alternateTop;
        const left = clampFloatingMenuOffset(preferredClientLeft - playerBounds.left, maximumLeft);
        menu.style.left = `${left}px`;
        menu.style.top = `${clampFloatingMenuOffset(top, maximumTop)}px`;
      };
      const positionPlayerPopup = (menu, anchorBounds) => {
        const playerBounds = this.getBoundingClientRect();
        const menuBounds = menu.getBoundingClientRect();
        positionFloatingMenu(
          menu,
          playerBounds,
          menuBounds,
          anchorBounds.left,
          anchorBounds.top - playerBounds.top - menuBounds.height - playerPopupOffset,
          anchorBounds.bottom - playerBounds.top + playerPopupOffset
        );
      };
      const overflowControl = controls.querySelector('[data-player-action="overflow"]');
      const playerPopupMenus = new Map([
        ['menu', contextMenu],
        ['audio', audioMenu],
        ['subtitles', subtitleMenu],
        ['overflow', mobileMenu],
      ]);
      const playerPopupElements = Array.from(playerPopupMenus.values());
      let activePlayerPopupAction = null;
      let activePlayerPopupTrigger = null;
      const playerPopupMenu = (action) => playerPopupMenus.get(action) || null;
      const setPlayerPopupVisibility = (action, visible) => {
        const menu = playerPopupMenu(action);
        if (menu === null) return;
        menu.hidden = !visible;
        this.querySelectorAll(`[data-player-action="${action}"]`).forEach((control) => {
          control.setAttribute('aria-expanded', String(visible));
        });
      };
      const playerPopupContains = (target) => playerPopupElements.some(
        (menu) => menu.contains(target)
      );
      const playerPopupTriggerForTarget = (target) => {
        const element = target instanceof Element ? target : null;
        const control = element?.closest('[data-player-action]');
        if (!(control instanceof Element)) return null;
        const action = control.getAttribute('data-player-action');
        if (!playerPopupMenus.has(action)) return null;
        return controls.contains(control) || mobileMenu.contains(control) ? control : null;
      };
      const isFocusablePlayerControl = (control) => (
        control instanceof HTMLElement
        && typeof control.focus === 'function'
        && !control.hidden
        && !control.hasAttribute('disabled')
        && (
          typeof control.getClientRects !== 'function'
          || control.getClientRects().length > 0
        )
      );
      const focusFirstPlayerPopupControl = (menu) => {
        const control = menu.querySelector('button:not([disabled]), input:not([disabled])');
        if (!isFocusablePlayerControl(control)) return false;
        control.focus({preventScroll: true});
        return true;
      };
      const restorePlayerPopupFocus = (action, trigger) => {
        const controlsForAction = Array.from(
          this.querySelectorAll(`[data-player-action="${action}"]`)
        );
        const focusTarget = [trigger, ...controlsForAction, overflowControl].find(
          isFocusablePlayerControl
        );
        focusTarget?.focus({preventScroll: true});
      };
      const visiblePlayerPopupAction = () => {
        for (const [action, menu] of playerPopupMenus) {
          if (!menu.hidden) return action;
        }
        return null;
      };
      const dismissPlayerPopups = (restoreFocus = false) => {
        const visibleAction = visiblePlayerPopupAction();
        const activeMenu = activePlayerPopupAction === null
          ? null
          : playerPopupMenu(activePlayerPopupAction);
        const action = activeMenu !== null && !activeMenu.hidden
          ? activePlayerPopupAction
          : visibleAction;
        const trigger = action === activePlayerPopupAction ? activePlayerPopupTrigger : null;
        hidePlayerTooltip();
        playerPopupMenus.forEach((_menu, popupAction) => {
          setPlayerPopupVisibility(popupAction, false);
        });
        activePlayerPopupAction = null;
        activePlayerPopupTrigger = null;
        showPlayerControls();
        if (restoreFocus && action !== null && trigger !== null) {
          restorePlayerPopupFocus(action, trigger);
        }
        return visibleAction !== null;
      };
      const openPlayerPopup = (action, trigger, toggle = true) => {
        const menu = playerPopupMenu(action);
        if (menu === null) return false;
        if (toggle && !menu.hidden) {
          dismissPlayerPopups();
          return false;
        }
        dismissPlayerPopups();
        setPlayerPopupVisibility(action, true);
        activePlayerPopupAction = action;
        activePlayerPopupTrigger = trigger;
        if (
          !isFocusablePlayerControl(trigger)
          && !focusFirstPlayerPopupControl(menu)
          && trigger !== null
        ) {
          restorePlayerPopupFocus(action, trigger);
        }
        showPlayerControls();
        return true;
      };
      const showMobileMenu = (target) => {
        const targetBounds = target.getBoundingClientRect();
        if (!openPlayerPopup('overflow', target)) return;
        positionPlayerPopup(mobileMenu, targetBounds);
      };
      const showTrackMenu = (action, target) => {
        const menu = playerPopupMenu(action);
        if (menu === null) return;
        const targetBounds = target.getBoundingClientRect();
        if (!openPlayerPopup(action, target)) return;
        positionPlayerPopup(menu, targetBounds);
      };
      const updateTrackOptions = () => {
        audioMenu.querySelectorAll('[data-player-audio-stream]').forEach((option) => {
          option.setAttribute('aria-pressed', String(Number(option.getAttribute('data-player-audio-stream')) === selectedAudioStream));
        });
        subtitleMenu.querySelectorAll('[data-player-subtitle-track]').forEach((option) => {
          option.setAttribute('aria-pressed', String((option.getAttribute('data-player-subtitle-track') || null) === selectedSubtitleTrack));
        });
        subtitleTimingLabel.textContent = `${subtitleTimingOffsetMilliseconds >= 0 ? '+' : ''}${(subtitleTimingOffsetMilliseconds / 1000).toFixed(1)}s`;
        subtitleFontScaleLabel.textContent = `${subtitleFontScalePercent}%`;
        this.dataset.subtitleFontScale = String(subtitleFontScalePercent);
        this.dataset.subtitleBackground = String(subtitleBackground);
        this.dataset.subtitleShadow = String(subtitleShadow);
        this.dataset.subtitleVerticalPosition = subtitleVerticalPosition;
        const selectedSubtitle = selectedSubtitleButton();
        const timingAvailable = selectedSubtitle !== null && !selectedSubtitle.hasAttribute('data-player-subtitle-unsupported');
        const nativeCueAppearanceAvailable = typeof CSS !== 'undefined'
          && typeof CSS.supports === 'function'
          && CSS.supports('selector(video::cue)');
        const appearanceAvailable = timingAvailable
          && selectedSubtitle.getAttribute('data-player-subtitle-format') === 'webvtt'
          && nativeCueAppearanceAvailable;
        subtitleAppearance.hidden = !appearanceAvailable;
        subtitleMenu.querySelectorAll('[data-player-subtitle-timing-step], [data-player-subtitle-timing-reset]').forEach((option) => {
          option.toggleAttribute('disabled', !timingAvailable);
        });
        subtitleMenu.querySelectorAll('[data-player-subtitle-font-scale-step], [data-player-subtitle-background], [data-player-subtitle-shadow], [data-player-subtitle-position]').forEach((option) => {
          option.toggleAttribute('disabled', !appearanceAvailable);
        });
        subtitleMenu.querySelector('[data-player-subtitle-background]')?.setAttribute('aria-pressed', String(subtitleBackground));
        subtitleMenu.querySelector('[data-player-subtitle-shadow]')?.setAttribute('aria-pressed', String(subtitleShadow));
        subtitleMenu.querySelectorAll('[data-player-subtitle-position]').forEach((option) => {
          option.setAttribute('aria-pressed', String(option.getAttribute('data-player-subtitle-position') === subtitleVerticalPosition));
        });
      };
      const clearNativeSubtitle = () => {
        video.querySelectorAll('track[data-player-subtitle]').forEach((track) => track.remove());
        nativeSubtitleTrack = null;
        nativeSubtitleTrackLoaded = false;
        nativeSubtitleCueStates.clear();
      };
      const disposeAssRenderer = () => {
        if (assRenderer && typeof assRenderer.dispose === 'function') assRenderer.dispose();
        assRenderer = null;
      };
      const subtitleUrl = (trackId, timingOffsetMilliseconds = subtitleTimingOffsetMilliseconds) => {
        const url = new URL(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/entries/${entryPosition}/subtitles/${encodeURIComponent(trackId)}`, window.location.origin);
        if (streamStartSeconds > 0) url.searchParams.set('offsetSeconds', String(streamStartSeconds));
        url.searchParams.set('timingOffsetMilliseconds', String(timingOffsetMilliseconds));
        return url.href;
      };
      const assFontUrls = () => Array.from(
        this.querySelectorAll('[data-player-ass-font]'),
        (font) => new URL(
          `/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/entries/${entryPosition}/fonts/${encodeURIComponent(font.getAttribute('data-player-ass-font'))}`,
          window.location.origin,
        ).href,
      );
      const applyNativeSubtitlePosition = () => {
        for (const [cue, state] of nativeSubtitleCueStates) {
          if (!('line' in cue) || !('snapToLines' in cue)) continue;
          if (subtitleVerticalPosition === 'author') {
            cue.snapToLines = state.snapToLines;
            cue.line = state.line;
            continue;
          }
          cue.snapToLines = false;
          cue.line = {top: 10, middle: 50, bottom: 90}[subtitleVerticalPosition];
        }
      };
      const applyNativeSubtitleTiming = () => {
        if (!nativeSubtitleTrack || !nativeSubtitleTrackLoaded) return true;
        const textTrack = nativeSubtitleTrack.track;
        if (!textTrack || typeof textTrack.addCue !== 'function' || typeof textTrack.removeCue !== 'function') return false;
        const timingDeltaSeconds = (subtitleTimingOffsetMilliseconds - nativeSubtitleTimingOffsetMilliseconds) / 1000;
        try {
          for (const [cue, state] of nativeSubtitleCueStates) {
            const startTime = Math.max(0, state.startTime + timingDeltaSeconds);
            const endTime = state.endTime + timingDeltaSeconds;
            if (endTime <= 0) {
              if (state.attached) textTrack.removeCue(cue);
              state.attached = false;
              continue;
            }
            cue.startTime = startTime;
            cue.endTime = Math.max(startTime + 0.001, endTime);
            if (!state.attached) textTrack.addCue(cue);
            state.attached = true;
          }
          return true;
        } catch (_) {
          return false;
        }
      };
      const reloadSubtitles = () => {
        clearNativeSubtitle();
        disposeAssRenderer();
        const option = selectedSubtitleButton();
        if (!option) return;
        if (option.hasAttribute('data-player-subtitle-unsupported')) {
          status.textContent = 'This image subtitle needs Kestrel.';
          void offerKestrelFallback();
          return;
        }
        const format = option.getAttribute('data-player-subtitle-format');
        if (format === 'ass') {
          if (typeof window.SubtitlesOctopus !== 'function') {
            status.textContent = 'ASS subtitles are unavailable in this browser.';
            return;
          }
          try {
            status.textContent = 'Loading subtitles…';
            assRenderer = new window.SubtitlesOctopus({
              video,
              subUrl: subtitleUrl(selectedSubtitleTrack),
              workerUrl: '/_kanvas/libass/subtitles-octopus-worker.js',
              legacyWorkerUrl: '/_kanvas/libass/subtitles-octopus-worker-legacy.js',
              fallbackFont: '/_kanvas/libass/default.woff2',
              fonts: assFontUrls(),
              timeOffset: streamStartSeconds - subtitleTimingOffsetMilliseconds / 1000,
              renderMode: 'wasm-blend',
              onReady: () => {
                if (status.textContent === 'Loading subtitles…') status.textContent = '';
              },
              onError: () => { status.textContent = 'ASS subtitles could not be rendered.'; }
            });
          } catch (_) {
            status.textContent = 'ASS subtitles could not be rendered.';
          }
          return;
        }
        const track = document.createElement('track');
        track.kind = 'subtitles';
        const trackTimingOffsetMilliseconds = subtitleTimingOffsetMilliseconds;
        track.src = subtitleUrl(selectedSubtitleTrack, trackTimingOffsetMilliseconds);
        track.default = true;
        track.dataset.playerSubtitle = 'true';
        track.addEventListener('load', () => {
          if (nativeSubtitleTrack !== track || !track.track.cues) return;
          nativeSubtitleTrackLoaded = true;
          nativeSubtitleTimingOffsetMilliseconds = trackTimingOffsetMilliseconds;
          nativeSubtitleCueStates.clear();
          Array.from(track.track.cues).forEach((cue) => {
            nativeSubtitleCueStates.set(cue, {
              attached: true,
              endTime: cue.endTime,
              line: cue.line,
              snapToLines: cue.snapToLines,
              startTime: cue.startTime
            });
          });
          applyNativeSubtitlePosition();
          if (
            subtitleTimingOffsetMilliseconds !== trackTimingOffsetMilliseconds
            && !applyNativeSubtitleTiming()
          ) {
            reloadSubtitles();
            return;
          }
          if (status.textContent === 'Loading subtitles…') status.textContent = '';
        });
        track.addEventListener('error', () => {
          if (nativeSubtitleTrack === track) status.textContent = 'Subtitles could not be loaded.';
        });
        nativeSubtitleTrack = track;
        nativeSubtitleTrackLoaded = false;
        status.textContent = 'Loading subtitles…';
        video.appendChild(track);
        track.track.mode = 'showing';
      };
      const selectDelivery = async (autoplay, startSeconds = 0) => {
        const requestVersion = ++deliveryRequestVersion;
        const requestEntryPosition = entryPosition;
        invalidatePlaybackAttempts();
        status.textContent = 'Preparing playback…';
        const response = await fetch(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/entries/${requestEntryPosition}/compatibility`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({media: await browserCapabilities()})
        });
        const payload = await response.json().catch(() => ({}));
        if (requestVersion !== deliveryRequestVersion || requestEntryPosition !== entryPosition) {
          return false;
        }
        if (!response.ok || typeof payload.mode !== 'string') throw new Error('Playback compatibility failed');
        if (payload.mode === 'unsupported' || typeof payload.mediaUrl !== 'string') {
          releaseVideoHeight();
          clearNativeSubtitle();
          disposeAssRenderer();
          video.removeAttribute('src');
          video.load();
          status.textContent = 'This browser cannot play this video.';
          if (kestrelLink instanceof HTMLAnchorElement && typeof payload.fallbackUri === 'string') {
            kestrelLink.href = payload.fallbackUri;
            kestrelLink.textContent = 'Open in Kestrel';
            kestrelLink.hidden = false;
          }
          return false;
        }
        if (kestrelLink instanceof HTMLAnchorElement) kestrelLink.hidden = true;
        deliveryMode = payload.mode;
        streamStartSeconds = deliveryMode === 'direct' ? 0 : startSeconds;
        pendingDirectSeek = deliveryMode === 'direct' && startSeconds > 0 ? startSeconds : null;
        const mediaUrl = new URL(payload.mediaUrl, window.location.origin);
        if (streamStartSeconds > 0) mediaUrl.searchParams.set('startSeconds', String(streamStartSeconds));
        preserveVideoHeight();
        mediaEntryPosition = requestEntryPosition;
        video.src = mediaUrl.href;
        video.load();
        entryPlaybackReady = false;
        reloadSubtitles();
        if (autoplay) requestPlayback();
        return true;
      };
      const loadEntry = async (nextPosition, nextResumePosition, nextDuration, autoplay) => {
        if (!Number.isSafeInteger(nextPosition) || nextPosition < 0 || !Number.isFinite(nextResumePosition) || (nextDuration !== null && (!Number.isFinite(nextDuration) || nextDuration < 0))) {
          throw new Error('Playback queue entry is invalid');
        }
        dismissPlayerPopups();
        invalidateDeliveryRequests();
        audioSelectionVersion += 1;
        latestTrackSelectionSaveVersion += 1;
        streamRecoveryAttemptCount = 0;
        activeStreamRecoveryId = null;
        generatedStreamSeekPending = false;
        pendingDirectSeek = null;
        entryPlaybackReady = false;
        mediaEntryPosition = null;
        entryPosition = nextPosition;
        resumePosition = nextResumePosition;
        catalogueDuration = nextDuration === null ? 0 : nextDuration;
        resumeApplied = false;
        lastReportedPosition = -1;
        this.setAttribute('entry-position', String(entryPosition));
        this.setAttribute('resume-position', String(resumePosition));
        this.setAttribute('duration-seconds', String(catalogueDuration));
        return selectDelivery(autoplay, nextResumePosition);
      };
      const formatTime = (seconds) => {
        if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
        const totalSeconds = Math.floor(seconds);
        const minutes = Math.floor(totalSeconds / 60);
        const remainingSeconds = totalSeconds % 60;
        if (minutes < 60) return `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
        const hours = Math.floor(minutes / 60);
        return `${hours}:${String(minutes % 60).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
      };
      const actionButtons = (action) => Array.from(
        this.querySelectorAll(`[data-player-action="${action}"]`)
      ).filter((button) => button instanceof Element);
      const updateActionPresentation = (action, accessibleName, alternateIcon) => {
        const buttons = actionButtons(action);
        buttons.forEach((button) => {
          button.setAttribute('aria-label', accessibleName);
          button.dataset.playerTooltip = accessibleName;
          button.dataset.playerIconState = alternateIcon ? 'alternate' : 'default';
        });
        if (buttons.includes(activePlayerTooltipButton)) playerTooltip.textContent = accessibleName;
        this.querySelectorAll(`[data-player-action-label="${action}"]`).forEach((label) => {
          label.textContent = accessibleName;
        });
      };
      const playbackDuration = () => {
        const mediaDuration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
        if (deliveryMode !== 'direct' && catalogueDuration > 0) return catalogueDuration;
        if (catalogueDuration > 0) {
          return mediaDuration > 0 ? Math.min(mediaDuration, catalogueDuration) : catalogueDuration;
        }
        return mediaDuration;
      };
      const playbackPosition = () => {
        const duration = playbackDuration();
        const mediaPosition = Number.isFinite(video.currentTime) ? video.currentTime : 0;
        const offset = deliveryMode === 'direct' ? 0 : streamStartSeconds;
        return Math.min(Math.max(offset + mediaPosition, 0), duration);
      };
      const bufferedEnd = () => {
        const mediaPosition = Number.isFinite(video.currentTime) ? video.currentTime : 0;
        const buffered = video.buffered;
        for (let index = 0; index < buffered.length; index += 1) {
          const start = buffered.start(index);
          const end = buffered.end(index);
          if (Number.isFinite(start) && Number.isFinite(end) && start <= mediaPosition && mediaPosition <= end) return end;
        }
        return null;
      };
      const updateBufferedIndicator = (duration, position) => {
        const bufferedMediaEnd = bufferedEnd();
        const offset = deliveryMode === 'direct' ? 0 : streamStartSeconds;
        const end = bufferedMediaEnd === null
          ? position
          : Math.min(Math.max(offset + bufferedMediaEnd, position), duration);
        const startPercent = duration === 0 ? 0 : (position / duration) * 100;
        const endPercent = duration === 0 ? 0 : (end / duration) * 100;
        bufferedIndicator.style.setProperty('--buffered-start-percent', `${startPercent}%`);
        bufferedIndicator.style.setProperty('--buffered-end-percent', `${endPercent}%`);
      };
      const showTimelinePreview = (position, clientX = null) => {
        const duration = playbackDuration();
        const timelineBounds = timeline.getBoundingClientRect();
        if (!Number.isFinite(position) || duration <= 0 || timelineBounds.width <= 0) return;
        const relativePosition = Math.min(Math.max(position / duration, 0), 1);
        const resolvedClientX = Number.isFinite(clientX)
          ? clientX
          : timelineBounds.left + timelineBounds.width * relativePosition;
        const cardBounds = this.getBoundingClientRect();
        const offset = Math.min(
          Math.max(resolvedClientX - cardBounds.left, 0),
          cardBounds.width,
        );
        timelinePreview.textContent = formatTime(position);
        timelinePreview.style.setProperty('--k-player-timeline-preview-offset', `${offset}px`);
        timelinePreview.hidden = false;
      };
      const hideTimelinePreview = () => { timelinePreview.hidden = true; };
      const timelinePositionAt = (clientX) => {
        const duration = playbackDuration();
        const timelineBounds = timeline.getBoundingClientRect();
        if (!Number.isFinite(clientX) || duration <= 0 || timelineBounds.width <= 0) return null;
        const ratio = Math.min(Math.max((clientX - timelineBounds.left) / timelineBounds.width, 0), 1);
        return duration * ratio;
      };
      const volumeControls = [volume, mobileVolume];
      const minimumPlayerVolume = 0;
      const standardMaximumPlayerVolume = 1;
      const boostedMaximumPlayerVolume = 2;
      const playerVolumeComparisonTolerance = 0.0001;
      const maximumPlayerVolume = () => (
        volumeBoostEnabled ? boostedMaximumPlayerVolume : standardMaximumPlayerVolume
      );
      const clampPlayerVolume = (value, maximum = maximumPlayerVolume()) => (
        Math.min(Math.max(value, minimumPlayerVolume), maximum)
      );
      // A media element can only be routed through one Web Audio source, so keep
      // its graph for the lifetime of the element rather than recreating it on reconnect.
      let volumeAudio = this._volumeAudio ?? null;
      const disposeVolumeAudio = () => {
        if (volumeAudio === null) return;
        const audio = volumeAudio;
        volumeAudio = null;
        if (this._volumeAudio === audio) this._volumeAudio = null;
        audio.source.disconnect();
        audio.gain.disconnect();
        if (audio.context.state !== 'closed') void audio.context.close().catch(() => {});
      };
      if (volumeAudio?.video !== video) disposeVolumeAudio();
      const initialPlayerVolume = volumeAudio?.video === video
        && volumeAudio.gain.gain.value > standardMaximumPlayerVolume
        ? volumeAudio.gain.gain.value
        : video.volume;
      let playerVolume = clampPlayerVolume(initialPlayerVolume);
      let lastAudibleVolume = playerVolume > minimumPlayerVolume
        ? playerVolume
        : standardMaximumPlayerVolume;
      const resumeVolumeAudio = () => {
        if (
          volumeAudio === null
          || volumeAudio.context.state === 'running'
          || volumeAudio.context.state === 'closed'
        ) return;
        void volumeAudio.context.resume().catch(() => {});
      };
      const applyVolumeGain = () => {
        if (volumeAudio === null) return;
        volumeAudio.gain.gain.value = Math.max(playerVolume, standardMaximumPlayerVolume);
      };
      const initialiseVolumeBoost = () => {
        if (volumeAudio !== null) {
          if (volumeAudio.context.state === 'closed') return false;
          resumeVolumeAudio();
          return true;
        }
        const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
        if (typeof AudioContextConstructor !== 'function') return false;
        let audioContext = null;
        let audioSource = null;
        let gainNode = null;
        try {
          audioContext = new AudioContextConstructor();
          audioSource = audioContext.createMediaElementSource(video);
          gainNode = audioContext.createGain();
          audioSource.connect(gainNode);
          gainNode.connect(audioContext.destination);
          volumeAudio = {context: audioContext, gain: gainNode, source: audioSource, video};
          this._volumeAudio = volumeAudio;
          applyVolumeGain();
          resumeVolumeAudio();
          return true;
        } catch (_) {
          audioSource?.disconnect();
          gainNode?.disconnect();
          if (audioContext !== null) void audioContext.close().catch(() => {});
          return false;
        }
      };
      const setPlayerVolume = (value) => {
        playerVolume = clampPlayerVolume(value);
        video.volume = Math.min(playerVolume, standardMaximumPlayerVolume);
        applyVolumeGain();
        resumeVolumeAudio();
        if (playerVolume > minimumPlayerVolume) lastAudibleVolume = playerVolume;
        video.muted = playerVolume === minimumPlayerVolume;
      };
      const togglePlayerMute = () => {
        if (video.muted || playerVolume === minimumPlayerVolume) {
          if (playerVolume === minimumPlayerVolume) setPlayerVolume(lastAudibleVolume);
          video.muted = false;
          return;
        }
        lastAudibleVolume = playerVolume;
        video.muted = true;
      };
      const isTheatreMode = () => this.hasAttribute('data-player-theatre-mode');
      const updateControls = () => {
        const duration = playbackDuration();
        const position = playbackPosition();
        timeline.max = String(duration);
        timeline.value = String(position);
        timeline.disabled = duration === 0;
        timeline.style.setProperty(
          '--progress-percent', `${duration === 0 ? 0 : (position / duration) * 100}%`
        );
        updateBufferedIndicator(duration, position);
        currentTime.textContent = formatTime(position);
        remainingTime.textContent = `-${formatTime(Math.max(duration - position, 0))}`;
        updateActionPresentation('toggle', video.paused ? 'Play' : 'Pause', !video.paused);
        if (!video.muted && playerVolume > minimumPlayerVolume) lastAudibleVolume = playerVolume;
        const muted = video.muted || playerVolume === minimumPlayerVolume;
        updateActionPresentation('mute', muted ? 'Unmute' : 'Mute', muted);
        const theatreMode = isTheatreMode();
        updateActionPresentation(
          'theatre', theatreMode ? 'Exit theatre mode' : 'Theatre mode', theatreMode
        );
        actionButtons('theatre').forEach((button) => {
          button.setAttribute('aria-pressed', String(theatreMode));
        });
        const isFullscreen = document.fullscreenElement === this || document.fullscreenElement === video;
        updateActionPresentation('fullscreen', isFullscreen ? 'Exit fullscreen' : 'Fullscreen', isFullscreen);
        contextMenu.querySelectorAll('[data-player-rate]').forEach((option) => {
          const rate = Number(option.getAttribute('data-player-rate'));
          option.setAttribute('aria-pressed', String(Math.abs(rate - video.playbackRate) < 0.01));
        });
        const volumeLevel = muted ? minimumPlayerVolume : playerVolume;
        const volumeMaximum = maximumPlayerVolume();
        const volumePercent = (volumeLevel / volumeMaximum) * 100;
        const volumePercentage = Math.round(volumeLevel * 100);
        const volumeText = `${volumePercentage}%`;
        volumeControls.forEach((volumeControl) => {
          volumeControl.max = String(volumeMaximum);
          volumeControl.value = String(volumeLevel);
          volumeControl.setAttribute('aria-valuetext', volumeText);
          volumeControl.style.setProperty('--volume-percent', `${volumePercent}%`);
        });
        volumeValueLabels.forEach((label) => { label.textContent = volumeText; });
        updateTrackOptions();
      };
      const isCardFullscreen = () => document.fullscreenElement === this;
      const isPlayerFullscreen = () => (
        webkitFullscreenActive || document.fullscreenElement === this || document.fullscreenElement === video
      );
      const isFrameAlignment = (alignment) => (
        alignment === 'centred' || alignment === 'start' || alignment === 'end'
      );
      const fullscreenFrameAxis = () => {
        if (!isCardFullscreen() || video.videoWidth <= 0 || video.videoHeight <= 0) return null;
        const {height, width} = this.getBoundingClientRect();
        if (width <= 0 || height <= 0) return null;
        const frameAspectRatio = width / height;
        const videoAspectRatio = video.videoWidth / video.videoHeight;
        const minimumUnusedFrameSpaceRatio = 0.05;
        const unusedFrameSpaceRatio = Math.abs(frameAspectRatio - videoAspectRatio)
          / Math.max(frameAspectRatio, videoAspectRatio);
        if (unusedFrameSpaceRatio < minimumUnusedFrameSpaceRatio) return null;
        return frameAspectRatio > videoAspectRatio ? 'horizontal' : 'vertical';
      };
      const frameAlignmentLabel = (axis, alignment) => {
        if (alignment === 'centred') return 'Centred';
        if (axis === 'horizontal') return alignment === 'start' ? 'Left' : 'Right';
        return alignment === 'start' ? 'Top' : 'Bottom';
      };
      const resetFullscreenFrameAlignment = () => {
        selectedFrameAlignment = 'centred';
        activeFullscreenFrameAxis = null;
        fullscreenFrameAlignment.hidden = true;
        this.removeAttribute('data-player-frame-axis');
        this.removeAttribute('data-player-frame-alignment');
      };
      const synchroniseFullscreenFrameAlignment = () => {
        const frameAxis = fullscreenFrameAxis();
        if (frameAxis === null) {
          resetFullscreenFrameAlignment();
          return;
        }
        if (activeFullscreenFrameAxis !== frameAxis) selectedFrameAlignment = 'centred';
        activeFullscreenFrameAxis = frameAxis;
        this.setAttribute('data-player-frame-axis', frameAxis);
        this.setAttribute('data-player-frame-alignment', selectedFrameAlignment);
        frameAlignmentOptions.forEach((option) => {
          const alignment = option.getAttribute('data-player-frame-alignment-option');
          if (!isFrameAlignment(alignment)) return;
          const label = frameAlignmentLabel(frameAxis, alignment);
          option.setAttribute('aria-label', label);
          option.setAttribute('aria-pressed', String(alignment === selectedFrameAlignment));
        });
        fullscreenFrameAlignment.hidden = false;
      };
      const fullscreenClockFormatter = new Intl.DateTimeFormat(undefined, {
        hour: '2-digit',
        hourCycle: 'h23',
        minute: '2-digit',
        second: '2-digit'
      });
      const clearFullscreenClock = () => {
        if (fullscreenClockTimer !== null) window.clearInterval(fullscreenClockTimer);
        fullscreenClockTimer = null;
        fullscreenTime.textContent = '';
      };
      const startFullscreenClock = () => {
        fullscreenTime.textContent = fullscreenClockFormatter.format(new Date());
        if (fullscreenClockTimer !== null) return;
        fullscreenClockTimer = window.setInterval(() => {
          fullscreenTime.textContent = fullscreenClockFormatter.format(new Date());
        }, 1_000);
      };
      const synchroniseFullscreenClock = () => {
        if (isCardFullscreen()) startFullscreenClock();
        else clearFullscreenClock();
      };
      const clearPlayerControlsHideTimer = () => {
        if (playerControlsHideTimer !== null) window.clearTimeout(playerControlsHideTimer);
        playerControlsHideTimer = null;
      };
      const playerControlsCanHide = () => (
        !video.paused
        && playerPopupElements.every((menu) => menu.hidden)
        && playerTooltip.hidden
      );
      const showPlayerControls = () => {
        this.classList.remove('k-player--controls-hidden');
        clearPlayerControlsHideTimer();
        if (!playerControlsCanHide()) return;
        playerControlsHideTimer = window.setTimeout(() => {
          if (playerControlsCanHide()) this.classList.add('k-player--controls-hidden');
        }, 2600);
      };
      const showContextMenu = (clientX, clientY, trigger = null) => {
        const triggerBounds = trigger instanceof Element ? trigger.getBoundingClientRect() : null;
        if (!openPlayerPopup('menu', trigger, trigger !== null)) return;
        if (triggerBounds !== null) {
          positionPlayerPopup(contextMenu, triggerBounds);
          return;
        }
        const playerBounds = this.getBoundingClientRect();
        const menuBounds = contextMenu.getBoundingClientRect();
        const preferredTop = clientY - playerBounds.top;
        positionFloatingMenu(
          contextMenu,
          playerBounds,
          menuBounds,
          clientX,
          preferredTop,
          preferredTop - menuBounds.height - floatingMenuInset
        );
      };
      const toggleTheatreMode = () => {
        this.toggleAttribute('data-player-theatre-mode', !isTheatreMode());
      };
      const toggleFullscreen = async () => {
        try {
          const fullscreenElement = document.fullscreenElement;
          if (fullscreenElement === this || fullscreenElement === video) {
            await document.exitFullscreen();
          } else if (typeof this.requestFullscreen === 'function') {
            await this.requestFullscreen();
          } else if (typeof video.webkitEnterFullscreen === 'function') {
            video.controls = true;
            video.webkitEnterFullscreen();
          } else {
            status.textContent = 'Fullscreen is not available in this browser.';
          }
        } catch (_) {
          status.textContent = 'Could not enter fullscreen.';
        } finally {
          updateControls();
        }
      };
      const saveProgress = async (report) => {
        try {
          const response = await fetch(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/progress`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({
              positionSeconds: report.position,
              seek: report.seek,
              entryPosition: report.entryPosition,
            }),
          });
          if (!response.ok) throw new Error('Progress failed');
          if (report.entryPosition === entryPosition) lastReportedPosition = report.position;
        } catch (_) {
          if (report.entryPosition === entryPosition) {
            status.textContent = 'Playback progress could not be saved.';
          }
        }
      };
      const runProgressReports = async (initialReport) => {
        let report = initialReport;
        while (report !== null) {
          activeProgress = report;
          await saveProgress(report);
          activeProgress = null;
          report = pendingProgress;
          pendingProgress = null;
        }
      };
      const reportProgress = (force, seek) => {
        if (!entryPlaybackReady) return Promise.resolve();
        const position = playbackPosition();
        if (!Number.isFinite(position)) return Promise.resolve();
        if (resumePosition > 0 && !resumeApplied) return Promise.resolve();
        if (!force && position - lastReportedPosition < 10) return Promise.resolve();
        const report = {entryPosition, position, seek};
        if (reporting) {
          if (
            activeProgress !== null
            && activeProgress.entryPosition === report.entryPosition
            && activeProgress.position === report.position
            && (activeProgress.seek || !report.seek)
          ) {
            return progressReportPromise || Promise.resolve();
          }
          if (pendingProgress !== null && pendingProgress.entryPosition === report.entryPosition) {
            pendingProgress.position = report.position;
            pendingProgress.seek = pendingProgress.seek || report.seek;
          } else {
            pendingProgress = report;
          }
          return progressReportPromise || Promise.resolve();
        }
        reporting = true;
        progressReportPromise = runProgressReports(report).finally(() => {
          reporting = false;
          progressReportPromise = null;
        });
        return progressReportPromise;
      };
      const navigateToPendingItemPage = async () => {
        const nextUrl = pendingItemPageUrl;
        if (nextUrl === null) return;
        pendingItemPageUrl = null;
        window.location.assign(itemPageAutoplayUrl(nextUrl));
      };
      const flushProgressOnPageHide = () => {
        if (!entryPlaybackReady) return;
        const position = playbackPosition();
        if (!Number.isFinite(position)) return;
        if (resumePosition > 0 && !resumeApplied) return;
        void fetch(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/progress`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
          credentials: 'same-origin',
          keepalive: true,
          body: JSON.stringify({
            positionSeconds: position,
            seek: seeking || generatedStreamSeekPending,
            entryPosition,
          })
        });
      };
      const restartGeneratedStream = async (position) => {
        const seekEntryPosition = entryPosition;
        generatedStreamSeekPending = true;
        const autoplay = !video.paused;
        try {
          await selectDelivery(autoplay, position);
        } catch (_) {
          if (seekEntryPosition === entryPosition) {
            generatedStreamSeekPending = false;
            status.textContent = 'Could not seek this video.';
          }
        }
      };
      const reconnectPlaybackStream = async () => {
        if (activeStreamRecoveryId !== null) return;
        if (streamRecoveryAttemptCount >= 1) {
          status.textContent = 'Playback stream stopped. Reload this page to retry.';
          return;
        }
        const recoveryId = ++nextStreamRecoveryId;
        activeStreamRecoveryId = recoveryId;
        streamRecoveryAttemptCount += 1;
        const recoveryEntryPosition = entryPosition;
        try {
          const available = await selectDelivery(true, playbackPosition());
          if (available && recoveryEntryPosition === entryPosition) {
            status.textContent = 'Reconnecting playback…';
          }
        } catch (_) {
          if (recoveryEntryPosition === entryPosition) {
            status.textContent = 'Playback stream stopped. Reload this page to retry.';
          }
        } finally {
          if (activeStreamRecoveryId === recoveryId) activeStreamRecoveryId = null;
        }
      };
      const currentTrackSelection = () => ({
        audioStream: selectedAudioStream,
        subtitleBackdrop: subtitleBackground,
        subtitleFontScale: subtitleFontScalePercent,
        subtitleOffsetMilliseconds: subtitleTimingOffsetMilliseconds,
        subtitlePosition: subtitleVerticalPosition,
        subtitleTextShadow: subtitleShadow,
        subtitleTrack: selectedSubtitleTrack
      });
      const persistTrackSelection = async (selection, selectionEntryPosition) => {
        const response = await fetch(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/tracks`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({
            audioStream: selection.audioStream,
            entryPosition: selectionEntryPosition,
            subtitleBackground: selection.subtitleBackdrop,
            subtitleFontScalePercent: selection.subtitleFontScale,
            subtitleOffsetMilliseconds: selection.subtitleOffsetMilliseconds,
            subtitleShadow: selection.subtitleTextShadow,
            subtitleTrack: selection.subtitleTrack,
            subtitleVerticalPosition: selection.subtitlePosition
          })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !Number.isSafeInteger(payload.audioStream) || !Number.isSafeInteger(payload.subtitleOffsetMilliseconds) || !Number.isSafeInteger(payload.subtitleFontScalePercent) || typeof payload.subtitleBackground !== 'boolean' || typeof payload.subtitleShadow !== 'boolean' || !['author', 'top', 'middle', 'bottom'].includes(payload.subtitleVerticalPosition)) throw new Error('Track selection failed');
      };
      const flushTrackSelectionSave = () => {
        if (trackSelectionSaveTimer !== null) {
          window.clearTimeout(trackSelectionSaveTimer);
          trackSelectionSaveTimer = null;
        }
        const pending = pendingTrackSelectionSave;
        if (!pending) return;
        pendingTrackSelectionSave = null;
        if (pending.entryPosition !== entryPosition) {
          pending.resolve();
          return;
        }
        const save = async () => {
          try {
            await persistTrackSelection(pending.selection, pending.entryPosition);
            pending.resolve();
          } catch (_) {
            if (
              pending.entryPosition === entryPosition
              && pending.version === latestTrackSelectionSaveVersion
            ) {
              status.textContent = pending.failureMessage;
            }
            pending.reject(new Error(pending.failureMessage));
          }
        };
        trackSelectionSaveSequence = trackSelectionSaveSequence.then(save, save);
      };
      const queueTrackSelectionSave = (failureMessage, immediate = false) => new Promise((resolve, reject) => {
        const version = ++latestTrackSelectionSaveVersion;
        const selection = currentTrackSelection();
        const selectionEntryPosition = entryPosition;
        if (pendingTrackSelectionSave && pendingTrackSelectionSave.entryPosition === selectionEntryPosition) {
          pendingTrackSelectionSave.selection = selection;
          pendingTrackSelectionSave.failureMessage = failureMessage;
          pendingTrackSelectionSave.version = version;
          const previousResolve = pendingTrackSelectionSave.resolve;
          const previousReject = pendingTrackSelectionSave.reject;
          pendingTrackSelectionSave.resolve = () => {
            previousResolve();
            resolve();
          };
          pendingTrackSelectionSave.reject = (error) => {
            previousReject(error);
            reject(error);
          };
        } else {
          if (pendingTrackSelectionSave) flushTrackSelectionSave();
          pendingTrackSelectionSave = {
            entryPosition: selectionEntryPosition,
            failureMessage,
            reject,
            resolve,
            selection,
            version
          };
        }
        if (immediate) {
          flushTrackSelectionSave();
        } else if (trackSelectionSaveTimer === null) {
          trackSelectionSaveTimer = window.setTimeout(flushTrackSelectionSave, 150);
        }
      });
      const offerKestrelFallback = async () => {
        try {
          const response = await fetch(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/kestrel`, {
            method: 'POST',
            headers: {'Accept': 'application/json'},
            credentials: 'same-origin'
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok || typeof payload.fallbackUri !== 'string') throw new Error('Fallback failed');
          if (kestrelLink instanceof HTMLAnchorElement) {
            kestrelLink.href = payload.fallbackUri;
            kestrelLink.textContent = 'Open in Kestrel for this subtitle';
            kestrelLink.hidden = false;
          }
        } catch (_) {
          status.textContent = 'This subtitle needs Kestrel, but its fallback is unavailable.';
        }
      };
      const handlePlayerAction = (target) => {
        const action = target.getAttribute('data-player-action');
        if (action === 'overflow') {
          showMobileMenu(target);
          updateControls();
          return;
        }
        if (action === 'menu') {
          const bounds = target.getBoundingClientRect();
          showContextMenu(bounds.left + bounds.width / 2, bounds.bottom, target);
          updateControls();
          return;
        }
        if (action === 'audio') {
          showTrackMenu('audio', target);
          updateControls();
          return;
        }
        if (action === 'subtitles') {
          showTrackMenu('subtitles', target);
          updateControls();
          return;
        }
        dismissPlayerPopups(mobileMenu.contains(target));
        if (action === 'toggle') {
          if (video.paused) requestPlayback();
          else video.pause();
        } else if (action === 'rewind' || action === 'forward') {
          const offset = action === 'rewind' ? -10 : 10;
          if (deliveryMode !== 'direct') {
            void restartGeneratedStream(Math.min(Math.max(playbackPosition() + offset, 0), playbackDuration()));
            return;
          }
          if (Number.isFinite(video.duration)) video.currentTime = Math.min(Math.max(video.currentTime + offset, 0), video.duration);
        } else if (action === 'mute') {
          togglePlayerMute();
        } else if (action === 'next') {
          void completeAndAdvancePlayback();
        } else if (action === 'theatre') {
          toggleTheatreMode();
        } else if (action === 'fullscreen') {
          void toggleFullscreen();
        }
        updateControls();
      };
      const onPlayerControlClick = (event) => {
        showPlayerControls();
        const element = event.target instanceof Element ? event.target : null;
        const target = element?.closest('[data-player-action]');
        if (!target) return;
        handlePlayerAction(target);
      };
      controls.addEventListener('click', onPlayerControlClick);
      mobileMenu.addEventListener('click', onPlayerControlClick);
      frameToggle.addEventListener('click', onPlayerControlClick);
      const onFullscreenFrameAlignmentClick = (event) => {
        const element = event.target instanceof Element ? event.target : null;
        const option = element?.closest('[data-player-frame-alignment-option]');
        const alignment = option?.getAttribute('data-player-frame-alignment-option');
        if (!isFrameAlignment(alignment)) return;
        selectedFrameAlignment = alignment;
        synchroniseFullscreenFrameAlignment();
        showPlayerControls();
      };
      fullscreenFrameAlignment.addEventListener('click', onFullscreenFrameAlignmentClick);
      audioMenu.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null;
        const option = element?.closest('[data-player-audio-stream]');
        const audioStream = Number(option?.getAttribute('data-player-audio-stream'));
        if (!Number.isSafeInteger(audioStream) || audioStream < 0 || audioStream === selectedAudioStream) {
          dismissPlayerPopups(true);
          return;
        }
        const position = playbackPosition();
        const autoplay = !video.paused;
        dismissPlayerPopups(true);
        selectedAudioStream = audioStream;
        invalidateDeliveryRequests();
        updateTrackOptions();
        const selectionVersion = ++audioSelectionVersion;
        const selectionEntryPosition = entryPosition;
        void (async () => {
          try {
            await queueTrackSelectionSave('Audio track could not be changed.', true);
            if (
              selectionVersion !== audioSelectionVersion
              || selectionEntryPosition !== entryPosition
            ) return;
            await selectDelivery(autoplay, position);
          } catch (_) {
            if (selectionEntryPosition === entryPosition) {
              status.textContent = 'Audio track could not be changed.';
            }
          }
        })();
      });
      subtitleMenu.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null;
        const option = element?.closest('[data-player-subtitle-track]');
        if (!option) return;
        const subtitleTrack = option.getAttribute('data-player-subtitle-track') || null;
        if (subtitleTrack === selectedSubtitleTrack) {
          dismissPlayerPopups(true);
          return;
        }
        const unsupported = option.hasAttribute('data-player-subtitle-unsupported');
        dismissPlayerPopups(true);
        selectedSubtitleTrack = subtitleTrack;
        updateTrackOptions();
        void queueTrackSelectionSave('Subtitle track could not be changed.', true).catch(() => {});
        if (unsupported) {
          clearNativeSubtitle();
          disposeAssRenderer();
          status.textContent = 'This image subtitle needs Kestrel.';
          void offerKestrelFallback();
        } else {
          reloadSubtitles();
        }
      });
      subtitleMenu.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null;
        const timingOption = element?.closest('[data-player-subtitle-timing-step], [data-player-subtitle-timing-reset]');
        if (!timingOption || timingOption.hasAttribute('disabled')) return;
        const step = timingOption.hasAttribute('data-player-subtitle-timing-reset')
          ? -subtitleTimingOffsetMilliseconds
          : Number(timingOption.getAttribute('data-player-subtitle-timing-step'));
        if (!Number.isSafeInteger(step)) return;
        const nextOffset = Math.min(
          Math.max(subtitleTimingOffsetMilliseconds + step, -maxSubtitleTimingOffsetMilliseconds),
          maxSubtitleTimingOffsetMilliseconds
        );
        if (nextOffset === subtitleTimingOffsetMilliseconds) return;
        subtitleTimingOffsetMilliseconds = nextOffset;
        this.setAttribute('subtitle-timing-offset-milliseconds', String(subtitleTimingOffsetMilliseconds));
        updateTrackOptions();
        const selectedSubtitle = selectedSubtitleButton();
        if (selectedSubtitle?.getAttribute('data-player-subtitle-format') === 'ass') {
          reloadSubtitles();
        } else if (!applyNativeSubtitleTiming()) {
          reloadSubtitles();
        }
        void queueTrackSelectionSave('Subtitle timing could not be changed.').catch(() => {});
      });
      subtitleMenu.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null;
        const option = element?.closest('[data-player-subtitle-font-scale-step], [data-player-subtitle-background], [data-player-subtitle-shadow], [data-player-subtitle-position]');
        if (!option || option.hasAttribute('disabled')) return;
        const scaleStep = Number(option.getAttribute('data-player-subtitle-font-scale-step'));
        const nextFontScale = Number.isSafeInteger(scaleStep)
          ? Math.min(Math.max(subtitleFontScalePercent + scaleStep, minSubtitleFontScalePercent), maxSubtitleFontScalePercent)
          : subtitleFontScalePercent;
        const nextBackground = option.hasAttribute('data-player-subtitle-background') ? !subtitleBackground : subtitleBackground;
        const nextShadow = option.hasAttribute('data-player-subtitle-shadow') ? !subtitleShadow : subtitleShadow;
        const requestedPosition = option.getAttribute('data-player-subtitle-position');
        const nextPosition = ['author', 'top', 'middle', 'bottom'].includes(requestedPosition)
          ? requestedPosition
          : subtitleVerticalPosition;
        if (nextFontScale === subtitleFontScalePercent && nextBackground === subtitleBackground && nextShadow === subtitleShadow && nextPosition === subtitleVerticalPosition) return;
        subtitleFontScalePercent = nextFontScale;
        subtitleBackground = nextBackground;
        subtitleShadow = nextShadow;
        subtitleVerticalPosition = nextPosition;
        updateTrackOptions();
        applyNativeSubtitlePosition();
        void queueTrackSelectionSave('Subtitle appearance could not be changed.').catch(() => {});
      });
      contextMenu.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null;
        const option = element?.closest('[data-player-rate]');
        if (!option) return;
        const rate = Number(option.getAttribute('data-player-rate'));
        if (!Number.isFinite(rate)) return;
        video.playbackRate = rate;
        updateControls();
        dismissPlayerPopups(true);
      });
      const previewTimelineAtPointer = (event) => {
        const position = timelinePositionAt(event.clientX);
        if (position !== null) showTimelinePreview(position, event.clientX);
      };
      timeline.addEventListener('pointerenter', previewTimelineAtPointer);
      timeline.addEventListener('pointermove', previewTimelineAtPointer);
      timeline.addEventListener('pointerdown', (event) => {
        timelinePointerDown = true;
        previewTimelineAtPointer(event);
      });
      timeline.addEventListener('pointerup', () => {
        timelinePointerDown = false;
        hideTimelinePreview();
      });
      timeline.addEventListener('pointercancel', () => {
        timelinePointerDown = false;
        hideTimelinePreview();
      });
      timeline.addEventListener('pointerleave', () => {
        if (!timelinePointerDown) hideTimelinePreview();
      });
      timeline.addEventListener('input', () => {
        showPlayerControls();
        const position = Number(timeline.value);
        if (!Number.isFinite(position)) return;
        showTimelinePreview(position);
        if (deliveryMode !== 'direct') return;
        video.currentTime = position;
        updateControls();
      });
      timeline.addEventListener('change', () => {
        hideTimelinePreview();
        if (deliveryMode === 'direct') return;
        const position = Number(timeline.value);
        if (Number.isFinite(position)) void restartGeneratedStream(position);
      });
      const onVolumeInput = (volumeControl) => {
        showPlayerControls();
        const nextVolume = Number(volumeControl.value);
        if (!Number.isFinite(nextVolume)) return;
        setPlayerVolume(nextVolume);
        updateControls();
      };
      const volumeInputHandlers = volumeControls.map((volumeControl) => {
        const handler = () => { onVolumeInput(volumeControl); };
        volumeControl.addEventListener('input', handler);
        return [volumeControl, handler];
      });
      const setVolumeBoostEnabled = (enabled) => {
        volumeBoostEnabled = enabled;
        if (!volumeBoostEnabled && playerVolume > standardMaximumPlayerVolume) {
          setPlayerVolume(standardMaximumPlayerVolume);
        } else {
          applyVolumeGain();
        }
        resumeVolumeAudio();
        updateControls();
      };
      const onVolumeBoostChange = () => {
        const shouldEnableVolumeBoost = volumeBoostControl.checked;
        if (shouldEnableVolumeBoost && !initialiseVolumeBoost()) {
          volumeBoostControl.checked = false;
          setVolumeBoostEnabled(false);
          status.textContent = 'Volume boost is unavailable in this browser.';
        } else {
          setVolumeBoostEnabled(shouldEnableVolumeBoost);
        }
        dismissPlayerPopups(true);
      };
      volumeBoostControl.addEventListener('change', onVolumeBoostChange);
      const onVideoVolumeChange = () => {
        const nativeVolume = clampPlayerVolume(video.volume, standardMaximumPlayerVolume);
        const expectedNativeVolume = Math.min(playerVolume, standardMaximumPlayerVolume);
        if (Math.abs(nativeVolume - expectedNativeVolume) > playerVolumeComparisonTolerance) {
          playerVolume = nativeVolume;
          applyVolumeGain();
        }
        updateControls();
      };
      video.addEventListener('volumechange', onVideoVolumeChange);
      nativeControls.addEventListener('change', () => {
        video.controls = nativeControls.checked;
        dismissPlayerPopups(true);
      });
      if (autoplayNextControl instanceof HTMLInputElement) {
        autoplayNextControl.addEventListener('change', () => {
          if (!hasQueuedNextItem) {
            updateAutoplayNextControl();
            return;
          }
          autoplayNext = autoplayNextControl.checked;
          dismissPlayerPopups(true);
        });
      }
      const playerTooltipButton = (target, frameToggleIconOnly = false) => {
        const element = target instanceof Element ? target : null;
        const button = element?.closest('button');
        if (!(button instanceof Element)) return null;
        if (!frameToggleIconOnly || button !== frameToggle) return button;
        return frameToggleIcon()?.contains(element) ? button : null;
      };
      const onPlayerPointerOver = (event) => {
        const button = playerTooltipButton(event.target, true);
        if (button) showPlayerTooltip(button);
      };
      const onPlayerPointerOut = (event) => {
        const button = playerTooltipButton(event.target, true);
        if (button && playerTooltipButton(event.relatedTarget, true) !== button) hidePlayerTooltip();
      };
      const onPlayerFocusIn = (event) => {
        showPlayerControls();
        const button = playerTooltipButton(event.target);
        if (button) showPlayerTooltip(button);
      };
      const onPlayerFocusOut = (event) => {
        const button = playerTooltipButton(event.target);
        if (button && playerTooltipButton(event.relatedTarget) !== button) hidePlayerTooltip();
        const relatedPopupTrigger = playerPopupTriggerForTarget(event.relatedTarget);
        const popupFocusWasLost = (
          playerPopupContains(event.target)
          || playerPopupTriggerForTarget(event.target) !== null
        ) && !playerPopupContains(event.relatedTarget) && relatedPopupTrigger === null;
        if (popupFocusWasLost && visiblePlayerPopupAction() !== null) dismissPlayerPopups();
      };
      const onPlayerContextMenu = (event) => {
        event.preventDefault();
        showContextMenu(event.clientX, event.clientY);
      };
      const onPlayerKeyDown = (event) => {
        if (event.key === 'Escape' && dismissPlayerPopups(true)) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        showPlayerControls();
      };
      this.addEventListener('pointerover', onPlayerPointerOver);
      this.addEventListener('pointerout', onPlayerPointerOut);
      this.addEventListener('focusin', onPlayerFocusIn);
      this.addEventListener('focusout', onPlayerFocusOut);
      this.addEventListener('contextmenu', onPlayerContextMenu);
      this.addEventListener('pointerenter', showPlayerControls);
      this.addEventListener('pointermove', showPlayerControls);
      this.addEventListener('pointerdown', showPlayerControls);
      this.addEventListener('touchstart', showPlayerControls, {passive: true});
      this.addEventListener('keydown', onPlayerKeyDown);
      const onPointerDown = (event) => {
        if (playerPopupContains(event.target) || playerPopupTriggerForTarget(event.target)) return;
        dismissPlayerPopups();
      };
      const onWindowResize = () => {
        if (visiblePlayerPopupAction() !== null) dismissPlayerPopups(true);
        synchroniseFullscreenFrameAlignment();
        updateFrameToggleSize();
      };
      document.addEventListener('pointerdown', onPointerDown);
      this._dispose = () => {
        invalidatePlaybackAttempts();
        clearPlayerControlsHideTimer();
        clearFullscreenClock();
        document.removeEventListener('pointerdown', onPointerDown);
        document.removeEventListener('fullscreenchange', onFullscreenChange);
        this.removeEventListener('pointerover', onPlayerPointerOver);
        this.removeEventListener('pointerout', onPlayerPointerOut);
        this.removeEventListener('focusin', onPlayerFocusIn);
        this.removeEventListener('focusout', onPlayerFocusOut);
        this.removeEventListener('contextmenu', onPlayerContextMenu);
        this.removeEventListener('pointerenter', showPlayerControls);
        this.removeEventListener('pointermove', showPlayerControls);
        this.removeEventListener('pointerdown', showPlayerControls);
        this.removeEventListener('touchstart', showPlayerControls);
        this.removeEventListener('keydown', onPlayerKeyDown);
        controls.removeEventListener('click', onPlayerControlClick);
        mobileMenu.removeEventListener('click', onPlayerControlClick);
        frameToggle.removeEventListener('click', onPlayerControlClick);
        fullscreenFrameAlignment.removeEventListener('click', onFullscreenFrameAlignmentClick);
        volumeInputHandlers.forEach(([volumeControl, handler]) => {
          volumeControl.removeEventListener('input', handler);
        });
        volumeBoostControl.removeEventListener('change', onVolumeBoostChange);
        frameToggleResizeObserver?.disconnect();
        video.removeEventListener('volumechange', onVideoVolumeChange);
        video.removeEventListener('webkitbeginfullscreen', onWebkitBeginFullscreen);
        video.removeEventListener('webkitendfullscreen', onWebkitEndFullscreen);
        video.removeEventListener('resize', synchroniseFullscreenFrameAlignment);
        video.removeEventListener('resize', updateFrameToggleSize);
        if (queueNext instanceof Element) queueNext.removeEventListener('click', onQueueNext);
        window.removeEventListener('pagehide', flushProgressOnPageHide);
        window.removeEventListener('resize', onWindowResize);
        clearNativeSubtitle();
        disposeAssRenderer();
      };
      video.addEventListener('loadedmetadata', () => {
        if (mediaEntryPosition !== entryPosition) return;
        entryPlaybackReady = true;
        if (pendingDirectSeek !== null && deliveryMode === 'direct' && Number.isFinite(video.duration)) {
          resumeApplied = true;
          video.currentTime = Math.min(pendingDirectSeek, video.duration);
          pendingDirectSeek = null;
        } else if (!resumeApplied && resumePosition > 0) {
          if (deliveryMode === 'direct' && Number.isFinite(video.duration)) {
            resumeApplied = true;
            video.currentTime = Math.min(resumePosition, video.duration);
          } else if (deliveryMode !== 'direct') {
            resumeApplied = true;
          }
        }
        releaseVideoHeight();
        updateFrameToggleSize();
        status.textContent = '';
        updateControls();
        synchroniseFullscreenFrameAlignment();
        if (generatedStreamSeekPending) {
          generatedStreamSeekPending = false;
          void reportProgress(true, true);
        }
      });
      video.addEventListener('play', () => {
        clearSelectPlayStatus();
        resumeVolumeAudio();
        updateControls();
        showPlayerControls();
      });
      video.addEventListener('playing', () => { streamRecoveryAttemptCount = 0; });
      video.addEventListener('pause', () => {
        updateControls();
        showPlayerControls();
      });
      video.addEventListener('ratechange', updateControls);
      const onFullscreenChange = () => {
        const playerFullscreen = isPlayerFullscreen();
        updateControls();
        synchroniseFullscreenClock();
        synchroniseFullscreenFrameAlignment();
        if (playerFullscreen || wasPlayerFullscreen) showPlayerControls();
        wasPlayerFullscreen = playerFullscreen;
        if (!playerFullscreen) void navigateToPendingItemPage();
      };
      document.addEventListener('fullscreenchange', onFullscreenChange);
      const onWebkitBeginFullscreen = () => {
        webkitFullscreenActive = true;
        updateControls();
        synchroniseFullscreenFrameAlignment();
        showPlayerControls();
      };
      const onWebkitEndFullscreen = () => {
        webkitFullscreenActive = false;
        updateControls();
        synchroniseFullscreenFrameAlignment();
        showPlayerControls();
        void navigateToPendingItemPage();
      };
      video.addEventListener('webkitbeginfullscreen', onWebkitBeginFullscreen);
      video.addEventListener('webkitendfullscreen', onWebkitEndFullscreen);
      video.addEventListener('resize', synchroniseFullscreenFrameAlignment);
      video.addEventListener('resize', updateFrameToggleSize);
      video.addEventListener('timeupdate', () => {
        updateControls();
        void reportProgress(false, false);
      });
      video.addEventListener('progress', updateControls);
      video.addEventListener('seeking', () => { seeking = true; });
      video.addEventListener('seeked', () => {
        void reportProgress(true, seeking);
        seeking = false;
      });
      video.addEventListener('pause', () => { void reportProgress(true, seeking); });
      video.addEventListener('error', () => {
        if (activeStreamRecoveryId !== null) return;
        invalidatePlaybackAttempts();
        releaseVideoHeight();
        void reconnectPlaybackStream();
      });
      const postPlaybackCompletion = async (action) => {
        const response = await fetch(
          `/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/${action}`,
          {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({entryPosition}),
          }
        );
        if (!response.ok) throw new Error('Completion failed');
        return response.status === 204 ? null : response.json();
      };
      const completeAndAdvancePlayback = async () => {
        if (completing) return;
        completing = true;
        setQueueNextBusy(true);
        status.textContent = 'Completing playback…';
        try {
          const payload = await postPlaybackCompletion('complete');
          if (payload === null) throw new Error('Completion response is missing');
          const nextUrl = itemPageUrl(payload.nextUrl);
          if (nextUrl !== null && !isPlayerFullscreen()) {
            window.location.assign(itemPageAutoplayUrl(nextUrl));
            return;
          }
          if (payload.nextEntry !== null && payload.nextEntry !== undefined) {
            const nextEntry = parseNextEntry(payload.nextEntry);
            if (nextUrl === null) throw new Error('Playback item page is unavailable');
            updateFullscreenInfo(nextEntry);
            applyEntryTrackOptions(nextEntry);
            setAutoplayNextAvailability(updatePlaybackQueue());
            pendingItemPageUrl = nextUrl;
            synchroniseItemPageUrl(nextUrl);
            await loadEntry(
              nextEntry.position,
              nextEntry.savedResumePositionSeconds,
              nextEntry.durationSeconds,
              true
            );
            completing = false;
            setQueueNextBusy(false);
          } else status.textContent = 'Playback complete.';
        } catch (_) {
          completing = false;
          setQueueNextBusy(false);
          status.textContent = 'Playback completion could not be saved.';
        }
      };
      const completeCurrentPlayback = async () => {
        if (completing) return;
        completing = true;
        setQueueNextBusy(true);
        status.textContent = 'Completing playback…';
        try {
          await postPlaybackCompletion('complete-current');
          status.textContent = 'Playback complete. Select Play next to continue.';
        } catch (_) {
          status.textContent = 'Playback completion could not be saved.';
        } finally {
          completing = false;
          setQueueNextBusy(false);
        }
      };
      const onQueueNext = (event) => {
        event.preventDefault();
        event.stopPropagation();
        void completeAndAdvancePlayback();
      };
      if (queueNext instanceof Element) queueNext.addEventListener('click', onQueueNext);
      video.addEventListener('ended', () => {
        if (!hasQueuedNextItem || autoplayNext) {
          void completeAndAdvancePlayback();
        } else {
          void completeCurrentPlayback();
        }
      });
      window.addEventListener('pagehide', flushProgressOnPageHide);
      if (volumeBoostEnabled && !initialiseVolumeBoost()) {
        volumeBoostControl.checked = false;
        setVolumeBoostEnabled(false);
        status.textContent = 'Volume boost is unavailable in this browser.';
      } else {
        setVolumeBoostEnabled(volumeBoostEnabled);
      }
      showPlayerControls();
      updateFrameToggleSize();
      synchroniseFullscreenFrameAlignment();
      window.addEventListener('resize', onWindowResize);
      const shouldPlayOnLoad = playOnLoad || (resumePosition > 0 && autoplayOnResume);
      void loadEntry(entryPosition, resumePosition, catalogueDuration, shouldPlayOnLoad).catch(() => {
        status.textContent = 'Playback compatibility could not be checked.';
      });
    }

    disconnectedCallback() {
      if (this._dispose) this._dispose();
    }
  }

  if (!customElements.get('kanvas-playback-player')) customElements.define('kanvas-playback-player', KanvasPlaybackPlayer);

})();
