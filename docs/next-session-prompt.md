# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 14):

- Django 4.2 app, 36+ models, 31 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- Deploy workflow: `git push` on Mac → SSH to server → `git pull && python manage.py migrate && sudo systemctl restart murphys-bench`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues
- HTMX inline notes, checklist, ticket replies, Quick Labor, credentials

**Session 14 additions:**
- **Credential encryption (migration 0031)**: `WorkOrder.device_username`, `device_password`, `device_pin`, `credential_notes` and `SiteSettings.email_password`, `inbound_password` now AES-256 encrypted at rest via `django-encrypted-model-fields`
- `FIELD_ENCRYPTION_KEY` added to settings.py (reads from env, dev fallback only)
- `encrypted_model_fields` added to INSTALLED_APPS and requirements.txt
- `.env.example` updated with key generation instructions
- **⚠️ Production deployment of migration 0031 is PENDING** — must set `FIELD_ENCRYPTION_KEY` in production env BEFORE pulling. Do this together. See `docs/` or memory for steps.

**Session 13 additions:**
- Cross-visibility: open tickets panel on WO detail, open WOs panel on ticket detail (status, last note/reply, one-click nav)
- WO detail toolbar: linked ticket shown as clickable purple pill (← TKT-XXXXX)
- Converted tickets now stay visible in sidebar, dashboard tile, and cross-visibility panels until resolved/closed
- History tab removed from ticket detail (same as WO detail)
- Sidebar: shows last reply/note preview instead of subject/description; falls back if no notes yet
- Mileage Calculate button: fixed CSRF token for production (was silently failing)
- Google Maps API confirmed working from production server (WAN IP restriction in Cloud Console)

**Batch 12 — COMPLETE (session 12):**
- Client list redesigned to match legacy layout (ACCOUNT/TYPE/CONTACT/PHONE/EMAIL/DEVICES/WOs)
- Settings: site logo, nav text color, sidebar bg/text color, font size dropdowns
- Sidebar: opacity-based text hierarchy (works with any sidebar bg color)
- WO detail — Device card: inline reassign device dropdown
- WO detail — Details card: inline edit (repair type, assigned to, scheduled date, contact, invoice ref) + custom repair type on the fly
- WO detail — Work Performed: editable entries (SVG pencil/trash), custom log form, collapsible Log Work buttons; WorkPerformed model now has custom_label + notes, labor_item nullable
- WO detail — Pre/Post Checklist: pre_check + post_check dropdowns (Pass/Fail/N/A), auto-save on change, color-coded rows, checked count in header
- WO detail — Credentials: display-only default, PIN masked, Edit toggle
- WO detail — Add Note: radio buttons for note type; History tab removed; Attachments tab has upload form

---

## What's next:

### Immediate (session 15 — visual polish, already queued)
Dashboard template already read and understood. Build in this order:

1. **Color-coded dashboard metric tiles** — Status-colored cards replacing uniform gray. Convention: Blue=active/in-progress, Yellow=waiting, Red=overdue, Green=complete, Gray=new/unassigned. Number color matches accent. DashboardTile model already has `icon` and `status_filter` fields to drive this.
2. **SVG icons replacing emoji** — Dashboard tiles, quick stats row, sidebar. Use Heroicons (consistent with Tailwind). Mapping: 🎫→ticket, ⚙️→cog, ⏳→clock, 🔴→exclamation-circle, 🔧→wrench, ✅→check-circle, 📋→clipboard-list, 🏢→office-building, 💻→desktop-computer.
3. **Device type icon grid** — Replace Device Type dropdown with 2×4 visual icon button grid (Laptop, Desktop, Tablet, Phone, Server, Printer, Monitor, Other). Selected state highlighted. Pattern from RepairShopCRM.

### Also queued (separate sessions)
- **Invoice model** — Lightweight billing tracker: `Invoice` entity on WO, `billing_status` enum (uninvoiced/invoiced/paid/paid_direct/disputed), payment metadata, customer balance on client detail. MB tracks state only — not an accounting module.
- **Production deploy of migration 0031** — Together. Set FIELD_ENCRYPTION_KEY in prod env first, then pull + migrate.
- **Native Admin section**, **Cloudflare tunnel**, **Testing suite**, **Client Portal**, **Reporting enhancements** — see TODO.md

---

## Key decisions locked (do not re-litigate — includes session 14):

- **Credential encryption**: AES-256 via django-encrypted-model-fields. FIELD_ENCRYPTION_KEY from env. Never plaintext.
- **Billing philosophy**: MB tracks billing state only (uninvoiced/invoiced/paid). Not an accounting module. Invoice Ninja or other system is authoritative for formal financials.
- **Invoice model**: Lightweight `Invoice` entity on WO — not fields on WO. Allows WO scope to change before invoicing. billing_status includes `paid_direct` for cash/walk-in.
- **Visual design is a first-class requirement**: Color + icons communicate status faster than text. Not optional polish.

- Permanently Delete blocks if client has WOs; offers Deactivate instead
- Address: 5 fields — Line 1, Line 2 (optional), City, State, Zip. No country.
- Colors: stored in SiteSettings, rendered as CSS variables in `<style>` block in base.html
- ChecklistItem: flat bank by device type — no repair-type template chain
- Pre/Post checklist: pre_check/post_check fields on WorkOrderItem; WorkOrderItemCheckView handles HTMX updates
- Display settings: localStorage only, no DB
- CannedResponse: stream (customer/internal) + optional category FK
- WorkPerformed: labor_item nullable; custom_label + notes allow per-job customization; ordered by logged_at

---

## Known gotchas (read before touching these areas):

- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py
- **`?assigned_to=me`**: Handled in TicketListView and WorkOrderListView
- **Alpine.js**: CDN with `defer`. HTMX-swapped content may need `Alpine.initTree(el)` in htmx:afterSwap
- **Queue filter_criteria JSON**: `assigned_to: null` (explicit null) means "unassigned only"
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False`
- **ContactPhone phones in Alpine edit form**: Saved as `phone_number[]` / `phone_type[]` / `phone_label[]` POST arrays
- **Settings tabs**: Special tabs use `None` FormClass, inject context via `_*_context()` helpers
- **Checklist apply**: `_apply_checklist_items(work_order)` — uses get_or_create to avoid duplicates
- **Status badges**: Use `wo-status-badge status-{key}` classes — CSS variables from SiteSettings
- **site_settings context processor**: Injects `site_settings` into every template
- **WorkOrderItemCheckView**: Posts `field` (pre_check/post_check) + `value`; returns full `checklist_list.html` partial targeting `#checklist-wrapper`
- **Production Python**: Ubuntu 24.04 uses Python 3.12 (`python3`, not `python`)
- **Production venv**: `/opt/murphys-bench/venv/` — activate before manage.py commands
- **Mileage Calculate CSRF**: Uses `document.querySelector('[name=csrfmiddlewaretoken]')` — do not revert to cookie parsing
- **Google Maps API key**: Stored in SiteSettings (DB), not .env. Restricted to WAN IP in Google Cloud Console. Distance Matrix API must be enabled.
- **Converted tickets**: Included in sidebar, dashboard "My Open Tickets" tile, and cross-visibility panels. Excluded only from resolved/closed filters.

---

## General rules for this project:

- All views use `LoginRequiredMixin`
- HTMX loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models (both dev and prod)
- Commit and push when complete; deploy with git pull + systemctl restart on server
