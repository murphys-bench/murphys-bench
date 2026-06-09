# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 13):

- Django 4.2 app, 36+ models, 30 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- Deploy workflow: `git push` on Mac → SSH to server → `git pull && python manage.py migrate && sudo systemctl restart murphys-bench`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues
- HTMX inline notes, checklist, ticket replies, Quick Labor, credentials

**Batch 12 — COMPLETE (session 12)**

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

## What's next: choose from TODO.md

Ask the user what they want to tackle. Good candidates:

1. **Native Admin section** — replace Django admin entirely; move Settings, Users, Security, Roles, Email Templates, Suppressed Addresses, SLA Plans, Help Topics, KB Categories, Logs under a Murphy's Bench `/manage/` section; gate by `can_manage_settings` role flag (not `is_staff`)
2. **Frontend dependency strategy** — currently using unpkg/jsdelivr/cdn.tailwindcss.com for HTMX, Alpine.js, Tailwind, Chart.js; discuss vendoring vs npm lock file vs Tailwind CLI; also evaluate replacing Google Distance Matrix API with OpenStreetMap/OSRM to eliminate external data sharing
3. **Cloudflare tunnel** — external access to the production server
4. **Testing suite** — model/view/form tests (deferred until real-world use surfaces edge cases)
5. **Client Portal** — read-only ticket/WO status view for clients
6. **Reporting enhancements** — revenue by period, technician productivity, device type breakdown
7. **Site-wide icon audit** — replace remaining text symbols (×, etc.) with SVG icons
8. Any other items in TODO.md

---

## Key decisions locked (do not re-litigate):

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
