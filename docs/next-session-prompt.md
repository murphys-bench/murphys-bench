# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 15):

- Django 4.2 app, 32 models, 32 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- Deploy workflow: `git push` on Mac → SSH `scs-tech@10.58.58.82` → `cd /opt/murphys-bench && git pull && source venv/bin/activate && python3 manage.py migrate` → `kill -HUP <gunicorn-master-pid>`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues
- HTMX inline notes, checklist, ticket replies, Quick Labor, credentials

**Session 15 additions:**
- **Color-coded dashboard tiles**: left-border accent, color computed from `status_filter`/`link_url` in `_tile_color()`. Blue=active, Yellow=waiting, Red=overdue, Green=completed.
- **SVG icon templatetag**: `core/templatetags/mb_icons.py` — `{% icon name size %}`. Heroicons v1 outline paths. Load with `{% load mb_icons %}`.
- **Device type icon grid**: replaces dropdown on device form. Alpine.js, hidden input, 7 types, selected state highlighted blue.
- Migration 0032: converts DashboardTile emoji icon values → icon name strings
- **Production fully deployed**: migrations 0031 + 0032, `FIELD_ENCRYPTION_KEY` in prod `.env`, key saved to Bitwarden

**Session 14 additions:**
- Credential encryption (migration 0031): `WorkOrder` device credentials + `SiteSettings` email passwords — AES-256 at rest via `django-encrypted-model-fields`
- `FIELD_ENCRYPTION_KEY` in settings.py (reads from env)

**Session 13 additions:**
- Cross-visibility panels: open tickets on WO detail, open WOs on ticket detail
- WO toolbar: linked ticket as clickable purple pill
- Sidebar: last reply/note preview instead of subject

---

## What's next:

### Immediate (session 16 — Invoice model)

**Basic billing tracker** — lightweight billing state on WorkOrder. No accounting, no financials. MB tracks state only; Invoice Ninja remains authoritative.

Build in this order:

1. **`Invoice` model** — One-to-one on WorkOrder (separate entity, not fields on WO):
   - `billing_status` CharField: `uninvoiced` / `invoiced` / `paid` / `paid_direct` / `disputed`
   - `invoiced_date` DateField (nullable)
   - `paid_date` DateField (nullable)
   - `payment_method` CharField (nullable): cash / check / card / transfer / other
   - `amount` DecimalField (nullable — optional, WO amount may not be known at intake)
   - `notes` TextField (blank)
   - Auto-created as `uninvoiced` when WO is created (signal or override)

2. **WO detail billing card** — Read-only display + HTMX inline edit:
   - Shows current status as colored badge
   - Quick-action buttons: "Mark Invoiced", "Mark Paid", "Mark Paid Direct"
   - Edit form: full fields (status, date, method, amount, notes)

3. **Client detail balance** — Show sum of uninvoiced + invoiced amounts on client detail page

4. **CSV export** — Simple export of invoice records for accounting system import

### Also queued (separate sessions)
- **Native Admin section**, **Cloudflare tunnel**, **Testing suite**, **Client Portal**, **Reporting enhancements** — see TODO.md

---

## Key decisions locked (do not re-litigate):

- **Credential encryption**: AES-256, FIELD_ENCRYPTION_KEY from env, key in Bitwarden
- **Billing philosophy**: MB tracks state only — not an accounting module. Invoice Ninja authoritative.
- **Invoice model**: separate entity on WO (not fields on WO) — `paid_direct` for cash/walk-in before formal invoice
- **Visual design is a first-class requirement**: color + icons communicate status faster than text

- Permanently Delete blocks if client has WOs; offers Deactivate instead
- Address: 5 fields — Line 1, Line 2 (optional), City, State, Zip. No country.
- Colors: stored in SiteSettings, rendered as CSS variables in `<style>` block in base.html
- WorkPerformed: labor_item nullable; custom_label + notes allow per-job customization
- Ticket close is always manual even when linked WO closes

---

## Known gotchas (read before touching these areas):

- **Gunicorn restart on prod**: runs as `scs-tech`, no passwordless sudo. Use `kill -HUP <master-pid>` to reload workers. Master PID: `ps aux | grep 'gunicorn.*murphys_bench.wsgi' | awk 'NR==1{print $2}'`
- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py
- **Alpine.js**: CDN with `defer`. HTMX-swapped content may need `Alpine.initTree(el)` in htmx:afterSwap
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False`
- **Mileage Calculate CSRF**: Uses `document.querySelector('[name=csrfmiddlewaretoken]')` — do not revert
- **Google Maps API key**: Stored in SiteSettings (DB). Restricted to WAN IP in Google Cloud Console.
- **Production Python**: `python3` not `python`. Venv: `/opt/murphys-bench/venv/`
- **mb_icons templatetag**: `{% load mb_icons %}` at top of any template that uses `{% icon %}`

---

## General rules for this project:

- All views use `LoginRequiredMixin`
- HTMX loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models (both dev and prod)
- Commit and push when complete; deploy with git pull + gunicorn reload on server
