# Murphy's Bench — Conventions, Gotchas & Locked Decisions

> The "don't relearn this the hard way" page. Read before touching the relevant area.

## Locked decisions (do not re-litigate)

- **Credential encryption:** AES-256 via `django-encrypted-model-fields`; `FIELD_ENCRYPTION_KEY` from env; key in Bitwarden.
- **Billing philosophy:** MB tracks billing *state* only — not an accounting module. Invoice Ninja is authoritative.
- **Invoice model:** a separate `Invoice` entity on the WO (not fields on the WO). `paid_direct` = cash/walk-in before a formal invoice.
- **Visual design is a first-class requirement** — colour + icons communicate status faster than text.
- **Modals for quick edits; full pages for complex creation** (new ticket/WO/client).
- **Soft-delete everything** — hard deletes require a deliberate, type-to-confirm admin action.
- **Export-based integrations** — CSV works with any accounting system; no live API sync until there's clear demand.
- **Org credentials vault** is a deliberate competitive advantage (RepairShopCRM has device-level only, no audit trail).
- **Address = 5 fields:** Line 1, Line 2 (optional), City, State, Zip. No country.
- **Colours** live in `SiteSettings`, rendered as CSS variables in a `<style>` block in `base.html`.
- **No Celery/async queue**, **no email OAuth2** — synchronous send + standard IMAP/POP3 is sufficient at MSP scale.
- **Single unified KB** — not split between tickets and work orders.

## Design intent — deliberate, do NOT "fix"

- **A completed Work Order must never auto-close its Ticket.** A human resolves the ticket manually after real contact. `AUTO_RESOLVE_TICKET_ON_WO_CLOSE` stays **off** by default.
- **A Work Order does not require a Ticket** — work doesn't always arrive that way. But if a ticket came first, it owns the last client interaction.
- **`converted` is an *active* ticket status** — never put it in `TICKET_CLOSED_STATUSES`.
- **WO statuses:** `completed` / `cancelled` are the closed ones. `'closed'` is **not** a valid WO status.
- **Customer-visible WO notes** mean "shows on the printed repair report" — passive, **no email** to the client.
- **Permanently Delete** blocks if a client has work orders; offers Deactivate instead.

## Operational gotchas

- **Service name:** `murphys-bench.service`, **NOT** `gunicorn.service`. Restart: `sudo systemctl restart murphys-bench`.
- **App path:** `/opt/murphys-bench/`, **NOT** `~/murphys-bench/`.
- **Production Python:** `python3`, not `python`. Venv: `/opt/murphys-bench/venv/`.
- **No cron on this VM** — scheduled jobs are systemd timers in `deploy/`.
- **Never run `manage.py flush`** — it destroys configuration too. Use `reset_operational_data`.
- **Google Maps API key** is in `SiteSettings` (DB), restricted to the WAN IP in Google Cloud Console. The mileage call is **server-side** — the key never reaches the browser.

## Template / front-end gotchas

- **Audit log:** never iterate `entry.changes_dict.items` in a template (an `'items'` key shadows `dict.items()`). Use `_audit_entries(obj)` from `views.py`.
- **`mb_icons` templatetag:** put `{% load mb_icons %}` at the top of any template (and any partial) that uses `{% icon %}`, `{% attr %}`, `{% getfield %}`, or `{% markdownify %}`.
- **Dark mode:** the `dark` class is on `<html>` (documentElement), **NOT** `<body>`. Use `html:not(.dark)` for light-only CSS, not `body:not(.dark)`.
- **Tailwind** is compiled self-hosted via the standalone CLI (no Node); the typography plugin is enabled in `tailwind.config.js` for KB prose rendering. Built by `scripts/build_css.sh` → `static/css/app.css` (gitignored, built on deploy before `collectstatic`).
- **Alpine.js** is self-hosted/pinned in `static/js/` and loaded with `defer`; HTMX-swapped content reinitialises via a mutation observer.
- **Template variable reference:** to display literal `{{ }}` tokens, wrap in `{% verbatim %}…{% endverbatim %}`.
- **WorkOrderNote customer filter:** use `note_type='customer_visible'`, **NOT** `is_internal=False`.
- **Mileage Calculate CSRF:** uses `document.querySelector('[name=csrfmiddlewaretoken]')` — do not revert (it silently failed in prod before).
- **two_factor template overrides** live in root `templates/two_factor/` (in `DIRS`), **NOT** `core/templates/` — `DIRS` beats `APP_DIRS`.

## Code gotchas

- **`reverse_lazy` at module level:** don't use `reverse_lazy('core:…')` in module-level assignments in `views.py` — it causes a circular import during URL loading. Use a helper that calls `reverse()` at request time.
- **`reverse` import:** must be imported in `views.py` (a missing import once 500'd 6 settings save handlers). Test settings **POST** paths, not just GET.
- **Inbound `TICKET_RE`:** `re.compile(r'\[?(TKT-[\d-]+)\]?', re.IGNORECASE)` — matches both sequential and legacy date-based ticket numbers. Don't narrow it.
- **`split_reply_quote` / `reply_body`:** keep them pure and keep the HTML-escaping before markup is added; they're unit-tested.
- **Email logo:** CID inline (`Content-ID: logo`, `cid:logo`) via `multipart/related`. Will switch to a public URL once Cloudflare is live.

## General build rules

- All views use `LoginRequiredMixin` (the app is internal-only).
- HTMX is loaded in `base.html` with a global CSRF header on `<body>`.
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates; match existing Tailwind class patterns.
- After building, run `python manage.py check`.
- Create and apply migrations for all new models — **dev and prod**.
- **Tests required for anything touching data** (deletion, billing, lifecycle, email routing, numbering, permissions).
- Commit + push when complete; deploy with `git pull` + migrate + service restart on the VM.

## The prime directive

The project is in a **stabilization phase**, not a feature phase. The default response to a new feature request is to check it against that rule first. Depth and trustworthiness over breadth. The one approved post-stabilization feature is the **Invoice Ninja bridge** — and only after the test suite is broader, because it moves money.
