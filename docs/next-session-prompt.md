# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 16):

- Django 4.2 app, 33 models, 33 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- Deploy workflow: `git push` on Mac → SSH `scs-tech@10.58.58.82` → `cd /opt/murphys-bench && git pull && source venv/bin/activate && python3 manage.py migrate` → `kill -HUP <gunicorn-master-pid>`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues
- HTMX inline notes, checklist, ticket replies, Quick Labor, credentials, billing

**Session 16 additions:**
- **`Invoice` model**: OneToOne on WorkOrder. Fields: `billing_status` (uninvoiced/invoiced/paid/paid_direct/disputed), `amount`, `invoiced_date`, `paid_date`, `payment_method`, `notes`. Auto-created on WO creation via `post_save` signal.
- **Migration 0033**: CreateModel + backfill RunPython. Applied to production.
- **`WorkOrderBillingUpdateView`**: HTMX POST at `/work-orders/<pk>/billing/`. Quick-action mode (just `billing_status`): updates status, auto-sets dates on first transition. Full edit mode (`full_edit=1`): updates all fields.
- **`billing_card.html`** partial: `id="billing-card"`, Alpine.js display/edit toggle, HTMX `hx-swap="outerHTML"`. Quick-action buttons are contextual per status.
- **WO detail**: billing card in right column between "Update Work Order" and "Device Credentials"
- **Client detail**: outstanding balance badge (yellow pill) next to "Work Order History" heading

**Session 15 additions:**
- **Color-coded dashboard tiles**: `_tile_color()` in views.py computes color from `status_filter`/`link_url`. Blue=active, Yellow=waiting, Red=overdue, Green=completed.
- **SVG icon templatetag**: `core/templatetags/mb_icons.py` — `{% icon name size %}`. Load with `{% load mb_icons %}`.
- **Device type icon grid**: Alpine.js, hidden input, 7 types, replaces dropdown on device form.
- Migration 0032: emoji → icon name strings in DashboardTile
- **Production fully deployed**: migrations 0031–0033, `FIELD_ENCRYPTION_KEY` in prod `.env`, key in Bitwarden

---

## What's next (session 17 options):

### Option A — CSV export for Invoice records
Simple view that exports invoice data per client as CSV for accounting import. Would tie off the billing module.
- `InvoiceExportView` at `/clients/<pk>/invoices.csv/` or `/invoices/export/?client=<pk>`
- Columns: WO#, client, description, amount, billing_status, invoiced_date, paid_date, payment_method, notes

### Option B — Native Settings UI expansion
From TODO.md Priority 3 items (see Batch 11 spec):
- **Repair Types** native CRUD (currently admin-only) — add `RepairTypeCategory` model, sort_order, full CRUD UI at `/settings/repair-types/`
- **Canned Responses** native CRUD — two note streams (Customer Notes / Tech Notes Internal), categories, CRUD, picker on WO detail
- **Quick Labor** native CRUD (currently admin-only)

### Option C — Site-wide icon audit
Replace remaining text symbols (×, ⚠, 🔑, etc.) with SVG icons via `{% icon %}`. Quick polish pass.

---

## Key decisions locked (do not re-litigate):

- **Credential encryption**: AES-256, FIELD_ENCRYPTION_KEY from env, key in Bitwarden
- **Billing philosophy**: MB tracks state only — not an accounting module. Invoice Ninja authoritative.
- **Invoice model**: separate entity on WO (not fields on WO) — `paid_direct` for cash/walk-in before formal invoice
- **Visual design is a first-class requirement**: color + icons communicate status faster than text
- Permanently Delete blocks if client has WOs; offers Deactivate instead
- Address: 5 fields — Line 1, Line 2 (optional), City, State, Zip. No country.
- Colors: stored in SiteSettings, rendered as CSS variables in `<style>` block in base.html
- Ticket close is always manual even when linked WO closes

---

## Known gotchas (read before touching these areas):

- **Gunicorn restart on prod**: runs as `scs-tech`, no passwordless sudo. Use `kill -HUP <master-pid>` to reload workers. Master PID: `ps aux | grep 'gunicorn.*murphys_bench.wsgi' | awk 'NR==1{print $2}'`
- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py
- **Alpine.js**: CDN with `defer`. HTMX-swapped content reinitializes automatically via mutation observer.
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
