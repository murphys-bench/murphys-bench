# Next Session Prompt — Murphy's Bench

## Start by reading these files in order:
1. `CLAUDE.md` — full project overview, all design decisions, current app state
2. `TODO.md` — complete build roadmap with specs for every planned feature

---

## IMPORTANT — read the "How We Work On This Project" section at the top of CLAUDE.md first.
We are in a **stabilization phase**, not a feature phase. Default response to a new
feature request is to check it against that rule. Tests are required for anything
touching data. The model drives deploys/ops directly (incl. SSH); narrate; pause for a
go/no-go only before destructive or production-affecting steps.

## Top of the queue for next session:

**MFA reset hardening (planned, tested — apply to BOTH demo and internal prod):**
- Audit-log every MFA reset (actor, target, timestamp) — web `AdminMFAResetView` AND a new
  `manage.py reset_mfa <username>` break-glass command. Today resets write NO record (the real gap).
- Gate the reset on a dedicated `can_reset_user_mfa` permission flag instead of a blanket admin
  check (lays a delegation seam without building any admin hierarchy).
- NOTE: SCS is genuinely **single-operator** (Mike). Jim is a TESTER with admin-for-testing on the
  DEMO box only — NOT a real backup admin. So on **internal prod the `reset_mfa` CLI command is the
  actual lockout recovery path**, not just belt-and-suspenders. Prioritize it for prod.
- Add tests for the view + the command (CLAUDE.md requires tests for permission-touching code).
- NOT building SuperAdmin/role tiers — SCS is single-operator; flat Administrator is correct.
- Full context in memory `project_mb_mfa_reset_hardening`.
- Note: the per-user "Reset MFA" button already exists in `user_list.html` but only shows for
  OTHER users who are already enrolled (hidden for self + unenrolled) — that's by design.

**Infra note:** the **demo** instance (MB2, `10.58.35.223`) is now live behind Cloudflare at
`https://mbdemo.scs-tech.net` with Cloudflare Access. Internal prod (`10.58.58.82`) stays LAN-only.

---

## What's already built and working (as of session 29):

**Session 29 — Inbound reply threading fix (shipped + deployed):**
- Client replies to **converted** or **closed** tickets were spawning orphan tickets
  (the production TKT-00008/00009 bug). Fixed the status guard in
  `fetch_inbound_email._process_message`: a subject-matched reply now always threads.
  Converted stays converted (just `needs_response`); closed reopens to `open`. 2 regression
  tests; suite at 43 passing. Orphans reconciled by hand.
- Mike switched inbound IMAP → **POP3 delete-from-server** to stop the duplication source.
  Inbound still points at `testing@…` — **switch to the real support inbox** when confident
  (carried over from session 27, still the one open action).

**Session 28 — Internal tech-to-tech messaging + notification center (shipped + deployed):**
- One-face-to-the-client principle reinforced: bench techs do NOT contact clients from the WO —
  they message the **ticket tech internally** (amber "Message Ticket Tech" card on the WO,
  reciprocal "Message Bench Tech" on the ticket). Message is stored as an internal `TicketReply`
  + notifies the counterpart tech (admin fallback; never the sender).
- New generic `Notification` model (migration 0051) + **sidebar bell** with unread-count badge
  (HTMX poll) + `/notifications/` page. Future producers (escalations/SLAs) can reuse the bell.
- **An email-from-WO approach was built then reverted** — it created a 2nd client-facing voice.
  Do NOT make WO notes email clients; customer-visible WO notes = repair-report content only.
- 7 new tests; suite at 40 passing.

- Django 4.2 app, migrations through 0051
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + PostgreSQL 16 (HTTP on LAN; no domain yet)
- **Gunicorn service**: `murphys-bench.service` — `sudo systemctl restart murphys-bench` (scs-tech has NOPASSWD for restart/status of this service only)
- **App path**: `/opt/murphys-bench/`  •  **SSH**: `ssh -i ~/.ssh/id_ed25519 scs-tech@10.58.58.82`  •  **venv Python 3.12**
- Deploy: `git push` on Mac → SSH → `git pull && venv/bin/python manage.py migrate` → `sudo systemctl restart murphys-bench`
- Full CRUD for work orders, clients, devices, mileage, contacts, tickets, KB, queues; HTML email + signatures; inbound email pipeline

**Session 27 — Stabilization (all shipped + deployed):**

