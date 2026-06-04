# Murphy's Bench Development Roadmap

**Last Updated**: June 4, 2026  
**Current Phase**: Phase 1 - SCS Internal (Backend Foundation Complete, Views & UI Next)

## Project Status Summary

✅ **COMPLETED**:
- Database schema fully designed and documented
- Django project initialized with all dependencies
- 13 data models created with proper relationships
- Database migrations created and applied (SQLite ready, PostgreSQL configured)
- Django settings configured for dev/production
- Email integration prepared for ticket ingestion
- Logging and static file handling configured
- Git repository with clean commit history

⬜ **NEXT**: 
- Admin interface customization
- Core workflows: views and forms (tickets, work orders, clients)
- HTML templates with Tailwind CSS
- HTMX for dynamic interactions (no page reloads)
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

### Core Workflows (MVP)
- [ ] **Work order list view**
  - Display all work orders with status, client, technician
  - Filter by status, technician, date range
  - Search by client/work order ID
  - Estimated: 2 hours

- [ ] **Work order detail view**
  - Show all work order information
  - Display associated device
  - Show customer-visible notes
  - Show internal notes (technicians only)
  - Estimated: 2 hours

- [ ] **Work order create/edit forms**
  - Create new work order from client
  - Edit existing work order
  - Assign technician
  - Update status
  - Add devices
  - Estimated: 3 hours

- [ ] **Client list & detail views**
  - List all clients
  - Client detail with contact info, device list, work order history
  - Create/edit client
  - Add contacts to client
  - Estimated: 2-3 hours

- [ ] **Device management**
  - Link devices to clients
  - Device detail with repair history
  - Create/edit device
  - Estimated: 1-2 hours

- [ ] **Checklist functionality**
  - Display checklist items for work order (based on repair type)
  - Mark items complete with HTMX (no page reload)
  - Update work order completion status
  - Estimated: 2 hours

- [ ] **Notes (customer-facing vs. internal)**
  - Add customer-visible notes (visible on work order printout/email)
  - Add internal technician notes (not visible to customer)
  - Edit/delete notes
  - Estimated: 1-2 hours

- [ ] **Mileage logging**
  - Quick mileage entry (date, from/to, miles, purpose)
  - Mileage list with summary totals
  - Edit/delete mileage entries
  - Estimated: 1.5 hours

### UI & Frontend
- [ ] **Base template & navigation**
  - Create base.html with consistent layout
  - Top navigation (home, clients, work orders, mileage, admin)
  - User info / logout
  - Responsive design (mobile/tablet friendly)
  - Estimated: 2 hours

- [ ] **Styling with Tailwind**
  - Set up Tailwind CSS in Django
  - Create utility classes for common patterns
  - Style forms for usability
  - Estimated: 2-3 hours

- [ ] **HTMX interactivity**
  - Checklist item toggle (no page reload)
  - Quick note addition
  - Status updates
  - Any other high-friction interactions
  - Estimated: 2-3 hours

### Admin Panel
- [ ] **Django admin customization**
  - Override default admin for better UX
  - Technician management (add/edit/deactivate)
  - Repair type management
  - Canned response templates
  - Checklist template creation
  - System settings
  - Estimated: 3-4 hours

### Testing & Quality
- [ ] **Write tests**
  - Model tests (validation, relationships)
  - View tests (authentication, permissions, data display)
  - Form tests (validation, submission)
  - Service/utility function tests
  - Aim for 70%+ code coverage
  - Estimated: 4-6 hours

- [ ] **Code quality**
  - Run black (code formatter)
  - Run flake8 (linter)
  - Run isort (import sorting)
  - Clean up any warnings
  - Estimated: 1 hour

### Documentation
- [ ] **README.md**
  - Setup instructions (venv, dependencies, migrations)
  - How to run locally
  - How to run tests
  - Project structure explanation
  - Estimated: 1 hour

- [ ] **Database schema documentation** (in docs/database-schema.md)
  - Entity relationship diagram
  - Field descriptions
  - Relationships and constraints
  - Estimated: 1-2 hours

- [ ] **Workflow documentation** (in docs/workflows.md)
  - SCS-specific workflows
  - How to use each feature
  - Common tasks and their steps
  - Estimated: 1-2 hours

### Deployment & Operations (Internal Network)
- [ ] **Local deployment testing**
  - Create script to set up fresh dev database
  - Test on clean machine (documentation accuracy)
  - Estimated: 1 hour

- [ ] **Internal network readiness**
  - Configure for internal deployment (settings, logging)
  - Set up HTTPS with self-signed certificate
  - Database backups strategy (on-network)
  - Deployment documentation (how to set up on internal server)
  - Test on internal network (10.58.58.235 or equivalent)
  - Estimated: 2-3 hours

- [ ] **Network security configuration**
  - Firewall rules (internal network only)
  - No public internet exposure
  - Email integration (SMTP inbound/outbound)
  - Documentation: Security model for self-hosting
  - Estimated: 1-2 hours

### Iteration & Feedback
- [ ] **Deploy to SCS and gather feedback**
  - Techs use it for real work
  - Collect pain points and suggestions
  - Log bugs

- [ ] **Iterate on UI/UX**
  - Adjust layouts based on feedback
  - Add missing features identified in use
  - Performance optimization if needed
  - Estimated: Variable

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
python manage.py createsuperuser  # Create admin account
# Visit http://localhost:8000/admin/ to log in
```

**What's ready to build next**:
1. Admin customization (`core/admin.py`) - Configure admin interface for data entry
2. Views layer (`core/views.py`) - Create, list, detail, update views for main workflows
3. URL routing (`core/urls.py`) - Map views to URLs
4. Templates (`core/templates/`) - HTML with Tailwind CSS
5. Forms (`core/forms.py`) - Validation for tickets, work orders, etc.

---

## Estimated Timeline

**Phase 1 MVP** (models, views, email integration, basic admin): **~20-30 hours of development**

**Phase 1 Complete** (with testing, documentation, deployment, iteration): **4-6 weeks of part-time work** (assuming 10-15 hours/week)

**Phase 2** (integrations, polish): **Depends on scope, TBD after Phase 1**

**Phase 3+** (multi-tenant SaaS if it ever happens): **Years away, only if explicit demand emerges**

---

## Dependencies & Blockers

- [ ] Final approval on database schema design
- [ ] Access to SCS internal network for deployment
- [ ] Feedback from SCS techs during development

---

## Notes

- This is a living roadmap; adjust as you learn more
- Time estimates are rough and may change
- Testing and documentation are built in, not afterthoughts
- Each completed feature should be deployable (even if not released to users yet)
