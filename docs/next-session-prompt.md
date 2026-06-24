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

**SESSION 44 (Jun 24) — `manage.py export_data` (portable data export). Suite 107→109.** Commit `a145d05`. Bus-factor data portability: writes one neutral CSV per table (csv/) + the protected/ & media/ file trees into a verified `mb-export-<ts>.tar.gz`. For reading/importing elsewhere — distinct from mb_backup.sh's restore-into-MB snapshot. Encrypted fields auto-detected (Encrypted* class) → `***REDACTED***` by default; `--include-secrets` writes decrypted (loud warning); `--no-media` for CSVs only; FKs as `<name>_id`; README.txt + row counts; logs/audit/lock tables skipped; fail-loud cleanup. 2 tests lock the redaction contract. Verified on mb-test (41 tables/258 rows, redaction confirmed). Present on prod/MB2/mb-test.

**▶ RESUME HERE — next self-sufficiency rungs (TODO "Self-sufficiency & release ops"):** (1) `setup.sh` one-line installer scripting INSTALL.md (Debian/Ubuntu apt family); (2) tagged releases + **auto-rollback** (semver tags; failed update auto-reverts code AND DB via `restore.sh`'s `RESTORE_YES=1` hook); (3) THEN the in-app admin "Update" button (gated behind tags — it shows current-vs-available version; out-of-band restart). Order is logical, leave it.

**SESSION 43 (Jun 24) — `scripts/restore.sh` (completes the backup pillar).** Commit `83052a4`. One-command restore from an `mb_backup.sh` tarball: integrity-checks the bundled SQLite snapshot before touching anything live → saves current db/protected/media/.env to `backups/pre-restore-<ts>/` → stops service → swaps in db (drops stale WAL/SHM) + protected/ + media/ → restarts → health-polls. Keeps the live `.env` by default (same-box rollback keeps current secrets); `--with-env` for fresh-box DR; `RESTORE_YES=1` skips the prompt (this is the auto-rollback hook update.sh already references). **Drill-verified on mb-test** (backup → add throwaway client → restore → throwaway gone, counts back to 8/2/22, app healthy 302). Present on prod/MB2/mb-test. **Next in the self-sufficiency bar:** data-export `manage.py` command, then `setup.sh`, then tagged-releases + auto-rollback (restore.sh is the building block) — see TODO "Self-sufficiency & release ops".

**SESSION 42 (Jun 24) — ticket SLA bugfix. Suite 104→107.** A replied-to ticket sitting in Waiting-on-Customer still flipped red once `due_at` passed, because `is_overdue` ignored whether we'd actually responded. Added `Ticket.first_responded_at`, stamped on the **first staff customer-visible reply** (internal notes + inbound client replies don't count); `is_overdue` returns False once it's set and the clock never re-arms. Migration **0065** backfills existing tickets from their earliest staff reply so already-replied tickets clear on deploy. Commit `2235c53`, **deployed + 0065 applied on prod (82) / MB2 (35.223) / mb-test (108)** — verified `[X]` on all three.

**SESSION 41 (Jun 23) — front-end fully self-hosted (no CDN) + WO reported-issue field. Suite 102→104.**
- **CDN removed everywhere.** Privacy Badger blocking `unpkg` broke the app on Mike's laptop (Alpine+HTMX failed). Fixed: HTMX 1.9.12 + Alpine 3.15.12 vendored/pinned to `static/js/` (`e445fdd`); Tailwind moved off `cdn.tailwindcss.com` to a **compiled self-hosted** `static/css/app.css` via the standalone Tailwind v3.4.19 CLI — `scripts/build_css.sh` + `tailwind.config.js` + `tailwind/input.css`, **no Node**, `app.css` gitignored & built-on-deploy (`63d9421`). Coverage check caught a dynamic icon-size purge → safelisted `(w|h)-(3..16)`. Live prod+MB2+mb-test, 0 CDN refs. Also fixed sidebar scroll-clip on short laptop screens.
- **WorkOrder.reported_problem** (migration 0064): free-text "Reported Issue / Work Requested" for uncategorizable/ad-hoc work; convert now carries `ticket.description` (was dropped); on form/detail/repair-report; works standalone. Memory `project_mb_wo_reported_issue`.

