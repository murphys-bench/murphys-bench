# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 8):

- Django 4.2 app, 34 models, 18 migrations applied
- Full CRUD views for work orders, clients, devices, mileage, contacts
- HTMX inline notes, checklist toggling, inline ticket replies, Quick Labor logging, device credentials
- **Batch 1–9**: See CLAUDE.md for full list
- **Batch 10 — Complete (session 8)**:
  - Repair Report at `/work-orders/<id>/print/` — standalone print page with logo, client, work performed, resolution, customer notes
  - Company Info fields on SiteSettings (name, address, phone, email, logo)
  - Quick Labor (QuickLaborItem + WorkPerformed models, HTMX one-click logging, grouped display on WO detail and print)
  - Device Credentials on WO (device_username/password/pin, masked display, HTMX inline save)
  - Client Type (Residential/Business) — badge on list + detail header, field on form
  - Multiple phones per Contact (ContactPhone model, Alpine.js dynamic rows on client detail inline form)
  - Contact notes + receives_email fields, full inline add/edit/delete on client detail
  - Native Settings UI at `/settings/` — 6 tabs: Company, Outbound Email, Inbound Email, Attachments, Security, Mileage
  - All admin panel links replaced with native app URLs; Django admin is now staff config/reference only

---

## Known gotchas (read before touching these areas):

- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py which pre-processes to `[{entry, changes: [{field, old, new}]}]`.
- **`?assigned_to=me`**: Handled in TicketListView and WorkOrderListView. Admins get all; techs get own.
- **Alpine.js**: CDN with `defer` in base.html. HTMX-swapped content may need `Alpine.initTree(el)` in htmx:afterSwap if it uses Alpine.
- **Queue filter_criteria JSON**: `assigned_to: null` (explicit null) means "unassigned only" in `_apply_queue_filters()`.
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`.
- **`_is_admin` + anonymous users**: Check `request.user.is_authenticated` before calling — AnonymousUser has no `has_perm_flag`.
- **Google Maps mileage**: Works in architecture but fails from localhost (no outbound internet in dev). Verify after deploying to internal server.
- **DeviceCreateView ?next=**: Pass `next` in both GET (for cancel link) and POST (hidden field in device_form.html). Used when launching "New Device" from a client detail page so the user returns there after save.
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False` — the field is a CharField.
- **ContactPhone phones in Alpine edit form**: Pre-populated via Django template loop into Alpine `phones` array. Phones saved as `phone_number[]` / `phone_type[]` POST arrays via `_save_contact_phones()`.

---

## What's next:

**Deployment** is the primary remaining Phase 1 milestone.

### Pre-deployment checklist
- [ ] Populate SiteSettings with real company info via `/settings/`
- [ ] Add QuickLaborItems via Django admin (Batch 10 native UI deferred)
- [ ] Set up PostgreSQL database for production
- [ ] Configure HTTPS (self-signed cert or internal CA)
- [ ] Set ALLOWED_HOSTS, SECRET_KEY, DEBUG=False in .env
- [ ] Configure static files (collectstatic + web server)
- [ ] Set up cron jobs: `check_sla_overdue` (every 15 min), `fetch_inbound_email` (every 2–5 min)
- [ ] Test Google Maps mileage on internal server (outbound HTTPS required)
- [ ] Deploy to internal network (10.58.58.x)
- [ ] Run on real data; collect feedback from SCS techs

### Phase 2 (after deployment + real-world use)
- Invoice Ninja API research + bridge
- Email OAuth2 (Gmail / Office 365)
- Departments, Teams, Auto-routing
- Customer self-service portal
- Ticket merging
- REST API (for Clover / Taskbar Utility App)

---

## General rules for this session:

- All views use `LoginRequiredMixin`
- HTMX loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models
- Update `TODO.md` to mark completed items ✅ when done
- Commit and push when complete
