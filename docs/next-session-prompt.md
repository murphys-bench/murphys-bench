# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `todo.md` — complete build roadmap with specs for every planned feature
3. `docs/ticketing-design.md` — ticketing system design and workflow rules

---

## What's already built and working:

- Django 4.2 app, 14 models, 3 migrations applied
- Full CRUD views for work orders, clients, devices, mileage
- HTMX inline notes on work order detail
- HTMX checklist item toggling
- Default checklists for 6 repair types
- **Full ticket views**: list, detail, create/edit, HTMX inline replies, convert-to-work-order
- Django admin customized for all models

## What was decided this session:

A full OSTicket feature comparison was done and 15 features were scoped for Phase 1. All design decisions have been finalized and documented. See `CLAUDE.md` (Planned Phase 1 Features) and `todo.md` (full specs in build order) for complete details.

---

## Your task this session: Build Batch 1

Three features, all fully specced, no open decisions remaining.

---

### 1. Collision Avoidance

**Purpose**: Prevent two techs from simultaneously writing conflicting replies on the same ticket.

**What to build:**
- `TicketLock` model: `ticket` (OneToOneField to Ticket), `locked_by` (FK to User), `locked_at` (DateTimeField)
- Lock expiry: 10 minutes (configurable via `TICKET_LOCK_TIMEOUT_MINUTES` in settings, default 10)
- On ticket detail page load: attempt to acquire lock
  - If unlocked or lock expired: acquire it for current user
  - If locked by another user and not expired: show non-blocking yellow banner — "⚠️ [Name] is currently viewing this ticket"
  - If locked by current user: silently refresh the lock timestamp
- Lock release endpoint: POST to `/tickets/<pk>/lock/release/` — called via JS `beforeunload` event
- HTMX polls `/tickets/<pk>/lock/status/` every 30 seconds to refresh banner state
- Add URLs: `ticket_lock_acquire`, `ticket_lock_release`, `ticket_lock_status`

**Code patterns to follow**: Same `LoginRequiredMixin, View` pattern as `WorkOrderNoteCreateView` in `core/views.py`. HTMX polling same pattern as checklist toggle.

---

### 2. Ticket/WO Closure Dependency

**Purpose**: A ticket linked to an open work order cannot be closed. Forces human contact at resolution.

**What to build:**
- In `TicketUpdateView.form_valid()`: if form tries to set status to `resolved` or `closed`, check if `ticket.work_order_created` exists and its status is not in `['closed', 'cancelled']`. If so, reject with a form error.
- On ticket detail page: if linked WO exists and is open, show a warning block — "🔗 Linked to [WO-XXXX] — this ticket cannot be closed until the work order is complete."
- On ticket detail page: if linked WO exists and is `closed` or `cancelled`, show a green prompt — "✅ Work Order [WO-XXXX] is complete — this ticket is ready to be resolved."
- Add `AUTO_RESOLVE_TICKET_ON_WO_CLOSE` to Django settings (default `False`)
- In `WorkOrderUpdateView.form_valid()`: when WO status changes to `closed`, check the setting — if `True`, auto-set linked ticket status to `resolved` and save

**No new models needed** — this is logic on existing models and views.

---

### 3. Ticket Linking

**Purpose**: Allow techs to link related or duplicate tickets for cross-reference.

**What to build:**
- `TicketLink` model:
  - `ticket_a` (FK to Ticket, related_name='links_as_a')
  - `ticket_b` (FK to Ticket, related_name='links_as_b')
  - `link_type` (CharField, choices: `related`/`duplicate`)
  - `created_by` (FK to User, SET_NULL)
  - `created_at` (DateTimeField, auto_now_add)
  - Meta: unique_together on (ticket_a, ticket_b) to prevent duplicate links
- Helper method on Ticket model: `get_linked_tickets()` — returns all tickets linked to this one regardless of which side of the link they're on
- **Link UI on ticket detail sidebar**:
  - Show existing linked tickets (number, subject, status badge, link type, unlink button)
  - "Link Ticket" form: ticket number input + link type select + submit
  - HTMX — submit adds the link and returns updated linked tickets list without page reload
  - Unlinking also via HTMX
- Add URLs: `ticket_link_add` (POST), `ticket_link_remove` (POST)
- Prevent linking a ticket to itself
- Prevent duplicate links (check both directions before creating)

---

## General rules for this session:

- All views use `LoginRequiredMixin`
- HTMX is loaded in `base.html` with global CSRF header on `<body>`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns from other templates
- After building, run `python manage.py check` to confirm no issues
- Create and apply a migration for any new models (`TicketLock`, `TicketLink`)
- Update `todo.md` to mark completed items ✅ when done
