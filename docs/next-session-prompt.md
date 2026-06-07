# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `todo.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working:

- Django 4.2 app, 27 models, 10 migrations applied
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

---

## Your task this session: Build Batch 6

Four features, all fully specced in `todo.md`. Read the specs there before starting — they are the authoritative source. This is the largest batch yet — take them in order and complete each before starting the next.

---

### 1. Custom Queues

**Purpose**: Give techs and admins saved, filterable ticket views beyond the basic list.

**What to build:**
- `TicketQueue` model: name, owner (FK to User, null = system queue), filter_criteria (JSONField), sort_field, sort_direction, is_active
- System queues (owner=null, visible to all) and personal queues (owner=user, visible to that user only)
- Queue filter criteria supports: status, assigned_to, help_topic, sla_plan, overdue (bool), client
- Queue list view: `/queues/` — shows system queues + user's personal queues
- Queue detail view: `/queues/<id>/` — ticket list filtered by queue criteria, with queue name as heading
- Create/edit/delete queue UI (personal queues only for techs; system queues admin-only)
- Seed 3 default system queues: "All Open", "Unassigned", "Overdue"

---

### 2. Persistent Sidebar

**Purpose**: Always-visible quick access to the current tech's open work on every page.

**What to build:**
- Visible on all pages except dashboard — added to `base.html` layout
- Two independently collapsible accordion sections: **My Tickets** and **My Work Orders**
- Each section header shows item count: "My Tickets (5)"
- Accordion state remembered in localStorage (survives page navigation)
- Each item: number, client name, truncated subject, color-coded status dot matching badge colors used elsewhere
- Tech sees only their own assignments; admin sees their own assignments (same as tech)
- Items link directly to the detail page
- Sidebar is narrow (fixed width, left or right); main content area adjusts

---

### 3. Enhanced Dashboard

**Purpose**: Replace the basic dashboard with role-aware, configurable tiles.

**What to build:**
- `DashboardTile` model: row (ticket/workorder), label, status_filter (JSONField — list of statuses), link_url, sort_order, is_active, visible_to (all/admin/tech)
- Two tile rows: Tickets (top) and Work Orders (below)
- **Tech view**: tiles show counts for own work — "My Open Tickets", "In Progress", "Waiting on Customer"
- **Admin view**: tiles show counts for all work — "Total Open", "Unassigned", "In Progress"
- Each tile links to a filtered ticket/WO list showing exactly those items
- Tiles configurable in admin (show/hide, label, filter, order)
- Seed sensible default tiles for both rows
- Replace the current static dashboard with this new one

---

### 4. Reporting & Analytics

**Purpose**: Give admins visibility into volume, workload, and SLA performance.

**What to build:**
- `/reports/` — dedicated section, date range filter (default: last 30 days), accessible via nav
- 8 reports (each a separate section on the page or tab):
  1. Ticket volume over time — bar chart by day/week
  2. Open tickets by status — donut chart
  3. Tickets by client — table + bar chart
  4. Tickets by technician (workload) — table
  5. Average ticket resolution time — by tech and by client
  6. SLA compliance rate — % of tickets closed before due_at
  7. Ticket → WO conversion rate — % of tickets converted, over time
  8. Mileage by tech and month — table
- Chart.js via CDN for all charts
- CSV export for every report (download button per report)
- No new models — all from existing data via ORM aggregations

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
