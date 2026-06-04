# Murphy's Bench Development Roadmap

## Phase 1: SCS Internal (Current Focus)

### Foundation & Setup
- [ ] **Finalize database schema**
  - Review current SCS Repair Tracker workflows in detail
  - Document entity relationships (ER diagram)
  - Define all required fields and constraints
  - Create schema documentation (docs/database-schema.md)
  - Estimated: 1-2 hours

- [ ] **Initialize Django project structure**
  - Create Django project (`django-admin startproject`)
  - Create core app (`manage.py startapp core`)
  - Create accounts app (`manage.py startapp accounts`)
  - Set up settings.py (dev/prod, environment variables)
  - Configure PostgreSQL connection
  - Estimated: 1 hour

- [ ] **Create models**
  - User/Technician model (auth)
  - Client model
  - Client contact model
  - Device model
  - Work order model
  - Work order notes (customer vs. internal)
  - Work order items/checklist model
  - Mileage model
  - Repair type model
  - Checklist template model
  - Canned response model
  - Run migrations
  - Estimated: 3-4 hours

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

### Deployment & Operations
- [ ] **Local deployment testing**
  - Create script to set up fresh dev database
  - Test on clean machine (documentation accuracy)
  - Estimated: 1 hour

- [ ] **Production readiness** (for SCS internal network)
  - Configure for production (settings, security, logging)
  - Set up SSL/HTTPS
  - Database backups strategy
  - Deployment to 10.58.58.235 (or new server)
  - Estimated: 2-3 hours

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
- [ ] All SCS core workflows functional
- [ ] Deployed to internal network and in daily use
- [ ] Techs prefer it to legacy PHP app
- [ ] Tests passing, code quality good
- [ ] No critical bugs in production
- [ ] Comprehensive documentation

---

## Phase 2: Multi-Tenant Platform (Future - Not Starting Yet)

*To be detailed after Phase 1 completion. Will include:*
- Company/organization data models
- Admin UI for company configuration
- Customizable workflows per company
- Information block rearrangement
- Custom field support
- Data isolation/filtering per company
- Subscription/billing integration (if needed)

---

## Estimated Timeline

**Phase 1 MVP** (1 hour HTMX, 2 hours CSS, 3 hours models, 5+ hours views/forms, 2 hours admin): **~20-25 hours of development**

With iteration, testing, documentation, and deployment: **4-6 weeks of part-time work** (assuming 10-15 hours/week)

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
