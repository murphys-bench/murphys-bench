# Murphy's Bench Development Roadmap

**Last Updated**: June 7, 2026
**Current Phase**: Phase 1 — SCS Internal

---

## Project Status Summary

✅ **COMPLETED**:
- Database schema fully designed and documented
- Django project initialized with all dependencies
- 14 data models created with proper relationships
- Migrations created and applied (SQLite dev, PostgreSQL configured)
- Django admin customized for all 14 models
- Base template with navigation (Tailwind CSS, dark nav bar)
- Authentication — login/logout, all views protected with LoginRequiredMixin
- Dashboard — stats, open work orders, recently closed, quick action buttons
- Work order list + detail views with HTMX inline notes
- Work order create/edit native forms
- Client list + detail views + create/edit native forms
- Device list + detail views + create/edit native forms
- Mileage log view (month filter, running total)
- Ticketing system design finalized and documented
- TicketReply model (threaded conversation)
- Ticket statuses: new, open, in_progress, waiting_on_customer, resolved, closed, converted
- Migration 0002 applied cleanly
- HTMX inline notes on work order detail
- HTMX checklist item toggling
- Default checklists for 6 repair types (57 items), apply on WO create/edit
- **Ticket views**: list, detail, create/edit, HTMX inline reply, convert-to-work-order
- **Batch 1**: Collision Avoidance (TicketLock), WO/Ticket Closure Dependency, Ticket Linking (TicketLink)
- **Batch 2**: Audit Log (django-auditlog, History tab on ticket/WO), Attachments (GenericFK, local/S3, SiteSettings admin panel)
- **Batch 3**: Outbound Email (EmailTemplate, SMTP via SiteSettings), Auto-Responder (ticket_created trigger), three-layer suppression (client flag, pattern list, exact address), EmailSendLog

---

## Phase 1: SCS Internal (Current Focus)

### Build Queue (in order)

---

#### Batch 1 — Collision Avoidance, WO/Ticket Dependency, Ticket Linking

