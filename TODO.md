# Murphy's Bench Development Roadmap

**Last Updated**: June 4, 2026  
**Current Phase**: Phase 1 - SCS Internal (Views & Forms In Progress)

## Project Status Summary

✅ **COMPLETED**:
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

⬜ **NEXT**:
- Authentication (login/logout pages)
- Dashboard (home page with key metrics)
- Native create/edit forms (work orders, clients, devices)
- HTMX for dynamic interactions (checklist toggles, quick notes)
- Testing suite

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

### UI & Frontend
- [x] **Base template & navigation** ✅ COMPLETE
  - [x] base.html with dark nav bar
  - [x] Nav links: Work Orders, Clients, Devices, Mileage, Admin
  - [x] Active state highlighting per page
  - [x] Tailwind CSS via CDN

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
  - [ ] Create new work order from client
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
  - [ ] Create new client
  - [ ] Edit existing client
  - [ ] Add/edit contacts
  - [ ] Estimated: 2 hours

- [ ] **Dashboard (home page)**
  - [ ] Open work order count by status (New, In Progress, Completed)
  - [ ] Work orders assigned per technician
  - [ ] Recent activity (last 10 work orders updated)
  - [ ] Quick links: New Work Order, New Client
  - [ ] Set as default landing page after login
  - [ ] Estimated: 1-2 hours

- [ ] **Device management**
  - [ ] Device list view
  - [ ] Device detail with repair history
  - [ ] Create/edit device
  - [ ] Estimated: 1-2 hours

- [ ] **Checklist functionality**
  - [ ] Display checklist items for work order (based on repair type)
  - [ ] Mark items complete with HTMX (no page reload)
  - [ ] Update work order completion status
  - [ ] Estimated: 2 hours

- [ ] **Notes (customer-facing vs. internal)**
  - [ ] Add customer-visible notes inline (no admin required)
  - [ ] Add internal technician notes inline
  - [ ] Edit/delete notes
  - [ ] Estimated: 1-2 hours

- [ ] **Mileage logging**
  - [ ] Quick mileage entry (date, from/to, miles, purpose)
  - [ ] Mileage list with summary totals
  - [ ] Edit/delete mileage entries
  - [ ] Estimated: 1.5 hours

### Authentication
- [ ] **Login/logout pages**
  - [ ] Login page (currently redirects to admin login)
  - [ ] Logout
  - [ ] Protect all views with LoginRequiredMixin
  - [ ] Redirect to login on unauthenticated access
  - [ ] Estimated: 1 hour

### Testing & Quality
- [ ] **Write tests**
  - [ ] Model tests (validation, relationships)
  - [ ] View tests (authentication, permissions, data display)
  - [ ] Form tests (validation, submission)
  - [ ] Service/utility function tests
  - [ ] Aim for 70%+ code coverage
  - [ ] Estimated: 4-6 hours

- [ ] **Code quality**
  - [ ] Run black (code formatter)
  - [ ] Run flake8 (linter)
  - [ ] Run isort (import sorting)
  - [ ] Clean up any warnings
  - [ ] Estimated: 1 hour

### Documentation
- [ ] **README.md**
  - [ ] Setup instructions (venv, dependencies, migrations)
  - [ ] How to run locally
  - [ ] How to run tests
  - [ ] Project structure explanation
  - [ ] Estimated: 1 hour

- [ ] **Workflow documentation** (in docs/workflows.md)
  - [ ] SCS-specific workflows
  - [ ] How to use each feature
  - [ ] Common tasks and their steps
  - [ ] Estimated: 1-2 hours

### Deployment & Operations (Internal Network)
- [ ] **Local deployment testing**
  - [ ] Create script to set up fresh dev database
  - [ ] Test on clean machine (documentation accuracy)
  - [ ] Estimated: 1 hour

