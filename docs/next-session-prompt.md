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

**Deployment** is the primary remaining task. The app is feature-complete for Phase 1.

1. **Get on the internal network** (10.58.58.x)
   - Copy repo to server, set up venv, apply migrations
   - Switch to PostgreSQL (update DB_ENGINE in .env)
   - Configure ALLOWED_HOSTS, HTTPS, static files
   - Set up cron for `check_sla_overdue` and `fetch_inbound_email`
   - Verify Google Maps mileage calculate works from the server

2. **Testing suite** (deferred — write after real-world use surfaces actual edge cases)

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
