# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature
3. `docs/batch-11-plan.md` — full spec for the Batch 11 foundational rebuild

---

## What's already built and working (as of session 9):

- Django 4.2 app, 34 models, 18 migrations applied
- Full CRUD views for work orders, clients, devices, mileage, contacts
- HTMX inline notes, checklist toggling, inline ticket replies, Quick Labor logging, device credentials
- **Batch 1–9**: See CLAUDE.md for full list
- **Batch 10 — Complete (session 8)**:
  - Repair Report at `/work-orders/<id>/print/`
  - Company Info fields on SiteSettings
  - Quick Labor (QuickLaborItem + WorkPerformed models, HTMX one-click logging)
  - Device Credentials on WO (masked display, HTMX inline save)
  - Client Type (Residential/Business)
  - Multiple phones per Contact (ContactPhone model, Alpine.js dynamic rows)
  - Contact notes + receives_email fields
  - Native Settings UI at `/settings/` — 6 tabs

---

## What's next: Batch 11 — Foundational Client-Centric Rebuild

**Read `docs/batch-11-plan.md` before writing any code.** The full spec is there.

This is a foundational rebuild. The core problem: Murphy's Bench treats Clients, Contacts,
Devices, and Work Orders as peer objects. The correct model is client-centric — everything
flows through the client.

### Build order (Priority 1 first):

**Priority 1:**
1. Device model: add `os`, `os_version`, `condition_at_intake`, `assigned_contact` FK — migration required. "Save & Create WO" button. Remove Device from top-level nav.
2. WorkOrder: add `contact` FK (nullable) — migration required.
3. Client detail page: hub layout, per-contact "+ WO", inline device add, phone custom label field, inline client type, Set Primary Contact.
4. Client edit: Deactivate + Permanently Delete (type-to-confirm, block if WOs exist).

**Priority 2:**
5. WO detail: unified action toolbar, client/device info cards, Days Open, Completed Date, Invoice Ninja Ref #, Work Performed with description+timestamp, collapsible checklist, credentials add-note.
6. Repair Report / Claim Ticket: OS/version/condition, note timestamps, signature lines, footer, Claim Ticket via `?type=claim`.

**Priority 3:**
7. Settings: Repair Types native UI (new RepairTypeCategory model with sort_order).
8. Settings: Canned Responses (two note streams + categories + WO detail picker).
9. Settings: Quick Labor native CRUD.
10. Settings: Checklist Items — model change to flat bank by device type (migration required).
11. Settings: Status Colors + Site Colors (SiteSettings fields → CSS variables in base.html).
12. Settings: Company Info — address split to Line 1/2/City/State/Zip (migration required). Report Header Preview.
13. Settings: Display Settings (localStorage — font size, card density).

---

## Key decisions locked (do not re-litigate):

- Permanently Delete blocks if client has WOs; message offers Deactivate instead
- Address: 5 fields — Line 1, Line 2 (optional), City, State, Zip. No country. Applies to both SiteSettings (Company Info) and Client model.
- Existing address data migrates to Line 1; user cleans up manually
- Colors: stored in SiteSettings, rendered as CSS variables in `<style>` block in base.html
- RepairTypeCategory: new model needed, with sort_order field
- Assigned Contact queryset: server-side filter via client_id URL param; no HTMX cascade
- WO Contact association: FK on WorkOrder, filtered to client's contacts, pre-filled from device's assigned_contact on "Save & Create WO"

---

## Known gotchas (read before touching these areas):

- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py which pre-processes to `[{entry, changes: [{field, old, new}]}]`.
- **`?assigned_to=me`**: Handled in TicketListView and WorkOrderListView. Admins get all; techs get own.
- **Alpine.js**: CDN with `defer` in base.html. HTMX-swapped content may need `Alpine.initTree(el)` in htmx:afterSwap if it uses Alpine.
- **Queue filter_criteria JSON**: `assigned_to: null` (explicit null) means "unassigned only" in `_apply_queue_filters()`.
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`.
- **`_is_admin` + anonymous users**: Check `request.user.is_authenticated` before calling — AnonymousUser has no `has_perm_flag`.
- **Google Maps mileage**: Works in architecture but fails from localhost. Verify after deploying to internal server.
- **DeviceCreateView ?next=**: Pass `next` in both GET and POST. Used when launching "New Device" from client detail.
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False`.
- **ContactPhone phones in Alpine edit form**: Pre-populated via Django template loop into Alpine `phones` array. Saved as `phone_number[]` / `phone_type[]` POST arrays via `_save_contact_phones()`.

---

## General rules for this session:

- All views use `LoginRequiredMixin`
- HTMX loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models
- Update `TODO.md` to mark completed items ✅ when done
- Commit and push when complete
