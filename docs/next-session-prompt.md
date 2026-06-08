# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature
3. `docs/batch-11-plan.md` — full spec for the Batch 11 foundational rebuild

---

## What's already built and working (as of session 10):

- Django 4.2 app, 36 models, 23 migrations applied
- Full CRUD views for work orders, clients, devices, mileage, contacts
- HTMX inline notes, checklist toggling, inline ticket replies, Quick Labor logging, device credentials
- **Batch 1–10**: See CLAUDE.md for full list
- **Batch 11 — Priority 1 + 2 complete (session 10)**:
  - Device: `os`, `os_version`, `condition_at_intake`, `assigned_contact` FK; "Save & Create WO →" button; removed from nav
  - WorkOrder: `contact` FK (nullable); pre-filled from device's assigned_contact
  - Client detail: hub layout — Account Info → Contacts → Devices → WO History; per-contact +WO/Set Primary; phone label field; Set Primary Contact
  - Client edit: Deactivate (Status section) + Permanently Delete (type-to-confirm Danger Zone, blocked if WOs exist)
  - WO detail: black unified toolbar; client/device info cards; Days Open; Invoice Ninja Ref #; credential notes; Work Performed with description+timestamp; collapsible checklist
  - Repair Report/Claim Ticket: OS/version/condition; note timestamps; signature lines; footer; `?type=claim` title switch
  - **Batch 11 Priority 3 Step 7**: Settings › Repair Types native CRUD (RepairTypeCategory model, collapsible categories, ▲/▼ reorder, inline edit/delete per type)

---

## What's next: Batch 11 Priority 3 (remaining)

Continue step by step through the remaining Settings UI items:

**Step 8 — Settings: Canned Responses**
- Two Note Streams: "Customer Notes" and "Tech Notes (Internal)"
- Each stream has user-defined, reorderable Categories
- Per-response: stream, category, label, body text
- CRUD: add/edit/delete per response; add/reorder categories per stream
- Canned response picker on WO detail note forms (insert text into the note textarea)
- New models required: `CannedResponseStream`, `CannedResponseCategory`, `CannedResponse`

**Step 9 — Settings: Quick Labor native CRUD**
- QuickLaborItem model already exists (Batch 10); currently admin-only
- Native UI grouped by category (Software/Hardware/Data/Maintenance/General)
- Per item: Button Label, Category, Print Description, Active toggle
- Add/edit/delete — no new models needed

**Step 10 — Settings: Checklist Items — model change**
- Currently: ChecklistItem tied to RepairType via Checklist template
- Required: flat item bank scoped by device type (not per-repair-type)
- New `ChecklistItem` fields: `name`, `device_types` (store as comma-sep or JSON), `is_active`
- Migration + data migration (migrate existing items to flat bank, assign device types from legacy list)
- WO checklist: filter items by WO's device type instead of repair type
- Native UI: flat list, per-item device type tags, add/retire

**Step 11 — Settings: Status Colors + Site Colors**
- New fields on SiteSettings for per-status colors (bg/text/border hex) + site palette
- Rendered as CSS variables in a `<style>` block in base.html
- Settings UI: hex inputs + live preview badges

**Step 12 — Settings: Company Info address split**
- Split `company_address` → `address_line_1` + `address_line_2` on SiteSettings (migration)
- Also split `address_street` → `address_line_1` + `address_line_2` on Client model (migration)
- Migrate existing data to Line 1; user cleans up manually
- Report Header Preview in Settings › Company

**Step 13 — Settings: Display Settings**
- Browser-local only (localStorage, no server round-trip)
- Nav font size, sidebar font size + width, content font size, card density
- Applied on page load via `<script>` in `<head>` to avoid flash

---

## Key decisions locked (do not re-litigate):

- Permanently Delete blocks if client has WOs; message offers Deactivate instead
- Address: 5 fields — Line 1, Line 2 (optional), City, State, Zip. No country.
- Existing address data migrates to Line 1; user cleans up manually
- Colors: stored in SiteSettings, rendered as CSS variables in `<style>` block in base.html
- RepairTypeCategory: created ✅
- Assigned Contact queryset: server-side filter via client_id URL param

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
- **ContactPhone phones in Alpine edit form**: Pre-populated via Django template loop into Alpine `phones` array. Saved as `phone_number[]` / `phone_type[]` / `phone_label[]` POST arrays via `_save_contact_phones()`.
- **Settings tabs**: `repair_types` tab is a special case — no SiteSettings form. Uses `_repair_types_context()` helper. `SETTINGS_TABS` list has `None` for the FormClass on special tabs; `SETTINGS_NAV_TABS` is used for rendering the sidebar nav.
- **WorkOrderForm contact queryset**: filtered by `client_id` kwarg passed from `get_form_kwargs()`. If client comes via `?device=`, the view resolves client_id from the device.

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
