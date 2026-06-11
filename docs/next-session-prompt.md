# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## What's already built and working (as of session 26):

- Django 4.2 app, 44 migrations applied
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16
- **Gunicorn service name**: `murphys-bench.service` — restart with `sudo systemctl restart murphys-bench`
- **App path on server**: `/opt/murphys-bench/`
- **SSH**: `ssh -i ~/.ssh/id_ed25519 scs-tech@10.58.58.82`
- Deploy workflow: `git push` on Mac → SSH → `cd /opt/murphys-bench && git pull && source venv/bin/activate && python3 manage.py migrate` → `sudo systemctl restart murphys-bench`
- Full CRUD views for work orders, clients, devices, mileage, contacts, tickets, KB, queues

**Session 26 additions:**

- **HTML email with signatures**: All outgoing ticket emails now send as HTML + plain text multipart. `base_email.html` template with company header (title bar color + text color), `white-space:pre-line` body, styled signature block with HR separator, ticket reference footer.
- **EmailSignature model** (migration 0044): name, body, is_default with single-default enforcement in `save()`. `db_table = 'email_signatures'`.
- **EmailTemplate.signature FK**: Per-template signature override; falls back to default signature when blank.
- **Logo as CID inline attachment**: Logo read from disk, attached as `MIMEImage` with `Content-ID: logo`, referenced as `cid:logo` in template. Falls back to company name text when no logo uploaded. Will be superseded by public URL when Cloudflare is set up.
- **Settings → Email Templates tab**: Signature dropdown on each template card. Signatures section below with inline add/edit/delete and default toggle.
- **Quick status change on ticket detail**: Status dropdown + Set button in Quick Actions panel. `TicketStatusUpdateView` handles WO blocking and status change emails.
- **Ticket client reassignment fix**: `TicketForm.__init__` now uses POSTed `client` value for contact queryset, not `instance.client_id`. Fixes contact validation when reassigning ticket to different client.
- **HTML entity fix**: `Context(ctx, autoescape=False)` in `email_utils.py` — prevents `&#x27;` in plain text emails.
- **Residential client labels**: Alpine.js reactive label swap on client form — "Company Name" ↔ "Client Name", "Company Info" ↔ "Client Info" based on client_type field.
- **Free email domain grouping fix**: `_FREE_EMAIL_DOMAINS` set in `fetch_inbound_email.py` — Gmail/Yahoo/etc. senders get per-person clients instead of grouping under "gmail.com".
- **Inbound email threading fix**: `TICKET_RE` regex updated to match sequential ticket numbers (`TKT-00005`) in addition to old date-based format (`TKT-20260610-0001`).
- **Inbound email timer**: systemd service + timer files written to `/tmp` on production server — Mike needs to run 4 sudo commands to install (see Known Issues below).
- **Security hardening**: `django-axes` (5 failures → 1hr lockout), `SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST`, `CSRF_TRUSTED_ORIGINS` from env, `SESSION_COOKIE_SAMESITE='Lax'`, password min length 12.

---

## Pending / Known Issues

- **Inbound email timer NOT yet installed on production**: Mike needs to run these on the server:
  ```bash
  sudo cp /tmp/fetch-inbound-email.service /etc/systemd/system/
  sudo cp /tmp/fetch-inbound-email.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now fetch-inbound-email.timer
  ```
  Verify with: `systemctl status fetch-inbound-email.timer`
  Files were written to `/tmp` but `/tmp` may be cleared on reboot — if so, need to recreate them.

- **Cloudflare setup pending**: When hostname chosen, add to `.env`:
  - `ALLOWED_HOSTS=yourdomain.com,10.58.58.82`
  - `SESSION_COOKIE_SECURE=True`
  - `CSRF_COOKIE_SECURE=True`
  - `CSRF_TRUSTED_ORIGINS=https://yourdomain.com`
  Once live, logo in emails can switch from CID inline to public URL.

- **Stray tickets TKT-00007 and TKT-00008**: Created when inbound threading was broken. Can be deleted.

