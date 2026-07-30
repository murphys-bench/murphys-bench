# Changelog

All notable changes to Murphy's Bench are recorded here, newest first.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions are the
tags cut by `scripts/release.sh` and deployed by `scripts/update.sh`.

New work accumulates under **Unreleased** as it lands on `main` (each fix its own commit,
verified on mb-test). When a batch is ready for production, it's cut as one version tag —
the Unreleased entries move under that version and prod gets a single update.

## Unreleased

> **Read the "Changed" section before you upgrade.** This batch alters three
> defaults, and one of them can affect an install that is reachable from the
> internet.

### Security

- **A technician could read every stored device password in the shop.** Revealing a
  device credential checked that you were assigned to the ticket or work order the
  reveal came from, but never checked that the job had anything to do with the device
  being requested. Anyone assigned to a single job could read the credentials of any
  device by asking for it with their own job attached, and the access log recorded the
  disclosure against that unrelated job. The job must now belong to the device.
  If you have more than one technician, treat this as the reason to upgrade.

- **Any signed-in user could change shop configuration.** The Settings page was
  restricted to administrators, but 22 of the actions behind it were not, so a
  technician could add, edit or delete knowledge-base categories, repair types,
  canned responses and checklist items, and silently add a client's address to the
  never-send email list, by using the action directly. All of `/settings/` is now
  administrator-only, and a test walks every settings route on every change so a new
  one cannot be added without a gate. The one deliberate exception is reading a shared
  shop credential from the vault, which stays available to technicians who hold that
  permission.

- **"Send a test email" would mail anyone, from your mail server.** The test accepted
  any address and sent to it using your shop's saved SMTP credentials, so any
  signed-in user could send mail as your business and put your domain's sending
  reputation at risk. The test now goes to your own address, the recipient field is
  gone, and connection errors are written to the log instead of shown in the browser,
  where they exposed your mail server's hostname and sometimes its login name.

- **Django admin did not ask for a two-factor code.** "Require Two-Factor
  Authentication" governed the application but not the admin back end, which checked
  only a password, so a staff account that had never completed two-factor setup could
  still use it. Admin can read decrypted credentials and rewrite any record, so it now
  requires a verified code **regardless of that setting**. See "Changed" below.

### Changed

- **Django admin now always requires a two-factor code.** This is not governed by the
  "Require Two-Factor Authentication" setting, deliberately: the admin back end holds
  the keys to everything. If you reach `/admin/` without having set up an
  authenticator, you are sent to the setup page rather than bounced in a loop.
  **If you lose your authenticator entirely**, recover on the server with
  `venv/bin/python manage.py reset_mfa <username>`, which clears the device so you can
  enrol again and records who did it. Generate backup codes before you need them, from
  Settings, Access & Security, Account Security.

- **Two-factor backup codes are administrator-only, and now actually enforced.** The
  documentation has claimed this since backup codes were added; the code never did it,
  and any user with an authenticator could generate their own. Employees now have a
  read-only **My Security** page showing their own two-factor status, and nothing from
  the admin back end. A technician who loses their authenticator is reset by an
  administrator, which leaves a record.

- **The Content-Security-Policy is now enforced, not just reported.** It shipped in
  report-only mode and nothing in the installer or the sample configuration ever
  turned it on, so on every install except the author's it was logging violations and
  preventing nothing. If a deployment genuinely breaks, set `CSP_REPORT_ONLY=True` in
  `.env` to return to reporting, and please report what broke.

- **⚠ Murphy's Bench no longer trusts `X-Forwarded-Proto` and `X-Forwarded-Host`
  unless you opt in.** These headers can be set by anyone who can reach the
  application's port directly, and trusting them unconditionally meant an install
  without a sanitising proxy in front would believe an attacker-supplied hostname and
  build it into links in outbound email.

  **If you run Murphy's Bench behind Cloudflare, nginx, Caddy or any other proxy that
  terminates HTTPS, add `TRUST_PROXY_HEADERS=True` to `.env` when you upgrade.**
  Without it the application will treat requests as plain HTTP, which can break secure
  cookies and produce `http://` links. A plain-HTTP install on a local network needs
  no change. See `docs/deployment-tls.md`.

- **A new install now starts with sample data.** Two clients, contacts, devices,
  tickets, a work order with priced labour, a managed contract and a counter sale, so
  you can see how the parts fit together instead of facing an empty database and a
  checklist. Every record is obviously fake. Remove it before entering real work with
  `manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"`, which
  clears the records and keeps your configuration. **One thing it deliberately keeps
  is the Products & Services catalog**, because a price list is configuration a real
  shop must not lose — so the five sample entries survive, and the command now lists
  them so you can review or delete them under Settings. Install with
  `--no-demo-data` to start empty.

