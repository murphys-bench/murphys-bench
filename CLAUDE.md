# Murphy's Bench

**Status**: Phase 1 Active Development — Deployed Internally (10.58.58.82)
**Tech Stack**: Python 3.12 / Django 4.2 / HTMX / Alpine.js / Tailwind CSS (CDN)
**Deployment Model**: Self-hosted on internal network (Proxmox VM, Gunicorn + Nginx, PostgreSQL 16)
**Repository**: `~/Documents/Claude/murphys-bench` + GitHub (private)
**Last Updated**: June 9, 2026 (end of session 17)

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
- `/work-orders/<id>/billing/` — HTMX: update billing state (quick-action + full edit)
- `/work-orders/<id>/log-labor/<item_id>/` — HTMX: log Quick Labor Work Performed entry
- `/work-performed/<id>/delete/` — HTMX: remove Work Performed entry
- `/clients/<client_id>/contacts/new/` — Create contact (form POST, redirects back)
- `/contacts/<id>/edit/` — Update contact with multiple phones
- `/contacts/<id>/delete/` — Delete contact
- `/settings/` — Native Settings UI (admin only, 6 tabs)

**What still requires admin panel:**
- Suppressed address management (SuppressedAddress model)
- Email send/receive log review (EmailSendLog, InboundEmailLog — read-only)
- SLA Plans, Help Topics, KB Categories (admin-managed)
- Roles and TechSkills management
- Credential access log review (CredentialAccessLog — read-only, audit only)

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
- Org-level credentials vault (OrgCredential + CredentialAccessLog)
- Device-level credentials (password field on Device, encrypted)
- Email Template Manager UI, Status Management UI, Data Management (import/export/deleted/reset)
- Financial reporting (invoiced/paid/outstanding by client)
- Invoice Ninja API bridge
- Email OAuth2 (Gmail/Office 365)
- Departments, Teams, Auto-routing
- Customer self-service portal
- REST API (for Taskbar Utility App / Clover integration)

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
│           ├── billing_card.html
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

### Data Models (33 current, 33 migrations applied)
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
- **Invoice** — billing state tracker; OneToOne on WorkOrder; billing_status enum (uninvoiced/invoiced/paid/paid_direct/disputed); amount, dates, payment_method, notes; auto-created on WO creation via signal
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

### ✅ Batch 11 — Foundational Client-Centric Rebuild (sessions 10–11 — COMPLETE)

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

### ✅ Session 13 — Cross-Visibility + Bug Fixes (session 13 — COMPLETE)

- **Cross-visibility panels**: Open tickets panel on WO detail; open WOs panel on ticket detail — status, last note/reply preview, one-click navigation
- WO detail toolbar: linked ticket shown as clickable purple pill (← TKT-XXXXX)
- Converted tickets stay visible in sidebar, dashboard "My Open Tickets" tile, and cross-visibility panels until resolved/closed
- History tab removed from ticket detail (consistent with WO detail)
- Sidebar: shows last reply/note preview instead of subject/description; falls back gracefully if no notes
- Mileage Calculate button: fixed CSRF token for production (was silently failing in prod)
- Google Maps API confirmed working from production server (WAN IP restriction set in Cloud Console)

### ✅ Session 17 — Phase 2 Foundations (session 17 — COMPLETE)

