# Murphy's Bench Development Roadmap

**Last Updated**: June 4, 2026  
**Current Phase**: Phase 1 - SCS Internal (Data layer complete, HTMX + Ticket views next)

## Project Status Summary

✅ **COMPLETED**:
- Database schema fully designed and documented
- Django project initialized with all dependencies
- 14 data models created with proper relationships
- Database migrations created and applied (SQLite ready, PostgreSQL configured)
- Django settings configured for dev/production
- Git repository with clean commit history — pushed to GitHub (private)
- Django admin customized for all 14 models (search, filters, inlines)
- Base template with navigation (Tailwind CSS, dark nav bar)
- Authentication — login/logout, all views protected with LoginRequiredMixin
- Dashboard — stats, open work orders, recently closed, quick action buttons
- Work order list + detail views
- Work order create/edit native forms
- Client list + detail views
- Client create/edit native forms
- Device list + detail views
- Device create/edit native forms
- Mileage log view (month filter, running total)
- Ticketing system design finalized and documented
- TicketReply model added (threaded conversation)
- Ticket statuses expanded (new, open, in_progress, waiting_on_customer, resolved, closed, converted)
- Migration 0002 applied cleanly

⬜ **NEXT — IN ORDER**:
1. ✅ **Ticketing context locked in** — full-featured ticketing with threaded conversation, ticket→work order conversion, trend analysis
2. ✅ **HTMX inline notes** — notes added to work orders without page reload
3. **HTMX checklist toggling** (mark items complete without page reload)
4. **Ticket views** (list, detail, reply form, convert-to-work-order)
5. **Mileage create form** (native form, no admin required)
6. **Testing suite**
7. **Deployment** (internal network)

---

## Phase 1: SCS Internal (Current Focus)

### Foundation & Setup
- [x] **Finalize database schema** ✅ COMPLETE
  - [x] Document entity relationships (ER diagram)
  - [x] Define all fields and constraints
  - [x] Created schema documentation (docs/database-schema.md)
  - [x] 13 models with optimized indexes

- [x] **Initialize Django project structure** ✅ COMPLETE
  - [x] Created Django 4.2.30 project
  - [x] Created core app (business logic)
  - [x] Created accounts app (auth)
  - [x] Set up settings.py (dev/prod, environment variables)
  - [x] PostgreSQL + SQLite support
  - [x] Email configuration
  - [x] Logging setup

- [x] **Create models** ✅ COMPLETE
  - [x] User model (extended Django User with roles)
  - [x] Client, Contact, Device models
  - [x] Ticket model (NEW - initial service request)
  - [x] WorkOrder model (repair job)
  - [x] WorkOrderNote (customer visible + internal)
  - [x] WorkOrderItem (checklist, parts, time)
  - [x] Mileage model
  - [x] RepairType, Checklist, ChecklistItem models
  - [x] CannedResponse model
  - [x] Migrations created and applied
  - [x] Database created with all tables

### Admin Panel
- [x] **Django admin customization** ✅ COMPLETE
  - [x] All 13 models registered with custom list displays
  - [x] Search fields configured per model
  - [x] Filters configured (status, priority, assigned_to, etc.)
  - [x] Inline editing (Contacts with Client, Notes/Items with WorkOrder, ChecklistItems with Checklist)
  - [x] Timestamps read-only and collapsible
  - [x] Auto-generate work order and ticket numbers on save

### Authentication
- [x] **Login/logout pages** ✅ COMPLETE
  - [x] Login page with error messaging (accounts/login/)
  - [x] Logout redirects to login page
  - [x] All views protected with LoginRequiredMixin
  - [x] Unauthenticated users redirected to login
  - [x] Nav shows username, logout link, admin link (staff only)
  - [x] LOGIN_URL, LOGIN_REDIRECT_URL set in settings

### UI & Frontend
- [x] **Base template & navigation** ✅ COMPLETE
  - [x] base.html with dark nav bar
  - [x] Nav links: Work Orders, Clients, Devices, Mileage, Admin
  - [x] Active state highlighting per page
  - [x] Tailwind CSS via CDN
  - [x] User info and logout in nav