### Fixed

- **The installer reported failure on installs that were completely fine.** The check
  that confirms the web server can serve stylesheets ran immediately after reloading
  nginx. nginx finishes reloading in the background, so the check could be answered by
  the old configuration and report `INSTALL FAILED: nginx returned HTTP 404` on a
  healthy box, while the same request succeeded moments later. It now waits for an
  answer instead of asking once, and its error messages distinguish a permissions
  problem from a configuration problem instead of guessing at permissions every time.
  Introduced in v0.4.52; if you saw that message, your install was probably fine.

- **The installer could report success over an application that was not running.**
  Stylesheets are served by nginx directly from disk, so that check passed even when
  the application itself was returning errors to every request, and the installer went
  on to print "Murphy's Bench is running at ...". It now restarts the application and
  confirms the application itself answers. The restart also means re-running the
  installer picks up a changed service definition rather than leaving the old process
  running against it.

- **"Clear the demo data" did not clear all of it.** `reset_operational_data` was
  written before Sales, Estimates, Prospects, Contracts and Assets existed and never
  learned about them. None of those reliably disappear with their client — a counter
  sale and a sales lead need no client at all — so clearing a seeded install left a
  sample sale and its priced line item behind, in a database the documentation said
  was clean. The command now removes them, reports every category it deletes, and
  lists what it keeps. A test seeds and then clears, and fails if any operational
  record of any kind survives, so the instruction cannot drift from the code again.

- **A dead database query on every work order edit**, left behind when automatic
  ticket closing was removed in June. No behaviour change; it simply stops happening.

- **INSTALL.md contradicted itself about where Murphy's Bench goes.** The quick install
  cloned into whatever directory you were in, while the manual instructions and the sample
  service file hardcoded `/opt/murphys-bench`. A tester installed to their home directory,
  which is correct and fully supported, and then had reason to think they had done it wrong.
  The manual section now states that the location is your choice, shows a home-directory
  install first with `/opt` as an equally valid alternative, and writes the install path as
  `<app-dir>` everywhere else instead of naming one specific directory.

### Added
- **Roadmap: restore from the web interface.** Backups can be run and downloaded from
  Settings, but restoring one is still a command-line step. A guided restore in the UI is
  planned; `scripts/restore.sh` stays as the disaster-recovery path.
- **Roadmap: calendar and scheduling**, under Considering. Requested by a tester.

## v0.4.52 — 2026-07-28

### Fixed
- **Backups, updates, and every scheduled job now work on installs that aren't the
  author's own server.** Every shell script and systemd unit hardcoded the path
  `/opt/murphys-bench` and the user `scs-tech`. On any other box the consequences were
  invisible and serious: the in-app **Back up now** and **Update** buttons wrote a request
  that nothing ever picked up, so they spun forever and survived a reboot; **scheduled
  backups never ran at all**; inbound email polling and SLA checks never ran. Nothing
  reported any of it. If you installed Murphy's Bench with `scripts/install.sh` before this
  release, **you have had no working backups.**

  **To pick this release up, run `git pull && scripts/install.sh`, not the in-app Update
  button.** On the version you are coming from, the updater is itself one of the broken
  scripts: outside `/opt` it dies looking for a directory that isn't there and leaves the
  request file behind, so the button spins forever. It works normally from this release on.
  `scripts/install.sh` is safe to re-run over an existing install and repairs both the
  missing services and the file permissions. Confirm with
  `systemctl list-units 'murphys-bench-*'`.

  Scripts now derive their location from where they actually are. The unit files in
  `deploy/` became templates rendered per-install by the new `scripts/install_units.sh`,
  which `install.sh` runs, so there is no path or username left to keep in sync by hand.
- **`scripts/install.sh` installs every unit, not just gunicorn.** It previously installed
  the web server and listed the timers as "optional next steps," which is what let the gap
  survive review. They are not optional. They are the entire background-jobs layer.
- **The login page renders styled on installs outside `/opt`.** nginx serves static files
  as `www-data`, and Ubuntu has created home directories mode 750 since 21.04, so an
  install under `~` produced a working login form with every stylesheet and image failing
  to load, which looks like broken software rather than a permissions problem. The
  installer now grants the web server traverse permission and **verifies a real stylesheet
  returns HTTP 200 before reporting success**. `update.sh` re-applies the permission after
  `collectstatic`, so a later release can't quietly undo it.

