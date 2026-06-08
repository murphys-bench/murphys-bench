# Murphy's Bench

**Status**: Phase 1 Active Development — Ready for Internal Deployment
**Tech Stack**: Python 3.11 / Django 4.2 / HTMX / Alpine.js / Tailwind CSS (CDN)
**Deployment Model**: Self-hosted on internal network (not cloud, not SaaS)
**Repository**: `~/Documents/Claude/murphys-bench` + GitHub (private)
**Last Updated**: June 8, 2026 (end of session 9)

---

## Current App State (What's Working)

The app is running locally at `http://localhost:8000`. All views require login.

**Working URLs:**
- `/` — Dashboard (stats, open work orders, recently closed)
- `/account/login/` — Login page (two_factor styled)
- `/account/two_factor/` — Account security / MFA enrollment
- `/account/two_factor/setup/` — TOTP setup wizard (QR code)
- `/account/two_factor/backup/tokens/` — Backup tokens (admin only, printable)
- `/work-orders/` — Work order list (search, filter, pagination)
- `/work-orders/new/` — Create work order (native form, includes service type)
- `/work-orders/<id>/` — Work order detail (HTMX inline notes, checklist, stopwatch, + Mileage button for onsite)
- `/work-orders/<id>/edit/` — Edit work order
- `/work-orders/<id>/add-time/` — HTMX: add minutes to time_spent (stopwatch log)
- `/work-orders/<id>/add-mileage/` — Mileage form launched from WO (pre-filled, Google Maps Calculate)
- `/clients/` — Client list (search, active filter)
- `/clients/new/` — Create client
- `/clients/<id>/` — Client detail (contacts, devices, work history)
- `/clients/<id>/edit/` — Edit client
- `/devices/` — Device list (search, type filter)
- `/devices/new/` — Create device
- `/devices/<id>/` — Device detail (repair history)
- `/devices/<id>/edit/` — Edit device
- `/mileage/` — Mileage log (month filter, running total, edit links)
- `/mileage/new/` — Log mileage (native form)
- `/mileage/<id>/edit/` — Edit mileage entry
- `/mileage/calculate/` — Server-side Google Distance Matrix proxy (POST, JSON)
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
- `/users/` — User management (admin only — shows all users with MFA status)
- `/users/<id>/reset-mfa/` — Admin MFA reset for lost device recovery (POST)
- `/admin/` — Django admin (full access, staff only)

- `/work-orders/<id>/print/` — Repair Report (print-optimized, opens new tab)
- `/work-orders/<id>/credentials/` — HTMX: save device credentials inline
- `/work-orders/<id>/log-labor/<item_id>/` — HTMX: log Quick Labor Work Performed entry
- `/work-performed/<id>/delete/` — HTMX: remove Work Performed entry
- `/clients/<client_id>/contacts/new/` — Create contact (form POST, redirects back)
- `/contacts/<id>/edit/` — Update contact with multiple phones
- `/contacts/<id>/delete/` — Delete contact
- `/settings/` — Native Settings UI (admin only, 6 tabs)

**What still requires admin panel:**
- Managing checklists and canned responses
- Email template editing (EmailTemplate model)
- Suppressed address management (SuppressedAddress model)
- Email send/receive log review (EmailSendLog, InboundEmailLog — read-only)
- SLA Plans, Help Topics, KB Categories (admin-managed)
- Roles and TechSkills management
- QuickLaborItem management (add/edit/retire quick labor buttons)

**Note**: All routine workflow actions (create client, work order, device, contact) now use native app pages. The Django admin is staff-only config/reference only.

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
- **Frontend**: Tailwind CSS (CDN), HTMX, Alpine.js
- **Database**: SQLite (dev), PostgreSQL (planned for production)
- **Auth**: Django session auth + django-two-factor-auth (TOTP), LoginRequiredMixin on all views