- [ ] **Styling polish** *(deferred — functionality first)*
  - [ ] Visual design refinement
  - [ ] Mobile/tablet responsive tweaks
  - [ ] Logo and branding

- [ ] **HTMX interactivity**
  - [ ] Checklist item toggle (no page reload)
  - [ ] Quick note addition inline
  - [ ] Status update inline
  - [ ] Estimated: 2-3 hours

### Core Workflows (MVP)
- [x] **Work order list view** ✅ COMPLETE
  - [x] Display all work orders with status, client, technician
  - [x] Filter by status, technician
  - [x] Search by client name / work order number
  - [x] Pagination (25 per page)
  - [x] Color-coded status and priority badges
  - [x] Clickable rows → detail view

- [x] **Work order detail view** ✅ COMPLETE
  - [x] All work order fields displayed
  - [x] Associated device and client shown
  - [x] Customer-visible notes section
  - [x] Internal notes section (yellow, locked indicator)
  - [x] Activity notes list
  - [x] Items & checklist sidebar
  - [x] Quick actions (Edit, Add Note)

- [ ] **Work order create/edit forms**
  - [ ] Create new work order (native form, not admin)
  - [ ] Edit existing work order
  - [ ] Assign technician
  - [ ] Update status
  - [ ] Add devices
  - [ ] Estimated: 3 hours

- [x] **Client list view** ✅ COMPLETE
  - [x] List all clients with email, phone, city, status
  - [x] Search by name, email, phone
  - [x] Active/inactive filter
  - [x] Clickable rows → detail view

- [x] **Client detail view** ✅ COMPLETE
  - [x] Contact info sidebar
  - [x] Contacts list with primary indicator
  - [x] Device list with type, model, serial
  - [x] Work order history table
  - [x] Client notes

- [ ] **Client create/edit forms**
  - [ ] Create new client (native form, not admin)
  - [ ] Edit existing client
  - [ ] Add/edit contacts
  - [ ] Estimated: 2 hours

- [ ] **Dashboard (home page)** ← BUILD NEXT
  - [ ] Open work order count by status (New, In Progress, Completed)
  - [ ] Work orders assigned per technician
  - [ ] Recent activity (last 10 work orders updated)
  - [ ] Quick links: New Work Order, New Client
  - [ ] Set as default landing page after login (url: /)
  - [ ] Estimated: 1-2 hours

- [x] **Device list view** ✅ COMPLETE
  - [x] Search by name, serial, model, client
  - [x] Filter by device type
  - [x] Clickable rows → detail view

- [x] **Device detail view** ✅ COMPLETE
  - [x] Device info sidebar (manufacturer, model, serial, client)
  - [x] Repair history table
  - [x] Link back to client

- [ ] **Device create/edit forms**
  - [ ] Create new device linked to client
  - [ ] Edit existing device
  - [ ] Estimated: 1-2 hours

- [ ] **Ticket views** (full-featured ticketing system)
  - [ ] Ticket model extended with statuses (New, Open, In Progress, Waiting on Customer, Resolved, Closed)
  - [ ] TicketReply model for threaded conversation (customer-visible vs. internal)
  - [ ] Ticket list view (search, filter by status, pagination)
  - [ ] Ticket detail view (show ticket + threaded replies)
  - [ ] Inline reply form (add customer-visible or internal reply via form)
  - [ ] Convert-to-work-order button (creates WO, retains ticket reference)
  - [ ] Historical search (find tickets by client/device for trend analysis)
  - [ ] Estimated: 4-5 hours

- [ ] **Checklist functionality**
  - [ ] Display checklist items for work order (based on repair type)
  - [ ] Mark items complete with HTMX (no page reload)
  - [ ] Estimated: 2 hours

