# Murphy's Bench Development Roadmap

**Last Updated**: June 24, 2026 (session 44 — self-sufficiency: `manage.py export_data` portable CSV+media export with secret redaction, commit a145d05, suite →109; session 43 — `scripts/restore.sh` one-command restore, drill-verified, commit 83052a4; session 42 — ticket SLA bugfix via `Ticket.first_responded_at`, migration 0065, suite 104→107. All deployed on prod/MB2/mb-test. Prior: session 41 — front-end fully self-hosted/no CDN; session 37 — inbound duplicate-ticket fix + full system assessment → BookStack page 09; see "System assessment & remediation" below)
**Current Phase**: Phase 1 — SCS Internal — **STABILIZATION** (see "How We Work" in CLAUDE.md)

> ⚠ We are in a stabilization phase, not a feature phase. New features are paused until
> the test suite is broader and the spine is solid. Default response to a feature request
> is to check it against the stabilization rule in CLAUDE.md first.

---

## System assessment & remediation (Jun 22 2026 — see BookStack page 09)

Full report-only audit across 8 domains. Verdict: app/code/data/security are sound (verified green);
every failure lived in the operational/process shell. Root cause = no verification/observability.
- [x] DB backup rebuilt — SQLite snapshot → immutable Backblaze B2, restore-drilled (Jun 22)
- [x] Inbound duplicate-ticket bug fixed — atomic claim + DB constraint + run-lock, migration 0062 (Jun 22)
- [x] **T1 · Observability keystone** — ✅ DONE Jun 22 (session 39): MB self-monitors → failures open a System Alert ticket. App 500s, systemd OnFailure on all 3 timers, daily disk check, backup dead-man's-switch (healthchecks.io), logrotate. `core/system_alerts.py` + `manage.py send_alert` + `SystemAlertHandler`, migration 0063, suite →102. Built on staging→prod. (deploy/README.md → Observability)
- [x] **T1 · Fix PBS whole-VM backup** — ✅ DONE Jun 22 (Mike-driven): resolved VMID 102/103 collisions (BookStack→202, Cloudflared→203, prod stays 103), purged dead WinXP/ITFlow groups (GC reclaimed 82GB), daily verify job + centralized prune (7/4/3), both PVE jobs → Selection "All", notify-on-fail→ticket. Convention: scsprox2=1xx, scsprox=2xx. (memory `reference_proxmox_pbs_infra`)
- [ ] **T1 · Correct any remaining stale doc claims** (PBS-as-safety-net / IMAP references).
- [x] **T2 · Dedicated TEST VM** — ✅ DONE Jun 22: `mb-test` (VMID 201, 10.58.58.108), fresh install-from-git, Ubuntu 24.04.4/Py3.12, SQLite, prod-data copy + prod key, integrations neutralized, read-only deploy key for git-pull deploys; 100/100 tests + login verified. Surfaced+fixed INSTALL.md shake-out (commit b1c1856). Still to do: actually *use* it as the gate (deploy→verify→prod) + snapshot-before-migrate; align the dev Mac venv to Py3.12.
- [ ] **T2 · Decommission the unused PostgreSQL** server + remove dead `DB_*` lines from `.env`.
- [ ] **T2 · Rotate the broad GitHub PAT** still on the scs-repair-tracker box.
- [x] **CI test gate — ✅ DONE Jun 23** (`.github/workflows/ci.yml`): runs pytest + `manage.py check` on every push/PR (Py3.12, ephemeral keys, SQLite). First run green. Makes the 102-test discipline self-enforcing. Mac→GitHub now over SSH (token/keychain friction retired).
- [x] **logrotate — ✅ DONE Jun 22** (gunicorn logs; part of the observability pass).
- [ ] **T3 (hygiene / by decision):** `pip-audit` loop · `fail2ban` · TLS decision · ClamAV · (later: lint config/ruff, mypy — low priority per review: gate > tooling).

