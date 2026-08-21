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
                if (localStorage.getItem(key) === 'closed') {
                    panel.classList.remove('show');
                    btn.setAttribute('aria-expanded', 'false');
                }
            } catch (e) {}
            panel.addEventListener('shown.bs.collapse', function () {
                try { localStorage.setItem(key, 'open'); } catch (e) {}
            });
            panel.addEventListener('hidden.bs.collapse', function () {
                try { localStorage.setItem(key, 'closed'); } catch (e) {}
            });
        });

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
