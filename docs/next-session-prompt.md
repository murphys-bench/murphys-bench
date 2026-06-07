# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `todo.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working:

- Django 4.2 app, 29 models, 13 migrations applied
- Full CRUD views for work orders, clients, devices, mileage
- HTMX inline notes on WO detail, checklist toggling, inline ticket replies
- Default checklists for 6 repair types
- **Full ticket views**: list, detail, create/edit, HTMX inline replies, convert-to-work-order
- Django admin customized for all models including SiteSettings singleton
- **Batch 1**: Collision avoidance (TicketLock, HTMX polling), WO/ticket closure dependency, ticket linking (TicketLink)
- **Batch 2**: Audit log (django-auditlog, History tab), file attachments (Attachment model with GenericFK, local/S3 storage, SiteSettings controls)
- **Batch 3**: Outbound email (EmailTemplate, SMTP via SiteSettings), auto-responder on ticket create, three-layer suppression, EmailSendLog
- **Batch 4**: SLA Plans (overdue badges, HTMX acknowledge workflow with required note), Help Topics (ticket classification), Knowledge Base (KBCategory + KBArticle, search/filter, is_restricted gating, KB link in nav), Roles & Permissions (Role model with 16 flags, seeded Administrator + Technician, TechSkill M2M)
- **Batch 4 bonus**: Stopwatch timer on WO detail (Start/Pause/Reset, localStorage persistence across page refresh, HTMX "Log X min" endpoint)
- **Batch 5**: Inbound email — `fetch_inbound_email` management command, IMAP + POP3 (SSL), threading by [TKT-…] in subject → TicketReply, quote stripping, email attachment saving, duplicate guard on Message-ID, InboundEmailLog audit trail, --dry-run flag
- **Batch 6**: Custom Queues (TicketQueue model, system + personal queues, 3 seeded system queues, full CRUD at /queues/), Persistent Sidebar (Alpine.js accordion, HTMX-loaded, admins see all/techs see own), Enhanced Dashboard (DashboardTile model, role-aware tile counts, seeded tiles), Reporting (8 reports at /reports/, Chart.js charts, CSV export, date range filter)
- **Ticket.assigned_to** added (was missing from the model); wired into form, admin, list filtering, and sidebar

---

## Known gotchas from last session (read before touching these areas):

- **Audit log in templates**: Never use `entry.changes_dict.items` in a Django template — the dict can have an `'items'` key that shadows the `.items()` method. Use `_audit_entries(obj)` from views.py which pre-processes entries into `[{entry, changes: [{field, old, new}]}]`.
- **`?assigned_to=me`**: Handled in both TicketListView and WorkOrderListView. Admins get all items (filter skipped); techs get only their own. Don't break this when adding new list filters.
- **Alpine.js**: Loaded via CDN with `defer` in base.html. Required for sidebar accordion. If you add new Alpine components to HTMX-swapped content, they may need `Alpine.initTree(el)` in an htmx:afterSwap handler.
- **Queue filter_criteria JSON**: The `assigned_to: null` key (explicit null, not absent key) means "unassigned only" in `_apply_queue_filters()`. Keep that distinction when extending queue filter support.
- **TicketQueueForm**: The `Meta.fields` list is mutated in `__init__` when `is_admin=True` to add the `owner` field. This is a bit fragile — don't rely on it as a pattern elsewhere.

---

## Your task this session: Build Batch 7

One feature, fully specced in `todo.md`. Read the spec there before starting.

---

### Custom Fields & Forms

**Purpose**: Let admins define extra fields on tickets and work orders without touching code. Captures job-specific data (e.g., "Asset Tag", "Room Number", "Warranty Expiry") scoped to a HelpTopic or RepairType.

**What to build:**
- `CustomField` model: label, field_type (text/textarea/select/checkbox/date), applies_to (ticket/workorder/both), is_required, help_text, sort_order, is_active, scoped_to_help_topic FK (nullable), scoped_to_repair_type FK (nullable)
- `CustomFieldChoice` model: FK to CustomField, label, sort_order — for select-type fields
- `CustomFieldValue` model: GenericForeignKey (Ticket or WorkOrder), field FK, value (TextField) — EAV storage
- Fields that are global (no scope FK set) appear on all tickets/WOs of that type
- Fields scoped to a HelpTopic appear only on tickets with that help topic
- Fields scoped to a RepairType appear only on WOs with that repair type
- Custom fields render below standard fields on ticket and WO create/edit forms
- Custom field values display on ticket and WO detail views (only fields with values, or all required fields)
- Admin: full management of CustomField + CustomFieldChoice in Django admin
- Field types for Phase 1: text, textarea, select, checkbox, date
- No new URL routes required beyond what's in existing create/edit views

---

## General rules for this session:

- All views use `LoginRequiredMixin`
- HTMX is loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js is loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models
- Update `todo.md` to mark completed items ✅ when done
- Commit and push when complete