- [ ] **Notes (inline, no admin required)**
  - [ ] Add customer-visible notes from work order detail page
  - [ ] Add internal notes from work order detail page
  - [ ] Estimated: 1-2 hours

- [x] **Mileage log view** ✅ COMPLETE
  - [x] List all entries with date, technician, from/to, miles, purpose
  - [x] Filter by month
  - [x] Running total (filtered and overall)
  - [x] Link to associated work order

### Testing & Quality
- [ ] **Write tests**
  - [ ] Model tests (validation, relationships)
  - [ ] View tests (authentication, permissions, data display)
  - [ ] Form tests (validation, submission)
  - [ ] Aim for 70%+ code coverage
  - [ ] Estimated: 4-6 hours

- [ ] **Code quality**
  - [ ] Run black (code formatter)
  - [ ] Run flake8 (linter)
  - [ ] Run isort (import sorting)
  - [ ] Estimated: 1 hour

### Documentation
- [ ] **README.md**
  - [ ] Setup instructions (venv, dependencies, migrations)
  - [ ] How to run locally
  - [ ] How to run tests
  - [ ] Project structure explanation
  - [ ] Estimated: 1 hour

### Deployment & Operations (Internal Network)
- [ ] **Internal network readiness**
  - [ ] Configure for internal deployment
  - [ ] Set up HTTPS with self-signed certificate
  - [ ] Database backups strategy
  - [ ] Test on internal network (10.58.58.235 or equivalent)
  - [ ] Estimated: 2-3 hours

### Phase 1 Completion Criteria
- [ ] All SCS core workflows functional (ticketing, work orders, tracking)
- [ ] Deployed to internal network and in daily use
- [ ] Techs prefer it to legacy PHP app
- [ ] Tests passing, code quality good
- [ ] No critical bugs in production
- [ ] Email integration working (inbound tickets, outbound updates)
- [ ] Comprehensive self-hosting documentation

---

## Phase 2: Integrations & Polish (Future - Not Starting Yet)

- Invoice Ninja API bridge (send completed work orders → invoices)
- Email parsing improvements (better ticket extraction)
- Optional integrations (Slack notifications, etc.)
- Performance optimization based on real usage
- Visual design polish (branding, colors, mobile)

## Phase 3+: Multi-Tenant SaaS (Speculative - Only if Demand Emerges)

*Only reconsidered if multiple companies request it AND we decide to offer a hosted version.*

---

## Quick Start (Next Session)

```bash
cd ~/Documents/Claude/murphys-bench
source venv/bin/activate
python manage.py runserver
# Visit http://localhost:8000 — goes to dashboard (login required)
# Login: admin / password123 (local dev only)
```

**⚠️ Start next session by**:
1. Reading CLAUDE.md and docs/ticketing-design.md for full context
2. Proceed with HTMX inline notes (work order detail page)
3. Then ticket views

**Build next — HTMX Inline Notes**:
- Add a note form directly on `work_order_detail.html`
- POST to a new view `WorkOrderNoteCreateView` in `core/views.py`
- Add URL: `path('work-orders/<int:pk>/notes/add/', ..., name='work_order_note_add')`
- Use HTMX to submit and render the new note without page reload
- Add HTMX CDN script to `base.html`
- Note form fields: `note_type` (internal/customer_visible), `content`
- On success: return just the new note HTML fragment, prepend to notes list

**Key files**:
- `core/views.py` — all views
- `core/urls.py` — URL routing
- `core/models.py` — all 13 data models
- `core/forms.py` — WorkOrderForm, ClientForm, DeviceForm
- `core/admin.py` — admin customization
- `core/templates/core/` — all HTML templates
- `accounts/views.py` — login/logout
- `murphys_bench/settings.py` — Django settings (LOGIN_URL set)

---

## Notes

- Visual polish is intentionally deferred — functionality first
- All views require login (LoginRequiredMixin on everything)
- Data entry still goes through /admin/ until native forms are built
- Tailwind CSS loaded via CDN (no build step needed)
- SQLite in dev, PostgreSQL planned for production
