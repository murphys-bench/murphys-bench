# Murphy's Bench

**Status**: Planning / Phase 1 Development  
**Tech Stack**: Python 3.11+ / Django 5.x / HTMX / Tailwind CSS  
**Target Deployment**: Internal network (SCS) → Future: Cloud-hosted multi-tenant  
**Repository**: Local development (`~/Documents/Claude/murphys-bench`)

---

## What This Is

Murphy's Bench is a work order and service management platform designed for field service businesses—particularly small MSPs (Managed Service Providers). It handles the workflow between receiving a repair request and completing the job: managing clients, tracking devices, assigning technicians, logging work, and maintaining audit trails.

**Named for Murphy's Law**: A tool built to *prevent* things from going wrong.

---

## Vision & Philosophy

### Phase 1: SCS Internal (Current)
Build a single-company, optimized web application for Shamrock Computer Services (SCS). This is the proof-of-concept that will inform all future decisions.

- **Focus**: Get SCS's workflow working perfectly
- **Scope**: Client management, work orders, device tracking, mileage logging, admin controls
- **Timeline**: Target 4-8 weeks to MVP for internal use
- **Success**: SCS techs prefer this to the legacy PHP app

### Phase 2: Multi-Tenant Platform (Future)
Once Phase 1 is stable and learnings are captured, expand to serve other field service companies with customizable workflows.

- **Focus**: Company-specific configuration, flexible workflows, customization
- **Scope**: Not defined yet (will be informed by Phase 1)
- **Timeline**: Post Phase 1 stabilization
- **Not starting until Phase 1 is complete**

---

## Architecture

### Technology Choices

**Backend: Python/Django 5.x**
- Forces good architecture (Models, Views, Templates separation)
- Security built-in (CSRF, SQL injection prevention, password hashing)
- Excellent testing culture and infrastructure
- Clear patterns make code maintainable long-term
- Large ecosystem for business applications

**Frontend: HTMX + Tailwind CSS**
- Server-rendered HTML with dynamic interactions via HTMX (no page reloads)
- Fast iteration—adjust layouts/styles without rewriting logic
- Minimal JavaScript complexity (keeps cognitive load low)
- Tailwind enables flexible UI adjustments per company (Phase 2)
- Not overly prescriptive; room to add Vue/React admin panel later if needed

**Database: PostgreSQL**
- Reliable, proven, scales well
- Good support for complex queries needed in Phase 2 (multi-tenant filtering)
- Excellent Django ORM support

### Why This Stack?

