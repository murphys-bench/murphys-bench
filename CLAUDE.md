# Murphy's Bench

**Status**: Phase 1 Active Development — Deployed Internally (10.58.58.82)
**Tech Stack**: Python 3.12 / Django 4.2 / HTMX + Alpine.js (self-hosted) / Tailwind CSS (compiled, self-hosted via standalone CLI — no CDN, no Node)
**Deployment Model**: Self-hosted on internal network (Proxmox VM, Gunicorn + Nginx, SQLite)
**Repository**: `~/Documents/Claude/murphys-bench` + GitHub (private)
**Last Updated**: June 24, 2026 (Session 47 — IN-APP ADMIN UPDATE BUTTON (released **v0.2.0**, commit `5f2fbd6`). Closes the LAST rung of Mike's self-sufficiency bar: an admin can now update MB from **Settings → Updates** instead of SSHing in. Hard constraint — a web request can't restart its own gunicorn (`update.sh` ends in `sudo systemctl restart`) — so it runs **out-of-band**: the view drops an empty trigger file `logs/update-trigger`; a systemd **`.path` unit** (`deploy/murphys-bench-update.path`, `PathExists`) watches it and launches a **one-shot** (`deploy/murphys-bench-update.service` → `scripts/run_update.sh`) that runs the existing `update.sh` UNTOUCHED and writes `logs/update-status.json` for an HTMX-polled status fragment. **No new sudo** (app only writes a file; the one-shot reuses update.sh's already-NOPASSWD restart). New `core/update_ops.py` (read-only git inspect + trigger/status helpers), 3 admin-gated views (`UpdateStatus/Check/Trigger`), `core/templates/core/partials/update_status.html`, Updates tab in `SETTINGS_TABS`. Decisions (Mike): **"Update to latest" only** (no tag-picker) + **single confirm click** (justified by update.sh's auto-rollback). 8 tests, suite **112→120**. **VERIFIED on mb-test (201):** units installed (path unit active); happy-path drill → `succeeded` + app restarted out-of-band; force-fail drill (broken migration on a higher tag) → migrate failed → **auto-rolled back to the good version, healthy**, status `failed` with log tail. ALSO this session: (a) **dev Python alignment COMPLETE** — MacBook Air M5 built its own Py3.12.13 venv (mini was already done), suite green on both; the `gh` CLI is now installed+authed (HTTPS token) on the laptop so CI can be confirmed before tagging. (b) **`update.sh` branch-deploy bug FIXED** (commit `5f2fbd6`, in v0.2.0): `update.sh <branch>` was checking out the box's STALE LOCAL branch ref instead of `origin/<branch>` — it silently DOWNGRADED mb-test mid-session; tags/SHAs were always safe (absolute). (c) First release cut via `scripts/release.sh` with CI confirmed green (`gh run list`). **DEPLOYED v0.2.0 to ALL THREE boxes:** mb-test (units + both drills), MB2 demo (units + happy drill — full NOPASSWD), prod (code deployed + healthy). prod units INSTALLED + path unit active/enabled (Mike ran the password-gated `/etc` copy-paste block — prod NOPASSWD is scoped to only `systemctl restart/status murphys-bench`). **v0.2.0 fully live on all three boxes; self-sufficiency bar COMPLETE end-to-end.** See memory `project_mb_publish_ops_selfsufficiency`. // Session 42 — TICKET SLA BUGFIX. A ticket that had been replied to and parked in Waiting-on-Customer still flipped **red/overdue** once `due_at` passed, because `is_overdue` only checked the deadline, never whether we'd actually responded. MB tickets carry a *response* SLA (`due_at = created_at + grace_period`), so once the first staff reply goes out the clock should stop for good. Fix: added **`Ticket.first_responded_at`**, stamped on the first staff **customer-visible** reply only (internal notes and inbound client replies do NOT count); `is_overdue` returns `False` once it's set and the clock never re-arms. **Migration 0065** backfills existing tickets from their earliest qualifying staff reply so already-replied tickets clear on deploy. Commit `2235c53`; deployed and 0065 applied (verified `[X]`) on prod (82) / MB2 (35.223) / mb-test (108). Suite 104→**107**. // Session 41 — FRONT-END SELF-HOSTING + WO REPORTED-ISSUE. (a) **CDN fully removed — entire front-end now self-hosted.** Trigger: Privacy Badger blocking `unpkg` on Mike's laptop silently broke the app (Alpine+HTMX failed to load) — a real-world proof that loading core deps from third-party CDNs is wrong for a self-hosted product. Fix in two strokes: **HTMX 1.9.12 + Alpine 3.15.12 vendored/pinned** into `static/js/` (commit `e445fdd`; resolved the floating `alpinejs@3.x.x`), and **Tailwind moved off `cdn.tailwindcss.com` to a compiled self-hosted stylesheet** `static/css/app.css` via the **standalone Tailwind v3.4.19 CLI** (`scripts/build_css.sh` + `tailwind.config.js` + `tailwind/input.css`; NO Node; binary cached in gitignored `.tailwind/`; `app.css` gitignored & built-on-deploy — `update.sh` builds before collectstatic) (commit `63d9421`). Purge-correctness checked by rendering 17 pages and verifying every `class=` token exists in app.css — caught that `{% icon %}` builds size classes dynamically in Python → **safelisted `(w|h)-(3..16)`**. Verified on mb-test (Mike eyeballed) incl. the Linux build path, then deployed prod+MB2+mb-test, 0 CDN refs. Also fixed a sidebar bug found same day: nav was `overflow-hidden` with no scroll region → footer clipped below Reports on short laptop screens; now a `flex-1 min-h-0 overflow-y-auto` region. (b) **WorkOrder.reported_problem** free-text "Reported Issue / Work Requested" field (migration 0064): WOs had no free-text problem field — only the predefined `repair_type` dropdown — and ticket→WO conversion **silently dropped `ticket.description`**. Now a bench-editable freeform field (works on standalone WOs with no ticket), carried from the ticket on convert, shown on form/detail/repair-report. Suite 102→**104**. (c) **NEXT: CSP** — now feasible (no CDN) but non-trivial (Alpine needs `unsafe-eval` or a CSP-build rewrite; inline scripts need nonces; inline styles → `style-src 'unsafe-inline'`). Plan: **report-only first**, its own session. (d) **Open task logged:** align dev Mac Python 3.9→3.12 (no 3.12 installed; best done on the incoming MacBook Air) — memory `project_mb_dev_python_alignment`. See memories `project_mb_tailwind_cdn_security`, `project_mb_wo_reported_issue`, `feedback_dont_excuse_shortcuts_with_my_gaps`. // Session 40 — PUBLISH-READINESS + CI GATE + SELF-SUFFICIENCY. (a) **CI gate LIVE** — `.github/workflows/ci.yml`: GitHub Actions runs pytest (102) + `manage.py check` on every push/PR (Py3.12, SQLite, ephemeral keys, plain `check` so green=green); first run green. Makes the test discipline self-enforcing — the #1 gap from an external-AI review Mike solicited, which reframed MB as a **credible, evidence-backed internal-tool foundation, not 'vibe-coded'** (verdict: gate > tooling; mypy deprioritized; verified 265 DB-state assertions vs 13 page-load-only across the 102 tests). (b) **`scripts/update.sh`** — one-command, fail-loud, **backup-first** update (pull→pip→migrate→collectstatic→restart→health-poll + rollback hint); verified on staging+prod; Mike self-updates without help. (c) **README.md** drafted for a possible open-source release — honest 'what it is / today + where it could go' framing, repair/work-order wedge, openly **UNDECIDED** on POS/inventory/SMS/other-billing(QBO)/docs/multi-shop (explicitly NOT foreclosed, per Mike); viability desk-check done (real gap for a self-hosted repair/work-order tool — niche but underserved; ITFlow = adjacent competitor + proof-of-demand). (d) **Mac→GitHub over SSH now** (key on scs-tech2026 acct; origin `git@github.com`) — retires the PAT workflow-scope/Keychain friction permanently. (e) Mike's **self-sufficiency bar** recorded: install/update/backup/export WITHOUT Claude; **tagged releases mandatory**; **failed update must AUTO-rollback (code+DB)**; Docker deferred (setup.sh instead). See memory `project_mb_publish_ops_selfsufficiency`. STILL QUEUED: `restore.sh`, data export, `setup.sh`, tagged-releases+auto-rollback, in-app admin Update; publish-remaining = screenshots/LICENSE/secrets-audit/de-Shamrock tweaks (validate demand first). // Session 39 — OBSERVABILITY KEYSTONE shipped, closing the assessment's last red (Domain G): MB now self-monitors — its own operational failures open a **System Alert ticket** (dedicated 'System Alerts' client + admin notification bell) via `core/system_alerts.py` + `manage.py send_alert`, chosen over email because the box can't send system mail. Coverage: app **500s → ticket** (`core/log_handlers.SystemAlertHandler` on the `django.request` logger, production-only, wired in `settings.LOGGING`); systemd **OnFailure → ticket** on all three job timers (`murphys-bench-alert@.service` template + `.service.d/onfailure.conf` drop-ins using `%N`); a daily **disk-usage check** (`scripts/mb_disk_check.sh` + timer); a **backup dead-man's-switch** via healthchecks.io (`HEALTHCHECKS_URL` in `.env`; `mb_backup.sh` pings on success and `/fail` on failure); and **logrotate** for the gunicorn access/error logs (previously unbounded). Migration 0063 (Ticket source 'system' + Notification kind 'system_alert'). Built + validated on the staging VM (201) first, then deployed to prod; suite →102. NOTE: prod sudo is NOT passwordless beyond the gunicorn restart, so the `/etc` unit installs are a Mike-run copy-paste block (documented in `deploy/README.md` → Observability). // Session 38 — built a dedicated TEST/STAGING VM `mb-test` (VMID **201**, `10.58.58.108`, scsprox node): fresh **install-from-git** on Ubuntu 24.04.4 / Py3.12, SQLite, gunicorn(unix socket)+nginx, sla-check timer. Runs a **COPY of prod data under prod's `FIELD_ENCRYPTION_KEY`** (Mike's Option-1 choice — faithful migration testing) with **all outbound integrations neutralized** (mailbox/Invoice-Ninja/B2/Maps creds blanked in the test DB; fetch-email + backup timers NOT enabled) → a real pre-prod mirror that ends edit-on-prod. **Read-only GitHub deploy key** for `git pull` deploys (deploy: `git pull && migrate && collectstatic && sudo systemctl restart murphys-bench`). 100/100 tests pass + Mike-verified login. ⚠ **Holds REAL client data — keep LAN-only, NEVER repurpose as a demo** (MB2 is the fake-data demo; see memory `mb_test_vm_holds_real_data`). The clean build doubled as an **INSTALL.md shake-out → FIXED** (commit `b1c1856`): missing `mkdir logs/` step (broke every manage.py at startup), Postgres-vs-SQLite defaults, stale gunicorn/nginx snippets (now unix-socket/50M/network.target + EnvironmentFile), dropped unused `psycopg2-binary`, added `static/.gitkeep` (fixes `staticfiles.W004`). ⚠ prod not yet pulled to `b1c1856` (doc/requirements-only, inert — sync at next deploy); dev Mac venv still Py3.9. ALSO **squared away PBS backups** (Mike-driven — the parked learning task, now DONE): resolved the VMID **102/103 collisions** across the two standalone nodes (BookStack 102→**202**, Cloudflared 103→**203**; prod stays 103), purged dead WinXP/ITFlow groups (GC reclaimed **82 GB**), added a daily **verify** job + centralized **prune** (keep 7/4/3), set **both** PVE backup jobs to Selection "All", and **notify-on-failure → opens a ticket** — closes the assessment's PBS red. VMID convention now: scsprox2=1xx, scsprox=2xx (see memory `reference_proxmox_pbs_infra`). // Session 37 — (a) inbound DUPLICATE-TICKET bug FIXED: a leftover user-level scheduler racing the system fetch timer + a non-atomic dedup → atomic Message-ID claim + DB unique constraint + flock run-lock + Message-ID strip (migration 0062, suite →100); (b) full report-only SYSTEM ASSESSMENT across 8 domains (BookStack page 09 + memory `project_mb_assessment_2026_06`): verdict — app/code/data/security are SOUND (verified green), every failure lived in the OPERATIONAL/PROCESS shell; the two reds = NO OBSERVABILITY (keystone — nothing reports failure) and a BROKEN PBS whole-VM backup for prod (VMID-103 collision prunes the real backup); prioritized remediation recorded; Mike to stand up a dedicated TEST VM as real staging. // Session 36 — DB backup FIXED: discovered prod runs on **SQLite**, not PostgreSQL (the old pg_dump dumped an empty Postgres DB — root cause of the long-broken backup). Built a fail-loud SQLite-snapshot + attachments + .env backup → **Backblaze B2** (immutable, Object Lock governance 30d, lifecycle auto-prune), WAL enabled, restore-tested, nightly timer repointed. Decision: stay on SQLite for the SCS instance. Docs swept — prior "PostgreSQL 16 in production" claims were never true and are corrected. // Session 35 — security posture pass. Audited prod (`manage.py check --deploy` + settings) and acted: added **admin user-delete** (self/last-superuser guards) so the leftover test accounts could be removed (Mike deleted them — only `admin` remains); tightened file perms (`.env` 640→600; `protected/`+`backups/` 775→750); upgraded runtime CVE deps **Pillow 10.1→12.2, requests 2.31→2.33, cryptography 48.0.0→48.0.1** (9 CVEs cleared; all 99 tests pass on prod's Py3.12; dev-only pytest/black left pinned). Found: **dev venv is Py3.9 vs prod Py3.12** (couldn't validate upgrades locally → validated on prod). Posture verdict: app layer solid; real gaps are infra — broken DB backup (tracked), plain-HTTP-on-LAN (TLS deferred, Mike gun-shy), SSH/OS hardening (sudo-gated, Mike to pair). Suite 96→99. Discussion queued: TLS, an easy patch/update mechanism, aligning dev Python to prod. // Session 34 — Phase B shipped + verified: one-directional **Invoice Ninja draft push** from a WO. `core/invoice_ninja.py` (requests, v5 API); Settings → Invoice Ninja card (URL + encrypted token + enable) with Test Connection; "Send to Invoice Ninja" button on the WO → POST `/invoices` as a DRAFT from PRICED lines only, IN assigns the number, WO# → `po_number`; find-or-create client (type-aware name; `Client.invoice_ninja_id` link-once); duplicate guard via `WorkOrder.invoice_ninja_id`; fail-loud; ref editable. Disabled by default. Mike configured the live token + ran a real push — works as intended. ALSO: added **work order hard-delete** (admin only) — there was never one; cleans attachment files, reopens a converted ticket, cascades the rest. Migration 0061. Suite 88→96. // Session 33 — Phase A billing primitive shipped: new generic `LineItem` model (GenericFK — WorkOrder now, future Quote later; kind labor/part, qty, unit_price, computed line_total) is now THE billable-work record. Unified `WorkPerformed` INTO it (migrated all rows → labor LineItems, rewired the log/edit/delete UI, deleted WorkPerformed). `QuickLaborItem.default_price` prefills the buttons; WO total shows on detail + repair report; custom entry does labor/part w/ price. MB captures+totals prices, Invoice Ninja stays the billing authority (sets up Phase B push). Migrations 0058/0059/0060. Deployed to PROD (3 WorkPerformed rows migrated cleanly), verified data/service AND browser-verified by Mike. Suite 84→88. ⚠ ALSO corrected a false doc claim: the pg_dump backup never worked (empty dumps) — PBS whole-VM backup is the real safety net; real DB backup tracked as a TODO. // Session 32 — attachment security review acted on: attachments now stored OUTSIDE the web root (`PRIVATE_MEDIA_ROOT=BASE_DIR/protected`) so nginx's /media/ alias can't serve them — the authenticated download view is the only path; download view now authorizes per-object (resolves owning Ticket/TicketReply/WorkOrder/WorkOrderNote + applies visibility scoping, closing an IDOR); inbound email attachments now enforce the blocked-extension list + size cap (untrusted path previously enforced neither). Migration 0057 (state-only), conftest isolates media roots. Deployed to PROD + verified: old /media/attachments URL → 404, auth view → login. Suite 80→84. PROD + MB2 demo both fixed+verified (demo also sits behind Cloudflare Access). // Session 31 — device/WO usability: ticket device dropdown now scopes to the selected client (form queryset + HTMX OOB cascade); Device gained free-text CPU/RAM/storage; WorkOrder snapshots those specs at creation as an "as-serviced" record and syncs edits back to the device master (migrations 0055/0056); device-detail back-link now returns to the device's client instead of the dead-end device list. All live on prod. Suite 71→80. // Prior: Billing-architecture decision — the Invoice Ninja bridge is staged into a priced line-item primitive FIRST (Phase A, generic/attachable line items + WO total + tests — the expensive-to-reverse-with-live-data piece), THEN the IN push (Phase B, draft-push so IN owns invoice assembly). Quote/Project approval layer deferred (additive, no live-data clock). No tax (Oregon). Full rationale in memory `project_mb_pricing_architecture` + `project_in_integration` and in TODO.md "Billing work". // Session 30 — T2/Helpdesk Buttons moved off OSTicket API to T2's Email Connector; MB unwraps the no-reply relay `email-connector@tier2tickets.com` to the real contact via forwarded `From:`; unmatched inbound now parks in an "Unsorted/Unverified" triage bucket (migration 0054, `Client.is_unsorted`) instead of auto-creating junk clients, with an admin dashboard card + `/tickets/?triage=1`. Inbound fully live on the real support inbox. Migrations through 0054; test suite 71 passing. Prod: Claude restarts it directly — NOPASSWD for `systemctl restart murphys-bench`.)
**Gunicorn service**: `murphys-bench.service` — `sudo systemctl restart murphys-bench`
**App path on server**: `/opt/murphys-bench/`

---

## How We Work On This Project

**Read this section first, every session. It governs everything below it.**

Murphy's Bench is in **daily production use at SCS**. It is past the prototype stage.
That single fact sets the rules below. The owner (Mike) is a non-developer and the
domain expert / director; the AI assistant is the technical director. Mike holds the
*intent*; the assistant holds the *implementation* — and is responsible for flagging when
a request would compromise the codebase's health, not just executing it.

### The prime directive: stabilize before adding
We are in a **stabilization phase**, not a feature phase. Until the spine test suite
exists and the safety guards are in place, **do not build new features** unless Mike
explicitly overrides this. When asked for a new feature, the default response is to
check it against this rule first. Breadth (more features, more configurability) is no
longer the goal; depth (trustworthiness of what already exists) is.

### Non-negotiable habits
1. **Tests are required for anything touching data.** Any change to deletion, billing
   state, ticket/WO lifecycle, email routing, number generation, or permissions ships
   *with* a test that locks in the behavior. No exceptions. Tests are not "later" —
   that era ended when the app went into production. Target the spine, not 70% coverage.
2. **Plan before building anything non-trivial.** Use plan mode. Get the approach
   approved *before* writing code. Most expensive mistakes are "built the wrong thing well."
3. **Review before it goes live.** Run a real review pass on any change touching money,
   credentials, permissions, or data deletion before it reaches the production VM.
4. **Fail loud, not silent.** No new `except: pass` or `fail_silently` that hides a real
   failure. Catch so the user isn't crashed, but log it so we find out.
5. **Every config option is a permanent cost.** Default to a good hardcoded choice.
   Do not add a toggle/setting/custom-field-type until a real user actually needs it.

### Which model does what
Model choice is secondary to the habits above — CLAUDE.md + tests are what keep the
project coherent across sessions, not the model. That said, match model to task:
- **Frontier reasoning model (Opus 4.8 / equivalent)** — planning, architecture
  decisions, code review, gnarly debugging, and "are we on track" check-ins.
- **Sonnet (fast, capable)** — routine implementation: forms, views, templates, CRUD.
- Switch freely; the source-of-truth docs and tests make the handoff safe.

### Known issues to fix first (stabilization backlog, in order)
1. ✅ **DONE (session 27):** `TicketDeleteView` guard fixed — now uses
   `WorkOrder.objects.filter(ticket=ticket).exists()`. Covered by tests.
2. ✅ **DONE (session 27):** `Device.serial_number` now `null=True`; `Device.save()`
   normalizes blank → `None`; migration 0045 converts existing blank → NULL. Covered by tests.
3. ✅ **DONE (session 27):** number assignment is now collision-resistant via
   `_save_with_unique_number()` helper + `save()` override on Ticket and WorkOrder
   (retry-on-IntegrityError, re-reads DB each attempt). Covered by tests.
4. ✅ **DONE (session 27):** silent email/inbound failures now log to the `core` logger
   (lands in `murphys_bench.log`); bad templates also record a failed EmailSendLog. Covered by tests.

**Test harness now exists** (session 27): `pytest.ini` + `core/tests.py` spine suite.
Run with `venv/bin/python -m pytest`. The "tests for anything touching data" rule is now enforceable.

5. ✅ **DONE (session 27):** `reset_operational_data` management command. Surgically
   deletes operational data (clients, contacts, devices, tickets, WOs, mileage,
   attachments+files, logs, non-superuser users) while KEEPING all configuration
   (settings, roles, statuses, help topics, SLA plans, repair types, checklists, canned
   responses, templates, tiles, custom-field *definitions*, KB, org credentials) and all
   superusers. **Dry-run by default**; the destructive path requires the exact phrase
   `--confirm "DELETE ALL OPERATIONAL DATA"`; runs in one transaction. Optional
   `--keep-users a,b`. Covered by tests. This is the clean cutover-from-OSTicket wipe.
   **Never use `manage.py flush`** — it destroys configuration too.
6. ✅ **DONE (session 27):** Production safety guards in settings.py. `DEBUG` now
   defaults to `False` (local dev sets `DEBUG=True` in `.env` — a local `.env` was
   created on Mike's Mac). Startup raises `ImproperlyConfigured` if `DEBUG=False` and
   `SECRET_KEY`/`FIELD_ENCRYPTION_KEY` are still the committed defaults. Added
   `SECURE_CONTENT_TYPE_NOSNIFF`; `SECURE_SSL_REDIRECT` + HSTS are opt-in via `.env`
   (HSTS deliberately left off until HTTPS is confirmed end-to-end — it's hard to undo).
   Prod verified already has DEBUG=False + real keys, so the guard passes there.
7. ✅ **DB backup — DONE (Jun 22). The old pg_dump backup dumped an EMPTY Postgres DB; prod actually runs on SQLite. Replaced with a fail-loud SQLite-snapshot + attachments backup to Backblaze B2 (immutable, Object Lock 30d). See docs/bookstack/05-backup-and-disaster-recovery.md.**
   **FIXED (Jun 22).** Root cause: prod runs on **SQLite**, but the old `backup_db.sh` ran `pg_dump`
   against an empty Postgres DB → ~394-byte empty dumps reported as "OK". Replaced with
   `scripts/mb_backup.sh`: a consistent SQLite snapshot + `protected/` + `media/` + `.env` → dated
   tarball, **fail-loud** (integrity + size checks), pushed off-site to **Backblaze B2** (immutable,
   Object Lock 30d), 14 local copies; **restore-drilled from the offsite copy**. (`backup_db.sh` now
   delegates to it.) ⚠️ **PBS whole-VM backup is NOT a working safety net for prod** — a VMID-103
   collision with another VM makes PBS prune the one real murphys-bench backup (found in the Jun 22
   assessment, BookStack page 09; fix is a scheduled hands-on task). Restore needs the tarball **+**
   `FIELD_ENCRYPTION_KEY` (Bitwarden; the B2 app key + `SECRET_KEY` are in Bitwarden too).

   ✅ **Related gap CLOSED + VERIFIED (session 27):** `fetch_inbound_email` (every 2 min)
   and `check_sla_overdue` (every 15 min) systemd timers (`deploy/`) are **installed and
   active** on the VM. Confirmed working end-to-end: the fetch service ran and connected to the
   mailbox `mail.shamrockcomputerservices.com` over **POP3** (inbound was switched IMAP→POP3 to kill
   a duplication bug). The fetch-email and sla-check timers are `active`/`enabled` and working.
   (The backup timer is active and now produces REAL backups — see item 7 above.)
   ⚠ **One action left for Mike:** the inbound mailbox is `testing@…` — point it at the
   real support inbox in Settings → Inbound Email so customer emails become tickets.

### Going HTTPS (Cloudflare cutover checklist — NOT done yet, deliberately deferred)
The app is currently served over plain HTTP on the LAN (`10.58.58.82`, no domain), so
`manage.py check --deploy` shows 4 HTTPS-related warnings (HSTS, SSL redirect, secure
session cookie, secure CSRF cookie). These are **correct to leave off** until HTTPS is
end-to-end — turning them on now would break internal access. When the Cloudflare tunnel
goes live, flip these together in the production `.env`:
- `SESSION_COOKIE_SECURE=True`, `CSRF_COOKIE_SECURE=True`
- `SECURE_SSL_REDIRECT=True`
- `SECURE_HSTS_SECONDS=31536000` (only once HTTPS is confirmed everywhere — HSTS is hard to undo)
- add the public hostname to `ALLOWED_HOSTS` and set `CSRF_TRUSTED_ORIGINS=https://<hostname>`
Then re-run `manage.py check --deploy` — it should come back clean.

### Roadmap re-prioritization (decided this session)
- **Demoted / dropped** (enterprise-shaped or "for someone else," not needed at a solo/small
  shop): Departments, Teams, ticket auto-routing, customer self-service portal, REST API,
  more custom-field types, async email queue, email OAuth2, extra storage backends.
- **Kept small:** Data Management — only the *export* + *soft-delete recovery* halves
  (useful internal safety). Skip the import wizard.
- **The one feature worth pursuing after stabilization:** Invoice Ninja bridge (real SCS
  billing value) — but only *after* the test suite exists, since it moves money.
- **"For others" hygiene** (LICENSE, README, fail-safe settings): cheap, do once when
  convenient, but it does **not** drive feature work. MB becomes useful to others by being
  bulletproof at one shop first — not by adding features for hypothetical users.

### Conversation view (ticket replies) — deliberate rendering (session 27)
`core/templates/core/partials/ticket_reply_item.html` + `reply_body`/`split_reply_quote`
in `mb_icons.py`:
- Reply side is keyed on `reply.created_by`: **empty = inbound client reply** (green,
  shows `ticket.contact` name); set + `internal` = internal note (yellow); set +
  `customer_visible` = staff→customer (blue). Header reads "<who> · <direction>", NOT
  "Customer Visible".
- `reply_body` filter: preserves newlines and **folds quoted email history** (everything
  from the first `>`/`On … wrote:`/`--- Original Message ---` boundary) into a collapsible
  greyed `<details>` blockquote. Content is HTML-escaped before markup is added — don't
  remove the escaping. `split_reply_quote` is unit-tested; keep it pure.
- `strip_quoted_replies` is intentionally OFF in prod (keep the full thread); the quote is
  hidden at display time, not destroyed at ingestion.
- **Reply form deliberate defaults** (`ticket_detail.html`): reply type defaults to
  **Customer Visible** (not internal); textarea is `rows=8` and resizable; the "also send to"
  field has a **BCC/CC selector defaulting to BCC** (`cc_mode` → `send_ticket_email(bcc=…)`);
  the draft **autosaves to `localStorage` per ticket** (`mb_draft_<pk>`) and restores on load,
  so a status-change reload doesn't lose it — cleared on successful submit. Status change is
  still a full POST/reload (the draft autosave is what protects the text; HTMX-ifying it is a
  possible later polish, not needed).

### Email appearance (session 27)
Client-facing HTML emails use `core/templates/core/email/base_email.html` via
`email_utils._build_html_email`:
- **Header text color is auto-computed** (`_contrast_text_color`) from the header bar color —
  never a stored setting. Keeps it readable on any bar color. Don't reintroduce a manual
  text-color field.
- **Logo embeds inline via `multipart/related`** (`msg.mixed_subtype = 'related'`). Without
  that, `cid:logo` doesn't resolve and clients dump the full image as an attachment. The logo
  is downscaled with Pillow (`_load_logo_resized`) and placed above the bar.
- **Email branding is editable** in Settings → Email Templates ("Email Branding" card):
  `email_header_color` + `email_logo` (migration 0046). Both optional — blank falls back to the
  app Title Bar color / company logo via `_email_header_color` / `_email_logo_field`. These are
  decoupled from the app's own colors on purpose.
- Gotcha fixed this session: `reverse` must be imported in `views.py` (it wasn't — 6 settings
  save handlers were latent 500s). Test settings **POST** paths, not just GET.

### Tech experience: visibility scoping + escalation levels (session 27, Jun 12)
The big shift this session — techs no longer see everything. Migrations 0046–0048.

**Nav / dashboard by role** (`is_admin` now in the context processor = staff OR
`can_manage_settings`):
- Sidebar order: Dashboard, Tickets, Work Orders, Clients, KB, then **admin-only** Queues,
  Mileage, Reports. Techs don't see the last three. (Hiding ≠ access control — those URLs
  aren't blocked, just unlinked.)
- Techs get a **"My Mileage"** dashboard card where admins see Team Workload (their mileage
  entry point, since Mileage left their nav).

**Visibility scoping (non-admins):**
- Work orders (`_scope_assignable_for`): own + unclaimed pool. Mileage list: own only.
- Tickets (`_scope_tickets_for`): own + unclaimed + tickets escalated above their owner's
  level up to the viewer's level. Applied to ticket **list, tab counts, AND detail** (a tech
  404s on another tech's ticket by URL). Admins see everything.

**Escalation levels (1–3):** `User.level` (default 1, set in user edit form),
`Ticket.escalation_level` (default 1).
- Tech actions are **Claim / Transfer / Escalate**; admins **Assign**. ("Assign" is a
  dispatcher verb — keep it off the tech view.)
- `Ticket.escalate()` raises to one level **above whoever currently holds it** (an L2-owned
  ticket jumps to L3, not L2). `can_escalate` hides the button when there's nowhere higher.
- **No black hole (Mike's hard rule):** escalating KEEPS the current owner; ownership only
  moves when a higher-level tech **Claims** it. `escalation_pending` = escalated above owner.
- Escalations surface in three places (must stay consistent): ticket detail badge, ticket
  list amber "Escalated → L#" badge, and the dashboard **"Escalated to You"** panel (the
  dashboard ticket queries are level-aware, not just `assigned_to=user`).
- **"New to you":** `Ticket.assignment_unseen` set when transferred/assigned by someone else
  (not self-claim), cleared when the assignee opens it; blue badge on the ticket list.

**Deliberately deferred** (don't build without a reason): retiring `TechSkill` (replaced in
spirit by levels — strip once levels are proven), leveling Work Orders (kept simple), and
bounding the unclaimed pool by level (techs still see all unclaimed).

### Internal tech-to-tech messaging + notification center (session 28, Jun 13)
**One face the client sees — the ticket tech.** The ticket is the single client-facing
channel. A bench tech who needs the client contacted does NOT email/contact the client from
the work order; they message the ticket tech **internally**, and the ticket tech makes the
client contact through the normal ticket reply. (We briefly built the opposite — customer-
visible WO notes emailing the client + mirroring to the ticket — and **reverted it**: it
creates a second client-facing voice. **Do not make WO notes email clients.** Customer-visible
WO notes mean only "shows on the printed repair report" — passive, no email.)

- **`Notification` model** (migration 0051): per-user in-app alerts; generic so future
  producers (escalations, SLAs, assignments) can feed the same bell. `target_url` → linked
  ticket detail else WO detail.
- **`TechMessageView`** (`source='wo'`/`'ticket'`; URLs `wo_message_tech`/`ticket_message_tech`):
  stores the message as an **internal `TicketReply`** in the ticket thread (consolidated
  record), then notifies **directionally** — a WO message targets the ticket tech, a ticket
  message targets the bench (WO) tech. If that target role is **unassigned** → fall back to
  admins (`_notification_admins`, a dispatcher picks it up). If the target role is **held by
  the sender** (one person working both ends) → notify no one (do NOT spam other admins about
  a message sent to oneself). Never notify the sender.
- **Sidebar bell** (`base.html`, new `bell` icon) with a red unread badge from an HTMX-polled
  fragment (`notification_count`, `load, every 60s`). `/notifications/` page: unread-first,
  click → `notification_open` marks read + redirects to target; `notification_read_all`.
- **Affordances:** amber "Message Ticket Tech" card on the WO (only when `work_order.ticket`);
  reciprocal "Message Bench Tech" on the ticket (only when `ticket.work_order_created`).
- **Known gap:** stand-alone WO (no ticket) has no ticket tech → action hidden there.
- Covered by 7 tests in `core/tests.py` (suite at 40 passing).

### Inbound reply threading — converted/closed tickets (session 29, Jun 14)
**Bug found in production:** a client reply to a **converted** ticket (and a closed one)
was falling through and creating a brand-new ticket instead of threading. Root cause was
the status guard in `fetch_inbound_email._process_message`:
`if ticket and ticket.status not in ('closed', 'converted')`. Once a ticket converted to a
WO, the next client reply failed the check → new ticket. The IMAP "leave on server" setting
then re-ran it every poll (forwarded mail had no usable `Message-ID` for the dedup guard),
multiplying one wrong ticket into several (TKT-00008/00009).
- **Fix:** a subject-matched reply now **always threads into its ticket.** Converted tickets
  stay `converted` (just flagged `needs_response` — never un-convert a live WO). Closed tickets
  **reopen to `open`** on reply. The matcher reads the `[TKT-…]` subject token, not headers —
  it never relied on `In-Reply-To`/`References`.
- Covered by 2 regression tests in `core/tests.py` (suite at 43 passing).
- **Mike's side:** switched inbound from IMAP to **POP3 (delete-from-server)** to stop the
  duplication at the source. Tradeoff: MB becomes the only copy of inbound mail — no server
  backup. Inbound is still pointed at `testing@…`; switch to the real support inbox once
  confident (the one open action carried over from session 27).
- The two orphan tickets were reconciled by hand: Wayne's reply was appended to
  TKT-20260610-0001 with its original timestamp, then TKT-00008/00009 were deleted.

### TLS / HTTPS — design decision (DECIDED Jun 20, session 35 — don't re-litigate)
**MB intentionally does not terminate TLS; it runs behind a TLS-terminating reverse proxy.** This is the standard Django model and is now documented for self-hosters in [`docs/deployment-tls.md`](docs/deployment-tls.md) (linked from INSTALL.md). MB is already proxy-ready: trusts `X-Forwarded-Proto`, hostname via `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`, secure-cookie/HSTS/SSL-redirect are `.env` toggles (off by default so HTTP-on-LAN works, flip on once TLS is in front).

**The decision (after a full discussion with Mike — capture so it's not re-opened; Mike won't remember the reasoning):**
- **Encryption ≠ exposure** — the two are independent. Mike's past Let's Encrypt scare (foreign IPs hammering) was *box exposure* (open ports), not the cert. TLS can be added with zero exposure.
- **SCS network:** 5 segmented LANs. Prod is on the **trusted main LAN** (not internet-reachable). The **VM LAN is untrusted**; MB2 demo lives there behind Cloudflare. External access = move the VM to the VM LAN + Cloudflare (encrypted via the tunnel; no open ports).
- **Resolution for SCS:** prod **stays plain HTTP on the trusted main LAN** — a deliberate, defensible choice (eavesdrop risk needs an attacker already inside the trusted segment; no external surface). **No internal certificate project** — Mike evaluated the subdomain/own-cert/DNS-01 route and concluded (correctly) it's *more* complication than just using Cloudflare, which he reserves `scs-tech.net` for. If prod ever needs external access, it goes behind CF like MB2, inheriting encryption with no cert work.
- **Cloudflare vs a local cert solve different problems:** CF encrypts the *remote/external* path (easy, no ports); a local cert only adds value for *direct LAN* access on an untrusted segment. On a trusted LAN that value is low → not worth the cert hassle.
- **For other people hosting MB:** TLS is a *deployment-docs* matter, not an MB feature — they bring any front door (CF / Caddy / nginx / a subdomain on their own web server / self-signed for LAN). Nobody is forced onto Cloudflare. Covered by `docs/deployment-tls.md`.
- **Still open (separate, real):** the `ufw` host-firewall lockdown on untrusted-LAN boxes (MB2 now) so direct LAN access to the app port is blocked and the tunnel is the only way in — tracked in the security/infra TODO + `project_mb_session35_security`.

### Security posture pass (session 35, Jun 20)
Mike asked for an honest posture read + weaknesses. Audited via `manage.py check --deploy` + settings/user introspection on prod. Verdict: **app layer is solid** (session auth + LoginRequired everywhere, django-axes, role perms + per-object visibility scoping, MFA enforced, AES-256 field encryption incl. the IN token, structurally-private attachments); **the real gaps are infrastructure/operational.** Detail in memory `project_mb_session35_security`.
- **Acted on (live):** added **admin user-delete** (`UserDeleteView`, admin-only, guards against deleting self or the last superuser; SET_NULL FKs keep history) — there was none, which is why Mike couldn't remove the 3 test accounts (now deleted; only `admin` remains). Tightened secret/file perms: `.env` 640→600, `protected/` + `backups/` 775→750. Upgraded runtime CVE deps **Pillow 12.2.0 / requests 2.33.0 / cryptography 48.0.1** (9 CVEs cleared; requests carries the IN token, Pillow processes uploaded logos). Validated by the full suite on prod's Py3.12.
- **Known gaps (ranked) — UPDATED Jun 22 (see BookStack page 09 assessment):** (1) ✅ **DB backup FIXED** (SQLite snapshot → immutable B2, restore-drilled). The standing infra reds are now: **(1a) NO observability** — nothing alerts on failure (the keystone gap that hid the broken backup for two weeks); **(1b) PBS whole-VM backup BROKEN for prod** (VMID-103 collision prunes the real backup — scheduled fix). (2) **Plain HTTP on the LAN** — session cookies/credential-vault reveals cross the LAN in cleartext; the 4 `check --deploy` HTTPS warnings are correctly env-gated off because there's no TLS. Mitigated by LAN-only. **TLS deferred — Mike is gun-shy** after a past Let's Encrypt exposure; the safe path is DNS-01 on a subdomain resolving to the *private* `10.x` IP (no open ports, no public front door), but it stays off the table until he decides. (3) **SSH/OS hardening (sudo-gated, Mike to pair):** key-only SSH, fail2ban, OS patch cadence — biggest infra lever; contains the "secrets live on the box → VM compromise = full exposure" risk. (4) No inbound-attachment malware scan (ClamAV) — named, deferred. (5) **dev Py3.9 vs prod Py3.12** divergence — folds into the "easy patch/update" discussion.
- **CSP — next hardening step (Jun 23 2026):** the front-end is now fully self-hosted (no CDN), so a Content-Security-Policy is finally feasible — but NOT a quick toggle. Blockers to a *strict* CSP: (1) Alpine v3 evaluates directives via `new Function()` → needs `script-src 'unsafe-eval'` unless we migrate to Alpine's CSP build (rewrites every `x-data`/expression); (2) inline `<script>` blocks (e.g. base.html pre-paint script) need per-request nonces via middleware; (3) inline `style="..."` attributes are pervasive → `style-src` will likely need `'unsafe-inline'`. Plan: **report-only CSP first** (log violations on mb-test, decide the Alpine question with data), then enforce. Its own session. See memory `project_mb_tailwind_cdn_security`.
- **Discussion queued (Mike wants to understand first):** TLS options, an easy patch/update mechanism (align dev Python to prod + a repeatable pip-audit→upgrade→test-on-3.12 loop).

### Phase B — Invoice Ninja draft push + WO delete (session 34, Jun 20)
The billing loop closes: MB hands IN clean priced lines; **IN stays the authority** (assigns the number, owns assembly + payment). One-directional, user-triggered, fail-loud. Built on Phase A's LineItems. Shipped + live-verified (Mike configured the token and ran a real push). Suite 88→96. Detail in memory `project_mb_session34_phase_b` + `project_in_integration`.
- **`core/invoice_ninja.py`** — `requests`-based IN v5 client. `test_connection()`; `in_client_name` (type-aware: business=Client.name, residential=primary contact full name — avoids invoicing a residential client as their bare last name); `find_or_create_client` (stored id → email match → create; saves `Client.invoice_ninja_id`, link-once-don't-sync; comment warns IN replaces the whole contacts array on POST/PUT); `push_work_order` → `POST /invoices` as a **draft** (omit `number` → IN assigns; WO# → `po_number`) from **priced lines only** (unpriced excluded; blocks if none priced). Stores returned IN id+number on the WO. All failures raise `InvoiceNinjaError`, surfaced to the user; on failure nothing is saved (clean retry).
- **Config:** `SiteSettings.invoice_ninja_enabled / _url / _token` (token encrypted). Settings → Invoice Ninja card + Test Connection. **Disabled by default.** Mike's instance: **Cloud Enterprise, `https://invoicing.co`** (self-hosting was evaluated + rejected — see `project_in_integration`).
- **WO detail:** "Send to Invoice Ninja" → flips to "Invoiced → #NNNN" with a warned **Re-send**; duplicate guard is WO-scoped (`WorkOrder.invoice_ninja_id`). `invoice_ninja_ref` is editable (inline WO edit) to record drift if a draft merged into a different final invoice in IN.
- **Deferred (named, not built):** on-demand payment-status check; email-on-push (create-only by design); the **Square-as-IN-gateway** zero-code companion win (config in IN, not MB). Quote/Project approval layer still deferred (additive, no live-data clock).

### Work order hard-delete (session 34, Jun 20)
There was **never** a way to delete a work order (only tickets had one) — found when Mike couldn't delete cancelled WO-00008. Added `WorkOrderDeleteView` (admin only) + "Delete WO" toolbar button (admin only, confirm dialog). Deletes attachment **files** from storage first (rows cascade with the WO but files don't), reopens a linked **'converted'** ticket so it isn't orphaned, then cascades line items/notes/items/invoice. **Mileage entries survive** (work_order SET_NULL — travel log, not WO-owned). Warns in the success message if the WO had been pushed to IN (a draft may still exist there). Tests: cascade + ticket reopen, 403 for non-admin.

### Phase A — priced line-item primitive (session 33, Jun 20)
First step of the billing roadmap (memory `project_mb_pricing_architecture`). The schema gap that's expensive-to-reverse-with-live-data, so it lands before the Invoice Ninja push. Deployed to prod; suite 84→88.
- **`LineItem`** (new, generic/attachable via GenericFK so a future Quote reuses it; `db_table='line_items'`): `kind` labor/part, `description`, `quantity`, `unit_price` (nullable = unpriced), computed `line_total` (None when unpriced), `source_labor_item` FK→QuickLaborItem (for the report's print-description fallback), `logged_by/at`. `WorkOrder.line_items` GenericRelation (cascades on WO delete) + `line_items_total` property (sums priced lines, ignores unpriced).
- **Unify (Mike's call):** `WorkPerformed` was migrated INTO `LineItem` and **deleted**. Migration 0059 copies every WorkPerformed → labor LineItem (price blank — price-less history isn't backfilled), 0060 drops the table. The log/custom/edit/delete endpoints + the Work Performed UI now operate on LineItem. View class names + URL names kept (`work_performed_*`) to avoid churn — they now act on LineItem.
- **`QuickLaborItem.default_price`** (optional) prefills a labor line's price when the button is clicked. New Default Price column in Settings → Quick Labor.
- **UI:** WO detail Work Performed section shows labor + parts with per-line qty/price + a running Total; custom-entry form gained kind (labor/part) + qty + price; repair report prints priced lines + total. **No "estimate" label** — Mike didn't want it (he's unconcerned about the UI implying authority; the boundary is enforced by Phase B pushing a *draft* to IN).
- **Authority boundary intact:** MB captures + totals prices; Invoice Ninja stays the system of record. Phase B (the IN draft-push) builds on these priced lines. See `project_in_integration`.
- **Migration gotcha (fixed):** the data migration uses `ContentType.objects.get_or_create` + an early-return on empty DB, because ContentTypes aren't populated mid-migration on a fresh build (test DB).

### Attachment security review — acted on (session 32, Jun 20)
Audited inbound/served attachment handling against a 4-point checklist; found and fixed real issues. All live on prod + verified; suite 80→84. Memory `project_mb_attachment_security_review` + `project_mb_session32`.
- **🔴 Found: attachments were publicly served.** nginx had `location /media/ { alias .../media/; }` → every file under `media/attachments/...` was reachable by URL with **no login**, bypassing the auth download view (paths guessable: sequential ids + original filename). Prod is LAN-only so LAN-exposure; **MB2 demo is internet-facing** (see below).
- **Structural fix (not a band-aid):** attachments now stored under `PRIVATE_MEDIA_ROOT = BASE_DIR/protected`, **outside MEDIA_ROOT**, via `PrivateMediaStorage` (FileSystemStorage subclass resolving `location` dynamically so tests isolate it; passed as a callable to stay out of migrations). nginx structurally can't serve them — the authenticated `AttachmentDownloadView` is the only path. No nginx edit needed (the dir it aliased is now empty). Existing files relocated `media/attachments → protected/attachments` per deploy target (one-time, manual).
- **🟠 Fixed IDOR:** `AttachmentDownloadView` now calls `_can_access_attachment()` — resolves the owning Ticket/TicketReply/WorkOrder/WorkOrderNote and applies `_scope_tickets_for`/`_scope_assignable_for` (admins see all). A tech can no longer fetch any attachment by id past the ticket-visibility scoping.
- **🟠 Fixed inbound parity:** `fetch_inbound_email._save_attachments` now enforces the blocked-extension list + size cap (the UNtrusted path previously enforced neither, while manual upload did); skips are `logger.warning`, not silent.
- **Kept-safe (today):** the download view forces `as_attachment=True` for everything → no inline XSS via emailed `.html`/`.svg`. Content-sniffed inline image rendering is deferred to the widget's screenshot feature (must sniff by content, never the attacker-supplied `mime_type`).
- **✅ MB2 DEMO (10.58.35.223) — DONE same day:** pulled to current, migs 0054–0057, restarted, verified (404 on old /media path, 302 app). 0 files to relocate. Demo is also behind Cloudflare Access (every request 302s to CF auth first) → double-gated. No outstanding attachment-security work on either box.
- **Deferred ceiling (named, not done):** malware scanning of inbound attachments (ClamAV); optional nginx `deny /media/attachments/` as belt-and-suspenders; force-download protects the browser session, not the tech's machine (endpoint AV's job).

### Device/WO hardware specs + navigation fixes (session 31, Jun 20)
Usability pass surfaced while onboarding Unsorted tickets and entering device data. All live on prod; suite 71→80.
- **Ticket device dropdown scoped to client** — onboarding an Unsorted/Unverified ticket no longer shows
  every device in the system. `TicketForm` scopes the `device` queryset to the effective client (same
  pattern as `contact`), and the client→contacts HTMX cascade (`TicketContactsByClientView`) now also
  returns an **out-of-band `<select id="id_device">`** so the device list re-narrows live on client change.
- **Device hardware specs** — added free-text `cpu`/`ram`/`storage` to `Device` (migration 0055). Free
  text on purpose (MSP values vary too widely to constrain; structured number+unit deferred unless
  sorting/filtering is needed). Shown on the device form ("Hardware Details") + device detail; OS is now
  also displayed on detail (was captured, never shown).
- **WO snapshot + sync-back** (migration 0056) — `WorkOrder` gained `cpu`/`ram`/`storage`. On creation
  (`save()` when `_state.adding`, via `apply_device_specs()`) the WO copies the device's specs as an
  **as-serviced** record — covers the create view, ticket-convert, and any programmatic create. Editing
  specs on the WO syncs back to the device master (`sync_specs_to_device()` from `WorkOrderUpdateView`);
  reassigning the device on the inline panel re-snapshots (`apply_device_specs(force=True)`). Past WOs
  stay frozen — later device edits don't rewrite history. Shown on WO form/detail/print. Only the
  *mutable* specs are snapshotted; manufacturer/model/serial stay live read-through (device identity).
  **Note:** existing devices/WOs are blank until filled; snapshot only fires on new WO creation.
- **Device-detail back-link fix** — "← Devices" landed on the device list, a dead end (Devices isn't in
  the nav). Now reads "← <client>" and returns to the device's client (client-centric model). The device
  list is still reachable from the dashboard "Devices on File" tile — kept by choice, no nav entry needed.

### T2/Helpdesk Buttons ingestion + Unsorted triage bucket (session 30, Jun 19)
**Tier2Tickets is the button-press front door, moved off OSTicket's API onto T2's Email Connector.**
T2 posts every button ticket from a fixed no-reply relay **`email-connector@tier2tickets.com`** with
the real end user carried in a **forwarded `From:` header inside the body** (plus report/remote links,
hostname, username, businessName, `[message]`, `[selections]`). Subject is `Fwd: E.xxxxx <subj>` —
that `E.xxxxx` is T2's own ticket ID (kept on purpose; clients are told it) and does NOT match MB's
`TICKET_RE`, so button tickets always create a new ticket, never mis-thread.
- **Adapter** (`fetch_inbound_email`): when the envelope sender ∈ `_T2_RELAY_ADDRESSES`,
  `_extract_forwarded_sender(body)` parses the first `From:` line **from the raw body before quote
  stripping**, and resolution runs on the REAL address. Unparseable → fall back to the relay address
  **and `logger.warning`** (fail loud). Blocked-sender + Message-ID dedup checks run *after* the unwrap
  so they apply to the real sender. **The reliable identity key is the contact email, not businessName**
  (businessName is first-use-only at SCS). T2 is ingestion-only — once the ticket exists, replies flow
  support-email ↔ contact directly; MB needn't know T2 was involved. Device/hostname extraction was
  deliberately deferred.
- **Unsorted/Unverified triage bucket** (migration 0054): an unmatched inbound sender no longer
  auto-creates a junk named client. The old per-person/free-email + domain-grouping fallback is GONE
  (`_FREE_EMAIL_DOMAINS` deleted). Instead `Client.is_unsorted` + `Client.get_unsorted()` route the
  ticket under one system "Unsorted / Unverified" client (real name/email still kept on the contact for
  reply routing + onboarding). A configured `inbound_default_client_name` catch-all still overrides.
  **Never hide a ticket** — it's visible and workable; only the *client record* is held pending triage.
  Surfacing: admin dashboard card "Unsorted — needs triage: N" → `/tickets/?triage=1` (indigo banner).
  Bucket is excluded from the Active-Clients stat and **cannot be deleted/deactivated** via the UI.
  **Onboard** = existing Edit-ticket reassignment (change client → contact dropdown cascades);
  **reject** = existing ticket delete + Settings → BlockedSenders (v1; no combined button). Policy is
  uniform for ALL unmatched inbound, not just T2. `reset_operational_data` wipes the bucket with
  everything else and `get_unsorted()` recreates it lazily on the next unmatched inbound.

### Design intent to preserve (don't "fix" these — they're deliberate)
- A completed Work Order must **never** auto-close its Ticket. The ticket drives the
  human-facing interaction and a person resolves it manually after real contact.
  `AUTO_RESOLVE_TICKET_ON_WO_CLOSE` stays off by default. (The close-dependency block in
  `TicketUpdateView` is correct and working — only the *delete* guard, item 1 above, is broken.)
- A Work Order does **not** require a Ticket — work doesn't always arrive that way. But if
  a ticket came first, it also owns the last interaction.

---

## Current App State (What's Working)

The app is running locally at `http://localhost:8000`. All views require login.

**Working URLs:**
- `/` — Dashboard (stats, open work orders, recently closed)
- `/account/login/` — Login page (two_factor styled)
- `/account/two_factor/` — Account security / MFA enrollment
- `/account/two_factor/setup/` — TOTP setup wizard (QR code)
- `/account/two_factor/backup/tokens/` — Backup tokens (admin only, printable)
- `/work-orders/` — Work order list (search, filter, pagination)
- `/work-orders/new/` — Create work order (native form, includes service type)
- `/work-orders/<id>/` — Work order detail (HTMX inline notes, checklist, stopwatch, + Mileage button for onsite)
- `/work-orders/<id>/edit/` — Edit work order
- `/work-orders/<id>/add-time/` — HTMX: add minutes to time_spent (stopwatch log)
- `/work-orders/<id>/add-mileage/` — Mileage form launched from WO (pre-filled, Google Maps Calculate)
- `/clients/` — Client list (search, active filter)
- `/clients/new/` — Create client
- `/clients/<id>/` — Client detail (contacts, devices, work history)
- `/clients/<id>/edit/` — Edit client
- `/devices/` — Device list (search, type filter)
- `/devices/new/` — Create device
- `/devices/<id>/` — Device detail (repair history)
- `/devices/<id>/edit/` — Edit device
- `/mileage/` — Mileage log (month filter, running total, edit links)
- `/mileage/new/` — Log mileage (native form)
- `/mileage/<id>/edit/` — Edit mileage entry
- `/mileage/calculate/` — Server-side Google Distance Matrix proxy (POST, JSON)
- `/tickets/` — Ticket list (search, status filter, overdue indicator)
- `/tickets/new/` — Create ticket (with help topic + SLA plan selectors)
- `/tickets/<id>/` — Ticket detail (HTMX inline replies, convert-to-WO, overdue badge + ack)
- `/tickets/<id>/edit/` — Edit ticket
- `/tickets/<id>/convert/` — Convert ticket to work order
- `/tickets/<id>/lock/release/` — Release ticket lock (called via JS beforeunload)
- `/tickets/<id>/lock/status/` — Lock status fragment (HTMX polled every 30s)
- `/tickets/<id>/links/add/` — Link two tickets (HTMX)
- `/tickets/<id>/links/remove/` — Unlink tickets (HTMX)
- `/tickets/<id>/acknowledge-overdue/` — Acknowledge overdue with required note (HTMX)
- `/attachments/<id>/download/` — Secure authenticated file download
- `/queues/` — Ticket queue list (system + personal queues)
- `/queues/<id>/` — Queue detail (filtered ticket list)
- `/queues/new/` — Create queue
- `/queues/<id>/edit/` — Edit queue
- `/reports/` — Reporting & analytics (8 reports, Chart.js, CSV export per report)
- `/sidebar/` — HTMX fragment: my tickets + my work orders for sidebar
- `/kb/` — Knowledge base list (search, category + type filters)
- `/kb/new/` — Create KB article (staff/can_manage_kb only)
- `/kb/<id>/` — KB article detail
- `/kb/<id>/edit/` — Edit KB article
- `/users/` — User management (admin only — shows all users with MFA status)
- `/users/<id>/reset-mfa/` — Admin MFA reset for lost device recovery (POST)
- `/admin/` — Django admin (full access, staff only)

- `/work-orders/<id>/print/` — Repair Report (print-optimized, opens new tab)
- `/work-orders/<id>/credentials/` — HTMX: save device credentials inline
- `/work-orders/<id>/billing/` — HTMX: update billing state (quick-action + full edit)
- `/work-orders/<id>/log-labor/<item_id>/` — HTMX: log Quick Labor Work Performed entry
- `/work-performed/<id>/delete/` — HTMX: remove Work Performed entry
- `/clients/<client_id>/contacts/new/` — Create contact (form POST, redirects back)
- `/contacts/<id>/edit/` — Update contact with multiple phones
- `/contacts/<id>/delete/` — Delete contact
- `/settings/` — Native Settings UI (admin only, 6 tabs)

**What still requires admin panel:**
- Superuser / `is_staff` flag management (by design — can't self-escalate in native UI)
- Emergency data fixes for records stuck in bad state

**Note**: All routine workflow actions and all configuration are now in native MB UI. Django admin is a break-glass tool only.

---

## Vision & Philosophy

Murphy's Bench is **internal-first, self-hosted software** for small field service businesses (MSPs).

### Core Principle
Build one thing well: a self-hosted repair tracking system that runs on a business's internal network. Other companies can self-host it on their infrastructure.

### Workflow
```
Ticket (intake + replies) → Triage → Work Order (repair) → Notes/Checklist → Closed → Invoice Ninja
```

### Phase 1: SCS Internal (Current)
- **Focus**: Get SCS's workflow working perfectly
- **Scope**: Ticketing, work orders, device tracking, mileage, email integration, reporting
- **Deployment**: Internal network
- **Success**: SCS techs prefer this to the legacy PHP app

### Phase 2: Integrations & Polish (Future)
- Org-level credentials vault (OrgCredential + CredentialAccessLog)
- Device-level credentials (password field on Device, encrypted)
- Email Template Manager UI, Status Management UI, Data Management (import/export/deleted/reset)
- Financial reporting (invoiced/paid/outstanding by client)
- Invoice Ninja API bridge
- Email OAuth2 (Gmail/Office 365)
- Departments, Teams, Auto-routing
- Customer self-service portal
- REST API (for Taskbar Utility App / Clover integration)

### Phase 3+: Multi-Tenancy (Speculative)

---

## Architecture

### Tech Stack
- **Backend**: Python 3.12 / Django 4.2.30
- **Frontend**: Tailwind CSS (compiled & self-hosted at `static/css/app.css` via the standalone CLI — `scripts/build_css.sh`, `tailwind.config.js`; built on deploy, no Node), HTMX + Alpine.js (self-hosted/pinned in `static/js/`). **Fully CDN-free as of Jun 23 2026** — including the admin reports page (Chart.js 4.4.0 + html2pdf 0.10.1 also vendored to `static/js/`).
- **Database**: SQLite (dev and production; PostgreSQL supported via DB_ENGINE but not used)
- **Auth**: Django session auth + django-two-factor-auth (TOTP), LoginRequiredMixin on all views

### Project Structure
```
murphys-bench/
├── CLAUDE.md                    # This file — read first each session
├── TODO.md                      # Full roadmap and build order
├── manage.py
├── requirements.txt
├── murphys_bench/              # Django project settings
│   ├── settings.py
│   └── urls.py
├── core/                        # Main app
│   ├── models.py               # All 32 data models
│   ├── views.py                # All views
│   ├── urls.py                 # Core URL patterns
│   ├── forms.py                # All forms
│   ├── admin.py                # Admin customization
│   ├── middleware.py           # MFAEnforcementMiddleware
│   ├── email_utils.py          # Outbound email helpers
│   ├── management/commands/
│   │   ├── check_sla_overdue.py    # Cron: flag overdue tickets
│   │   └── fetch_inbound_email.py  # Cron: poll IMAP/POP3 mailbox
│   └── templates/core/
│       ├── base.html
│       ├── dashboard.html
│       ├── work_order_list.html
│       ├── work_order_detail.html  # Stopwatch timer, + Mileage button (onsite)
│       ├── work_order_form.html    # Includes service_type field
│       ├── client_list.html
│       ├── client_detail.html
│       ├── client_form.html
│       ├── device_list.html
│       ├── device_detail.html
│       ├── device_form.html
│       ├── mileage_list.html       # Edit links per row
│       ├── mileage_form.html       # General mileage create/edit
│       ├── mileage_wo_form.html    # WO-linked mileage with Calculate button
│       ├── user_list.html          # Admin user management + MFA status
│       ├── ticket_list.html
│       ├── ticket_detail.html
│       ├── ticket_form.html
│       ├── ticket_convert.html
│       ├── kb_list.html
│       ├── kb_detail.html
│       ├── kb_form.html
│       ├── queue_list.html
│       ├── queue_detail.html
│       ├── queue_form.html
│       ├── reports.html
│       └── partials/
│           ├── note_item.html
│           ├── checklist_item.html
│           ├── ticket_reply_item.html
│           ├── ticket_lock_banner.html
│           ├── ticket_linked_list.html
│           ├── attachment_list.html
│           ├── overdue_badge.html
│           ├── overdue_ack_form.html
│           ├── wo_time_spent.html
│           ├── billing_card.html
│           └── sidebar_content.html
├── templates/two_factor/        # Tailwind overrides for django-two-factor-auth
│   ├── _base.html               # Extends Murphy's Bench base.html (profile pages)
│   ├── _base_focus.html         # Standalone centered card (login/setup pages)
│   ├── _wizard_forms.html
│   ├── _wizard_actions.html
│   ├── core/login.html
│   ├── core/setup.html
│   ├── core/setup_complete.html
│   ├── core/backup_tokens.html  # Printable backup token list
│   ├── profile/profile.html     # Account security page
│   └── profile/disable.html
├── accounts/                    # Auth app
└── docs/
    ├── database-schema.md
    ├── ticketing-design.md
    └── next-session-prompt.md
```

### Data Models (34 current, migrations through 0051)
- **Role** — permission role with 16 boolean flags; seeded: Administrator, Technician
- **TechSkill** — skill tags M2M on User; captured for future skill-based routing
- **User** — extended Django user; role CharField (legacy) + role_obj FK to Role + skills M2M
- **Client** — company/customer
- **Contact** — person at a client company
- **Device** — equipment being serviced
- **SLAPlan** — response deadline config (grace_period_hours, overdue alerts toggle)
- **HelpTopic** — ticket classification with optional default SLA
- **Ticket** — initial service request; statuses: new, open, in_progress, waiting_on_customer, resolved, closed, converted
- **TicketReply** — threaded conversation on a ticket (customer_visible or internal)
- **WorkOrder** — repair job; service_type (in_shop/onsite/remote); time_spent_minutes; linked to originating ticket via OneToOne
- **WorkOrderNote** — customer-visible or internal notes on a work order
- **WorkOrderItem** — checklist items, parts, time entries
- **Invoice** — billing state tracker; OneToOne on WorkOrder; billing_status enum (uninvoiced/invoiced/paid/paid_direct/disputed); amount, dates, payment_method, notes; auto-created on WO creation via signal
- **Mileage** — travel logging; trip_type (one_way/round_trip); optionally linked to WorkOrder
- **RepairType** — category (Laptop Repair, Desktop Repair, etc.)
- **Checklist** — template task list linked to a repair type
- **ChecklistItem** — individual task in a checklist template
- **CannedResponse** — template notes for common situations
- **TicketLock** — collision avoidance; OneToOne on Ticket, 10-min expiry
- **TicketLink** — links related/duplicate tickets; unique_together on (ticket_a, ticket_b)
- **SiteSettings** — singleton; SMTP, inbound email, attachment config, Google Maps API key + shop address, require_mfa toggle
- **Attachment** — GenericFK to Ticket/TicketReply/WorkOrder/WorkOrderNote; local or S3 storage
- **EmailTemplate** — trigger-based outbound email templates (4 triggers, seeded with defaults)
- **SuppressedAddress** — exact email addresses that never receive automated email
- **EmailSendLog** — audit trail for every outbound send attempt
- **InboundEmailLog** — audit trail for every inbound message fetched
- **Notification** — per-user in-app alert (sidebar bell + unread count); first producer is internal tech-to-tech messaging; generic for future producers
- **KBCategory** — knowledge base category (admin-managed)
- **KBArticle** — KB article; types: troubleshooting / how_to / vendor / internal; is_restricted flag
- **TicketQueue** — Saved ticket filters; owner=null = system queue; filter_criteria JSONField
- **DashboardTile** — Configurable dashboard tile; row (ticket/workorder), status_filter, visible_to
- **CustomField** — Admin-defined extra fields for Tickets or Work Orders; scoped to HelpTopic or RepairType
- **CustomFieldChoice** — Options for select-type CustomFields
- **CustomFieldValue** — EAV storage: one row per (object, field) pair; GenericForeignKey

---

## Ticketing System Design

See `docs/ticketing-design.md` for full detail.

### Ticket Statuses
`new` → `open` → `in_progress` → `waiting_on_customer` → `resolved` → `closed`
Also: `converted` (converted to Work Order — read-only after this point)

### Ticket → Work Order Rules
- A ticket linked to an open WO **cannot** be closed/resolved — hard block
- When the WO closes, ticket shows a prompt: "WO complete — ready to resolve" — tech closes manually
- `AUTO_RESOLVE_TICKET_ON_WO_CLOSE` admin setting (default **off**)
- Ticket remains in system after conversion — full history retained

---

## Phase 1 Feature Status

### ✅ Batch 1 — Collision Avoidance, WO/Ticket Dependency, Ticket Linking
### ✅ Batch 2 — Audit Log, Attachments
### ✅ Batch 3 — Outbound Email, Auto-Responder
### ✅ Batch 4 — SLA Plans, Help Topics/KB, Roles & Permissions + Stopwatch timer
### ✅ Batch 5 — Inbound Email (IMAP/POP3, threading, quote strip, attachments)
### ✅ Batch 6 — Custom Queues, Persistent Sidebar, Enhanced Dashboard, Reporting
### ✅ Batch 7 — Custom Fields (EAV, scoped to HelpTopic/RepairType, all field types)
### ✅ Batch 8 — MFA (TOTP, enforcement toggle, backup tokens, admin reset, user management panel)
### ✅ Batch 9 — Mileage native form, service_type on WO, Google Maps auto-calculate

### ✅ Batch 10 — Legacy App Gap Closure (complete — session 8)
- **P1**: Repair Report (`/work-orders/<id>/print/`), Company Info in SiteSettings, Quick Labor / Work Performed (HTMX)
- **P2**: Credentials on WO (masked), Client Type badge (Residential/Business), Multiple phones per Contact (Alpine.js dynamic rows), Contact notes + receives_email, Invoice Ninja Ref # deferred to Phase 2
- **P3**: Native Settings UI at `/settings/` — 6 tabs: Company, Outbound Email, Inbound Email, Attachments, Security, Mileage

### ✅ Batch 11 — Foundational Client-Centric Rebuild (sessions 10–11 — COMPLETE)

Full spec in `docs/batch-11-plan.md`. Identified by complete side-by-side audit of the legacy
PHP app (SCS Repair Tracker) vs Murphy's Bench. Core problem: Murphy's Bench treats Clients,
Contacts, Devices, and Work Orders as peer objects. The legacy app — and correct workflow — is
**client-centric**: everything flows through the client.

**Priority 1 — Device + Client Hub:**
- Device model: add `os`, `os_version`, `condition_at_intake`, `assigned_contact` FK, "Save & Create WO" button. Remove Device from top-level nav.
- WorkOrder: add `contact` FK (nullable) — "whose WO is this?" Shown in WO History, WO detail header, WO create/edit form.
- Client detail as hub: single-column layout, per-contact "+ WO" button, inline device add, phone custom label + type dropdown, inline client type edit, Set Primary Contact.
- Client edit: Deactivate (block if WOs on delete) + Permanently Delete (type-to-confirm).

**Priority 2 — WO Detail + Print:**
- Unified black action toolbar: View Client | Edit Client | Edit Device | Edit WO | WO History | Repair Report | Claim Ticket | Email Report | Status ▼
- Client info + Device info (OS, serial, condition) on WO page.
- Days Open counter, Completed Date, Invoice Ninja Ref #.
- Work Performed entries show bold label + description + timestamp.
- Pre/Post Checklist collapsed by default. Credentials "+ Add note" field.
- Repair Report: add OS/version/condition, note timestamps, signature lines, footer.
- Claim Ticket: same template, `?type=claim` changes title only.

**Priority 3 — Native Settings UI Expansion:**
- Repair Types: native CRUD with categories + ▲/▼ reorder. Needs new `RepairTypeCategory` model.
- Canned Responses: two Note Streams (Customer Notes / Tech Notes Internal), categories per stream, CRUD, picker on WO detail.
- Quick Labor: native CRUD (currently Django admin only).
- Checklist Items: model change — flat bank scoped by device type (not per-repair-type). Migration required.
- Status Colors + Site Colors: hex inputs + live preview, stored in SiteSettings, rendered as CSS variables in base.html.
- Company Info: split address into Line 1, Line 2, City, State, Zip (both SiteSettings and Client model). Report Header Preview.
- Display Settings: browser-local UI preferences (localStorage) — nav/sidebar/content font size, card density (Compact/Normal/Comfortable).

**Decisions locked in session 9:**
- Permanently Delete blocks if client has WOs; offers Deactivate instead
- Address: 5 fields (Line 1, Line 2 optional, City, State, Zip) — no country field
- Existing address data migrates to Line 1; user cleans up manually
- Colors stored in SiteSettings; rendered as `<style>` block of CSS variables in base.html
- RepairTypeCategory model needs to be created with sort_order field
- Device assigned_contact: server-side queryset filter (client_id from URL param); no HTMX cascade needed (standalone Device page being removed)

### ✅ Session 13 — Cross-Visibility + Bug Fixes (session 13 — COMPLETE)

- **Cross-visibility panels**: Open tickets panel on WO detail; open WOs panel on ticket detail — status, last note/reply preview, one-click navigation
- WO detail toolbar: linked ticket shown as clickable purple pill (← TKT-XXXXX)
- Converted tickets stay visible in sidebar, dashboard "My Open Tickets" tile, and cross-visibility panels until resolved/closed
- History tab removed from ticket detail (consistent with WO detail)
- Sidebar: shows last reply/note preview instead of subject/description; falls back gracefully if no notes
- Mileage Calculate button: fixed CSRF token for production (was silently failing in prod)
- Google Maps API confirmed working from production server (WAN IP restriction set in Cloud Console)

### ✅ Session 26 — HTML Email, Signatures, Inbound Fixes (session 26 — COMPLETE)

- **HTML email + signatures**: `EmailMultiAlternatives`, `base_email.html` with header/body/signature/footer. `EmailSignature` model (migration 0044), per-template FK override, default fallback. Settings → Email Templates has full signature CRUD.
- **CID inline logo**: Logo read from disk, attached as `MIMEImage Content-ID: logo`. Falls back to company name text. Switches to public URL when Cloudflare is live.
- **Quick status change on ticket detail**: dropdown + Set in Quick Actions; `TicketStatusUpdateView`.
- **Ticket client reassignment fix**: uses POSTed `client` value for contact queryset.
- **Residential client labels**: Alpine.js reactive label swap on client form.
- **Free email domain fix**: `_FREE_EMAIL_DOMAINS` — Gmail/Yahoo/etc. get per-person clients.
- **Inbound threading fix**: `TICKET_RE` matches sequential numbers (`TKT-00005`).
- **Security hardening**: django-axes, proxy SSL headers, CSRF trusted origins, Lax cookie, password min 12.
- **Inbound email timer**: systemd units written to `/tmp` — Mike to install with sudo.

### ✅ Session 22 — UI Polish, Dark Mode, KB Markdown (session 22 — COMPLETE)

- **Search bars inline**: Tickets, Work Orders, Clients, Mileage, KB lists — filter controls moved into page header bar. Fixed missing technician options in WO assigned_to dropdown.
- **Mileage decimal fix**: `floatformat:1` on total miles display.
- **Ticket reply type**: Radio buttons instead of dropdown. Removed redundant "Add Reply ↓" Quick Actions button.
- **KB Markdown rendering**: `markdown` library, `markdownify` template filter, Tailwind typography plugin (now compiled into the self-hosted stylesheet). Articles render headings/bold/lists/code/tables from pasted `.md` files.
- **KB Categories in Settings**: Native CRUD tab — no Django admin needed.
- **Dark mode**: Per-user toggle in sidebar footer (moon/sun icon), persisted to `localStorage`. CSS override strategy in `base.html` covers all common surfaces, text, borders, inputs, tinted panels (blue-50/yellow-50/green-50), prose.
- **My Work sidebar removed**: Was redundant in practice.
- **Dashboard stat cards**: Active Clients + Devices on File are now clickable links.
- **Reports page overhaul**: Per-section CSV/Print/PDF dropdowns in header. Print uses hidden iframe (no popup tab). PDF uses html2pdf.js. Mileage miles floatformat:1 in template and CSV.

### ✅ Session 21 — Ticket Contact FK, Email Fixes, User/Role Management (session 21 — COMPLETE)

- **Ticket contact FK** (migration 0037): `Ticket.contact` nullable FK to `Contact`. Reply emails route to `ticket.contact.email` first, fall back to primary contact. Inbound emails auto-set contact from matched sender.
- **HTMX contact cascade on ticket form**: Client select dynamically loads contacts. Endpoint: `GET /tickets/contacts-by-client/?client=<id>`.
- **Reply resend**: Each customer-visible reply has a "Resend" button — pick any client contact or type a custom address.
- **CC on replies**: Reply form shows a CC field (comma-separated) when Customer Visible is selected.
- **Native User management**: `/users/new/`, `/users/<pk>/edit/`, `/users/<pk>/set-password/` — full CRUD, no Django admin needed.
- **Native Role management**: `/roles/` — list with ✓/✗ permission grid, create/edit/delete. 17 permission flags. System roles protected.
- **Users + Roles in Settings sidebar**: Both at the bottom of Settings nav, with "← Settings" back links.
- New template filters: `attr` (getattr on model), `getfield` (form[name]) — in `mb_icons.py`.

### ✅ Session 20 — Vertical Left Sidebar Nav (session 20 — COMPLETE)

- **Replaced horizontal top nav** with fixed left sidebar (`w-64` expanded / `w-16` collapsed to icon-only)
- **Logo** fills sidebar header at top (no company name text alongside it)
- **8 primary nav links** with icons: Dashboard (home), Work Orders (list), Clients (building), Tickets (ticket), Queues (funnel), Mileage (map-pin), KB (book-open), Reports (chart-bar). All `text-base` with active-page highlight.
- **My Work section** (HTMX accordion with tickets + WOs) integrated into scrollable sidebar middle — always loaded, hidden when collapsed
- **Footer**: Admin (admin-only → `/settings/`), Log Out. Security removed from sidebar.
- **Collapse toggle** (chevron) at bottom — state persisted to `localStorage`; pre-Alpine inline script + CSS attribute (`data-sidebar-collapsed`) prevents layout flash on page load
- **8 new icons** added to `mb_icons.py`: `home`, `map-pin`, `chart-bar`, `funnel`, `chevron-left`, `book-open`, `shield`, `logout`
- No model/migration changes. Deployed to production.

### ✅ Session 19 — Status Management UI (session 19 — COMPLETE)

- **`StatusDefinition` model**: `entity_type` (ticket/workorder), `slug`, `label`, `color` (hex bg), `is_system`, `sort_order`, `is_active`
- **Migration 0036**: AlterField removes choices= from Ticket.status and WorkOrder.status (max_length→50); seeds 13 core statuses with default colors; RunPython after CreateModel
- **Template tag suite** in `mb_icons.py`: `status_badge`, `status_label`, `status_color` — 2-min module-level cache, graceful fallback for unknown slugs. `invalidate_status_cache()` called after any CRUD change.
- **11 templates updated**: all hardcoded status badge `{% if status == ... %}bg-X{% endif %}` patterns replaced
- **WorkOrderForm + TicketForm**: status field overridden in `__init__` to load choices from StatusDefinition — custom statuses appear in dropdowns automatically
- **WorkOrderListView, TicketListView, WorkOrderDetailView**: pass status choices via context
- **Settings → Statuses tab**: two tables (Ticket / Work Order), color picker on each row, inline edit form (Alpine.js toggle), custom status add form at bottom, system statuses get "Edit Color" only
- **email_utils.py**: `status` context var resolved via StatusDefinition instead of `get_status_display()`
- Migration 0036 applied to production; all changes live

### ✅ Session 18 — Device Credentials Vault (session 18 — COMPLETE)

- **Device-level credentials**: `device_username`, `device_password`, `credential_notes` (AES-256 encrypted) added to `Device` model
- **`DeviceCredentialAccessLog`** model — logs every reveal (field + user) and edit
- **`can_view_device_credentials`** flag on `Role` (Administrator=True, Technician=False by default, configurable)
- **HTMX eye-reveal card** on device detail right column — masked by default, eye icon triggers HTMX GET, logs access
- Admin always sees edit form (Alpine.js toggle). Users with flag can reveal. Others see "contact admin" message.
- Migration 0035 applied to production. Administrator role seeded on prod.

### ✅ Session 17 — Phase 2 Foundations (session 17 — COMPLETE)

- **Invoice CSV export**: `InvoiceExportView` at `/clients/<pk>/invoices.csv` — all invoices for a client, optional `?status=` filter. CSV button on client detail WO History header.
- **Icon audit**: 10 new icons added to `mb_icons.py` (x-mark, exclamation-triangle, lock-closed, user, key, document-text, chevron-up/down/right, arrow-down-tray, eye). All emoji/text symbols replaced across templates. Fixed arrow-down-tray silently rendering nothing.
- **Billing financial summary on Reports page**: Invoiced/Collected/Outstanding metric cards + outstanding-by-client table with CSV links. Billing CSV export at `/reports/csv/billing/`.
- **Org credentials vault**: `OrgCredential` + `CredentialAccessLog` models (migration 0034). Settings → Credentials tab. AES-256 encrypted username/password/notes. HTMX eye-reveal logs every access. CRUD with admin-only flag. Every view/edit/delete written to audit log.
- **Email Template Manager**: Settings → Email Templates tab. Native UI for all 4 `EmailTemplate` triggers. Editable subject/body (monospace), active toggle, variable reference panel (`{% verbatim %}`), last-updated timestamp. Auto-creates inactive defaults on first visit.
- **Team workload widget**: Dashboard (admin only) — Team Workload table showing open WOs + tickets per tech, sorted by total load, counts link to filtered lists.
- **Technician performance report**: Reports page — WOs in period, completed count, completion % (color-coded), avg resolution hours, current open WOs. CSV export at `/reports/csv/tech_perf/`.
- **Doc sweep**: MB_UI_UX_Analysis.md content merged into CLAUDE.md + TODO.md. Stale admin panel entries cleaned up.
- Production deployed: migration 0034 applied, all changes live.

### ✅ Session 16 — Invoice Model (session 16 — COMPLETE)

- **`Invoice` model**: OneToOne on WorkOrder (`db_table = 'invoices'`). Fields: `billing_status` (uninvoiced/invoiced/paid/paid_direct/disputed), `amount`, `invoiced_date`, `paid_date`, `payment_method`, `notes`
- **Signal**: `post_save` on WorkOrder auto-creates Invoice on WO creation
- **Migration 0033**: CreateModel + `backfill_invoices` RunPython for existing WOs; applied to production
- **`WorkOrderBillingUpdateView`**: HTMX POST. Quick-action mode (just `billing_status`): updates status + auto-sets dates on first transition. Full edit mode (`full_edit=1`): updates all fields. Returns `billing_card.html` partial.
- **`billing_card.html`** partial: display mode shows status badge + amount + dates + quick-action buttons (contextual per status). Edit mode (Alpine.js toggle): full form. HTMX `hx-swap="outerHTML"` on `#billing-card`.
- **WO detail**: billing card inserted in right column between "Update Work Order" and "Device Credentials"
- **Client detail**: outstanding balance badge (yellow pill) next to "Work Order History" heading — sum of `uninvoiced`+`invoiced` WO amounts
- URL: `/work-orders/<pk>/billing/` → `wo_billing_update`
- Production deployed: migration 0033 applied, Gunicorn reloaded

### ✅ Session 15 — Visual Polish (session 15 — COMPLETE)

- **Color-coded dashboard tiles**: left-border accent per status (Blue=active, Yellow=waiting, Red=overdue, Green=completed). Color computed in `_tile_color()` from `status_filter` and `link_url`.
- **SVG icons replacing emoji**: all dashboard tiles and quick stats row now use Heroicons outline via `{% icon name size %}` templatetag (`core/templatetags/mb_icons.py`)
- **Device type icon grid**: replaced Device Type dropdown on device form with 2-row × 4-col Alpine.js button grid (Laptop, Desktop, Mobile, Tablet, Server, Printer, Other). Selected state highlighted blue.
- Migration 0032: data migration converting emoji icon values → icon name strings in DashboardTile
- Production deployed: migrations 0031 + 0032 applied, `FIELD_ENCRYPTION_KEY` set in prod `.env`, Gunicorn reloaded

### ✅ Session 14 — Credential Encryption + Billing Architecture (session 14 — COMPLETE)

- **Credential encryption (migration 0031)**: `WorkOrder.device_username`, `device_password`, `device_pin`, `credential_notes` and `SiteSettings.email_password`, `inbound_password` now AES-256 encrypted at rest via `django-encrypted-model-fields` (Fernet symmetric encryption)
- `FIELD_ENCRYPTION_KEY` added to `murphys_bench/settings.py` — reads from env, dev fallback only
- `encrypted_model_fields` added to INSTALLED_APPS and `requirements.txt`
- `.env.example` updated with key generation instructions and warning
- RepairShopCRM comparative UI/UX analysis completed — documented in `MB_UI_UX_Analysis.md`
- **⚠️ Production deployment of migration 0031 is PENDING** — must set `FIELD_ENCRYPTION_KEY` in production `.env` BEFORE pulling. Must be done together. See `memory/project_credential_encryption_deploy.md`.

### ✅ Batch 12 — Production Deployment + WO Detail Polish (session 12 — COMPLETE)

**Deployment:**
- Ubuntu 24.04 VM on Proxmox (10.58.58.82), SQLite, Gunicorn + Nginx, systemd
- Python 3.12 (Ubuntu 24.04 default), SSH key auth, config data migrated via dumpdata/loaddata

**WO Detail improvements:**
- Inline editing: Device card (reassign device), Details card (repair type, assigned to, scheduled date, contact, invoice ref)
- Custom repair type on the fly (＋ Custom… option in Details edit, get_or_create)
- Attachment upload form in Attachments tab (WorkOrderAttachmentUploadView)
- History tab removed from WO detail
- Work Performed redesign: editable entries (pencil/trash SVG icons), custom log form, collapsible Log Work buttons
- WorkPerformed model: labor_item nullable, custom_label + notes fields, ordered by logged_at
- Pre/Post Checklist: pre_check + post_check fields on WorkOrderItem, auto-saving dropdowns, color-coded rows, checked count in header
- Device Credentials: display-only by default, PIN masked like password, Edit toggle
- Add Note: radio buttons instead of dropdown for note type
- Quick Actions: removed redundant Add Note button

**Settings additions:**
- site_logo (ImageField), color_nav_text, color_sidebar_bg, color_sidebar_text in SiteSettings
- ColorSettingsForm expanded; base.html CSS variables updated; sidebar uses opacity-based text hierarchy
- Font size dropdowns (px values stored in localStorage)
- Client list redesigned to match legacy app layout (ACCOUNT/TYPE/CONTACT/PHONE/EMAIL/DEVICES/WOs)

### Remaining / Future
- **Testing suite** (deferred — will write after real-world use surfaces actual edge cases)
- **Cloudflare tunnel** — ✅ LIVE for the **demo** instance (MB2, `10.58.35.223`) at
  `https://mbdemo.scs-tech.net`, gated by Cloudflare Access (Mike + Jim). Internal prod
  (`10.58.58.82`) stays LAN-only/unexposed by choice. See `~/Documents/Claude/MB2-Cloudflare-Setup.md`.
- **MFA reset hardening** — ✅ DONE + deployed (migration 0053, Jun 18). `MFAResetLog` audit record
  on every reset (shared `reset_user_mfa()` helper); `can_reset_user_mfa` Role flag gates the web
  view (`_can_reset_mfa` = superuser OR flag); `manage.py reset_mfa <username>` break-glass that
  auto-stamps the shell identity (os-user + SSH source IP) into the audit note rather than logging
  an anonymous null actor — the CLI is the highest-risk path so it's made traceable, not faceless.
  Seed flags admin roles on; log read-only in Django admin; 5 tests. Live on demo; prod
  migrated+seeded (prod restart pending). NOT building admin tiers. See `project_mb_mfa_reset_hardening`.
- **Login/logo branding** — ✅ LIVE on **prod + demo** (migration 0052). `login_logo` field +
  Settings upload; login page renders it (fallback to text), logo wrapper decoupled from the form
  (`max-w-[640px]`, logo max-height 560px, form pinned `max-w-md`); sidebar uses ratio-preserving
  fit (232px wide, 160px cap, hide when collapsed) replacing the old hard-coded 90px crush; upload
  guard rejects >2000×2000 (3 tests). See memory `project_mb_login_branding`.
- **Repair report fixes (Jun 18, live on both)** — `WorkOrderPrintView` 500'd on custom Work
  Performed entries (`labor_item=None` → `.category` AttributeError); now grouped under "Other",
  template shows `custom_label`/`notes` for custom entries, regression test added. Print page's
  return link now **closes** the new print tab instead of opening a 2nd WO tab. (Prod restart for
  the cosmetic tab-close change may still be pending — confirm `git log` HEAD is `4942f22`.)
- **Site-wide icon audit** — replace remaining text symbols (×, etc.) with SVG icons

---

## Key Decisions Made

- **Front-end asset delivery — fully self-hosted, no CDN (Jun 23 2026):** HTMX (1.9.12) + Alpine (3.15.12) pinned in `static/js/` (commit `e445fdd`), and Tailwind moved off `cdn.tailwindcss.com` to a **compiled self-hosted stylesheet** `static/css/app.css` (commit `63d9421`) built by `scripts/build_css.sh` (pinned standalone Tailwind v3.4.19 CLI, no Node; cached in gitignored `.tailwind/`; `app.css` is gitignored and **built on deploy** — `update.sh` runs the build before collectstatic, and any manual `git pull` deploy MUST run `scripts/build_css.sh` before collectstatic). Trigger: Privacy Badger blocking `unpkg` had broken the app on a real laptop. ⚠ The `{% icon %}` tag builds size classes dynamically in Python, so `tailwind.config.js` **safelists** `(w|h)-(3..16)` — keep that if adding new icon sizes. The admin **reports page** Chart.js (4.4.0) + html2pdf (0.10.1) are also vendored to `static/js/` — nothing loads from a CDN anywhere now. See memory `project_mb_tailwind_cdn_security`.
- **LoginRequiredMixin on all views** — app is internal-only
- **WorkOrder free-text problem**: `WorkOrder.reported_problem` (TextField, mig 0064) is the freeform "Reported Issue / Work Requested" — for work that doesn't fit a predefined `repair_type` plus ad-hoc client asks. Bench-editable; works on standalone WOs (no ticket). `TicketConvertView` carries `ticket.description` into it (it was silently dropped before). Shown on WO form/detail + the repair report ("Problem / Task"). `repair_type` is optional, not the only way to state the issue. See memory `project_mb_wo_reported_issue`.
- **Work order numbers** auto-generated as `WO-YYYYMMDD-NNNN`
- **Ticket numbers** auto-generated as `TKT-YYYYMMDD-NNNN`
- **SQLite for dev and production** — PostgreSQL supported via DB_ENGINE but not used (decision Jun 21)
- **Visual polish** — shipped session 15: color-coded dashboard tiles, SVG icons replacing emoji, device type icon grid
- **GitHub**: Private repo, push after each working feature
- **HTMX** for inline interactions (notes, replies, checklist toggling)
- **No Celery/async queue** — synchronous email sending is sufficient at MSP scale
- **No OAuth2** for email — SCS uses cPanel-hosted mail with standard IMAP/POP3 credentials
- **Single unified KB** — not split between tickets and work orders
- **Ticket close is always manual** even when linked WO closes — forces human contact
- **MFA backup codes for admin only** — other users recover via admin reset
- **SLA overdue alerts are in-app only** — acknowledgment with required note creates audit trail
- **Attachment storage Phase 1**: local filesystem (configurable path) + S3-compatible
- **Alpine.js** self-hosted in `static/js/` (pinned 3.15.12) loaded with `defer` — required for sidebar accordion (was CDN until Jun 23 2026)
- **Sidebar**: HTMX-loaded on every page except dashboard; admins see all, techs see own
- **`?assigned_to=me` filter**: works on both `/tickets/` and `/work-orders/`; admins see all
- **Credential encryption**: AES-256 via `django-encrypted-model-fields`. `FIELD_ENCRYPTION_KEY` read from env. Never plaintext. Migrations 0031 + 0032 applied to production (June 9, session 15). Key stored in Bitwarden.
- **Billing philosophy**: MB tracks billing state only — not an accounting module. Lightweight `Invoice` entity on WorkOrder (not fields on WO directly). `billing_status` enum: uninvoiced / invoiced / paid / paid_direct / disputed. `paid_direct` = cash/walk-in before formal invoice. Invoice Ninja and other systems remain authoritative for formal financials.
- **Visual design is a first-class requirement**: Color + icons communicate status faster than text. Not optional polish.
- **Modals for quick edits, full pages for complex creation**: Settings section edits, status changes, mark-as-paid → modal. New Ticket, New WO, New Client → full page form.
- **Soft-delete everything**: Hard deletes require deliberate admin action (type-to-confirm). No silent permanent deletes in normal operation.
- **Export-based integrations**: CSV export works with any accounting system. No live API sync until there is clear demand. More flexible and future-proof.
- **Org-level credentials vault is a competitive advantage**: RepairShopCRM has device-level credentials only, no audit trail. MB's org vault + access log is a differentiator — build it properly in Phase 2.
- **Status color convention**: Blue = In Progress/Active, Yellow = Waiting on Customer, Red = Overdue/Urgent, Green = Completed, Gray = New/Unassigned.
- **Audit log gotcha**: `changes_dict` can contain an `'items'` key that shadows `dict.items()` in Django templates. Always use `_audit_entries()` from views.py — never iterate `changes_dict.items` in templates.
- **Queue filter_criteria**: JSON dict with optional keys: `status` (list), `assigned_to` (int or null), `overdue` (bool), `client` (int), `help_topic` (int), `sla_plan` (int). The `assigned_to: null` key (explicit null, not absent) means "unassigned only".
- **Google Maps mileage**: API call is server-side via `MileageDistanceView` — key never sent to browser. Tested working in architecture; needs verification on internal server (outbound HTTPS required).
- **Service type on WorkOrder**: in_shop / onsite / remote. `+ Mileage` button appears on WO detail only when service_type == onsite.
- **two_factor template overrides** live in root `templates/two_factor/` (in DIRS), NOT in `core/templates/` — DIRS takes priority over APP_DIRS in Django's template loader.

---

## Development Setup

```bash
cd ~/Documents/Claude/murphys-bench
source venv/bin/activate
python manage.py runserver
# http://localhost:8000 — login: admin / password123 (local dev only)
```

---

## Related Projects

- **scs-repair-tracker** (`~/Documents/Claude/scs-repair-tracker`) — Legacy PHP app, reference only
- **Clover** (`~/Documents/Clover`) — macOS desktop app, future integration Phase 2+
- **Invoice Ninja** — Financial backend; API research required before Phase 2 integration
