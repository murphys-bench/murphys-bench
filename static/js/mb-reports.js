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
        wolist: 'Work Orders', tickettime: 'Ticket Time Logged', wotime: 'Work Order Time Logged'
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

    function downloadPDF(key) {
        var el = key === 'all' ? document.getElementById('reports-content') : document.getElementById('section-' + key);
        if (!el || !window.html2pdf) { alert('Section not available.'); return; }
        var r = range();
        html2pdf().set({
            margin: 10,
            filename: 'report-' + (key === 'all' ? 'all-reports' : key) + '-' + r.start + '-to-' + r.end + '.pdf',
            image: { type: 'jpeg', quality: 0.95 },
            html2canvas: { scale: 2, useCORS: true },
            jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
        }).from(el).save();
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
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
