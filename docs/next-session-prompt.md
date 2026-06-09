# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 19):

- Django 4.2 app, 37 models, 36 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- Deploy workflow: `git push` on Mac → SSH `scs-tech@10.58.58.82` → `cd /opt/murphys-bench && git pull && source venv/bin/activate && python3 manage.py migrate` → `kill -HUP <gunicorn-master-pid>`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues
- HTMX inline notes, checklist, ticket replies, Quick Labor, credentials, billing

**Session 19 additions:**
- **Status Management UI**: `StatusDefinition` model — configurable label + hex color per status, `entity_type` (ticket/workorder), `is_system` flag. Migration 0036 seeds 13 core statuses with default colors. Settings → Statuses tab: color picker for all statuses, custom status add/edit/delete. System statuses are color-editable but not deletable.
- **Template tag suite**: `{% status_badge slug entity_type %}` (styled HTML span), `{% status_label slug entity_type %}` (plain text), `{% status_color slug entity_type %}` (hex color). 2-minute module-level cache. Graceful fallback for unknown slugs.
- **Replaced all hardcoded badge patterns**: 11 templates updated — ticket_list, ticket_detail, work_order_list, work_order_detail, client_detail, device_detail, dashboard, queue_detail, sidebar_content, ticket_linked_list, work_order_print.
- **Dynamic form choices**: WorkOrderForm + TicketForm load status dropdowns from StatusDefinition (custom statuses appear automatically).
- **email_utils.py**: `status` context variable for email templates now resolved via StatusDefinition.

**Session 18 additions:**
- **Device-level credentials vault**: `device_username`, `device_password`, `credential_notes` (AES-256 encrypted) on `Device` model. `DeviceCredentialAccessLog` model logs every reveal/edit. `can_view_device_credentials` flag on `Role` (Admin=True, Technician=False). HTMX eye-reveal card on device detail page. Admin can edit; techs with flag can reveal; others see "contact admin" message. Migration 0035 applied to production.

**Session 17 additions:**
- **Invoice CSV export**: `/clients/<pk>/invoices.csv`, optional `?status=` filter, CSV button on client detail
- **Icon audit**: 10 new icons in `mb_icons.py`, all emoji/text symbols replaced across templates
- **Billing financial summary**: Reports page — Invoiced/Collected/Outstanding cards + outstanding-by-client table. CSV at `/reports/csv/billing/`
- **Org credentials vault**: `OrgCredential` + `CredentialAccessLog` models (migration 0034). Settings → Credentials tab. AES-256 encrypted. HTMX eye-reveal logs every access.
- **Email Template Manager**: Settings → Email Templates tab. Editable subject/body, active toggle, variable reference panel.
- **Team workload widget**: Dashboard (admin only) — open WOs + tickets per tech, sorted by load.
- **Technician performance report**: Reports page — completion %, avg resolution hours, open WOs. CSV export.

**Session 16 additions:**
- **`Invoice` model**: OneToOne on WorkOrder. Fields: `billing_status` (uninvoiced/invoiced/paid/paid_direct/disputed), `amount`, `invoiced_date`, `paid_date`, `payment_method`, `notes`. Auto-created on WO creation via `post_save` signal.
- **Migration 0033**: CreateModel + backfill RunPython. Applied to production.
- **`WorkOrderBillingUpdateView`**: HTMX POST at `/work-orders/<pk>/billing/`. Quick-action + full edit mode.
- **`billing_card.html`** partial: Alpine.js display/edit toggle, HTMX `hx-swap="outerHTML"`.
- **Client detail**: outstanding balance badge (yellow pill) next to "Work Order History" heading.

---

## What's next (session 18 options):

### Option A — Status Management UI (was Option A last session)
Native CRUD for ticket and WO statuses. Currently hardcoded enums — would require a new model (`CustomStatus`) and migration. Core statuses stay locked; custom statuses add/edit/delete with color picker. Suggested library per entity type.

### Option B — Data Management
Import wizard (CSV → map columns → preview → import), bulk export ZIP, deleted data recovery (requires soft-delete changes), reset tool. Substantial build.

### Option C — Device-level credentials
`password` field on Device model (AES-256 encrypted, masked display + eye icon reveal). Admin always; Technician based on role flag. Simpler than it sounds — follows OrgCredential pattern exactly.

### Option D — Something from daily use
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

- **Gunicorn restart on prod**: runs as `scs-tech`, no passwordless sudo. Use `kill -HUP <master-pid>` to reload workers. Master PID: `ps aux | grep 'gunicorn.*murphys_bench.wsgi' | awk 'NR==1{print $2}'`
- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py
- **Alpine.js**: CDN with `defer`. HTMX-swapped content reinitializes automatically via mutation observer.
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False`
- **Mileage Calculate CSRF**: Uses `document.querySelector('[name=csrfmiddlewaretoken]')` — do not revert
- **Google Maps API key**: Stored in SiteSettings (DB). Restricted to WAN IP in Google Cloud Console.
- **Production Python**: `python3` not `python`. Venv: `/opt/murphys-bench/venv/`
- **mb_icons templatetag**: `{% load mb_icons %}` at top of any template that uses `{% icon %}`. Partials need their own load tag.
- **Email template variable reference**: Must use `{% verbatim %}...{% endverbatim %}` to display `{{ }}` tokens in templates.

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