### Added
- **`scripts/verify_install.sh`, a clean-room install gate.** Run it on a throwaway VM
  after `install.sh` and it asserts the features actually work: static files are served,
  every unit is installed and running, and **Back up now** really reaches the backup
  script. Every check that existed before this verified the *code*. pytest runs Django
  in-process and cannot see systemd, nginx, or file permissions, so everything past that
  boundary had only ever been validated by hand, on boxes that were set up by hand. That is
  why this class of bug reached a tester before it reached us. A green pytest run plus a
  green run of this script is now the release gate.
- Two tests that fail if a script or unit file starts hardcoding an install path or user
  again, so the regression can't return between clean-room runs.
- **`update.sh` now reports an incomplete install instead of leaving you to find out.**
  Updating an install made before this release does not repair it: the background jobs
  still aren't installed and the stylesheet permissions still aren't set, and `update.sh`
  can't fix either itself because it deliberately holds no sudo beyond restarting the
  service. So it checks after the restart and says plainly what is broken, in terms of what
  stops working rather than unit names, with the single command that fixes it
  (`scripts/install.sh`, safe to re-run over an existing install). It never fails the
  update over this.

## v0.4.51 — 2026-07-27

### Added
- **`ROADMAP.md`** — a public view of where Murphy's Bench is going: Working On,
  Considering, Not Planned. Prompted by a tester asking about data import and having
  nowhere to look for the answer. README's own planned-development list is retired in
  favor of it, so the two can't drift apart.
- **Settings → Logs is searchable.** The tab rendered five separate tables, each capped at
  its most recent 200 rows — so anything older was unreachable in the UI at all, and a
  question spanning two logs ("what happened around 9:01 PM?") meant eyeballing them side
  by side. Now one merged, newest-first stream by default, with free-text search, a date
  range, and a source filter. Picking a source gives that log its own columns and its
  **complete** history, paged in the database. The merged view stays bounded on purpose —
  it's an overview, not an archive. Which of those two a shop reaches for first differs by
  shop, so the filter is one click away rather than a stored setting. Notifications are
  one of the sources, which is what makes retaining dismissed notices defensible — the
  bell hides them, and this is where the who-saw-it-when record can actually be read.
- **Notices can be dismissed.** An × on each, plus Dismiss all. Dismissing also marks it
  read — acting on a notice is acknowledging it. Rows are **never deleted**: recipient and
  `read_at` are the only record in MB of which tech saw an alert and when (the audit log
  records changes, not reads), which is accountability data once a shop has more than one
  tech. Migration 0100 adds `dismissed_at`.
- **The bell now shows tickets awaiting your reply.** A client reply sets
  `Ticket.needs_response`, which until now only surfaced on the dashboard, the ticket list
  and the ticket itself — so on a work order or in Settings, nothing told you a client had
  written back. These are queried live rather than mirrored into notification rows,
  deliberately: the flag clears itself when a tech replies and stays correct when someone
  else picks the ticket up, neither of which a per-recipient row does. Nothing to dismiss.

### Fixed
- **A bad `FIELD_ENCRYPTION_KEY` now says so, instead of erroring 40 lines deep.** Copying
  `.env.example` without generating the keys left the placeholder string in place, which
  isn't a valid Fernet key — the first `manage.py` command died inside an import chain with
  `Fernet key must be 32 url-safe base64-encoded bytes` and no indication of the cause or the
  cure. The existing safety guard didn't catch it: it only checks for the *committed default*,
  and only when `DEBUG=False`, while `.env.example` ships `DEBUG=True`. The key is now
  validated at startup in every mode, with the generate command in the error message.
- **The installer's "no manage.py" error no longer sends you to the wrong place.** It said
  "run this from the cloned repo root", but the script `cd`s to its own parent before any
  check, so the working directory is irrelevant — following that advice can't fix anything.
  It now names the directory it actually inspected, explains that the directory came from the
  script's own location, and gives the clone-and-rerun recovery. (Reported by a tester who hit
  this on a fresh install and had to wipe and start over.)
- **The test suite no longer requires `collectstatic` to have been run.** On a fresh manual
  install, 120 tests failed with `Missing staticfiles manifest entry for 'css/app.css'` —
  which reads like broken code but only means the CSS build step hadn't run yet. Tests now use
  plain static storage. The coverage that would otherwise be lost — a `{% static %}` in a
  template naming a file that doesn't exist — is kept by a dedicated test that opts back into
  manifest storage and skips itself when no manifest is present, so it always runs in CI.
