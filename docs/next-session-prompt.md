# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 11):

- Django 4.2 app, 36+ models, 27 migrations applied
- Full CRUD views for work orders, clients, devices, mileage, contacts
- HTMX inline notes, checklist toggling, inline ticket replies, Quick Labor logging, device credentials
- **Batch 11 — COMPLETE (sessions 10–11)**:
  - Device: `os`, `os_version`, `condition_at_intake`, `assigned_contact` FK; "Save & Create WO →"
  - WorkOrder: `contact` FK; Invoice Ninja Ref #; credential_notes
  - Client detail: hub layout with Contacts / Devices / WO History
  - Client edit: Deactivate + Permanently Delete (type-to-confirm, blocked if WOs exist)
  - WO detail: black toolbar; client/device cards; Days Open; Work Performed with timestamps; collapsible checklist; canned response picker
  - Repair Report / Claim Ticket: OS/condition; note timestamps; signature lines; `?type=claim` switch
  - Settings › Repair Types: RepairTypeCategory model, collapsible categories, ▲/▼ reorder
  - Settings › Canned Responses: two streams (Customer/Internal), categories, CRUD, WO detail picker
  - Settings › Quick Labor: native CRUD grouped by category, inline edit, active toggle
  - Settings › Checklist Items: flat bank by device type (migration 0025), inline add/edit, device type checkboxes
  - Settings › Colors: per-status hex colors + site palette (nav bg, accent); CSS variables in base.html; status badges on list/detail/dashboard
  - Settings › Company: address_line1 + address_line2 split (migration 0027); Report Header Preview
  - Settings › Display: localStorage font size (content + nav) + table density; applied via inline script before first paint

---

## What's next: choose from TODO.md

Batch 11 is fully complete. Candidates for Batch 12 based on TODO.md priority order:

1. **Testing suite** — model tests, view tests, form validation
2. **Client Portal** — read-only ticket/WO status view for clients (separate login)
3. **Ticket ↔ WO linking improvements** — auto-close ticket when WO closes; show linked WO on ticket detail
4. **Reporting enhancements** — revenue by period, technician productivity, device type breakdown
5. **Email templates UI** — native CRUD for EmailTemplate (trigger-based outbound emails)
6. Any other items in TODO.md

Ask the user what they want to tackle next.

---

## Key decisions locked (do not re-litigate):

- Permanently Delete blocks if client has WOs; message offers Deactivate instead
- Address: 5 fields — Line 1, Line 2 (optional), City, State, Zip. No country.
- Colors: stored in SiteSettings, rendered as CSS variables in `<style>` block in base.html
- ChecklistItem is a flat bank by device type — no repair-type template chain
- Display settings: localStorage only, no DB, applied as data attributes on `<html>` before paint
- CannedResponse: stream (customer/internal) + optional category FK

---

## Known gotchas (read before touching these areas):

- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py which pre-processes to `[{entry, changes: [{field, old, new}]}]`.
- **`?assigned_to=me`**: Handled in TicketListView and WorkOrderListView. Admins get all; techs get own.
- **Alpine.js**: CDN with `defer` in base.html. HTMX-swapped content may need `Alpine.initTree(el)` in htmx:afterSwap if it uses Alpine.
- **Queue filter_criteria JSON**: `assigned_to: null` (explicit null) means "unassigned only" in `_apply_queue_filters()`.
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`.
- **`_is_admin` + anonymous users**: Check `request.user.is_authenticated` before calling.
- **Google Maps mileage**: Works in architecture but fails from localhost. Verify after deploying.
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False`.
- **ContactPhone phones in Alpine edit form**: Pre-populated via Django template loop into Alpine `phones` array. Saved as `phone_number[]` / `phone_type[]` / `phone_label[]` POST arrays via `_save_contact_phones()`.
- **Settings tabs**: Special tabs (repair_types, canned_responses, quick_labor, checklist_items, colors, display) use `None` FormClass and inject context via `_*_context()` helpers. `SETTINGS_NAV_TABS` drives the sidebar.
- **WorkOrderForm contact queryset**: filtered by `client_id` kwarg. If client comes via `?device=`, resolved from device.
- **Checklist apply**: `_apply_checklist_items(work_order)` pulls from flat `ChecklistItem` bank filtered by device type. Uses `get_or_create` to avoid duplicates.
- **Status badges**: Use `wo-status-badge status-{key}` classes — driven by CSS variables from SiteSettings, not hardcoded Tailwind.
- **site_settings context processor**: `core.context_processors.site_settings` injects `site_settings` into every template. Registered in `TEMPLATES` in settings.py.
- **Display settings**: Inline script in `<head>` of base.html reads localStorage keys `mb_font_size`, `mb_nav_size`, `mb_density` and sets data attributes on `<html>`. CSS rules in base.html respond to those attributes.

---

## General rules for this project:

- All views use `LoginRequiredMixin`
- HTMX loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models
- Update `TODO.md` to mark completed items ✅ when done
- Commit and push when complete