- ✅ **Collision Avoidance** (#9)
  - `TicketLock` model: ticket (OneToOne), locked_by (FK), locked_at
  - Lock expires after 10 min (configurable setting)
  - Ticket detail page: acquire lock on load, show non-blocking banner if locked by another user
  - HTMX polls lock status every 30s on detail page
  - Lock released on navigate-away (`beforeunload` JS) or expiry

- ✅ **Ticket/WO Closure Dependency** (#10)
  - Ticket cannot be marked resolved/closed while a linked WO is open
  - Show warning on ticket detail: "Linked to [WO-XXXX] — ticket cannot be closed until work order is complete"
  - When WO is marked closed: show prompt on ticket detail — "✅ Work Order [WO-XXXX] is complete — this ticket is ready to be resolved" — tech still closes ticket manually
  - `AUTO_RESOLVE_TICKET_ON_WO_CLOSE` admin setting (default **off**) — enables automatic ticket resolution for shops that prefer it

- ✅ **Ticket Linking** (#12 — linking only; merge in Phase 2)
  - `TicketLink` model: ticket_a FK, ticket_b FK, link_type (related/duplicate), created_by, created_at
  - Link/unlink UI on ticket detail sidebar
  - Linked tickets shown on both sides of the link
  - Link types: `related` and `duplicate` only — merge deferred to Phase 2

---

#### Batch 2 — Audit Log, Attachments

- ✅ **Audit Log** (#14)
  - Add `django-auditlog` package
  - Register: Ticket, TicketReply, WorkOrder, WorkOrderNote models
  - "History" tab on ticket detail and work order detail pages
  - Shows: user, action, timestamp, field-level changes

- ✅ **Attachments** (#11)
  - `Attachment` model with `GenericForeignKey`: attaches to Ticket, TicketReply, WorkOrder, WorkOrderNote
  - Fields: file, original_filename, mime_type, size_bytes, uploaded_by, created_at
  - Storage backends (selectable in admin settings):
    - **Local filesystem**: configurable path (`MEDIA_ROOT`) — covers VM local disk, mounted Synology, UNAS, any NAS that mounts as a filesystem path
    - **S3-compatible**: via `django-storages` + boto3 — covers AWS S3, Backblaze B2, MinIO (self-hosted), Wasabi, and any S3-API-compatible service
  - Storage backend and path/credentials configurable in admin settings panel without code changes
  - `MAX_ATTACHMENT_SIZE_MB` (default 25, editable in admin settings panel)
  - `BLOCKED_EXTENSIONS` (exe, bat, sh, ps1, etc., editable in admin settings panel)
  - File input on ticket create, ticket reply form, work order form, note add form
  - Displayed as downloadable links on detail views

---

#### Batch 3 — Outbound Email, Auto-Responder

- ✅ **Outbound Email** (#7)
  - Configure SMTP in settings (host, port, TLS, credentials)
  - `EmailTemplate` model: trigger (ticket_created, reply_added, status_changed, overdue, ticket_resolved), subject template, body template
  - Template variables: `{{ ticket.ticket_number }}`, `{{ ticket.subject }}`, `{{ client.name }}`, `{{ tech.name }}`, etc.
  - Send on: ticket create, customer-visible reply, status change, overdue alert
  - Synchronous sending (no queue/Celery needed at this scale)
  - Email config UI in admin settings panel

- ✅ **Auto-Responder + Outgoing Email Filtering** (#8)
  - On ticket create: automatically send acknowledgment email to client's primary contact email
  - Three-layer suppression system — all managed from admin settings panel:
    1. **Pattern-based blocklist**: catches common automated senders (`noreply@*`, `donotreply@*`, `mailer-daemon@*`, `postmaster@*`, `no-reply@*`) — admin-editable
    2. **Exact address suppression list**: admin can add any specific email address (e.g., a vendor noreply that doesn't match patterns, like T2/ConnectWise automated senders) — prevents bounce loops that pattern matching would miss
    3. **Per-client suppress flag**: `Client.suppress_emails` boolean — silences all automated emails to that client
  - When any layer matches, email is silently skipped and a log entry is written (visible in admin) so suppression is auditable, not invisible

---

#### Batch 4 — SLA Plans, Help Topics & KB, Roles & Permissions

- ✅ **SLA Plans** (#1)
  - `SLAPlan` model: name, grace_period_hours, is_active, is_transient, disable_overdue_alerts
  - `Ticket.sla_plan` FK (optional), `Ticket.due_at` DateTimeField (calculated on assign)
  - `Ticket.is_overdue` property (due_at < now and status not closed/resolved/converted)
  - Overdue badge/highlight on ticket list row and ticket detail
  - If ticket has a linked WO: overdue badge appears on the WO detail as well
  - Management command `check_sla_overdue`: flags newly-overdue tickets, run via system cron every 15 minutes
  - **Overdue acknowledgment workflow**:
    - "Acknowledge Overdue" action available on both ticket detail and linked WO detail
    - Acknowledging from either side satisfies both (ticket + WO both reflect acknowledgment)
    - Acknowledgment requires a note (required, not optional) — stored as an internal reply on the ticket
    - Badge changes to "Overdue — Acknowledged [date] by [name]" (auditable, no longer an unactioned alert)
    - If ticket goes overdue again after acknowledgment, a new acknowledgment is required
  - In-app only — no email alerts for SLA overdue
  - SLA assigned manually on ticket, or via help topic default (once help topics are built)

- ✅ **Help Topics & Internal Knowledge Base** (#2)
  - `HelpTopic` model: name, description, default_sla FK, is_active, sort_order
  - `Ticket.help_topic` FK (optional) — classification only, no auto-routing yet
  - Help topic selector on ticket create/edit form
  - **Single unified KB** — one knowledge base accessible from both ticket and work order detail
  - `KBCategory` model: name, description, sort_order — single-level, admin-managed (Hardware, Networking, Software, Vendor Contacts, Procedures, etc.)
  - `KBArticle` model: title, content (rich text), category FK, article_type, author FK, is_active, is_restricted, created_at, updated_at
  - Article types: `troubleshooting`, `how_to`, `vendor`, `internal`
  - `is_restricted` flag — admin-only articles, visible only to users with `can_manage_settings` permission
  - All other authenticated users can read unrestricted articles
  - Internal-only (no customer-facing portal yet)
  - KB list view with search, category filter, and article type filter
  - KB article detail view
  - KB accessible from nav link, from ticket detail ("Search KB"), and from work order detail ("Search KB")

- ✅ **Roles & Permissions** (#3)
  - Replace flat `role` CharField on User with FK to new `Role` model
  - `Role` model: name, description, is_system (locks defaults from deletion), permission flags:
    - `can_create_ticket`, `can_edit_ticket`, `can_close_ticket`, `can_delete_ticket`
    - `can_assign_ticket`, `can_reply_internal`, `can_reply_customer`
    - `can_create_workorder`, `can_edit_workorder`, `can_close_workorder`
    - `can_merge_tickets`, `can_view_reports`, `can_manage_kb`, `can_manage_settings`
  - Phase 1 seeded roles: **Administrator** (all permissions) and **Technician** (standard set) only
  - Viewer, billing, and dispatcher roles deferred — model supports them when needed
  - `PermissionRequiredMixin` equivalent for each permission flag — applied in views
  - Role management UI in admin settings
  - **Tech skill profiles**: `TechSkill` model (name, description) + M2M on User profile
    - Skills added/managed from user profile in admin
    - Data captured now to feed Phase 2 skill-based ticket auto-routing
    - Example skills: Networking, Hardware, Software, Server, Mobile, Printer

---

#### Batch 5 — Inbound Email

- ✅ **Inbound Email Piping** (#6)
  - Management command `fetch_inbound_email`: polls mailbox, processes messages
  - Protocol selectable in admin settings: **IMAP** or **POP3**
  - Run via system cron every 1–5 minutes
  - New email → create Ticket (sender matched to Client Contact by email, or contact created)
  - Reply to existing ticket → if subject contains `[TKT-YYYYMMDD-NNNN]`, add as TicketReply
  - Email threading by ticket number in subject line
  - Strip quoted reply text from incoming replies (configurable)
  - Attachments on inbound emails → saved as Attachments on ticket/reply
  - Mail server settings in settings.py: host, port, protocol (IMAP/POP3), credentials, SSL
  - SCS mail on cPanel-hosted domain — standard IMAP with username/password, no OAuth2 needed
  - IMAP credentials stored in admin settings panel (not hardcoded)
  - OAuth2 support (Gmail / Office 365) deferred to Phase 2

---

#### Batch 6 — Custom Queues, Reporting & Analytics

- ✅ **Custom Queues / Ticket Views** (#5)
  - `TicketQueue` model: name, owner (user FK, null = shared system queue), filter_criteria (JSON), column_list (JSON), sort_field, sort_direction, is_active
  - System queues (admin-created, visible to all agents): e.g., "Overdue Tickets," "Unassigned — New," "All Open"
  - Personal queues (per-user saved filters)
  - Queue list in left sidebar on ticket list and detail pages
  - UI to create/edit/delete queues
  - Filter criteria supports: status, assigned_to, help_topic, sla_plan, overdue flag, client, date range, custom fields (once #4 is built)

- ✅ **Persistent sidebar** (visible on all pages except dashboard)
  - Shows current tech's assigned tickets and work orders
  - **Accordion style** — two independently collapsible sections: My Tickets / My Work Orders
  - Section headers show item count (e.g., "My Tickets (5)") so content is visible before expanding
  - Accordion state remembered per session
  - Each item: ticket/WO number, client name, truncated subject
  - Items **color coded by status** matching the status badge colors used elsewhere in the app
  - Tech sees own assignments only — keeps sidebar focused and uncluttered
  - Admins see their own assignments in sidebar, same as any tech

- ✅ **Enhanced dashboard**
  - Two tile rows: Tickets (top) and Work Orders (below)
  - **Tech view**: tiles show own assignments — Assigned to Me, Actively Working, Resolved/Completed
  - **Admin view**: tiles show all items — Total, Unassigned, Actively Working, Completed
  - Each tile links to a pre-filtered list showing exactly those items
  - Fully configurable per tile in admin settings: visible/hidden, label, status filter, link target
  - `DashboardTile` model: row (ticket/workorder), label, status_filter (JSON), link_url, sort_order, is_active, visible_to (all/admin/tech)

- ✅ **Reporting & Analytics** (#13)
  - Dedicated `/reports/` section, date range filter (default: last 30 days)
  - Reports (each configurable in admin — show/hide, display order, default date range):
    - Ticket volume over time (bar chart by day/week/month)
    - Open tickets by status (donut chart)
    - Tickets by client (table + bar chart)
    - Tickets by technician (workload distribution)
    - Average ticket resolution time (by tech, by client)
    - SLA compliance rate (% closed before due_at)
    - Ticket to WO conversion rate (% of tickets converted, over time and by client)
    - Mileage by tech and month
  - Chart.js via CDN for visualizations
  - CSV export for every report
  - No new models — all from existing data via ORM aggregations

---

#### Batch 7 — Custom Fields & Forms

- [ ] **Custom Fields & Forms** (#4)
  - `CustomField` model: label, field_type (text/textarea/select/checkbox/date), applies_to (ticket/workorder/both), is_required, help_text, sort_order, is_active
  - `CustomFieldChoice` model: field FK, label, sort_order (for select fields)
  - `CustomFieldValue` model: GenericForeignKey (Ticket or WorkOrder), field FK, value (TextField) — EAV storage
  - Fields can be global or scoped to a HelpTopic (tickets) or RepairType (work orders)
  - Custom fields appear on ticket and work order create/edit forms below standard fields
  - Custom fields displayed on ticket and work order detail views
  - Custom fields searchable in ticket/WO lists and usable in queue filters (#5)
  - Field types for Phase 1: text, textarea, select, checkbox, date
  - Field types deferred to Phase 2: number, email, URL

---

#### Batch 8 — MFA

- [ ] **Multi-Factor Authentication** (#15)
  - Package: `django-two-factor-auth` (TOTP — works with Google Authenticator, Authy, 1Password, etc.)
  - No SMS, no external dependency — fully self-contained
  - **Available to all users** — any user can enroll TOTP from their profile
  - **Enforcement toggle in admin settings**: "Require MFA for all users" — off by default
  - When enforcement enabled: users prompted to enroll on next login before accessing anything
  - Enrollment: QR code scan + confirm code
  - **Backup codes for admin only** — admin is the only one with self-recovery path; all other users recover through admin reset
  - Admin can reset/disable MFA for any user from user management panel (lost device recovery)
  - User re-enrolls on next login after admin reset
  - MFA status shown on user profile page and user management panel

---

### Remaining Phase 1 Items (existing)

- [ ] **Mileage create form** (native form, no admin required)
- [ ] **Testing suite**
  - Model tests (validation, relationships)
  - View tests (authentication, permissions, data display)
  - Form tests (validation, submission)
  - Target: 70%+ coverage
- [ ] **Deployment** (internal network)
  - Configure for internal deployment
  - HTTPS with self-signed certificate
  - Database backup strategy
  - Test on internal network (10.58.58.x)

---

### Phase 1 Completion Criteria

- [ ] All SCS core workflows functional (ticketing, work orders, tracking)
- [ ] Deployed to internal network and in daily use
- [ ] Techs prefer it to legacy PHP app
- [ ] Tests passing, code quality good
- [ ] No critical bugs in production
- [ ] Email integration working (inbound tickets, outbound updates)
- [ ] Comprehensive self-hosting documentation

---

## Phase 2: Integrations, Polish & Advanced Features

### Research Tasks (prerequisites for Phase 2 build)

- [ ] **Invoice Ninja API audit**
  - Document what data IN exposes via API (clients, invoices, payments, line items)
  - Determine what can be pushed from Murphy's Bench (completed WO → invoice)
  - Determine what can be pulled back (payment status, invoice state)
  - Identify auth method (API token) and rate limits
  - Output: design doc for the integration before any code is written

---

### From OSTicket comparison — deferred features

- [ ] **Ticket Merging** (destructive — secondary absorbed into primary)
  - Move all replies from secondary to primary with source annotation
  - Mark secondary as `merged`, store `merged_into` FK
  - Re-link any WO from secondary to primary if applicable
  - Confirmation UX: require typing target ticket number to confirm

- [ ] **Departments**
  - Organize agents into departments (Hardware, Networking, etc.)
  - Department-level SLA defaults and email templates
  - Per-department role assignments (agent can have different role in different departments)
  - Ticket auto-routing by department (ticket filters)

- [ ] **Teams**
  - Cross-department groups (e.g., "Senior Techs," "On-Call")
  - Assignable to tickets regardless of department

- [ ] **Ticket Auto-Routing / Filters**
  - Rule engine: route tickets based on subject keywords, sender email, help topic, custom fields
  - Actions: assign to agent/team/department, set SLA, apply canned response, reject

- [ ] **Customer Self-Service Portal**
  - Customer-facing web portal to submit tickets, check status, view history
  - No account required (email + ticket number lookup)
  - Browse internal KB articles marked as public

- [ ] **Additional attachment storage backends**
  - TrueNAS API-based storage (SMB/NFS mounts already covered by local filesystem path)
  - Any provider-specific integrations that don't conform to the S3 API

- [ ] **Custom Field Types: number, email, URL** (extension of Phase 1 #4)

- [ ] **REST API**
  - Create tickets, update status, add replies
  - Authentication via API token
  - Enables Taskbar Utility App integration (Clover/Phase 1.5)

- [ ] **Invoice Ninja API Bridge**
  - One-way push: completed work orders → Invoice Ninja invoices
  - Line items from WorkOrderItems
  - Triggered when WO status = completed

### Polish & Infrastructure

- [ ] **Async Email Queue** (Celery or django-q2 if volume demands it)
- [ ] **Email OAuth2** (Gmail / Office 365 IMAP via OAuth2 if not needed in Phase 1)
- [ ] **Visual design polish** (branding, colors, mobile responsive)
- [ ] **Performance optimization** based on real usage data
- [ ] **README.md** — setup instructions, self-hosting guide

---

## Phase 3+: Multi-Tenant SaaS (Speculative)

*Only if multiple companies request hosted version. Years away if ever.*

---

## Quick Start

```bash
cd ~/Documents/Claude/murphys-bench
source venv/bin/activate
python manage.py runserver
# http://localhost:8000 — login: admin / password123 (local dev only)
```

**Key files**:
- `core/views.py` — all views
- `core/urls.py` — URL routing
- `core/models.py` — all data models
- `core/forms.py` — all forms
- `core/admin.py` — admin customization
- `core/templates/core/` — all HTML templates
- `murphys_bench/settings.py` — Django settings