### Project Structure
```
murphys-bench/
├── CLAUDE.md                    # This file — read first each session
├── TODO.md                      # Full roadmap and build order
├── manage.py
├── requirements.txt
├── murphys_bench/              # Django project settings
│   ├── settings.py
│   └── urls.py
├── core/                        # Main app
│   ├── models.py               # All 32 data models
│   ├── views.py                # All views
│   ├── urls.py                 # Core URL patterns
│   ├── forms.py                # All forms
│   ├── admin.py                # Admin customization
│   ├── middleware.py           # MFAEnforcementMiddleware
│   ├── email_utils.py          # Outbound email helpers
│   ├── management/commands/
│   │   ├── check_sla_overdue.py    # Cron: flag overdue tickets
│   │   └── fetch_inbound_email.py  # Cron: poll IMAP/POP3 mailbox
│   └── templates/core/
│       ├── base.html
│       ├── dashboard.html
│       ├── work_order_list.html
│       ├── work_order_detail.html  # Stopwatch timer, + Mileage button (onsite)
│       ├── work_order_form.html    # Includes service_type field
│       ├── client_list.html
│       ├── client_detail.html
│       ├── client_form.html
│       ├── device_list.html
│       ├── device_detail.html
│       ├── device_form.html
│       ├── mileage_list.html       # Edit links per row
│       ├── mileage_form.html       # General mileage create/edit
│       ├── mileage_wo_form.html    # WO-linked mileage with Calculate button
│       ├── user_list.html          # Admin user management + MFA status
│       ├── ticket_list.html
│       ├── ticket_detail.html
│       ├── ticket_form.html
│       ├── ticket_convert.html
│       ├── kb_list.html
│       ├── kb_detail.html
│       ├── kb_form.html
│       ├── queue_list.html
│       ├── queue_detail.html
│       ├── queue_form.html
│       ├── reports.html
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
│           └── sidebar_content.html
├── templates/two_factor/        # Tailwind overrides for django-two-factor-auth
│   ├── _base.html               # Extends Murphy's Bench base.html (profile pages)
│   ├── _base_focus.html         # Standalone centered card (login/setup pages)
│   ├── _wizard_forms.html
│   ├── _wizard_actions.html
│   ├── core/login.html
│   ├── core/setup.html
│   ├── core/setup_complete.html
│   ├── core/backup_tokens.html  # Printable backup token list
│   ├── profile/profile.html     # Account security page
│   └── profile/disable.html
├── accounts/                    # Auth app
└── docs/
    ├── database-schema.md
    ├── ticketing-design.md
    └── next-session-prompt.md
```

### Data Models (32 current)
- **Role** — permission role with 16 boolean flags; seeded: Administrator, Technician
- **TechSkill** — skill tags M2M on User; captured for future skill-based routing
- **User** — extended Django user; role CharField (legacy) + role_obj FK to Role + skills M2M
- **Client** — company/customer
- **Contact** — person at a client company
- **Device** — equipment being serviced
- **SLAPlan** — response deadline config (grace_period_hours, overdue alerts toggle)
- **HelpTopic** — ticket classification with optional default SLA
- **Ticket** — initial service request; statuses: new, open, in_progress, waiting_on_customer, resolved, closed, converted
- **TicketReply** — threaded conversation on a ticket (customer_visible or internal)
- **WorkOrder** — repair job; service_type (in_shop/onsite/remote); time_spent_minutes; linked to originating ticket via OneToOne
- **WorkOrderNote** — customer-visible or internal notes on a work order
- **WorkOrderItem** — checklist items, parts, time entries
- **Mileage** — travel logging; trip_type (one_way/round_trip); optionally linked to WorkOrder
- **RepairType** — category (Laptop Repair, Desktop Repair, etc.)
- **Checklist** — template task list linked to a repair type
- **ChecklistItem** — individual task in a checklist template
- **CannedResponse** — template notes for common situations
- **TicketLock** — collision avoidance; OneToOne on Ticket, 10-min expiry
- **TicketLink** — links related/duplicate tickets; unique_together on (ticket_a, ticket_b)
- **SiteSettings** — singleton; SMTP, inbound email, attachment config, Google Maps API key + shop address, require_mfa toggle
- **Attachment** — GenericFK to Ticket/TicketReply/WorkOrder/WorkOrderNote; local or S3 storage
- **EmailTemplate** — trigger-based outbound email templates (4 triggers, seeded with defaults)
- **SuppressedAddress** — exact email addresses that never receive automated email
- **EmailSendLog** — audit trail for every outbound send attempt
- **InboundEmailLog** — audit trail for every inbound message fetched
- **KBCategory** — knowledge base category (admin-managed)
- **KBArticle** — KB article; types: troubleshooting / how_to / vendor / internal; is_restricted flag
- **TicketQueue** — Saved ticket filters; owner=null = system queue; filter_criteria JSONField
- **DashboardTile** — Configurable dashboard tile; row (ticket/workorder), status_filter, visible_to
- **CustomField** — Admin-defined extra fields for Tickets or Work Orders; scoped to HelpTopic or RepairType
- **CustomFieldChoice** — Options for select-type CustomFields
- **CustomFieldValue** — EAV storage: one row per (object, field) pair; GenericForeignKey