- [ ] **Internal network readiness**
  - [ ] Configure for internal deployment (settings, logging)
  - [ ] Set up HTTPS with self-signed certificate
  - [ ] Database backups strategy (on-network)
  - [ ] Deployment documentation (how to set up on internal server)
  - [ ] Test on internal network (10.58.58.235 or equivalent)
  - [ ] Estimated: 2-3 hours

- [ ] **Network security configuration**
  - [ ] Firewall rules (internal network only)
  - [ ] No public internet exposure
  - [ ] Email integration (SMTP inbound/outbound)
  - [ ] Documentation: Security model for self-hosting
  - [ ] Estimated: 1-2 hours

### Iteration & Feedback
- [ ] **Deploy to SCS and gather feedback**
  - [ ] Techs use it for real work
  - [ ] Collect pain points and suggestions
  - [ ] Log bugs

- [ ] **Iterate on UI/UX**
  - [ ] Adjust layouts based on feedback
  - [ ] Add missing features identified in use
  - [ ] Performance optimization if needed
  - [ ] Estimated: Variable

### Phase 1 Completion Criteria
- [ ] All SCS core workflows functional (ticketing, work orders, tracking)
- [ ] Deployed to internal network and in daily use
- [ ] Techs prefer it to legacy PHP app
- [ ] Tests passing, code quality good
- [ ] No critical bugs in production
- [ ] Email integration working (inbound tickets, outbound updates)
- [ ] Taskbar utility working (creates tickets via email)
- [ ] Comprehensive self-hosting documentation
- [ ] Security model documented for internal deployment

---

## Phase 2: Integrations & Polish (Future - Not Starting Yet)

*To be detailed after Phase 1 completion. Focus on reducing context switching and integrating with existing tools:*
- Invoice Ninja API bridge (send completed work orders → invoices)
- Email parsing improvements (better ticket extraction)
- Optional integrations (Slack notifications, etc.)
- Performance optimization based on real usage
- Visual design polish (branding, colors, mobile)

## Phase 3+: Multi-Tenant SaaS (Speculative - Only if Demand Emerges)

*Note: This is speculative and NOT being planned for now. Only reconsidered if multiple companies request it AND we decide to offer a hosted version.*

- Would require separate SaaS codebase/infrastructure
- Original Murphy's Bench remains self-hosted
- Decision point: When/if actual demand is clear (not now)

---

## Quick Start (Next Session)

To pick up development in the next chat:

```bash
cd ~/Documents/Claude/murphys-bench
source venv/bin/activate
python manage.py runserver  # Starts on http://localhost:8000
# Visit http://localhost:8000/work-orders/ — main app
# Visit http://localhost:8000/admin/ — admin panel (login: admin)
```

**What's working now**:
- `/work-orders/` — work order list with search and filters
- `/work-orders/<id>/` — work order detail
- `/clients/` — client list with search
- `/clients/<id>/` — client detail with contacts, devices, work history
- `/admin/` — full admin panel for all data entry

**What's ready to build next**:
1. Device list + detail views (`core/views.py`, `core/urls.py`, new templates)
2. Mileage list view
3. Native create/edit forms (so techs don't need to use admin)
4. Login/logout authentication pages
5. HTMX checklist toggling

---

## Estimated Timeline

**Phase 1 MVP** (all views, forms, auth, email integration): **~15-20 hours remaining**

**Phase 1 Complete** (with testing, documentation, deployment, iteration): **3-5 weeks of part-time work** (assuming 10-15 hours/week)

**Phase 2** (integrations, polish): **Depends on scope, TBD after Phase 1**

**Phase 3+** (multi-tenant SaaS if it ever happens): **Years away, only if explicit demand emerges**

---

## Dependencies & Blockers

- [x] GitHub repository set up and connected
- [ ] Access to SCS internal network for deployment
- [ ] Feedback from SCS techs during development

---

## Notes

- This is a living roadmap; adjust as you learn more
- Time estimates are rough and may change
- Testing and documentation are built in, not afterthoughts
- Each completed feature should be deployable (even if not released to users yet)
- Visual polish is intentionally deferred — functionality first
