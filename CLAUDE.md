# Murphy's Bench

**Status**: Phase 1 Active Development
**Tech Stack**: Python 3.11 / Django 4.2 / HTMX / Alpine.js / Tailwind CSS (CDN)
**Deployment Model**: Self-hosted on internal network (not cloud, not SaaS)
**Repository**: `~/Documents/Claude/murphys-bench` + GitHub (private)
**Last Updated**: June 7, 2026 (end of session 5)

---

## Current App State (What's Working)

The app is running locally at `http://localhost:8000`. All views require login.

**Working URLs:**
- `/` — Dashboard (stats, open work orders, recently closed)
- `/accounts/login/` — Login page
- `/work-orders/` — Work order list (search, filter, pagination)
- `/work-orders/new/` — Create work order (native form)
- `/work-orders/<id>/` — Work order detail (HTMX inline notes, checklist toggling, stopwatch timer)
- `/work-orders/<id>/edit/` — Edit work order
- `/work-orders/<id>/add-time/` — HTMX: add minutes to time_spent (stopwatch log)
- `/clients/` — Client list (search, active filter)
- `/clients/new/` — Create client
- `/clients/<id>/` — Client detail (contacts, devices, work history)
- `/clients/<id>/edit/` — Edit client
- `/devices/` — Device list (search, type filter)
- `/devices/new/` — Create device
- `/devices/<id>/` — Device detail (repair history)
- `/devices/<id>/edit/` — Edit device
- `/mileage/` — Mileage log (month filter, running total)
- `/tickets/` — Ticket list (search, status filter, overdue indicator)
- `/tickets/new/` — Create ticket (with help topic + SLA plan selectors)
- `/tickets/<id>/` — Ticket detail (HTMX inline replies, convert-to-WO, overdue badge + ack)
- `/tickets/<id>/edit/` — Edit ticket
- `/tickets/<id>/convert/` — Convert ticket to work order
- `/tickets/<id>/lock/release/` — Release ticket lock (called via JS beforeunload)
- `/tickets/<id>/lock/status/` — Lock status fragment (HTMX polled every 30s)
- `/tickets/<id>/links/add/` — Link two tickets (HTMX)
- `/tickets/<id>/links/remove/` — Unlink tickets (HTMX)
- `/tickets/<id>/acknowledge-overdue/` — Acknowledge overdue with required note (HTMX)
- `/attachments/<id>/download/` — Secure authenticated file download
- `/queues/` — Ticket queue list (system + personal queues)
- `/queues/<id>/` — Queue detail (filtered ticket list)
- `/queues/new/` — Create queue
- `/queues/<id>/edit/` — Edit queue
- `/reports/` — Reporting & analytics (8 reports, Chart.js, CSV export per report)
- `/sidebar/` — HTMX fragment: my tickets + my work orders for sidebar
- `/kb/` — Knowledge base list (search, category + type filters)
- `/kb/new/` — Create KB article (staff/can_manage_kb only)
- `/kb/<id>/` — KB article detail
- `/kb/<id>/edit/` — Edit KB article
- `/admin/` — Django admin (full access, staff only)

**What still requires admin panel:**
- Logging mileage (no native form yet)
- Managing checklists and canned responses
- Email template editing (EmailTemplate model)
- Suppressed address management (SuppressedAddress model)
- Email send/receive log review (EmailSendLog, InboundEmailLog — read-only)
- Site settings: SMTP, inbound email, attachment limits, storage backend
- SLA Plans, Help Topics, KB Categories (admin-managed)
- Roles and TechSkills management
- Admin UI needs a descriptive polish pass (deferred — noted in .claude/notes/admin_cleanup.md)

---

## Vision & Philosophy

Murphy's Bench is **internal-first, self-hosted software** for small field service businesses (MSPs).

### Core Principle
Build one thing well: a self-hosted repair tracking system that runs on a business's internal network. Other companies can self-host it on their infrastructure.

### Workflow
```
Ticket (intake + replies) → Triage → Work Order (repair) → Notes/Checklist → Closed → Invoice Ninja
```

### Phase 1: SCS Internal (Current)
- **Focus**: Get SCS's workflow working perfectly
- **Scope**: Ticketing, work orders, device tracking, mileage, email integration, reporting
- **Deployment**: Internal network
- **Success**: SCS techs prefer this to the legacy PHP app

### Phase 2: Integrations & Polish (Future)
- Invoice Ninja API bridge
- Email OAuth2 (Gmail/Office 365)
- Departments, Teams, Auto-routing
- Customer self-service portal
- REST API (for Taskbar Utility App / Clover integration)
- Visual design polish

### Phase 3+: Multi-Tenancy (Speculative)

---

## Architecture

### Tech Stack
- **Backend**: Python 3.11 / Django 4.2.30
- **Frontend**: Tailwind CSS (CDN), HTMX
- **Database**: SQLite (dev), PostgreSQL (planned for production)
- **Auth**: Django session auth, LoginRequiredMixin on all views