**▶ RESUME HERE — CSP (Content-Security-Policy), the last front-end hardening step.** Now feasible (no CDN) but NOT a quick toggle:
- Alpine v3 evaluates directives via `new Function()` → a strict policy needs `script-src 'unsafe-eval'` UNLESS we migrate to Alpine's CSP build (rewrites every `x-data`/inline expression — large).
- Inline `<script>` blocks (e.g. base.html pre-paint localStorage script) need per-request **nonces** via middleware.
- Inline `style="..."` attributes are pervasive → `style-src` will likely need `'unsafe-inline'`.
- **Plan: report-only CSP first** (`Content-Security-Policy-Report-Only`) on mb-test to see the real violation set and decide the Alpine question with data, THEN enforce. Its own session. Memory `project_mb_tailwind_cdn_security`.
- ⚠ When deploying any branch that changes CSS: run `scripts/build_css.sh` before `collectstatic` (update.sh does this; manual `git pull` deploys must too).
- Build CSS on every box at deploy: prod (82, key `id_ed25519`), MB2 (35.223, key `claude-code`), mb-test (108, key `claude-code`).

**Also still open:** align dev Mac Python 3.9→3.12 (no 3.12 installed; do on the new MacBook Air) — memory `project_mb_dev_python_alignment`.

(Reports page Chart.js + html2pdf were also vendored Jun 23 — app is now fully CDN-free.)

---

**SESSION 40 (Jun 23) — current state.** Large multi-part session:
- **Staging VM** `mb-test` (201, `10.58.58.108`) stood up — fresh install-from-git, real prod-data copy (⚠ LAN-only, NEVER a demo — `mb_test_vm_holds_real_data`).
- **PBS backups squared away** (VMID 102/103 collisions fixed → scsprox2=1xx/scsprox=2xx; verify+prune+notify) — `reference_proxmox_pbs_infra`.
- **Observability keystone** shipped — MB self-monitors → System Alert tickets (app 500s, systemd OnFailure, disk check, backup heartbeat, logrotate). **Both assessment reds now CLOSED.**
- **`scripts/update.sh`** — self-serve, backup-first, auto-health-checked update (prod+staging).
- **CI gate LIVE** — GitHub Actions runs pytest+check on every push (first run green).
- **README.md** drafted + viability desk-check; **Mac→GitHub switched to SSH**.
- External-AI review reframed MB as a **credible, evidence-backed internal-tool foundation** (gate > tooling). Full detail in CLAUDE.md header.

**Resume here — Mike's self-sufficiency bar (install/update/backup/export WITHOUT Claude):**
1. **`restore.sh`** — one-command restore from a backup tarball (stop → restore db+media+protected → restart; FIELD_ENCRYPTION_KEY reminder). Completes the backup pillar.
2. **Data export** — `manage.py` command → portable CSV + media bundle.
3. **`setup.sh`** — one-line installer scripting INSTALL.md (Debian/Ubuntu apt family; Ubuntu 24.04 tested, not locked — any systemd Linux, Py≥3.10).
4. **Tagged releases + AUTO-rollback (FIRM):** semver tags (know current/target version); failed update auto-reverts **code AND DB** to last stable (reuse restore.sh). CI-green → tag = rollback target.
5. **In-app admin 'Update'** — wraps update.sh out-of-band; current-vs-available version; admin-only.
6. **Publish-readiness (only if going public):** screenshots, LICENSE (AGPLv3), secrets/client-data audit → fresh public repo, de-Shamrock tweaks (COMPANY_NAME default, T2T relay). Validate demand first (show MB2 to peers).

Memory: `project_mb_publish_ops_selfsufficiency`, `project_mb_test_vm_plan`, `reference_proxmox_pbs_infra`, `project_mb_assessment_2026_06`. ⚠ **When giving Mike terminal commands, ALWAYS state WHERE to run them** (Mac Terminal vs SSH into which VM) — `feedback_state_where_to_run_commands`.

---

