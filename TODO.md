# Murphy's Bench Development Roadmap

**Last Updated**: June 4, 2026  
**Current Phase**: Phase 1 - SCS Internal (Views complete, Dashboard + Forms next)

## Project Status Summary

✅ **COMPLETED THIS SESSION**:
- Device list + detail views (search, type filter, repair history)
- Mileage log view (month filter, running total)
- Authentication — login/logout pages, all views protected
- Nav bar shows logged-in username, logout link, admin link (staff only)
- GitHub repository connected and up to date

✅ **COMPLETED PREVIOUSLY**:
- Database schema fully designed and documented
- Django project initialized with all dependencies
- 13 data models created with proper relationships
- Database migrations created and applied (SQLite ready, PostgreSQL configured)
- Django settings configured for dev/production
- Email integration prepared for ticket ingestion
- Logging and static file handling configured
- Git repository with clean commit history — pushed to GitHub
- Django admin customized for all 13 models (search, filters, inlines)
- Base template with navigation (Tailwind CSS)
- Work order list view (search, filter by status/technician, pagination)
- Work order detail view (notes, items, quick actions)
- Client list view (search, active/inactive filter)
- Client detail view (contacts, devices, work order history)

⬜ **NEXT — IN ORDER**:
1. **Dashboard** (home page, landing after login)
2. **Native create/edit forms** (work orders, clients, devices — no admin required)
3. **HTMX** (checklist toggles, inline notes)
4. **Testing suite**
5. **Deployment** (internal network)

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
# Visit http://localhost:8000 — redirects to login
# Login: admin / password123 (local dev only)
```

**What's working now**:
- `/` → redirects to login (dashboard not built yet)
- `/accounts/login/` — login page
- `/work-orders/` — work order list
- `/work-orders/<id>/` — work order detail
- `/clients/` — client list
- `/clients/<id>/` — client detail
- `/devices/` — device list
- `/devices/<id>/` — device detail
- `/mileage/` — mileage log
- `/admin/` — Django admin (all data entry done here for now)

**Build next — Dashboard**:
- Add `DashboardView` to `core/views.py`
- Add `path('', DashboardView.as_view(), name='dashboard')` to `core/urls.py`
- Create `core/templates/core/dashboard.html`
- Show: open WO counts by status, recent work orders, quick links
- Use `WorkOrder.objects.values('status').annotate(count=Count('status'))` for counts
- Reference the mockup at `~/Documents/Claude/dashboard-mockup.html` for layout ideas

**Key files**:
- `core/views.py` — all views
- `core/urls.py` — URL routing
- `core/models.py` — all data models
- `core/admin.py` — admin customization
- `core/templates/core/` — all HTML templates
- `accounts/views.py` — login/logout
- `murphys_bench/settings.py` — Django settings

---

## Notes

- Visual polish is intentionally deferred — functionality first
- All views require login (LoginRequiredMixin on everything)
- Data entry still goes through /admin/ until native forms are built
- Tailwind CSS loaded via CDN (no build step needed)
- SQLite in dev, PostgreSQL planned for production
