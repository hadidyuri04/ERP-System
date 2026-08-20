/* ==========================================================================
   ERP System — Core front-end runtime
   Toasts · AJAX helpers · smooth transitions · POS cart · form utilities
   No jQuery. Bootstrap 5 bundle is the only hard dependency (for modals).
   ========================================================================== */

(function (window, document) {
  'use strict';

  const ERP = {};

  /* ------------------------------------------------------------------
     i18n bridge
     Strings that JS produces at runtime must still be translatable.
     base.html injects window.ERP_I18N = {...} from {% trans %} tags,
     so this file itself contains no untranslated user-facing text.
     ------------------------------------------------------------------ */
  const I18N = window.ERP_I18N || {};
  ERP.t = function (key, fallback) {
    return I18N[key] || fallback || key;
  };

  /* ------------------------------------------------------------------
     Locale / direction
     ------------------------------------------------------------------ */
  ERP.isRTL = document.documentElement.getAttribute('dir') === 'rtl';
  ERP.locale = document.documentElement.getAttribute('lang') || 'en';

  ERP.formatMoney = function (value, decimals) {
    const d = decimals === undefined ? 3 : decimals;   // JOD = 3 decimals per spec §10
    const n = Number(value || 0);
    return n.toLocaleString(ERP.locale === 'ar' ? 'ar-JO' : 'en-JO', {
      minimumFractionDigits: d,
      maximumFractionDigits: d
    });
  };

  ERP.formatQty = function (value) {
    return Number(value || 0).toLocaleString(ERP.locale === 'ar' ? 'ar-JO' : 'en-JO', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 3
    });
  };

  /* ==================================================================
     1. TOASTS
     ERP.toast.success('Saved', 'Product created successfully')
     ================================================================== */

  const ICONS = {
    success: 'bi-check-lg',
    error:   'bi-exclamation-triangle',
    warning: 'bi-exclamation-circle',
    info:    'bi-info-circle'
  };

  function toastStack() {
    let el = document.getElementById('toastStack');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toastStack';
      el.className = 'toast-stack';
      el.setAttribute('aria-live', 'polite');
      el.setAttribute('aria-atomic', 'true');
      document.body.appendChild(el);
    }
    return el;
  }

  function showToast(type, title, message, duration) {
    const ms = duration === undefined ? 4200 : duration;
    const stack = toastStack();

    const el = document.createElement('div');
    el.className = 'toast-erp is-' + type;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');

    el.innerHTML =
      '<div class="toast-ico"><i class="bi ' + (ICONS[type] || ICONS.info) + '"></i></div>' +
      '<div class="toast-body">' +
        '<p class="toast-title"></p>' +
        (message ? '<p class="toast-msg"></p>' : '') +
      '</div>' +
      '<button type="button" class="toast-x" aria-label="' +
        ERP.t('close', 'Close') + '"><i class="bi bi-x-lg"></i></button>' +
      (ms ? '<div class="toast-progress"></div>' : '');

    // textContent, not innerHTML — never inject server data into markup
    el.querySelector('.toast-title').textContent = title || '';
    if (message) el.querySelector('.toast-msg').textContent = message;

    const bar = el.querySelector('.toast-progress');
    if (bar) bar.style.animationDuration = ms + 'ms';

    function dismiss() {
      if (el.classList.contains('hiding')) return;
      el.classList.add('hiding');
      el.addEventListener('animationend', () => el.remove(), { once: true });
      setTimeout(() => el.remove(), 400);          // belt and braces
    }

    el.querySelector('.toast-x').addEventListener('click', dismiss);
    stack.appendChild(el);

    let timer = ms ? setTimeout(dismiss, ms) : null;
    if (timer) {
      el.addEventListener('mouseenter', () => { clearTimeout(timer); if (bar) bar.style.animationPlayState = 'paused'; });
      el.addEventListener('mouseleave', () => { timer = setTimeout(dismiss, 1200); if (bar) bar.style.animationPlayState = 'running'; });
    }
    return { dismiss };
  }

  ERP.toast = {
    show:    showToast,
    success: (t, m, d) => showToast('success', t || ERP.t('success', 'Success'), m, d),
    error:   (t, m, d) => showToast('error',   t || ERP.t('error', 'Error'), m, d === undefined ? 6000 : d),
    warning: (t, m, d) => showToast('warning', t || ERP.t('warning', 'Warning'), m, d),
    info:    (t, m, d) => showToast('info',    t || ERP.t('info', 'Notice'), m, d)
  };

  /* ==================================================================
     2. AJAX
     Thin fetch wrapper: CSRF, JSON, top progress bar, toast on failure.
     ================================================================== */

  ERP.getCookie = function (name) {
    const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return match ? decodeURIComponent(match.pop()) : null;
  };

  ERP.csrfToken = function () {
    const input = document.querySelector('[name=csrfmiddlewaretoken]');
    if (input) return input.value;
    return ERP.getCookie('csrftoken') || '';
  };

  const progress = {
    el: null,
    count: 0,
    node() {
      if (!this.el) {
        this.el = document.createElement('div');
        this.el.className = 'ajax-bar';
        document.body.appendChild(this.el);
      }
      return this.el;
    },
    start() {
      this.count++;
      const n = this.node();
      n.classList.add('active');
      n.style.width = '18%';
      setTimeout(() => { if (this.count) n.style.width = '72%'; }, 220);
    },
    done() {
      this.count = Math.max(0, this.count - 1);
      if (this.count) return;
      const n = this.node();
      n.style.width = '100%';
      setTimeout(() => {
        n.classList.remove('active');
        setTimeout(() => { n.style.width = '0'; }, 260);
      }, 180);
    }
  };

  ERP.request = function (url, options) {
    const opts = Object.assign({ method: 'GET', quiet: false }, options || {});
    const headers = Object.assign({
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json'
    }, opts.headers || {});

    if (!['GET', 'HEAD', 'OPTIONS'].includes(opts.method.toUpperCase())) {
      headers['X-CSRFToken'] = ERP.csrfToken();
    }

    let body = opts.body;
    if (opts.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(opts.json);
    }

    if (!opts.quiet) progress.start();

    return fetch(url, {
      method: opts.method,
      headers: headers,
      body: body,
      credentials: 'same-origin'
    })
      .then(async (res) => {
        const ct = res.headers.get('content-type') || '';
        const data = ct.includes('application/json') ? await res.json() : await res.text();
        if (!res.ok) {
          const err = new Error((data && data.detail) || res.statusText);
          err.status = res.status;
          err.data = data;
          throw err;
        }
        return data;
      })
      .catch((err) => {
        if (!opts.silent) {
          ERP.toast.error(
            ERP.t('request_failed', 'Request failed'),
            err.message || ERP.t('try_again', 'Please try again.')
          );
        }
        throw err;
      })
      .finally(() => { if (!opts.quiet) progress.done(); });
  };

  ERP.get  = (url, o) => ERP.request(url, Object.assign({ method: 'GET' }, o));
  ERP.post = (url, data, o) => ERP.request(url, Object.assign({ method: 'POST', json: data }, o));

  /* ------------------------------------------------------------------
     AJAX form submit — progressive enhancement.
     Any <form data-ajax> posts without a page reload and toasts the result.
     Expected JSON: { ok: true, message: "...", redirect: "/optional/" }
                    { ok: false, errors: {field: ["msg"]}, message: "..." }
     ------------------------------------------------------------------ */
  ERP.bindAjaxForms = function (root) {
    (root || document).querySelectorAll('form[data-ajax]:not([data-ajax-bound])').forEach((form) => {
      form.setAttribute('data-ajax-bound', '1');

      form.addEventListener('submit', function (e) {
        e.preventDefault();
        ERP.clearFormErrors(form);

        const btn = form.querySelector('[type=submit]');
        const original = btn ? btn.innerHTML : null;
        if (btn) {
          btn.disabled = true;
          btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>' +
                          ERP.t('saving', 'Saving...');
        }

        ERP.request(form.action || window.location.href, {
          method: (form.method || 'POST').toUpperCase(),
          body: new FormData(form),
          silent: true
        })
          .then((data) => {
            if (data && data.ok === false) {
              if (data.errors) ERP.applyFormErrors(form, data.errors);
              ERP.toast.error(ERP.t('save_failed', 'Could not save'),
                              data.message || ERP.t('check_fields', 'Please check the highlighted fields.'));
              return;
            }
            ERP.toast.success(ERP.t('saved', 'Saved'),
                              (data && data.message) || ERP.t('changes_saved', 'Your changes have been saved.'));
            if (data && data.redirect) {
              setTimeout(() => ERP.navigate(data.redirect), 700);
            } else if (form.hasAttribute('data-reset-on-success')) {
              form.reset();
            }
            form.dispatchEvent(new CustomEvent('erp:saved', { detail: data, bubbles: true }));
          })
          .catch((err) => {
            if (err.data && err.data.errors) ERP.applyFormErrors(form, err.data.errors);
            ERP.toast.error(ERP.t('save_failed', 'Could not save'),
                            err.message || ERP.t('try_again', 'Please try again.'));
          })
          .finally(() => {
            if (btn) { btn.disabled = false; btn.innerHTML = original; }
          });
      });
    });
  };

  ERP.clearFormErrors = function (form) {
    form.querySelectorAll('.is-invalid').forEach((el) => el.classList.remove('is-invalid'));
    form.querySelectorAll('.invalid-feedback[data-generated]').forEach((el) => el.remove());
  };

  ERP.applyFormErrors = function (form, errors) {
    let first = null;
    Object.keys(errors).forEach((field) => {
      const input = form.querySelector('[name="' + field + '"]');
      if (!input) return;
      input.classList.add('is-invalid');
      if (!first) first = input;
      const msg = document.createElement('div');
      msg.className = 'invalid-feedback d-block';
      msg.setAttribute('data-generated', '1');
      msg.textContent = Array.isArray(errors[field]) ? errors[field].join(' ') : errors[field];
      input.insertAdjacentElement('afterend', msg);
    });
    if (first) { first.focus(); first.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  };

  /* ==================================================================
     3. SMOOTH NAVIGATION
     Fades the page out before navigating so module switches don't flash.
     ================================================================== */

  ERP.navigate = function (url) {
    const main = document.querySelector('[data-page-root]');
    if (!main || !url || url === '#') { window.location.href = url; return; }
    main.classList.add('page-leaving');
    setTimeout(() => { window.location.href = url; }, 190);
  };

  ERP.bindSmoothLinks = function () {
    document.addEventListener('click', function (e) {
      const a = e.target.closest('a[data-smooth]');
      if (!a) return;
      const href = a.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('http') || a.target === '_blank') return;
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      ERP.navigate(href);
    });
  };

  /* ------------------------------------------------------------------
     Live AJAX table filtering.
     <input data-live-filter="#tableId"> filters rows client-side while
     the backend endpoint is not ready; if data-url is present it debounces
     a server round-trip and swaps in the returned <tbody>.
     ------------------------------------------------------------------ */
  ERP.bindLiveFilters = function (root) {
    (root || document).querySelectorAll('[data-live-filter]:not([data-filter-bound])').forEach((input) => {
      input.setAttribute('data-filter-bound', '1');
      const target = document.querySelector(input.getAttribute('data-live-filter'));
      if (!target) return;
      const url = input.getAttribute('data-url');
      let timer;

      input.addEventListener('input', function () {
        clearTimeout(timer);
        const q = input.value.trim().toLowerCase();

        timer = setTimeout(() => {
          if (url) {
            const body = target.querySelector('tbody');
            if (body) body.style.opacity = '.45';
            ERP.get(url + (url.includes('?') ? '&' : '?') + 'q=' + encodeURIComponent(q), { quiet: true })
              .then((html) => {
                if (body && typeof html === 'string') body.innerHTML = html;
                if (body) body.style.opacity = '1';
                ERP.refreshEmptyState(target);
              })
              .catch(() => { if (body) body.style.opacity = '1'; });
            return;
          }

          // Client-side fallback
          let visible = 0;
          target.querySelectorAll('tbody tr[data-row]').forEach((tr) => {
            const hit = !q || tr.textContent.toLowerCase().includes(q);
            tr.hidden = !hit;
            if (hit) visible++;
          });
          ERP.refreshEmptyState(target, visible);
        }, url ? 320 : 90);
      });
    });
  };

  ERP.refreshEmptyState = function (table, visible) {
    const holder = table.closest('[data-table-card]');
    if (!holder) return;
    const empty = holder.querySelector('[data-empty]');
    if (!empty) return;
    const count = visible !== undefined
      ? visible
      : table.querySelectorAll('tbody tr[data-row]:not([hidden])').length;
    empty.hidden = count > 0;
    table.hidden = count === 0;
  };

  /* ==================================================================
     4. LINE-ITEM GRID  (purchase invoice, quotation, waste & loss)
     Adds/removes rows and keeps totals live. Mirrors the spec's
     subtotal / discount / tax / total field structure.
     ================================================================== */

  ERP.LineGrid = function (rootSelector) {
    const root = document.querySelector(rootSelector);
    if (!root) return null;

    const tbody = root.querySelector('tbody');
    const tpl = root.querySelector('template[data-row-template]');
    let seq = tbody.querySelectorAll('tr').length;

    function recalcRow(tr) {
      const q  = parseFloat(tr.querySelector('[data-f=quantity]')?.value) || 0;
      const c  = parseFloat(tr.querySelector('[data-f=unit_cost]')?.value) || 0;
      const d  = parseFloat(tr.querySelector('[data-f=discount_amount]')?.value) || 0;
      const t  = parseFloat(tr.querySelector('[data-f=tax_amount]')?.value) || 0;
      const line = Math.max(0, (q * c) - d + t);
      const out = tr.querySelector('[data-f=line_total]');
      if (out) {
        if (out.tagName === 'INPUT') out.value = line.toFixed(3);
        else out.textContent = ERP.formatMoney(line);
      }
      return line;
    }

    function recalcAll() {
      let subtotal = 0, discount = 0, tax = 0;
      tbody.querySelectorAll('tr').forEach((tr) => {
        const q = parseFloat(tr.querySelector('[data-f=quantity]')?.value) || 0;
        const c = parseFloat(tr.querySelector('[data-f=unit_cost]')?.value) || 0;
        subtotal += q * c;
        discount += parseFloat(tr.querySelector('[data-f=discount_amount]')?.value) || 0;
        tax      += parseFloat(tr.querySelector('[data-f=tax_amount]')?.value) || 0;
        recalcRow(tr);
      });
      const extra = parseFloat(document.querySelector('[data-f=additional_expenses]')?.value) || 0;
      const total = subtotal - discount + tax + extra;

      setOut('subtotal', subtotal);
      setOut('total_discount', discount);
      setOut('total_tax', tax);
      setOut('grand_total', total);
      root.dispatchEvent(new CustomEvent('erp:totals', {
        detail: { subtotal, discount, tax, extra, total }, bubbles: true
      }));
    }

    function setOut(name, value) {
      document.querySelectorAll('[data-out="' + name + '"]').forEach((el) => {
        if (el.tagName === 'INPUT') el.value = value.toFixed(3);
        else el.textContent = ERP.formatMoney(value);
      });
    }

    function addRow() {
      if (!tpl) return;
      const frag = tpl.content.cloneNode(true);
      const tr = frag.querySelector('tr');
      // Django formset-friendly indexing: name="items-0-product" etc.
      tr.innerHTML = tr.innerHTML.replace(/__prefix__/g, seq);
      seq++;
      tbody.appendChild(tr);
      tr.querySelector('input, select')?.focus();
      recalcAll();
      return tr;
    }

    root.addEventListener('input', (e) => {
      if (e.target.matches('[data-f]')) recalcAll();
    });

    root.addEventListener('click', (e) => {
      if (e.target.closest('[data-add-row]')) { e.preventDefault(); addRow(); }
      const del = e.target.closest('[data-del-row]');
      if (del) {
        e.preventDefault();
        const tr = del.closest('tr');
        if (tbody.querySelectorAll('tr').length <= 1) {
          ERP.toast.warning(ERP.t('cannot_remove', 'Cannot remove'),
                            ERP.t('need_one_line', 'An invoice needs at least one line.'));
          return;
        }
        tr.style.transition = 'opacity .18s, transform .18s';
        tr.style.opacity = '0';
        tr.style.transform = 'translateX(-14px)';
        setTimeout(() => { tr.remove(); recalcAll(); }, 180);
      }
    });

    document.querySelectorAll('[data-add-row]').forEach((b) =>
      b.addEventListener('click', (e) => { e.preventDefault(); addRow(); }));
    document.querySelector('[data-f=additional_expenses]')
      ?.addEventListener('input', recalcAll);

    recalcAll();
    return { addRow, recalcAll };
  };

  /* ==================================================================
     5. POS CART
     ================================================================== */

  ERP.POS = {
    items: [],
    taxRate: 0,          // set from CompanySettings when backend is ready
    discount: 0,

    add(product) {
      const found = this.items.find((i) => i.id === product.id);
      if (found) {
        found.qty += (product.qty || 1);
      } else {
        this.items.push({
          id: product.id,
          code: product.code || '',
          name: product.name,
          price: Number(product.price) || 0,
          qty: product.qty || 1,
          unit: product.unit || ''
        });
      }
      this.render();
      ERP.toast.success(ERP.t('added_to_cart', 'Added to cart'), product.name, 1800);
    },

    setQty(id, qty) {
      const item = this.items.find((i) => i.id === id);
      if (!item) return;
      if (qty <= 0) return this.remove(id);
      item.qty = qty;
      this.render();
    },

    remove(id) {
      this.items = this.items.filter((i) => i.id !== id);
      this.render();
    },

    clear() {
      this.items = [];
      this.discount = 0;
      this.render();
    },

    totals() {
      const subtotal = this.items.reduce((s, i) => s + i.price * i.qty, 0);
      const tax = (subtotal - this.discount) * this.taxRate;
      return { subtotal, discount: this.discount, tax, total: subtotal - this.discount + tax };
    },

    render() {
      const list = document.getElementById('posCartItems');
      const empty = document.getElementById('posCartEmpty');
      if (!list) return;

      list.innerHTML = '';
      if (empty) empty.hidden = this.items.length > 0;

      this.items.forEach((i) => {
        const row = document.createElement('div');
        row.className = 'cart-row';
        row.innerHTML =
          '<div class="flex-grow-1 min-w-0">' +
            '<p class="c-name text-truncate"></p>' +
            '<span class="c-meta"></span>' +
          '</div>' +
          '<div class="qty-stepper">' +
            '<button type="button" data-act="dec" aria-label="' + ERP.t('decrease', 'Decrease') + '"><i class="bi bi-dash"></i></button>' +
            '<span class="q"></span>' +
            '<button type="button" data-act="inc" aria-label="' + ERP.t('increase', 'Increase') + '"><i class="bi bi-plus"></i></button>' +
          '</div>' +
          '<span class="c-total"></span>' +
          '<button type="button" class="btn-icon danger" data-act="del" aria-label="' + ERP.t('remove', 'Remove') + '"><i class="bi bi-x-lg"></i></button>';

        row.querySelector('.c-name').textContent = i.name;
        row.querySelector('.c-meta').textContent = ERP.formatMoney(i.price) + ' × ' + ERP.formatQty(i.qty) + ' ' + i.unit;
        row.querySelector('.q').textContent = ERP.formatQty(i.qty);
        row.querySelector('.c-total').textContent = ERP.formatMoney(i.price * i.qty);

        row.addEventListener('click', (e) => {
          const act = e.target.closest('[data-act]')?.dataset.act;
          if (act === 'inc') this.setQty(i.id, i.qty + 1);
          if (act === 'dec') this.setQty(i.id, i.qty - 1);
          if (act === 'del') this.remove(i.id);
        });

        list.appendChild(row);
      });

      const t = this.totals();
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = ERP.formatMoney(v); };
      set('posSubtotal', t.subtotal);
      set('posDiscount', t.discount);
      set('posTax', t.tax);
      set('posTotal', t.total);

      const countEl = document.getElementById('posItemCount');
      if (countEl) countEl.textContent = this.items.length;

      const payBtn = document.getElementById('posPayBtn');
      if (payBtn) payBtn.disabled = this.items.length === 0;
    }
  };

  /* ==================================================================
     6. MISC UI
     ================================================================== */

  // Mobile sidebar
  ERP.bindSidebar = function () {
    const sidebar = document.querySelector('.sidebar');
    const toggle = document.querySelector('[data-sidebar-toggle]');
    const closeButton = document.querySelector('[data-sidebar-close]');
    if (!sidebar || !toggle) return;

    let backdrop = document.querySelector('.sidebar-backdrop');
    if (!backdrop) {
      backdrop = document.createElement('div');
      backdrop.className = 'sidebar-backdrop';
      document.body.appendChild(backdrop);
    }
    const close = () => {
      sidebar.classList.remove('open');
      backdrop.classList.remove('show');
      toggle.setAttribute('aria-expanded', 'false');
    };
    toggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
      backdrop.classList.toggle('show', sidebar.classList.contains('open'));
      toggle.setAttribute('aria-expanded', sidebar.classList.contains('open') ? 'true' : 'false');
    });
    if (closeButton) closeButton.addEventListener('click', close);
    sidebar.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => {
      if (window.innerWidth < 992) close();
    }));
    backdrop.addEventListener('click', close);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  };

  // Count-up animation on dashboard stats
  ERP.animateCounters = function (root) {
    const els = (root || document).querySelectorAll('[data-countup]:not([data-counted])');
    els.forEach((el) => {
      el.setAttribute('data-counted', '1');
      const target = parseFloat(el.getAttribute('data-countup')) || 0;
      const dec = parseInt(el.getAttribute('data-decimals') || '2', 10);
      const prefix = el.getAttribute('data-prefix') || '';
      const duration = 900;
      const start = performance.now();

      function tick(now) {
        const p = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - p, 3);
        el.textContent = prefix + (target * eased).toLocaleString(undefined, {
          minimumFractionDigits: dec, maximumFractionDigits: dec
        });
        if (p < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  };

  // Confirm-before-action, styled toast instead of window.confirm
  ERP.bindConfirms = function () {
    document.addEventListener('click', (e) => {
      const el = e.target.closest('[data-confirm]');
      if (!el || el.dataset.confirmed) return;
      e.preventDefault();
      const modalEl = document.getElementById('confirmModal');
      if (!modalEl) {
        if (window.confirm(el.getAttribute('data-confirm'))) {
          el.dataset.confirmed = '1'; el.click();
        }
        return;
      }
      modalEl.querySelector('[data-confirm-text]').textContent = el.getAttribute('data-confirm');
      const ok = modalEl.querySelector('[data-confirm-ok]');
      const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
      const handler = () => {
        modal.hide();
        el.dataset.confirmed = '1';
        el.click();
        ok.removeEventListener('click', handler);
      };
      ok.addEventListener('click', handler);
      modal.show();
    });
  };

  // Language switcher — posts to Django's set_language view
  ERP.bindLanguageSwitch = function () {
    document.querySelectorAll('[data-set-language]').forEach((el) => {
      el.addEventListener('click', function (e) {
        e.preventDefault();
        const form = document.getElementById('langForm');
        if (!form) return;
        form.querySelector('[name=language]').value = el.getAttribute('data-set-language');
        form.submit();
      });
    });
  };

  // Django messages framework → toasts
  ERP.flushServerMessages = function () {
    document.querySelectorAll('[data-server-message]').forEach((el) => {
      const level = el.getAttribute('data-level') || 'info';
      const map = { debug: 'info', info: 'info', success: 'success', warning: 'warning', error: 'error' };
      ERP.toast.show(map[level] || 'info', el.getAttribute('data-title') || '', el.textContent.trim());
      el.remove();
    });
  };

  // Surface normal Django form validation through the same toast system.
  // Inline errors remain in place so users can still identify each field.
  ERP.toastFormErrors = function (root) {
    const scope = root || document;
    const messages = [];
    const seen = new Set();

    scope.querySelectorAll('.form-error, .journal-form-errors, form .errorlist').forEach((el) => {
      const message = (el.textContent || '').replace(/\s+/g, ' ').trim();
      if (!message || seen.has(message)) return;
      seen.add(message);
      messages.push(message);
    });

    if (!messages.length) return;

    const detail = messages.slice(0, 3).join(' • ');
    ERP.toast.error(
      ERP.t('save_failed', 'Could not save'),
      detail || ERP.t('check_fields', 'Please check the highlighted fields.'),
      8000
    );
  };

  /* ==================================================================
     Boot
     ================================================================== */

  ERP.bindNavGroups = function () {
    const groups = document.querySelectorAll('.sidebar .nav-group');
    groups.forEach((group) => group.addEventListener('toggle', () => {
      if (!group.open) return;
      groups.forEach((other) => { if (other !== group) other.open = false; });
    }));

    const activeLink = document.querySelector('.sidebar-nav .nav-icon.active');
    if (activeLink) {
      requestAnimationFrame(() => activeLink.scrollIntoView({ block: 'nearest' }));
    }
  };

  ERP.init = function () {
    ERP.bindSidebar();
    ERP.bindNavGroups();
    ERP.bindAjaxForms();
    ERP.bindLiveFilters();
    ERP.bindSmoothLinks();
    ERP.bindConfirms();
    ERP.bindLanguageSwitch();
    ERP.animateCounters();
    ERP.flushServerMessages();
    ERP.toastFormErrors();

    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((el) => {
      if (window.bootstrap) new bootstrap.Tooltip(el);
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ERP.init);
  } else {
    ERP.init();
  }

  window.ERP = ERP;

})(window, document);