- **A failed email now records *why* it failed.** Both SMTP failure paths caught the
  exception, wrote it to `murphys_bench.log` on the server, and stored only the slug
  `send_error` — so the Logs page could say "Failed" but never "authentication rejected"
  or "connection refused". The cause is now captured onto the log entry (truncated to the
  field's 255 chars) and is searchable, so an admin can diagnose a broken mailbox from the
  UI instead of needing SSH.
- **A notice for finished work no longer sticks around forever.** A System Alert opens a
  ticket and pings the bell; closing that ticket left the notice sitting there with nothing
  left to act on. A notice now drops out once its ticket is resolved/closed/converted or
  its work order is completed/closed/cancelled. That's a filter on the ticket's own status
  rather than a stored flag, so it fixes existing notices with no data migration and
  reverses on its own if the ticket is reopened. Only MB's built-in statuses count as
  settled — a custom status added in Settings → Statuses won't auto-clear, chosen so a
  lingering notice is the failure mode rather than a vanished alert.

### Changed
- **Ubuntu 26.04 LTS is now a supported install target**, alongside 24.04. Verified end to
  end on a fresh 26.04 VM: every migration applies, the full suite passes, and the app comes
  up serving the login page. 26.04 ships Python 3.14, which the application runs on unchanged.
- **The installer script is now `scripts/install.sh`** (was `scripts/setup.sh`). A tester
  reasonably looked for the script named after `INSTALL.md` and found `setup.sh` instead —
  made worse by `SETUP.md`, which is the *post*-install configuration guide and has nothing
  to do with the script. Install lives in `install.sh` / `INSTALL.md`; setup-after-login
  lives in `SETUP.md`.
- **Notifications removed from the sidebar.** The bell in each page header already goes to
  the same place, so the count lived in two spots. The bell was added to the Settings
  header, which was the one area without one.

### Security
- **Pinned the last unpinned dependency (`markdown`).** Every other line in
  `requirements.txt` was already `==` pinned; `markdown` was not, which left one line where
  a resolver could pick up something other than what was reviewed. Pinned to `3.10.2` — the
  version already installed on prod, so this is a no-op at deploy time and purely closes the
  hole. Context: the "slopsquatting" class of attack, where a plausible-but-wrong package
  name (hallucinated by an AI assistant, or simply mistyped) resolves to a package an
  attacker pre-registered. Pinning is the main defense and MB already had it everywhere else.

### Changed
- **README leads with a screenshot.** The Screenshots section was at line 117, below the
  feature list — a visitor saw only text above the fold. GitHub Traffic showed real
  visitors clicking straight into screenshot files, so the dashboard image now sits
  directly under the tagline with a link down to the full gallery. Documentation only.

## v0.4.50 — 2026-07-25

### Fixed
- **The inbound duplicate-ticket regression test could fail spuriously.** The fetch command's
  single-runner lock used one fixed path shared by every process on the host, so any other
  process holding it (a second test run, or a live fetch timer) made the command skip its fetch
  and the test see no new ticket. The lock path is now a setting and tests get their own; the
  real job is unchanged — one fixed path per install, overlapping fetches still impossible. Also
  adds the first test of the single-runner guarantee itself.

### Changed
- **Device credentials are now stored in one place, and reachable from tickets.** Credentials
  used to exist twice — once on the Device, once on each Work Order — read by different pages,
  so a work order could show "No credentials on file" for a machine whose device page had a
  password saved. The Device is now the single store, rendered in the shared Device card, which
  also makes credentials available on a **ticket** for the first time (a ticket has a device but
  never a work order). Migrations 0098/0099 move any work-order-held credentials onto their
  device — the device's own value wins, and anything that would be lost (a differing value, a
  PIN, notes) is carried into the credential notes rather than discarded.
- **Credential access is gated asymmetrically.** *Revealing* one requires the credentials
  permission AND either admin or assignment to the ticket/work order it's revealed from; on the
  device page, where there's no job to check against, it's admin-only. *Recording or updating*
  one requires only the permission — clients often volunteer a password change mid-job, and a
  tech who can't save it puts it somewhere worse. Overwrites are covered by the audit log, which
  now records which ticket or work order the action came from and whether it replaced an existing
  value. Previous values are not retained.