- **Ticket client reassignment UX**: Works but Mike called it "clunky" — future task for inline reassignment on ticket detail without full edit form.

---

## What's next (session 26 options):

### Option A — Inbound email overhaul
Smarter intake: junk/noise filtering, unmatched sender handling, new ticket notifications (in-app badge + email alert), visual "unread" indicator on ticket list (bold row + dot, clears on first open).

### Option B — Data Management
Import wizard (CSV → map columns → preview → import), bulk export ZIP, deleted data recovery.

### Option C — Something from daily use
Any friction points or gaps SCS has noticed in actual use since deployment.

---

## Key decisions locked (do not re-litigate):

- **Credential encryption**: AES-256, FIELD_ENCRYPTION_KEY from env, key in Bitwarden
- **Billing philosophy**: MB tracks state only — not an accounting module. Invoice Ninja authoritative.
- **Invoice model**: separate entity on WO (not fields on WO) — `paid_direct` for cash/walk-in
- **Visual design is a first-class requirement**: color + icons communicate status faster than text
- **Modals for quick edits, full pages for complex creation**
- **Soft-delete everything** (hard deletes require admin deliberate action)
- **Export-based integrations** — CSV works with any accounting system
- **Org credentials vault is a competitive advantage** over RepairShopCRM
- Permanently Delete blocks if client has WOs; offers Deactivate instead
- Address: 5 fields — Line 1, Line 2 (optional), City, State, Zip. No country.
- Colors: stored in SiteSettings, rendered as CSS variables in `<style>` block in base.html
- Ticket close is always manual even when linked WO closes
- **converted = active ticket status** — never in TICKET_CLOSED_STATUSES
- **WO statuses**: completed/cancelled are closed. 'closed' is not a valid WO status.

---

## Known gotchas (read before touching these areas):

- **Gunicorn service**: `murphys-bench.service` — NOT `gunicorn.service`. Restart: `sudo systemctl restart murphys-bench`
- **App path on server**: `/opt/murphys-bench/` — NOT `~/murphys-bench/`
- **Audit log in templates**: Never use `entry.changes_dict.items` — use `_audit_entries(obj)` from views.py
- **Alpine.js**: CDN with `defer`. HTMX-swapped content reinitializes automatically via mutation observer.
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False`
- **Mileage Calculate CSRF**: Uses `document.querySelector('[name=csrfmiddlewaretoken]')` — do not revert
- **Google Maps API key**: Stored in SiteSettings (DB). Restricted to WAN IP in Google Cloud Console.
- **Production Python**: `python3` not `python`. Venv: `/opt/murphys-bench/venv/`
- **mb_icons templatetag**: `{% load mb_icons %}` at top of any template that uses `{% icon %}`, `{% attr %}`, `{% getfield %}`, or `{% markdownify %}`. Partials need their own load tag.
- **Email template variable reference**: Must use `{% verbatim %}...{% endverbatim %}` to display `{{ }}` tokens in templates.
- **Dark mode**: `dark` class is on `<html>` (documentElement), NOT `<body>`. Use `html:not(.dark)` for light-mode-only CSS rules, NOT `body:not(.dark)`.
- **Tailwind CDN**: Loaded with `?plugins=typography` for KB prose rendering.
- **reverse_lazy at module level**: Don't use `reverse_lazy('core:...')` in module-level variable assignments in views.py — causes circular import during URL loading. Use a helper function with `reverse()` called at request time instead.
- **Email logo**: CID inline attachment (`Content-ID: logo`, `cid:logo` in template). Logo read from `site.company_logo.path`. Will switch to public URL once Cloudflare is live.
- **Inbound email regex**: `TICKET_RE = re.compile(r'\[?(TKT-[\d-]+)\]?', re.IGNORECASE)` — matches both sequential (TKT-00005) and legacy date-based (TKT-20260610-0001) formats.

---

## General rules for this project:

- All views use `LoginRequiredMixin`
- HTMX loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js loaded in `base.html` with `defer`
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS via CDN — match existing class patterns
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models (both dev and prod)
- Commit and push when complete; deploy with git pull + service restart on server