- **Invoice CSV export**: `InvoiceExportView` at `/clients/<pk>/invoices.csv` — all invoices for a client, optional `?status=` filter. CSV button on client detail WO History header.
- **Icon audit**: 10 new icons added to `mb_icons.py` (x-mark, exclamation-triangle, lock-closed, user, key, document-text, chevron-up/down/right, arrow-down-tray, eye). All emoji/text symbols replaced across templates. Fixed arrow-down-tray silently rendering nothing.
- **Billing financial summary on Reports page**: Invoiced/Collected/Outstanding metric cards + outstanding-by-client table with CSV links. Billing CSV export at `/reports/csv/billing/`.
- **Org credentials vault**: `OrgCredential` + `CredentialAccessLog` models (migration 0034). Settings → Credentials tab. AES-256 encrypted username/password/notes. HTMX eye-reveal logs every access. CRUD with admin-only flag. Every view/edit/delete written to audit log.
- **Email Template Manager**: Settings → Email Templates tab. Native UI for all 4 `EmailTemplate` triggers. Editable subject/body (monospace), active toggle, variable reference panel (`{% verbatim %}`), last-updated timestamp. Auto-creates inactive defaults on first visit.
- **Team workload widget**: Dashboard (admin only) — Team Workload table showing open WOs + tickets per tech, sorted by total load, counts link to filtered lists.
- **Technician performance report**: Reports page — WOs in period, completed count, completion % (color-coded), avg resolution hours, current open WOs. CSV export at `/reports/csv/tech_perf/`.
- **Doc sweep**: MB_UI_UX_Analysis.md content merged into CLAUDE.md + TODO.md. Stale admin panel entries cleaned up.
- Production deployed: migration 0034 applied, all changes live.

### ✅ Session 16 — Invoice Model (session 16 — COMPLETE)

- **`Invoice` model**: OneToOne on WorkOrder (`db_table = 'invoices'`). Fields: `billing_status` (uninvoiced/invoiced/paid/paid_direct/disputed), `amount`, `invoiced_date`, `paid_date`, `payment_method`, `notes`
- **Signal**: `post_save` on WorkOrder auto-creates Invoice on WO creation
- **Migration 0033**: CreateModel + `backfill_invoices` RunPython for existing WOs; applied to production
- **`WorkOrderBillingUpdateView`**: HTMX POST. Quick-action mode (just `billing_status`): updates status + auto-sets dates on first transition. Full edit mode (`full_edit=1`): updates all fields. Returns `billing_card.html` partial.
- **`billing_card.html`** partial: display mode shows status badge + amount + dates + quick-action buttons (contextual per status). Edit mode (Alpine.js toggle): full form. HTMX `hx-swap="outerHTML"` on `#billing-card`.
- **WO detail**: billing card inserted in right column between "Update Work Order" and "Device Credentials"
- **Client detail**: outstanding balance badge (yellow pill) next to "Work Order History" heading — sum of `uninvoiced`+`invoiced` WO amounts
- URL: `/work-orders/<pk>/billing/` → `wo_billing_update`
- Production deployed: migration 0033 applied, Gunicorn reloaded

### ✅ Session 15 — Visual Polish (session 15 — COMPLETE)

- **Color-coded dashboard tiles**: left-border accent per status (Blue=active, Yellow=waiting, Red=overdue, Green=completed). Color computed in `_tile_color()` from `status_filter` and `link_url`.
- **SVG icons replacing emoji**: all dashboard tiles and quick stats row now use Heroicons outline via `{% icon name size %}` templatetag (`core/templatetags/mb_icons.py`)
- **Device type icon grid**: replaced Device Type dropdown on device form with 2-row × 4-col Alpine.js button grid (Laptop, Desktop, Mobile, Tablet, Server, Printer, Other). Selected state highlighted blue.
- Migration 0032: data migration converting emoji icon values → icon name strings in DashboardTile
- Production deployed: migrations 0031 + 0032 applied, `FIELD_ENCRYPTION_KEY` set in prod `.env`, Gunicorn reloaded

### ✅ Session 14 — Credential Encryption + Billing Architecture (session 14 — COMPLETE)

- **Credential encryption (migration 0031)**: `WorkOrder.device_username`, `device_password`, `device_pin`, `credential_notes` and `SiteSettings.email_password`, `inbound_password` now AES-256 encrypted at rest via `django-encrypted-model-fields` (Fernet symmetric encryption)
- `FIELD_ENCRYPTION_KEY` added to `murphys_bench/settings.py` — reads from env, dev fallback only
- `encrypted_model_fields` added to INSTALLED_APPS and `requirements.txt`
- `.env.example` updated with key generation instructions and warning
- RepairShopCRM comparative UI/UX analysis completed — documented in `MB_UI_UX_Analysis.md`
- **⚠️ Production deployment of migration 0031 is PENDING** — must set `FIELD_ENCRYPTION_KEY` in production `.env` BEFORE pulling. Must be done together. See `memory/project_credential_encryption_deploy.md`.

