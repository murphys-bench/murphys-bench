# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 23):

- Django 4.2 app, 41 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- **Gunicorn service name**: `murphys-bench.service` — restart with `sudo systemctl restart murphys-bench`
- **App path on server**: `/opt/murphys-bench/`
- **SSH**: `ssh -i ~/.ssh/id_ed25519 scs-tech@10.58.58.82`
- Deploy workflow: `git push` on Mac → SSH → `cd /opt/murphys-bench && git pull && source venv/bin/activate && python3 manage.py migrate` → `sudo systemctl restart murphys-bench`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues

**Session 23 additions:**

- **Converted ticket interaction fixed**: Edit, reply, Quick Actions, and Close Ticket button all work on converted tickets. `TicketCloseView` at `/tickets/<pk>/close/` sets status to `resolved` and clears `wo_complete`. Duplicate WO-complete card in right column removed.
- **Active/Closed tabs on ticket and WO lists**: Both lists have Active/Closed tab bar. `TICKET_CLOSED_STATUSES = ['resolved', 'closed']`. `WO_CLOSED_STATUSES = ['completed', 'cancelled']`. 'converted' is always active.
- **needs_response flag**: Armed by inbound email (created_by=None on TicketReply). Disarmed by staff customer-visible reply or manual dismiss with note. Amber banner on ticket detail and dashboard.
- **wo_complete flag**: Armed when WO moves to completed/cancelled. Cleared when ticket closes. Green banner on ticket detail.
- **Sequential numbering**: `TKT-00001`, `TKT-00002`, etc. WO inherits ticket suffix on conversion (`WO-00001`).
- **Assign/claim on tickets and WOs**: Admin can assign to any user, any user can claim. `TicketAssignView`, `WorkOrderClaimView`.
- **Open Tickets section on dashboard**: Above Open Work Orders, with amber/green dots for needs_response/wo_complete.
- **Team Workload counts converted tickets**: `open_ticket_statuses` includes 'converted'.
- **Page color settings** (Settings → Colors → Page Colors): Page Background, Title Bar Background, Title Text, Section Header — all CSS variable driven, light mode only (dark mode uses its own scheme). Migrations 0040–0041.
- **Light mode card depth**: `border: 1px solid #e2e8f0` + subtle shadow on all `bg-white rounded-lg/xl` cards.
- **Dark mode CSS fixes**: `html:not(.dark)` scope for all light-mode-only color variable rules. Section header color applies to card title bar divs only (not thead or content rows).

---

## What's next (session 24 options):

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
- **converted = active ticket status** — never in TICKET_CLOSED_STATUSES
- **WO statuses**: completed/cancelled are closed. 'closed' is not a valid WO status.

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
- **Dark mode**: `dark` class is on `<html>` (documentElement), NOT `<body>`. Use `html:not(.dark)` for light-mode-only CSS rules, NOT `body:not(.dark)`.
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
