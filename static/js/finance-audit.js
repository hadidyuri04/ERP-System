(function (window, document) {
  'use strict';

  const form = document.getElementById('audit-filter-form');
  const results = document.getElementById('audit-results');
  if (!form || !results || !window.ERP) return;

  const search = form.querySelector('[name="q"]');
  const refresh = form.querySelector('[data-audit-refresh]');
  let debounceTimer = null;
  let requestNumber = 0;

  function formUrl() {
    const params = new URLSearchParams(new FormData(form));
    for (const [key, value] of Array.from(params.entries())) {
      if (!value) params.delete(key);
    }
    const query = params.toString();
    const endpoint = form.getAttribute('action') || window.location.pathname;
    return endpoint + (query ? '?' + query : '');
  }

  function syncForm(url) {
    const params = new URL(url, window.location.origin).searchParams;
    for (const element of Array.from(form.elements)) {
      if (element.name) element.value = params.get(element.name) || '';
    }
  }

  function load(url, options) {
    const settings = Object.assign({ history: true, quiet: false, notify: true }, options || {});
    const currentRequest = ++requestNumber;
    results.setAttribute('aria-busy', 'true');
    results.classList.add('is-loading');
    if (refresh) refresh.classList.add('is-loading');

    return window.ERP.get(url, {
      silent: true,
      quiet: settings.quiet,
      headers: { 'Accept': 'text/html' }
    })
      .then((html) => {
        if (currentRequest !== requestNumber) return;
        if (typeof html !== 'string' || !html.includes('audit-summary')) {
          throw new Error(window.ERP.t('try_again', 'Please try again.'));
        }
        results.innerHTML = html;
        syncForm(url);
        if (settings.history) {
          const nextUrl = new URL(url, window.location.origin);
          window.history.pushState({}, '', nextUrl.pathname + nextUrl.search + nextUrl.hash);
        }
      })
      .catch((error) => {
        if (currentRequest !== requestNumber) return;
        if (settings.notify) {
          window.ERP.toast.error(
            window.ERP.t('request_failed', 'Request failed'),
            error.message || window.ERP.t('try_again', 'Please try again.')
          );
        }
      })
      .finally(() => {
        if (currentRequest !== requestNumber) return;
        results.setAttribute('aria-busy', 'false');
        results.classList.remove('is-loading');
        if (refresh) refresh.classList.remove('is-loading');
      });
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    load(formUrl());
  });

  form.querySelectorAll('select').forEach((select) => {
    select.addEventListener('change', () => load(formUrl()));
  });

  if (search) {
    search.addEventListener('input', () => {
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(() => load(formUrl()), 350);
    });
  }

  form.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      load(link.href);
    });
  });

  results.addEventListener('click', (event) => {
    const link = event.target.closest('.pagination-row a');
    if (!link) return;
    event.preventDefault();
    load(link.href);
    results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  if (refresh) refresh.addEventListener('click', () => load(window.location.href, { history: false }));
  window.addEventListener('popstate', () => load(window.location.href, { history: false }));

  window.setInterval(() => {
    if (window.navigator.onLine && !document.hidden && !results.querySelector('details[open]')) {
      load(window.location.href, { history: false, quiet: true, notify: false });
    }
  }, 30000);
})(window, document);