### ✅ Batch 12 — Production Deployment + WO Detail Polish (session 12 — COMPLETE)

**Deployment:**
- Ubuntu 24.04 VM on Proxmox (10.58.58.82), PostgreSQL 16, Gunicorn + Nginx, systemd
- Python 3.12 (Ubuntu 24.04 default), SSH key auth, config data migrated via dumpdata/loaddata

**WO Detail improvements:**
- Inline editing: Device card (reassign device), Details card (repair type, assigned to, scheduled date, contact, invoice ref)
- Custom repair type on the fly (＋ Custom… option in Details edit, get_or_create)
- Attachment upload form in Attachments tab (WorkOrderAttachmentUploadView)
- History tab removed from WO detail
- Work Performed redesign: editable entries (pencil/trash SVG icons), custom log form, collapsible Log Work buttons
- WorkPerformed model: labor_item nullable, custom_label + notes fields, ordered by logged_at
- Pre/Post Checklist: pre_check + post_check fields on WorkOrderItem, auto-saving dropdowns, color-coded rows, checked count in header
- Device Credentials: display-only by default, PIN masked like password, Edit toggle
- Add Note: radio buttons instead of dropdown for note type
- Quick Actions: removed redundant Add Note button

**Settings additions:**
- site_logo (ImageField), color_nav_text, color_sidebar_bg, color_sidebar_text in SiteSettings
- ColorSettingsForm expanded; base.html CSS variables updated; sidebar uses opacity-based text hierarchy
- Font size dropdowns (px values stored in localStorage)
- Client list redesigned to match legacy app layout (ACCOUNT/TYPE/CONTACT/PHONE/EMAIL/DEVICES/WOs)

### Remaining / Future
- **Testing suite** (deferred — will write after real-world use surfaces actual edge cases)
- **Cloudflare tunnel** — external access when ready
- **Site-wide icon audit** — replace remaining text symbols (×, etc.) with SVG icons

---

## Key Decisions Made

- **Tailwind via CDN** — no build step needed for now
- **LoginRequiredMixin on all views** — app is internal-only
- **Work order numbers** auto-generated as `WO-YYYYMMDD-NNNN`
- **Ticket numbers** auto-generated as `TKT-YYYYMMDD-NNNN`
- **SQLite for dev** — switch to PostgreSQL for production
- **Visual polish** — shipped session 15: color-coded dashboard tiles, SVG icons replacing emoji, device type icon grid
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
- **Credential encryption**: AES-256 via `django-encrypted-model-fields`. `FIELD_ENCRYPTION_KEY` read from env. Never plaintext. Migrations 0031 + 0032 applied to production (June 9, session 15). Key stored in Bitwarden.
- **Billing philosophy**: MB tracks billing state only — not an accounting module. Lightweight `Invoice` entity on WorkOrder (not fields on WO directly). `billing_status` enum: uninvoiced / invoiced / paid / paid_direct / disputed. `paid_direct` = cash/walk-in before formal invoice. Invoice Ninja and other systems remain authoritative for formal financials.
- **Visual design is a first-class requirement**: Color + icons communicate status faster than text. Not optional polish.
- **Modals for quick edits, full pages for complex creation**: Settings section edits, status changes, mark-as-paid → modal. New Ticket, New WO, New Client → full page form.
- **Soft-delete everything**: Hard deletes require deliberate admin action (type-to-confirm). No silent permanent deletes in normal operation.
- **Export-based integrations**: CSV export works with any accounting system. No live API sync until there is clear demand. More flexible and future-proof.
- **Org-level credentials vault is a competitive advantage**: RepairShopCRM has device-level credentials only, no audit trail. MB's org vault + access log is a differentiator — build it properly in Phase 2.
- **Status color convention**: Blue = In Progress/Active, Yellow = Waiting on Customer, Red = Overdue/Urgent, Green = Completed, Gray = New/Unassigned.
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