## Billing work (decided Jun 19 2026 — see memory `project_mb_pricing_architecture` + `project_in_integration`)

The one approved post-stabilization feature (Invoice Ninja bridge), staged into two phases
after a long technical-director discussion. MB captures NO pricing today — that schema gap is
the expensive-to-reverse-with-live-data piece, so it lands FIRST.

- [x] **Phase A — priced line-item primitive** ✅ DONE (session 33, Jun 20, live on prod).
      New generic `LineItem` (GenericFK, kind labor/part, qty, unit_price, computed line_total),
      `QuickLaborItem.default_price` prefill, WO total on detail + repair report. Unified
      `WorkPerformed` into LineItem (migrated + deleted). Migrations 0058/0059/0060, suite →88.
      Browser-verified by Mike. Full detail in memory `project_mb_session33_phase_a`.
- [x] **Phase B — Invoice Ninja push** ✅ DONE + live-verified (session 34, Jun 20). Manual
      "Send to Invoice Ninja" on a WO → draft invoice from priced lines, IN assigns number, WO# →
      po_number; type-aware find-or-create client (stores IN client_id); WO-scoped duplicate guard;
      editable ref; create-only/no auto-email; fail-loud. `core/invoice_ninja.py`, migration 0061,
      suite →96. Detail in memory `project_mb_session34_phase_b`.
      - [ ] **Companion (zero-code, do anytime):** configure **Square as a payment gateway inside IN**
            so IN records payments + can send hosted pay-now links. Not an MB task — config in IN.
            (Confirm Square account exposes API creds.) See `project_in_integration`.
      - [ ] Optional later: on-demand "check payment status" button; email-on-push toggle.
- [ ] **Deferred (documented, NOT now):** Quote/Project layer — priced lines + approval gate + WO
      lifecycle on the SAME primitive. Additive net-new tables → no live-data clock → wait until
      real project workflow shapes the approval state machine.

Tax: non-issue (Oregon, no sales tax) — MB sends pre-tax line totals, IN handles the receipt.

---

## Security & infrastructure (from the session-35 posture pass)

App-layer security is solid; these are the infra/operational gaps + things Mike wants to discuss.
Detail in memory `project_mb_session35_security`.

- [ ] **SSH / VM hardening (sudo-gated — pair with Mike).** Key-only SSH (disable password auth),
      `fail2ban`, disable root SSH login, OS patch cadence (`unattended-upgrades`). Biggest infra
      lever — contains "secrets live on the box → VM compromise = full exposure." Doesn't affect
      local app access. Claude can't do it (needs the sudo password); produce copy-paste commands.
- [x] **TLS / HTTPS — DECIDED (Jun 20, session 35).** MB stays proxy-terminated (it never does TLS
      itself); rationale + every hosting option written up for self-hosters in
      [`docs/deployment-tls.md`](docs/deployment-tls.md). For SCS: prod stays **plain HTTP on the
      trusted main LAN** (deliberate — no external surface); external access (if ever) = move to the
      VM LAN behind Cloudflare like MB2. No internal-cert project (more hassle than CF, which SCS
      reserves `scs-tech.net` for). Full decision + reasoning in CLAUDE.md "TLS / HTTPS — design
      decision" and memory `project_mb_tls_decision`. Don't re-litigate.
- [ ] **Write the public-readiness deploy recipes** — `docs/deployment-tls.md` is done (the "why" +
      options). Still nice-to-have: short copy-paste Caddy + nginx vhost examples in INSTALL.md for
      the non-CF paths, when the public-readiness pass happens.
- [ ] **Easy patch/update mechanism (Mike wants this).** A repeatable loop: `pip-audit` → bump →
      test on **Py3.12** → deploy. Prereq/companion: **align the dev venv to prod Python** — dev is
      3.9, prod is 3.12, so prod-pinned deps (gunicorn 26, Pillow 12…) can't install or be tested
      locally. Either get 3.12 on the Mac + rebuild the venv, or standardize on validating on prod's env.