This combination prioritizes:
1. **Security from day one** (Django enforces security patterns)
2. **Maintainability** (clear separation of concerns, easy to understand)
3. **Flexibility for Phase 2** (can add Vue admin panel without rewriting core)
4. **Developer experience** (Django's documentation and patterns are excellent)
5. **Appropriate complexity** (not over-engineered for Phase 1, extensible for Phase 2)

---

## Project Structure (Planned)

```
murphys-bench/
├── CLAUDE.md                    # This file
├── README.md                    # Setup and deployment guide
├── manage.py                    # Django management
├── requirements.txt             # Python dependencies
├── .env.example                 # Configuration template
├── .gitignore
├── murphys_bench/              # Django project folder
│   ├── settings.py             # Django settings (dev/prod configs)
│   ├── urls.py                 # URL routing
│   ├── wsgi.py                 # WSGI application
│   └── asgi.py                 # ASGI application (if needed later)
├── core/                        # Core business logic app
│   ├── models.py               # Client, WorkOrder, Device, Technician, etc.
│   ├── views.py                # Work order list, detail, create, edit views
│   ├── urls.py                 # Core app URL patterns
│   ├── forms.py                # Form definitions
│   ├── services.py             # Business logic (not in models, not in views)
│   └── templates/              # HTML templates
│       ├── base.html           # Base template with nav
│       ├── work_order_list.html
│       ├── work_order_detail.html
│       ├── work_order_form.html
│       ├── client_list.html
│       ├── client_detail.html
│       └── ...
├── accounts/                    # User authentication
│   ├── models.py               # User, Technician, Role models
│   ├── views.py                # Login, logout, user management
│   ├── urls.py
│   └── templates/
├── static/                      # CSS, JavaScript, images
│   ├── css/
│   │   └── style.css           # Tailwind-compiled styles
│   ├── js/
│   │   └── app.js              # HTMX scripts, utilities
│   └── images/
├── tests/                       # Test suite
│   ├── test_models.py
│   ├── test_views.py
│   └── test_services.py
└── docs/                        # Documentation
    ├── database-schema.md       # Data model explanation
    ├── workflows.md             # SCS workflow description
    └── deployment.md            # Deployment instructions
```

---

## Core Features (Phase 1)

### Clients
- CRUD operations (Create, Read, Update, Delete)
- Contact information (names, phone, email, addresses)
- Associated devices and work order history
- Notes and communication history

### Work Orders
- Create new work orders from client
- Assign to technician
- Track status (New → In Progress → Complete → Closed)
- Add customer-visible notes (customer sees these)
- Add technician-internal notes (only internal staff see)
- Log time spent
- Log parts/materials used
- Print or email work order summary
- Checklist of standard tasks (repair type dependent)

### Devices
- Link devices to clients
- Track device type, serial number, condition
- Associate with multiple work orders
- Device history/repair log

### Mileage
- Log travel for billing/expense tracking
- Date, from/to, miles, purpose
- Generate mileage reports

### Admin Panel
- User/technician management
- Repair type definitions
- Canned responses (templates for common notes)
- Checklists (customizable per repair type)
- System settings and configuration

---

## Database Schema Overview

(Based on current SCS Repair Tracker PHP app)

**Core Tables:**
- `users` / `technicians` - Staff members, login credentials, roles
- `clients` - Company/customer information
- `client_contacts` - People at client companies
- `devices` - Equipment being serviced
- `work_orders` - The main entity, represents a repair job
- `work_order_notes` - Customer and internal notes
- `work_order_items` - Checklist items, parts, time entries
- `mileage` - Travel logging
- `repair_types` - Categories (Laptop repair, Desktop repair, etc.)
- `checklists` - Standard task lists per repair type
- `canned_responses` - Template notes/responses

See `docs/database-schema.md` for detailed ER diagram and field definitions.

---

## Development Setup

### Prerequisites
- Python 3.11+
- PostgreSQL 14+
- Git
- Virtual environment tool (venv or pyenv)

### Local Development

```bash
# Clone/navigate to project
cd ~/Documents/Claude/murphys-bench

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with local database credentials

# Create database and run migrations
python manage.py migrate

# Create superuser (admin)
python manage.py createsuperuser

# Load sample data (if available)
python manage.py loaddata fixtures/sample-data.json

# Run development server
python manage.py runserver
```

Then visit: `http://localhost:8000`

---

## Development Workflow

1. **Feature branch**: Create a branch for each feature/bug fix
2. **Write tests**: Add tests as you build (TDD encouraged)
3. **Run locally**: Test in browser against local PostgreSQL
4. **Commit messages**: Clear, descriptive messages
5. **Code review**: Review before merging to main

### Testing

```bash
# Run all tests
python manage.py test

# Run specific app tests
python manage.py test core.tests

# With coverage report
coverage run --source='.' manage.py test
coverage report
```

---

## Key Decisions & Rationale

### Why Not Refactor the PHP App?
The legacy PHP app (in `~/Documents/Claude/scs-repair-tracker`) serves as a living specification and reference. Building fresh with Django allows us to:
- Implement security from day one (not retrofit)
- Create clean, testable architecture
- Avoid technical debt from the procedural PHP code
- Learn workflows while building, not while refactoring

The PHP app stays in maintenance mode (security fixes, small improvements) until Murphy's Bench is ready to replace it.

### Why Django for an MSP Tool?
- OSTicket and ITFlow (existing tools in the MSP space) show this domain works well in traditional web frameworks
- Django's batteries-included approach fits small business software (security, admin panel, forms, testing)
- Python + Django attracts developers who value stability and maintainability
- Open-source friendly (will be important in Phase 2)

### Why HTMX Instead of React/Vue?
- Reduces complexity for Phase 1 (no separate frontend build process)
- Server-rendered gives us simplicity and performance
- HTMX adds interactivity without JavaScript fatigue
- Easy to adjust UI based on feedback
- Can add Vue admin panel for Phase 2 configuration without affecting core

---

## Known Issues & TODOs

- [ ] Database schema not yet finalized (pending detailed workflow review)
- [ ] Authentication system not yet designed (bearer tokens? sessions? multi-factor?)
- [ ] Email/notifications not yet planned
- [ ] API (for future Clover macOS app integration) not yet designed

---

## Related Projects

- **scs-repair-tracker** (`~/Documents/Claude/scs-repair-tracker`) - Legacy PHP app, serves as specification
- **Clover** (`~/Documents/Clover`) - macOS desktop app, future integration target (not a Murphy's Bench dependency for Phase 1)

---

## Notes for Future Development

### Phase 1 → Phase 2 Transition
When moving to multi-tenant in Phase 2:
- Database models will add `company` foreign key to all relevant tables
- Views will filter by logged-in user's company
- Configuration layer will handle company-specific workflows
- Admin panel will evolve to include company administration (not just system admin)

### Open Source Aspirations
This project is being built with eventual open-source release in mind:
- Clear code patterns and documentation
- Tests as specification and quality assurance
- No SCS-specific hard-coding (use configuration instead)
- Comprehensive README and setup guides

---

**Project Location**: `~/Documents/Claude/murphys-bench`  
**Last Updated**: June 3, 2026  
**Maintainer**: Mike McCall / Claude (technical director)