### Project Structure
```
murphys-bench/
├── CLAUDE.md                    # This file — read first each session
├── todo.md                      # Full roadmap and build order
├── manage.py
├── requirements.txt
├── murphys_bench/              # Django project settings
│   ├── settings.py
│   └── urls.py
├── core/                        # Main app
│   ├── models.py               # All 27 data models
│   ├── views.py                # All views
│   ├── urls.py                 # Core URL patterns
│   ├── forms.py                # All forms
│   ├── admin.py                # Admin customization
│   ├── email_utils.py          # Outbound email helpers
│   ├── management/commands/
│   │   ├── check_sla_overdue.py    # Cron: flag overdue tickets
│   │   └── fetch_inbound_email.py  # Cron: poll IMAP/POP3 mailbox
│   └── templates/core/
│       ├── base.html           # Shared layout + nav (includes KB link)
│       ├── dashboard.html
│       ├── work_order_list.html
│       ├── work_order_detail.html  # Includes stopwatch timer
│       ├── work_order_form.html
│       ├── client_list.html
│       ├── client_detail.html
│       ├── client_form.html
│       ├── device_list.html
│       ├── device_detail.html
│       ├── device_form.html
│       ├── mileage_list.html
│       ├── ticket_list.html    # Overdue indicators
│       ├── ticket_detail.html  # Overdue badge + ack, SLA/HelpTopic display
│       ├── ticket_form.html    # Includes help_topic + sla_plan + assigned_to fields
│       ├── ticket_convert.html
│       ├── kb_list.html
│       ├── kb_detail.html
│       ├── kb_form.html
│       ├── queue_list.html
│       ├── queue_detail.html
│       ├── queue_form.html
│       ├── reports.html        # 8 reports, Chart.js, date range filter, CSV links
│       └── partials/
│           ├── note_item.html
│           ├── checklist_item.html
│           ├── ticket_reply_item.html
│           ├── ticket_lock_banner.html
│           ├── ticket_linked_list.html
│           ├── attachment_list.html
│           ├── overdue_badge.html
│           ├── overdue_ack_form.html
│           ├── wo_time_spent.html
│           └── sidebar_content.html  # HTMX sidebar fragment (tickets + WOs)
├── accounts/                    # Auth app
└── docs/
    ├── database-schema.md
    ├── ticketing-design.md
    └── next-session-prompt.md
```

### Data Models (29 current)
- **Role** — permission role with 16 boolean flags; seeded: Administrator, Technician
- **TechSkill** — skill tags M2M on User; captured for future skill-based routing
- **User** — extended Django user; role CharField (legacy) + role_obj FK to Role + skills M2M
- **Client** — company/customer
- **Contact** — person at a client company
- **Device** — equipment being serviced
- **SLAPlan** — response deadline config (grace_period_hours, overdue alerts toggle)
- **HelpTopic** — ticket classification with optional default SLA
- **Ticket** — initial service request; statuses: new, open, in_progress, waiting_on_customer, resolved, closed, converted; has sla_plan FK, help_topic FK, assigned_to FK, due_at, overdue ack fields
- **TicketReply** — threaded conversation on a ticket (customer_visible or internal)
- **WorkOrder** — repair job (main entity); linked back to originating ticket via OneToOne; time_spent_minutes + time_spent_display property
- **WorkOrderNote** — customer-visible or internal notes on a work order
- **WorkOrderItem** — checklist items, parts, time entries
- **Mileage** — travel logging
- **RepairType** — category (Laptop Repair, Desktop Repair, etc.)
- **Checklist** — template task list linked to a repair type
- **ChecklistItem** — individual task in a checklist template
- **CannedResponse** — template notes for common situations
- **TicketLock** — collision avoidance; OneToOne on Ticket, 10-min expiry
- **TicketLink** — links related/duplicate tickets; unique_together on (ticket_a, ticket_b)
- **SiteSettings** — singleton; outbound SMTP, inbound IMAP/POP3, attachment limits/storage, suppression patterns
- **Attachment** — GenericFK to Ticket/TicketReply/WorkOrder/WorkOrderNote; local or S3 storage
- **EmailTemplate** — trigger-based outbound email templates (4 triggers, seeded with defaults)
- **SuppressedAddress** — exact email addresses that never receive automated email
- **EmailSendLog** — audit trail for every outbound send attempt (sent / suppressed / failed)
- **InboundEmailLog** — audit trail for every inbound message fetched (new_ticket / reply / duplicate / error)
- **KBCategory** — knowledge base category (admin-managed)
- **KBArticle** — KB article; types: troubleshooting / how_to / vendor / internal; is_restricted flag
- **TicketQueue** — Saved ticket filters; owner=null = system queue (all users); personal queues scoped to owner; filter_criteria JSONField; 3 seeded system queues
- **DashboardTile** — Configurable dashboard tile; row (ticket/workorder), status_filter (JSON list), visible_to (all/admin/tech); seeded defaults for both rows

---

## Ticketing System Design

See `docs/ticketing-design.md` for full detail.

### Ticket Statuses
`new` → `open` → `in_progress` → `waiting_on_customer` → `resolved` → `closed`
Also: `converted` (converted to Work Order — read-only after this point)