- [ ] **Inbound-attachment malware scan (ClamAV)** — deferred ceiling from the attachment review;
      force-download already protects the app, this is defense-in-depth for the tech's machine.
- Done this session: admin user-delete; `.env` 600 + `protected/`/`backups/` 750; runtime CVE dep
  upgrades (Pillow 12.2 / requests 2.33 / cryptography 48.0.1).

## Real DB backup — ✅ DONE (Jun 22 2026)

> Root cause of the "broken backup": the old `pg_dump` job dumped an **empty PostgreSQL** DB —
> production actually runs on **SQLite**. Fixed end-to-end:
> - `scripts/mb_backup.sh` — consistent SQLite snapshot (Python online-backup, WAL on) + `.env` +
>   `protected/` + `media/` → dated gzipped tarball, **fail-loud** (integrity + size checks).
> - Off-site to **Backblaze B2** (`scs-mb-backups`) via bundled rclone, **Object Lock governance 30d**
>   (immutable) + lifecycle auto-prune (~36d); 14 local copies kept.
> - Restore-tested; nightly `murphys-bench-backup.timer` repointed (old `backup_db.sh` delegates to it).
> - Restore needs the tarball **and** `FIELD_ENCRYPTION_KEY` (Bitwarden). See docs/bookstack/05.

---

## Project Status Summary

✅ **COMPLETED**:
- Database schema fully designed and documented
- Django project initialized with all dependencies
- 14 data models created with proper relationships
- Migrations created and applied (SQLite, dev and production)
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

