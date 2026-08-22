(() => {
  'use strict';

  const MAX_MOUNTED_POSTERS = 144;
  const GRID_ROW_TOP_TOLERANCE_PX = 1;
  const PROFILE_ACCENT_DEFAULT = '#e8e8e8';
  const PROFILE_PIN_MIN_LENGTH = 2;
  const PROFILE_PIN_MAX_LENGTH = 16;
  const PROFILE_SESSION_CHANNEL = 'kasana-profile-session';
  const profileSessionChannel = typeof window.BroadcastChannel === 'function'
    ? new window.BroadcastChannel(PROFILE_SESSION_CHANNEL)
    : null;

  const changeProfileSession = (destination) => {
    const message = {type: 'profile-session-changed', destination};
    profileSessionChannel?.postMessage(message);
    try {
      const nonce = window.crypto?.randomUUID?.() || String(Date.now());
      window.localStorage.setItem(PROFILE_SESSION_CHANNEL, JSON.stringify({...message, nonce}));
    } catch (_) {
      // BroadcastChannel remains available in browsers that restrict local storage.
    }
  };

  const receiveProfileSessionChange = (message) => {
    if (!message || message.type !== 'profile-session-changed') return;
    const destination = message.destination === '/profiles' ? '/profiles' : '/';
    window.location.replace(destination);
  };

  profileSessionChannel?.addEventListener('message', (event) => receiveProfileSessionChange(event.data));
  window.addEventListener('storage', (event) => {
    if (event.key !== PROFILE_SESSION_CHANNEL || !event.newValue) return;
    try {
      receiveProfileSessionChange(JSON.parse(event.newValue));
    } catch (_) {
      // Ignore unrelated malformed storage values.
    }
  });

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-kanvas-session-form')) return;
    const submitter = event.submitter;
    const submitterAction = submitter instanceof HTMLButtonElement ? submitter.getAttribute('formaction') : null;
    const action = new URL(submitterAction || form.action, window.location.origin);
    if (!['/profiles/select', '/profiles/bootstrap', '/profiles/sign-out'].includes(action.pathname)) return;
    event.preventDefault();
    const destination = action.pathname === '/profiles/sign-out' ? '/profiles' : '/';
    void fetch(action, {
      method: (submitter instanceof HTMLButtonElement && submitter.getAttribute('formmethod')) || form.method || 'POST',
      body: new FormData(form),
      credentials: 'same-origin',
    }).then((response) => {
      const finalUrl = new URL(response.url, window.location.origin);
      const succeeded = action.pathname === '/profiles/sign-out'
        ? finalUrl.pathname === '/profiles'
        : finalUrl.pathname === '/';
      if (succeeded) changeProfileSession(destination);
      window.location.assign(finalUrl);
    }).catch(() => HTMLFormElement.prototype.submit.call(form));
  });
  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);

  const POSTER_STATES = new Set([
    'normal', 'in_progress', 'watched', 'unavailable', 'selected', 'loading', 'missing_artwork'
  ]);

  const localArtworkUrl = (value) => typeof value === 'string' && /^\/kanvas\/artwork\/\d+\/\d+$/.test(value);
  const safeRequestId = (value) => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value) ? value : null;
  const safePosterId = (value) => value && typeof value === 'object' && Number.isSafeInteger(value.id) && value.id > 0 ? value.id : null;
  const jobDetail = (job, counters) => (job.status === 'failed' || job.status === 'interrupted')
    ? (job.failure || job.message || counters || '—')
    : (counters || job.message || job.failure || '—');
  const normalisePlaceholder = (value, title) => {
    const lines = value && typeof value === 'object' && Array.isArray(value.lines)
      ? value.lines
      : [title];
    const footer = value && typeof value === 'object' && typeof value.footer === 'string'
      ? value.footer.trim().slice(0, 80)
      : null;
    const safeLines = lines
      .filter((line) => typeof line === 'string' && line.trim())
      .slice(0, 3)
      .map((line) => line.trim().slice(0, 160));
    return {lines: safeLines.length ? safeLines : [title], footer: footer || null};
  };
  const normaliseHexColour = (value) => (
    typeof value === 'string' && /^#[0-9A-Fa-f]{6}$/.test(value) ? value : null
  );
  const normaliseLanguageTag = (value) => (
    typeof value === 'string' && /^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*$/.test(value)
      ? value.trim().toLowerCase()
      : null
  );
  const providerEntryUrl = (entry) => {
    const provider = typeof entry?.provider === 'string' ? entry.provider.trim().toLowerCase() : '';
    const providerId = typeof entry?.providerId === 'string'
      ? entry.providerId.trim()
      : typeof entry?.provider_id === 'string'
        ? entry.provider_id.trim()
        : '';
    const kind = typeof entry?.kind === 'string' ? entry.kind : '';
    if (provider === 'tmdb' && /^\d+$/.test(providerId)) {
      const section = kind === 'movie' ? 'movie' : kind === 'series' ? 'tv' : null;
      return section ? `https://www.themoviedb.org/${section}/${providerId}` : null;
    }
    if ((provider === 'imdb' || provider === 'omdb') && /^tt\d+$/i.test(providerId)) {
      return `https://www.imdb.com/title/${providerId}/`;
    }
    if (provider === 'tvmaze' && kind === 'series' && /^\d+$/.test(providerId)) {
      return `https://www.tvmaze.com/shows/${providerId}`;
    }
    return null;
  };
  const tmdbEntryReferenceFromUrl = (value, expectedKind) => {
    if (typeof value !== 'string' || !['movie', 'series'].includes(expectedKind)) return null;
    let url;
    try {
      url = new URL(value.trim());
    } catch {
      return null;
    }
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    const host = url.hostname.toLowerCase().replace(/^www\./, '');
    const segments = url.pathname.split('/').filter(Boolean);
    if (host === 'themoviedb.org') {
      const sectionIndex = segments.findIndex((segment) => segment === 'movie' || segment === 'tv');
      const section = segments[sectionIndex];
      const idMatch = segments[sectionIndex + 1]?.match(/^(\d+)(?:[-_].*)?$/);
      const kind = section === 'movie' ? 'movie' : section === 'tv' ? 'series' : null;
      if (!idMatch || kind !== expectedKind) return null;
      return {provider: 'tmdb', provider_id: idMatch[1], kind};
    }
    return null;
  };
  const providerDisplayName = (provider) => ({tmdb: 'TMDB', imdb: 'IMDb', omdb: 'OMDb', tvmaze: 'TVmaze'})[
    typeof provider === 'string' ? provider.toLowerCase() : ''
  ] || provider;
  const languageDisplayName = (tag) => {
    try {
      const locale = navigator.language || 'en';
      return new Intl.DisplayNames([locale], {type: 'language'}).of(tag) || tag;
    } catch {
      return tag;
    }
  };
  const profileMenuMarkup = (
    name,
    accentColour,
    preferredAudioLanguage,
    preferredSubtitleLanguage,
    defaultSubtitleFontScalePercent,
    defaultSubtitleBackground,
    defaultSubtitleShadow,
    autoplayOnResume
  ) => `
    <button type="button" class="k-profile-switcher" aria-haspopup="dialog" aria-label="Open profile settings" title="Profile settings">${escapeHtml(name)}</button>
    <dialog class="k-kanvas-dialog k-profile-dialog">
      <div class="k-picker k-profile-form" role="document">
        <div class="k-picker__header">
          <strong>Profile</strong>
          <button type="button" class="k-icon-action" data-profile-close aria-label="Close profile settings" title="Close">×</button>
        </div>
        <form data-profile-form data-kanvas-session-form>
          <div class="k-profile-tabs" role="tablist" aria-label="Profile settings">
            <button type="button" class="k-profile-tab" data-profile-tab="profile" role="tab" aria-selected="true">Profile</button>
            <button type="button" class="k-profile-tab" data-profile-tab="settings" role="tab" aria-selected="false">Settings</button>
          </div>
          <section data-profile-panel="profile" role="tabpanel">
            <label class="k-control-shell k-input-shell">
              <span class="k-sr-only">Profile name</span>
              <input class="k-input" name="displayName" value="${escapeHtml(name)}" aria-label="Profile name" placeholder="Profile name">
            </label>
            <div class="k-profile-pin-field">
              <label class="k-control-shell k-input-shell">
                <span class="k-sr-only">New PIN</span>
                <input class="k-input" name="pin" type="text" autocomplete="off" inputmode="numeric" minlength="${PROFILE_PIN_MIN_LENGTH}" maxlength="${PROFILE_PIN_MAX_LENGTH}" aria-label="New PIN" placeholder="New PIN">
              </label>
              <button type="button" class="k-button" data-profile-clear-pin>Clear PIN</button>
            </div>
            <label class="k-colour-field">
              <span>Accent colour</span>
              <input name="accentColour" type="color" value="${escapeHtml(accentColour)}" aria-label="Accent colour">
            </label>
          </section>
          <section data-profile-panel="settings" role="tabpanel" hidden>
            <h3 class="k-profile-section-heading">Playback languages</h3>
            <label class="k-control-shell k-select-wrap">
              <span class="k-sr-only">Preferred audio language</span>
              <select class="k-select" name="preferredAudioLanguage" aria-label="Preferred audio language" data-profile-language="audio"><option value="">Automatic audio</option></select>
            </label>
            <label class="k-control-shell k-select-wrap">
              <span class="k-sr-only">Preferred subtitle language</span>
              <select class="k-select" name="preferredSubtitleLanguage" aria-label="Preferred subtitle language" data-profile-language="subtitles"><option value="">Automatic subtitles</option><option value="none"${preferredSubtitleLanguage === 'none' ? ' selected' : ''}>No subtitles</option></select>
            </label>
            <h3 class="k-profile-section-heading">Subtitle defaults</h3>
            <label class="k-control-shell k-select-wrap">
              <span class="k-sr-only">Default subtitle font size</span>
              <select class="k-select" name="defaultSubtitleFontScalePercent" aria-label="Default subtitle font size">${[75, 100, 125, 150, 175, 200].map((value) => `<option value="${value}"${value === defaultSubtitleFontScalePercent ? ' selected' : ''}>${value}%</option>`).join('')}</select>
            </label>
            <label class="k-check"><input type="checkbox" name="defaultSubtitleBackground"${defaultSubtitleBackground ? ' checked' : ''}> Subtitle backdrop</label>
            <label class="k-check"><input type="checkbox" name="defaultSubtitleShadow"${defaultSubtitleShadow ? ' checked' : ''}> Subtitle shadow</label>
            <h3 class="k-profile-section-heading">Playback</h3>
            <label class="k-check"><input type="checkbox" name="autoplayOnResume"${autoplayOnResume ? ' checked' : ''}> Automatically play media when resuming</label>
          </section>
          <div class="k-picker__status" data-profile-status aria-live="polite"></div>
          <div class="k-profile-actions">
            <button type="submit" class="k-button k-button--primary" data-profile-save>Save changes</button>
            <button type="submit" class="k-button" formaction="/profiles/sign-out" formmethod="post" data-profile-logout>Log out</button>
          </div>
        </form>
      </div>
    </dialog>`;

  class KanvasProfileMenu extends HTMLElement {
    constructor() {
      super();
      this.dialog = null;
      this.status = null;
      this.saveButton = null;
      this.pinClearRequested = false;
      this.languageOptions = {audio: [], subtitles: []};
      this.languageOptionsLoaded = false;
      this.languageOptionsLoading = false;
    }

    connectedCallback() {
      const name = this.profileName();
      const accentColour = normaliseHexColour(this.getAttribute('data-accent-colour')) || PROFILE_ACCENT_DEFAULT;
      const preferredAudioLanguage = this.getAttribute('data-preferred-audio-language') || '';
      const preferredSubtitleLanguage = this.getAttribute('data-preferred-subtitle-language') || '';
      const defaultSubtitleFontScalePercent = Number(
        this.getAttribute('data-default-subtitle-font-scale-percent') || '100'
      );
      const defaultSubtitleBackground = this.getAttribute('data-default-subtitle-background') === 'true';
      const defaultSubtitleShadow = this.getAttribute('data-default-subtitle-shadow') === 'true';
      const autoplayOnResume = this.getAttribute('data-autoplay-on-resume') === 'true';
      this.innerHTML = profileMenuMarkup(
        name,
        accentColour,
        preferredAudioLanguage,
        preferredSubtitleLanguage,
        [75, 100, 125, 150, 175, 200].includes(defaultSubtitleFontScalePercent)
          ? defaultSubtitleFontScalePercent
          : 100,
        defaultSubtitleBackground,
        defaultSubtitleShadow,
        autoplayOnResume
      );
      this.dialog = this.querySelector('dialog');
      this.status = this.querySelector('[data-profile-status]');
      this.saveButton = this.querySelector('[data-profile-save]');
      this.renderLanguageOptions();
      const closeDialog = () => this.dialog?.close();
      this.querySelector('.k-profile-switcher')?.addEventListener('click', () => this.open());
      this.querySelector('[data-profile-close]')?.addEventListener('click', closeDialog);
      this.querySelector('[data-profile-form]')?.addEventListener('submit', (event) => this.handleSubmit(event));
      this.querySelector('[data-profile-clear-pin]')?.addEventListener('click', () => this.requestPinClear());
      this.querySelectorAll('[data-profile-tab]').forEach((tab) => {
        tab.addEventListener('click', () => this.selectTab(tab.dataset.profileTab));
      });
      this.querySelector('input[name="pin"]')?.addEventListener('input', () => this.cancelPinClearWhenReplacing());
      this.dialog?.addEventListener('click', (event) => this.closeFromBackdrop(event));
      this.dialog?.addEventListener('close', () => this.resetForm());
    }

    profileName() {
      const name = this.getAttribute('data-name');
      return typeof name === 'string' && name.trim() ? name.trim() : 'Profile';
    }

    open() {
      if (!this.dialog) return;
      if (!this.dialog.open) this.dialog.showModal();
      this.querySelector('input[name="displayName"]')?.focus();
      void this.loadLanguageOptions();
    }

    profileLanguagePreference(kind) {
      const attribute = kind === 'audio'
        ? 'data-preferred-audio-language'
        : 'data-preferred-subtitle-language';
      return normaliseLanguageTag(this.getAttribute(attribute));
    }

    renderLanguageOptions() {
      this.renderLanguageSelect('audio', 'preferredAudioLanguage', 'Automatic audio');
      this.renderLanguageSelect('subtitles', 'preferredSubtitleLanguage', 'Automatic subtitles');
    }

    renderLanguageSelect(kind, fieldName, automaticLabel) {
      const select = this.querySelector(`select[name="${fieldName}"]`);
      if (!(select instanceof HTMLSelectElement)) return;
      const selected = this.profileLanguagePreference(kind);
      const subtitlesDisabled = kind === 'subtitles' && selected === 'none';
      const tags = this.languageOptions[kind]
        .map(normaliseLanguageTag)
        .filter(Boolean);
      if (selected && !subtitlesDisabled) tags.push(selected);
      const choices = [...new Set(tags)].sort((left, right) => (
        languageDisplayName(left).localeCompare(languageDisplayName(right), navigator.language)
      ));
      const disabledOption = kind === 'subtitles' ? '<option value="none">No subtitles</option>' : '';
      select.innerHTML = `<option value="">${escapeHtml(automaticLabel)}</option>${disabledOption}${choices.map((tag) => `<option value="${escapeHtml(tag)}">${escapeHtml(languageDisplayName(tag))}</option>`).join('')}`;
      select.value = selected || '';
    }

    async loadLanguageOptions() {
      if (this.languageOptionsLoaded || this.languageOptionsLoading) return;
      this.languageOptionsLoading = true;
      try {
        const response = await fetch('/profiles/current/playback-languages', {
          headers: {'Accept': 'application/json'},
          credentials: 'same-origin'
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload || typeof payload !== 'object') return;
        const audio = Array.isArray(payload.audio) ? payload.audio : [];
        const subtitles = Array.isArray(payload.subtitles) ? payload.subtitles : [];
        this.languageOptions = {audio, subtitles};
        this.languageOptionsLoaded = true;
        this.renderLanguageOptions();
      } finally {
        this.languageOptionsLoading = false;
      }
    }

    selectTab(tabName) {
      const selected = tabName === 'settings' ? 'settings' : 'profile';
      this.querySelectorAll('[data-profile-tab]').forEach((tab) => {
        tab.setAttribute('aria-selected', String(tab.dataset.profileTab === selected));
      });
      this.querySelectorAll('[data-profile-panel]').forEach((panel) => {
        panel.hidden = panel.dataset.profilePanel !== selected;
      });
    }

    closeFromBackdrop(event) {
      if (event.target === this.dialog) this.dialog?.close();
    }

    handleSubmit(event) {
      const submitter = event.submitter;
      if (submitter instanceof HTMLButtonElement && submitter.hasAttribute('data-profile-logout')) return;
      this.save(event);
    }

    requestPinClear() {
      const pinInput = this.querySelector('input[name="pin"]');
      if (!(pinInput instanceof HTMLInputElement)) return;
      pinInput.value = '';
      this.pinClearRequested = true;
      this.setStatus('PIN will be cleared when you save changes.');
      pinInput.focus();
    }

    cancelPinClearWhenReplacing() {
      const pinInput = this.querySelector('input[name="pin"]');
      if (pinInput instanceof HTMLInputElement && pinInput.value.trim()) this.pinClearRequested = false;
    }

    resetForm() {
      const form = this.querySelector('[data-profile-form]');
      if (!(form instanceof HTMLFormElement)) return;
      form.reset();
      const nameInput = form.elements.namedItem('displayName');
      if (nameInput instanceof HTMLInputElement) nameInput.value = this.profileName();
      const accentInput = form.elements.namedItem('accentColour');
      if (accentInput instanceof HTMLInputElement) {
        accentInput.value = normaliseHexColour(this.getAttribute('data-accent-colour')) || PROFILE_ACCENT_DEFAULT;
      }
      this.renderLanguageOptions();
      const fontScaleInput = form.elements.namedItem('defaultSubtitleFontScalePercent');
      if (fontScaleInput instanceof HTMLSelectElement) {
        fontScaleInput.value = this.getAttribute('data-default-subtitle-font-scale-percent') || '100';
      }
      const backdropInput = form.elements.namedItem('defaultSubtitleBackground');
      if (backdropInput instanceof HTMLInputElement) {
        backdropInput.checked = this.getAttribute('data-default-subtitle-background') === 'true';
      }
      const shadowInput = form.elements.namedItem('defaultSubtitleShadow');
      if (shadowInput instanceof HTMLInputElement) {
        shadowInput.checked = this.getAttribute('data-default-subtitle-shadow') === 'true';
      }
      const autoplayOnResumeInput = form.elements.namedItem('autoplayOnResume');
      if (autoplayOnResumeInput instanceof HTMLInputElement) {
        autoplayOnResumeInput.checked = this.getAttribute('data-autoplay-on-resume') === 'true';
      }
      this.selectTab('profile');
      this.pinClearRequested = false;
      this.setStatus('');
    }

    setStatus(message, error = false) {
      if (!this.status) return;
      this.status.textContent = message;
      this.status.classList.toggle('k-picker__status--error', error);
    }

    async save(event) {
      event.preventDefault();
      const form = event.currentTarget;
      if (!(form instanceof HTMLFormElement)) return;
      const data = new FormData(form);
      const accentColour = normaliseHexColour(String(data.get('accentColour') || ''));
      const pin = String(data.get('pin') || '').trim();
      if (!accentColour) {
        this.setStatus('Choose a valid accent colour.', true);
        return;
      }
      if (pin && (pin.length < PROFILE_PIN_MIN_LENGTH || pin.length > PROFILE_PIN_MAX_LENGTH)) {
        this.setStatus(`PIN must be ${PROFILE_PIN_MIN_LENGTH}-${PROFILE_PIN_MAX_LENGTH} characters.`, true);
        return;
      }
      const profilePayload = {
        expectedUserId: Number(this.getAttribute('data-user-id')),
        displayName: String(data.get('displayName') || '').trim(),
        accent_colour: accentColour,
      };
      const preferredAudioLanguage = String(data.get('preferredAudioLanguage') || '').trim();
      const preferredSubtitleLanguage = String(data.get('preferredSubtitleLanguage') || '').trim();
      profilePayload.preferred_audio_language = preferredAudioLanguage || null;
      profilePayload.preferred_subtitle_language = preferredSubtitleLanguage || null;
      profilePayload.defaultSubtitleFontScalePercent = Number(
        data.get('defaultSubtitleFontScalePercent')
      );
      profilePayload.defaultSubtitleBackground = data.has('defaultSubtitleBackground');
      profilePayload.defaultSubtitleShadow = data.has('defaultSubtitleShadow');
      profilePayload.autoplayOnResume = data.has('autoplayOnResume');
      if (pin) profilePayload.pin = pin;
      else if (this.pinClearRequested) profilePayload.pin = null;
      this.saveButton?.setAttribute('disabled', 'disabled');
      this.setStatus('Saving...');
      try {
        const profile = await this.patchJson('/profiles/current', profilePayload);
        const nextName = profile.display_name || profile.username || profilePayload.displayName || this.profileName();
        this.setAttribute('data-name', nextName);
        const button = this.querySelector('.k-profile-switcher');
        if (button instanceof HTMLButtonElement) button.textContent = nextName;
        const savedColour = normaliseHexColour(profile.accent_colour) || accentColour;
        this.setAttribute('data-accent-colour', savedColour);
        this.setAttribute('data-preferred-audio-language', profile.preferred_audio_language || '');
        this.setAttribute('data-preferred-subtitle-language', profile.preferred_subtitle_language || '');
        this.renderLanguageOptions();
        this.setAttribute(
          'data-default-subtitle-font-scale-percent',
          String(profile.default_subtitle_font_scale_percent)
        );
        this.setAttribute(
          'data-default-subtitle-background', String(profile.default_subtitle_background)
        );
        this.setAttribute('data-default-subtitle-shadow', String(profile.default_subtitle_shadow));
        this.setAttribute('data-autoplay-on-resume', String(profile.autoplay_on_resume));
        document.documentElement.style.setProperty('--k-accent', savedColour);
        const pinInput = form.elements.namedItem('pin');
        if (pinInput instanceof HTMLInputElement) pinInput.value = '';
        this.pinClearRequested = false;
        this.setStatus('Saved.');
      } catch (error) {
        this.setStatus(error?.message || 'Changes could not be saved.', true);
      } finally {
        this.saveButton?.removeAttribute('disabled');
      }
    }

    async patchJson(url, payload) {
      const response = await fetch(url, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify(payload)
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Changes could not be saved.');
      return body;
    }
  }

  if (!customElements.get('kanvas-profile-menu')) customElements.define('kanvas-profile-menu', KanvasProfileMenu);

  class LibraryLoadError extends Error {
    constructor(category, {status = null, requestId = null, cause = null} = {}) {
      super(category);
      this.category = category;
      this.status = status;
      this.requestId = safeRequestId(requestId);
      this.cause = cause;
    }
  }

  const normalisePoster = (value) => {
    if (!value || typeof value !== 'object') return null;
    const poster = value;
    if (typeof poster.id !== 'number' || !Number.isSafeInteger(poster.id) || poster.id <= 0) return null;
    if (typeof poster.title !== 'string' || !poster.title) return null;
    if (typeof poster.href !== 'string' || !/^\/(?:item\/\d+|play\/item\/\d+\?resume=true&onDeck=true|play\/watch-orders\/\d+\?resume=true&onDeck=true)$/.test(poster.href)) return null;
    if (typeof poster.available !== 'boolean') return null;
    if (poster.posterUrl != null && !localArtworkUrl(poster.posterUrl)) return null;
    const placeholder = normalisePlaceholder(poster.placeholder, poster.title);
    if (poster.header != null && typeof poster.header !== 'string') return null;
    if (poster.subtitle != null && typeof poster.subtitle !== 'string') return null;
    if (poster.progressPercent != null && (!Number.isInteger(poster.progressPercent) || poster.progressPercent < 0 || poster.progressPercent > 100)) return null;
    if (typeof poster.state !== 'string' || !POSTER_STATES.has(poster.state)) return null;
    if (poster.watched != null && typeof poster.watched !== 'boolean') return null;
    if (poster.partiallyWatched != null && typeof poster.partiallyWatched !== 'boolean') return null;
    return {
      id: poster.id,
      title: poster.title,
      href: poster.href,
      posterUrl: poster.posterUrl ?? null,
      placeholder,
      header: poster.header?.trim() || null,
      subtitle: poster.subtitle ?? null,
      progressPercent: poster.progressPercent ?? null,
      state: poster.state,
      watched: poster.watched === true,
      partiallyWatched: poster.partiallyWatched === true,
      available: poster.available
    };
  };

  const posterMarkup = (poster) => {
    const progress = poster.progressPercent == null ? '' :
      `<span class="k-progress" aria-label="Playback progress"><span class="k-progress__value" style="--k-progress:${poster.progressPercent}%"></span></span>`;
    const placeholderLines = poster.placeholder.lines
      .map((line) => `<span class="k-poster__fallback-line">${escapeHtml(line)}</span>`)
      .join('');
    const placeholderFooter = poster.placeholder.footer
      ? `<span class="k-poster__fallback-footer">${escapeHtml(poster.placeholder.footer)}</span>`
      : '';
    const artwork = poster.posterUrl
      ? `<img class="k-poster__image" src="${escapeHtml(poster.posterUrl)}" alt="" loading="lazy" decoding="async">`
      : `<span class="k-poster__fallback" aria-hidden="true">${placeholderLines}${placeholderFooter}</span>`;
    const watched = poster.watched
      ? '<span class="k-poster__watched" role="img" aria-label="Watched"></span>'
      : poster.partiallyWatched
        ? '<span class="k-poster__watched k-poster__watched--partial" role="img" aria-label="Partially watched"></span>'
        : '';
    const header = poster.header ? `<span class="k-poster__header">${escapeHtml(poster.header)}</span>` : '';
    const subtitle = poster.subtitle ? `<span class="k-poster__subtitle">${escapeHtml(poster.subtitle)}</span>` : '';
    return `<a class="k-poster k-poster--${escapeHtml(poster.state)}" href="${escapeHtml(poster.href)}" aria-label="${escapeHtml(poster.title)}" title="${escapeHtml(poster.title)}" data-kanvas-poster="${poster.id}">
      <span class="k-poster__art">${artwork}${header}${progress}${watched}</span>
      <span class="k-poster__meta"><span class="k-poster__title">${escapeHtml(poster.title)}</span>${subtitle}</span>
    </a>`;
  };

  class KanvasPoster extends HTMLElement {
    static get observedAttributes() {
      return ['poster'];
    }

    connectedCallback() {
      this.render();
    }

    attributeChangedCallback() {
      if (this.isConnected) this.render();
    }

    set poster(value) {
      this.setAttribute('poster', typeof value === 'string' ? value : JSON.stringify(value));
    }

    render() {
      const rawPoster = this.getAttribute('poster');
      if (!rawPoster) {
        this.replaceChildren();
        return;
      }
      try {
        const poster = normalisePoster(JSON.parse(rawPoster));
        if (!poster) throw new TypeError('Invalid poster payload');
        this.innerHTML = posterMarkup(poster);
      } catch (_) {
        this.replaceChildren();
      }
    }
  }

  const posterElement = (value) => {
    const poster = normalisePoster(value);
    if (!poster) throw new TypeError('Invalid poster payload');
    const element = document.createElement('kanvas-poster');
    element.setAttribute('poster', JSON.stringify(poster));
    return element;
  };

  const gridColumnCount = (grid) => {
    const children = Array.from(grid.children);
    if (!children.length) return 1;
    const firstTop = children[0].getBoundingClientRect().top;
    const sameRow = children.findIndex((child) => Math.abs(child.getBoundingClientRect().top - firstTop) > GRID_ROW_TOP_TOLERANCE_PX);
    return Math.max(1, sameRow === -1 ? children.length : sameRow);
  };

  const trimOldestGridRows = (grid, maxMounted) => {
    const overflow = grid.children.length - maxMounted;
    if (overflow <= 0) return 0;
    const children = Array.from(grid.children);
    const columns = gridColumnCount(grid);
    const requestedCount = columns >= children.length ? overflow : Math.ceil(overflow / columns) * columns;
    const removeCount = Math.min(children.length - 1, requestedCount);
    const removed = children.slice(0, removeCount);
    if (!removed.length || removed.some((child) => child.contains(document.activeElement))) {
      return 0;
    }
    const anchor = children[removeCount] || null;
    const anchorTop = anchor?.getBoundingClientRect().top ?? null;
    for (const child of removed) child.remove();
    if (anchor && anchorTop !== null) window.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
    return removed.length;
  };

  const LIBRARY_GRID_SCHEMA_VERSION = 5;
  const LIBRARY_RESPONSE_SCHEMA_VERSION = 1;
  const libraryAssetVersion = () => {
    const scripts = Array.from(document.scripts);
    const script = scripts.find((candidate) => candidate.src.includes('/_kanvas/kanvas.js'));
    if (!script) return 'unversioned';
    return new URL(script.src, window.location.origin).searchParams.get('v') || 'unversioned';
  };

  const normalisedGridSource = (source) => {
    const url = new URL(source, window.location.origin);
    url.searchParams.delete('cursor');
    const entries = Array.from(url.searchParams.entries())
      .sort(([leftName, leftValue], [rightName, rightValue]) => leftName.localeCompare(rightName) || leftValue.localeCompare(rightValue));
    url.search = new URLSearchParams(entries).toString();
    return `${url.pathname}${url.search}`;
  };

  const libraryGridPayload = (payload) => {
    if (!payload || typeof payload !== 'object' || payload.schemaVersion !== LIBRARY_RESPONSE_SCHEMA_VERSION || !Array.isArray(payload.items)) {
      throw new LibraryLoadError('invalid_envelope');
    }
    if (payload.nextCursor != null && typeof payload.nextCursor !== 'string') {
      throw new LibraryLoadError('invalid_envelope');
    }
    const requestId = safeRequestId(payload.requestId);
    if (!requestId) throw new LibraryLoadError('invalid_envelope');
    const items = [];
    const invalidPosterIds = [];
    for (const item of payload.items) {
      const poster = normalisePoster(item);
      if (poster) items.push(poster);
      else invalidPosterIds.push(safePosterId(item));
    }
    return {items, invalidPosterIds, nextCursor: payload.nextCursor ?? null, requestId};
  };

  class KanvasPosterGrid extends HTMLElement {
    static get observedAttributes() {
      return ['source', 'catalogue-revision'];
    }

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
      this.posters = [];
      this.mountedStart = 0;
      this.requestController = null;
      this.generation = 0;
      this.requestId = null;
      this.invalidPosterCount = 0;
      this.retryRequired = false;
      this.hasSuccessfulPage = false;
      this.onPageHide = () => this.saveState();
    }

    connectedCallback() {
      this.initialise();
    }

    attributeChangedCallback(name, previous, current) {
      if ((name === 'source' || name === 'catalogue-revision') && this.isConnected && previous !== current) this.initialise();
    }

    disconnectedCallback() {
      this.generation += 1;
      this.requestController?.abort();
      this.requestController = null;
      this.observer?.disconnect();
      this.observer = null;
      window.removeEventListener('pagehide', this.onPageHide);
    }

    initialise() {
      this.generation += 1;
      this.requestController?.abort();
      this.requestController = null;
      this.observer?.disconnect();
      const source = this.getAttribute('source');
      this.cursor = null;
      this.done = false;
      this.loading = false;
      this.posters = [];
      this.mountedStart = 0;
      this.requestId = null;
      this.invalidPosterCount = 0;
      this.retryRequired = false;
      this.hasSuccessfulPage = false;
      this.stateKey = source ? this.buildStateKey(source) : null;
      this.innerHTML = '<div class="k-grid-status" aria-live="polite">Loading library…</div><div class="k-grid" aria-busy="true"></div><div class="k-grid-sentinel" aria-hidden="true"></div>';
      this.status = this.querySelector('.k-grid-status');
      this.grid = this.querySelector('.k-grid');
      this.sentinel = this.querySelector('.k-grid-sentinel');
      if (!source || !this.grid || !this.status || !this.sentinel) {
        if (this.status) this.status.textContent = 'The library grid could not be configured.';
        return;
      }
      this.observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) this.loadNext();
      }, {rootMargin: '640px 0px'});
      this.observer.observe(this.sentinel);
      window.removeEventListener('pagehide', this.onPageHide);
      window.addEventListener('pagehide', this.onPageHide);
      if (!this.restoreState()) this.loadNext();
    }

    buildStateKey(source) {
      const user = this.getAttribute('state-user') || 'anonymous';
      const catalogueRevision = this.catalogueRevision();
      return `kanvas:grid:v${LIBRARY_GRID_SCHEMA_VERSION}:asset=${libraryAssetVersion()}:catalogue=${encodeURIComponent(catalogueRevision)}:user=${encodeURIComponent(user)}:filters=${encodeURIComponent(normalisedGridSource(source))}`;
    }

    catalogueRevision() {
      return this.getAttribute('catalogue-revision') || 'unknown';
    }

    async loadNext({retry = false} = {}) {
      if (this.loading || this.done || this.retryRequired && !retry || !this.grid || !this.status) return;
      const source = this.getAttribute('source');
      if (!source) return;
      if (retry) this.retryRequired = false;
      const generation = this.generation;
      const controller = new AbortController();
      this.requestController?.abort();
      this.requestController = controller;
      this.loading = true;
      this.grid.setAttribute('aria-busy', 'true');
      this.status.textContent = this.posters.length ? 'Loading more…' : 'Loading library…';
      try {
        const url = new URL(source, window.location.origin);
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {
          headers: {'Accept': 'application/json'},
          credentials: 'same-origin',
          signal: controller.signal
        });
        const responseRequestId = safeRequestId(response.headers.get('X-Request-ID'));
        if (!response.ok) {
          throw await this.httpFailure(response, responseRequestId);
        }
        const contentType = response.headers.get('content-type') || '';
        if (!/^application\/json(?:\s*;|$)/i.test(contentType)) {
          throw new LibraryLoadError('invalid_content_type', {
            status: response.status,
            requestId: responseRequestId
          });
        }
        let documentPayload;
        try {
          documentPayload = await response.json();
        } catch (error) {
          throw new LibraryLoadError('invalid_json', {
            status: response.status,
            requestId: responseRequestId,
            cause: error
          });
        }
        let payload;
        try {
          payload = libraryGridPayload(documentPayload);
        } catch (error) {
          if (error instanceof LibraryLoadError) {
            error.status = response.status;
            error.requestId = error.requestId || responseRequestId;
            throw error;
          }
          throw new LibraryLoadError('invalid_envelope', {
            status: response.status,
            requestId: responseRequestId,
            cause: error
          });
        }
        if (generation !== this.generation) return;
        this.requestId = payload.requestId;
        if (payload.invalidPosterIds.length) this.reportInvalidPosters(payload.invalidPosterIds);
        this.invalidPosterCount += payload.invalidPosterIds.length;
        if (!payload.items.length && !this.posters.length && !payload.invalidPosterIds.length) {
          this.status.textContent = 'No items match these filters.';
        } else {
          try {
            const fragment = document.createDocumentFragment();
            for (const item of payload.items) fragment.append(posterElement(item));
            this.posters.push(...payload.items);
            this.grid.append(fragment);
            this.trimMountedPosters();
          } catch (error) {
            throw new LibraryLoadError('rendering_failure', {
              status: response.status,
              requestId: payload.requestId,
              cause: error
            });
          }
          this.status.textContent = this.pageStatus(payload.nextCursor);
        }
        this.cursor = payload.nextCursor;
        this.done = this.cursor === null;
        this.hasSuccessfulPage = true;
        this.retryRequired = false;
      } catch (error) {
        if (controller.signal.aborted || generation !== this.generation) return;
        const failure = error instanceof LibraryLoadError
          ? error
          : new LibraryLoadError('network_failure', {cause: error});
        this.requestId = failure.requestId || this.requestId;
        this.retryRequired = true;
        this.showFailure(failure);
        this.reportFailure(failure);
      } finally {
        if (generation !== this.generation) return;
        this.loading = false;
        this.requestController = null;
        this.grid?.setAttribute('aria-busy', 'false');
      }
    }

    async httpFailure(response, responseRequestId) {
      const contentType = response.headers.get('content-type') || '';
      let requestId = responseRequestId;
      if (/^application\/json(?:\s*;|$)/i.test(contentType)) {
        try {
          const body = await response.json();
          if (body && typeof body === 'object' && body.error && typeof body.error === 'object') {
            requestId = safeRequestId(body.error.requestId) || requestId;
          }
        } catch (error) {
          this.reportFailure(new LibraryLoadError('invalid_json', {
            status: response.status,
            requestId,
            cause: error
          }));
        }
      }
      return new LibraryLoadError('http_failure', {status: response.status, requestId});
    }

    pageStatus(nextCursor) {
      const invalid = this.invalidPosterCount
        ? `${this.invalidPosterCount} item${this.invalidPosterCount === 1 ? '' : 's'} could not be displayed.`
        : '';
      if (nextCursor !== null) return invalid;
      return invalid ? `${invalid} End of library.` : 'End of library.';
    }

    showFailure(failure) {
      if (!this.status) return;
      this.status.textContent = 'Could not load this part of the library.';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'k-button k-grid-retry';
      retry.textContent = 'Retry';
      retry.addEventListener('click', () => {
        retry.remove();
        this.loadNext({retry: true});
      }, {once: true});
      const diagnostic = document.createElement('details');
      diagnostic.className = 'k-grid-diagnostic';
      const summary = document.createElement('summary');
      summary.textContent = 'Details';
      const content = document.createElement('div');
      content.textContent = `Category: ${failure.category}\nHTTP status: ${failure.status ?? '—'}\nRequest ID: ${failure.requestId ?? '—'}`;
      diagnostic.append(summary, content);
      this.status.append(retry, diagnostic);
    }

    reportFailure(failure) {
      if (this.getAttribute('development-mode') === 'true') {
        console.error('Kanvas library load failed', {
          category: failure.category,
          status: failure.status,
          requestId: failure.requestId
        }, failure.cause || failure);
      }
    }

    reportInvalidPosters(itemIds) {
      if (this.getAttribute('development-mode') === 'true') {
        console.error('Kanvas library posters rejected', {itemIds: itemIds.filter((itemId) => itemId !== null)});
      }
    }

    trimMountedPosters() {
      if (!this.grid) return;
      this.mountedStart += trimOldestGridRows(this.grid, MAX_MOUNTED_POSTERS);
    }

    saveState() {
      if (!this.stateKey || !this.posters.length || !this.hasSuccessfulPage || this.retryRequired) {
        if (this.stateKey) sessionStorage.removeItem(this.stateKey);
        return;
      }
      sessionStorage.setItem(this.stateKey, JSON.stringify({
        schemaVersion: LIBRARY_GRID_SCHEMA_VERSION,
        asset: libraryAssetVersion(),
        catalogueRevision: this.catalogueRevision(),
        filters: normalisedGridSource(this.getAttribute('source') || ''),
        user: this.getAttribute('state-user') || 'anonymous',
        cursor: this.cursor,
        completed: this.done,
        outcome: 'success',
        posters: this.posters,
        scrollY: window.scrollY
      }));
    }

    restoreState() {
      if (!this.stateKey || !this.grid || !this.status) return false;
      const stored = sessionStorage.getItem(this.stateKey);
      if (!stored) return false;
      try {
        const state = JSON.parse(stored);
        const expectedFilters = normalisedGridSource(this.getAttribute('source') || '');
        if (
          state.schemaVersion !== LIBRARY_GRID_SCHEMA_VERSION ||
          state.asset !== libraryAssetVersion() ||
          state.catalogueRevision !== this.catalogueRevision() ||
          state.filters !== expectedFilters ||
          state.user !== (this.getAttribute('state-user') || 'anonymous') ||
          !Array.isArray(state.posters) ||
          !state.posters.length ||
          state.outcome !== 'success' ||
          typeof state.completed !== 'boolean' ||
          (state.cursor != null && typeof state.cursor !== 'string')
        ) throw new TypeError('Incompatible library grid state');
        const posters = state.posters.map(normalisePoster);
        if (posters.some((poster) => poster === null)) throw new TypeError('Invalid saved poster');
        this.posters = posters;
        this.mountedStart = Math.max(0, posters.length - MAX_MOUNTED_POSTERS);
        const mounted = document.createDocumentFragment();
        for (const poster of posters.slice(this.mountedStart)) mounted.append(posterElement(poster));
        this.grid.replaceChildren(mounted);
        this.cursor = state.cursor ?? null;
        this.done = state.completed;
        this.hasSuccessfulPage = true;
        this.retryRequired = false;
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

  if (!customElements.get('kanvas-poster')) customElements.define('kanvas-poster', KanvasPoster);
  if (!customElements.get('kanvas-poster-grid')) customElements.define('kanvas-poster-grid', KanvasPosterGrid);

  class KanvasOnboarding extends HTMLElement {
    connectedCallback() {
      const key = this.getAttribute('state-key') || 'default';
      const storageKey = `kanvas:onboarding:${key}`;
      if (sessionStorage.getItem(storageKey) === 'dismissed') {
        this.replaceChildren();
        return;
      }
      this.innerHTML = '<section class="k-onboarding" role="status"><div><strong>Artwork is not configured yet</strong><p>Your scanned library is ready to review. Configure TMDB, review scanner issues, then choose when to match and fetch artwork.</p><span class="k-action-row"><a class="k-button" href="/administration/hierarchy">Review scanner issues</a><a class="k-button" href="/administration/metadata">Configure TMDB</a><a class="k-button" href="/administration/artwork">Fetch artwork</a></span></div><button type="button" class="k-button" data-onboarding-dismiss>Dismiss</button></section>';
      this.querySelector('[data-onboarding-dismiss]')?.addEventListener('click', () => {
        sessionStorage.setItem(storageKey, 'dismissed');
        this.replaceChildren();
      });
    }
  }

  if (!customElements.get('kanvas-onboarding')) customElements.define('kanvas-onboarding', KanvasOnboarding);

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

  const normaliseCollection = (value) => {
    if (!value || typeof value !== 'object') return null;
    const collection = value;
    if (!Number.isSafeInteger(collection.id) || collection.id <= 0) return null;
    if (typeof collection.name !== 'string' || !collection.name) return null;
    if (!Number.isInteger(collection.itemCount) || collection.itemCount < 0) return null;
    if (!Number.isInteger(collection.watchOrderCount) || collection.watchOrderCount < 0) return null;
    if (collection.artworkUrl != null && !localArtworkUrl(collection.artworkUrl)) return null;
    const mosaic = Array.isArray(collection.mosaicUrls) ? collection.mosaicUrls : [];
    if (mosaic.length > 4 || mosaic.some((url) => !localArtworkUrl(url))) return null;
    return {
      id: collection.id,
      name: collection.name,
      itemCount: collection.itemCount,
      watchOrderCount: collection.watchOrderCount,
      artworkUrl: collection.artworkUrl ?? null,
      mosaicUrls: mosaic
    };
  };

  const collectionMarkup = (collection) => {
    const art = collection.artworkUrl
      ? `<img class="k-collection-art__image" src="${escapeHtml(collection.artworkUrl)}" alt="" loading="lazy" decoding="async">`
      : collection.mosaicUrls.length
        ? `<span class="k-poster-mosaic" aria-hidden="true">${collection.mosaicUrls.map((url) => `<img class="k-poster-mosaic__image" src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async">`).join('')}</span>`
        : `<span class="k-collection-art__fallback">${escapeHtml(collection.name.slice(0, 1).toUpperCase())}</span>`;
    return `<a class="k-collection-tile" href="/collections/${collection.id}" aria-label="${escapeHtml(collection.name)}" data-kanvas-collection="${collection.id}">
      <span class="k-collection-art">${art}</span>
      <span class="k-collection-tile__meta"><span class="k-collection-tile__title">${escapeHtml(collection.name)}</span><span class="k-collection-tile__facts">${collection.itemCount} items · ${collection.watchOrderCount} orders</span></span>
    </a>`;
  };

  class KanvasCollectionGrid extends HTMLElement {
    constructor() {
      super();
      this.cursor = null;
      this.loading = false;
      this.done = false;
      this.observer = null;
      this.grid = null;
      this.status = null;
      this.sentinel = null;
    }

    connectedCallback() {
      if (!this.getAttribute('source')) return;
      this.innerHTML = '<div class="k-grid-status" aria-live="polite"></div><div class="k-collection-grid" aria-busy="true"></div><div class="k-grid-sentinel" aria-hidden="true"></div>';
      this.status = this.querySelector('.k-grid-status');
      this.grid = this.querySelector('.k-collection-grid');
      this.sentinel = this.querySelector('.k-grid-sentinel');
      this.observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) this.loadNext();
      }, {rootMargin: '640px 0px'});
      this.observer.observe(this.sentinel);
      this.loadNext();
    }

    disconnectedCallback() { this.observer?.disconnect(); }

    async loadNext() {
      if (this.loading || this.done || !this.grid || !this.status) return;
      const source = this.getAttribute('source');
      if (!source) return;
      this.loading = true;
      this.grid.setAttribute('aria-busy', 'true');
      this.status.textContent = this.grid.children.length ? 'Loading more…' : 'Loading collections…';
      try {
        const url = new URL(source, window.location.origin);
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        if (!response.ok) throw new Error(`Collection request failed (${response.status})`);
        const payload = await response.json();
        const collections = Array.isArray(payload.items) ? payload.items.map(normaliseCollection).filter(Boolean) : [];
        if (!collections.length && !this.grid.children.length) {
          this.status.textContent = 'No collections match this search.';
        } else {
          this.grid.insertAdjacentHTML('beforeend', collections.map(collectionMarkup).join(''));
          this.trimMountedCollections();
          this.status.textContent = payload.nextCursor ? '' : 'End of collections.';
        }
        this.cursor = typeof payload.nextCursor === 'string' ? payload.nextCursor : null;
        this.done = this.cursor === null;
      } catch (_) {
        this.status.textContent = 'Could not load collections.';
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

    trimMountedCollections() {
      if (!this.grid) return;
      trimOldestGridRows(this.grid, MAX_MOUNTED_POSTERS);
    }
  }

  const normalisePickerItem = (value) => {
    if (!value || typeof value !== 'object') return null;
    const item = value;
    if (!Number.isSafeInteger(item.id) || item.id <= 0 || typeof item.title !== 'string' || !item.title) return null;
    if (typeof item.kind !== 'string' || typeof item.available !== 'boolean' || typeof item.alreadyMember !== 'boolean') return null;
    if (item.year != null && (!Number.isInteger(item.year) || item.year < 1)) return null;
    if (item.posterUrl != null && !localArtworkUrl(item.posterUrl)) return null;
    return item;
  };

  class KanvasItemPicker extends HTMLElement {
    constructor() {
      super();
      this.cursor = null;
      this.revision = Number(this.getAttribute('revision')) || 0;
      this.pendingIntent = null;
      this.searchTimer = null;
      this.dialog = null;
      this.results = null;
      this.status = null;
    }

    connectedCallback() {
      const label = this.getAttribute('label') || 'Add item';
      this.innerHTML = `<button type="button" class="k-button" aria-haspopup="dialog">${escapeHtml(label)}</button><dialog class="k-kanvas-dialog"><div class="k-picker" role="document"><div class="k-picker__header"><label class="k-control-shell k-input-shell"><span class="k-sr-only">Search library</span><input class="k-input" type="search" data-picker-search aria-label="Search library" placeholder="Search library"></label><button type="button" class="k-button" data-picker-close>Close</button></div><div class="k-picker__status" aria-live="polite"></div><div class="k-picker__results" role="list"></div><button type="button" class="k-button" data-picker-more>Load more</button><div class="k-conflict-state" hidden aria-live="assertive"></div></div></dialog>`;
      this.dialog = this.querySelector('dialog');
      this.results = this.querySelector('.k-picker__results');
      this.status = this.querySelector('.k-picker__status');
      const open = this.querySelector('button');
      const close = this.querySelector('[data-picker-close]');
      const search = this.querySelector('[data-picker-search]');
      const more = this.querySelector('[data-picker-more]');
      open?.addEventListener('click', () => this.open());
      close?.addEventListener('click', () => this.dialog?.close());
      search?.addEventListener('input', () => {
        window.clearTimeout(this.searchTimer);
        this.searchTimer = window.setTimeout(() => this.resetAndLoad(), 180);
      });
      more?.addEventListener('click', () => this.loadNext());
      this.results?.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target.closest('[data-picker-add]') : null;
        if (target instanceof HTMLButtonElement) this.addItem(Number(target.dataset.pickerAdd));
      });
      window.kanvas = window.kanvas || {};
      window.kanvas.openPicker = () => this.open();
    }

    open() {
      if (!this.dialog) return;
      if (!this.dialog.open) this.dialog.showModal();
      this.resetAndLoad();
      this.querySelector('[data-picker-search]')?.focus();
    }

    resetAndLoad() {
      this.cursor = null;
      if (this.results) this.results.replaceChildren();
      this.loadNext();
    }

    async loadNext() {
      const source = this.getAttribute('source');
      if (!source || !this.results || !this.status) return;
      this.status.textContent = 'Loading items…';
      try {
        const url = new URL(source, window.location.origin);
        const search = this.querySelector('[data-picker-search]');
        if (search instanceof HTMLInputElement && search.value.trim()) url.searchParams.set('search', search.value.trim());
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        if (!response.ok) throw new Error('Picker request failed');
        const payload = await response.json();
        const items = Array.isArray(payload.items) ? payload.items.map(normalisePickerItem).filter(Boolean) : [];
        const fragment = document.createDocumentFragment();
        for (const item of items) {
          const row = document.createElement('div');
          row.className = 'k-picker-row';
          row.setAttribute('role', 'listitem');
          const year = item.year ? ` · ${item.year}` : '';
          const availability = item.available ? '' : ' · unavailable';
          row.innerHTML = `<span class="k-picker-row__title">${escapeHtml(item.title)}</span><span class="k-picker-row__facts">${escapeHtml(item.kind)}${year}${availability}</span><button type="button" class="k-button" data-picker-add="${item.id}" ${item.alreadyMember ? 'disabled aria-disabled="true"' : ''}>${item.alreadyMember ? 'Added' : 'Add'}</button>`;
          fragment.append(row);
        }
        this.results.append(fragment);
        this.cursor = typeof payload.nextCursor === 'string' ? payload.nextCursor : null;
        this.status.textContent = items.length ? '' : 'No matching library items.';
        const more = this.querySelector('[data-picker-more]');
        if (more instanceof HTMLButtonElement) more.hidden = this.cursor === null;
      } catch (_) {
        this.status.textContent = 'Could not load library items.';
      }
    }

    async addItem(itemId) {
      if (!Number.isSafeInteger(itemId) || itemId <= 0) return;
      const intent = {operation: 'add', itemId, revision: this.revision};
      const success = await this.mutate(intent);
      if (success) window.location.reload();
    }

    async mutate(intent) {
      const action = this.getAttribute('action');
      if (!action || !this.status) return false;
      this.status.textContent = 'Saving…';
      try {
        const response = await fetch(action, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify(intent)});
        const payload = await response.json();
        if (response.status === 409) {
          this.showConflict(payload, intent);
          return false;
        }
        if (!response.ok || !Number.isInteger(payload.revision)) throw new Error(payload.error || 'Action failed');
        this.revision = payload.revision;
        this.status.textContent = '';
        return true;
      } catch (_) {
        this.status.textContent = 'Could not save this change.';
        return false;
      }
    }

    showConflict(payload, intent) {
      this.pendingIntent = intent;
      const state = this.querySelector('.k-conflict-state');
      if (!state) return;
      const revision = Number.isInteger(payload.currentRevision) ? payload.currentRevision : null;
      state.hidden = false;
      state.innerHTML = '<span>This collection changed elsewhere.</span><button type="button" class="k-button" data-conflict-reload>Reload</button><button type="button" class="k-button" data-conflict-reapply>Reapply</button>';
      state.querySelector('[data-conflict-reload]')?.addEventListener('click', () => window.location.reload());
      state.querySelector('[data-conflict-reapply]')?.addEventListener('click', async () => {
        if (!this.pendingIntent || revision === null) return;
        const replay = {...this.pendingIntent, revision};
        if (await this.mutate(replay)) window.location.reload();
      });
    }
  }

  const normaliseWatchRow = (value) => {
    if (!value || typeof value !== 'object') return null;
    const row = value;
    if (!Number.isSafeInteger(row.id) || row.id <= 0 || !Number.isSafeInteger(row.itemId) || row.itemId <= 0) return null;
    if (!Number.isInteger(row.position) || row.position < 0 || typeof row.title !== 'string' || !row.title) return null;
    if (typeof row.kind !== 'string' || typeof row.available !== 'boolean') return null;
    if (row.year != null && (!Number.isInteger(row.year) || row.year < 1)) return null;
    if (row.posterUrl != null && !localArtworkUrl(row.posterUrl)) return null;
    const fallbackPoster = {
      id: row.itemId,
      title: row.title,
      href: `/item/${row.itemId}`,
      posterUrl: row.posterUrl ?? null,
      placeholder: {lines: [row.title], footer: row.kind},
      subtitle: [row.year, row.kind].filter(Boolean).join(' · ') || null,
      state: row.available ? (row.posterUrl ? 'normal' : 'missing_artwork') : 'unavailable',
      available: row.available
    };
    const poster = normalisePoster(row.poster) || normalisePoster(fallbackPoster);
    return poster ? {...row, poster} : null;
  };

  class KanvasWatchOrderList extends HTMLElement {
    constructor() {
      super();
      this.cursor = null;
      this.revision = Number(this.getAttribute('revision')) || 0;
      this.loading = false;
      this.done = false;
      this.list = null;
      this.status = null;
      this.pendingIntent = null;
      this.draggedId = null;
    }

    connectedCallback() {
      this.innerHTML = '<div class="k-watch-list-status" aria-live="polite"></div><div class="k-watch-order-list" role="list" aria-label="Watch order"></div><button type="button" class="k-button k-watch-list-more">Load more</button><div class="k-conflict-state" hidden aria-live="assertive"></div>';
      this.list = this.querySelector('.k-watch-order-list');
      this.status = this.querySelector('.k-watch-list-status');
      this.querySelector('.k-watch-list-more')?.addEventListener('click', () => this.loadNext());
      this.list?.addEventListener('click', (event) => this.onClick(event));
      this.list?.addEventListener('keydown', (event) => this.onKeydown(event));
      this.list?.addEventListener('dragstart', (event) => this.onDragStart(event));
      this.list?.addEventListener('dragover', (event) => event.preventDefault());
      this.list?.addEventListener('drop', (event) => this.onDrop(event));
      this.loadNext();
    }

    async loadNext() {
      const source = this.getAttribute('source');
      if (!source || !this.list || !this.status || this.loading || this.done) return;
      this.loading = true;
      this.status.textContent = this.list.children.length ? 'Loading more…' : 'Loading entries…';
      try {
        const url = new URL(source, window.location.origin);
        if (this.cursor) url.searchParams.set('cursor', this.cursor);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        if (!response.ok) throw new Error('Watch order request failed');
        const payload = await response.json();
        const rows = Array.isArray(payload.items) ? payload.items.map(normaliseWatchRow).filter(Boolean) : [];
        if (Number.isInteger(payload.revision)) this.revision = payload.revision;
        this.list.insertAdjacentHTML('beforeend', rows.map((row) => this.rowMarkup(row)).join(''));
        this.trimRows();
        this.cursor = typeof payload.nextCursor === 'string' ? payload.nextCursor : null;
        this.done = this.cursor === null;
        this.status.textContent = rows.length ? '' : 'This watch order is empty.';
        const more = this.querySelector('.k-watch-list-more');
        if (more instanceof HTMLButtonElement) more.hidden = this.done;
      } catch (_) {
        this.status.textContent = 'Could not load watch-order entries.';
      } finally {
        this.loading = false;
      }
    }

    rowMarkup(row) {
      const year = row.year ? ` · ${row.year}` : '';
      const unavailable = row.available ? '' : '<span class="k-watch-row__warning">Unavailable</span>';
      return `<div class="k-watch-row" role="listitem" tabindex="0" draggable="true" data-entry-id="${row.id}" data-item-id="${row.itemId}"><span class="k-watch-row__position">${row.position + 1}</span><a class="k-watch-row__detail" href="/item/${row.itemId}"><span class="k-watch-row__title">${escapeHtml(row.title)}</span><span class="k-watch-row__facts">${escapeHtml(row.kind)}${year}</span></a>${unavailable}<span class="k-watch-row__actions"><button type="button" class="k-row-button" data-row-action="up" aria-label="Move entry up">↑</button><button type="button" class="k-row-button" data-row-action="down" aria-label="Move entry down">↓</button><button type="button" class="k-row-button" data-row-action="start" aria-label="Move entry to start">⇤</button><button type="button" class="k-row-button" data-row-action="end" aria-label="Move entry to end">⇥</button><button type="button" class="k-row-button" data-row-action="play" aria-label="Play from here">▶</button><button type="button" class="k-row-button" data-row-action="remove" aria-label="Remove entry">×</button></span></div>`;
    }

    trimRows() {
      if (!this.list) return;
      while (this.list.children.length > 120) {
        const first = this.list.firstElementChild;
        if (!first || first.contains(document.activeElement)) return;
        first.remove();
      }
    }

    onClick(event) {
      const target = event.target instanceof Element ? event.target.closest('[data-row-action]') : null;
      if (!(target instanceof HTMLButtonElement)) return;
      const row = target.closest('.k-watch-row');
      if (!(row instanceof HTMLElement)) return;
      const action = target.dataset.rowAction;
      if (action === 'up') this.moveRelative(row, -1);
      if (action === 'down') this.moveRelative(row, 1);
      if (action === 'start' || action === 'end') this.moveBoundary(row, action);
      if (action === 'remove') this.removeRow(row);
      if (action === 'play') this.playFromHere(row);
    }

    onKeydown(event) {
      const target = event.target;
      const row = target instanceof Element ? target.closest('.k-watch-row') : null;
      if (!(row instanceof HTMLElement) || target instanceof HTMLButtonElement) return;
      if (event.key === 'ArrowUp') { event.preventDefault(); this.moveRelative(row, -1); }
      if (event.key === 'ArrowDown') { event.preventDefault(); this.moveRelative(row, 1); }
      if (event.key === 'Home') { event.preventDefault(); this.moveBoundary(row, 'start'); }
      if (event.key === 'End') { event.preventDefault(); this.moveBoundary(row, 'end'); }
      if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); this.removeRow(row); }
      if (event.key === 'Enter') { event.preventDefault(); window.location.assign(`/item/${row.dataset.itemId}`); }
    }

    onDragStart(event) {
      const row = event.target instanceof Element ? event.target.closest('.k-watch-row') : null;
      if (!(row instanceof HTMLElement)) return;
      this.draggedId = row.dataset.entryId || null;
      event.dataTransfer?.setData('text/plain', this.draggedId || '');
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
    }

    onDrop(event) {
      event.preventDefault();
      const target = event.target instanceof Element ? event.target.closest('.k-watch-row') : null;
      if (!(target instanceof HTMLElement) || !this.draggedId || !this.list) return;
      const source = this.list.querySelector(`[data-entry-id="${CSS.escape(this.draggedId)}"]`);
      if (!(source instanceof HTMLElement) || source === target) return;
      const rows = Array.from(this.list.children);
      const targetIndex = rows.indexOf(target);
      if (targetIndex >= 0) this.moveToIndex(source, targetIndex);
      this.draggedId = null;
    }

    moveRelative(row, offset) {
      if (!this.list) return;
      const rows = Array.from(this.list.children);
      const index = rows.indexOf(row);
      const targetIndex = index + offset;
      if (index < 0 || targetIndex < 0 || targetIndex >= rows.length) return;
      this.moveToIndex(row, targetIndex);
    }

    async moveToIndex(row, targetIndex) {
      if (!this.list) return;
      const previousRows = Array.from(this.list.children);
      const sourceIndex = previousRows.indexOf(row);
      if (sourceIndex < 0 || sourceIndex === targetIndex) return;
      const reordered = [...previousRows];
      reordered.splice(sourceIndex, 1);
      reordered.splice(targetIndex, 0, row);
      this.list.replaceChildren(...reordered);
      const before = reordered[targetIndex + 1];
      const intent = {operation: 'move', entryId: Number(row.dataset.entryId), beforeEntryId: before ? Number(before.dataset.entryId) : null, afterEntryId: null, revision: this.revision};
      const success = await this.mutate(intent);
      if (!success && !this.pendingIntent) this.list.replaceChildren(...previousRows);
    }

    async moveBoundary(row, boundary) {
      const intent = {operation: 'move', entryId: Number(row.dataset.entryId), boundary, revision: this.revision};
      const success = await this.mutate(intent);
      if (success) window.location.reload();
    }

    async removeRow(row) {
      if (!this.list) return;
      const previousSibling = row.previousElementSibling;
      const nextSibling = row.nextElementSibling;
      row.remove();
      const success = await this.mutate({operation: 'remove', entryId: Number(row.dataset.entryId), revision: this.revision});
      if (!success && !this.pendingIntent) {
        if (nextSibling) this.list.insertBefore(row, nextSibling);
        else if (previousSibling) previousSibling.after(row);
        else this.list.append(row);
      }
    }

    async playFromHere(row) {
      const action = this.getAttribute('launch-action');
      if (!action || !this.status) return;
      if (row.querySelector('.k-watch-row__warning')) {
        this.status.textContent = 'This entry is unavailable. Use Play available entries to skip it.';
        return;
      }
      this.status.textContent = 'Opening player…';
      try {
        const response = await fetch(action, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify({itemId: Number(row.dataset.itemId)})});
        const payload = await response.json();
        if (!response.ok || typeof payload.playbackUrl !== 'string' || !payload.playbackUrl.startsWith('/play/watch-orders/')) throw new Error('Launch failed');
        window.location.assign(payload.playbackUrl);
      } catch (_) {
        this.status.textContent = 'Could not start browser playback.';
      }
    }

    async mutate(intent) {
      const action = this.getAttribute('action');
      if (!action || !this.status) return false;
      this.setAttribute('aria-busy', 'true');
      this.status.textContent = 'Saving change…';
      try {
        const response = await fetch(action, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify(intent)});
        const payload = await response.json();
        if (response.status === 409) {
          this.showConflict(payload, intent);
          return false;
        }
        if (!response.ok || !Number.isInteger(payload.revision)) throw new Error(payload.error || 'Action failed');
        this.revision = payload.revision;
        this.status.textContent = '';
        return true;
      } catch (_) {
        this.status.textContent = 'Could not save this change.';
        return false;
      } finally {
        this.removeAttribute('aria-busy');
      }
    }

    showConflict(payload, intent) {
      this.pendingIntent = intent;
      const state = this.querySelector('.k-conflict-state');
      if (!state) return;
      const revision = Number.isInteger(payload.currentRevision) ? payload.currentRevision : null;
      state.hidden = false;
      state.innerHTML = '<span>This watch order changed elsewhere. Your local operation is still ready.</span><button type="button" class="k-button" data-conflict-reload>Reload</button><button type="button" class="k-button" data-conflict-reapply>Reapply</button>';
      state.querySelector('[data-conflict-reload]')?.addEventListener('click', () => window.location.reload());
      state.querySelector('[data-conflict-reapply]')?.addEventListener('click', async () => {
        if (!this.pendingIntent || revision === null) return;
        const replay = {...this.pendingIntent, revision};
        if (await this.mutate(replay)) window.location.reload();
      });
    }
  }

  const normaliseWatchSource = (value) => {
    if (!value || typeof value !== 'object') return null;
    const source = value;
    if (!Number.isSafeInteger(source.id) || source.id <= 0 || typeof source.title !== 'string' || !source.title) return null;
    if (typeof source.kind !== 'string' || !Number.isInteger(source.entryCount) || source.entryCount < 0 || typeof source.addable !== 'boolean' || typeof source.available !== 'boolean') return null;
    if (source.year != null && (!Number.isInteger(source.year) || source.year < 1)) return null;
    if (source.seriesTitle != null && typeof source.seriesTitle !== 'string') return null;
    if (source.seasonNumber != null && (!Number.isInteger(source.seasonNumber) || source.seasonNumber < 0)) return null;
    const poster = normalisePoster(source.poster);
    return poster ? {...source, poster} : null;
  };

  class KanvasWatchOrderWorkspace extends HTMLElement {
    constructor() {
      super();
      this.revision = Number(this.getAttribute('revision')) || 0;
      this.entries = [];
      this.sources = [];
      this.selectedSourceIds = new Set();
      this.status = null;
      this.order = null;
      this.pool = null;
      this.pendingIntent = null;
      this.activeSlot = null;
      this.isDragging = false;
      this.dragScrollDirection = 0;
      this.dragScrollFrame = null;
      this.windowWheelListener = (event) => this.onOrderWheel(event);
    }

    connectedCallback() {
      this.innerHTML = '<section class="k-watch-workspace" aria-label="Watch-order editor"><div class="k-watch-list-status" aria-live="polite"></div><section><div class="k-watch-workspace__heading"><h2 class="k-section-title">Play order</h2><span class="k-watch-workspace__hint">The leftmost poster plays first. Drag posters onto the spaces between them; shows and seasons stay together.</span></div><div class="k-watch-workspace__dropzone" data-order-dropzone><div class="k-watch-order-list k-watch-workspace__order" role="list" aria-label="Play order, leftmost plays first"></div></div></section><section class="k-watch-workspace__sources"><div class="k-watch-workspace__heading"><h2 class="k-section-title">Collection items</h2><label class="k-control-shell k-input-shell"><span class="k-sr-only">Filter collection items</span><input class="k-input" type="search" placeholder="Filter movies, shows, seasons, episodes" aria-label="Filter collection items" data-source-filter></label></div><div class="k-watch-workspace__pool" role="list" aria-label="Available collection items"></div></section><div class="k-conflict-state" hidden aria-live="assertive"></div></section>';
      this.status = this.querySelector('.k-watch-list-status');
      this.order = this.querySelector('.k-watch-workspace__order');
      this.pool = this.querySelector('.k-watch-workspace__pool');
      this.order?.addEventListener('click', (event) => this.onOrderClick(event));
      this.order?.addEventListener('keydown', (event) => this.onOrderKeydown(event));
      this.order?.addEventListener('dragstart', (event) => this.onOrderDragStart(event));
      this.order?.addEventListener('dragend', () => this.clearDragState());
      const dropzone = this.querySelector('[data-order-dropzone]');
      dropzone?.addEventListener('dragover', (event) => this.onOrderDragOver(event));
      dropzone?.addEventListener('dragleave', (event) => this.onOrderDragLeave(event));
      dropzone?.addEventListener('drop', (event) => this.onOrderDrop(event));
      window.addEventListener('wheel', this.windowWheelListener, {capture: true, passive: false});
      this.pool?.addEventListener('click', (event) => this.onPoolClick(event));
      this.pool?.addEventListener('keydown', (event) => this.onPoolKeydown(event));
      this.pool?.addEventListener('dragstart', (event) => this.onPoolDragStart(event));
      this.pool?.addEventListener('dragend', () => this.clearDragState());
      this.querySelector('[data-source-filter]')?.addEventListener('input', () => this.renderSources());
      this.load();
    }

    disconnectedCallback() {
      window.removeEventListener('wheel', this.windowWheelListener, {capture: true});
    }

    async load() {
      const source = this.getAttribute('source');
      if (!source || !this.status) return;
      this.status.textContent = 'Loading watch-order workspace…';
      try {
        const response = await fetch(source, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        if (!response.ok) throw new Error('Workspace request failed');
        const payload = await response.json();
        const entries = Array.isArray(payload.entries) ? payload.entries.map(normaliseWatchRow).filter(Boolean) : [];
        const sources = Array.isArray(payload.sources) ? payload.sources.map(normaliseWatchSource).filter(Boolean) : [];
        if (!Number.isInteger(payload.revision)) throw new Error('Invalid workspace revision');
        this.revision = payload.revision;
        this.entries = entries;
        this.sources = sources;
        this.selectedSourceIds = new Set(
          [...this.selectedSourceIds].filter((id) => sources.some((source) => source.id === id))
        );
        this.renderOrder();
        this.renderSources();
        this.status.textContent = entries.length ? '' : 'Drop a collection item here to start this order.';
      } catch (_) {
        this.status.textContent = 'Could not load the watch-order workspace.';
      }
    }

    renderOrder() {
      if (!this.order) return;
      const previousPositions = new Map(
        Array.from(this.order.querySelectorAll('[data-entry-id]')).map((element) => [
          element.dataset.entryId,
          element.getBoundingClientRect(),
        ])
      );
      this.order.innerHTML = this.entries.length
        ? this.entries.map((row, index) => `${this.insertionSlot(this.entries[index]?.id ?? null)}${this.rowMarkup(row)}`).join('') + this.insertionSlot(null)
        : '';
      this.animateOrderLayout(previousPositions);
    }

    renderSources() {
      if (!this.pool) return;
      const input = this.querySelector('[data-source-filter]');
      const query = input instanceof HTMLInputElement ? input.value.trim().toLocaleLowerCase() : '';
      const matches = this.sources.filter((source) => this.sourceText(source).includes(query));
      this.pool.innerHTML = matches.map((source) => this.sourceMarkup(source)).join('') || '<p class="k-watch-workspace__empty">No collection items match this filter.</p>';
    }

    sourceText(source) {
      return [source.title, source.kind, source.seriesTitle || '', source.seasonNumber == null ? '' : `season ${source.seasonNumber}`].join(' ').toLocaleLowerCase();
    }

    rowMarkup(row) {
      const unavailable = row.available ? '' : '<span class="k-watch-order-poster__warning">Unavailable</span>';
      return `<article class="k-watch-order-poster" role="listitem" tabindex="0" draggable="true" data-entry-id="${row.id}" data-item-id="${row.itemId}" aria-label="${escapeHtml(`${row.position + 1}. ${row.title}`)}"><span class="k-watch-order-poster__position">${row.position + 1}</span>${posterMarkup(row.poster)}${unavailable}<span class="k-watch-order-poster__actions"><button type="button" class="k-row-button" data-row-action="back" aria-label="Move ${escapeHtml(row.title)} earlier; Shift-click to move to the start" title="Move earlier · Shift-click for start">←</button><button type="button" class="k-row-button" data-row-action="forward" aria-label="Move ${escapeHtml(row.title)} later; Shift-click to move to the end" title="Move later · Shift-click for end">→</button><button type="button" class="k-row-button" data-row-action="play" aria-label="Play ${escapeHtml(row.title)} from here">▶</button><button type="button" class="k-row-button" data-row-action="remove" aria-label="Remove ${escapeHtml(row.title)}">×</button></span></article>`;
    }

    insertionSlot(beforeEntryId) {
      const before = beforeEntryId == null ? '' : String(beforeEntryId);
      const label = beforeEntryId == null ? 'Add to end of order' : 'Insert before this poster';
      return `<div class="k-watch-order-slot" data-insert-before="${before}" aria-label="${label}" role="presentation"><span></span></div>`;
    }

    animateOrderLayout(previousPositions) {
      if (!this.order || !previousPositions.size) return;
      if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
      for (const element of this.order.querySelectorAll('[data-entry-id]')) {
        const previous = previousPositions.get(element.dataset.entryId);
        if (!previous) continue;
        const current = element.getBoundingClientRect();
        const x = previous.left - current.left;
        const y = previous.top - current.top;
        if ((x || y) && typeof element.animate === 'function') {
          element.animate(
            [{transform: `translate(${x}px, ${y}px)`}, {transform: 'translate(0, 0)'}],
            {duration: 220, easing: 'cubic-bezier(.2,.75,.25,1)'}
          );
        }
      }
    }

    sourceMarkup(source) {
      const unavailable = source.available ? '' : '<span class="k-watch-row__warning">Includes unavailable media</span>';
      const action = source.addable
        ? `<button type="button" class="k-row-button" data-source-add aria-label="Add ${escapeHtml(source.title)} to watch order">+</button>`
        : '<span class="k-watch-source__note">No playable descendants</span>';
      const selected = this.selectedSourceIds.has(source.id);
      const selectedClass = selected ? ' k-watch-source--selected' : '';
      return `<article class="k-watch-source${selectedClass}" role="listitem" aria-selected="${selected}" tabindex="${source.addable ? '0' : '-1'}" draggable="${source.addable}" data-source-item-id="${source.id}">${posterMarkup(source.poster)}${unavailable}${action}</article>`;
    }

    onPoolClick(event) {
      const button = event.target instanceof Element ? event.target.closest('[data-source-add]') : null;
      const source = button instanceof Element ? button.closest('.k-watch-source') : null;
      if (source instanceof HTMLElement) {
        event.preventDefault();
        this.addSource(source.dataset.sourceItemId, null);
        return;
      }
      const poster = event.target instanceof Element ? event.target.closest('.k-watch-source') : null;
      if (poster instanceof HTMLElement) {
        event.preventDefault();
        this.toggleSourceSelection(poster.dataset.sourceItemId);
      }
    }

    onPoolKeydown(event) {
      const source = event.target instanceof Element ? event.target.closest('.k-watch-source') : null;
      if (!(source instanceof HTMLElement) || event.target instanceof HTMLButtonElement) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        this.toggleSourceSelection(source.dataset.sourceItemId);
      }
    }

    toggleSourceSelection(sourceItemId) {
      const sourceId = Number(sourceItemId);
      const source = this.sources.find((candidate) => candidate.id === sourceId);
      if (!source?.addable) return;
      if (this.selectedSourceIds.has(sourceId)) this.selectedSourceIds.delete(sourceId);
      else this.selectedSourceIds.add(sourceId);
      this.renderSources();
    }

    onPoolDragStart(event) {
      const source = event.target instanceof Element ? event.target.closest('.k-watch-source') : null;
      if (!(source instanceof HTMLElement) || source.getAttribute('draggable') !== 'true' || !source.dataset.sourceItemId) return;
      this.isDragging = true;
      source.classList.add('k-watch-source--dragging');
      const sourceId = Number(source.dataset.sourceItemId);
      const selectedSourceIds = this.selectedSourceIds.has(sourceId)
        ? this.sources
          .filter((candidate) => this.selectedSourceIds.has(candidate.id))
          .map((candidate) => candidate.id)
        : [sourceId];
      event.dataTransfer?.setData('application/x-kanvas-watch-sources', JSON.stringify(selectedSourceIds));
      event.dataTransfer?.setData('application/x-kanvas-watch-source', source.dataset.sourceItemId);
      event.dataTransfer?.setData('text/plain', `kanvas-watch-sources:${selectedSourceIds.join(',')}`);
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'copy';
    }

    onOrderDragStart(event) {
      const poster = event.target instanceof Element ? event.target.closest('.k-watch-order-poster') : null;
      if (!(poster instanceof HTMLElement) || !poster.dataset.entryId) return;
      this.isDragging = true;
      poster.classList.add('k-watch-order-poster--dragging');
      event.dataTransfer?.setData('application/x-kanvas-watch-entry', poster.dataset.entryId);
      event.dataTransfer?.setData('text/plain', poster.dataset.entryId);
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
    }

    onOrderDragOver(event) {
      event.preventDefault();
      this.setActiveSlot(this.insertionSlotForTarget(event.target, event.clientX));
      this.updateDragScroll(event.clientX);
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = Array.from(event.dataTransfer.types).includes('application/x-kanvas-watch-source') ? 'copy' : 'move';
      }
    }

    onOrderDragLeave(event) {
      const related = event.relatedTarget;
      const dropzone = this.querySelector('[data-order-dropzone]');
      if (dropzone instanceof Element && related instanceof Node && dropzone.contains(related)) return;
      this.setActiveSlot(null);
      this.stopDragScroll();
    }

    onOrderDrop(event) {
      event.preventDefault();
      const slot = this.insertionSlotForTarget(event.target, event.clientX);
      const beforeEntryId = slot?.dataset.insertBefore || null;
      const plainText = event.dataTransfer?.getData('text/plain') || '';
      const sourceIds = this.sourceIdsFromDrop(event.dataTransfer, plainText);
      if (sourceIds.length) {
        this.addSources(sourceIds, beforeEntryId);
        this.clearDragState();
        return;
      }
      const entryId = event.dataTransfer?.getData('application/x-kanvas-watch-entry');
      if (entryId && !this.isNoopMove(entryId, beforeEntryId)) this.moveEntry(entryId, beforeEntryId);
      this.clearDragState();
    }

    sourceIdsFromDrop(dataTransfer, plainText) {
      const encoded = dataTransfer?.getData('application/x-kanvas-watch-sources');
      let values = [];
      try {
        values = encoded ? JSON.parse(encoded) : plainText.startsWith('kanvas-watch-sources:')
          ? plainText.slice('kanvas-watch-sources:'.length).split(',').map(Number)
          : [Number(dataTransfer?.getData('application/x-kanvas-watch-source'))];
      } catch (_) {
        return [];
      }
      if (!Array.isArray(values)) return [];
      const sourceIds = values.filter((id) => Number.isSafeInteger(id) && id > 0);
      return sourceIds.length === values.length && new Set(sourceIds).size === sourceIds.length ? sourceIds : [];
    }

    updateDragScroll(clientX) {
      if (!this.order) return;
      const bounds = this.order.getBoundingClientRect();
      const edgeWidth = Math.min(72, bounds.width / 3);
      const leftDistance = clientX - bounds.left;
      const rightDistance = bounds.right - clientX;
      this.dragScrollDirection = leftDistance < edgeWidth
        ? -(1 - leftDistance / edgeWidth)
        : rightDistance < edgeWidth
          ? 1 - rightDistance / edgeWidth
          : 0;
      if (this.dragScrollDirection && this.dragScrollFrame === null) this.runDragScroll();
    }

    runDragScroll() {
      if (!this.order || !this.dragScrollDirection) {
        this.dragScrollFrame = null;
        return;
      }
      this.order.scrollLeft += this.dragScrollDirection * 18;
      this.dragScrollFrame = requestAnimationFrame(() => this.runDragScroll());
    }

    stopDragScroll() {
      this.dragScrollDirection = 0;
      if (this.dragScrollFrame !== null) cancelAnimationFrame(this.dragScrollFrame);
      this.dragScrollFrame = null;
    }

    onOrderWheel(event) {
      if (!this.order || !this.isDragging || this.activeSlot === null) return;
      const delta = event.deltaX || event.deltaY;
      if (!delta) return;
      event.preventDefault();
      this.order.scrollLeft += delta;
    }

    insertionSlotForTarget(target, clientX) {
      const element = target instanceof Element ? target : null;
      const slot = element?.closest('.k-watch-order-slot');
      if (slot instanceof HTMLElement) return slot;
      const poster = element?.closest('.k-watch-order-poster');
      if (poster instanceof HTMLElement) {
        const bounds = poster.getBoundingClientRect();
        const insertBefore = clientX < bounds.left + bounds.width / 2;
        const adjacent = insertBefore ? poster.previousElementSibling : poster.nextElementSibling;
        if (adjacent instanceof HTMLElement && adjacent.classList.contains('k-watch-order-slot')) return adjacent;
      }
      return this.order?.querySelector('.k-watch-order-slot:last-child') ?? null;
    }

    setActiveSlot(slot) {
      if (this.activeSlot === slot) return;
      this.activeSlot?.classList.remove('k-watch-order-slot--active');
      this.activeSlot = slot instanceof HTMLElement ? slot : null;
      this.activeSlot?.classList.add('k-watch-order-slot--active');
      this.order?.classList.toggle('k-watch-workspace__order--dragging', this.activeSlot !== null);
    }

    clearDragState() {
      this.setActiveSlot(null);
      this.isDragging = false;
      this.stopDragScroll();
      this.querySelectorAll('.k-watch-order-poster--dragging, .k-watch-source--dragging').forEach((element) => {
        element.classList.remove('k-watch-order-poster--dragging', 'k-watch-source--dragging');
      });
    }

    isNoopMove(entryId, beforeEntryId) {
      const sourceIndex = this.entries.findIndex((entry) => entry.id === Number(entryId));
      const beforeIndex = beforeEntryId == null
        ? this.entries.length
        : this.entries.findIndex((entry) => entry.id === Number(beforeEntryId));
      return sourceIndex < 0 || beforeIndex < 0 || sourceIndex === beforeIndex || sourceIndex + 1 === beforeIndex;
    }

    onOrderClick(event) {
      const button = event.target instanceof Element ? event.target.closest('[data-row-action]') : null;
      const poster = button instanceof Element ? button.closest('.k-watch-order-poster') : null;
      if (!(button instanceof HTMLButtonElement) || !(poster instanceof HTMLElement)) return;
      const index = this.entries.findIndex((entry) => entry.id === Number(poster.dataset.entryId));
      if (index < 0) return;
      if (button.dataset.rowAction === 'back' && index > 0) {
        if (event.shiftKey) this.moveBoundary(poster.dataset.entryId, 'start');
        else this.moveEntry(poster.dataset.entryId, String(this.entries[index - 1].id));
      }
      if (button.dataset.rowAction === 'forward' && index < this.entries.length - 1) {
        if (event.shiftKey) this.moveBoundary(poster.dataset.entryId, 'end');
        else this.moveEntry(poster.dataset.entryId, index + 2 < this.entries.length ? String(this.entries[index + 2].id) : null);
      }
      if (button.dataset.rowAction === 'remove') this.mutate({operation: 'remove', entryId: Number(poster.dataset.entryId), revision: this.revision});
      if (button.dataset.rowAction === 'play') this.playFromHere(poster);
    }

    onOrderKeydown(event) {
      const poster = event.target instanceof Element ? event.target.closest('.k-watch-order-poster') : null;
      if (!(poster instanceof HTMLElement) || event.target instanceof HTMLButtonElement) return;
      const index = this.entries.findIndex((entry) => entry.id === Number(poster.dataset.entryId));
      if (index < 0) return;
      if ((event.key === 'ArrowLeft' || event.key === 'ArrowUp') && index > 0) { event.preventDefault(); this.moveEntry(poster.dataset.entryId, String(this.entries[index - 1].id)); }
      if ((event.key === 'ArrowRight' || event.key === 'ArrowDown') && index < this.entries.length - 1) { event.preventDefault(); this.moveEntry(poster.dataset.entryId, index + 2 < this.entries.length ? String(this.entries[index + 2].id) : null); }
      if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); this.mutate({operation: 'remove', entryId: Number(poster.dataset.entryId), revision: this.revision}); }
      if (event.key === 'Enter') { event.preventDefault(); window.location.assign(`/item/${poster.dataset.itemId}`); }
    }

    addSource(sourceItemId, beforeEntryId) {
      const sourceId = Number(sourceItemId);
      const beforeId = beforeEntryId == null ? null : Number(beforeEntryId);
      if (!Number.isSafeInteger(sourceId) || sourceId <= 0 || (beforeId !== null && (!Number.isSafeInteger(beforeId) || beforeId <= 0))) return;
      this.mutate({operation: 'add_source', sourceItemId: sourceId, beforeEntryId: beforeId, revision: this.revision});
    }

    addSources(sourceItemIds, beforeEntryId) {
      const sourceIds = sourceItemIds.map(Number);
      const beforeId = beforeEntryId == null ? null : Number(beforeEntryId);
      if (!sourceIds.length || sourceIds.some((id) => !Number.isSafeInteger(id) || id <= 0)) return;
      if (new Set(sourceIds).size !== sourceIds.length) return;
      if (beforeId !== null && (!Number.isSafeInteger(beforeId) || beforeId <= 0)) return;
      this.mutate({operation: 'add_sources', sourceItemIds: sourceIds, beforeEntryId: beforeId, revision: this.revision});
    }

    moveEntry(entryId, beforeEntryId) {
      const id = Number(entryId);
      const before = beforeEntryId == null ? null : Number(beforeEntryId);
      if (!Number.isSafeInteger(id) || id <= 0 || (before !== null && (!Number.isSafeInteger(before) || before <= 0))) return;
      this.mutate({operation: 'move', entryId: id, beforeEntryId: before, afterEntryId: null, revision: this.revision});
    }

    moveBoundary(entryId, boundary) {
      const id = Number(entryId);
      if (!Number.isSafeInteger(id) || id <= 0 || (boundary !== 'start' && boundary !== 'end')) return;
      this.mutate({operation: 'move', entryId: id, boundary, revision: this.revision});
    }

    async playFromHere(row) {
      const action = this.getAttribute('launch-action');
      if (!action || !this.status) return;
      if (row.querySelector('.k-watch-row__warning, .k-watch-order-poster__warning')) { this.status.textContent = 'This entry is unavailable. Use Play available entries to skip it.'; return; }
      this.status.textContent = 'Opening player…';
      try {
        const response = await fetch(action, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify({itemId: Number(row.dataset.itemId)})});
        const payload = await response.json();
        if (!response.ok || typeof payload.playbackUrl !== 'string' || !payload.playbackUrl.startsWith('/play/watch-orders/')) throw new Error('Launch failed');
        window.location.assign(payload.playbackUrl);
      } catch (_) { this.status.textContent = 'Could not start browser playback.'; }
    }

    async mutate(intent) {
      const action = this.getAttribute('action');
      if (!action || !this.status) return;
      this.setAttribute('aria-busy', 'true');
      this.status.textContent = 'Saving change…';
      try {
        const response = await fetch(action, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, credentials: 'same-origin', body: JSON.stringify(intent)});
        const payload = await response.json();
        if (response.status === 409) { this.showConflict(payload, intent); return; }
        if (!response.ok || !Number.isInteger(payload.revision)) throw new Error(payload.error || 'Action failed');
        this.revision = payload.revision;
        this.syncWatchOrderFormRevisions();
        await this.load();
      } catch (_) { this.status.textContent = 'Could not save this change.'; }
      finally { this.removeAttribute('aria-busy'); }
    }

    syncWatchOrderFormRevisions() {
      const entryAction = this.getAttribute('action');
      const actionPrefix = entryAction?.replace(/\/entries$/, '');
      if (!actionPrefix) return;
      document.querySelectorAll('form').forEach((form) => {
        if (!form.getAttribute('action')?.startsWith(actionPrefix)) return;
        form.querySelectorAll('input[name="revision"]').forEach((input) => {
          input.value = String(this.revision);
        });
      });
    }

    showConflict(payload, intent) {
      this.pendingIntent = intent;
      const state = this.querySelector('.k-conflict-state');
      if (!state) return;
      const revision = Number.isInteger(payload.currentRevision) ? payload.currentRevision : null;
      state.hidden = false;
      state.innerHTML = '<span>This watch order changed elsewhere.</span><button type="button" class="k-button" data-conflict-reload>Reload</button><button type="button" class="k-button" data-conflict-reapply>Reapply</button>';
      state.querySelector('[data-conflict-reload]')?.addEventListener('click', () => this.load());
      state.querySelector('[data-conflict-reapply]')?.addEventListener('click', () => {
        if (this.pendingIntent && revision !== null) this.mutate({...this.pendingIntent, revision});
      });
    }
  }

  if (!customElements.get('kanvas-collection-grid')) customElements.define('kanvas-collection-grid', KanvasCollectionGrid);
  if (!customElements.get('kanvas-item-picker')) customElements.define('kanvas-item-picker', KanvasItemPicker);
  if (!customElements.get('kanvas-watch-order-list')) customElements.define('kanvas-watch-order-list', KanvasWatchOrderList);
  if (!customElements.get('kanvas-watch-order-workspace')) customElements.define('kanvas-watch-order-workspace', KanvasWatchOrderWorkspace);

  const ITEM_EDITOR_KINDS = ['movie', 'series', 'season', 'episode', 'special', 'extra'];
  const ITEM_EDITOR_KIND_LABELS = {
    movie: 'Movie',
    series: 'Series',
    season: 'Season',
    episode: 'Episode',
    special: 'Special',
    extra: 'Extra'
  };
  const ITEM_EDITOR_LOCK_FIELDS = [
    {value: 'title', label: 'Title', kinds: ITEM_EDITOR_KINDS},
    {value: 'sort_title', label: 'Sort title', kinds: ITEM_EDITOR_KINDS},
    {value: 'release_date', label: 'Release date', kinds: ['movie', 'series', 'season', 'episode']},
    {value: 'overview', label: 'Overview', kinds: ITEM_EDITOR_KINDS},
    {value: 'season_number', label: 'Season number', kinds: ['season', 'episode']},
    {value: 'episode_number', label: 'Episode number', kinds: ['episode']}
  ];
  const ITEM_EDITOR_NUMBER_FIELDS = {
    season: [{name: 'seasonNumber', value: 'season_number', placeholder: 'Season', label: 'Season number'}],
    episode: [
      {name: 'seasonNumber', value: 'season_number', placeholder: 'Season', label: 'Season number'},
      {name: 'episodeNumber', value: 'episode_number', placeholder: 'Episode', label: 'Episode number'}
    ]
  };
  const ITEM_EDITOR_PARENT_KINDS = new Set(['season', 'episode', 'special', 'extra']);
  const ITEM_EDITOR_MATCHABLE_KINDS = new Set(['movie', 'series']);
  const ITEM_EDITOR_TABS = [
    {id: 'details', label: 'Details'},
    {id: 'match', label: 'Match'},
    {id: 'organise', label: 'Organise'},
    {id: 'artwork', label: 'Artwork'},
    {id: 'playback', label: 'Playback'},
    {id: 'history', label: 'History'}
  ];
  const ITEM_EDITOR_DEFAULT_TAB = 'details';

  const itemEditorKind = (value) => ITEM_EDITOR_KINDS.includes(value) ? value : 'movie';
  const itemEditorTab = (value) => ITEM_EDITOR_TABS.some((tab) => tab.id === value)
    ? value
    : ITEM_EDITOR_DEFAULT_TAB;
  const itemEditorRelevantLocks = (kind) => ITEM_EDITOR_LOCK_FIELDS.filter((field) => field.kinds.includes(kind));
  const itemEditorNumberFields = (kind) => ITEM_EDITOR_NUMBER_FIELDS[kind] || [];
  const itemEditorItemValue = (item, value) => item?.[value] ?? '';
  const normaliseItemEditorParentChoice = (value) => {
    if (!value || typeof value !== 'object') return null;
    const choice = value;
    if (!Number.isSafeInteger(choice.id) || choice.id <= 0) return null;
    if (typeof choice.title !== 'string' || !choice.title.trim()) return null;
    if (!ITEM_EDITOR_KINDS.includes(choice.kind)) return null;
    if (choice.season_number != null && (!Number.isSafeInteger(choice.season_number) || choice.season_number < 0)) return null;
    return choice;
  };
  const normaliseItemEditorMetadataBinding = (value) => {
    if (!value || typeof value !== 'object') return null;
    const binding = value;
    if (typeof binding.provider !== 'string' || !binding.provider.trim()) return null;
    if (typeof binding.provider_id !== 'string' || !binding.provider_id.trim()) return null;
    if (!ITEM_EDITOR_MATCHABLE_KINDS.has(binding.kind)) return null;
    if (binding.title != null && (typeof binding.title !== 'string' || !binding.title.trim())) return null;
    if (binding.year != null && (!Number.isSafeInteger(binding.year) || binding.year < 1 || binding.year > 9999)) return null;
    return binding;
  };
  const normaliseItemEditorMetadataSearchResult = (value) => {
    const result = normaliseItemEditorMetadataBinding(value);
    if (!result || typeof result.title !== 'string' || !result.title.trim()) return null;
    if (typeof value.confidence !== 'number' || !Number.isFinite(value.confidence) || value.confidence < 0 || value.confidence > 1) return null;
    return value;
  };

  class KanvasItemEditor extends HTMLElement {
    constructor() {
      super();
      this.dialog = null;
      this.status = null;
      this.controller = null;
      this.lockedMetadataFields = new Set();
      this.initialSelectedArtwork = new Map();
      this.currentItem = null;
      this.currentMetadataBinding = null;
      this.metadataSearchResults = [];
      this.selectedMetadataMatch = null;
      this.parentChoices = [];
      this.parentChoiceLoad = 0;
      this.isDirty = false;
      this.isSaving = false;
      this.activeTab = ITEM_EDITOR_DEFAULT_TAB;
    }

    connectedCallback() {
      this.innerHTML = '<button type="button" class="k-button" data-item-edit-open>Edit Details</button><dialog class="k-kanvas-dialog k-item-editor"><div class="k-picker" data-item-editor-content></div></dialog>';
      this.dialog = this.querySelector('dialog');
      this.querySelector('[data-item-edit-open]')?.addEventListener('click', () => this.open());
      this.dialog?.addEventListener('cancel', (event) => {
        if (!this.confirmDiscard()) event.preventDefault();
      });
      this.dialog?.addEventListener('close', () => {
        this.controller?.abort();
        this.isDirty = false;
        this.isSaving = false;
      });
    }

    disconnectedCallback() { this.controller?.abort(); }

    async open() {
      if (!this.dialog) return;
      if (!this.dialog.open) this.dialog.showModal();
      await this.load();
    }

    confirmDiscard() {
      if (this.isSaving || !this.isDirty) return !this.isSaving;
      return window.confirm('Discard unsaved changes to this item?');
    }

    requestClose() {
      if (!this.confirmDiscard()) return;
      this.dialog?.close();
    }

    async load() {
      const content = this.querySelector('[data-item-editor-content]');
      if (!content) return;
      content.innerHTML = '<div class="k-picker__status" aria-live="polite">Loading editable metadata…</div>';
      this.controller?.abort();
      this.controller = new AbortController();
      const source = this.getAttribute('source');
      if (!source) {
        content.innerHTML = '<div class="k-picker__status">This item cannot be edited because its data source is unavailable.</div>';
        return;
      }
      try {
        const response = await fetch(source, {headers: {'Accept': 'application/json'}, credentials: 'same-origin', signal: this.controller.signal});
        if (!response.ok) throw new Error('Item editor request failed');
        const payload = await response.json();
        if (!payload.item || typeof payload.item !== 'object') throw new Error('Item editor response was invalid');
        this.render(
          payload.item,
          Array.isArray(payload.audit) ? payload.audit : [],
          Array.isArray(payload.collectionChoices) ? payload.collectionChoices : [],
          Array.isArray(payload.collectionRelationships) ? payload.collectionRelationships : [],
          Array.isArray(payload.parentChoices) ? payload.parentChoices : [],
          payload.metadataBinding
        );
      } catch (error) {
        if (error?.name !== 'AbortError') content.innerHTML = '<div class="k-picker__status">This item could not be loaded for editing. Close and try again.</div>';
      }
    }

    render(item, audit, collectionChoices, collectionRelationships, parentChoices, metadataBinding) {
      const content = this.querySelector('[data-item-editor-content]');
      if (!content) return;
      this.currentItem = item;
      this.currentMetadataBinding = normaliseItemEditorMetadataBinding(metadataBinding);
      this.metadataSearchResults = [];
      this.selectedMetadataMatch = null;
      const selected = new Map((Array.isArray(item.selected_artwork) ? item.selected_artwork : []).map((entry) => [entry.kind, entry.artwork_id]));
      this.initialSelectedArtwork = selected;
      const locks = new Set(Array.isArray(item.locked_metadata_fields) ? item.locked_metadata_fields : []);
      this.lockedMetadataFields = locks;
      const artworks = Array.isArray(item.artwork) ? item.artwork : [];
      const artworkKinds = [...new Set(artworks.map((artwork) => artwork.kind))];
      const artworkRows = this.renderArtworkRows(artworks, artworkKinds, selected);
      const auditRows = audit.length ? audit.map((entry) => `<li>${escapeHtml(entry.actor || 'administrator')} · ${escapeHtml((entry.changed_fields || []).join(', ') || 'updated')} · ${escapeHtml(entry.occurred_at || '')}</li>`).join('') : '<li>No local edits have been recorded.</li>';
      const kind = itemEditorKind(item.kind);
      const collectionControls = this.renderCollectionControls(
        item, collectionChoices, collectionRelationships
      );
      this.parentChoices = parentChoices.map(normaliseItemEditorParentChoice).filter(Boolean);
      const playbackControls = this.renderPlaybackDefaults(item);
      const tabs = this.editorTabs(Boolean(playbackControls));
      this.activeTab = this.availableTab(this.activeTab, tabs);
      content.innerHTML = `<form class="k-item-editor__form" data-item-editor-form><div class="k-picker__header"><div class="k-item-editor__heading"><strong title="${escapeHtml(item.title || `Item ${item.id || ''}`)}">Edit ${escapeHtml(item.title || `Item ${item.id || ''}`)}</strong><span>${escapeHtml(ITEM_EDITOR_KIND_LABELS[kind])}</span></div><button type="button" class="k-button" data-item-editor-close>Close</button></div>${this.renderTabNavigation(tabs)}<div class="k-item-editor__tab-panels">${this.renderTabPanel('details', this.renderDetailsTab(kind, item), 'Details')}${this.renderTabPanel('match', this.renderMatchTab(kind, locks, this.currentMetadataBinding, item.title), 'Match')}${this.renderTabPanel('organise', this.renderOrganiseTab(kind, item, collectionControls), 'Organise')}${this.renderTabPanel('artwork', this.renderArtworkTab(artworkRows, kind, this.currentMetadataBinding), 'Artwork')}${playbackControls ? this.renderTabPanel('playback', playbackControls, 'Playback') : ''}${this.renderTabPanel('history', this.renderHistoryTab(auditRows), 'History')}</div><div class="k-picker__status" data-item-editor-status aria-live="polite"></div><div class="k-action-row"><button type="submit" class="k-button k-button--primary">Save local edits</button></div></form>`;
      this.status = content.querySelector('[data-item-editor-status]');
      content.querySelector('[data-item-editor-close]')?.addEventListener('click', () => this.requestClose());
      const form = content.querySelector('[data-item-editor-form]');
      form?.addEventListener('submit', (event) => this.submit(event));
      form?.addEventListener('input', (event) => {
        if (!event.target?.closest?.('[data-item-match-workflow]')) this.isDirty = true;
      });
      form?.addEventListener('change', (event) => {
        if (!event.target?.closest?.('[data-item-match-workflow]')) this.isDirty = true;
      });
      form?.addEventListener('invalid', (event) => this.showTabForControl(event.target), true);
      content.querySelector('[data-item-editor-kind]')?.addEventListener('change', (event) => {
        void this.updateKindFields(event.currentTarget?.value);
      });
      this.bindTabs(content);
      this.bindMetadataMatchControls(content);
      this.bindArtworkFetchControl(content);
      this.bindCollectionControls(content);
      this.bindPlaybackForceControls(content);
      this.addVisibleFieldLabels(content);
      this.isDirty = false;
    }

    editorTabs(hasPlaybackControls) {
      return ITEM_EDITOR_TABS.filter((tab) => tab.id !== 'playback' || hasPlaybackControls);
    }

    availableTab(value, tabs) {
      const tab = itemEditorTab(value);
      return tabs.some((entry) => entry.id === tab) ? tab : ITEM_EDITOR_DEFAULT_TAB;
    }

    renderTabNavigation(tabs) {
      return `<div class="k-item-editor__tabs" role="tablist" aria-label="Edit sections">${tabs.map((tab) => {
        const active = tab.id === this.activeTab;
        return `<button type="button" class="k-item-editor__tab${active ? ' k-item-editor__tab--active' : ''}" role="tab" id="item-editor-tab-${tab.id}" aria-controls="item-editor-panel-${tab.id}" aria-selected="${active}" tabindex="${active ? '0' : '-1'}" data-item-editor-tab="${tab.id}">${tab.label}</button>`;
      }).join('')}</div>`;
    }

    renderTabPanel(id, content, label) {
      const active = id === this.activeTab;
      return `<section class="k-item-editor__tab-panel" role="tabpanel" id="item-editor-panel-${id}" aria-labelledby="item-editor-tab-${id}" data-item-editor-tab-panel="${id}"${active ? '' : ' hidden'}><h2 class="k-item-editor__panel-heading">${label}</h2>${content}</section>`;
    }

    renderDetailsTab(kind, item) {
      return `<section class="k-item-editor__section"><label class="k-control-shell k-input-shell"><input class="k-input" name="title" value="${escapeHtml(item.title || '')}" aria-label="Title" required></label><label class="k-control-shell k-input-shell"><input class="k-input" name="sortTitle" value="${escapeHtml(item.sort_title || '')}" aria-label="Sort title" required></label><label class="k-control-shell k-textarea-shell"><textarea class="k-textarea" name="overview" aria-label="Overview">${escapeHtml(item.overview || '')}</textarea></label></section><section class="k-item-editor__section"><div class="k-item-editor__grid"><label class="k-control-shell k-input-shell"><input class="k-input" type="date" name="releaseDate" value="${escapeHtml(item.release_date || '')}" aria-label="Release date"></label><label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="1" max="9999" name="releaseYear" value="${item.year || ''}" placeholder="Year" aria-label="Release year"></label></div><label class="k-control-shell k-input-shell"><input class="k-input" name="tags" value="${escapeHtml((item.tags || []).join(', '))}" aria-label="Tags" placeholder="Tags, comma separated"></label></section><section class="k-item-editor__section" data-item-editor-kind-fields>${this.renderKindFields(kind, item)}</section>`;
    }

    renderMatchTab(kind, locks, binding, defaultQuery) {
      const locksSection = `<section class="k-item-editor__section"><div><h3 class="k-item-editor__section-heading">Protect local edits</h3><p class="k-item-editor__muted">Protected fields are preserved when metadata is refreshed or a new match is applied.</p></div><div class="k-item-editor__checks" data-item-editor-locks>${this.renderLockRows(kind, locks)}</div></section>`;
      if (!ITEM_EDITOR_MATCHABLE_KINDS.has(kind)) {
        return `<section class="k-item-editor__match-card"><p>${escapeHtml(ITEM_EDITOR_KIND_LABELS[kind])} items inherit their metadata association from their parent record.</p></section>${locksSection}`;
      }
      const currentTitle = this.renderMetadataEntryTitle(
        binding,
        binding?.title || 'No metadata record selected'
      );
      const currentFacts = binding
        ? `${binding.year || '—'} · ${binding.provider} · ${binding.provider_id}`
        : 'Choose a provider record to apply its unlocked metadata.';
      return `<section class="k-item-editor__match-card"><div class="k-item-editor__match-current"><div><h3 class="k-item-editor__section-heading">Current metadata match</h3>${currentTitle}<small>${escapeHtml(currentFacts)}</small></div><button type="button" class="k-button" data-item-match-change>Change match</button></div><div class="k-item-editor__match-workflow" data-item-match-workflow hidden><div class="k-item-editor__match-input-row"><label class="k-item-editor__match-input"><span>Search database records</span><input class="k-input" value="${escapeHtml(defaultQuery || '')}" aria-label="Search database records" data-item-match-query></label><button type="button" class="k-button" data-item-match-search>Search database</button></div><div class="k-item-editor__match-input-row"><label class="k-item-editor__match-input"><span>Or paste a TMDB link</span><input class="k-input" inputmode="url" placeholder="https://www.themoviedb.org/movie/..." aria-label="Paste a TMDB record link" data-item-match-link></label><button type="button" class="k-button" data-item-match-link-use>Review link match</button></div><p class="k-item-editor__muted">After pasting a link, choose Review link match, then Apply selected match below. Save local edits does not change the metadata association.</p><div class="k-picker__status" data-item-match-status aria-live="polite"></div><div class="k-item-editor__match-results" data-item-match-results></div><section class="k-item-editor__match-confirmation" data-item-match-confirmation hidden></section></div></section>${locksSection}`;
    }

    renderMetadataEntryTitle(entry, fallback) {
      const title = typeof entry?.title === 'string' && entry.title.trim() ? entry.title : fallback;
      const url = providerEntryUrl(entry);
      if (!url) return `<strong class="k-item-editor__match-title">${escapeHtml(title)}</strong>`;
      const provider = providerDisplayName(entry.provider);
      return `<a class="k-item-editor__match-title" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(title)} on ${escapeHtml(provider)}">${escapeHtml(title)}</a>`;
    }

    bindMetadataMatchControls(content) {
      const workflow = content.querySelector('[data-item-match-workflow]');
      const query = content.querySelector('[data-item-match-query]');
      const link = content.querySelector('[data-item-match-link]');
      content.querySelector('[data-item-match-change]')?.addEventListener('click', () => {
        if (!workflow) return;
        workflow.hidden = false;
        query?.focus();
      });
      content.querySelector('[data-item-match-search]')?.addEventListener('click', () => {
        void this.searchMetadata(query?.value);
      });
      query?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        void this.searchMetadata(query.value);
      });
      content.querySelector('[data-item-match-link-use]')?.addEventListener('click', () => {
        this.selectMetadataMatchFromLink(link?.value);
      });
      link?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        this.selectMetadataMatchFromLink(link.value);
      });
    }

    selectMetadataMatchFromLink(value) {
      const content = this.querySelector('[data-item-editor-content]');
      const status = content?.querySelector('[data-item-match-status]');
      if (!status) return;
      const match = this.metadataMatchFromLink(value);
      if (!match) {
        status.textContent = 'Enter a TMDB movie or TV link that matches this item type.';
        return;
      }
      this.selectMetadataMatchResult(match);
      status.textContent = 'Review the link match below, then apply it.';
    }

    metadataMatchFromLink(value) {
      const kind = itemEditorKind(this.currentItem?.kind);
      const reference = tmdbEntryReferenceFromUrl(value, kind);
      if (!reference) return null;
      return {
        ...reference,
        title: `${providerDisplayName(reference.provider)} record ${reference.provider_id}`,
        year: null
      };
    }

    async searchMetadata(rawQuery) {
      const content = this.querySelector('[data-item-editor-content]');
      const status = content?.querySelector('[data-item-match-status]');
      const results = content?.querySelector('[data-item-match-results]');
      const confirmation = content?.querySelector('[data-item-match-confirmation]');
      const query = typeof rawQuery === 'string' ? rawQuery.trim() : '';
      if (!content || !status || !results || !confirmation) return;
      if (!query) {
        status.textContent = 'Enter a title to search.';
        return;
      }
      const source = this.getAttribute('metadata-search-source');
      if (!source) {
        status.textContent = 'Metadata search is unavailable for this item.';
        return;
      }
      const searchButton = content.querySelector('[data-item-match-search]');
      if (searchButton) searchButton.disabled = true;
      this.metadataSearchResults = [];
      this.selectedMetadataMatch = null;
      confirmation.hidden = true;
      confirmation.replaceChildren();
      results.replaceChildren();
      status.textContent = 'Searching database records…';
      try {
        const url = new URL(source, window.location.origin);
        url.searchParams.set('query', query);
        const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.error === 'string' ? payload.error : 'Metadata search failed.');
        if (!Array.isArray(payload.results)) throw new Error('Metadata search response was invalid.');
        this.metadataSearchResults = payload.results.map(normaliseItemEditorMetadataSearchResult).filter(Boolean);
        if (!this.metadataSearchResults.length) {
          status.textContent = 'No matching database records were found.';
          return;
        }
        status.textContent = `${this.metadataSearchResults.length} database records found.`;
        this.renderMetadataSearchResults(results);
      } catch (error) {
        status.textContent = error?.message || 'Metadata search failed.';
      } finally {
        if (searchButton) searchButton.disabled = false;
      }
    }

    renderMetadataSearchResults(container) {
      container.innerHTML = this.metadataSearchResults.map((result, index) => `<article class="k-item-editor__match-result"><div>${this.renderMetadataEntryTitle(result, result.title)}<small>${escapeHtml(`${result.year || '—'} · ${result.kind} · ${result.provider}`)}</small></div><small>${Math.round(result.confidence * 100)}% similarity</small><button type="button" class="k-button" data-item-match-result="${index}">Select</button></article>`).join('');
      container.querySelectorAll('[data-item-match-result]').forEach((button) => {
        button.addEventListener('click', () => this.selectMetadataMatch(Number(button.dataset.itemMatchResult)));
      });
    }

    selectMetadataMatch(index) {
      this.selectMetadataMatchResult(this.metadataSearchResults[index]);
    }

    selectMetadataMatchResult(result) {
      const content = this.querySelector('[data-item-editor-content]');
      const confirmation = content?.querySelector('[data-item-match-confirmation]');
      if (!result || !confirmation) return;
      this.selectedMetadataMatch = result;
      confirmation.hidden = false;
      confirmation.innerHTML = `<h3 class="k-item-editor__section-heading">Apply ${this.renderMetadataEntryTitle(result, result.title)}?</h3><p>${escapeHtml(`${result.year || '—'} · ${result.provider} · ${result.provider_id}`)}</p><p class="k-item-editor__muted">This replaces the current metadata match and updates every field not protected above.</p><button type="button" class="k-button k-button--primary" data-item-match-apply>Apply selected match</button>`;
      confirmation.querySelector('[data-item-match-apply]')?.addEventListener('click', () => {
        void this.applyMetadataMatch();
      });
    }

    async applyMetadataMatch() {
      const match = this.selectedMetadataMatch;
      const content = this.querySelector('[data-item-editor-content]');
      const form = content?.querySelector('[data-item-editor-form]');
      const applyButton = content?.querySelector('[data-item-match-apply]');
      if (!match || !(form instanceof HTMLFormElement) || !this.status || this.isSaving) return;
      if (typeof form.reportValidity === 'function' && !form.reportValidity()) return;
      const source = this.getAttribute('metadata-match-source');
      if (!source) {
        this.status.textContent = 'Metadata reassignment is unavailable for this item.';
        return;
      }
      if (applyButton) applyButton.disabled = true;
      this.isSaving = true;
      try {
        if (this.isDirty) {
          this.status.textContent = 'Saving changes before reassignment…';
          await this.saveItemMetadata(form);
          this.isDirty = false;
        }
        this.status.textContent = 'Applying metadata match…';
        const response = await fetch(source, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({provider: match.provider, providerId: match.provider_id, confirmed: true})
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.error === 'string' ? payload.error : 'Metadata reassignment failed.');
        this.status.textContent = `Matched ${match.title}.`;
        window.setTimeout(() => window.location.reload(), 450);
      } catch (error) {
        this.status.textContent = error?.message || 'Metadata reassignment could not be applied.';
        if (applyButton) applyButton.disabled = false;
        this.isSaving = false;
      }
    }

    renderOrganiseTab(kind, item, collectionControls) {
      return `<section class="k-item-editor__section"><div><h3 class="k-item-editor__section-heading">Library organisation</h3><p class="k-item-editor__muted">Set the item type and its place in the library hierarchy.</p></div><div class="k-item-editor__grid"><label class="k-control-shell k-select-wrap"><select class="k-select" name="kind" aria-label="Kind" data-item-editor-kind>${ITEM_EDITOR_KINDS.map((kindOption) => `<option value="${kindOption}"${kindOption === kind ? ' selected' : ''}>${ITEM_EDITOR_KIND_LABELS[kindOption]}</option>`).join('')}</select></label><span data-item-editor-hierarchy-fields>${this.renderHierarchyFields(kind, item)}</span></div></section>${collectionControls}`;
    }

    renderArtworkTab(artworkRows, kind, binding) {
      const fetchControl = ITEM_EDITOR_MATCHABLE_KINDS.has(kind) && binding
        ? `<div class="k-action-row"><button type="button" class="k-button" data-item-artwork-fetch>Load poster choices</button></div><div class="k-picker__status" data-item-artwork-status aria-live="polite"></div>`
        : '';
      return `<section class="k-item-editor__section">${fetchControl}<div class="k-item-editor__artwork-grid" data-item-editor-artwork-grid>${artworkRows}</div></section>`;
    }

    bindArtworkFetchControl(content) {
      const button = content.querySelector('[data-item-artwork-fetch]');
      const status = content.querySelector('[data-item-artwork-status]');
      button?.addEventListener('click', async () => {
        const source = this.getAttribute('artwork-fetch-source');
        if (!source || !status || this.isSaving) {
          if (status && !source) status.textContent = 'Artwork fetch is unavailable for this item.';
          return;
        }
        button.disabled = true;
        status.textContent = 'Loading poster choices from the current match…';
        try {
          const response = await fetch(source, {
            method: 'POST',
            headers: {'Accept': 'application/json'},
            credentials: 'same-origin'
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(typeof payload.error === 'string' ? payload.error : 'Artwork fetch failed.');
          if (!Array.isArray(payload.artwork)) throw new Error('Artwork fetch response was invalid.');
          const grid = content.querySelector('[data-item-editor-artwork-grid]');
          const form = content.querySelector('[data-item-editor-form]');
          if (!grid || !(form instanceof HTMLFormElement) || !this.currentItem) {
            throw new Error('Artwork could not be displayed.');
          }
          const selected = this.selectedArtworkFromForm(form);
          this.currentItem.artwork = payload.artwork;
          const artworkKinds = [...new Set(payload.artwork.map((artwork) => artwork.kind))];
          grid.innerHTML = this.renderArtworkRows(payload.artwork, artworkKinds, selected);
          status.textContent = payload.artwork.length
            ? `${payload.artwork.length} poster choice${payload.artwork.length === 1 ? '' : 's'} loaded. Choose one below, then save local edits.`
            : 'No posters were available for the current metadata match.';
        } catch (error) {
          status.textContent = error?.message || 'Artwork could not be fetched.';
        } finally {
          button.disabled = false;
        }
      });
    }

    renderHistoryTab(auditRows) {
      return `<section class="k-item-editor__section"><p class="k-item-editor__muted">Changes made in this editor are recorded here.</p><ul class="k-item-editor__audit">${auditRows}</ul></section>`;
    }

    bindTabs(content) {
      const buttons = Array.from(content.querySelectorAll('[data-item-editor-tab]'));
      buttons.forEach((button) => {
        button.addEventListener('click', () => this.setActiveTab(button.dataset.itemEditorTab));
        button.addEventListener('keydown', (event) => {
          const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
          if (!keys.includes(event.key)) return;
          event.preventDefault();
          const currentIndex = buttons.indexOf(button);
          const nextIndex = event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? buttons.length - 1
              : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
          this.setActiveTab(buttons[nextIndex]?.dataset.itemEditorTab, true);
        });
      });
    }

    setActiveTab(value, focus = false) {
      const content = this.querySelector('[data-item-editor-content]');
      if (!content) return;
      const tabs = this.editorTabs(Boolean(content.querySelector('[data-item-editor-tab="playback"]')));
      const activeTab = this.availableTab(value, tabs);
      this.activeTab = activeTab;
      let activeButton = null;
      content.querySelectorAll('[data-item-editor-tab]').forEach((button) => {
        const active = button.dataset.itemEditorTab === activeTab;
        button.classList.toggle('k-item-editor__tab--active', active);
        button.setAttribute('aria-selected', String(active));
        button.tabIndex = active ? 0 : -1;
        if (active) activeButton = button;
      });
      content.querySelectorAll('[data-item-editor-tab-panel]').forEach((panel) => {
        panel.hidden = panel.dataset.itemEditorTabPanel !== activeTab;
      });
      if (focus) activeButton?.focus();
    }

    showTabForControl(control) {
      const panel = control?.closest?.('[data-item-editor-tab-panel]');
      if (panel?.dataset.itemEditorTabPanel) this.setActiveTab(panel.dataset.itemEditorTabPanel);
    }

    addVisibleFieldLabels(content) {
      const labels = {
        title: 'Title',
        sortTitle: 'Sort title',
        overview: 'Overview',
        releaseDate: 'Release date',
        releaseYear: 'Release year',
        tags: 'Tags',
        seasonNumber: 'Season number',
        episodeNumber: 'Episode number',
        kind: 'Item type',
        defaultAudioStreamIndex: 'Default audio',
        defaultSubtitleTrackId: 'Default subtitles',
        defaultSubtitleTimingOffsetMilliseconds: 'Subtitle timing',
        defaultSubtitleFontScalePercent: 'Subtitle size',
      };
      Object.entries(labels).forEach(([name, label]) => {
        const control = content.querySelector(`[name="${name}"]`);
        const shell = control?.closest('.k-control-shell');
        if (!shell || shell.querySelector('.k-item-editor__field-label')) return;
        const fieldLabel = document.createElement('span');
        fieldLabel.className = 'k-item-editor__field-label';
        fieldLabel.textContent = label;
        shell.classList.add('k-item-editor__field');
        shell.prepend(fieldLabel);
      });
    }

    renderCollectionControls(item, collectionChoices, collectionRelationships) {
      const isCollection = (collection) => collection
        && Number.isSafeInteger(collection.id)
        && collection.id > 0
        && typeof collection.name === 'string'
        && collection.name.trim()
        && Number.isSafeInteger(collection.revision)
        && collection.revision > 0;
      const included = (Array.isArray(item.collections) ? item.collections : []).filter(isCollection);
      const choices = collectionChoices.filter(isCollection);
      const relationships = collectionRelationships.filter((relationship) => (
        typeof relationship === 'string' && relationship
      ));
      const memberRows = included.length
        ? included.map((collection) => `<div class="k-member-editor-row"><a href="/collections/${collection.id}" class="k-member-editor-row__title">${escapeHtml(collection.name)}</a>${collection.relationship ? `<span class="k-member-editor-row__relationship">${escapeHtml(String(collection.relationship).replaceAll('_', ' '))}</span>` : ''}<button type="button" class="k-button" data-item-collection-remove="${collection.id}" data-item-collection-revision="${collection.revision}">Remove</button></div>`).join('')
        : '<p class="k-item-editor__muted">This item is not in a collection.</p>';
      const relationshipOptions = ['<option value="">No relationship</option>']
        .concat(relationships.map((relationship) => `<option value="${escapeHtml(relationship)}">${escapeHtml(relationship.replaceAll('_', ' '))}</option>`))
        .join('');
      const addControl = choices.length
        ? `<div class="k-item-editor__grid"><label class="k-control-shell k-select-wrap"><select class="k-select" aria-label="Add to collection" data-item-collection-target>${choices.map((collection) => `<option value="${collection.id}:${collection.revision}">${escapeHtml(collection.name)}</option>`).join('')}</select></label><label class="k-control-shell k-select-wrap"><select class="k-select" aria-label="Collection relationship" data-item-collection-relationship>${relationshipOptions}</select></label><button type="button" class="k-button" data-item-collection-add>Add to collection</button></div>`
        : '<p class="k-item-editor__muted">No other collections are available.</p>';
      return `<section class="k-item-editor__section"><div><h3 class="k-item-editor__section-heading">Collections</h3><p class="k-item-editor__muted">Place this item in one or more collections.</p></div>${memberRows}${addControl}</section>`;
    }

    renderPlaybackDefaults(item) {
      const defaults = item.playback_defaults && typeof item.playback_defaults === 'object'
        ? item.playback_defaults
        : {};
      const audioStreams = Array.isArray(item.playback_audio_streams)
        ? item.playback_audio_streams
        : [];
      const subtitleTracks = Array.isArray(item.playback_subtitle_tracks)
        ? item.playback_subtitle_tracks
        : [];
      if (!audioStreams.length && !subtitleTracks.length) return '';
      const audioOptions = audioStreams.map((stream, index) => {
        const label = [stream.language, stream.title, stream.codec].filter(Boolean).join(' · ') || `Audio ${index + 1}`;
        return `<option value="${index}"${defaults.audio_stream_index === index ? ' selected' : ''}>${escapeHtml(label)}</option>`;
      }).join('');
      const subtitleOptions = subtitleTracks.map((track) => {
        const label = [track.language, track.title, track.codec].filter(Boolean).join(' · ') || 'Subtitle';
        return `<option value="${escapeHtml(track.id)}"${defaults.subtitle_track_id === track.id ? ' selected' : ''}>${escapeHtml(label)}</option>`;
      }).join('');
      const timing = defaults.subtitle_timing_offset_milliseconds ?? '';
      const fontScale = defaults.subtitle_font_scale_percent ?? '';
      return `<section class="k-item-editor__section"><p class="k-item-editor__muted">Profile defaults take precedence unless the matching item control is forced. Playback-session choices remain changeable.</p><div class="k-item-editor__playback-option"><label class="k-control-shell k-select-wrap"><select class="k-select" name="defaultAudioStreamIndex" aria-label="Default audio track"><option value="">Automatic audio</option>${audioOptions}</select></label><label class="k-check"><input type="checkbox" name="forceDefaultAudioStream"${defaults.force_audio_stream ? ' checked' : ''}> Force this audio track</label></div><div class="k-item-editor__playback-option"><label class="k-control-shell k-select-wrap"><select class="k-select" name="defaultSubtitleTrackId" aria-label="Default subtitle track"><option value="">Automatic subtitles</option>${subtitleOptions}</select></label><label class="k-check"><input type="checkbox" name="forceDefaultSubtitleTrack"${defaults.force_subtitle_track ? ' checked' : ''}> Force this subtitle track</label></div><label class="k-control-shell k-input-shell"><input class="k-input" type="number" min="-30000" max="30000" step="100" name="defaultSubtitleTimingOffsetMilliseconds" value="${timing}" aria-label="Default subtitle timing offset in milliseconds" placeholder="Timing offset in milliseconds"></label><div class="k-item-editor__playback-option"><label class="k-control-shell k-select-wrap"><select class="k-select" name="defaultSubtitleFontScalePercent" aria-label="Default subtitle font size"><option value="">Use profile default</option>${[75, 100, 125, 150, 175, 200].map((value) => `<option value="${value}"${fontScale === value ? ' selected' : ''}>${value}%</option>`).join('')}</select></label><label class="k-check"><input type="checkbox" name="forceDefaultSubtitleFontScale"${defaults.force_subtitle_font_scale ? ' checked' : ''}> Force this subtitle font size</label></div></section>`;
    }

    bindCollectionControls(content) {
      content.querySelectorAll('[data-item-collection-remove]').forEach((button) => {
        button.addEventListener('click', async () => {
          const collectionId = Number(button.dataset.itemCollectionRemove);
          const revision = Number(button.dataset.itemCollectionRevision);
          if (!Number.isSafeInteger(collectionId) || collectionId <= 0 || !Number.isSafeInteger(revision) || revision <= 0) return;
          const action = this.collectionActionSource();
          if (!action) return;
          await this.mutateCollection(
            `${action}/${collectionId}/remove`,
            {revision: String(revision)}
          );
        });
      });
      content.querySelector('[data-item-collection-add]')?.addEventListener('click', async () => {
        const target = content.querySelector('[data-item-collection-target]');
        const relationship = content.querySelector('[data-item-collection-relationship]');
        if (!(target instanceof HTMLSelectElement) || !(relationship instanceof HTMLSelectElement)) return;
        if (!/^([1-9]\d*):([1-9]\d*)$/.test(target.value)) return;
        await this.mutateCollection(this.collectionActionSource(), {
          collection_target: target.value,
          relationship: relationship.value
        });
      });
    }

    collectionActionSource() {
      const action = this.getAttribute('action-source');
      return action ? `${action}/collections` : '';
    }

    async mutateCollection(action, values) {
      if (!action || !this.status) return;
      this.status.textContent = 'Saving collection membership…';
      try {
        const response = await fetch(action, {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          credentials: 'same-origin',
          body: new URLSearchParams(values)
        });
        if (!response.ok) throw new Error('Collection membership could not be saved.');
        window.location.reload();
      } catch (error) {
        this.status.textContent = error?.message || 'Collection membership could not be saved.';
      }
    }

    renderKindFields(kind, item, values = new Map()) {
      const fields = itemEditorNumberFields(kind);
      if (!fields.length) return '';
      return `<div class="k-item-editor__grid">${fields.map((field) => {
        const value = values.has(field.name)
          ? values.get(field.name)
          : itemEditorItemValue(item, field.value);
        return `<label class="k-control-shell k-input-shell--year"><input class="k-input" type="number" min="0" name="${field.name}" value="${escapeHtml(String(value ?? ''))}" placeholder="${field.placeholder}" aria-label="${field.label}"></label>`;
      }).join('')}</div>`;
    }

    renderHierarchyFields(kind, item, parentId, parentChoices = this.parentChoices) {
      if (!ITEM_EDITOR_PARENT_KINDS.has(kind)) return '<span class="k-item-editor__muted">Top-level item</span>';
      const value = parentId === undefined ? item.parent_id : parentId;
      const selectedId = value == null ? '' : String(value);
      const choices = Array.isArray(parentChoices) ? parentChoices : [];
      const options = choices.map((choice) => {
        const context = choice.kind === 'season' && choice.season_number != null
          ? `Season ${choice.season_number}`
          : ITEM_EDITOR_KIND_LABELS[choice.kind];
        return `<option value="${choice.id}"${String(choice.id) === selectedId ? ' selected' : ''}>${escapeHtml(choice.title)} · ${escapeHtml(context)}</option>`;
      }).join('');
      const emptyOption = choices.length
        ? '<option value="">Select a parent</option>'
        : '<option value="">No eligible parents are available</option>';
      return `<label class="k-item-editor__field"><span class="k-item-editor__field-label">Parent</span><span class="k-control-shell k-select-wrap"><select class="k-select" name="parentId" aria-label="Parent item" required>${emptyOption}${options}</select></span></label>`;
    }

    renderLockRows(kind, locks) {
      return itemEditorRelevantLocks(kind).map((field) => `<label class="k-check"><input type="checkbox" name="lock" value="${field.value}"${locks.has(field.value) ? ' checked' : ''}> ${field.label}</label>`).join('');
    }

    renderArtworkRows(artworks, artworkKinds, selected) {
      if (!artworks.length) return '<p class="k-quiet-copy">No cached artwork is available to select.</p>';
      return artworkKinds.map((kind) => {
        const choicesForKind = artworks.filter((artwork) => artwork.kind === kind);
        const primary = choicesForKind.find((artwork) => artwork.is_primary) || choicesForKind[0];
        const artworkUrl = (artwork) => typeof artwork?.url === 'string'
          ? artwork.url.replace(/^\/api\/v1\/library\/items\/(\d+)\/artwork\/(\d+)$/, '/kanvas/artwork/$1/$2')
          : null;
        const image = (artwork) => {
          const url = artworkUrl(artwork);
          return url && localArtworkUrl(url)
            ? `<img src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async">`
            : '<span class="k-item-editor__artwork-placeholder" aria-hidden="true"></span>';
        };
        const details = (artwork) => {
          const values = [];
          if (typeof artwork.language === 'string' && artwork.language.trim()) values.push(artwork.language.toUpperCase());
          if (Number.isSafeInteger(artwork.width) && Number.isSafeInteger(artwork.height)) values.push(`${artwork.width} × ${artwork.height}`);
          if (typeof artwork.vote_average === 'number' && Number.isFinite(artwork.vote_average) && Number.isSafeInteger(artwork.vote_count) && artwork.vote_count > 0) values.push(`${artwork.vote_average.toFixed(1)} · ${artwork.vote_count} votes`);
          return values.length ? values.join(' · ') : 'Poster variant';
        };
        const automatic = `<label class="k-item-editor__artwork"><input type="radio" name="artwork-${escapeHtml(kind)}" value="" data-artwork-kind="${escapeHtml(kind)}"${selected.has(kind) ? '' : ' checked'}><span class="k-item-editor__artwork-card">${image(primary)}<span class="k-item-editor__artwork-title">Automatic</span><small>Provider default</small></span></label>`;
        const choices = choicesForKind.map((artwork) => `<label class="k-item-editor__artwork"><input type="radio" name="artwork-${escapeHtml(artwork.kind)}" value="${artwork.id}" data-artwork-kind="${escapeHtml(artwork.kind)}"${selected.get(artwork.kind) === artwork.id ? ' checked' : ''}><span class="k-item-editor__artwork-card">${image(artwork)}<span class="k-item-editor__artwork-title">${artwork.is_primary ? 'Provider default' : 'Poster variant'}</span><small>${escapeHtml(details(artwork))}</small></span></label>`).join('');
        return `<fieldset class="k-item-editor__artwork-group"><legend>${escapeHtml(kind)}</legend>${automatic}${choices}</fieldset>`;
      }).join('');
    }

    selectedArtworkFromForm(form) {
      return new Map(
        Array.from(form.querySelectorAll('[data-artwork-kind]:checked'))
          .filter((input) => input.value)
          .map((input) => [input.dataset.artworkKind, Number(input.value)])
      );
    }

    async updateKindFields(value) {
      const kind = itemEditorKind(value);
      const content = this.querySelector('[data-item-editor-content]');
      const item = this.currentItem || {};
      const fields = content?.querySelector('[data-item-editor-kind-fields]');
      const locks = content?.querySelector('[data-item-editor-locks]');
      const hierarchy = content?.querySelector('[data-item-editor-hierarchy-fields]');
      const fieldValues = new Map(
        Array.from(content?.querySelectorAll('[data-item-editor-kind-fields] input[name]') || [])
          .filter((input) => typeof input.name === 'string' && typeof input.value === 'string')
          .map((input) => [input.name, input.value])
      );
      const parentInput = content?.querySelector('[data-item-editor-hierarchy-fields] [name="parentId"]');
      const parentId = parentInput && typeof parentInput.value === 'string'
        ? parentInput.value
        : undefined;
      this.lockedMetadataFields = this.lockedMetadataFieldsFromForm(content);
      if (fields) fields.innerHTML = this.renderKindFields(kind, item, fieldValues);
      if (locks) locks.innerHTML = this.renderLockRows(kind, this.lockedMetadataFields);
      this.addVisibleFieldLabels(content);
      const load = ++this.parentChoiceLoad;
      const saveButton = content.querySelector('button[type="submit"]');
      if (!hierarchy) return;
      if (!ITEM_EDITOR_PARENT_KINDS.has(kind)) {
        this.parentChoices = [];
        hierarchy.innerHTML = this.renderHierarchyFields(kind, item, parentId);
        if (saveButton) saveButton.disabled = false;
        return;
      }
      if (saveButton) saveButton.disabled = true;
      hierarchy.innerHTML = '<span class="k-item-editor__muted">Loading eligible parents…</span>';
      try {
        const choices = await this.loadParentChoices(kind);
        if (load !== this.parentChoiceLoad) return;
        this.parentChoices = choices;
        hierarchy.innerHTML = this.renderHierarchyFields(kind, item, parentId);
        if (saveButton) saveButton.disabled = false;
      } catch (_) {
        if (load !== this.parentChoiceLoad) return;
        this.parentChoices = [];
        hierarchy.innerHTML = `${this.renderHierarchyFields(kind, item, parentId)}<span class="k-item-editor__muted">Eligible parents could not be loaded.</span>`;
        if (saveButton) saveButton.disabled = false;
      }
    }

    async loadParentChoices(kind) {
      const source = this.getAttribute('parent-choices-source');
      if (!source) throw new Error('Missing parent choices source');
      const url = new URL(source, window.location.origin);
      url.searchParams.set('kind', kind);
      const response = await fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
      if (!response.ok) throw new Error('Parent choices request failed');
      const payload = await response.json();
      if (!Array.isArray(payload.parentChoices)) throw new Error('Parent choices response was invalid');
      return payload.parentChoices.map(normaliseItemEditorParentChoice).filter(Boolean);
    }

    bindPlaybackForceControls(content) {
      const pairs = [
        ['defaultAudioStreamIndex', 'forceDefaultAudioStream'],
        ['defaultSubtitleTrackId', 'forceDefaultSubtitleTrack'],
        ['defaultSubtitleFontScalePercent', 'forceDefaultSubtitleFontScale'],
      ];
      pairs.forEach(([choiceName, forceName]) => {
        const choice = content.querySelector(`[name="${choiceName}"]`);
        const force = content.querySelector(`[name="${forceName}"]`);
        const forceControl = force?.closest('.k-check');
        if (!(choice instanceof HTMLSelectElement) || !(force instanceof HTMLInputElement) || !forceControl) return;
        const sync = () => {
          const visible = choice.value !== '';
          forceControl.hidden = !visible;
          force.disabled = !visible;
          if (!visible) force.checked = false;
        };
        choice.addEventListener('change', sync);
        sync();
      });
    }

    lockedMetadataFieldsFromForm(form) {
      const locks = new Set(this.lockedMetadataFields);
      form?.querySelectorAll('input[name="lock"]').forEach((input) => {
        if (typeof input.value !== 'string' || typeof input.checked !== 'boolean') return;
        if (input.checked) locks.add(input.value);
        else locks.delete(input.value);
      });
      return locks;
    }

    async submit(event) {
      event.preventDefault();
      const form = event.currentTarget;
      if (!(form instanceof HTMLFormElement) || !this.status) return;
      const button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      this.isSaving = true;
      this.status.textContent = 'Saving metadata…';
      try {
        const result = await this.saveItemMetadata(form);
        this.status.textContent = `Saved ${result.audit?.changed_fields?.join(', ') || 'metadata'}.`;
        window.setTimeout(() => window.location.reload(), 450);
      } catch (error) {
        this.status.textContent = error?.message || 'Item edit could not be applied.';
        if (button) button.disabled = false;
        this.isSaving = false;
      }
    }

    async saveItemMetadata(form) {
      const source = this.getAttribute('action-source');
      if (!source) throw new Error('Missing item edit action');
      const payload = this.payloadFromForm(form, new FormData(form));
      const response = await fetch(source, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify(payload)
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof result.error === 'string' ? result.error : 'Item edit failed');
      }
      return result;
    }

    payloadFromForm(form, values) {
      const toNullableNumber = (name) => {
        if (!values.has(name)) return undefined;
        const raw = String(values.get(name) || '').trim();
        return raw ? Number(raw) : null;
      };
      const selectedArtwork = Array.from(form.querySelectorAll('[data-artwork-kind]:checked'))
        .filter((input) => input.value)
        .map((input) => ({kind: input.dataset.artworkKind, artworkId: Number(input.value)}));
      const visibleArtworkKinds = new Set(Array.from(form.querySelectorAll('[data-artwork-kind]')).map((input) => input.dataset.artworkKind));
      for (const [kind, artworkId] of this.initialSelectedArtwork.entries()) {
        if (!visibleArtworkKinds.has(kind)) selectedArtwork.push({kind, artworkId});
      }
      const lockedMetadataFields = Array.from(this.lockedMetadataFieldsFromForm(form));
      const payload = {
        title: String(values.get('title') || ''),
        sortTitle: String(values.get('sortTitle') || ''),
        overview: String(values.get('overview') || '').trim() || null,
        releaseDate: String(values.get('releaseDate') || '').trim() || null,
        releaseYear: toNullableNumber('releaseYear'),
        tags: String(values.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean),
        lockedMetadataFields,
        kind: String(values.get('kind') || '')
      };
      const kind = itemEditorKind(payload.kind);
      const current = this.currentItem || {};
      const numberNames = new Set(itemEditorNumberFields(kind).map((field) => field.name));
      if (values.has('seasonNumber')) payload.seasonNumber = toNullableNumber('seasonNumber');
      else if (!numberNames.has('seasonNumber') && current.season_number !== null && current.season_number !== undefined) payload.seasonNumber = null;
      if (values.has('episodeNumber')) payload.episodeNumber = toNullableNumber('episodeNumber');
      else if (!numberNames.has('episodeNumber') && current.episode_number !== null && current.episode_number !== undefined) payload.episodeNumber = null;
      if (values.has('parentId')) payload.parentId = toNullableNumber('parentId');
      else if (!ITEM_EDITOR_PARENT_KINDS.has(kind)) payload.parentId = null;
      if (values.has('defaultAudioStreamIndex')) {
        payload.defaultAudioStreamIndex = toNullableNumber('defaultAudioStreamIndex');
        payload.forceDefaultAudioStream = (
          payload.defaultAudioStreamIndex !== null && values.has('forceDefaultAudioStream')
        );
      }
      if (values.has('defaultSubtitleTrackId')) {
        payload.defaultSubtitleTrackId = String(values.get('defaultSubtitleTrackId') || '') || null;
        payload.forceDefaultSubtitleTrack = (
          payload.defaultSubtitleTrackId !== null && values.has('forceDefaultSubtitleTrack')
        );
      }
      if (values.has('defaultSubtitleTimingOffsetMilliseconds')) {
        payload.defaultSubtitleTimingOffsetMilliseconds = toNullableNumber(
          'defaultSubtitleTimingOffsetMilliseconds'
        );
      }
      if (values.has('defaultSubtitleFontScalePercent')) {
        payload.defaultSubtitleFontScalePercent = toNullableNumber(
          'defaultSubtitleFontScalePercent'
        );
        payload.forceDefaultSubtitleFontScale = (
          payload.defaultSubtitleFontScalePercent !== null
          && values.has('forceDefaultSubtitleFontScale')
        );
      }
      if (visibleArtworkKinds.size) payload.selectedArtwork = selectedArtwork;
      return payload;
    }
  }

  if (!customElements.get('kanvas-item-editor')) customElements.define('kanvas-item-editor', KanvasItemEditor);

  class KanvasPlaybackPlayer extends HTMLElement {
    connectedCallback() {
      const video = this.querySelector('video');
      const status = this.querySelector('.k-player__status');
      const controls = this.querySelector('.k-player__controls');
      const timeline = this.querySelector('[data-player-timeline]');
      const bufferedIndicator = this.querySelector('[data-player-buffered]');
      const currentTime = this.querySelector('[data-player-current-time]');
      const remainingTime = this.querySelector('[data-player-remaining-time]');
      const volume = this.querySelector('[data-player-volume]');
      const contextMenu = this.querySelector('[data-player-context-menu]');
      const audioMenu = this.querySelector('[data-player-audio-menu]');
      const subtitleMenu = this.querySelector('[data-player-subtitle-menu]');
      const subtitleTimingLabel = subtitleMenu?.querySelector('[data-player-subtitle-timing-label]');
      const subtitleFontScaleLabel = subtitleMenu?.querySelector('[data-player-subtitle-font-scale-label]');
      const subtitleAppearance = subtitleMenu?.querySelector('[data-player-subtitle-appearance]');
      const nativeControls = this.querySelector('[data-player-native-controls]');
      const kestrelLink = this.querySelector('[data-player-kestrel]');
      const audioOptions = audioMenu?.querySelector('[data-player-audio-options]');
      const subtitleOptions = subtitleMenu?.querySelector('[data-player-subtitle-options]');
      const subtitleFonts = this.querySelector('[data-player-ass-fonts]');
      const fullscreenTitle = this.querySelector('[data-player-fullscreen-title]');
      const fullscreenSpecialInfo = this.querySelector('[data-player-fullscreen-special-info]');
      const fullscreenTime = this.querySelector('[data-player-fullscreen-time]');
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
      if (!video || !status || !controls || !timeline || !bufferedIndicator || !currentTime || !remainingTime || !volume || !contextMenu || !audioMenu || !subtitleMenu || !subtitleTimingLabel || !subtitleFontScaleLabel || !subtitleAppearance || !nativeControls || !fullscreenTitle || !fullscreenSpecialInfo || !fullscreenTime || !sessionId || !Number.isSafeInteger(entryPosition) || entryPosition < 0 || !Number.isFinite(resumePosition) || !Number.isSafeInteger(subtitleTimingOffsetMilliseconds) || Math.abs(subtitleTimingOffsetMilliseconds) > 30000 || !Number.isSafeInteger(subtitleFontScalePercent) || subtitleFontScalePercent < 75 || subtitleFontScalePercent > 200 || subtitleFontScalePercent % 25 !== 0 || !['author', 'top', 'middle', 'bottom'].includes(subtitleVerticalPosition)) return;
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
      let fullscreenHideTimer = null;
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
      const updatePlaybackQueue = () => {
        if (typeof document.querySelector !== 'function') return;
        const queue = document.querySelector('[data-player-queue]');
        if (!(queue instanceof Element)) {
          if (fullscreenQueueNext instanceof Element) fullscreenQueueNext.hidden = true;
          return;
        }
        const queueEntries = Array.from(queue.querySelectorAll('.k-playback-queue__entry'));
        queueEntries[0]?.remove();
        const remainingEntries = Array.from(queue.querySelectorAll('.k-playback-queue__entry'));
        if (remainingEntries.length === 0) {
          queue.remove();
          if (fullscreenQueueNext instanceof Element) fullscreenQueueNext.hidden = true;
          return;
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
      };
      const setQueueNextBusy = (busy) => {
        queueNextControls.forEach((control) => {
          control.toggleAttribute('disabled', busy);
          control.setAttribute('aria-disabled', String(busy));
        });
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
      const closeTrackMenus = () => {
        audioMenu.hidden = true;
        subtitleMenu.hidden = true;
      };
      const showTrackMenu = (menu, target) => {
        const bounds = this.getBoundingClientRect();
        const targetBounds = target.getBoundingClientRect();
        contextMenu.hidden = true;
        closeTrackMenus();
        menu.hidden = false;
        menu.style.left = `${Math.max(8, Math.min(targetBounds.left - bounds.left, bounds.width - 210))}px`;
        menu.style.top = `${Math.max(8, targetBounds.top - bounds.top - 8)}px`;
        showFullscreenControls();
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
      const actionButton = (action) => controls.querySelector(`[data-player-action="${action}"]`);
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
        const toggle = actionButton('toggle');
        if (toggle) {
          toggle.innerHTML = video.paused ? '&#9654;' : '&#10074;&#10074;';
          toggle.setAttribute('aria-label', video.paused ? 'Play' : 'Pause');
        }
        const mute = actionButton('mute');
        if (mute) {
          mute.innerHTML = video.muted || video.volume === 0 ? '&#128263;' : '&#128266;';
          mute.setAttribute('aria-label', video.muted || video.volume === 0 ? 'Unmute' : 'Mute');
        }
        const fullscreen = actionButton('fullscreen');
        if (fullscreen) {
          const isFullscreen = document.fullscreenElement === this || document.fullscreenElement === video;
          fullscreen.innerHTML = isFullscreen ? '&#10005;' : '&#9974;';
          fullscreen.setAttribute('aria-label', isFullscreen ? 'Exit fullscreen' : 'Fullscreen');
        }
        contextMenu.querySelectorAll('[data-player-rate]').forEach((option) => {
          const rate = Number(option.getAttribute('data-player-rate'));
          option.setAttribute('aria-pressed', String(Math.abs(rate - video.playbackRate) < 0.01));
        });
        volume.value = String(video.muted ? 0 : video.volume);
        volume.style.setProperty('--volume-percent', `${video.muted ? 0 : video.volume * 100}%`);
        updateTrackOptions();
      };
      const isCardFullscreen = () => document.fullscreenElement === this;
      const isPlayerFullscreen = () => (
        webkitFullscreenActive || document.fullscreenElement === this || document.fullscreenElement === video
      );
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
      const clearFullscreenHideTimer = () => {
        if (fullscreenHideTimer !== null) window.clearTimeout(fullscreenHideTimer);
        fullscreenHideTimer = null;
      };
      const showFullscreenControls = () => {
        if (!isCardFullscreen()) return;
        this.classList.remove('k-player--controls-hidden');
        clearFullscreenHideTimer();
        if (!video.paused && contextMenu.hidden) {
          fullscreenHideTimer = window.setTimeout(() => {
            if (isCardFullscreen() && !video.paused && contextMenu.hidden) {
              this.classList.add('k-player--controls-hidden');
            }
          }, 2600);
        }
      };
      const hideContextMenu = () => {
        contextMenu.hidden = true;
        closeTrackMenus();
        showFullscreenControls();
      };
      const showContextMenu = (clientX, clientY) => {
        const bounds = this.getBoundingClientRect();
        contextMenu.hidden = false;
        showFullscreenControls();
        contextMenu.style.left = `${Math.max(8, Math.min(clientX - bounds.left, bounds.width - 210))}px`;
        contextMenu.style.top = `${Math.max(8, clientY - bounds.top)}px`;
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
      controls.addEventListener('click', (event) => {
        showFullscreenControls();
        const element = event.target instanceof Element ? event.target : null;
        const target = element?.closest('[data-player-action]');
        if (!target) return;
        const action = target.getAttribute('data-player-action');
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
        } else if (action === 'menu') {
          const bounds = target.getBoundingClientRect();
          showContextMenu(bounds.left + bounds.width / 2, bounds.bottom);
        } else if (action === 'audio') {
          showTrackMenu(audioMenu, target);
        } else if (action === 'subtitles') {
          showTrackMenu(subtitleMenu, target);
        } else if (action === 'mute') {
          video.muted = !video.muted;
        } else if (action === 'next') {
          void completeAndAdvancePlayback();
        } else if (action === 'fullscreen') {
          void toggleFullscreen();
        }
        updateControls();
      });
      audioMenu.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null;
        const option = element?.closest('[data-player-audio-stream]');
        const audioStream = Number(option?.getAttribute('data-player-audio-stream'));
        if (!Number.isSafeInteger(audioStream) || audioStream < 0 || audioStream === selectedAudioStream) {
          closeTrackMenus();
          return;
        }
        const position = playbackPosition();
        const autoplay = !video.paused;
        closeTrackMenus();
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
          closeTrackMenus();
          return;
        }
        const unsupported = option.hasAttribute('data-player-subtitle-unsupported');
        closeTrackMenus();
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
        hideContextMenu();
      });
      timeline.addEventListener('input', () => {
        showFullscreenControls();
        const position = Number(timeline.value);
        if (!Number.isFinite(position)) return;
        if (deliveryMode !== 'direct') return;
        video.currentTime = position;
        updateControls();
      });
      timeline.addEventListener('change', () => {
        if (deliveryMode === 'direct') return;
        const position = Number(timeline.value);
        if (Number.isFinite(position)) void restartGeneratedStream(position);
      });
      volume.addEventListener('input', () => {
        showFullscreenControls();
        const nextVolume = Number(volume.value);
        if (!Number.isFinite(nextVolume)) return;
        video.volume = Math.min(Math.max(nextVolume, 0), 1);
        video.muted = video.volume === 0;
        updateControls();
      });
      nativeControls.addEventListener('change', () => {
        video.controls = nativeControls.checked;
        hideContextMenu();
      });
      this.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        showContextMenu(event.clientX, event.clientY);
      });
      this.addEventListener('pointermove', showFullscreenControls);
      this.addEventListener('pointerdown', showFullscreenControls);
      this.addEventListener('touchstart', showFullscreenControls, {passive: true});
      this.addEventListener('keydown', showFullscreenControls);
      this.addEventListener('focusin', showFullscreenControls);
      const onPointerDown = (event) => {
        if (!contextMenu.contains(event.target) && !audioMenu.contains(event.target) && !subtitleMenu.contains(event.target)) hideContextMenu();
      };
      document.addEventListener('pointerdown', onPointerDown);
      this._dispose = () => {
        invalidatePlaybackAttempts();
        clearFullscreenHideTimer();
        clearFullscreenClock();
        document.removeEventListener('pointerdown', onPointerDown);
        document.removeEventListener('fullscreenchange', onFullscreenChange);
        video.removeEventListener('webkitbeginfullscreen', onWebkitBeginFullscreen);
        video.removeEventListener('webkitendfullscreen', onWebkitEndFullscreen);
        if (queueNext instanceof Element) queueNext.removeEventListener('click', onQueueNext);
        window.removeEventListener('pagehide', flushProgressOnPageHide);
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
        status.textContent = '';
        updateControls();
        if (generatedStreamSeekPending) {
          generatedStreamSeekPending = false;
          void reportProgress(true, true);
        }
      });
      video.addEventListener('play', () => {
        clearSelectPlayStatus();
        updateControls();
        showFullscreenControls();
      });
      video.addEventListener('playing', () => { streamRecoveryAttemptCount = 0; });
      video.addEventListener('pause', () => {
        updateControls();
        showFullscreenControls();
      });
      video.addEventListener('ratechange', updateControls);
      video.addEventListener('volumechange', updateControls);
      const onFullscreenChange = () => {
        updateControls();
        synchroniseFullscreenClock();
        if (isCardFullscreen()) showFullscreenControls();
        else {
          clearFullscreenHideTimer();
          this.classList.remove('k-player--controls-hidden');
        }
        if (!isPlayerFullscreen()) void navigateToPendingItemPage();
      };
      document.addEventListener('fullscreenchange', onFullscreenChange);
      const onWebkitBeginFullscreen = () => {
        webkitFullscreenActive = true;
        updateControls();
      };
      const onWebkitEndFullscreen = () => {
        webkitFullscreenActive = false;
        updateControls();
        void navigateToPendingItemPage();
      };
      video.addEventListener('webkitbeginfullscreen', onWebkitBeginFullscreen);
      video.addEventListener('webkitendfullscreen', onWebkitEndFullscreen);
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
      const completeAndAdvancePlayback = async () => {
        if (completing) return;
        completing = true;
        setQueueNextBusy(true);
        status.textContent = 'Completing playback…';
        try {
          const response = await fetch(`/kanvas/playback/sessions/${encodeURIComponent(sessionId)}/complete`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({entryPosition}),
          });
          const payload = await response.json();
          if (!response.ok) throw new Error('Completion failed');
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
            updatePlaybackQueue();
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
      const onQueueNext = (event) => {
        event.preventDefault();
        event.stopPropagation();
        void completeAndAdvancePlayback();
      };
      if (queueNext instanceof Element) queueNext.addEventListener('click', onQueueNext);
      video.addEventListener('ended', () => {
        void completeAndAdvancePlayback();
      });
      window.addEventListener('pagehide', flushProgressOnPageHide);
      updateControls();
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

  window.kanvasInternals = {
    escapeHtml,
    jobDetail,
    tmdbEntryReferenceFromUrl,
    providerEntryUrl,
  };

})();
