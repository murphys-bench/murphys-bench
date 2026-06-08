# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working:

- Django 4.2 app, 32 models, 16 migrations applied
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
- **Batch 7**: Custom Fields (CustomField + CustomFieldChoice + CustomFieldValue, EAV storage, scoped to HelpTopic or RepairType, all field types: text/textarea/select/checkbox/date, renders on ticket + WO create/edit/detail)
- **Batch 8**: MFA via django-two-factor-auth — TOTP enrollment, Tailwind-styled two_factor template overrides, MFAEnforcementMiddleware, backup tokens (admin only, printable), user management panel at /users/ with MFA status + admin reset
- **Batch 9**: Mileage native create/edit form, WorkOrder.service_type (In-Shop/Onsite/Remote), Mileage.trip_type (One-Way/Round Trip), Google Maps Distance Matrix auto-calculate (server-side proxy at /mileage/calculate/), + Mileage button on WO detail for onsite jobs, SiteSettings: google_maps_api_key + shop_address

---

## Known gotchas (read before touching these areas):

- **Audit log in templates**: Never use `entry.changes_dict.items` in a Django template — the dict can have an `'items'` key that shadows the `.items()` method. Use `_audit_entries(obj)` from views.py which pre-processes entries into `[{entry, changes: [{field, old, new}]}]`.
- **`?assigned_to=me`**: Handled in both TicketListView and WorkOrderListView. Admins get all items (filter skipped); techs get only their own. Don't break this when adding new list filters.
- **Alpine.js**: Loaded via CDN with `defer` in base.html. Required for sidebar accordion. If you add new Alpine components to HTMX-swapped content, they may need `Alpine.initTree(el)` in an htmx:afterSwap handler.
- **Queue filter_criteria JSON**: The `assigned_to: null` key (explicit null, not absent key) means "unassigned only" in `_apply_queue_filters()`. Keep that distinction when extending queue filter support.
- **two_factor template overrides**: Live in root `templates/two_factor/` (listed in DIRS), NOT in `core/templates/two_factor/` — DIRS takes priority over APP_DIRS. The core/ location is dead and was deleted.
- **`_is_admin` + anonymous users**: Never call `_is_admin(request.user)` before checking `request.user.is_authenticated` — AnonymousUser has no `has_perm_flag` method. Pattern: `if request.user.is_authenticated and not _is_admin(request.user): raise PermissionDenied`.
- **Google Maps mileage**: Tested working in architecture. Fails from localhost (no outbound internet in dev). Needs verification on internal server — noted in TODO.md deployment checklist.

---

## What's next:

**Batch 10 — Legacy App Gap Closure** (identified in session 7 via full audit of legacy PHP app at 10.58.58.235).

### Priority 1 — Build first (repair report is the critical missing piece)
1. **Company Info** — add to SiteSettings: `company_name`, `company_address`, `company_phone`, `company_email`, `company_logo` (ImageField). Needed for report header.
2. **Quick Labor / Work Performed** — `QuickLaborItem` model (label, category, print_description, is_active) + `WorkPerformed` model (work_order FK, labor_item FK, logged_by, logged_at). Categorized HTMX buttons on WO detail → logs entry. Shows as grouped tags on WO detail. Appears on repair report.
3. **Repair Report** (`/work-orders/<id>/print/`) — print-optimized page: logo + company header, client + device, problem/task + repair type tags, Work Performed, Resolution Summary, customer notes. `@media print` CSS. "Print Report" button on WO detail.

### Priority 2 — Data model changes (clean up before deployment)
4. **Credentials on WO** — add `device_username`, `device_password`, `device_pin` to WorkOrder. Display on WO detail (password masked, click to reveal). Never on report.
5. **Client Type** — add `client_type` (residential/business) to Client. Badge on list + detail. Residential/Business filter buttons on client list.
6. **Multiple phones per Contact** — new `ContactPhone` model (contact FK, number, phone_type: cell/home/work/other). HTMX inline add/remove on client detail.
7. **Contact enhancements** — add `notes` TextField and `receives_email` BooleanField (default True) to Contact.
8. **Invoice Ninja Ref #** — add `invoice_ref` CharField (blank=True) to WorkOrder. Show on WO detail + client WO history table.

### Priority 3 — Native Settings UI
9. **`/settings/` panel** — side-nav with: Company Info, Email Settings, Repair Types, Canned Responses, Quick Labor, Checklist Items, Colors (status + site palette), Display Settings (localStorage). Admin-only. Link from nav.

### After Batch 10
- **Deployment** to internal network (10.58.58.x) — PostgreSQL, HTTPS, static files, cron
- **Testing suite** (deferred — write after real-world use surfaces edge cases)

---

## General rules for this session:

- All views use `LoginRequiredMixin`
- HTMX is loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js is loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models
- Update `TODO.md` to mark completed items ✅ when done
- Commit and push when complete