- ✅ **Custom Fields & Forms** (#4)
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

- ✅ **Multi-Factor Authentication** (#15)
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

---

#### Batch 9 — Mileage Native Form + Onsite Mileage Auto-Calculate

- ✅ **Mileage native form** — create/edit views, no admin required; tech auto-assigned as technician
- ✅ **WorkOrder.service_type** — In-Shop / Onsite / Remote; shown on WO detail and form
- ✅ **Mileage.trip_type** — One-Way / Round Trip stored with each entry
- ✅ **Google Maps auto-calculate** — server-side Distance Matrix proxy (`/mileage/calculate/`); API key never sent to browser; shop address + client address pre-filled from SiteSettings and Client record
- ✅ **+ Mileage button** — appears on WO detail when service_type == Onsite; launches pre-populated form with Calculate button
- ✅ **SiteSettings**: google_maps_api_key + shop_address fields; managed in admin under "Google Maps / Mileage" section
- ✅ **Backup token print** — Print button on backup tokens page (browser print with clean layout)

---

### Remaining Phase 1 Items

---

#### Batch 10 — Legacy App Gap Closure (Pre-Deployment) ✅

*Identified by full audit of legacy PHP app (session 7). Built in session 8.*

##### ✅ Priority 1 — Repair Report, Company Info, Quick Labor

- ✅ **Repair Report** (`/work-orders/<id>/print/`) — standalone print-optimized page, `@media print` CSS, logo + company header, client + device, repair type tags, Work Performed grouped by category, Resolution Summary, customer-visible notes. "🖨 Report" button in WO detail toolbar (opens new tab).

- ✅ **Company Info in SiteSettings** — `company_name`, `company_address`, `company_phone`, `company_email`, `company_logo` (ImageField). Used in Repair Report header.

- ✅ **Quick Labor / Work Performed** — `QuickLaborItem` model (label, category, print_description, is_active, sort_order) + `WorkPerformed` model (work_order FK, labor_item FK, logged_by, logged_at). Categorized HTMX one-click buttons on WO detail → logs entry. Grouped tags display on WO detail. Repair Report "Work Performed" section lists by category with print_description.

##### ✅ Priority 2 — Credentials, Client Type, Multiple Phones, Contact Enhancements

- ✅ **Credentials on Work Order** — `device_username`, `device_password`, `device_pin` fields on WorkOrder. HTMX inline card on WO detail; password masked with blur-sm + JS click-to-reveal. Not on Repair Report.

- ✅ **Client Type (Residential / Business)** — `client_type` field on Client. Color-coded badge on client list (Type column) and client detail header. `client_type` field in client create/edit form.

- ✅ **Multiple Phone Numbers per Contact** — `ContactPhone` model (contact FK, number, phone_type: cell/home/work/other). Alpine.js dynamic rows for add/remove on client detail inline forms. Registered in admin.

- ✅ **Contact enhancements** — `notes` TextField and `receives_email` BooleanField on Contact. Display on contact card. Full inline add/edit/delete UI on client detail using Alpine.js.

- ⏭ **Invoice Ninja Ref #** — deferred to Phase 2 API bridge. Will be driven by Invoice Ninja API capabilities once researched.

##### ✅ Priority 3 — Native Settings UI

- ✅ **Native Settings Panel** (`/settings/`) — six-tab page: Company, Outbound Email, Inbound Email, Attachments, Security, Mileage. Each tab is its own POST form with per-section save. Admin/can_manage_settings only (PermissionDenied guard). Success message flash. Settings link in nav bar (admin-only). Company tab supports logo upload.

---

#### Batch 11 — Foundational Client-Centric Rebuild

*Identified by full legacy app audit (session 9, June 8 2026). Full spec in `docs/batch-11-plan.md`.*

##### Priority 1 — Device Model + Client Hub ✅

- ✅ **Device model additions** — `os`, `os_version`, `condition_at_intake` (CharField), `assigned_contact` (FK to Contact, null/blank). Migration 0019. Form: assigned_contact queryset filtered to client's contacts; "Save & Create Work Order →" button. Removed Device from top-level nav.

- ✅ **WorkOrder — Contact association** — `contact` FK (nullable) added to WorkOrder. Migration 0020. Shown as Contact column in WO History. Settable on WO create/edit. Pre-filled from device's assigned_contact on "Save & Create WO."

- ✅ **Client detail as hub** — single-column layout; Account Info → Contacts → Devices → WO History. Per-contact Edit | +WO | Set Primary | Delete. Phone label field (ContactPhone.label, migration 0021). ContactSetPrimaryView. Devices table with OS + assigned contact + View/+WO per row.

- ✅ **Client edit — deactivate + delete** — Status section with explanatory text. Danger Zone (collapsed Alpine accordion): blocked with WO count message when WOs exist; type-to-confirm delete when clear.

##### Priority 2 — WO Detail + Print ✅

- ✅ **WO detail — unified action toolbar** — black bar: View Client | Edit Client | Edit Device | Edit WO | WO History | 🖨 Repair Report | Claim Ticket | inline Status dropdown.

- ✅ **WO detail — content additions** — Client info card + Device info card (serial, OS, version, condition). Days Open counter. Completed Date in header. Invoice Ninja Ref # field (migration 0022). Work Performed: bold label + print_description + timestamp. Checklist collapsed by default. Credential Notes field.

- ✅ **Repair Report / Claim Ticket** — OS/version/condition in device section. Note timestamps + author. Technician + Client signature lines. Footer (Company • WO# • Date). `?type=claim` switches title.

##### Priority 3 — Native Settings UI Expansion

- ✅ **Settings: Repair Types** — RepairTypeCategory model (migration 0023). Native CRUD: collapsible category sections with counts, ▲/▼ reorder, inline edit per type, delete category (orphans types), add type per category, uncategorised bucket.

- ✅ **Settings: Canned Responses** — two Note Streams (Customer Notes / Tech Notes Internal), each with user-defined reorderable categories. Per-response: stream, category, label, body text. CRUD. Canned response picker on WO detail note forms.

- ✅ **Settings: Quick Labor** — native CRUD UI (currently Django admin only): grouped by category (Software/Hardware/Data/Maintenance/General), add/edit/delete per item (label, category, print description).

- ✅ **Settings: Checklist Items** — model change: flat item bank scoped by device type (remove repair-type association). `ChecklistItem`: name + device_types (multi-select). WO checklist filtered by device type. Native UI: flat list, per-item device type tags, add/retire. Migration + data migration required.

- ✅ **Settings: Status Colors + Site Colors** — per-status hex color fields + site palette (nav bg, accent). Stored in SiteSettings; rendered as CSS variables in base.html. Status badges on WO list/detail/dashboard use CSS variable classes.

- ✅ **Settings: Company Info additions** — split `company_address` → `company_address_line1` + `company_address_line2`; split Client `address_street` → `address_line1` + `address_line2`. Report Header Preview in Settings › Company. Migration with data migration.

- ✅ **Settings: Display Settings** — browser-local UI preferences (localStorage, no server round-trip). Content font size, nav font size, table density. Applied via inline script in `<head>` as data attributes before first paint. Reset to Defaults.

---

#### Session 13 — Cross-Visibility + Misc Fixes ✅
- Cross-visibility panels: open tickets on WO detail, open WOs on ticket detail
- WO toolbar: linked ticket as purple pill (← TKT-XXXXX)
- Converted tickets visible in sidebar/dashboard until resolved/closed
- History tab removed from ticket detail
- Sidebar: last reply/note preview instead of subject
- Mileage Calculate: CSRF fix for production
- Google Maps API confirmed working from production server

#### Session 14 — Credential Encryption ✅
- `WorkOrder.device_username`, `device_password`, `device_pin`, `credential_notes` — AES-256 encrypted at rest
- `SiteSettings.email_password`, `inbound_password` — AES-256 encrypted at rest
- Package: `django-encrypted-model-fields==0.6.5`; `FIELD_ENCRYPTION_KEY` from env
- Migration 0031 applied locally and **deployed to production** (session 15)

#### Session 15 — Visual Polish ✅
- Color-coded dashboard metric tiles (Blue=active, Yellow=waiting, Red=overdue, Green=complete)
- SVG icons replacing emoji via `{% icon %}` templatetag (`core/templatetags/mb_icons.py`)
- Device type icon grid replacing dropdown on device form (Alpine.js, 7 types)
- Migration 0032: emoji → icon name strings in DashboardTile
- Production deployed: migrations 0031 + 0032, FIELD_ENCRYPTION_KEY set, key in Bitwarden

---

#### ✅ Session 16 — Invoice Model (COMPLETE)
- Invoice model: OneToOne on WorkOrder, billing_status enum, amount, dates, payment_method, notes
- Signal: auto-creates Invoice on WorkOrder creation
- Migration 0033: CreateModel + backfill RunPython — applied to production
- WorkOrderBillingUpdateView: quick-action + full edit, returns billing_card.html partial
- billing_card.html: display/edit toggle via Alpine.js, HTMX outerHTML swap
- WO detail: billing card in right column (between Update WO and Device Credentials)
- Client detail: outstanding balance badge on Work Order History header
- URL: /work-orders/<pk>/billing/ → wo_billing_update

---

- [x] **CSV export for Invoice records** — `InvoiceExportView` at `/clients/<pk>/invoices.csv`, optional `?status=` filter, CSV button on client detail

- [~] **Testing suite** — STARTED (session 27): `pytest.ini` + `core/tests.py` spine suite
  (10 tests) covering the four bug fixes and the reset command. Run `venv/bin/python -m pytest`.
  - [ ] Broaden beyond the spine: ticket→WO convert/lifecycle, email routing, queue filters,
        permission denials, form validation. (No fixed coverage % target — target the spine
        and the money/data paths first.)
- [ ] ~~**Deployment** (internal network)~~ — ✅ COMPLETE (session 12, 10.58.58.82)

---

#### Session 27 — Stabilization (COMPLETE) ✅

Full code review → shifted from feature-building to hardening. All shipped + deployed:
- "How We Work On This Project" guardrail added to top of CLAUDE.md
- Four data-integrity bugs fixed (delete guard, nullable serial/migration 0045, number-collision
  retry, fail-loud logging) — each test-covered
- First test harness (pytest, 10 tests)
- `reset_operational_data` management command (clean OSTicket-cutover wipe; dry-run by default)
- Production safety guards (DEBUG default False; refuse default secret/encryption keys)
- Nightly DB backup (`scripts/backup_db.sh`) + systemd timer
- systemd timers for `fetch_inbound_email` (2 min) and `check_sla_overdue` (15 min) — inbound
  email was unscheduled/dormant; now installed, active, and verified connecting to IMAP
- Conversation-view polish: client replies colored green with contact name; quoted email history
  folded into a collapsible blockquote; reply header shows who replied (not "Customer Visible")
- Email rendering fixes (readable header, inline downscaled logo) + Email Branding settings section
- Tech experience: role-based nav/dashboard, visibility scoping (techs see own + unclaimed), and
  3-level ticket escalation (Claim/Transfer/Escalate, no-orphan handoff, dashboard/list surfacing).
  Migrations 0046–0048.
- **Open follow-ups:** retire `TechSkill` (superseded by levels), decide whether to level Work
  Orders, finish the "audit every tech-facing list for correct scoping" pass.
- **Action left for Mike:** point inbound mailbox from `testing@` to the real support inbox; set
  user levels (Settings → Users) so escalation has somewhere to land.

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

### Credentials & Security

- [ ] **Device-level credentials** — `password` field on Device model (AES-256 encrypted, masked display + eye icon reveal). Who can view: Administrators always; Technicians only if role allows.

- [x] **Org-level credentials vault** — `OrgCredential` + `CredentialAccessLog` models (migration 0034). Settings → Credentials tab. AES-256 encrypted username/password/notes. HTMX eye-reveal logs every access. Admin-only flag.

---

### Settings UI Expansion

- [x] **Email Template Manager** — Settings → Email Templates tab. Editable subject/body (monospace), active toggle, variable reference panel, last-updated timestamp. Auto-creates inactive defaults on first visit.

- [ ] **Status Management UI** — Native CRUD for Ticket and WO statuses
  - Separate sections for Ticket Statuses and WO Statuses
  - Core statuses locked (cannot delete); custom statuses add/edit/delete with color picker
  - Suggested library per entity type (e.g., WOs: Diagnosed, Awaiting Parts, Quality Check, Ready for Pickup)
  - Drag-to-reorder

- [ ] **Data Management** — Import, Export, Deleted Data recovery, Reset
  - Import wizard: Choose Type → Upload CSV → Map Columns → Preview → Import (Customers, Devices, Tickets, WOs)
  - Export: per-entity CSV + bulk ZIP; audit log of who exported what/when
  - Deleted Data: soft-delete recovery view — restore or permanently delete
  - Reset: admin-only, checkbox per entity type, confirmation phrase, requires backup download first

---

### Reporting Expansion

- [x] **Financial reporting** — Billing Summary section on Reports page: Invoiced/Collected/Outstanding metric cards + outstanding-by-client table. CSV at `/reports/csv/billing/`.

- [x] **Technician performance reports** — Reports page: WOs in period, completion %, avg resolution hours, open WOs. CSV at `/reports/csv/tech_perf/`.

- [x] **Team workload widget** — Dashboard (admin only): open WOs + tickets per tech, sorted by load, counts link to filtered lists.

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

## Candidate features — considered, UNDECIDED (from the publish/viability discussion, Jun 2026)

Surfaced while assessing whether to open-source MB (see README "Today, and where it could go"
+ memory `project_web_app_vision`). **None of these are committed or rejected** — they're
parked here so they're not lost, and so the README's "tell me what you'd need" invitation has
a place to collect real demand. Decide by what SCS actually needs and what (if any) other
shops ask for — not by guessing.

- **SMS / text notifications** ("device ready") — repair-shop staple (PCRT/RepairDesk have it); email-only today.
- **Parts inventory & ordering** — stock levels, low-stock alerts, reorder; parts are billable line items today.
- **Point of sale (POS)** — counter checkout / accessory sales.
- **Additional invoicing/accounting targets** — QuickBooks Online & others (IN is wired first because SCS uses it; explicitly NOT IN-only forever).
- **Deeper documentation** — beyond the current KB + credential vaults (pairs with BookStack today).
- **Customer self-service portal**, **RMM integration**, **SSL/domain expiry alerts**, **recurring-contract (MRR) billing**.
- **Multi-shop capability** — PCRT supported it; open question, not foreclosed.

---

## Self-sufficiency & release ops (Mike's bar — FIRM, Jun 23 2026)

Mike must be able to **install, update, backup, and export data WITHOUT Claude** (bus-factor). Each = one fail-loud command + a short doc line, no code-reading. Memory: `project_mb_publish_ops_selfsufficiency`.

- [x] **`scripts/update.sh`** — backup-first → pull → pip → migrate → collectstatic → restart → health-poll + rollback hint. Verified staging+prod (Jun 23). Run: `cd /opt/murphys-bench && scripts/update.sh`.
- [x] **CI gate** — `.github/workflows/ci.yml` (pytest + `manage.py check` on every push). See the remediation section.
- [x] **`scripts/restore.sh`** — ✅ DONE Jun 24 (session 43, commit `83052a4`). One-command restore from an `mb_backup.sh` tarball: integrity-checks the bundled SQLite snapshot BEFORE touching anything live → saves current db/protected/media/.env to `backups/pre-restore-<ts>/` (restore is itself reversible) → stops service → swaps db (drops stale WAL/SHM) + protected/ + media/ → restarts → health-polls. Keeps the live `.env` by default (same-box rollback keeps current secrets); `--with-env` for fresh-box DR; `RESTORE_YES=1` skips the prompt (update.sh rollback path). Loud FIELD_ENCRYPTION_KEY reminder. **Drill-verified on mb-test** (backup → mutate → restore → mutation gone, counts back to baseline, app healthy). Present on prod/MB2/mb-test. Run: `cd /opt/murphys-bench && scripts/restore.sh backups/mb-backup-<ts>.tar.gz`. Completes the backup pillar.
- [x] **Data export** — ✅ DONE Jun 24 (session 44, commit `a145d05`). `manage.py export_data` → one neutral CSV per table (csv/) + the protected/ & media/ file trees, bundled into a verified `mb-export-<ts>.tar.gz`. Portable/no-lock-in (readable in any spreadsheet, importable elsewhere) — distinct from mb_backup.sh's restore-into-MB snapshot. Encrypted fields auto-detected (Encrypted* class) and written `***REDACTED***` by default; `--include-secrets` writes decrypted (loud warning); `--no-media` for CSVs only; FKs export as `<name>_id`; README.txt with row counts; logs/audit/lock tables skipped; fail-loud cleanup. 2 tests lock the redaction contract (suite →109). Run: `cd /opt/murphys-bench && venv/bin/python manage.py export_data`. Verified on mb-test (41 tables/258 rows, secret redaction confirmed). Present on prod/MB2/mb-test.
- [ ] **`setup.sh`** — one-line installer scripting INSTALL.md (Debian/Ubuntu apt family).
- [ ] **Tagged releases + AUTO-rollback (FIRM):** semver tags (current/target version); failed update auto-reverts **code AND DB** to last stable (reuse restore.sh). CI-green → tag = rollback target.
- [ ] **In-app admin 'Update'** — wraps update.sh out-of-band (can't restart gunicorn from its own request); current-vs-available version; admin-only/CSRF. Mainly for single-instance adopters; SCS stays staging-first.
- **Docker:** deferred until demand validated (would fork the systemd model); setup.sh is the easy-install answer for now.

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
