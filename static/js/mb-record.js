/* Behaviour for the Work Record page (core/work_record.html). Bound by data-mb-*
 * attributes; no inline script, no Alpine. One file, loaded deferred. */
(function () {
    'use strict';

    function onReady(fn) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn();
    }

    onReady(function () {

        // ── Ticket lock: release on leave ─────────────────────────────────
        var lock = document.querySelector('[data-mb-lock-release]');
        if (lock) {
            var url = lock.getAttribute('data-mb-lock-release');
            var csrf = lock.getAttribute('data-mb-csrf');
            window.addEventListener('beforeunload', function () {
                navigator.sendBeacon(url, new URLSearchParams({ csrfmiddlewaretoken: csrf }));
            });
        }

        // ── Draft autosave: <textarea data-mb-draft="key"> ─────────────────
        document.querySelectorAll('[data-mb-draft]').forEach(function (ta) {
            var key = ta.getAttribute('data-mb-draft');
            try {
                var saved = localStorage.getItem(key);
                if (saved && !ta.value) ta.value = saved;
                ta.addEventListener('input', function () { localStorage.setItem(key, ta.value); });
                var form = ta.closest('form');
                if (form) form.addEventListener('htmx:afterRequest', function (ev) {
                    if (ev.detail.successful) { localStorage.removeItem(key); form.reset(); }
                });
            } catch (e) {}
        });

        // ── Forms that reset themselves after a successful HTMX post ──────
        document.querySelectorAll('form[data-mb-reset-after]').forEach(function (form) {
            form.addEventListener('htmx:afterRequest', function (ev) {
                if (!ev.detail.successful) return;
                form.reset();
                var target = form.getAttribute('data-mb-reset-after');
                if (target && window.bootstrap) {
                    var el = document.querySelector(target);
                    if (el) bootstrap.Collapse.getOrCreateInstance(el).hide();
                }
            });
        });

        // ── Show a block only while a given radio value is selected ───────
        // <div data-mb-show-for="reply_type=customer_visible">
        document.querySelectorAll('[data-mb-show-for]').forEach(function (el) {
            var pair = el.getAttribute('data-mb-show-for').split('=');
            var name = pair[0], value = pair[1];
            function sync() {
                var checked = document.querySelector('input[name="' + name + '"]:checked');
                el.classList.toggle('d-none', !(checked && checked.value === value));
            }
            document.querySelectorAll('input[name="' + name + '"]').forEach(function (r) { r.addEventListener('change', sync); });
            sync();
        });

        // ── Notes order (newest first by default, remembered per browser) ──
        document.querySelectorAll('[data-mb-order-toggle]').forEach(function (btn) {
            var list = document.querySelector(btn.getAttribute('data-mb-order-toggle'));
            if (!list) return;
            var KEY = 'mb_wo_notes_order';
            function apply(order) {
                list.classList.toggle('flex-column-reverse', order === 'newest');
                list.classList.toggle('flex-column', order !== 'newest');
                btn.textContent = order === 'newest' ? 'Newest first' : 'Oldest first';
            }
            var order = 'newest';
            try { order = localStorage.getItem(KEY) || 'newest'; } catch (e) {}
            apply(order);
            btn.addEventListener('click', function () {
                order = order === 'newest' ? 'oldest' : 'newest';
                try { localStorage.setItem(KEY, order); } catch (e) {}
                apply(order);
            });
        });

        // ── Canned responses: fetch the picker, insert at the cursor ───────
        document.querySelectorAll('[data-mb-canned]').forEach(function (btn) {
            var menu = document.querySelector(btn.getAttribute('data-mb-canned'));
            var textarea = document.querySelector(btn.getAttribute('data-mb-canned-target'));
            var radioName = btn.getAttribute('data-mb-canned-stream-radio');
            if (!menu || !textarea) return;
            btn.addEventListener('click', function () {
                var checked = radioName ? document.querySelector('input[name="' + radioName + '"]:checked') : null;
                var stream = checked && checked.value === 'customer_visible' ? 'customer' : 'internal';
                menu.innerHTML = '<div class="dropdown-item text-secondary">Loading…</div>';
                fetch(btn.getAttribute('data-mb-canned-url') + '?stream=' + stream, { credentials: 'same-origin' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        menu.innerHTML = '';
                        if (!data.groups || !data.groups.length) {
                            menu.innerHTML = '<div class="dropdown-item text-secondary">No canned responses yet.</div>';
                            return;
                        }
                        data.groups.forEach(function (g) {
                            var h = document.createElement('div'); h.className = 'dropdown-header'; h.textContent = g.category; menu.appendChild(h);
                            g.items.forEach(function (item) {
                                var a = document.createElement('button'); a.type = 'button'; a.className = 'dropdown-item'; a.textContent = item.label;
                                a.addEventListener('click', function () {
                                    var start = textarea.selectionStart, before = textarea.value.substring(0, start), after = textarea.value.substring(textarea.selectionEnd);
                                    textarea.value = before + (before && !before.endsWith('\n') ? '\n' : '') + item.body + after;
                                    textarea.dispatchEvent(new Event('input'));
                                    textarea.focus();
                                });
                                menu.appendChild(a);
                            });
                        });
                    });
            });
        });

        // ── Timer: one per page, state in localStorage keyed by the record ─
        document.querySelectorAll('[data-mb-timer]').forEach(function (root) {
            var KEY = root.getAttribute('data-mb-timer');
            var display = root.querySelector('[data-mb-timer-display]');
            var startBtn = root.querySelector('[data-mb-timer-start]');
            var pauseBtn = root.querySelector('[data-mb-timer-pause]');
            var resetBtn = root.querySelector('[data-mb-timer-reset]');
            var minutes = root.querySelector('[data-mb-timer-minutes]');
            var logBtn = root.querySelector('[data-mb-timer-log]');
            var label = root.querySelector('[data-mb-timer-label]');
            var form = root.querySelector('form');
            var interval = null;
            function load() { try { var raw = localStorage.getItem(KEY); return raw ? JSON.parse(raw) : { accumulated: 0, startedAt: null }; } catch (e) { return { accumulated: 0, startedAt: null }; } }
            function save(st) { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} }
            function elapsed(st) { return st.accumulated + (st.startedAt ? Date.now() - st.startedAt : 0); }
            function fmt(ms) { var s = Math.floor(ms / 1000); return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map(function (n) { return String(n).padStart(2, '0'); }).join(':'); }
            function ui() {
                var st = load(), ms = elapsed(st), min = Math.floor(ms / 60000);
                display.textContent = fmt(ms); minutes.value = min; label.textContent = min;
                startBtn.disabled = !!st.startedAt; pauseBtn.disabled = !st.startedAt; logBtn.disabled = min === 0;
            }
            function start() { var st = load(); if (!st.startedAt) { st.startedAt = Date.now(); save(st); } clearInterval(interval); interval = setInterval(ui, 1000); ui(); }
            function pause() { var st = load(); if (st.startedAt) { st.accumulated += Date.now() - st.startedAt; st.startedAt = null; save(st); } clearInterval(interval); ui(); }
            function reset() { clearInterval(interval); save({ accumulated: 0, startedAt: null }); ui(); }
            startBtn.addEventListener('click', start);
            pauseBtn.addEventListener('click', pause);
            resetBtn.addEventListener('click', reset);
            if (form) form.addEventListener('htmx:afterRequest', function (ev) { if (ev.detail.successful) reset(); });
            var st = load();
            if (st.startedAt) start(); else ui();
        });
    });
})();
