# Murphy's Bench

**Status**: Phase 1 Active Development  
**Tech Stack**: Python 3.11 / Django 4.2 / HTMX / Tailwind CSS (CDN)  
**Deployment Model**: Self-hosted on internal network (not cloud, not SaaS)  
**Repository**: `~/Documents/Claude/murphys-bench` + GitHub (private)  
**Last Updated**: June 4, 2026

---

## ⚠️ Ticketing System Requirements (Locked In)

Murphy's Bench includes a **full-featured ticketing system** — not just work order tracking. Key requirements:

1. **Ticket Ingestion** — Accept initial service requests (via email, web form, or manual entry)
2. **Threaded Conversation** — Track ticket + all replies/updates (who said what, when)
3. **Ticket-to-Work Order Conversion** — Convert selected tickets to Work Orders; retain historical data
4. **Trend Analysis** — Store historical ticket data by client and device to identify patterns
5. **Scope**: Ticketing + work order management. **No CRM, no financial functions** beyond Invoice Ninja integration (Phase 2)

See [Ticketing System Design](#ticketing-system-design) below for model details.

---

## What This Is

Murphy's Bench is a work order and service management platform for field service businesses — particularly small MSPs. It handles the workflow between receiving a repair request and completing the job: managing clients, tracking devices, assigning technicians, logging work, and maintaining audit trails.

**Named for Murphy's Law**: A tool built to *prevent* things from going wrong.

---

## Current App State (What's Working)

The app is running locally at `http://localhost:8000`. All views require login.

**Working URLs:**
- `/` — Dashboard (stats, open work orders, recently closed)
- `/accounts/login/` — Login page
- `/work-orders/` — Work order list (search, filter, pagination)
- `/work-orders/new/` — Create work order (native form)
- `/work-orders/<id>/` — Work order detail
- `/work-orders/<id>/edit/` — Edit work order (native form)
- `/clients/` — Client list (search, active filter)
- `/clients/new/` — Create client (native form)
- `/clients/<id>/` — Client detail (contacts, devices, work history)
- `/clients/<id>/edit/` — Edit client (native form)
- `/devices/` — Device list (search, type filter)
- `/devices/new/` — Create device (native form)
- `/devices/<id>/` — Device detail (repair history)
- `/devices/<id>/edit/` — Edit device (native form)
- `/mileage/` — Mileage log (month filter, running total)
- `/admin/` — Django admin (full access, staff only)

**What still requires admin panel:**
- Logging mileage (no native form yet)
- Managing tickets (no native views built yet — next build)
- Managing checklists and canned responses (editable via /admin/)

---

## Vision & Philosophy

Murphy's Bench is **internal-first, self-hosted software** for small field service businesses.

### Core Principle
Build one thing well: a self-hosted repair tracking system that runs on a business's internal network. Other companies can self-host it on their infrastructure. No SaaS hosting or multi-tenancy unless/until there's explicit demand.

### Phase 1: SCS Internal (Current)
Build a single-company, optimized web application for Shamrock Computer Services (SCS).

- **Focus**: Get SCS's workflow working perfectly
- **Scope**: Ticketing, work orders, device tracking, mileage logging, admin controls
- **Deployment**: Internal network (10.58.58.235 or equivalent)
- **Communication**: Email-based (inbound tickets, outbound updates, ongoing communication)
- **Success**: SCS techs prefer this to the legacy PHP app

### Phase 2: Integrations & Polish (Future)
- Invoice Ninja API bridge
- Email parsing improvements
- Optional integrations (Slack, etc.)
- Visual design polish
- NOT starting until Phase 1 is complete

### Phase 3+: Multi-Tenancy (Speculative)
Only if explicit demand emerges. Years away, if ever.

---

## Architecture

### Tech Stack
- **Backend**: Python 3.11 / Django 4.2.30
- **Frontend**: Tailwind CSS (CDN), HTMX (not yet implemented)
- **Database**: SQLite (dev), PostgreSQL (planned for production)
- **Auth**: Django session auth, LoginRequiredMixin on all views

### Project Structure
```
murphys-bench/
├── CLAUDE.md                    # This file — read first each session
├── TODO.md                      # Full roadmap and build order
├── manage.py
├── requirements.txt
├── murphys_bench/              # Django project settings
│   ├── settings.py
│   └── urls.py                 # Root URL routing
├── core/                        # Main app
│   ├── models.py               # All 13 data models
│   ├── views.py                # All views (list, detail, create, edit)
│   ├── urls.py                 # Core URL patterns
│   ├── forms.py                # WorkOrderForm, ClientForm, DeviceForm
│   ├── admin.py                # Admin customization for all models
│   └── templates/core/         # All HTML templates
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
│       └── mileage_list.html
├── accounts/                    # Auth app
│   ├── views.py                # LoginView, logout_view
│   ├── urls.py
│   └── templates/accounts/
│       └── login.html
└── docs/
    └── database-schema.md
```

### Data Models (14 total)
- **User** — extended Django user with role (admin/technician/viewer)
- **Client** — company/customer
- **Contact** — person at a client company
- **Device** — equipment being serviced
- **Ticket** — initial service request (intake); statuses: new, open, in_progress, waiting_on_customer, resolved, closed, converted
- **TicketReply** — threaded conversation on a ticket (customer_visible or internal)
- **WorkOrder** — repair job (main entity); linked back to originating ticket
- **WorkOrderNote** — customer-visible or internal notes on a work order
- **WorkOrderItem** — checklist items, parts, time entries
- **Mileage** — travel logging
- **RepairType** — category (Laptop Repair, Desktop Repair, etc.)
- **Checklist** — template task list linked to a repair type
- **ChecklistItem** — individual task in a checklist template
- **CannedResponse** — template notes for common situations

### Workflow (Intended)
```
Ticket (intake + replies) → Triage → Work Order (repair) → Notes/Checklist → Closed → Invoice Ninja
```
- Tickets are created via email, phone, or web form
- Ticket conversation tracked (replies, updates, status changes)
- Triaged and converted to Work Orders (historical data retained)
- Work Orders track the actual repair
- Notes distinguish customer-visible vs. internal
- Checklists ensure consistent repair steps
- Invoice Ninja integration (Phase 2) — completed work orders → invoices

---

## Ticketing System Design

### Ticket Model Requirements

**Ticket statuses**: New, Open, In Progress, Waiting on Customer, Resolved, Closed

**Fields**:
- Unique ticket number (auto-generated: `TKT-YYYYMMDD-NNNN`)
- Client (foreign key)
- Device (foreign key, optional — may be discovered during triage)
- Subject
- Initial description
- Status (dropdown)
- Priority (if applicable)
- Created/updated timestamps
- Created by (user)

**Ticket replies/conversation** (separate model):
- Parent ticket (foreign key)
- Author (user or external contact)
- Content
- Is internal (boolean — internal staff notes vs. customer-visible)
- Created timestamp

### Ticket Views Needed

1. **Ticket list** — search, filter by status, sort by created/updated, pagination
2. **Ticket detail** — show ticket + threaded replies, quick actions (edit, close, convert to work order)
3. **Ticket reply form** — add reply (internal or customer-visible) inline
4. **Convert to work order** — button that creates work order from ticket, retains ticket reference
5. **Historical search** — search tickets by client/device to identify trends

### Ticket Data Retention

When a ticket is converted to a work order:
- Ticket remains in system (marked as "Converted")
- Work order contains a reference back to the original ticket
- All historical ticket data (replies, updates) stays accessible
- This allows trend analysis: "How many tickets from this client?" "What devices have recurring issues?"

---

## Development Setup

```bash
cd ~/Documents/Claude/murphys-bench
source venv/bin/activate
python manage.py runserver
# http://localhost:8000 — login: admin / password123 (local dev only)
```

---

## Key Decisions Made

- **Tailwind via CDN** — no build step needed for now; can switch to compiled later
- **LoginRequiredMixin on all views** — app is internal-only
- **Admin panel still used** for notes, mileage entry, ticket management until native forms are built
- **Work order numbers** auto-generated as `WO-YYYYMMDD-NNNN`
- **Ticket numbers** auto-generated as `TKT-YYYYMMDD-NNNN`
- **SQLite for dev** — switch to PostgreSQL for production deployment
- **Visual polish deferred** — functionality first, design later
- **GitHub**: Private repo, push after each working feature

---

## Related Projects

- **scs-repair-tracker** (`~/Documents/Claude/scs-repair-tracker`) — Legacy PHP app, reference only
- **Clover** (`~/Documents/Clover`) — macOS desktop app, future integration (Phase 2+)
- **dashboard-mockup.html** (`~/Documents/Claude/dashboard-mockup.html`) — Original UI mockup reference
