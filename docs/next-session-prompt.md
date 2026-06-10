# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 21):

- Django 4.2 app, 37 models, 37 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- **Gunicorn service name**: `murphys-bench.service` — restart with `sudo systemctl restart murphys-bench`
- **App path on server**: `/opt/murphys-bench/`
- Deploy workflow: `git push` on Mac → SSH `scs-tech@10.58.58.82` → `cd /opt/murphys-bench && git pull && source venv/bin/activate && python3 manage.py migrate` → `sudo systemctl restart murphys-bench`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues

**Session 21 additions:**
- **Ticket contact FK** (migration 0037): `Ticket.contact` nullable FK to `Contact` — same pattern as `WorkOrder.contact`. Email replies route to `ticket.contact.email` first, then fall back to primary contact. Inbound emails auto-set contact from matched sender.
- **HTMX contact cascade on ticket form**: Selecting a client dynamically loads that client's contacts into the Contact dropdown. Endpoint: `GET /tickets/contacts-by-client/?client=<id>`.
- **Ticket contact shown on ticket detail**: Name + email in the info panel.
- **Reply resend**: Each customer-visible reply has a "Resend" button. Pick any client contact or type a custom address — fires the same reply email to that recipient.
- **CC on replies**: Reply form shows a CC field (comma-separated) when Customer Visible is selected. Included as BCC on that send only, not saved.
- **Native User management**: `/users/new/`, `/users/<pk>/edit/`, `/users/<pk>/set-password/`. Full user CRUD — name, username, email, phone, role, is_staff, is_active, password. No Django admin needed.
- **Native Role management**: `/roles/` — list all roles with 17 permission flags shown as ✓/✗ grid. Create, edit, delete. System roles protected. Edit page shows all flags as checkboxes.
- **Users + Roles in Settings sidebar**: Both appear at the bottom of the Settings nav and link to their pages. Each page has "← Settings" back link. Roles also has "← Users".

---

## What's next (session 22 options):

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
- **mb_icons templatetag**: `{% load mb_icons %}` at top of any template that uses `{% icon %}`, `{% attr %}`, or `{% getfield %}`. Partials need their own load tag.
- **Email template variable reference**: Must use `{% verbatim %}...{% endverbatim %}` to display `{{ }}` tokens in templates.
- **Inbound email timer**: systemd user timer as `scs-tech` — `mb-inbound.timer`, runs every 5 min. Log: `/home/scs-tech/mb-inbound.log`. Enabled linger: `loginctl enable-linger scs-tech`.

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
