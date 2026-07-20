# Changelog

All notable changes to Murphy's Bench are recorded here, newest first.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions are the
tags cut by `scripts/release.sh` and deployed by `scripts/update.sh`.

New work accumulates under **Unreleased** as it lands on `main` (each fix its own commit,
verified on mb-test). When a batch is ready for production, it's cut as one version tag —
the Unreleased entries move under that version and prod gets a single update.

## Unreleased

_Nothing yet._

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
