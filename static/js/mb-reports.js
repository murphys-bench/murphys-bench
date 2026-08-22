/* Reports page: CSV export, print one section, PDF of one section, and the
 * charts. Bound by data-mb-* attributes; chart data comes from json_script
 * blobs on the page, so the page carries no inline script. */
(function () {
    'use strict';

    var SECTION_LABELS = {
        billing: 'Billing Summary', countersales: 'Counter Sales', revenue: 'Revenue',
        techperf: 'Technician Performance', volume: 'Ticket Volume', status: 'Tickets by Status',
        byclient: 'Tickets by Client', bytech: 'Tickets by Technician', resolution: 'Resolution Time',
        sla: 'SLA Compliance', backlog: 'Backlog Health', conversion: 'Conversion Rate',
        mileage: 'Mileage', wostatus: 'Work Orders by Status', wobyclient: 'Work Orders by Client',
        wolist: 'Work Orders', woweek: 'Work Orders per Week', tickettime: 'Ticket Time Logged', wotime: 'Work Order Time Logged'
    };
    var CHART_COLORS = ['#206bc4', '#2fb344', '#f59f00', '#d63939', '#6f32be', '#d6336c', '#0ca678', '#f76707', '#4263eb', '#74b816'];

    function blob(id) {
        var el = document.getElementById(id);
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }
    function range() {
        return { start: document.getElementById('rpt-start').value, end: document.getElementById('rpt-end').value };
    }

    function downloadCSV(url) {
        var r = range();
        window.location = url + '?start_date=' + r.start + '&end_date=' + r.end;
    }

    // Print one section (or all) through a hidden iframe, no extra window.
    function printSection(key) {
        var target = key === 'all'
            ? document.getElementById('reports-content').innerHTML
            : (document.getElementById('section-' + key) || {}).innerHTML;
        if (!target) { alert('Section not available.'); return; }
        var label = key === 'all' ? 'All Reports' : (SECTION_LABELS[key] || key);
        var r = range();
        var frame = document.createElement('iframe');
        frame.style.cssText = 'position:fixed;top:-9999px;left:-9999px;width:1px;height:1px;border:0';
        document.body.appendChild(frame);
        var doc = frame.contentDocument || frame.contentWindow.document;
        doc.open();
        doc.write('<html><head><title>' + label + '</title>');
        doc.write('<style>body{font-family:sans-serif;margin:24px;color:#111}table{border-collapse:collapse;width:100%}th,td{border:1px solid #e5e7eb;padding:6px 10px;font-size:13px;text-align:left}th{background:#f9fafb;font-weight:600}h3{font-size:16px;margin-bottom:12px}.text-end{text-align:right}p,div{font-size:13px}.d-print-none,.card-actions,canvas{display:none}</style>');
        doc.write('</head><body>');
        doc.write('<p style="color:#888;font-size:12px;margin-bottom:8px">' + label + ' &nbsp;|&nbsp; ' + r.start + ' to ' + r.end + '</p>');
        doc.write(target);
        doc.write('</body></html>');
        doc.close();
        frame.onload = function () {
            frame.contentWindow.focus();
            frame.contentWindow.print();
            document.body.removeChild(frame);
        };
    }

    // PDF: made on the server (WeasyPrint), like every other paper MB produces.
    // The browser contributes only the pictures of its charts (PNG of each
    // canvas inside the chosen section); the server checks each one is a real
    // PNG of sane size before it goes into the document. Posted as a plain
    // form so the browser handles the download itself.
    function downloadPDF(key) {
        var menu = document.querySelector('[data-mb-pdf-url]');
        var el = key === 'all' ? document.getElementById('reports-content') : document.getElementById('section-' + key);
        if (!el || !menu) { alert('Section not available.'); return; }
        var r = range();
        var form = document.createElement('form');
        form.method = 'post'; form.action = menu.getAttribute('data-mb-pdf-url'); form.style.display = 'none';
        function add(name, value) { var i = document.createElement('input'); i.type = 'hidden'; i.name = name; i.value = value; form.appendChild(i); }
        var csrf = (document.querySelector('input[name=csrfmiddlewaretoken]') || {}).value;
        if (!csrf) { try { csrf = JSON.parse(document.body.getAttribute('hx-headers') || '{}')['X-CSRFToken']; } catch (e) {} }
        add('csrfmiddlewaretoken', csrf || '');
        add('domain', (document.querySelector('#reports-date-form [name=domain]') || {}).value || '');
        add('start_date', r.start); add('end_date', r.end); add('section', key);
        var g = document.querySelector('select[name=granularity]'); if (g) add('granularity', g.value);
        el.querySelectorAll('canvas[id]').forEach(function (c) {
            try { add('chart_' + c.id, c.toDataURL('image/png')); } catch (e) { /* a chart that cannot be read is simply left out */ }
        });
        document.body.appendChild(form); form.submit(); form.remove();
    }

    function charts() {
        if (!window.Chart) return;
        var labels = blob('rpt-volume-labels'), data = blob('rpt-volume-data');
        var c = document.getElementById('chartVolume');
        if (c && labels && data) {
            new Chart(c, { type: 'bar', data: { labels: labels, datasets: [{ label: 'Tickets', data: data, backgroundColor: CHART_COLORS[0], borderRadius: 3 }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } } });
        }
        labels = blob('rpt-status-labels'); data = blob('rpt-status-data');
        c = document.getElementById('chartStatus');
        if (c && labels && data) {
            new Chart(c, { type: 'doughnut', data: { labels: labels, datasets: [{ data: data, backgroundColor: CHART_COLORS, borderWidth: 2 }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } } });
        }
        labels = blob('rpt-client-labels'); data = blob('rpt-client-data');
        c = document.getElementById('chartClients');
        if (c && labels && data) {
            new Chart(c, { type: 'bar', data: { labels: labels, datasets: [{ label: 'Tickets', data: data, backgroundColor: CHART_COLORS, borderRadius: 3 }] },
                options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } } });
        }
    }

    // Generic charts: one JSON blob keyed by canvas id (see ReportsView chart_data).
    function genericCharts() {
        var all = blob('rpt-chart-data');
        if (!all || !window.Chart) return;
        Object.keys(all).forEach(function (id) {
            var c = document.getElementById(id), spec = all[id];
            if (!c || !spec || !spec.labels || !spec.labels.length) { if (c) c.parentNode.innerHTML = '<p class="text-secondary mb-0">No data for this period.</p>'; return; }
            var opts = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: spec.type === 'doughnut', position: 'right' } } };
            if (spec.type !== 'doughnut') {
                var axis = spec.horizontal ? 'x' : 'y';
                opts.indexAxis = spec.horizontal ? 'y' : 'x';
                opts.scales = {}; opts.scales[axis] = { beginAtZero: true, ticks: { precision: 0 } };
                if (spec.max) opts.scales[axis].max = spec.max;
            }
            new Chart(c, { type: spec.type, data: { labels: spec.labels, datasets: [{ label: spec.label || '', data: spec.data,
                backgroundColor: spec.type === 'doughnut' || spec.horizontal ? CHART_COLORS : CHART_COLORS[0], borderRadius: spec.type === 'doughnut' ? 0 : 3, borderWidth: spec.type === 'doughnut' ? 2 : 0 }] }, options: opts });
        });
    }

    function init() {
        document.addEventListener('click', function (ev) {
            var a = ev.target.closest('[data-mb-csv], [data-mb-print], [data-mb-pdf]');
            if (!a) return;
            ev.preventDefault();
            if (a.hasAttribute('data-mb-csv')) downloadCSV(a.getAttribute('data-mb-csv'));
            else if (a.hasAttribute('data-mb-print')) printSection(a.getAttribute('data-mb-print'));
            else downloadPDF(a.getAttribute('data-mb-pdf'));
        });
        var g = document.querySelector('[data-mb-submit-on-change]');
        if (g) g.addEventListener('change', function () { g.form.submit(); });
        charts();
        genericCharts();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
