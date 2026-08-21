/* Runs synchronously in <head>, before first paint, so the page never flashes
 * the wrong theme or an expanded sidebar that then snaps shut. Deliberately an
 * external file, not an inline <script>: the rebuild's goal is a CSP without
 * 'unsafe-inline', and this is the one script that cannot be deferred. */
(function () {
    var el = document.documentElement;
    try {
        if (localStorage.getItem('mb_dark_mode') === 'true') {
            el.setAttribute('data-bs-theme', 'dark');
        } else {
            el.setAttribute('data-bs-theme', 'light');
        }
        var size = localStorage.getItem('mb_text_size');
        if (size && /^1[4-9]$|^20$/.test(size)) el.style.fontSize = size + 'px';
        if (localStorage.getItem('mb_nav_collapsed') === 'true') {
            el.setAttribute('data-sidebar-collapsed', 'true');
        }
    } catch (e) {
        /* localStorage unavailable (private mode, locked-down browser): stay light, expanded */
    }
})();