- **Work Order detail right rail consolidated.** The separate "Update Work Order" accordion is
  gone — Status, Priority and Service Type joined the Details card's Edit view, which already
  held repair type / assigned to / scheduled date / contact / Invoice Ninja ref. Device
  Credentials moved into the Details card as a sub-section (same permission gating and access
  logging — the `credentials_display` partial is unchanged). Pre / Post Checklist moved out of
  the rail into the main column as a full-width collapsible card between Details and Work
  Performed. Timer is now the first card in the rail, matching Ticket detail.
- **Settings → Help Topics: the "Add Help Topic" form moved above the topics table.** With a
  full list of topics the add form sat below the fold; it's now the first thing on the card.
  Form itself is unchanged.
- **Ticket + Work Order detail pages standardized, Device gets its own card with notes.**
  Ticket detail's right-rail "Details" list was crowded and had nowhere to show device notes;
  Work Order already had the right shape (Client + Device cards up top, tools-only right rail),
  it just lacked notes. Both pages now share one Device card (collapsed by default, click to
  expand specs + notes). Ticket adopted WO's layout — Client + Device cards, a compact Ticket
  Details card, slimmer right rail. Low-frequency metadata (created by/dates, source, linked
  tickets, invoice ref) moved off both main pages onto a new "Details & history" sub-page per
  record. Template-only, no migration.

## v0.4.49 — 2026-07-20

### Fixed
- **Ticket Time Spent now shown in the Details card, matching Work Orders.** The Timer card
  used to show its own "Logged: X" total in its header — inconsistent with Work Orders, where
  Time Spent has always lived in the Details card. Moved the display into Ticket Details
  (same row label, same spot); the Timer card now just has the stopwatch/log controls.

### Added
- **Work Order time now shows up in Reports.** Business Metrics had a Ticket Time Logged
  section but no equivalent for Work Orders — a real gap. New **Work Order Time Logged**
  section reports the same period's WO stopwatch time: total minutes, a **by work order**
  table, and a **by technician** breakdown (based on each WO's assigned tech, since WO time is
  a single running counter, not per-entry like ticket time).

## v0.4.48 — 2026-07-20

### Added
- **Ticket time tracking (lightweight, non-billable).** A Timer card on ticket detail (same
  stopwatch as Work Orders) logs blocks of time directly against a ticket via a new
  `TicketWorkLog` entry — per-entry rows (duration + optional note + who + when), no billing,
  never touches Invoice Ninja. Captures work that never becomes a work order (a quick account
  unlock, checking an alert and resolving it) so the time is still visible. A new **Ticket
  Time Logged** section under the Reports → Business Metrics domain totals minutes/entries by
  period and technician, plus a **By ticket** table showing the total time on each ticket and
  every technician who worked on it — so an admin sees it all in Reports without opening each
  ticket.

### Changed
- **Work Order Timer moved** below the Update Work Order card; **Update Work Order** and the
  **Ticket Details** card are now collapsible accordions.
- **All accordion cards default to closed and remember their open/closed state per browser**
  (Ticket Details, Update Work Order, WO Checklist, catalog Services/Products, Settings
  repair-type and canned-response category cards).

## v0.4.47 — 2026-07-20

### Added
- **"View changelog" link on the Software Updates card.** Settings → Maintenance now links
  from the "Latest available" version straight to that release's CHANGELOG.md section,
  read from the actual release tag (not the working tree) so it's accurate even if newer,
  undeployed work has since changed the file.

### Changed
- **Sales history moved into Reports.** The Sales list is no longer a sidebar tab; it's
  reached from the **Counter Sales** section of the Reports page ("View all sales →").
  Sales history is a management/reporting concern, so it lives on the management surface
  rather than a top-level nav item. (The Register stays in the sidebar for taking sales.)
- **Reports page reorganized into a side-menu (Slice 1 of the Reports restructure.)**
  The ~11 report sections used to be one long flat scroll — cluttered and hard to scan.
  They're now grouped into three domains (**Financial**, **Tickets**, **Work Orders**)
  behind a left side-menu, matching the same navigation pattern as Settings/Admin. Only
  the selected domain's sections render at a time; Export CSV/Print/PDF menus are scoped
  to the visible domain too. The date-range filter still applies across all domains.
  Financial gains room to grow into deeper sales/P&L reporting in a later slice.
- **Register: added a "Recent sales" card and decluttered the work-order list.** Counter
  sales had zero visibility on the Register page — only work orders showed up. A new
  "Recent sales" card lists recently completed counter sales with a receipt link. The
  work-order list above it is now action-focused: an already-paid work order no longer
  clutters the "needs settling" list (an explicit search still finds it, to pull its
  receipt back up).