**SESSION 35 (Jun 20) — Security posture pass. Suite 96→99.** Audited prod (`check --deploy` + settings)
and acted: **admin user-delete** (guards self/last-superuser — Mike removed the 3 test accounts, only
`admin` remains); file perms tightened (`.env` 600; `protected/`+`backups/` 750); runtime CVE dep upgrades
**Pillow 12.2 / requests 2.33 / cryptography 48.0.1** (validated by full suite on prod's Py3.12). Verdict:
app layer solid, gaps are infra. Detail in memory `project_mb_session35_security`.

### TLS / HTTPS — DECIDED (Jun 20), don't re-open
MB stays reverse-proxy-terminated (never does TLS itself — standard Django model). SCS prod stays plain
HTTP on the **trusted main LAN**; external access (if ever) = behind Cloudflare like MB2; no internal-cert
project. Full rationale for self-hosters in **`docs/deployment-tls.md`**; decision record in CLAUDE.md
"TLS / HTTPS — design decision" + memory `project_mb_tls_decision`. Key idea: encryption ≠ exposure.

### Still wants to DISCUSS:
- **Easy patch/update mechanism** — repeatable pip-audit→bump→test-on-3.12→deploy loop; prereq is
  **aligning the dev venv (Py3.9) to prod (Py3.12)** so upgrades can be tested locally.

### MB2 demo — now at FULL parity with prod (Jun 20)
Caught MB2 (`10.58.35.223`) up to current main `ca45f8e`: migrations 0058–0061 applied, deps upgraded
(Pillow 12.2/requests 2.33/cryptography 48.0.1), perms hardened (.env 600, protected/backups 750),
Invoice Ninja correctly **disabled** (fake data, no token). Both boxes functionally identical.
Leftover: prod's checkout is 2 **docs-only** commits behind (couldn't sync from the VM LAN) — trivial
`git pull` on the main LAN, no restart.

### Tracked infra work (not yet done):
- **`ufw` lockdown on MB2** (untrusted-LAN box) so the Cloudflare tunnel is the only way in — sudo-gated.
- **SSH/VM hardening (sudo-gated, pair with Mike):** key-only SSH, fail2ban, OS patch cadence.
- ✅ **Real DB backup — DONE Jun 22** (SQLite snapshot → immutable B2, restore-drilled). ⚠️ but **PBS whole-VM backup is BROKEN for prod** (VMID collision) — fix scheduled. See BookStack page 09 (system assessment) + the remediation tiers.
- Inbound-attachment ClamAV scan (deferred ceiling).

---

**SESSION 34 (Jun 20) — Phase B (Invoice Ninja push) shipped + live-verified; WO delete added. Suite 88→96.**
The billing loop is closed. `core/invoice_ninja.py` (requests, IN v5). "Send to Invoice Ninja" on a WO →
DRAFT invoice from priced lines, IN assigns the number, WO# → po_number; type-aware find-or-create client
(stores `Client.invoice_ninja_id`); WO-scoped duplicate guard; editable ref; fail-loud. Disabled by default;
Mike configured the live token (Cloud Enterprise, `https://invoicing.co`) and ran a real push — works.
Also added **WO hard-delete** (admin only — there was none; cleans attachment files, reopens converted ticket,
cascades the rest). Migration 0061. Commits `03badde` (Phase B) + `a35bf97` (WO delete). Detail in memory
`project_mb_session34_phase_b`.

### What's left on the billing thread (small, optional)
- **Square-as-IN-gateway** (zero-code, config in IN — NOT an MB task): lets IN record payments + send hosted
  pay-now links. Confirm Square exposes API creds. See `project_in_integration`.
- Optional MB later: on-demand "check payment status" button; email-on-push toggle.
- **Quote/Project approval layer** still deferred (additive, no live-data clock — wait for real project workflow).

### ✅ DONE: Real DB backup (Jun 22)
Fixed — prod runs SQLite (not Postgres); the old pg_dump dumped an empty DB. New fail-loud SQLite-snapshot
+ attachments backup → Backblaze B2 (immutable, Object Lock 30d, lifecycle auto-prune). See
docs/bookstack/05-backup-and-disaster-recovery.md and memory `project_mb_db_sqlite_decision`.

---

