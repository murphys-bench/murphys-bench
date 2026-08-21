/* The frame's own behaviour: theme toggle, sidebar collapse, sidebar scroll
 * memory. Everything else in the chrome is Bootstrap data-attributes. No
 * inline handlers anywhere; this file is loaded deferred and binds by
 * data-mb-* attributes so templates carry no script. */
(function () {
    'use strict';
    var el = document.documentElement;

    function setTheme(dark) {
        el.setAttribute('data-bs-theme', dark ? 'dark' : 'light');
        try { localStorage.setItem('mb_dark_mode', dark ? 'true' : 'false'); } catch (e) {}
        document.querySelectorAll('[data-mb-theme-label]').forEach(function (n) {
            n.textContent = dark ? 'Light mode' : 'Dark mode';
        });
    }

    function setCollapsed(collapsed) {
        if (collapsed) {
            el.setAttribute('data-sidebar-collapsed', 'true');
        } else {
            el.removeAttribute('data-sidebar-collapsed');
        }
        try { localStorage.setItem('mb_nav_collapsed', collapsed ? 'true' : 'false'); } catch (e) {}
    }

    function init() {
        document.querySelectorAll('[data-mb-toggle="theme"]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                setTheme(el.getAttribute('data-bs-theme') !== 'dark');
            });
        });
        document.querySelectorAll('[data-mb-toggle="sidebar"]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                setCollapsed(!el.hasAttribute('data-sidebar-collapsed'));
            });
        });
        // Label reflects the stored state on load, not just after a click.
        document.querySelectorAll('[data-mb-theme-label]').forEach(function (n) {
            n.textContent = el.getAttribute('data-bs-theme') === 'dark' ? 'Light mode' : 'Dark mode';
        });

        // Room groups in the sidebar remember open/closed per browser.
        document.querySelectorAll('[data-mb-group]').forEach(function (btn) {
            var id = btn.getAttribute('data-mb-group');
            var panel = document.getElementById(id);
            if (!panel) return;
            var key = 'mb_nav_group_' + id;
            try {
                var stored = localStorage.getItem(key);
                if (stored === 'closed') {
                    panel.classList.remove('show');
                    btn.setAttribute('aria-expanded', 'false');
                } else if (stored === 'open') {
                    panel.classList.add('show');
                    btn.setAttribute('aria-expanded', 'true');
                }
            } catch (e) {}
            panel.addEventListener('shown.bs.collapse', function () {
                try { localStorage.setItem(key, 'open'); } catch (e) {}
            });
            panel.addEventListener('hidden.bs.collapse', function () {
                try { localStorage.setItem(key, 'closed'); } catch (e) {}
            });
        });

        // Destructive actions confirm in a modal, never window.confirm (design
        // rules section 7). Mark the <form> with data-mb-confirm="what will happen".
        var modalEl = document.getElementById('mb-confirm');
        if (modalEl && window.bootstrap) {
            var modal = new bootstrap.Modal(modalEl);
            var pending = null;
            function ask(text, onOk) {
                pending = onOk;
                modalEl.querySelector('[data-mb-confirm-text]').textContent = text;
                modal.show();
            }
            document.addEventListener('submit', function (ev) {
                var form = ev.target;
                if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-mb-confirm')) return;
                if (form.hasAttribute('hx-post') || form.hasAttribute('hx-get')) return; // htmx:confirm handles these
                if (form.dataset.mbConfirmed === '1') { form.dataset.mbConfirmed = ''; return; }
                ev.preventDefault();
                ask(form.getAttribute('data-mb-confirm'), function () {
                    form.dataset.mbConfirmed = '1';
                    form.requestSubmit ? form.requestSubmit() : form.submit();
                });
            }, true);
            // HTMX requests: the form (or button) carries data-mb-confirm; the request is
            // held until the modal's OK, then issued with the confirm skipped.
            document.addEventListener('htmx:confirm', function (ev) {
                var el = ev.target.closest ? ev.target.closest('[data-mb-confirm]') : null;
                if (!el) return;
                ev.preventDefault();
                ask(el.getAttribute('data-mb-confirm'), function () { ev.detail.issueRequest(true); });
            });
            modalEl.querySelector('[data-mb-confirm-ok]').addEventListener('click', function () {
                if (!pending) return;
                var fn = pending; pending = null;
                modal.hide();
                fn();
            });
        }

        // Forms that reset themselves after a successful HTMX post:
        // <form data-mb-reset-after="#collapse-id"> (the id is optional; when
        // given, that collapse closes too). Delegated, so rows HTMX swaps in later
        // still get it.
        document.addEventListener('htmx:afterRequest', function (ev) {
            var form = ev.target;
            if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-mb-reset-after')) return;
            if (!ev.detail.successful) return;
            form.reset();
            var target = form.getAttribute('data-mb-reset-after');
            if (target && window.bootstrap) {
                var el = document.querySelector(target);
                if (el) bootstrap.Collapse.getOrCreateInstance(el).hide();
            }
        });

        // Show a block only while a given radio/checkbox value is selected:
        // <div data-mb-show-for="reply_type=customer_visible">
        function bindShowFor(root) {
            root.querySelectorAll('[data-mb-show-for]').forEach(function (el) {
                if (el.dataset.mbShowForBound) return;
                el.dataset.mbShowForBound = '1';
                var pair = el.getAttribute('data-mb-show-for').split('=');
                var name = pair[0], value = pair[1];
                function sync() {
                    var checked = document.querySelector('input[name="' + name + '"]:checked');
                    el.classList.toggle('d-none', !(checked && checked.value === value));
                }
                document.querySelectorAll('input[name="' + name + '"]').forEach(function (r) { r.addEventListener('change', sync); });
                sync();
            });
        }
        bindShowFor(document);
        document.addEventListener('htmx:afterSwap', function (ev) { bindShowFor(ev.target); });

        // Color pickers: <div data-mb-color> holds an <input type=color>, a hex
        // <input type=text>, and optionally [data-mb-color-preview] whose
        // background (or color, with data-mb-color-preview="fg") tracks the value.
        function bindColors(root) {
            root.querySelectorAll('[data-mb-color]').forEach(function (box) {
                if (box.dataset.mbColorBound) return;
                box.dataset.mbColorBound = '1';
                var pick = box.querySelector('input[type="color"]'), hex = box.querySelector('input[type="text"]');
                var prev = box.querySelector('[data-mb-color-preview]');
                function paint(v) {
                    if (!prev) return;
                    if (prev.getAttribute('data-mb-color-preview') === 'fg') prev.style.color = v; else prev.style.background = v;
                }
                if (pick && hex) {
                    pick.addEventListener('input', function () { hex.value = pick.value; paint(pick.value); });
                    hex.addEventListener('input', function () { if (/^#[0-9a-fA-F]{6}$/.test(hex.value)) { pick.value = hex.value; paint(hex.value); } });
                    if (hex.value && /^#[0-9a-fA-F]{6}$/.test(hex.value)) pick.value = hex.value;
                    paint(hex.value || pick.value);
                }
            });
        }
        bindColors(document);
        document.addEventListener('htmx:afterSwap', function (ev) { bindColors(ev.target); });

        // Restore picker: choosing a backup fills the form and enables the button;
        // the confirm text names the choice. <div data-mb-restore-picker>
        document.addEventListener('change', function (ev) {
            var radio = ev.target;
            if (!(radio instanceof HTMLInputElement) || radio.name !== 'archive_choice') return;
            var box = radio.closest('[data-mb-restore-picker]');
            if (!box) return;
            var form = box.querySelector('form[data-mb-restore-form]');
            if (!form) return;
            form.querySelector('[name="archive"]').value = radio.value;
            form.querySelector('[name="source"]').value = radio.getAttribute('data-source') || '';
            var label = radio.getAttribute('data-label') || radio.value;
            form.setAttribute('data-mb-confirm', 'Restore the backup taken ' + label + '? Everything created since then will be permanently gone, and Murphy\'s Bench will restart.');
            var btn = form.querySelector('button[type="submit"]');
            if (btn && !btn.hasAttribute('data-mb-locked')) { btn.disabled = false; btn.textContent = 'Restore the backup from ' + label; }
        });

        // <button data-mb-print-page> prints the page (backup tokens).
        document.addEventListener('click', function (ev) {
            if (ev.target.closest('[data-mb-print-page]')) window.print();
        });

        // Repeating rows: <button data-mb-add-row="#tpl-id" data-mb-add-row-into="#list-id">
        // clones the <template> into the list; [data-mb-remove-row] removes its row.
        document.addEventListener('click', function (ev) {
            var add = ev.target.closest('[data-mb-add-row]');
            if (add) {
                var tpl = document.querySelector(add.getAttribute('data-mb-add-row'));
                var into = document.querySelector(add.getAttribute('data-mb-add-row-into'));
                if (tpl && into) into.appendChild(tpl.content.cloneNode(true));
                return;
            }
            var rm = ev.target.closest('[data-mb-remove-row]');
            if (rm) {
                var row = rm.closest('[data-mb-row]');
                if (row) row.remove();
            }
        });

        // Typed-name confirmation: <input data-mb-confirm-name="Exact Name" data-mb-enables="#btn">
        document.querySelectorAll('[data-mb-confirm-name]').forEach(function (inp) {
            var btn = document.querySelector(inp.getAttribute('data-mb-enables'));
            if (!btn) return;
            function sync() { btn.disabled = inp.value !== inp.getAttribute('data-mb-confirm-name'); }
            inp.addEventListener('input', sync); sync();
        });

        // Mileage calculator (WO mileage form): posts origin/destination to the
        // server-side Distance Matrix proxy and fills the miles field.
        var calc = document.querySelector('[data-mb-mileage-calc]');
        if (calc) {
            var url = calc.getAttribute('data-mb-mileage-calc');
            var q = function (n) { return document.querySelector('[name="' + n + '"]'); };
            var oneWayOut = document.getElementById('one-way-display'), roundOut = document.getElementById('round-trip-display');
            var summary = document.getElementById('distance-summary');
            var oneWay = null, roundTrip = null;
            function syncMiles() {
                if (oneWay === null) return;
                q('miles').value = q('trip_type').value === 'one_way' ? oneWay : roundTrip;
            }
            q('trip_type').addEventListener('change', syncMiles);
            calc.addEventListener('click', function () {
                var origin = q('from_location').value.trim(), dest = q('to_location').value.trim();
                if (!origin || !dest) { summary.textContent = 'Enter both origin and destination first.'; return; }
                calc.disabled = true;
                var csrf = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
                fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                             body: JSON.stringify({ origin: origin, destination: dest }) })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.error) { summary.textContent = 'Error: ' + data.error; return; }
                        oneWay = data.one_way; roundTrip = data.round_trip;
                        oneWayOut.value = oneWay; roundOut.value = roundTrip;
                        summary.textContent = oneWay + ' miles one way, ' + roundTrip + ' miles round trip.';
                        syncMiles();
                    })
                    .catch(function () { summary.textContent = 'Could not reach the distance service.'; })
                    .finally(function () { calc.disabled = false; });
            });
        }

        // Sidebar scroll position survives full-page navigation (short laptop
        // screens scroll the nav; snapping to top on every click was a reported
        // annoyance on the old frame and the fix carries over).
        var nav = document.getElementById('mb-nav-scroll');
        if (nav) {
            var KEY = 'mb_nav_scroll';
            try {
                var saved = sessionStorage.getItem(KEY);
                if (saved !== null) nav.scrollTop = parseInt(saved, 10) || 0;
                nav.addEventListener('scroll', function () {
                    sessionStorage.setItem(KEY, nav.scrollTop);
                }, { passive: true });
            } catch (e) {}
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