- **Reports restructure Slice 2: Financial "Revenue" section, and a new Business Metrics
  domain.** Adds a Revenue breakdown to the Financial domain — combines paid work orders
  and completed counter sales into one figure, broken down by day/week/month/year, client
  type (Business/Residential/Walk-in), product/service category, and source (Work Orders
  vs. Counter Sales). Deliberately a REVENUE statement, not a profit/loss — Murphy's Bench
  doesn't track costs or expenses, so a real P&L can't be honestly computed yet.
  Also reorganizes the domain side-menu: SLA Compliance, Resolution Time, Conversion Rate,
  Backlog Health, and Technician Performance move out of Tickets/Work Orders into a new
  **Business Metrics** domain — they're "how are we doing" numbers, not raw activity data
  or money, and don't belong mixed into either.
- **Reports restructure Slice 3: Work Orders domain gets real content.** The Work Orders
  domain previously had only Mileage — Mike noticed 5 closed work orders were nowhere to
  be found in Reports. Added Work Orders by Status (all statuses, including closed —
  unlike Tickets' by-status view, which intentionally excludes closed), Work Orders by
  Client, and a Work Orders list (linking to each WO) for the selected date range.

## v0.4.46 — 2026-07-19

### Fixed
- **No-charge polish on the work-order settle screen.** When a work order had no priced
  line items, the settle screen still showed "Mark Paid — $0.00", which could only fail
  with "nothing to settle." Now, with a $0 total, the payment fields and Mark Paid are
  hidden and **No Charge** becomes the primary action. Also kept the new "No charge"
  method out of the payment-method radios/dropdown, where it isn't a way to *pay*.

## v0.4.45 — 2026-07-19

### Added
- **No-charge receipts.** Both the counter Sale and the work-order settle screen now have
  a **No Charge** option that completes the transaction at $0.00 (warranty, goodwill, a
  handout) and prints a Murphy's Bench receipt reading "No charge." It records a real $0
  completed transaction so the no-charge work shows up in history and reporting rather
  than vanishing. Available even with no priced line items, and it never touches Invoice
  Ninja (there's no money to reconcile). New `No charge` payment method (migration 0096).

## v0.4.44 — 2026-07-19

### Fixed
- **Settle a work order in cash without Invoice Ninja.** The register's work-order
  settle screen hard-blocked with "Invoice Ninja is not enabled in Settings" if IN was
  off — a shop not running IN couldn't take payment on a work order at all. It now
  records the payment on Murphy's Bench's own record (amount, method, reference, paid
  date) and prints MB's receipt, with no IN push and no warning. When IN *is* enabled,
  nothing changes — it still pushes to and reconciles with Invoice Ninja exactly as
  before. Part of making MB stand on its own financially without any external app.
  - The "Bill Later (Draft)" button is hidden when IN is off, since that action only
    means "push an unpaid draft to Invoice Ninja" and has no standalone equivalent yet.

## v0.4.43 — 2026-07-19

### Fixed
- **Sales nav link restored.** `/sales/` (counter/walk-in sale history) had no sidebar
  entry — reachable only by clicking a "Register →" button on another page — and the
  Reports section that was meant to surface it instead was never built. A reviewer
  couldn't find the page at all. Sales now has its own sidebar link.

## v0.4.42 — 2026-07-19

### Fixed
- **Register (Light POS)**: the "recently completed" list from v0.4.41 could sort the
  newest work order to the bottom instead of the top. `completed_date` is only stamped
  by `WorkOrder.mark_completed()` — a WO completed through any other status-change path
  has it NULL, and sorting straight on that column mixed dated and undated rows
  unpredictably. Now falls back to the WO's creation time when `completed_date` is
  unset, so newest is always first. Also capped the list to a fixed scrollable height
  so a full 25-row list doesn't push "Start New Sale" off screen.

## v0.4.41 — 2026-07-19

### Fixed
- **Install docs**: `INSTALL.md` no longer runs the full pytest suite as part of
  "Initialize the Application" — running hundreds of tests isn't part of bringing the
  app up, it's an optional health check. Moved to its own "verify the install" note.
- **Register (Light POS)**: the register's search screen only ever showed a work order
  if you typed a search term — a walk-in or unnamed-client job had no way to be found
  short of guessing its exact client name (e.g. the system "Unsorted / Unverified"
  bucket). It now lists the most recently completed work orders by default, so any
  finished job can be found by browsing instead of searching blind.