---

## Ticketing System Design

See `docs/ticketing-design.md` for full detail.

### Ticket Statuses
`new` → `open` → `in_progress` → `waiting_on_customer` → `resolved` → `closed`
Also: `converted` (converted to Work Order — read-only after this point)

### Ticket → Work Order Rules
- A ticket linked to an open WO **cannot** be closed/resolved — hard block
- When the WO closes, ticket shows a prompt: "WO complete — ready to resolve" — tech closes manually
- `AUTO_RESOLVE_TICKET_ON_WO_CLOSE` admin setting (default **off**)
- Ticket remains in system after conversion — full history retained

---

## Phase 1 Feature Status

### ✅ Batch 1 — Collision Avoidance, WO/Ticket Dependency, Ticket Linking
### ✅ Batch 2 — Audit Log, Attachments
### ✅ Batch 3 — Outbound Email, Auto-Responder
### ✅ Batch 4 — SLA Plans, Help Topics/KB, Roles & Permissions + Stopwatch timer
### ✅ Batch 5 — Inbound Email (IMAP/POP3, threading, quote strip, attachments)
### ✅ Batch 6 — Custom Queues, Persistent Sidebar, Enhanced Dashboard, Reporting
### ✅ Batch 7 — Custom Fields (EAV, scoped to HelpTopic/RepairType, all field types)
### ✅ Batch 8 — MFA (TOTP, enforcement toggle, backup tokens, admin reset, user management panel)
### ✅ Batch 9 — Mileage native form, service_type on WO, Google Maps auto-calculate

### ✅ Batch 10 — Legacy App Gap Closure (complete — session 8)
- **P1**: Repair Report (`/work-orders/<id>/print/`), Company Info in SiteSettings, Quick Labor / Work Performed (HTMX)
- **P2**: Credentials on WO (masked), Client Type badge (Residential/Business), Multiple phones per Contact (Alpine.js dynamic rows), Contact notes + receives_email, Invoice Ninja Ref # deferred to Phase 2
- **P3**: Native Settings UI at `/settings/` — 6 tabs: Company, Outbound Email, Inbound Email, Attachments, Security, Mileage

### 🔜 Batch 11 — Foundational Client-Centric Rebuild (session 9 — planned, not yet built)

Full spec in `docs/batch-11-plan.md`. Identified by complete side-by-side audit of the legacy
PHP app (SCS Repair Tracker) vs Murphy's Bench. Core problem: Murphy's Bench treats Clients,
Contacts, Devices, and Work Orders as peer objects. The legacy app — and correct workflow — is
**client-centric**: everything flows through the client.

**Priority 1 — Device + Client Hub:**
- Device model: add `os`, `os_version`, `condition_at_intake`, `assigned_contact` FK, "Save & Create WO" button. Remove Device from top-level nav.
- WorkOrder: add `contact` FK (nullable) — "whose WO is this?" Shown in WO History, WO detail header, WO create/edit form.
- Client detail as hub: single-column layout, per-contact "+ WO" button, inline device add, phone custom label + type dropdown, inline client type edit, Set Primary Contact.
- Client edit: Deactivate (block if WOs on delete) + Permanently Delete (type-to-confirm).

**Priority 2 — WO Detail + Print:**
- Unified black action toolbar: View Client | Edit Client | Edit Device | Edit WO | WO History | Repair Report | Claim Ticket | Email Report | Status ▼
- Client info + Device info (OS, serial, condition) on WO page.
- Days Open counter, Completed Date, Invoice Ninja Ref #.
- Work Performed entries show bold label + description + timestamp.
- Pre/Post Checklist collapsed by default. Credentials "+ Add note" field.
- Repair Report: add OS/version/condition, note timestamps, signature lines, footer.
- Claim Ticket: same template, `?type=claim` changes title only.