**SESSION 33 (Jun 20) — Phase A billing primitive shipped to prod. Suite 84→88. Commit `0534b30`.**
New generic `LineItem` (GenericFK — WorkOrder now, future Quote later; kind labor/part, qty,
unit_price, computed line_total) is now THE billable-work record. Unified `WorkPerformed` into it
(migrated all rows → labor LineItems, rewired the log/edit/delete UI, deleted the model).
`QuickLaborItem.default_price` prefills buttons; WO total on detail + repair report; custom entry
does labor/part with price. MB captures+totals, IN stays billing authority. Migrations 0058/0059/0060.
Deployed to prod (3 rows migrated cleanly) and **browser-verified by Mike — looks good**. Detail in
memory `project_mb_session33_phase_a`. Phase A fully closed out.

### ▶ NEXT REAL ITEM: Phase B — Invoice Ninja push (builds on Phase A's priced lines)
Manual "Send to IN" button, find-or-create client (type-aware name mapping, store IN client_id),
create invoice as a **draft** (IN mints the number, owns assembly + payment; stamp WO# → po_number),
duplicate guard on returned IN id, editable stored ref, create-only/no auto-email. IN v5 API audit
already done. Full detail + push-gaps in memory `project_in_integration`. Plan before building.

### ✅ DONE: Real DB backup (Jun 22)
Root cause: prod runs **SQLite**; the old pg_dump dumped an empty Postgres DB. New `scripts/mb_backup.sh` =
fail-loud SQLite snapshot + `.env`/`protected/`/`media/` → Backblaze B2 (Object Lock governance 30d,
lifecycle auto-prune), restore-tested, nightly timer repointed. See docs/bookstack/05 + memory
`project_mb_db_sqlite_decision`.

---

### ✅ MB2 demo attachment security — DONE (Jun 20, session 32)
Both prod AND the MB2 demo (`10.58.35.223`) now have the attachment fix. MB2 pulled to current, migrations
0054–0057 applied, restarted, verified: localhost `/media/attachments/...` → 404, app → 302. MB2 had 0
attachment files (nothing to relocate); new uploads now land in `protected/`. Bonus: the demo is also behind
**Cloudflare Access** (every request 302s to CF auth first), so it's double-gated. No outstanding attachment-
security work on either box. Optional belt-and-suspenders still available: nginx `deny /media/attachments/`.

---

**SESSION 32 (Jun 20) — Attachment security review acted on, LIVE on prod + verified. Suite 80→84.**
Audited attachment handling; found attachments were served publicly via nginx `/media/` (no login, guessable
URLs), plus an IDOR and inbound paths skipping the upload guards. Fixes (memory `project_mb_session32`,
commit `971b573`):
- **Structural:** attachments now stored under `PRIVATE_MEDIA_ROOT=BASE_DIR/protected` (outside MEDIA_ROOT)
  via `PrivateMediaStorage`; nginx can't serve them; auth view is the only path. Files relocated per target.
- **IDOR:** `AttachmentDownloadView` authorizes per-object (`_can_access_attachment` → ticket/WO scoping).
- **Inbound parity:** `fetch_inbound_email._save_attachments` enforces blocked-extension list + size cap.
- Deferred ceiling: ClamAV scan; content-sniffed inline image rendering (lands with the widget screenshots).

---

**SESSION 31 (Jun 20) — Device/WO hardware specs + nav fixes, all LIVE on prod. Suite 71→80.**
Usability pass (full detail in memory `project_mb_session31`). Commits `cd9caae` + `25166ac`.
- **Ticket device dropdown scoped to client** — onboarding an Unsorted ticket no longer lists every
  device. `TicketForm.device` queryset scoped to the effective client + HTMX OOB device `<select>` from
  `TicketContactsByClientView` so it re-narrows on client change.
- **Device CPU/RAM/storage** (free text, migration 0055) on device form + detail (OS now shown on detail too).
- **WO snapshot + sync-back** (migration 0056): WO copies device specs at creation (as-serviced), edits
  sync back to the device master, device reassign re-snapshots, past WOs stay frozen. On WO form/detail/print.
  Only mutable specs snapshot; manufacturer/model/serial stay live read-through. Existing rows are blank
  until filled — snapshot only fires on new WO creation.
- **Device-detail back-link** now returns to the device's client (was the dead-end device list). List still
  reachable from the dashboard "Devices on File" tile.

**Possible follow-ups (only if Mike raises them):** snapshot manufacturer/model/serial too; add CPU/RAM/
storage to the repair report's spec block (already done) for stand-alone Device print; structured
number+unit spec entry if sorting/filtering by RAM/disk is ever wanted.

---

**SESSION 30 (Jun 19) — T2 ingestion + Unsorted triage bucket, all LIVE on prod. Suite 55→71.**
Inbound is fully live on the real support inbox (closed the carried-over action from sessions 27→29),
and Tier2Tickets (Helpdesk Buttons) is moved off OSTicket's API onto MB via T2's **Email Connector**.
Three things shipped + deployed (full detail in memory `project_mb_session30`):
- **Inbound test broadening** (commit `952db73`, →61): new-ticket, reply-to-open, Message-ID dedup,
  returning-sender, blocked-sender.
- **T2 ingestion adapter** (commit `e540498`, →66): T2 posts from no-reply relay
  `email-connector@tier2tickets.com` with the real end user in a forwarded `From:` in the body. MB
  unwraps it (`_extract_forwarded_sender` in `fetch_inbound_email`) and resolves the real contact.
  Subject `Fwd: E.xxxxx` kept (T2's ticket ID; doesn't collide with `TKT-`). **Contact email is the
  reliable key, not businessName.** T2 is ingestion-only; replies flow support↔contact directly.
- **Unsorted/Unverified triage bucket** (commit `f5627eb`, migration `0054`, →71): unmatched inbound
  no longer mints junk clients (removed per-person/free-email/domain grouping). `Client.is_unsorted`
  + `get_unsorted()`; unknown sender parks under one "Unsorted / Unverified" bucket. Admin dashboard
  card "Unsorted — needs triage: N" → `/tickets/?triage=1`. Onboard = Edit-ticket reassignment;
  reject = delete + BlockedSenders. Bucket excluded from Active-Clients, can't be deleted.

**Open cleanup (Mike, low-priority):** delete the two test junk clients (`tier2tickets.com`,
`Mike McCall`) and reassign test tickets TKT-00009/00010/00011 to real clients via Edit. Load
remaining client contacts into MB so future T2 senders straight-match (until then they correctly
land in the triage bucket).

**Next item to pick up: Phase A of the billing work — a priced line-item primitive** (NOT the IN
push yet). Decided in a long technical-director discussion Jun 19 (full detail in memory
`project_mb_pricing_architecture` + `project_in_integration`; both carry the rationale — don't
re-litigate). The IN API audit is already DONE and validated against IN v5 (in `project_in_integration`).

The decision in short:
- MB captures NO pricing today (`WorkPerformed`/`QuickLaborItem` are description-only;
  `WorkOrderItem.unit_price` nullable/unused; `Invoice.amount` is a lone manual total). That's the
  one schema gap that's expensive-to-reverse-with-live-data, so it lands FIRST.
- **Phase A (next session, self-contained, low-risk):** a GENERIC/attachable priced line-item model
  (description, qty, unit_price, item_type labor/part — sharable with a future Quote, NOT hard-welded
  to WorkOrder), optional default price on `QuickLaborItem` (buttons prefill), parts priced too,
  computed WO total on WO detail + repair report. No new screens. Migration + tests (billing data →
  tests required). Prove it on real WOs before wiring money out.
- **Phase B (later session):** the **Invoice Ninja push** built on the real priced lines — manual
  "Send to IN" button, find-or-create client (type-aware name mapping, store IN client_id), create
  invoice as a DRAFT (IN owns assembly + mints the number; stamp WO# into po_number), duplicate guard
  on the returned IN id, editable stored ref, create-only/no-auto-email. See the push-gaps note in
  `project_in_integration` (a WO may be only part of a combined invoice — handled by draft-push, not
  by making MB model combined invoices).
- **Deferred (documented, not now):** the Quote/Project layer (priced lines + approval gate + WO
  lifecycle on the same primitive). Additive net-new tables → no live-data clock → wait until real
  project workflow shapes it. Tax is a non-issue (Oregon, no sales tax).

**Login / logo branding — ✅ LIVE on prod + demo (migration 0052).** `login_logo` field + Settings
upload; sidebar ratio-preserving fit (232/160, hide-collapsed) replacing the 90px crush; login logo
wrapper decoupled from form (`max-w-[640px]`, height 560); upload guard >2000² (3 tests). Field→space:
sidebar=`site_logo`, login=`login_logo`, reports=`company_logo`, email=`email_logo`. Numbers adjustable.

**Repair report fixes (Jun 18, live on both):** print 500 on custom Work Performed entries fixed
(`labor_item=None` guard + template `custom_label`/`notes`, regression test); print page "Close" now
closes the new tab instead of opening a 2nd WO tab. **One trivial open item:** prod restart for the
cosmetic tab-close template change may be pending — verify prod `git log` HEAD = `4942f22` and that
the running service was restarted after it.

**Prod restart — Claude CAN do it (verified Jun 19):** `scs-tech` has NOPASSWD for
`systemctl restart/status murphys-bench` on prod, so Claude deploys end-to-end (`git pull` +
`venv/bin/python manage.py migrate` + `sudo -n systemctl restart murphys-bench`). Earlier notes
claiming "prod needs a password, Mike must restart" were STALE — disregard. Health-check with the
correct Host header: `curl -H "Host: 10.58.58.82" http://127.0.0.1/account/login/` → 200 (a bare
`curl 127.0.0.1` gives 400 DisallowedHost, which is correct, not a fault).

**MFA reset hardening — ✅ DONE + FULLY LIVE (Jun 18, migration 0053, commit 66582df, suite 43→55).**
`MFAResetLog` audit record on every reset (via shared `reset_user_mfa()` helper); `can_reset_user_mfa`
Role flag gates the web view (`_can_reset_mfa` = superuser OR flag); `manage.py reset_mfa <username>`
break-glass auto-stamps shell identity (os-user + SSH source IP) into the audit note instead of an
anonymous null actor. Seed turns the flag on for admin roles. Log is read-only in Django admin.
Deployed + restarted on both demo and prod. Full context in memory `project_mb_mfa_reset_hardening`.

**Infra hardening (Jun 18, not in repo — recorded in memory `reference_ssh_access`):** rotated Claude's
SSH key (fresh `~/.ssh/claude-code`, old key removed from prod), and made **demo SSH key-only** to
match prod (both boxes now `PasswordAuthentication no`; verified). Claude connects with
`-i ~/.ssh/claude-code`; Mike's manual `ssh` uses his own Mac key (kept separate for audit).

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
- **Deployed internally**: Ubuntu 24.04 VM, 10.58.58.82, Gunicorn + Nginx + SQLite (HTTP on LAN; no domain yet)
- **Gunicorn service**: `murphys-bench.service` — `sudo systemctl restart murphys-bench` (scs-tech has NOPASSWD for restart/status of this service only)
- **App path**: `/opt/murphys-bench/`  •  **SSH**: `ssh -i ~/.ssh/id_ed25519 scs-tech@10.58.58.82`  •  **venv Python 3.12**
- Deploy: `git push` on Mac → SSH → `git pull && venv/bin/python manage.py migrate` → `sudo systemctl restart murphys-bench`
- Full CRUD for work orders, clients, devices, mileage, contacts, tickets, KB, queues; HTML email + signatures; inbound email pipeline

**Session 27 — Stabilization (all shipped + deployed):**

- **Test harness bootstrapped**: `pytest.ini` + `core/tests.py` spine suite (10 tests). Run `venv/bin/python -m pytest`.
- **Four data-integrity bugs fixed** (each test-covered): ticket-delete guard (was always-false `hasattr`); `Device.serial_number` now nullable so many serial-less devices coexist (migration 0045); collision-resistant ticket/WO number assignment (`_save_with_unique_number`); email/inbound failures now log to `core` logger instead of failing silently.
- **`reset_operational_data` management command**: clean OSTicket-cutover wipe. Dry-run by default; destructive path needs `--confirm "DELETE ALL OPERATIONAL DATA"`. Deletes operational data, keeps all config + superusers.
- **Production safety guards**: `DEBUG` now defaults False; startup refuses default `SECRET_KEY`/`FIELD_ENCRYPTION_KEY` when `DEBUG=False`; added nosniff; SSL-redirect/HSTS opt-in via `.env`. Local Mac `.env` created (DEBUG=True).
- **Nightly DB backup**: `scripts/mb_backup.sh` (SQLite snapshot + files → immutable B2) + systemd timer (02:15). Working + restore-drilled (Jun 22).
- **systemd timers** for `fetch_inbound_email` (2 min) + `check_sla_overdue` (15 min) — installed + active; inbound now over **POP3** (switched from IMAP to kill a dedup bug).
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

1. **Phase A — priced line-item primitive** (next). Generic/attachable priced lines + WO total +
   tests. The expensive-to-reverse schema piece; lands before the push. See
   `project_mb_pricing_architecture`.
2. **Phase B — Invoice Ninja push** built on the priced lines (draft-push, IN owns assembly). API
   audit already done; see `project_in_integration`.
3. **Deferred (documented, not now):** Quote/Project layer (approval-gated, on the same line-item
   primitive) — wait for real project workflow.
4. Demoted (do not build without explicit override): departments/teams/routing, customer portal,
   REST API, extra custom-field types, async queue, OAuth2, extra storage backends.

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
- **Alpine.js**: self-hosted `static/js/alpine-3.15.12.min.js` with `defer` (NOT CDN as of Jun 23). HTMX-swapped content reinitializes automatically via mutation observer.
- **two_factor template overrides**: Live in root `templates/two_factor/` (DIRS), NOT `core/templates/`
- **WorkOrderNote customer filter**: Use `note_type='customer_visible'` NOT `is_internal=False`
- **Mileage Calculate CSRF**: Uses `document.querySelector('[name=csrfmiddlewaretoken]')` — do not revert
- **Google Maps API key**: Stored in SiteSettings (DB). Restricted to WAN IP in Google Cloud Console.
- **Production Python**: `python3` not `python`. Venv: `/opt/murphys-bench/venv/`
- **mb_icons templatetag**: `{% load mb_icons %}` at top of any template that uses `{% icon %}`, `{% attr %}`, `{% getfield %}`, or `{% markdownify %}`. Partials need their own load tag.
- **Email template variable reference**: Must use `{% verbatim %}...{% endverbatim %}` to display `{{ }}` tokens in templates.
- **Dark mode**: `dark` class is on `<html>` (documentElement), NOT `<body>`. Use `html:not(.dark)` for light-mode-only CSS rules, NOT `body:not(.dark)`.
- **Tailwind**: compiled & self-hosted at `static/css/app.css` (NOT CDN as of Jun 23). Built by `scripts/build_css.sh` (standalone v3.4.19 CLI, no Node); config `tailwind.config.js` (typography plugin for KB prose; `darkMode:'class'`; safelist `(w|h)-(3..16)` for the dynamic `{% icon %}` sizes). `app.css` is gitignored & built on deploy — run the build before collectstatic.
- **reverse_lazy at module level**: Don't use `reverse_lazy('core:...')` in module-level variable assignments in views.py — causes circular import during URL loading. Use a helper function with `reverse()` called at request time instead.
- **Email logo**: CID inline attachment (`Content-ID: logo`, `cid:logo` in template). Logo read from `site.company_logo.path`. Will switch to public URL once Cloudflare is live.
- **Inbound email regex**: `TICKET_RE = re.compile(r'\[?(TKT-[\d-]+)\]?', re.IGNORECASE)` — matches both sequential (TKT-00005) and legacy date-based (TKT-20260610-0001) formats.

---

## General rules for this project:

- All views use `LoginRequiredMixin`
- HTMX loaded in `base.html` with global CSRF header on `<body>`
- Alpine.js loaded in `base.html` with `defer` (self-hosted `static/js/`)
- Follow existing patterns in `core/views.py`, `core/urls.py`, and existing templates
- Tailwind CSS compiled & self-hosted (`static/css/app.css`, no CDN) — match existing class patterns; new dynamic/Python-built classes need a `safelist` entry in `tailwind.config.js`
- After building, run `python manage.py check` to confirm no issues
- Create and apply migrations for all new models (both dev and prod)
- Commit and push when complete; deploy with git pull + service restart on server
