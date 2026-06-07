# Murphy's Bench

**Status**: Phase 1 Active Development
**Tech Stack**: Python 3.11 / Django 4.2 / HTMX / Tailwind CSS (CDN)
**Deployment Model**: Self-hosted on internal network (not cloud, not SaaS)
**Repository**: `~/Documents/Claude/murphys-bench` + GitHub (private)
**Last Updated**: June 7, 2026 (end of session 3)

---

## Current App State (What's Working)

The app is running locally at `http://localhost:8000`. All views require login.

**Working URLs:**
- `/` — Dashboard (stats, open work orders, recently closed)
- `/accounts/login/` — Login page
- `/work-orders/` — Work order list (search, filter, pagination)
- `/work-orders/new/` — Create work order (native form)
- `/work-orders/<id>/` — Work order detail (HTMX inline notes, checklist toggling)
- `/work-orders/<id>/edit/` — Edit work order
- `/clients/` — Client list (search, active filter)
- `/clients/new/` — Create client
- `/clients/<id>/` — Client detail (contacts, devices, work history)
- `/clients/<id>/edit/` — Edit client
- `/devices/` — Device list (search, type filter)
- `/devices/new/` — Create device
- `/devices/<id>/` — Device detail (repair history)
- `/devices/<id>/edit/` — Edit device
- `/mileage/` — Mileage log (month filter, running total)
- `/tickets/` — Ticket list (search, status filter, pagination)
- `/tickets/new/` — Create ticket (native form)
- `/tickets/<id>/` — Ticket detail (HTMX inline replies, convert-to-WO)
- `/tickets/<id>/edit/` — Edit ticket
- `/tickets/<id>/convert/` — Convert ticket to work order
- `/tickets/<id>/lock/release/` — Release ticket lock (called via JS beforeunload)
- `/tickets/<id>/lock/status/` — Lock status fragment (HTMX polled every 30s)
- `/tickets/<id>/links/add/` — Link two tickets (HTMX)
- `/tickets/<id>/links/remove/` — Unlink tickets (HTMX)
- `/attachments/<id>/download/` — Secure authenticated file download
- `/admin/` — Django admin (full access, staff only)

**What still requires admin panel:**
- Logging mileage (no native form yet)
- Managing checklists and canned responses
- Email template editing (EmailTemplate model)
- Suppressed address management (SuppressedAddress model)
- Email send log review (EmailSendLog model, read-only)
- Site settings: SMTP config, attachment limits, storage backend

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
│   ├── models.py               # All 14 data models
│   ├── views.py                # All views
│   ├── urls.py                 # Core URL patterns
│   ├── forms.py                # All forms
│   ├── admin.py                # Admin customization
│   └── templates/core/
│       ├── base.html           # Shared layout + nav
│       ├── dashboard.html
│       ├── work_order_list.html
│       ├── work_order_detail.html
│       ├── work_order_form.html
│       ├── client_list.html
│       ├── client_detail.html
│       ├── client_form.html
│       ├── device_list.html
│       ├── device_detail.html
│       ├── device_form.html
│       ├── mileage_list.html
│       ├── ticket_list.html
│       ├── ticket_detail.html
│       ├── ticket_form.html
│       ├── ticket_convert.html
│       └── partials/
│           ├── note_item.html
│           ├── checklist_item.html
│           └── ticket_reply_item.html
├── accounts/                    # Auth app
└── docs/
    ├── database-schema.md
    └── ticketing-design.md
```

### Data Models (22 current)
- **User** — extended Django user with role (transitioning to Role FK — see Planned Features)
- **Client** — company/customer
- **Contact** — person at a client company
- **Device** — equipment being serviced
- **Ticket** — initial service request; statuses: new, open, in_progress, waiting_on_customer, resolved, closed, converted
- **TicketReply** — threaded conversation on a ticket (customer_visible or internal)
- **WorkOrder** — repair job (main entity); linked back to originating ticket via OneToOne
- **WorkOrderNote** — customer-visible or internal notes on a work order
- **WorkOrderItem** — checklist items, parts, time entries
- **Mileage** — travel logging
- **RepairType** — category (Laptop Repair, Desktop Repair, etc.)
- **Checklist** — template task list linked to a repair type
- **ChecklistItem** — individual task in a checklist template
- **CannedResponse** — template notes for common situations
- **TicketLock** — collision avoidance; OneToOne on Ticket, 10-min expiry
- **TicketLink** — links related/duplicate tickets; unique_together on (ticket_a, ticket_b)
- **SiteSettings** — singleton; SMTP config, attachment limits/storage, suppression patterns
- **Attachment** — GenericFK to Ticket/TicketReply/WorkOrder/WorkOrderNote; local or S3 storage
- **EmailTemplate** — trigger-based outbound email templates (4 triggers, seeded with defaults)
- **SuppressedAddress** — exact email addresses that never receive automated email
- **EmailSendLog** — audit trail for every send attempt (sent / suppressed / failed)

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

## Planned Phase 1 Features

All design decisions have been finalized. Build order and full specs in `todo.md`.

### Batch 1 — Collision Avoidance, WO/Ticket Dependency, Ticket Linking
- **Collision Avoidance**: `TicketLock` model, 10-min expiry, non-blocking banner, HTMX polling
- **WO/Ticket Dependency**: Hard block on ticket close while WO open; prompt when WO closes; manual resolution only
- **Ticket Linking**: `TicketLink` model, link types: `related` and `duplicate` only

### Batch 2 — Audit Log, Attachments
- **Audit Log**: `django-auditlog` package, History tab on ticket and WO detail
- **Attachments**: GenericForeignKey to Ticket/TicketReply/WorkOrder/WorkOrderNote; local filesystem + S3-compatible storage (both Phase 1); 25 MB default max; all settings editable in admin

### Batch 3 — Outbound Email, Auto-Responder
- **Outbound Email**: SMTP config, `EmailTemplate` model with trigger-based sending, synchronous
- **Auto-Responder + Filtering**: Three-layer suppression — pattern blocklist + exact address list + per-client suppress flag; suppression is logged not silent

### Batch 4 — SLA, Help Topics/KB, Roles & Permissions
- **SLA Plans**: Grace period, overdue badges on ticket + linked WO, acknowledgment workflow (requires note), in-app only
- **Knowledge Base**: Single unified KB (not split ticket/WO), `KBCategory` + `KBArticle`, article types (troubleshooting/how_to/vendor/internal), `is_restricted` for admin-only articles, accessible from ticket and WO detail
- **Roles & Permissions**: Replace flat role field with `Role` model + permission flags; seed Administrator + Technician; `TechSkill` M2M on User for future skill-based routing

### Batch 5 — Inbound Email
- **Email Piping**: IMAP + POP3 (both selectable in admin), cPanel/standard mail, no OAuth2 needed; threading by ticket number in subject; attachments on inbound emails saved automatically

### Batch 6 — Queues, Sidebar, Dashboard, Reporting
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