**Priority 3 — Native Settings UI Expansion:**
- Repair Types: native CRUD with categories + ▲/▼ reorder. Needs new `RepairTypeCategory` model.
- Canned Responses: two Note Streams (Customer Notes / Tech Notes Internal), categories per stream, CRUD, picker on WO detail.
- Quick Labor: native CRUD (currently Django admin only).
- Checklist Items: model change — flat bank scoped by device type (not per-repair-type). Migration required.
- Status Colors + Site Colors: hex inputs + live preview, stored in SiteSettings, rendered as CSS variables in base.html.
- Company Info: split address into Line 1, Line 2, City, State, Zip (both SiteSettings and Client model). Report Header Preview.
- Display Settings: browser-local UI preferences (localStorage) — nav/sidebar/content font size, card density (Compact/Normal/Comfortable).

**Decisions locked in session 9:**
- Permanently Delete blocks if client has WOs; offers Deactivate instead
- Address: 5 fields (Line 1, Line 2 optional, City, State, Zip) — no country field
- Existing address data migrates to Line 1; user cleans up manually
- Colors stored in SiteSettings; rendered as `<style>` block of CSS variables in base.html
- RepairTypeCategory model needs to be created with sort_order field
- Device assigned_contact: server-side queryset filter (client_id from URL param); no HTMX cascade needed (standalone Device page being removed)

### Remaining Before Deployment
- **Batch 11** — foundational rebuild (see above + `docs/batch-11-plan.md`)
- **Testing suite** (deferred — will write after real-world use surfaces actual edge cases)
- **Deployment** — internal network, HTTPS, PostgreSQL, backup strategy

---

## Key Decisions Made

- **Tailwind via CDN** — no build step needed for now
- **LoginRequiredMixin on all views** — app is internal-only
- **Work order numbers** auto-generated as `WO-YYYYMMDD-NNNN`
- **Ticket numbers** auto-generated as `TKT-YYYYMMDD-NNNN`
- **SQLite for dev** — switch to PostgreSQL for production
- **Visual polish deferred** — functionality first
- **GitHub**: Private repo, push after each working feature
- **HTMX** for inline interactions (notes, replies, checklist toggling)
- **No Celery/async queue** — synchronous email sending is sufficient at MSP scale
- **No OAuth2** for email — SCS uses cPanel-hosted mail with standard IMAP/POP3 credentials
- **Single unified KB** — not split between tickets and work orders
- **Ticket close is always manual** even when linked WO closes — forces human contact
- **MFA backup codes for admin only** — other users recover via admin reset
- **SLA overdue alerts are in-app only** — acknowledgment with required note creates audit trail
- **Attachment storage Phase 1**: local filesystem (configurable path) + S3-compatible
- **Alpine.js** loaded via CDN in base.html with `defer` — required for sidebar accordion
- **Sidebar**: HTMX-loaded on every page except dashboard; admins see all, techs see own
- **`?assigned_to=me` filter**: works on both `/tickets/` and `/work-orders/`; admins see all
- **Audit log gotcha**: `changes_dict` can contain an `'items'` key that shadows `dict.items()` in Django templates. Always use `_audit_entries()` from views.py — never iterate `changes_dict.items` in templates.
- **Queue filter_criteria**: JSON dict with optional keys: `status` (list), `assigned_to` (int or null), `overdue` (bool), `client` (int), `help_topic` (int), `sla_plan` (int). The `assigned_to: null` key (explicit null, not absent) means "unassigned only".
- **Google Maps mileage**: API call is server-side via `MileageDistanceView` — key never sent to browser. Tested working in architecture; needs verification on internal server (outbound HTTPS required).
- **Service type on WorkOrder**: in_shop / onsite / remote. `+ Mileage` button appears on WO detail only when service_type == onsite.
- **two_factor template overrides** live in root `templates/two_factor/` (in DIRS), NOT in `core/templates/` — DIRS takes priority over APP_DIRS in Django's template loader.

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
