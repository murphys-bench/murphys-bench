# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `todo.md` — complete build roadmap with specs for every planned feature
3. `docs/ticketing-design.md` — ticketing system design and workflow rules

---

## What's already built and working:

- Django 4.2 app, 20+ models, 7 migrations applied
- Full CRUD views for work orders, clients, devices, mileage
- HTMX inline notes on work order detail (with file attachments)
- HTMX checklist item toggling
- Default checklists for 6 repair types
- **Full ticket views**: list, detail, create/edit, HTMX inline replies, convert-to-work-order
- Django admin customized for all models including SiteSettings singleton
- **Batch 1**: Collision avoidance (TicketLock, HTMX polling, lock banner), WO/ticket closure dependency (hard block + warnings), ticket linking (TicketLink, HTMX link/unlink sidebar)
- **Batch 2**: Audit log (django-auditlog, History tab on ticket + WO detail), file attachments (Attachment model with GenericFK, GenericRelation on all target models, file upload on create forms and HTMX forms, secure download view, SiteSettings controls max size / blocked extensions / storage backend)
- **Batch 3**: Outbound email (EmailTemplate model, 4 triggers, SMTP config in SiteSettings admin panel), auto-responder on ticket create, three-layer suppression (client flag / pattern blocklist / exact address list), EmailSendLog audit trail

---

## What was decided across all sessions:

All design decisions are finalized and documented. See `CLAUDE.md` (Planned Phase 1 Features) and `todo.md` (full specs in build order) for complete details.

---

## Your task this session: Build Batch 4

Three features, all fully specced in `todo.md`. Read the specs there before starting — they are the authoritative source.

---

### 1. SLA Plans

**Purpose**: Track response/resolution deadlines on tickets and surface overdue alerts in-app.

**What to build:**
- `SLAPlan` model: name, grace_period_hours, is_active, is_transient, disable_overdue_alerts
- `Ticket.sla_plan` FK (optional), `Ticket.due_at` DateTimeField (calculated on assign)
- `Ticket.is_overdue` property: `due_at < now` and status not in closed/resolved/converted
- Overdue badge on ticket list row and ticket detail header
- If ticket has a linked WO: overdue badge on WO detail as well
- Management command `check_sla_overdue` (run via system cron every 15 min — just build the command, not the cron)
- **Overdue acknowledgment workflow**:
  - "Acknowledge Overdue" button on ticket detail (and linked WO detail)
  - Acknowledging from either side satisfies both
  - Requires an internal note (stored as TicketReply, reply_type='internal')
  - Badge changes to "Overdue — Acknowledged [date] by [name]"
  - New overdue period = new acknowledgment required
- In-app only — no email alerts for SLA
- SLA assigned manually on ticket edit form

---

### 2. Help Topics & Internal Knowledge Base

**Purpose**: Classify tickets by topic and give techs a searchable internal KB.

**What to build:**
- `HelpTopic` model: name, description, default_sla FK, is_active, sort_order
- `Ticket.help_topic` FK (optional) — classification only
- Help topic selector on ticket create/edit form
- **Single unified KB** — one knowledge base for tickets and work orders
- `KBCategory` model: name, description, sort_order
- `KBArticle` model: title, content (TextField), category FK, article_type (troubleshooting/how_to/vendor/internal), author FK, is_active, is_restricted, created_at, updated_at
- `is_restricted` — admin-only articles (visible only to staff/admin role)
- KB list view: search + category filter + article type filter
- KB article detail view
- KB link in nav, and "Search KB" link on ticket detail and WO detail sidebars

---

### 3. Roles & Permissions

**Purpose**: Replace flat role CharField with a proper permission system.

**What to build:**
- `Role` model: name, description, is_system (protects from deletion), and permission flags:
  - `can_manage_settings` (admin panel / SiteSettings)
  - `can_view_all_tickets` (vs. own only)
  - `can_close_tickets`
  - `can_manage_users`
  - `can_view_reports` (placeholder for Batch 6)
  - `can_view_restricted_kb`
- Seed two system roles: `Administrator` (all permissions) and `Technician` (standard access)
- `User.role_obj` FK to Role (nullable for migration safety); keep old `role` CharField temporarily
- `TechSkill` model: user M2M (via `User.skills`), skill name — for future skill-based routing
- Permission checks: `is_restricted` KB articles gated on `can_view_restricted_kb`; `can_manage_settings` gates SiteSettings in admin (or just use is_staff for now)
- Migration: seed Administrator + Technician roles

---

## General rules for this session:

- All views use `LoginRequiredMixin`
- HTMX is loaded in `base.html` with global CSRF header on `<body>`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models
- Update `todo.md` to mark completed items ✅ when done
- Commit and push when complete
