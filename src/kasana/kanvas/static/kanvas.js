(() => {
  'use strict';

  const MAX_MOUNTED_POSTERS = 144;
  const LIBRARY_VIRTUAL_OVERSCAN_PX = 960;
  const GRID_ROW_TOP_TOLERANCE_PX = 1;
  const GRID_TRIM_VIEWPORT_BUFFER_PX = 0;
  const LIBRARY_FILTER_INPUT_DELAY_MS = 260;
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

  const libraryFilterUrl = (action, values) => {
    const url = new URL(action, window.location.origin);
    url.search = '';
    for (const [name, rawValue] of values) {
      const value = String(rawValue).trim();
      if (value) url.searchParams.append(name, value);
    }
    return `${url.pathname}${url.search}${url.hash}`;
  };

  const libraryFilterTimers = new WeakMap();
  const clearLibraryFilterTimer = (form) => {
    const timer = libraryFilterTimers.get(form);
    if (timer !== undefined) window.clearTimeout(timer);
    libraryFilterTimers.delete(form);
  };

  const submitLibraryFilters = (form) => {
    clearLibraryFilterTimer(form);
    const action = form.getAttribute('action') || window.location.pathname;
    window.location.assign(libraryFilterUrl(action, new FormData(form)));
  };

  const scheduleLibraryFilterSubmit = (form, delay) => {
    clearLibraryFilterTimer(form);
    libraryFilterTimers.set(form, window.setTimeout(() => submitLibraryFilters(form), delay));
  };

  const libraryFilterForm = (target) => target instanceof Element
    ? target.closest('form[data-kanvas-library-filters="true"]')
    : null;

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-kanvas-library-filters')) return;
    event.preventDefault();
    submitLibraryFilters(form);
  });

  document.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return;
    const form = libraryFilterForm(target);
    if (!(form instanceof HTMLFormElement)) return;
    const delay = target instanceof HTMLInputElement && target.type === 'checkbox'
      ? LIBRARY_FILTER_INPUT_DELAY_MS
      : 0;
    scheduleLibraryFilterSubmit(form, delay);
  });

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);

  class KanvasConfirmationDialog extends HTMLElement {
    constructor() {
      super();
      this.dialog = null;
      this.resolve = null;
    }

    connectedCallback() {
      if (this.dialog) return;
      this.innerHTML = `<dialog class="k-kanvas-dialog k-confirmation-dialog" data-kanvas-confirmation-dialog>
        <form method="dialog" class="k-picker k-confirmation-dialog__content">
          <div class="k-picker__header"><strong data-kanvas-confirmation-title></strong></div>
          <p class="k-confirmation-dialog__message" data-kanvas-confirmation-message></p>
          <div class="k-action-row k-confirmation-dialog__actions">
            <button type="submit" class="k-button" value="cancel">Cancel</button>
            <button type="submit" class="k-button k-button--primary" value="confirm" data-kanvas-confirmation-accept>Confirm</button>
          </div>
        </form>
      </dialog>`;
      this.dialog = this.querySelector('[data-kanvas-confirmation-dialog]');
      this.dialog?.addEventListener('close', () => {
        this.finish(this.dialog?.returnValue === 'confirm');
      });
    }

    disconnectedCallback() {
      this.finish(false);
    }

    request({title, message, confirmLabel = 'Confirm', destructive = false}) {
      if (!(this.dialog instanceof HTMLDialogElement) || this.dialog.open || this.resolve) {
        return Promise.resolve(false);
      }
      const titleElement = this.dialog.querySelector('[data-kanvas-confirmation-title]');
      const messageElement = this.dialog.querySelector('[data-kanvas-confirmation-message]');
      const confirmButton = this.dialog.querySelector('[data-kanvas-confirmation-accept]');
      if (!titleElement || !messageElement || !confirmButton) return Promise.resolve(false);
      titleElement.textContent = String(title || 'Confirm action');
      messageElement.textContent = String(message || 'This action cannot be undone.');
      confirmButton.textContent = String(confirmLabel || 'Confirm');
      confirmButton.classList.toggle('k-button--danger', Boolean(destructive));
      return new Promise((resolve) => {
        this.resolve = resolve;
        try {
          this.dialog.returnValue = '';
          this.dialog.showModal();
        } catch (_) {
          this.finish(false);
        }
      });
    }

    finish(confirmed) {
      const resolve = this.resolve;
      this.resolve = null;
      resolve?.(confirmed);
    }
  }

  const requestKanvasConfirmation = (element, options) => (
    element instanceof KanvasConfirmationDialog ? element.request(options) : Promise.resolve(false)
  );

  if (!customElements.get('kanvas-confirmation-dialog')) {
    customElements.define('kanvas-confirmation-dialog', KanvasConfirmationDialog);
  }

  const TOAST_EVENT = 'kanvas:toast';
  const TOAST_CONSUME_EVENT = 'kanvas:consume-toasts';
  const TOAST_MAX_VISIBLE = 4;
  const TOAST_SEVERITIES = new Set(['success', 'info', 'warning', 'error']);
  const TOAST_TIMEOUTS_MS = Object.freeze({
    success: 5_000,
    info: 6_000,
    warning: 8_000,
    error: null,
  });
  const normaliseToastText = (value, maximumLength) => {
    if (typeof value !== 'string') return null;
    const text = value.trim();
    return text && text.length <= maximumLength ? text : null;
  };
  const normaliseToast = (value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const severity = typeof value.severity === 'string' && TOAST_SEVERITIES.has(value.severity)
      ? value.severity
      : null;
    const title = normaliseToastText(value.title, 120);
    const detail = value.detail == null ? null : normaliseToastText(value.detail, 400);
    return severity && title && (value.detail == null || detail)
      ? {severity, title, detail}
      : null;
  };
  const publishKanvasToast = (value) => {
    const toast = normaliseToast(value);
    if (!toast) return false;
    window.dispatchEvent(new CustomEvent(TOAST_EVENT, {detail: toast}));
    return true;
  };
  const consumeQueuedKanvasToasts = () => {
    window.dispatchEvent(new Event(TOAST_CONSUME_EVENT));
  };
  window.kanvas = window.kanvas || {};
  window.kanvas.toast = publishKanvasToast;
  window.kanvas.consumeToasts = consumeQueuedKanvasToasts;

  const actionErrorDetail = async (response) => {
    const payload = await response.json().catch(() => null);
    return normaliseToastText(payload?.error, 400)
      || normaliseToastText(payload?.detail, 400)
      || 'Try again in a moment.';
  };

  const kanvasActionSubmission = (form, submitter) => {
    const submitterAction = submitter instanceof HTMLElement ? submitter.getAttribute('formaction') : null;
    const submitterMethod = submitter instanceof HTMLElement ? submitter.getAttribute('formmethod') : null;
    let action;
    try {
      action = new URL(submitterAction || form.action, window.location.origin);
    } catch (_) {
      return null;
    }
    const method = (submitterMethod || form.method || 'POST').toUpperCase();
    return action.origin === window.location.origin && method === 'POST' ? {action, method} : null;
  };

  const isKanvasActionSubmitter = (value) => (
    value instanceof HTMLButtonElement || value instanceof HTMLInputElement
  );

  const submitKanvasActionForm = async (form, submitter, submission = kanvasActionSubmission(form, submitter)) => {
    if (!submission) return;
    const {action, method} = submission;
    const formData = isKanvasActionSubmitter(submitter)
      ? new FormData(form, submitter)
      : new FormData(form);
    form.dataset.kanvasSubmitting = 'true';
    form.setAttribute('aria-busy', 'true');
    if (isKanvasActionSubmitter(submitter)) submitter.disabled = true;
    try {
      const response = await fetch(action, {
        method,
        body: formData,
        credentials: 'same-origin',
      });
      if (!response.ok) {
        publishKanvasToast({
          severity: 'error',
          title: 'Could not save changes',
          detail: await actionErrorDetail(response),
        });
        return;
      }
      const destination = new URL(response.url || action, window.location.origin);
      if (destination.origin !== window.location.origin) throw new Error('Unexpected action destination.');
      window.location.assign(destination.href);
    } catch (_) {
      publishKanvasToast({
        severity: 'error',
        title: 'Could not save changes',
        detail: 'Check your connection and try again.',
      });
    } finally {
      delete form.dataset.kanvasSubmitting;
      form.removeAttribute('aria-busy');
      if (isKanvasActionSubmitter(submitter)) submitter.disabled = false;
    }
  };

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-kanvas-action-form')) return;
    if (form.dataset.kanvasSubmitting === 'true') {
      event.preventDefault();
      return;
    }
    const submission = kanvasActionSubmission(form, event.submitter);
    event.preventDefault();
    if (!submission) {
      publishKanvasToast({
        severity: 'error',
        title: 'Could not submit form',
        detail: 'This action is unavailable. Reload the page and try again.',
      });
      return;
    }
    void submitKanvasActionForm(form, event.submitter, submission);
  });

  class KanvasToasts extends HTMLElement {
    constructor() {
      super();
      this.toasts = [];
      this.nextId = 0;
      this.timers = new Map();
      this.consuming = false;
      this.consumeAgain = false;
      this.consumeAbort = null;
      this.consumeRequest = 0;
      this.onToast = (event) => this.add(event.detail);
      this.onConsume = () => this.requestConsumption();
    }

    connectedCallback() {
      this.hidden = true;
      this.setAttribute('aria-label', 'Notifications');
      window.addEventListener(TOAST_EVENT, this.onToast);
      window.addEventListener(TOAST_CONSUME_EVENT, this.onConsume);
      void this.consumeInitialToasts();
    }

    disconnectedCallback() {
      window.removeEventListener(TOAST_EVENT, this.onToast);
      window.removeEventListener(TOAST_CONSUME_EVENT, this.onConsume);
      this.consumeRequest += 1;
      this.consumeAbort?.abort();
      this.consumeAbort = null;
      this.consuming = false;
      this.consumeAgain = false;
      for (const timer of this.timers.values()) window.clearTimeout(timer);
      this.timers.clear();
      this.toasts = [];
    }

    async consumeInitialToasts() {
      const source = this.getAttribute('source');
      if (!source || this.consuming) return;
      const controller = new AbortController();
      const request = ++this.consumeRequest;
      this.consumeAbort = controller;
      this.consuming = true;
      try {
        const response = await fetch(source, {
          method: 'POST',
          headers: {'Accept': 'application/json'},
          credentials: 'same-origin',
          signal: controller.signal,
        });
        if (request !== this.consumeRequest || !this.isConnected) return;
        if (response.status === 401) {
          window.location.assign('/profiles');
          return;
        }
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload || typeof payload !== 'object' || !Array.isArray(payload.toasts)) {
          return;
        }
        for (const toast of payload.toasts) this.add(toast);
      } catch (_) {
        // Shell-level connectivity alerts already explain a failed dashboard request.
      } finally {
        if (request !== this.consumeRequest) return;
        this.consumeAbort = null;
        this.consuming = false;
        if (this.consumeAgain && this.isConnected) {
          this.consumeAgain = false;
          void this.consumeInitialToasts();
        }
      }
    }

    requestConsumption() {
      if (this.consuming) {
        this.consumeAgain = true;
        return;
      }
      void this.consumeInitialToasts();
    }

    add(value) {
      const toast = normaliseToast(value);
      if (!toast) return false;
      const signature = JSON.stringify(toast);
      const existing = this.toasts.find((entry) => entry.signature === signature);
      if (existing) {
        this.scheduleDismissal(existing);
        return true;
      }
      while (this.toasts.length >= TOAST_MAX_VISIBLE) this.dismiss(this.toasts[0]?.id);
      const entry = {id: ++this.nextId, signature, ...toast};
      this.toasts.push(entry);
      this.render();
      this.scheduleDismissal(entry);
      return true;
    }

    dismiss(id) {
      if (!Number.isInteger(id)) return;
      const index = this.toasts.findIndex((toast) => toast.id === id);
      if (index < 0) return;
      const timer = this.timers.get(id);
      if (timer !== undefined) window.clearTimeout(timer);
      this.timers.delete(id);
      this.toasts.splice(index, 1);
      this.render();
    }

    scheduleDismissal(toast) {
      const timeout = TOAST_TIMEOUTS_MS[toast.severity];
      if (timeout === null) return;
      const existingTimer = this.timers.get(toast.id);
      if (existingTimer !== undefined) window.clearTimeout(existingTimer);
      this.timers.set(toast.id, window.setTimeout(() => this.dismiss(toast.id), timeout));
    }

    render() {
      this.hidden = this.toasts.length === 0;
      this.replaceChildren();
      for (const toast of this.toasts) {
        const item = document.createElement('section');
        item.className = `k-toast k-toast--${toast.severity}`;
        item.setAttribute('role', toast.severity === 'error' ? 'alert' : 'status');
        item.setAttribute('aria-live', toast.severity === 'error' ? 'assertive' : 'polite');
        const message = document.createElement('div');
        message.className = 'k-toast__message';
        const title = document.createElement('strong');
        title.className = 'k-toast__title';
        title.textContent = toast.title;
        message.append(title);
        if (toast.detail) {
          const detail = document.createElement('span');
          detail.className = 'k-toast__detail';
          detail.textContent = toast.detail;
          message.append(detail);
        }
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'k-icon-action k-toast__close';
        close.textContent = '×';
        close.setAttribute('aria-label', `Dismiss ${toast.title}`);
        close.addEventListener('click', () => this.dismiss(toast.id));
        item.append(message, close);
        this.append(item);
      }
    }
  }

  if (!customElements.get('kanvas-toasts')) {
    customElements.define('kanvas-toasts', KanvasToasts);
  }

  const SYSTEM_ALERT_REFRESH_MS = 30_000;
  const SYSTEM_ALERT_DEGRADED_REFRESH_MS = 10_000;
  const SYSTEM_ALERT_REQUEST_TIMEOUT_MS = 15_000;
  const SYSTEM_ALERT_SEVERITIES = new Set(['info', 'warning', 'error']);
  const SYSTEM_ALERT_CODES = new Set([
    'browser_offline',
    'kanvas_unavailable',
    'katalog_unavailable',
    'database_unhealthy',
    'library_root_unavailable',
    'maintenance_jobs_failed'
  ]);
  const DURABLE_SYSTEM_ALERT_CODES = new Set([
    'database_unhealthy',
    'library_root_unavailable',
    'maintenance_jobs_failed'
  ]);
  const SYSTEM_ALERT_ACTION_KINDS = new Set(['navigate', 'retry']);
  const safeSystemAlertHref = (value) => (
    typeof value === 'string'
    && value.startsWith('/')
    && !value.startsWith('//')
    && !/[\\\s]/.test(value)
    && value.length <= 500
      ? value
      : null
  );
  const normaliseSystemAlertText = (value, maximumLength) => {
    if (typeof value !== 'string') return null;
    const text = value.trim();
    return text && text.length <= maximumLength ? text : null;
  };
  const normaliseSystemIncidentId = (value) => (
    Number.isInteger(value) && value > 0 ? value : null
  );
  const normaliseSystemAlertTimestamp = (value) => (
    typeof value === 'string' && value.length <= 80 && !Number.isNaN(Date.parse(value))
      ? value
      : null
  );
  const normaliseSystemAlertAction = (value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const kind = typeof value.kind === 'string' ? value.kind : null;
    const label = normaliseSystemAlertText(value.label, 80);
    if (!kind || !SYSTEM_ALERT_ACTION_KINDS.has(kind) || !label) return null;
    if (kind === 'retry') return value.href == null ? {kind, label} : null;
    const href = safeSystemAlertHref(value.href);
    return href ? {kind, label, href} : null;
  };
  const normaliseSystemAlert = (value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const id = typeof value.id === 'string' && /^[a-z][a-z0-9_-]{0,99}$/.test(value.id)
      ? value.id
      : null;
    const code = typeof value.code === 'string' && SYSTEM_ALERT_CODES.has(value.code)
      ? value.code
      : null;
    const severity = typeof value.severity === 'string' && SYSTEM_ALERT_SEVERITIES.has(value.severity)
      ? value.severity
      : null;
    const title = normaliseSystemAlertText(value.title, 160);
    const detail = normaliseSystemAlertText(value.detail, 500);
    const action = normaliseSystemAlertAction(value.action);
    const incidentId = value.incidentId == null ? null : normaliseSystemIncidentId(value.incidentId);
    const acknowledgedAt = value.acknowledgedAt == null
      ? null
      : normaliseSystemAlertTimestamp(value.acknowledgedAt);
    if (
      (value.incidentId != null && !incidentId)
      || (value.acknowledgedAt != null && !acknowledgedAt)
      || (acknowledgedAt && !incidentId)
      || (incidentId && !DURABLE_SYSTEM_ALERT_CODES.has(code))
    ) return null;
    return id && code && severity && title && detail && action
      ? {
          id,
          code,
          severity,
          title,
          detail,
          action,
          ...(incidentId ? {incidentId} : {}),
          ...(acknowledgedAt ? {acknowledgedAt} : {})
        }
      : null;
  };
  const normaliseSystemAlertHistory = (value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const incidentId = normaliseSystemIncidentId(value.incidentId);
    const code = typeof value.code === 'string' && DURABLE_SYSTEM_ALERT_CODES.has(value.code)
      ? value.code
      : null;
    const severity = typeof value.severity === 'string' && SYSTEM_ALERT_SEVERITIES.has(value.severity)
      ? value.severity
      : null;
    const title = normaliseSystemAlertText(value.title, 160);
    const detail = normaliseSystemAlertText(value.detail, 500);
    const firstDetectedAt = normaliseSystemAlertTimestamp(value.firstDetectedAt);
    const lastDetectedAt = normaliseSystemAlertTimestamp(value.lastDetectedAt);
    const resolvedAt = normaliseSystemAlertTimestamp(value.resolvedAt);
    const acknowledgedAt = value.acknowledgedAt == null
      ? null
      : normaliseSystemAlertTimestamp(value.acknowledgedAt);
    if (
      !incidentId
      || !code
      || !severity
      || !title
      || !detail
      || !firstDetectedAt
      || !lastDetectedAt
      || !resolvedAt
      || (value.acknowledgedAt != null && !acknowledgedAt)
    ) return null;
    return {
      incidentId,
      code,
      severity,
      title,
      detail,
      firstDetectedAt,
      lastDetectedAt,
      resolvedAt,
      ...(acknowledgedAt ? {acknowledgedAt} : {})
    };
  };
  const systemAlertTimeLabel = (value) => {
    const timestamp = new Date(value);
    return Number.isNaN(timestamp.getTime()) ? 'at an unknown time' : timestamp.toLocaleString();
  };
  const browserOfflineAlert = () => ({
    id: 'browser-offline',
    code: 'browser_offline',
    severity: 'error',
    title: 'Connection lost',
    detail: 'This device is offline. Kanvas will reconnect when the network returns.',
    action: {kind: 'retry', label: 'Retry'}
  });
  const kanvasUnavailableAlert = () => ({
    id: 'kanvas-unavailable',
    code: 'kanvas_unavailable',
    severity: 'error',
    title: 'Kanvas is unreachable',
    detail: 'The dashboard cannot be reached right now. Try again in a moment.',
    action: {kind: 'retry', label: 'Retry'}
  });

  class KanvasSystemAlerts extends HTMLElement {
    constructor() {
      super();
      this.alerts = [];
      this.history = [];
      this.signature = '';
      this.loading = false;
      this.loadAgain = false;
      this.acknowledgingIncidentIds = new Set();
      this.timer = null;
      this.abort = null;
      this.drawer = null;
      this.onVisibilityChange = () => this.visibilityChanged();
      this.onOnline = () => this.load();
      this.onOffline = () => this.offline();
    }

    connectedCallback() {
      this.hidden = true;
      document.addEventListener('visibilitychange', this.onVisibilityChange);
      window.addEventListener('online', this.onOnline);
      window.addEventListener('offline', this.onOffline);
      void this.load();
    }

    disconnectedCallback() {
      document.removeEventListener('visibilitychange', this.onVisibilityChange);
      window.removeEventListener('online', this.onOnline);
      window.removeEventListener('offline', this.onOffline);
      this.stopPolling();
      this.abort?.abort();
      this.abort = null;
      this.loadAgain = false;
    }

    source() {
      return this.getAttribute('source');
    }

    acknowledgementSource() {
      return safeSystemAlertHref(this.getAttribute('acknowledgement-source'));
    }

    visibilityChanged() {
      if (document.visibilityState === 'hidden') {
        this.stopPolling();
        return;
      }
      void this.load();
    }

    offline() {
      this.abort?.abort();
      this.loadAgain = false;
      this.applyFeed([browserOfflineAlert()], []);
      this.stopPolling();
    }

    async load() {
      if (this.loading) {
        this.loadAgain = true;
        return;
      }
      if (!this.isConnected || document.visibilityState === 'hidden') return;
      if (navigator.onLine === false) {
        this.applyFeed([browserOfflineAlert()], []);
        this.stopPolling();
        return;
      }
      const source = this.source();
      if (!source) return;
      const controller = new AbortController();
      let timedOut = false;
      const timeout = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, SYSTEM_ALERT_REQUEST_TIMEOUT_MS);
      this.abort = controller;
      this.loading = true;
      try {
        const response = await fetch(source, {
          headers: {'Accept': 'application/json'},
          credentials: 'same-origin',
          signal: controller.signal,
        });
        if (response.status === 401) {
          window.location.assign('/profiles');
          return;
        }
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload || typeof payload !== 'object') {
          throw new Error('System alert request failed.');
        }
        const alerts = Array.isArray(payload.alerts)
          ? payload.alerts.map(normaliseSystemAlert).filter(Boolean)
          : null;
        const history = payload.history == null
          ? []
          : (Array.isArray(payload.history)
            ? payload.history.map(normaliseSystemAlertHistory).filter(Boolean)
            : null);
        if (alerts === null || history === null) throw new Error('System alert payload was invalid.');
        this.applyFeed(alerts, history);
      } catch (error) {
        if (error?.name !== 'AbortError' || timedOut) {
          this.applyFeed([
            navigator.onLine === false ? browserOfflineAlert() : kanvasUnavailableAlert()
          ], []);
        }
      } finally {
        window.clearTimeout(timeout);
        if (this.abort === controller) this.abort = null;
        this.loading = false;
        if (
          this.loadAgain
          && this.isConnected
          && document.visibilityState !== 'hidden'
          && navigator.onLine !== false
        ) {
          this.loadAgain = false;
          void this.load();
          return;
        }
        this.loadAgain = false;
        this.schedulePolling();
      }
    }

    schedulePolling() {
      this.stopPolling();
      if (
        !this.isConnected ||
        document.visibilityState === 'hidden' ||
        navigator.onLine === false
      ) return;
      const degraded = this.alerts.some((alert) => alert.severity === 'error');
      this.timer = window.setTimeout(
        () => void this.load(),
        degraded ? SYSTEM_ALERT_DEGRADED_REFRESH_MS : SYSTEM_ALERT_REFRESH_MS
      );
    }

    stopPolling() {
      if (this.timer !== null) window.clearTimeout(this.timer);
      this.timer = null;
    }

    applyAlerts(alerts) {
      this.applyFeed(alerts, []);
    }

    applyFeed(alerts, history) {
      const signature = JSON.stringify({alerts, history});
      if (signature === this.signature) return;
      this.alerts = alerts;
      this.history = history;
      this.signature = signature;
      this.render();
    }

    render() {
      const previousDrawer = this.drawer;
      const restoreDrawer = previousDrawer instanceof HTMLDialogElement && previousDrawer.open;
      if (restoreDrawer) previousDrawer.close();
      if (!this.alerts.length && !this.history.length) {
        this.hidden = true;
        this.drawer = null;
        this.replaceChildren();
        return;
      }
      this.hidden = false;
      const drawer = this.drawerElement();
      if (!this.alerts.length) {
        const historyBar = document.createElement('section');
        historyBar.className = 'k-system-alerts__history-bar';
        const message = document.createElement('div');
        message.className = 'k-system-alerts__message';
        const title = document.createElement('strong');
        title.className = 'k-system-alerts__title';
        title.textContent = 'No active system issues';
        const detail = document.createElement('span');
        detail.className = 'k-system-alerts__detail';
        detail.textContent = `${this.history.length} recovered ${this.history.length === 1 ? 'condition' : 'conditions'} retained.`;
        message.append(title, detail);
        const drawerButton = this.drawerButton('System history');
        historyBar.append(message, drawerButton);
        this.replaceChildren(historyBar, drawer);
        if (restoreDrawer) this.openDrawer();
        return;
      }
      const primary = this.alerts.find((alert) => alert.severity === 'error') || this.alerts[0];
      const banner = document.createElement('section');
      banner.className = `k-system-alerts__banner k-system-alerts__banner--${primary.severity}`;
      banner.setAttribute('role', primary.severity === 'error' ? 'alert' : 'status');
      banner.setAttribute('aria-live', primary.severity === 'error' ? 'assertive' : 'polite');

      const message = document.createElement('div');
      message.className = 'k-system-alerts__message';
      const title = document.createElement('strong');
      title.className = 'k-system-alerts__title';
      title.textContent = primary.title;
      const detail = document.createElement('span');
      detail.className = 'k-system-alerts__detail';
      detail.textContent = primary.detail;
      message.append(title, detail);
      const primaryAction = this.actionElement(primary.action);
      primaryAction.classList.add('k-system-alerts__primary-action');
      banner.append(message, primaryAction);

      const drawerButton = this.drawerButton(
        this.alerts.length === 1 ? 'Attention' : `Attention (${this.alerts.length})`
      );
      banner.append(drawerButton);

      this.replaceChildren(banner, drawer);
      if (restoreDrawer) this.openDrawer();
    }

    drawerButton(label) {
      const drawerButton = document.createElement('button');
      drawerButton.type = 'button';
      drawerButton.className = 'k-button k-system-alerts__drawer-button';
      drawerButton.textContent = label;
      drawerButton.setAttribute('aria-haspopup', 'dialog');
      drawerButton.addEventListener('click', () => this.openDrawer());
      return drawerButton;
    }

    drawerElement() {
      const drawer = document.createElement('dialog');
      drawer.className = 'k-kanvas-dialog k-system-alerts__drawer';
      const drawerTitleText = this.alerts.length ? 'System attention' : 'System history';
      drawer.setAttribute('aria-label', drawerTitleText);
      const drawerContent = document.createElement('section');
      drawerContent.className = 'k-picker k-system-alerts__drawer-content';
      const drawerHeader = document.createElement('div');
      drawerHeader.className = 'k-picker__header';
      const drawerTitle = document.createElement('strong');
      drawerTitle.textContent = drawerTitleText;
      const closeButton = document.createElement('button');
      closeButton.type = 'button';
      closeButton.className = 'k-button';
      closeButton.textContent = 'Close';
      closeButton.addEventListener('click', () => drawer.close());
      drawerHeader.append(drawerTitle, closeButton);
      drawerContent.append(drawerHeader);
      if (this.alerts.length) {
        const activeSection = document.createElement('section');
        activeSection.className = 'k-system-alerts__section';
        const activeTitle = document.createElement('strong');
        activeTitle.className = 'k-system-alerts__section-title';
        activeTitle.textContent = 'Active conditions';
        const activeList = document.createElement('ul');
        activeList.className = 'k-system-alerts__list';
        for (const alert of this.alerts) activeList.append(this.alertItem(alert));
        activeSection.append(activeTitle, activeList);
        drawerContent.append(activeSection);
      }
      if (this.history.length) {
        const historySection = document.createElement('section');
        historySection.className = 'k-system-alerts__section';
        const historyTitle = document.createElement('strong');
        historyTitle.className = 'k-system-alerts__section-title';
        historyTitle.textContent = 'Recently resolved';
        const historyList = document.createElement('ul');
        historyList.className = 'k-system-alerts__history-list';
        for (const incident of this.history) historyList.append(this.historyItem(incident));
        historySection.append(historyTitle, historyList);
        drawerContent.append(historySection);
      }
      drawer.append(drawerContent);
      drawer.addEventListener('click', (event) => {
        if (event.target === drawer) drawer.close();
      });

      this.drawer = drawer;
      return drawer;
    }

    actionElement(action) {
      if (action.kind === 'navigate') {
        const link = document.createElement('a');
        link.className = 'k-button';
        link.href = action.href;
        link.textContent = action.label;
        return link;
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'k-button';
      button.textContent = action.label;
      button.addEventListener('click', () => {
        this.abort?.abort();
        void this.load();
      });
      return button;
    }

    alertItem(alert) {
      const item = document.createElement('li');
      item.className = `k-system-alerts__item k-system-alerts__item--${alert.severity}`;
      const message = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = alert.title;
      const detail = document.createElement('span');
      detail.textContent = alert.detail;
      message.append(title, detail);
      const controls = document.createElement('div');
      controls.className = 'k-system-alerts__controls';
      controls.append(this.actionElement(alert.action));
      const acknowledgement = this.acknowledgementElement(alert);
      if (acknowledgement) controls.append(acknowledgement);
      item.append(message, controls);
      return item;
    }

    acknowledgementElement(alert) {
      if (!alert.incidentId) return null;
      if (alert.acknowledgedAt) {
        const acknowledgement = document.createElement('span');
        acknowledgement.className = 'k-system-alerts__acknowledged';
        acknowledgement.textContent = `Acknowledged ${systemAlertTimeLabel(alert.acknowledgedAt)}`;
        return acknowledgement;
      }
      if (!this.acknowledgementSource()) return null;
      const acknowledgement = document.createElement('button');
      acknowledgement.type = 'button';
      acknowledgement.className = 'k-button k-system-alerts__acknowledge';
      acknowledgement.disabled = this.acknowledgingIncidentIds.has(alert.incidentId);
      acknowledgement.textContent = acknowledgement.disabled ? 'Acknowledging…' : 'Acknowledge';
      acknowledgement.addEventListener('click', () => void this.acknowledge(alert.incidentId));
      return acknowledgement;
    }

    historyItem(incident) {
      const item = document.createElement('li');
      item.className = `k-system-alerts__history-item k-system-alerts__history-item--${incident.severity}`;
      const title = document.createElement('strong');
      title.textContent = incident.title;
      const detail = document.createElement('span');
      const acknowledgement = incident.acknowledgedAt
        ? ` Acknowledged ${systemAlertTimeLabel(incident.acknowledgedAt)}.`
        : '';
      detail.textContent = `${incident.detail} Recovered ${systemAlertTimeLabel(incident.resolvedAt)}.${acknowledgement}`;
      item.append(title, detail);
      return item;
    }

    async acknowledge(incidentId) {
      const source = this.acknowledgementSource();
      if (!source || !normaliseSystemIncidentId(incidentId) || this.acknowledgingIncidentIds.has(incidentId)) return;
      this.acknowledgingIncidentIds.add(incidentId);
      this.render();
      try {
        const response = await fetch(`${source}/${incidentId}/acknowledge`, {
          method: 'POST',
          headers: {'Accept': 'application/json'},
          credentials: 'same-origin',
        });
        if (response.status === 401) {
          window.location.assign('/profiles');
          return;
        }
        if (!response.ok) {
          if (response.status === 404 || response.status === 409) {
            await this.load();
            publishKanvasToast({
              severity: 'info',
              title: 'System issue is no longer active',
              detail: 'The alert list has been refreshed.',
            });
            return;
          }
          throw new Error(await actionErrorDetail(response));
        }
        await this.load();
      } catch (error) {
        publishKanvasToast({
          severity: 'error',
          title: 'Could not acknowledge the system issue',
          detail: error instanceof Error ? error.message : 'Check your connection and try again.',
        });
      } finally {
        this.acknowledgingIncidentIds.delete(incidentId);
        this.render();
      }
    }

    openDrawer() {
      if (!(this.drawer instanceof HTMLDialogElement) || this.drawer.open) return;
      this.drawer.showModal();
    }
  }

  if (!customElements.get('kanvas-system-alerts')) {
    customElements.define('kanvas-system-alerts', KanvasSystemAlerts);
  }

  const POSTER_STATES = new Set([
    'normal', 'in_progress', 'watched', 'unavailable', 'selected', 'loading', 'missing_artwork'
  ]);
  const POSTER_ARTWORK_SHAPES = new Set(['portrait', 'landscape']);
  const LIBRARY_GRID_LAYOUTS = new Set(['portrait', 'landscape']);
  const POSTER_ACTIONS = {
    resume: {
      label: 'Resume',
      icon: '<svg class="k-poster__action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"></path></svg>'
    },
    play_next: {
      label: 'Play next',
      icon: '<svg class="k-poster__action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5v14l9-7z M16 5v14"></path></svg>'
    }
  };
  const POSTER_STATUS_ICONS = {
    unavailable: '<svg class="k-poster__status-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12 M18 6 6 18"></path></svg>'
  };

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
    const safeLines = lines
      .filter((line) => typeof line === 'string' && line.trim())
      .slice(0, 3)
      .map((line) => line.trim().slice(0, 160));
    return {lines: safeLines.length ? safeLines : [title]};
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
  const tmdbEntryReferenceFromValue = (value, expectedKind) => {
    if (typeof value !== 'string' || !['movie', 'series'].includes(expectedKind)) return null;
    const trimmed = value.trim();
    if (/^\d+$/.test(trimmed)) {
      return {provider: 'tmdb', provider_id: trimmed, kind: expectedKind};
    }
    return tmdbEntryReferenceFromUrl(trimmed, expectedKind);
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
        publishKanvasToast({severity: 'success', title: 'Profile settings saved'});
      } catch (error) {
        const message = error?.message || 'Changes could not be saved.';
        this.setStatus(message, true);
        publishKanvasToast({
          severity: 'error',
          title: 'Profile settings could not be saved',
          detail: message,
        });
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
    const artworkShape = poster.artworkShape ?? 'portrait';
    if (typeof artworkShape !== 'string' || !POSTER_ARTWORK_SHAPES.has(artworkShape)) return null;
    const mosaicUrls = poster.mosaicUrls ?? [];
    if (!Array.isArray(mosaicUrls) || mosaicUrls.length > 4 || mosaicUrls.some((url) => !localArtworkUrl(url))) return null;
    if (poster.posterUrl != null && mosaicUrls.length > 0) return null;
    const placeholder = normalisePlaceholder(poster.placeholder, poster.title);
    if (poster.context != null && typeof poster.context !== 'string') return null;
    if (poster.detail != null && typeof poster.detail !== 'string') return null;
    if (poster.artworkLabel != null && typeof poster.artworkLabel !== 'string') return null;
    if (poster.progressPercent != null && (!Number.isInteger(poster.progressPercent) || poster.progressPercent < 0 || poster.progressPercent > 100)) return null;
    if (typeof poster.state !== 'string' || !POSTER_STATES.has(poster.state)) return null;
    if (poster.watched != null && typeof poster.watched !== 'boolean') return null;
    if (poster.partiallyWatched != null && typeof poster.partiallyWatched !== 'boolean') return null;
    if (poster.action != null && (
      typeof poster.action !== 'string' || !Object.prototype.hasOwnProperty.call(POSTER_ACTIONS, poster.action)
    )) return null;
    return {
      id: poster.id,
      title: poster.title,
      href: poster.href,
      posterUrl: poster.posterUrl ?? null,
      artworkShape,
      mosaicUrls,
      placeholder,
      context: poster.context?.trim() || null,
      detail: poster.detail?.trim() || null,
      artworkLabel: poster.artworkLabel?.trim().slice(0, 80) || null,
      progressPercent: poster.progressPercent ?? null,
      state: poster.state,
      watched: poster.watched === true,
      partiallyWatched: poster.partiallyWatched === true,
      available: poster.available,
      action: poster.action ?? null
    };
  };

  const posterStatusMarkup = (poster) => {
    if (!poster.available) {
      return `<span class="k-poster__status k-poster__status--unavailable" role="img" aria-label="Unavailable">${POSTER_STATUS_ICONS.unavailable}</span>`;
    }
    if (poster.watched) {
      return '<span class="k-poster__completion k-poster__completion--watched" role="img" aria-label="Watched"></span>';
    }
    if (poster.partiallyWatched) {
      return '<span class="k-poster__completion k-poster__completion--partial" role="img" aria-label="Partially watched"></span>';
    }
    return '';
  };

  const posterMarkup = (poster) => {
    const progress = poster.progressPercent == null ? '' :
      `<span class="k-progress" aria-label="Playback progress"><span class="k-progress__value" style="--k-progress:${poster.progressPercent}%"></span></span>`;
    const placeholderLines = poster.placeholder.lines
      .map((line) => `<span class="k-poster__fallback-line">${escapeHtml(line)}</span>`)
      .join('');
    const artworkLabel = poster.artworkLabel
      ? `<span class="k-poster__artwork-label"><span class="k-poster__artwork-label-banner" aria-hidden="true"></span><span class="k-poster__artwork-label-text">${escapeHtml(poster.artworkLabel)}</span></span>`
      : '';
    const mosaic = poster.mosaicUrls.length
      ? `<span class="k-poster-mosaic" aria-hidden="true">${poster.mosaicUrls
        .map((url) => `<img class="k-poster-mosaic__image" src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async">`)
        .join('')}</span>`
      : '';
    const artwork = poster.posterUrl
      ? `<img class="k-poster__image" src="${escapeHtml(poster.posterUrl)}" alt="" loading="lazy" decoding="async">`
      : mosaic || `<span class="k-poster__fallback" aria-hidden="true">${placeholderLines}</span>`;
    const status = posterStatusMarkup(poster);
    const context = poster.context ? `<span class="k-poster__context">${escapeHtml(poster.context)}</span>` : '';
    const detail = poster.detail ? `<span class="k-poster__detail"><span class="k-poster__detail-text">${escapeHtml(poster.detail)}</span></span>` : '';
    const actionView = poster.action ? POSTER_ACTIONS[poster.action] : null;
    const action = actionView
      ? `<span class="k-poster__action k-poster__action--${poster.action}" aria-hidden="true"><span class="k-poster__action-content">${actionView.icon}<span class="k-poster__action-label">${actionView.label}</span></span></span>`
      : '';
    const artworkShapeClass = ` k-poster--${escapeHtml(poster.artworkShape)}`;
    const actionClass = actionView ? ` k-poster--has-action k-poster--action-${poster.action}` : '';
    const posterLabel = poster.artworkLabel ? `${poster.title} — ${poster.artworkLabel}` : poster.title;
    const accessibleLabel = actionView ? `${actionView.label} ${posterLabel}` : posterLabel;
    return `<a class="k-poster k-poster--${escapeHtml(poster.state)}${artworkShapeClass}${actionClass}" href="${escapeHtml(poster.href)}" aria-label="${escapeHtml(accessibleLabel)}" title="${escapeHtml(poster.title)}" data-kanvas-poster="${poster.id}">
      <span class="k-poster__art">${artwork}${artworkLabel}${progress}${status}${action}</span>
      <span class="k-poster__meta">${context}<span class="k-poster__title">${escapeHtml(poster.title)}</span>${detail}</span>
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
    element.dataset.libraryPosterId = String(poster.id);
    element.setAttribute('poster', JSON.stringify(poster));
    return element;
  };

  const updateRailControls = () => {
    if (typeof document.querySelectorAll !== 'function') return;
    for (const rail of document.querySelectorAll('.k-rail')) {
      const viewport = rail.querySelector('[data-kanvas-rail-viewport="true"]');
      const controls = rail.querySelector('.k-rail__controls');
      if (!(viewport instanceof HTMLElement) || !(controls instanceof HTMLElement)) continue;
      controls.hidden = viewport.scrollWidth <= viewport.clientWidth + 1;
    }
  };

  let railControlsObserver = null;
  let railControlsUpdateQueued = false;
  const queueRailControlsUpdate = () => {
    if (railControlsUpdateQueued) return;
    railControlsUpdateQueued = true;
    const update = () => {
      railControlsUpdateQueued = false;
      updateRailControls();
    };
    if (typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(update);
    } else {
      update();
    }
  };

  const initialiseRailControls = () => {
    queueRailControlsUpdate();
    if (railControlsObserver || typeof MutationObserver !== 'function' || !document.body) return;
    railControlsObserver = new MutationObserver(queueRailControlsUpdate);
    railControlsObserver.observe(document.body, {childList: true, subtree: true});
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialiseRailControls, {once: true});
  } else {
    initialiseRailControls();
  }
  window.addEventListener('resize', queueRailControlsUpdate);

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const control = target?.closest('[data-kanvas-rail-scroll]');
    if (!(control instanceof HTMLButtonElement)) return;
    const direction = control.getAttribute('data-kanvas-rail-scroll') === 'previous' ? -1 : 1;
    const viewport = control.closest('.k-rail')?.querySelector('[data-kanvas-rail-viewport="true"]');
    if (!(viewport instanceof HTMLElement)) return;
    event.preventDefault();
    viewport.scrollBy({
      left: direction * Math.max(Math.round(viewport.clientWidth * 0.85), 180),
      behavior: 'smooth'
    });
  });

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
    const completeRowCount = Math.floor(removeCount / columns) * columns;
    const maximumSafeRemoval = completeRowCount || removeCount;
    let safeRemovalCount = 0;

    for (let count = columns; count <= maximumSafeRemoval; count += columns) {
      const lastCandidate = children[count - 1];
      if (!lastCandidate) break;
      const bounds = lastCandidate.getBoundingClientRect();
      const bottom = Number.isFinite(bounds.bottom) ? bounds.bottom : bounds.top + bounds.height;
      if (bottom > GRID_TRIM_VIEWPORT_BUFFER_PX) break;
      safeRemovalCount = count;
    }
    if (safeRemovalCount === 0 && maximumSafeRemoval < columns) {
      const lastCandidate = children[maximumSafeRemoval - 1];
      if (lastCandidate) {
        const bounds = lastCandidate.getBoundingClientRect();
        const bottom = Number.isFinite(bounds.bottom) ? bounds.bottom : bounds.top + bounds.height;
        if (bottom <= GRID_TRIM_VIEWPORT_BUFFER_PX) safeRemovalCount = maximumSafeRemoval;
      }
    }

    const removed = children.slice(0, safeRemovalCount);
    const anchor = children[safeRemovalCount] || null;
    if (!removed.length || !anchor) return 0;
    if (removed.some((child) => child.contains(document.activeElement))) {
      const focusTarget = anchor.querySelector('.k-poster');
      if (!focusTarget || typeof focusTarget.focus !== 'function') return 0;
      focusTarget.focus({preventScroll: true});
    }
    const anchorTop = anchor?.getBoundingClientRect().top ?? null;
    for (const child of removed) child.remove();
    if (anchor && anchorTop !== null) window.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
    return removed.length;
  };

  const LIBRARY_GRID_SCHEMA_VERSION = 10;
  const LIBRARY_RESPONSE_SCHEMA_VERSION = 2;
  const LibraryPageDirection = Object.freeze({
    INITIAL: 'initial',
    PREVIOUS: 'previous',
    NEXT: 'next'
  });
  const libraryGridLayout = (value) => LIBRARY_GRID_LAYOUTS.has(value) ? value : 'portrait';
  const libraryGridMarkup = (layout) => {
    const skeletonCount = layout === 'landscape' ? 3 : 6;
    const loading = Array.from(
      {length: skeletonCount}, () => '<span class="k-library-grid__skeleton"></span>'
    ).join('');
    return '<div class="k-library-grid__loading k-library-grid__loading--' + layout + '" aria-hidden="true">' + loading + '</div>'
      + '<div class="k-grid-spacer" data-library-spacer="before" aria-hidden="true"></div>'
      + '<div class="k-grid-status k-grid-status--head" data-library-status="previous" aria-live="polite"></div>'
      + '<div class="k-grid-sentinel" data-library-sentinel="previous" aria-hidden="true"></div>'
      + '<div class="k-grid k-grid--' + layout + '" data-library-grid="' + layout + '" aria-busy="true"></div>'
      + '<div class="k-grid-status k-grid-status--tail" data-library-status="next" aria-live="polite">Loading library…</div>'
      + '<div class="k-grid-sentinel" data-library-sentinel="next" aria-hidden="true"></div>'
      + '<div class="k-grid-spacer" data-library-spacer="after" aria-hidden="true"></div>';
  };
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
    return url.pathname + url.search;
  };

  const libraryCursor = (value) => value === null || typeof value === 'string';
  const libraryGridPayload = (payload) => {
    if (!payload || typeof payload !== 'object' || payload.schemaVersion !== LIBRARY_RESPONSE_SCHEMA_VERSION || !Array.isArray(payload.items)) {
      throw new LibraryLoadError('invalid_envelope');
    }
    if (!libraryCursor(payload.previousCursor) || !libraryCursor(payload.nextCursor)) {
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
    return {
      items,
      invalidPosterIds,
      previousCursor: payload.previousCursor ?? null,
      nextCursor: payload.nextCursor ?? null,
      requestId
    };
  };

  const savedLibraryPage = (value) => {
    if (!value || typeof value !== 'object' || !Array.isArray(value.items) || !libraryCursor(value.previousCursor) || !libraryCursor(value.nextCursor)) {
      throw new TypeError('Invalid saved library page');
    }
    const items = value.items.map(normalisePoster);
    if (!items.length || items.some((item) => item === null)) {
      throw new TypeError('Invalid saved library posters');
    }
    return {
      items,
      previousCursor: value.previousCursor ?? null,
      nextCursor: value.nextCursor ?? null
    };
  };

  const virtualHeight = (value) => (
    typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 10_000_000
      ? value
      : null
  );

  class KanvasPosterGrid extends HTMLElement {
    static get observedAttributes() {
      return ['source', 'catalogue-revision', 'grid-layout', 'result-label', 'max-mounted'];
    }

    constructor() {
      super();
      this.pages = [];
      this.leadingHeight = 0;
      this.trailingHeight = 0;
      this.loadingDirection = null;
      this.retryDirection = null;
      this.observer = null;
      this.grid = null;
      this.previousStatus = null;
      this.nextStatus = null;
      this.previousSentinel = null;
      this.nextSentinel = null;
      this.leadingSpacer = null;
      this.trailingSpacer = null;
      this.loadingView = null;
      this.stateKey = null;
      this.requestController = null;
      this.generation = 0;
      this.requestId = null;
      this.invalidPosterCount = 0;
      this.hasSuccessfulPage = false;
      this.viewportCheckQueued = false;
      this.gridHeight = 0;
      this.pendingFocus = null;
      this.onPageHide = () => this.saveState();
      this.onScroll = () => {
        if (this.trimMountedPages()) this.scheduleGridMeasurement();
        this.scheduleViewportCheck();
      };
      this.onResize = () => this.handleResize();
      this.onKeyDown = (event) => this.handleKeyDown(event);
    }

    connectedCallback() {
      this.initialise();
    }

    attributeChangedCallback(name, previous, current) {
      if (
        ['source', 'catalogue-revision', 'grid-layout', 'result-label', 'max-mounted'].includes(name)
        && this.isConnected
        && previous !== current
      ) this.initialise();
    }

    disconnectedCallback() {
      this.generation += 1;
      this.requestController?.abort();
      this.requestController = null;
      this.observer?.disconnect();
      this.observer = null;
      this.removeEventListener('keydown', this.onKeyDown);
      window.removeEventListener('pagehide', this.onPageHide);
      window.removeEventListener('scroll', this.onScroll);
      window.removeEventListener('resize', this.onResize);
    }

    initialise() {
      this.generation += 1;
      this.requestController?.abort();
      this.requestController = null;
      this.observer?.disconnect();
      const source = this.getAttribute('source');
      this.pages = [];
      this.leadingHeight = 0;
      this.trailingHeight = 0;
      this.loadingDirection = null;
      this.retryDirection = null;
      this.requestId = null;
      this.invalidPosterCount = 0;
      this.hasSuccessfulPage = false;
      this.gridHeight = 0;
      this.pendingFocus = null;
      this.stateKey = source ? this.buildStateKey(source) : null;
      this.innerHTML = libraryGridMarkup(this.gridLayout());
      this.grid = this.querySelector('[data-library-grid]');
      this.previousStatus = this.querySelector('[data-library-status="previous"]');
      this.nextStatus = this.querySelector('[data-library-status="next"]');
      this.previousSentinel = this.querySelector('[data-library-sentinel="previous"]');
      this.nextSentinel = this.querySelector('[data-library-sentinel="next"]');
      this.leadingSpacer = this.querySelector('[data-library-spacer="before"]');
      this.trailingSpacer = this.querySelector('[data-library-spacer="after"]');
      this.loadingView = this.querySelector('.k-library-grid__loading');
      if (
        !source ||
        !(this.grid instanceof HTMLElement) ||
        !(this.previousStatus instanceof HTMLElement) ||
        !(this.nextStatus instanceof HTMLElement) ||
        !(this.previousSentinel instanceof HTMLElement) ||
        !(this.nextSentinel instanceof HTMLElement) ||
        !(this.leadingSpacer instanceof HTMLElement) ||
        !(this.trailingSpacer instanceof HTMLElement)
      ) {
        if (this.nextStatus) this.nextStatus.textContent = 'The library grid could not be configured.';
        return;
      }
      this.observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting || !(entry.target instanceof HTMLElement)) continue;
          const direction = entry.target.dataset.librarySentinel;
          if (direction === LibraryPageDirection.PREVIOUS || direction === LibraryPageDirection.NEXT) {
            void this.load(direction);
          }
        }
      }, {rootMargin: String(LIBRARY_VIRTUAL_OVERSCAN_PX) + 'px 0px'});
      this.observer.observe(this.previousSentinel);
      this.observer.observe(this.nextSentinel);
      this.removeEventListener('keydown', this.onKeyDown);
      this.addEventListener('keydown', this.onKeyDown);
      window.removeEventListener('pagehide', this.onPageHide);
      window.addEventListener('pagehide', this.onPageHide);
      window.removeEventListener('scroll', this.onScroll);
      window.addEventListener('scroll', this.onScroll, {passive: true});
      window.removeEventListener('resize', this.onResize);
      window.addEventListener('resize', this.onResize, {passive: true});
      if (this.restoreState()) {
        this.scheduleViewportCheck();
      } else {
        void this.load(LibraryPageDirection.INITIAL);
      }
    }

    gridLayout() {
      return libraryGridLayout(this.getAttribute('grid-layout'));
    }

    resultLabel() {
      const label = this.getAttribute('result-label')?.trim() || '';
      return label.toLowerCase() === 'library' ? null : label;
    }

    loadingStatus() {
      const label = this.resultLabel();
      return label ? 'Loading ' + label.toLowerCase() + '…' : 'Loading library…';
    }

    emptyStatus() {
      const label = this.resultLabel();
      return label ? 'No ' + label.toLowerCase() + ' match these filters.' : 'No items match these filters.';
    }

    endStatus() {
      const label = this.resultLabel();
      return label ? 'End of ' + label.toLowerCase() + '.' : 'End of library.';
    }

    maximumMountedPosters() {
      const requested = Number(this.getAttribute('max-mounted'));
      if (!Number.isSafeInteger(requested) || requested < 1) return MAX_MOUNTED_POSTERS;
      return Math.min(requested, MAX_MOUNTED_POSTERS);
    }

    posterElements() {
      return this.grid ? Array.from(this.grid.children) : [];
    }

    mountedPosterCount() {
      return this.pages.reduce((count, page) => count + page.items.length, 0);
    }

    edgeCursor(direction) {
      if (!this.pages.length) return null;
      if (direction === LibraryPageDirection.PREVIOUS) return this.pages[0].previousCursor;
      if (direction === LibraryPageDirection.NEXT) return this.pages.at(-1).nextCursor;
      return null;
    }

    canLoad(direction, retry) {
      if (this.loadingDirection !== null || !(this.grid instanceof HTMLElement)) return false;
      if (direction === LibraryPageDirection.INITIAL) return this.pages.length === 0;
      if (this.retryDirection === direction && !retry) return false;
      return this.edgeCursor(direction) !== null;
    }

    setGridBusy(busy) {
      this.grid?.setAttribute('aria-busy', String(busy));
    }

    clearLoadingView() {
      this.loadingView?.remove();
      this.loadingView = null;
    }

    setLeadingHeight(height) {
      this.leadingHeight = Math.max(0, height);
      if (this.leadingSpacer) this.leadingSpacer.style.height = String(this.leadingHeight) + 'px';
    }

    setTrailingHeight(height) {
      this.trailingHeight = Math.max(0, height);
      if (this.trailingSpacer) this.trailingSpacer.style.height = String(this.trailingHeight) + 'px';
    }

    appendPage(page) {
      if (!this.grid) throw new TypeError('Library grid is unavailable');
      const fragment = document.createDocumentFragment();
      for (const poster of page.items) fragment.append(posterElement(poster));
      this.grid.append(fragment);
    }

    prependPage(page) {
      if (!this.grid) throw new TypeError('Library grid is unavailable');
      const fragment = document.createDocumentFragment();
      for (const poster of page.items) fragment.append(posterElement(poster));
      this.grid.prepend(fragment);
    }

    renderPages() {
      if (!this.grid) throw new TypeError('Library grid is unavailable');
      this.grid.replaceChildren();
      for (const page of this.pages) this.appendPage(page);
    }

    buildStateKey(source) {
      const user = this.getAttribute('state-user') || 'anonymous';
      const catalogueRevision = this.catalogueRevision();
      return 'kanvas:grid:v' + LIBRARY_GRID_SCHEMA_VERSION
        + ':asset=' + libraryAssetVersion()
        + ':catalogue=' + encodeURIComponent(catalogueRevision)
        + ':user=' + encodeURIComponent(user)
        + ':max-mounted=' + this.maximumMountedPosters()
        + ':filters=' + encodeURIComponent(normalisedGridSource(source));
    }

    catalogueRevision() {
      return this.getAttribute('catalogue-revision') || 'unknown';
    }

    async load(direction, {retry = false, focusColumn = null} = {}) {
      if (!this.canLoad(direction, retry)) return;
      const source = this.getAttribute('source');
      if (!source) return;
      const cursor = direction === LibraryPageDirection.INITIAL ? null : this.edgeCursor(direction);
      const generation = this.generation;
      const controller = new AbortController();
      this.requestController = controller;
      this.loadingDirection = direction;
      this.pendingFocus = focusColumn === null ? null : {direction, column: focusColumn};
      if (retry) this.retryDirection = null;
      this.setGridBusy(true);
      const status = direction === LibraryPageDirection.PREVIOUS ? this.previousStatus : this.nextStatus;
      if (status) {
        status.textContent = direction === LibraryPageDirection.INITIAL
          ? this.loadingStatus()
          : direction === LibraryPageDirection.PREVIOUS ? 'Loading earlier items…' : 'Loading more…';
      }
      let completed = false;
      try {
        const url = new URL(source, window.location.origin);
        if (cursor) url.searchParams.set('cursor', cursor);
        const response = await fetch(url, {
          headers: {'Accept': 'application/json'},
          credentials: 'same-origin',
          signal: controller.signal
        });
        const responseRequestId = safeRequestId(response.headers.get('X-Request-ID'));
        if (!response.ok) throw await this.httpFailure(response, responseRequestId);
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
        const page = {
          items: payload.items,
          previousCursor: payload.previousCursor,
          nextCursor: payload.nextCursor
        };
        try {
          this.integratePage(page, direction);
        } catch (error) {
          throw new LibraryLoadError('rendering_failure', {
            status: response.status,
            requestId: payload.requestId,
            cause: error
          });
        }
        this.clearLoadingView();
        this.hasSuccessfulPage = true;
        this.retryDirection = null;
        this.updateEdgeStatus();
        this.trimMountedPages();
        this.focusInsertedPage();
        completed = true;
      } catch (error) {
        if (controller.signal.aborted || generation !== this.generation) return;
        const failure = error instanceof LibraryLoadError
          ? error
          : new LibraryLoadError('network_failure', {cause: error});
        this.requestId = failure.requestId || this.requestId;
        this.retryDirection = direction;
        this.clearLoadingView();
        this.showFailure(direction, failure);
        this.reportFailure(failure);
      } finally {
        if (generation !== this.generation) return;
        this.loadingDirection = null;
        this.requestController = null;
        this.setGridBusy(false);
        if (!completed) return;
        this.scheduleGridMeasurement();
        if (!this.mountedPosterCount() && this.edgeCursor(LibraryPageDirection.NEXT) !== null) {
          requestAnimationFrame(() => { void this.load(LibraryPageDirection.NEXT); });
        } else {
          this.scheduleViewportCheck();
        }
      }
    }

    integratePage(page, direction) {
      if (direction === LibraryPageDirection.INITIAL) {
        if (!page.items.length) {
          this.pages = page.nextCursor === null && !this.invalidPosterCount ? [] : [page];
          return;
        }
        this.pages = [page];
        this.renderPages();
        return;
      }
      if (!page.items.length) {
        this.updateEmptyEdge(page, direction);
        return;
      }
      if (direction === LibraryPageDirection.PREVIOUS) {
        const anchor = this.posterElements()[0] || null;
        const anchorTop = anchor?.getBoundingClientRect().top ?? null;
        this.pages.unshift(page);
        this.prependPage(page);
        if (anchor && anchorTop !== null) {
          const insertedHeight = Math.max(0, anchor.getBoundingClientRect().top - anchorTop);
          this.setLeadingHeight(this.leadingHeight - insertedHeight);
          window.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
        }
        return;
      }
      const trailingBottom = this.trailingSpacer?.getBoundingClientRect().bottom ?? null;
      this.pages.push(page);
      this.appendPage(page);
      if (trailingBottom !== null && this.trailingSpacer) {
        const insertedHeight = Math.max(0, this.trailingSpacer.getBoundingClientRect().bottom - trailingBottom);
        this.setTrailingHeight(this.trailingHeight - insertedHeight);
      }
    }

    updateEmptyEdge(page, direction) {
      if (!this.pages.length) {
        this.pages = [page];
        return;
      }
      const edge = direction === LibraryPageDirection.PREVIOUS ? this.pages[0] : this.pages.at(-1);
      if (direction === LibraryPageDirection.PREVIOUS) edge.previousCursor = page.previousCursor;
      else edge.nextCursor = page.nextCursor;
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

    updateEdgeStatus() {
      if (!this.previousStatus || !this.nextStatus) return;
      this.previousStatus.textContent = '';
      if (!this.pages.length) {
        this.nextStatus.textContent = this.emptyStatus();
        return;
      }
      this.nextStatus.textContent = this.pageStatus(this.edgeCursor(LibraryPageDirection.NEXT));
    }

    pageStatus(nextCursor) {
      const invalid = this.invalidPosterCount
        ? String(this.invalidPosterCount) + ' item' + (this.invalidPosterCount === 1 ? '' : 's') + ' could not be displayed.'
        : '';
      if (nextCursor !== null) return invalid;
      return invalid ? invalid + ' ' + this.endStatus() : this.endStatus();
    }

    showFailure(direction, failure) {
      const status = direction === LibraryPageDirection.PREVIOUS ? this.previousStatus : this.nextStatus;
      if (!status) return;
      status.textContent = 'Could not load this part of the library.';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'k-button k-grid-retry';
      retry.textContent = 'Retry';
      retry.addEventListener('click', () => {
        retry.remove();
        void this.load(direction, {retry: true});
      }, {once: true});
      const diagnostic = document.createElement('details');
      diagnostic.className = 'k-grid-diagnostic';
      const summary = document.createElement('summary');
      summary.textContent = 'Details';
      const content = document.createElement('div');
      content.textContent = 'Category: ' + failure.category
        + '\nHTTP status: ' + (failure.status ?? '—')
        + '\nRequest ID: ' + (failure.requestId ?? '—');
      diagnostic.append(summary, content);
      status.append(retry, diagnostic);
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

    trimMountedPages() {
      let evicted = false;
      while (this.mountedPosterCount() > this.maximumMountedPosters()) {
        if (this.canEvictPreviousPage()) {
          this.evictPreviousPage();
          evicted = true;
          continue;
        }
        if (this.canEvictNextPage()) {
          this.evictNextPage();
          evicted = true;
          continue;
        }
        return evicted;
      }
      return evicted;
    }

    canEvictPreviousPage() {
      if (this.pages.length < 2) return false;
      const count = this.pages[0].items.length;
      const last = this.posterElements()[count - 1];
      if (!last || last.contains(document.activeElement)) return false;
      return last.getBoundingClientRect().bottom < -LIBRARY_VIRTUAL_OVERSCAN_PX;
    }

    canEvictNextPage() {
      if (this.pages.length < 2) return false;
      const count = this.pages.at(-1).items.length;
      const posters = this.posterElements();
      const first = posters[posters.length - count];
      if (!first || first.contains(document.activeElement)) return false;
      return first.getBoundingClientRect().top > window.innerHeight + LIBRARY_VIRTUAL_OVERSCAN_PX;
    }

    evictPreviousPage() {
      const page = this.pages[0];
      const posters = this.posterElements();
      const anchor = posters[page.items.length];
      if (!anchor) return;
      const anchorTop = anchor.getBoundingClientRect().top;
      for (const poster of posters.slice(0, page.items.length)) poster.remove();
      this.setLeadingHeight(this.leadingHeight + Math.max(0, anchorTop - anchor.getBoundingClientRect().top));
      this.pages.shift();
    }

    evictNextPage() {
      const page = this.pages.at(-1);
      const posters = this.posterElements();
      const firstIndex = posters.length - page.items.length;
      const before = this.trailingSpacer?.getBoundingClientRect().bottom ?? null;
      for (const poster of posters.slice(firstIndex)) poster.remove();
      if (before !== null && this.trailingSpacer) {
        this.setTrailingHeight(
          this.trailingHeight + Math.max(0, before - this.trailingSpacer.getBoundingClientRect().bottom)
        );
      }
      this.pages.pop();
    }

    focusInsertedPage() {
      const pending = this.pendingFocus;
      this.pendingFocus = null;
      if (!pending || !this.grid) return;
      const page = pending.direction === LibraryPageDirection.PREVIOUS ? this.pages[0] : this.pages.at(-1);
      if (!page?.items.length) return;
      const posters = this.posterElements();
      const columns = gridColumnCount(this.grid);
      const pageStart = pending.direction === LibraryPageDirection.PREVIOUS
        ? 0
        : posters.length - page.items.length;
      const offset = pending.direction === LibraryPageDirection.PREVIOUS
        ? Math.max(0, page.items.length - columns + Math.min(pending.column, columns - 1))
        : Math.min(pending.column, page.items.length - 1);
      posters[pageStart + Math.min(offset, page.items.length - 1)]
        ?.querySelector('.k-poster')
        ?.focus({preventScroll: true});
    }

    handleKeyDown(event) {
      if (!(event.target instanceof HTMLElement) || !/^Arrow(?:Up|Down)$/.test(event.key)) return;
      const card = event.target.closest('kanvas-poster');
      if (!card || card.parentElement !== this.grid) return;
      const posters = this.posterElements();
      const index = posters.indexOf(card);
      if (index < 0 || !this.grid) return;
      const columns = gridColumnCount(this.grid);
      const direction = event.key === 'ArrowUp'
        ? LibraryPageDirection.PREVIOUS
        : LibraryPageDirection.NEXT;
      const reachesEdge = direction === LibraryPageDirection.PREVIOUS
        ? index < columns
        : index + columns >= posters.length;
      if (!reachesEdge || this.edgeCursor(direction) === null) return;
      event.preventDefault();
      void this.load(direction, {focusColumn: index % columns});
    }

    scheduleViewportCheck() {
      if (this.viewportCheckQueued) return;
      this.viewportCheckQueued = true;
      requestAnimationFrame(() => {
        this.viewportCheckQueued = false;
        this.ensureViewportCoverage();
      });
    }

    scheduleGridMeasurement() {
      requestAnimationFrame(() => {
        if (this.grid) this.gridHeight = this.grid.getBoundingClientRect().height;
      });
    }

    handleResize() {
      const previousHeight = this.gridHeight;
      requestAnimationFrame(() => {
        if (!this.grid) return;
        const nextHeight = this.grid.getBoundingClientRect().height;
        if (previousHeight > 0 && nextHeight > 0) {
          const scale = nextHeight / previousHeight;
          this.setLeadingHeight(this.leadingHeight * scale);
          this.setTrailingHeight(this.trailingHeight * scale);
        }
        this.gridHeight = nextHeight;
        this.scheduleViewportCheck();
      });
    }

    ensureViewportCoverage() {
      if (!this.grid || this.loadingDirection !== null) return;
      const bounds = this.grid.getBoundingClientRect();
      const previousCursor = this.edgeCursor(LibraryPageDirection.PREVIOUS);
      const nextCursor = this.edgeCursor(LibraryPageDirection.NEXT);
      if (
        this.leadingHeight > 0 &&
        bounds.top > window.innerHeight + LIBRARY_VIRTUAL_OVERSCAN_PX &&
        previousCursor !== null
      ) {
        void this.load(LibraryPageDirection.PREVIOUS);
        return;
      }
      if (
        this.trailingHeight > 0 &&
        bounds.bottom < -LIBRARY_VIRTUAL_OVERSCAN_PX &&
        nextCursor !== null
      ) void this.load(LibraryPageDirection.NEXT);
      else if (
        previousCursor !== null &&
        this.previousSentinel &&
        this.previousSentinel.getBoundingClientRect().bottom >= -LIBRARY_VIRTUAL_OVERSCAN_PX
      ) void this.load(LibraryPageDirection.PREVIOUS);
      else if (
        nextCursor !== null &&
        this.nextSentinel &&
        this.nextSentinel.getBoundingClientRect().top <= window.innerHeight + LIBRARY_VIRTUAL_OVERSCAN_PX
      ) void this.load(LibraryPageDirection.NEXT);
    }

    visibleAnchor() {
      const anchor = this.posterElements().find((poster) => poster.getBoundingClientRect().bottom >= 0);
      if (!anchor) return null;
      const id = Number(anchor.dataset.libraryPosterId);
      if (!Number.isSafeInteger(id) || id < 1) return null;
      return {id, top: anchor.getBoundingClientRect().top};
    }

    saveState() {
      if (
        !this.stateKey ||
        !this.mountedPosterCount() ||
        !this.hasSuccessfulPage ||
        this.retryDirection !== null
      ) {
        if (this.stateKey) sessionStorage.removeItem(this.stateKey);
        return;
      }
      try {
        sessionStorage.setItem(this.stateKey, JSON.stringify({
          schemaVersion: LIBRARY_GRID_SCHEMA_VERSION,
          asset: libraryAssetVersion(),
          catalogueRevision: this.catalogueRevision(),
          filters: normalisedGridSource(this.getAttribute('source') || ''),
          layout: this.gridLayout(),
          user: this.getAttribute('state-user') || 'anonymous',
          maxMounted: this.maximumMountedPosters(),
          pages: this.pages,
          leadingHeight: this.leadingHeight,
          trailingHeight: this.trailingHeight,
          scrollY: window.scrollY,
          anchor: this.visibleAnchor(),
          outcome: 'success'
        }));
      } catch (_) {
        sessionStorage.removeItem(this.stateKey);
      }
    }

    restoreState() {
      if (!this.stateKey || !this.grid || !this.nextStatus) return false;
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
          state.layout !== this.gridLayout() ||
          state.user !== (this.getAttribute('state-user') || 'anonymous') ||
          state.maxMounted !== this.maximumMountedPosters() ||
          !Array.isArray(state.pages) ||
          !state.pages.length ||
          state.outcome !== 'success'
        ) throw new TypeError('Incompatible library grid state');
        const pages = state.pages.map(savedLibraryPage);
        const itemCount = pages.reduce((count, page) => count + page.items.length, 0);
        const leadingHeight = virtualHeight(state.leadingHeight);
        const trailingHeight = virtualHeight(state.trailingHeight);
        if (
          itemCount > this.maximumMountedPosters() ||
          leadingHeight === null ||
          trailingHeight === null ||
          !Number.isFinite(state.scrollY) ||
          state.scrollY < 0
        ) throw new TypeError('Invalid library grid state');
        if (
          state.anchor !== null &&
          (typeof state.anchor !== 'object' ||
          !Number.isSafeInteger(state.anchor.id) ||
          state.anchor.id < 1 ||
          !Number.isFinite(state.anchor.top))
        ) throw new TypeError('Invalid library scroll anchor');
        this.pages = pages;
        this.setLeadingHeight(leadingHeight);
        this.setTrailingHeight(trailingHeight);
        this.renderPages();
        this.hasSuccessfulPage = true;
        this.retryDirection = null;
        this.clearLoadingView();
        this.setGridBusy(false);
        this.updateEdgeStatus();
        this.scheduleGridMeasurement();
        requestAnimationFrame(() => this.restoreScroll(state));
        return true;
      } catch (_) {
        sessionStorage.removeItem(this.stateKey);
        return false;
      }
    }

    restoreScroll(state) {
      const savedAnchor = state.anchor;
      if (savedAnchor) {
        const anchor = this.posterElements().find(
          (poster) => Number(poster.dataset.libraryPosterId) === savedAnchor.id
        );
        if (anchor) {
          window.scrollBy(0, anchor.getBoundingClientRect().top - savedAnchor.top);
          return;
        }
      }
      window.scrollTo(0, state.scrollY);
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
      this.innerHTML = '<section class="k-onboarding" role="status"><div><strong>Artwork is not configured yet</strong><p>Review library issues, match items, then choose artwork.</p><span class="k-action-row"><a class="k-button" href="/administration/libraries/hierarchy">Review library issues</a><a class="k-button" href="/administration/metadata">Review metadata</a><a class="k-button" href="/administration/metadata/artwork">Artwork maintenance</a></span></div><button type="button" class="k-button" data-onboarding-dismiss>Dismiss</button></section>';
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
    const columns = gridColumnCount(grid);
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
        const message = 'Could not save this change.';
        this.status.textContent = message;
        publishKanvasToast({severity: 'error', title: 'Collection update failed', detail: message});
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
      artworkShape: row.kind === 'episode' ? 'landscape' : 'portrait',
      artworkLabel: row.kind,
      placeholder: {lines: [row.title]},
      detail: [row.year, row.kind].filter(Boolean).join(' · ') || null,
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

  window.kanvasInternals = {
    escapeHtml,
    jobDetail,
    localArtworkUrl,
    normaliseToast,
    providerDisplayName,
    providerEntryUrl,
    publishKanvasToast,
    requestKanvasConfirmation,
    tmdbEntryReferenceFromUrl,
    tmdbEntryReferenceFromValue,
  };

  const loadKanvasComponents = () => {
    const componentSources = new Map(
      Object.entries(window.kanvasComponentScripts || {}).filter(([name, source]) => (
        /^[a-z][a-z0-9-]*-[a-z0-9-]+$/.test(name)
        && typeof source === 'string'
        && source.startsWith('/_kanvas/')
      ))
    );
    if (!componentSources.size || !document.head) return;
    const selector = Array.from(componentSources.keys()).join(',');
    const scheduled = new Set();
    const load = (element) => {
      if (!(element instanceof Element)) return;
      const source = componentSources.get(element.localName);
      if (!source || scheduled.has(element.localName) || customElements.get(element.localName)) return;
      scheduled.add(element.localName);
      const script = document.createElement('script');
      script.async = false;
      script.src = source;
      script.dataset.kanvasComponent = element.localName;
      document.head.append(script);
    };
    document.querySelectorAll(selector).forEach(load);
  };

  if (document.readyState === 'complete') {
    loadKanvasComponents();
  } else {
    document.addEventListener('DOMContentLoaded', loadKanvasComponents, {once: true});
  }

})();