- **Test harness bootstrapped**: `pytest.ini` + `core/tests.py` spine suite (10 tests). Run `venv/bin/python -m pytest`.
- **Four data-integrity bugs fixed** (each test-covered): ticket-delete guard (was always-false `hasattr`); `Device.serial_number` now nullable so many serial-less devices coexist (migration 0045); collision-resistant ticket/WO number assignment (`_save_with_unique_number`); email/inbound failures now log to `core` logger instead of failing silently.
- **`reset_operational_data` management command**: clean OSTicket-cutover wipe. Dry-run by default; destructive path needs `--confirm "DELETE ALL OPERATIONAL DATA"`. Deletes operational data, keeps all config + superusers.
- **Production safety guards**: `DEBUG` now defaults False; startup refuses default `SECRET_KEY`/`FIELD_ENCRYPTION_KEY` when `DEBUG=False`; added nosniff; SSL-redirect/HSTS opt-in via `.env`. Local Mac `.env` created (DEBUG=True).
- **Nightly DB backup**: `scripts/backup_db.sh` + systemd timer (02:15 nightly). Installed + active.
- **systemd timers** for `fetch_inbound_email` (2 min) + `check_sla_overdue` (15 min) — installed + active; inbound verified connecting to IMAP.
- **Conversation-view polish**: client replies render green with the contact name; quoted email history folds into a collapsible greyed blockquote; reply header shows "<Tech> · to customer" / "<Contact> · client reply" instead of "Customer Visible". (`reply_body`/`split_reply_quote` in `mb_icons.py`.)
- **Email rendering fixes + Email Branding**: client emails now show white/contrast header text (was unreadable black-on-teal) and embed the logo inline (`multipart/related`) downscaled above the bar, instead of dumping a 695KB attachment. New "Email Branding" card (Settings → Email Templates) with `email_header_color` + `email_logo` (migration 0046), decoupled from app colors, with a live preview. Also fixed a latent missing-`reverse`-import bug that 500'd 6 settings save handlers.
- **Ticket reply UX**: reply box enlarged (rows=8, resizable); reply type defaults to Customer Visible; "also send to" has a BCC/CC selector (default BCC, `send_ticket_email(bcc=…)`); reply draft autosaves to localStorage per ticket so a status-change reload doesn't lose it.
- **Tech experience — role-based nav/dashboard + visibility scoping + escalation levels** (migrations 0047–0048): nav reordered with Queues/Mileage/Reports admin-only; tech "My Mileage" dashboard card; non-admins scoped to own + unclaimed (tickets/WOs) / own (mileage), enforced on lists, counts, AND ticket detail. Escalation: `User.level` 1–3 + `Ticket.escalation_level`; Claim/Transfer/Escalate for techs; escalate goes one level above the holder; owner kept until a higher tech claims (no orphan); "Escalated to You" dashboard panel + list/detail badges; "New to you" flag on transfers. Full design in CLAUDE.md "Tech experience" section. **Open follow-ups: retire `TechSkill`, decide on WO leveling, finish the "does every tech-facing list scope correctly" audit.**

---

## Pending / Known Issues

- **Install the backup timer (one-time sudo, Mike)** — files are on the VM at `/opt/murphys-bench/deploy/`:
  ```bash
  sudo cp /opt/murphys-bench/deploy/murphys-bench-backup.service /etc/systemd/system/
  sudo cp /opt/murphys-bench/deploy/murphys-bench-backup.timer   /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now murphys-bench-backup.timer
  sudo systemctl list-timers murphys-bench-backup.timer
  ```

- ✅ **Inbound email + SLA checks now scheduled + verified** — systemd timers installed and
  active on the VM (fetch-email every 2 min, sla-check every 15 min, backup nightly 02:15).
  The fetch service was confirmed connecting to IMAP (status 0/SUCCESS).
  **⚠ One action left:** the inbound mailbox is `testing@shamrockcomputerservices.com` —
  point it at the real support inbox in Settings → Inbound Email so customer emails become tickets.

- **HTTPS / Cloudflare cutover pending**: app is HTTP-on-LAN, so 4 `check --deploy` warnings
  (secure cookies, SSL redirect, HSTS) are deliberately deferred. See the "Going HTTPS"
  checklist in CLAUDE.md for the coordinated `.env` flip when the tunnel goes live.

---

## What's next (suggested priority order):

1. **Schedule inbound email + SLA checks** (systemd timers) — the app's inbound pipeline is currently dormant. Highest-value fix.
2. **Broaden the test suite** beyond the spine — convert/lifecycle, email routing, queue filters, permissions.
3. **Invoice Ninja bridge** (the one approved post-stabilization feature) — only after tests are broader, since it moves money. Needs the API audit first.
4. Demoted (do not build without explicit override): departments/teams/routing, customer portal, REST API, extra custom-field types, async queue, OAuth2, extra storage backends.

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
