# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 22):

- Django 4.2 app, 37 models, 37 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- **Gunicorn service name**: `murphys-bench.service` — restart with `sudo systemctl restart murphys-bench`
- **App path on server**: `/opt/murphys-bench/`
- Deploy workflow: `git push` on Mac → SSH `scs-tech@10.58.58.82` → `cd /opt/murphys-bench && git pull && source venv/bin/activate && python3 manage.py migrate` → `sudo systemctl restart murphys-bench`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues

**Session 22 additions:**
- **UI polish — search bars inline**: Tickets, Work Orders, Clients, Mileage, KB list pages all have search/filter controls moved into the page header bar. Also fixed missing technician options in WO assigned_to dropdown.
- **Mileage decimal fix**: Total miles now shows `67.6` not `67.6000000000000` (floatformat:1) — in list, reports template, and CSV export.
- **Ticket reply type**: Radio buttons instead of dropdown. Redundant "Add Reply ↓" Quick Actions button removed.
- **KB Markdown rendering**: `markdown` library installed. Articles render full Markdown — headings, bold, lists, fenced code blocks, tables. `{% load mb_icons %}` fix applied. Tailwind typography plugin loaded via CDN.
- **KB Categories in Settings**: Native CRUD tab at Settings → KB Categories. No Django admin needed.
- **Dark mode**: Per-user toggle (moon/sun) in sidebar footer. Persisted to `localStorage` — no flash on load. CSS override strategy covers surfaces, text, borders, inputs, tables, tinted panels (blue-50/yellow-50/green-50), colored text (blue/green/red/yellow/purple), prose. `darkMode: 'class'` configured in Tailwind.
- **My Work sidebar section removed**: Was redundant in practice. Dead CSS rule cleaned up.
- **Dashboard stat cards**: Active Clients and Devices on File are now clickable links to `/clients/` and `/devices/`.
- **Reports page overhaul**: All CSV download buttons removed from individual sections. Header bar now has three export dropdowns — **Export CSV ▼** (pick report → download CSV), **Print ▼** (pick section → hidden iframe print, no popup tab), **PDF ▼** (pick section → html2pdf.js download). "All Sections" option available in Print and PDF dropdowns.

---

## What's next (session 23 options):

### Option A — Inbound email overhaul
Smarter intake: junk/noise filtering, unmatched sender handling, new ticket notifications (in-app badge + email alert), visual "unread" indicator on ticket list (bold row + dot, clears on first open). The inbound timer runs every 5 min via systemd user timer (`mb-inbound.timer`). Log at `/home/scs-tech/mb-inbound.log`.

### Option B — Data Management
Import wizard (CSV → map columns → preview → import), bulk export ZIP, deleted data recovery.

### Option C — Something from daily use
Any friction points or gaps SCS has noticed in actual use since deployment.

---

## Key decisions locked (do not re-litigate):

- **Credential encryption**: AES-256, FIELD_ENCRYPTION_KEY from env, key in Bitwarden
- **Billing philosophy**: MB tracks state only — not an accounting module. Invoice Ninja authoritative.
- **Invoice model**: separate entity on WO (not fields on WO) — `paid_direct` for cash/walk-in
- **Visual design is a first-class requirement**: color + icons communicate status faster than text
- **Modals for quick edits, full pages for complex creation**
- **Soft-delete everything** (hard deletes require admin deliberate action)
- **Export-based integrations** — CSV works with any accounting system
- **Org credentials vault is a competitive advantage** over RepairShopCRM
- Permanently Delete blocks if client has WOs; offers Deactivate instead
- Address: 5 fields — Line 1, Line 2 (optional), City, State, Zip. No country.
- Colors: stored in SiteSettings, rendered as CSS variables in `<style>` block in base.html
- Ticket close is always manual even when linked WO closes

---

## Known gotchas (read before touching these areas):

- **Gunicorn service**: `murphys-bench.service` — NOT `gunicorn.service`. Restart: `sudo systemctl restart murphys-bench`
- **App path on server**: `/opt/murphys-bench/` — NOT `~/murphys-bench/`
- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py
- **Alpine.js**: CDN with `defer`. HTMX-swapped content reinitializes automatically via mutation observer.
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False`
- **Mileage Calculate CSRF**: Uses `document.querySelector('[name=csrfmiddlewaretoken]')` — do not revert
- **Google Maps API key**: Stored in SiteSettings (DB). Restricted to WAN IP in Google Cloud Console.
- **Production Python**: `python3` not `python`. Venv: `/opt/murphys-bench/venv/`
- **mb_icons templatetag**: `{% load mb_icons %}` at top of any template that uses `{% icon %}`, `{% attr %}`, `{% getfield %}`, or `{% markdownify %}`. Partials need their own load tag.
- **Email template variable reference**: Must use `{% verbatim %}...{% endverbatim %}` to display `{{ }}` tokens in templates.
- **Inbound email timer**: systemd user timer as `scs-tech` — `mb-inbound.timer`, runs every 5 min. Log: `/home/scs-tech/mb-inbound.log`. Enabled linger: `loginctl enable-linger scs-tech`.
- **Dark mode**: CSS override strategy in base.html — `darkMode: 'class'` in Tailwind config, `dark` class toggled on `<html>` via localStorage. Per-user, persisted across sessions.
- **Tailwind CDN**: Loaded with `?plugins=typography` for KB prose rendering.

---

## General rules for this project:

- All views use `LoginRequiredMixin`
- HTMX loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models (both dev and prod)
- Commit and push when complete; deploy with git pull + service restart on server