### Ticket → Work Order Rules
- A ticket linked to an open WO **cannot** be closed/resolved — hard block
- When the WO closes, ticket shows a prompt: "WO complete — ready to resolve" — tech closes manually
- `AUTO_RESOLVE_TICKET_ON_WO_CLOSE` admin setting (default **off**) for shops that prefer automatic behavior
- Ticket remains in system after conversion — full history retained

---

## Phase 1 Feature Status

Full specs in `todo.md`. Design decisions finalized.

### ✅ Batch 1 — Collision Avoidance, WO/Ticket Dependency, Ticket Linking
### ✅ Batch 2 — Audit Log, Attachments
### ✅ Batch 3 — Outbound Email, Auto-Responder
### ✅ Batch 4 — SLA Plans, Help Topics/KB, Roles & Permissions
- Bonus: Stopwatch timer on WO detail (localStorage, HTMX log-time)
### ✅ Batch 5 — Inbound Email (IMAP/POP3, threading, quote strip, attachments)
### ✅ Batch 6 — Custom Queues, Persistent Sidebar, Enhanced Dashboard, Reporting

### Batch 7 — Custom Fields ← NEXT
- **Custom Queues**: System queues (admin-created) + personal queues (per-user); left sidebar on ticket list/detail
- **Persistent Sidebar**: Visible on all pages except dashboard; accordion with My Tickets / My Work Orders sections; color-coded by status; tech sees own assignments only
- **Enhanced Dashboard**: Two tile rows (Tickets + WOs); tech sees own work, admin sees everything; fully configurable tiles in admin (`DashboardTile` model)
- **Reporting**: 8 reports including ticket-to-WO conversion rate; Chart.js charts; CSV export; configurable in admin

### Batch 7 — Custom Fields
- **Custom Fields**: Available on both Tickets and Work Orders; field types: text/textarea/select/checkbox/date; EAV storage; scoped to HelpTopic or RepairType

### Batch 8 — MFA
- **MFA**: TOTP via `django-two-factor-auth`; available to all users; enforcement toggle in admin (default off); backup codes for admin only; admin resets for lost devices

---

## Key Decisions Made

- **Tailwind via CDN** — no build step needed for now
- **LoginRequiredMixin on all views** — app is internal-only
- **Work order numbers** auto-generated as `WO-YYYYMMDD-NNNN`
- **Ticket numbers** auto-generated as `TKT-YYYYMMDD-NNNN`
- **SQLite for dev** — switch to PostgreSQL for production
- **Visual polish deferred** — functionality first
- **GitHub**: Private repo, push after each working feature
- **HTMX** for inline interactions (notes, replies, checklist toggling) — already implemented
- **No Celery/async queue** — synchronous email sending is sufficient at MSP scale; reassess in Phase 2
- **No OAuth2** for email — SCS uses cPanel-hosted mail with standard IMAP/POP3 credentials
- **Single unified KB** — not split between tickets and work orders
- **Ticket close is always manual** even when linked WO closes — forces human contact
- **MFA backup codes for admin only** — other users recover via admin reset
- **SLA overdue alerts are in-app only** — acknowledgment with required note creates audit trail
- **Attachment storage Phase 1**: local filesystem (configurable path) + S3-compatible (covers B2, MinIO, Wasabi, AWS)
- **Alpine.js** added in session 5 for sidebar accordion state (localStorage persistence); loaded via CDN in base.html with `defer`
- **Sidebar**: HTMX-loaded on every page except dashboard; admins see all open tickets/WOs, techs see own assignments (assigned_to or created_by for tickets)
- **`?assigned_to=me` filter**: works on both `/tickets/` and `/work-orders/`; admins see all (no filter applied), techs see only their own
- **Audit log gotcha**: `changes_dict` from django-auditlog can contain an `'items'` key (WorkOrderItem relation), which in Django templates shadows `dict.items()` via dictionary lookup priority. Audit log entries are pre-processed in the view via `_audit_entries()` to a list of `{entry, changes}` dicts — never iterate `changes_dict.items` in templates.
- **Dashboard tiles**: `DashboardTile.link_url` uses relative paths with `?assigned_to=me`; admin users see all items at those URLs (filter is a no-op for admins)
- **Queue filter_criteria**: JSON dict with optional keys: `status` (list), `assigned_to` (int or null), `overdue` (bool), `client` (int), `help_topic` (int), `sla_plan` (int). The `assigned_to: null` key (explicit null, not absent) means "unassigned only".

---

## Development Setup

```bash
cd ~/Documents/Claude/murphys-bench
source venv/bin/activate
python manage.py runserver
# http://localhost:8000 — login: admin / password123 (local dev only)
```

---

## Related Projects

- **scs-repair-tracker** (`~/Documents/Claude/scs-repair-tracker`) — Legacy PHP app, reference only
- **Clover** (`~/Documents/Clover`) — macOS desktop app, future integration Phase 2+
- **Invoice Ninja** — Financial backend; API research required before Phase 2 integration
