# Changelog

All notable changes to Murphy's Bench are recorded here, newest first.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions are the
tags cut by `scripts/release.sh` and deployed by `scripts/update.sh`.

New work accumulates under **Unreleased** as it lands on `main` (each fix its own commit,
verified on mb-test). When a batch is ready for production, it's cut as one version tag —
the Unreleased entries move under that version and prod gets a single update.

## Unreleased

### Added

- The shop is emailed when work arrives: the assigned tech on hand-off and on customer replies; new tickets go to a recipient list on Settings, Outbound Email (blank means every admin).
- One-click follow-up: mark one of your own email templates "One-click send" and finished tickets and work orders show a single button that emails the customer and records the follow-up.

### Changed

- The Desk Follow-ups list is gone. A follow-up lives on its customer and record, and on the Board while due; planning one from a client or prospect is unchanged.

### Fixed

- The template variables table described `{{ site_name }}` as "Murphy's Bench"; it renders your Company Name.

## v0.14.0 — 2026-08-23

The new look: every screen rebuilt on Tabler. Rooms, the Board, one Work Record, the Relationship Desk.

### Added

- The Board at `/` replaces both dashboards: tiles that mean decisions, four regions (Tickets, Bench, Follow-ups, Unsorted), triage order.
- One Work Record for a ticket and its work order; convert in place; History section.
- Client Hub with Service Agreement status set in place; intake asks who first and shows what is already open.
- Rooms: Work, Register, Desk, Back Office each have their own color, adjustable per shop under Settings, Colors.
- Relationship Desk: follow-ups (plan, due on the Board, Follow-ups list), and your own email templates sent by hand from a record or a customer with Email customer.
- Reports: charts for Work Orders and Business Metrics, not only Tickets.
- Reports PDF is made on the server (WeasyPrint, like quotes and repair reports) with the charts included; the browser-side PDF library is gone.
- Settings, Display: a text size control, per browser.
- Sign-in and two-factor screens on the new frame.

### Changed

- Every list shares one worklist skeleton; filters survive paging.
- Destructive actions confirm in a dialog instead of a browser pop-up.
- Settings is one page per tab; the Colors tab offers only what the frame reads (logos, accent, room colors); statuses are colored on the Statuses tab.
- Account Security carries the Settings sidebar.

### Removed

- Tailwind, Alpine and the old frame. No CSS build step on install or update; `scripts/build_css.sh` is a no-op for one release so older updaters still work.
- The configurable dashboard tiles and the old nav/page/section/dashboard color settings (the Board and the new frame do not read them).
- The `django-crispy-forms` and `crispy-tailwind` packages (unused).

### Security

- Content-Security-Policy `script-src` is now `'self'` only: no inline scripts, no `unsafe-eval`. HTMX runs with `allowEval` off.

## v0.13.4 — 2026-08-19

Dependency hardening.

### Changed

- Dependencies are now fully locked: requirements.txt pins every package including
  transitive ones (generated from requirements.in), so every install runs the identical
  set and the CVE audit covers exactly what production runs.

## v0.13.3 — 2026-08-19

Security patch release.

### Security

- Django 5.2.16 -> 5.2.17 (CVE-2026-15830, GeoDjango crash on crafted input). MB does not
  use GeoDjango; routine pin bump so the CVE gate stays green.

## v0.13.2 — 2026-08-19

Resolved tickets close themselves when the reopen window runs out. Outside-reviewed
(two rounds, round 2 clean).

### Changed

- Resolved tickets are marked Closed automatically once the reopen window
  (Settings > Inbound Email) has run out. No email is sent; each flip is recorded in
  the ticket's Record Changes history. Runs on the existing 15-minute timer; the first
  run closes every resolved ticket already past the window.

## v0.13.1 — 2026-08-18

Tickets get a priority. A fix to an oversight rather than a new feature (work orders had
one; tickets never did), so a patch. Outside-reviewed (two rounds, one P3 fixed).

### Added

- Tickets now carry a priority (Low / Normal / High / Urgent). Set on the New Ticket
  form, changed on the ticket page, shown as a list column. Internal only: it never
  emails the client and does not affect the SLA clock.
- Helpdesk Buttons (T2) tickets take their priority from the requester's own picks:
  "emergency" or "unable to use my system" arrive Urgent, "just a question" arrives Low.
  Existing tickets and other sources start at Normal.

## v0.13.0 — 2026-08-16

One machine, one record. Outside-reviewed over three rounds; the migration was drilled
against production snapshots and the rollback path exercised before release.

### Changed

- Assets are gone. Every machine is a Device; a device can be marked as covered by one
  of its client's contracts. Existing assets are merged back onto their devices on
  update, coverage links carried over, nothing typed by hand lost.
- Device types are now editable in Settings (add, rename, remove). A type still used by
  a device or checklist item cannot be deleted. "Network Device" added for routers,
  modems, and switches.

### Removed

- The Assets card, asset pages, and the "Promote to Asset" button.

## v0.12.2 — 2026-08-15

Fixes from real ticket TKT-00041, outside-reviewed before release.

### Fixed

- "Bill Later" now records the invoice on MB's own books, not just in Invoice Ninja,
  so the work order leaves the billing-ready queue. Stranded ones self-correct on the
  next Bill Later, with the amount left blank to reconcile against Invoice Ninja.
- Removed "Converted to Work Order" from the ticket status dropdowns; picking it
  renamed the ticket without converting and hid the real Convert button. The button
  now always offers conversion, or links to the work order once one exists.
- Mark Paid and Paid Direct now require a recorded amount; a paid job without one is
  invisible to revenue reports. Enter the amount with Edit. The Register is unaffected.

## v0.12.1 — 2026-08-08

### Fixed

- **Running the test suite no longer writes fake failures into the application log.** Every
  test run appended about fifteen records to `logs/murphys_bench.log`, and several of them
  were indistinguishable from real ones, including "Outbound email test failed for host
  smtp.example.com" and "Backup failed: destination unreachable". None of it ever happened.
  This only affects people who run the tests on the same machine Murphy's Bench is installed
  on, but that is the same file the app now tells you to read when outbound email fails, so
  anyone troubleshooting could have found a manufactured failure and chased it. Test runs now
  log to a temporary file that is deleted when they finish. Nothing about where the running
  application logs has changed.

- **`git status` in your install folder is quiet again.** Murphy's Bench writes runtime files
  into `logs/` (status files the Updates and Backups cards read, last-run markers, rotated log
  copies), and Git was told to ignore log files but not those, so every install permanently
  reported `logs/` as an unexpected leftover. Only relevant if you look at your install with
  Git, but that check is how you spot a file edited directly on the server, and constant noise
  made a clean install and a modified one look the same.

- **The email test buttons now name the file the error is written to.** When a test send or
  a test mailbox connection fails, Murphy's Bench deliberately keeps the raw error off the
  screen, because mail server failures often quote the server's own banner, its software
  version and sometimes your login name. The message said the details were "in the
  application log" without saying where that is, so the first person to hit it had to ask.
  It now names the file: `logs/murphys_bench.log`, inside the folder Murphy's Bench is
  installed in. The error itself still stays out of the browser.

## v0.12.0 — 2026-08-06

### Security

- **Eleven role permissions are now enforced on the main ticket and work order screens.**
  These checkboxes in Settings → Roles were displayed but never checked by anything: "View
  All Tickets", "Create Tickets", "Edit Tickets", "Close/Resolve Tickets", "Delete
  Tickets", "Assign Tickets", "Internal Replies", "Customer Replies", "Create Work
  Orders", "Edit Work Orders" and "Close Work Orders". Whatever you set them to, every
  technician could do all of it. This cut both ways: unchecking a box did not take an
  ability away, and ticking one did not hand it over — "Delete Tickets" in particular
  could never grant deletion, because deletion checked a separate administrator flag
  instead. If you run Murphy's Bench alone this changed nothing you would have noticed. If
  you have more than one technician, the permissions you configured were not the
  permissions you had. Found by an outside review of the public repository.
- **The smaller actions obey the same permissions as the screens they sit on.** A first
  pass covered creating, editing, closing, deleting, assigning and replying, but left the
  buttons around them open, so "Edit Work Orders" could be switched off while a technician
  still added notes, logged time, ticked checklist items, uploaded attachments, changed
  billing, and added, repriced or deleted the priced lines that decide what a job is worth.
  The ticket side was the same for escalating, linking, logging time, dismissing a
  needs-response flag and acknowledging an overdue ticket. All of those now follow the box
  that claims to cover them.
- **"Manage Users" now actually grants user management, without granting more than that.**
  The box was ignored in favour of an administrator check, so ticking it did nothing. It
  works now, and a role that has it can add, edit and remove users and set their passwords
  — but only within its own authority. It cannot make anyone an administrator, cannot give
  out a role carrying any permission it does not itself hold (charging a card, resetting
  two-factor, the credential vault, deleting tickets), and cannot raise anyone's escalation
  level above its own. It also cannot edit, delete, reset the password of, or clear the
  two-factor of anyone who outranks it — meaning anyone who is an administrator, holds a
  permission it lacks, or sits at a higher escalation level. Escalation level counts
  because a higher-level technician can see tickets escalated to them, so taking over such
  an account would buy visibility the permission never granted.

  Those limits are the whole point. Without them, this one permission was a way to become
  the owner: tick "Admin" on your own account, give yourself a role that reaches Settings,
  or reset the owner's password and sign in as them. Administrators are unaffected, and the
  stock Technician role does not have this permission.
- **Converting a ticket to a work order needs both "Create Work Orders" and "Edit
  Tickets".** Converting creates the work order and also ends the ticket, so it needs the
  permission for each. It previously asked only for the first, which let a role that could
  not otherwise finish a ticket finish one this way.
- **"Close/Resolve Tickets" is switched on for every existing role when you upgrade.**
  Every technician could close tickets before, regardless of the setting, so enforcing the
  old default would have taken that away from working shops that changed nothing. The
  upgrade preserves what you actually had; turn it off for a role when you mean to.
- **Claiming an unassigned ticket is deliberately not restricted** by "Assign Tickets".
  Any technician can still pick up unclaimed work — that permission covers handing a
  ticket to someone else, which is the part that needs the grant.
- **"View All Tickets" is switched off for non-administrator roles when you upgrade, so
  technicians keep seeing exactly what they see today.** The stock Technician role has had
  that box ticked since long before Murphy's Bench limited what a technician sees, and
  because nothing read it, technicians got the normal view regardless: their own tickets,
  plus the unclaimed pool, plus anything escalated to them. Enforcing the box as-found
  would have shown every technician every ticket in the shop the moment you upgraded,
  without you changing a thing. Administrator roles are left alone — they see everything
  either way, and unticking a box that still lets you see everything would be its own lie.
  Turn it on for a role when you actually want that.

### Fixed

- **"Closed" is no longer a work order status. A work order is Completed, or Cancelled.**
  "Closed" sat between the two and behaved like neither: the register would settle it,
  the reports counted it as finished, the work order list called it active, and if the job
  came from a ticket, that ticket never showed "Work is complete — ready for final contact"
  and never offered its Close Ticket button — it stayed stranded behind a job the app
  insisted was still open. Most shops will never have seen it, because it was already
  switched off in Settings → Statuses and the dropdown stopped offering it; the views
  accepted it anyway. Any work order still holding it becomes Completed when you upgrade.
  **Tickets are unaffected and still close** — this is only about work orders.
- **Editing a line on a sale or a quote no longer asks for work order permission.** Sales,
  quotes, recurring client charges and contract lines share the same underlying editor as
  work orders, and it had started demanding work-order rights for all of them — so a role
  set up to handle sales or quoting was locked out of its own pricing. Each now asks for
  the permission that covers the record being edited.
- **Buttons match what your permissions allow.** Convert, Dismiss, Escalate, the ticket
  timer, Apply Checklist, the checklist pass/fail dropdowns and the line-item buttons were
  still drawn for people the server would refuse. They now appear only when they will work,
  and the checklist shows its results as plain text to anyone who cannot change them. The
  user list does the same: Edit, Set Password, Delete and Reset MFA appear per person, only
  for accounts you are allowed to act on, and Manage Roles only for administrators. Its two
  links into Settings are shown only to people Settings will let in.
- **The ticket status dropdown rejects a status that does not exist**, matching the same
  fix on work orders.
- **A negative price is refused instead of silently becoming no price at all.** Typing
  -60.00 into a line item produced a line with no price, no error, and an unchanged total —
  so anyone trying to record a discount got a worthless line and no hint that it had not
  worked. Murphy's Bench has no discount line yet; it now says so rather than pretending.
- **The work order status panel now rejects a status that does not exist.** It saved
  whatever was submitted, so a stale page, a typo, or a retired status could leave a work
  order in a state that no list, report or register recognised. The full edit form always
  checked this; the inline panel did not.
- **A finished job shows up under "Recently Closed" again, and leaves "My Work".** The
  technician dashboard's Recently Closed panel looked for the retired "Closed" status and so
  could never list a completed job. Separately, and for longer, the My Work sidebar excluded
  cancelled work but not completed work, so finished jobs stayed on a technician's list
  indefinitely. Both now use the same definition of a finished work order as everywhere else.
- **The roadmap no longer says restoring a backup is command-line only.** Restore from
  Settings shipped in v0.11.0.

- **A role with "Manage Users" can now find the user list.** The only way into Settings —
  where users are managed — was shown to Django staff accounts alone, so granting that
  permission handed someone an ability with no route to it but typing the address. Such a
  role now gets a Users link of its own. For the same reason, a role with "Manage Settings"
  now sees the Admin link: it could always open Settings, but nothing offered the way in.

### Changed

- The weekly dependency-audit workflow now pins its GitHub Actions to exact commits, the
  way the main CI workflow already did.

## v0.11.3 — 2026-08-05

### Fixed

- **Offsite backups no longer depend on permission to inspect the bucket.** Murphy's Bench
  ships backups to a bucket you have already created, and never creates one. It was still
  asking the storage provider to check the bucket first, which a least-privilege key is
  often not allowed to do: a Backblaze key restricted to a single bucket fails that check,
  and with it every offsite copy. Nothing about your settings changes, and the Test
  destination button and the backup itself still exercise the real target.
- **Running the fix now clears the warning that asked for it.** When an update cannot
  finish installing a release, Settings → Maintenance → Updates shows "This install is
  incomplete" and names the command that repairs it. Running that command repaired the
  server and left the message on screen, because only an update could clear it. You could
  do exactly as you were told, watch it work, and still be told your install was broken
  until the next release came out. `scripts/install_units.sh` and `scripts/install.sh` now
  re-check the install when they finish, so the message goes when the problem does.
- **An install set up with `--skip-web` is no longer told it is incomplete.** That mode
  deliberately installs no background services and no web server, so the check had nothing
  to measure and reported all of them missing, recommending the standard setup its operator
  had chosen not to use. It no longer runs there, and says why.
- **The warning no longer names the wrong cause.** It described every problem as a missing
  background service, including when the actual problem was that the web server could not
  read this install's stylesheets. It now leaves the description to the specific problem
  found, which was always printed just below it.

### Changed

- **What counts as a complete install is described in one place**, `scripts/check_install.sh`,
  which `update.sh`, `install_units.sh` and `install.sh` all run when they finish. Repairing
  half a problem now reports the half that is left, rather than clearing the message or
  keeping a stale one.

## v0.11.2 — 2026-08-05

This release adds no services, no sudo rules and no packages of its own, so the Update
button can deliver everything that is in it.

**That is not the same as your install being complete.** If an earlier release put a
system service on your server that was never installed, this release does not fix that,
and the Updates page will keep telling you so until you run the command it names. In-app
restore shipped two services in v0.11.0, and a server that took v0.11.0 or v0.11.1 through
the Update button does not have them: its Restore page will wait forever for something
that is not running. The fix is one command in a terminal, and it needs a password, which
is why the button cannot do it for you:

```
cd /opt/murphys-bench && scripts/install_units.sh
```

### Security

- **The library that encrypts your stored secrets was updated.** Three vulnerabilities
  were disclosed on August 4th against the version Murphy's Bench pinned
  (PYSEC-2026-3552, PYSEC-2026-3553, PYSEC-2026-3554); this release moves `cryptography`
  from 48.0.1 to 50.0.0, which fixes all three. The fields this protects are your mailbox
  passwords, the backup destination password, the Invoice Ninja token and saved device
  credentials. The stored format did not change, and every encrypted field in a real
  production database was checked to still decrypt before this shipped.

### Fixed

- **The installer and the install checker described two different correct installs.**
  `scripts/install.sh` granted the app user five `sudo` permissions, the instructions it
  prints when it cannot write the rule itself named three, and `scripts/verify_install.sh`
  checked three. So a server set up by hand from those printed instructions was missing
  two permissions and still passed the check. A box in that state can apply an update but
  cannot roll one back, which is the state you find out about on the day it matters. The
  checker now tests every permission the installer grants. If your box reports missing
  permissions after this update, it was already missing them; the check just could not see
  it before.

### Changed

- **What a correct install contains is now written down once**, in `deploy/manifest.sh`:
  the services, which of them are enabled, which jobs report their own failures, the sudo
  permissions and the system packages. `install.sh`, `install_units.sh`,
  `verify_install.sh` and `update.sh` all read that one file instead of each keeping its
  own copy, so they can no longer drift apart the way they just had. Nothing about how you
  install or update changes; a complete checkout now simply has to include `deploy/`.

## v0.11.1 — 2026-08-03

Code only. The in-app Update button can deliver this one on its own.

### Fixed

- **An incomplete install now says so in the app, not just in a log.** v0.11.0 made the
  updater report an install it could not finish, but it reported it to the terminal. The
  in-app Update button captures that output into a log it shows collapsed, underneath a
  green "update succeeded" banner, so the way almost everyone updates still showed a tick
  with the warning folded out of sight. Settings → Maintenance → Updates now reports "This
  install is incomplete", names the services that are missing and gives the command that
  installs them, and keeps reporting it until the install is whole rather than only once
  after an update.
- **The recovery command in `UPDATING.md` was wrong.** It said `git pull`, which fails on
  any server that has ever used the Update button, because releases are deployed as
  detached tags and `git pull` refuses to run without a branch. Use `scripts/update.sh`,
  which deploys the newest release tag, then `scripts/install_units.sh`.

## v0.11.0 — 2026-08-02

### Upgrading to this release

**A fresh install needs nothing. An existing install needs one command.**

In-app restore ships with two systemd services, and the Update button cannot install
system services, because Murphy's Bench runs as an unprivileged user on purpose. So after
updating an existing install, run:

```
cd /opt/murphys-bench && scripts/install_units.sh
```

Update first, then the units, since the unit definitions arrive with the code. Without
this the Restore page appears but waits forever for something that is not running. From
this release on, the Update button checks for exactly this and tells you rather than
reporting success. See `UPDATING.md`.

### Added

- **Restore from a backup in the app.** Settings → Maintenance → Restore lists the backups
  on your configured destination and restores one. Until now recovering a backup was a
  command-line job, which meant the one thing you need on your worst day was the one thing
  the app could not do. The restore runs out-of-band, the same way the Update button does,
  because it has to stop and start Murphy's Bench and a web request cannot stop the server
  answering it. Your current data is copied aside first.
  - **Choosing a backup and restoring it are two separate steps.** You select a backup,
    then press a button that names the one you picked and states plainly that everything
    created since it will be gone. The rows are near-identical timestamps, so a Restore
    button on each row would have made picking the wrong one and running it the same
    click. Each backup shows when it was taken and how long ago.
  - The list reads the backup destination rather than the server's own disk, because a
    backup is shipped off-box and the local copy deleted — a list of local files would be
    empty on a healthy box. If a destination is unreachable it says so and still shows
    what it could read.
  - **`scripts/restore.sh` no longer needs `--with-env`.** Rebuilding on a fresh box now
    picks up the encryption key bundled in the archive automatically, because that is the
    only key that can read what was just restored and a disaster recovery should not fail
    on a forgotten argument. Rolling back on the same box still keeps the live `.env`, so
    working secrets are never silently overwritten. Force either with `--with-env` or
    `--keep-env`.
  - **A restore now tests the encryption key instead of warning you to watch for it.** A
    mismatched key does not break anything visibly: the app starts normally and every
    stored credential is quietly unreadable. The restore now reads one encrypted field and
    tells you plainly if it could not decrypt it.
- **`UPDATING.md`** explains what the in-app Update, Restore and Back Up Now buttons can
  and cannot do, why the limit exists, and the one command an existing install needs after
  a release that adds a system service. A fresh install needs none of it.

### Changed

- **The Update button now tells you when a release needs a command.** Murphy's Bench runs
  as an unprivileged user on purpose, so updating cannot install a systemd unit, a sudoers
  rule or an OS package. Until now that part of a release simply did not arrive and the
  update still reported success. It now reports exactly which system services are missing,
  what stops working without them, and the one command that installs them.
- **That check no longer keeps its own list of units.** It named three of them inline, so
  it only knew about the units that existed when that line was written, which is why it
  said nothing when in-app restore added two more. It now derives the list from
  `install_units.sh`, so a release that adds a unit is covered without anyone remembering
  to update a second place. Opt-in units are excluded, so a box that deliberately does not
  run them is not warned about them.

## v0.10.1 — 2026-08-01

### Fixed

- **A reset that failed partway through destroyed attachment files anyway.**
  `reset_operational_data` promised a clean rollback on failure, and delivered it for the
  database only: it deleted attachment files from storage inside its transaction, so a
  failure further down restored the attachment rows while their files were already gone.
  Files are now unlinked only after the transaction commits, and a file that cannot be
  removed is reported instead of failing the wipe.
- **An admin who manages settings by role, not by Django staff status, got a 403 on the
  email suppression list.** Both suppressed-address views kept a redundant `is_staff` check
  inside a view already gated on superuser-or-`can_manage_settings`, which quietly narrowed
  those two routes to staff only while every neighbouring settings page worked.
- **The data reset left an audit trail of itself, naming the customers it had just
  deleted.** The audit log was wiped partway through the sequence, and everything destroyed
  after that point wrote fresh entries, so the command finished holding rows that carried
  real client names and ticket subjects on a box it had just reported clean. The audit log
  is now wiped last. Found by running the real wipe on a test box; a dry run cannot show
  it, because nothing is deleted and so nothing is logged.

### Changed

- **The settings-route guard now checks the gate structurally, not just the response code.**
  It accepted 404 and 405 as proof the gate had fired, so a view that forgot the admin mixin
  entirely still passed if its placeholder id happened to 404 or it only accepted the other
  HTTP method. It now asserts each settings view actually carries the mixin, with the one
  deliberate exception named in code. Verified by planting exactly that regression: the old
  check passed it, the new one fails it.
- **What counts as operational data is declared once.** `seed_demo_data` (which decides
  whether a box is already in real use) and `reset_operational_data` (which decides what to
  wipe) kept separate hand-maintained lists of the same thing, so adding a model meant
  remembering two files with nothing failing if you only remembered one. Both now read a
  single registry, and a new model fails the suite until it is classified — a seeder that
  misses one injects demo records into a working shop, and a reset that misses one leaves
  real records behind while reporting the box clean.

## v0.10.0 — 2026-07-31

> ## ⚠ Do not use the in-app Update button to install this release
>
> If you are on v0.9.0 or earlier, your box does not have the service-control
> rule this release adds, and that is the bug being fixed. The Update button runs the
> updater you already have, which does not know that, so it will start, fail at the
> restart, fail again trying to roll back, and print MANUAL RECOVERY NEEDED. That
> is exactly what a tester hit, and it is reproducible.
>
> Install this one from a terminal instead:
>
> ```bash
> cd ~/murphys-bench      # or wherever it is installed
> git fetch --all --tags
> git checkout --detach "$(git tag -l 'v*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
> scripts/install.sh
> ```
>
> (If you have ever used the Update button, your checkout is detached from any
> branch, which is how the updater deploys a release, so `git pull` will refuse
> with "You are not currently on a branch". The commands above work either way.)
>
> It asks for your password once, is safe to run over an existing install, and
> keeps your data and settings. **After this, the Update button works normally and
> you never need the manual steps again**. If the rule is ever missing, the
> updater now refuses up front and changes nothing rather than half-updating.

### Fixed

- **A single release-candidate tag would have made every install offer and deploy a
  prerelease as the latest release.** Git's version sort ranks a prerelease *above*
  the release it precedes: with `v0.10.0` and `v0.10.0-rc1` both present, both
  `git tag --sort=-v:refname` and `sort -V` pick the release candidate, and with a
  `v1.0.0-beta` present, they pick that. Every place that decides "what is the newest
  release" used one of those unfiltered: the in-app Update button's target, the
  version the Updates page advertises, the updater's own default, the clean-room
  gate, and the recovery command printed in this changelog. So tagging one RC would
  have pushed a prerelease onto every box that pressed Update, and a tag cannot be
  withdrawn once boxes have fetched it. Nothing had gone wrong only because no
  prerelease tag had ever been cut. All five now accept strictly `vX.Y.Z`, with a
  test that fails if any one of them drifts back.

- **`--skip-web` installed, started and enabled nginx, the one thing it promises not
  to touch.** The flag is documented as "don't touch gunicorn/nginx/systemd", and the
  later web setup is correctly skipped. But `nginx` sat in the installer's apt package
  list, which is gated on `--skip-apt` and never on `--skip-web`, and Ubuntu's nginx
  package starts and enables itself. So a `--skip-web` box ended up with an active,
  enabled nginx serving the default welcome page on port 80. On a server already
  running Caddy, Traefik or a hand-maintained proxy, which is a documented reason to
  pass the flag, that is a competing web server contending for the port the operator
  chose to manage themselves, and on a non-systemd host it installs both. nginx is now
  installed only for the standard, web-managed path. Existing nginx installs are never
  removed, stopped or reconfigured. **Found by running the installer on a clean
  machine, not by reading it**. Five rounds of review of this same file, by two
  readers, did not surface it.

- **The in-app Update button could not work on any install but the author's, and
  a failed update left the box in a bad state.** `update.sh` restarts the service
  with `sudo systemctl restart`, and the automatic rollback's restore stops and
  starts it too. Both need root. Run from an SSH session sudo prompts and a human
  answers; run from the in-app button there is no terminal, so sudo fails with "a
  terminal is required to authenticate". The rule that makes it work had been added
  BY HAND, months ago, on the three boxes we test on, and the installer never wrote
  it. So on a tester's machine the update failed at the restart, then the rollback
  failed at the stop, leaving the OLD code running against a database the NEW
  migrations had already been applied to.

  `scripts/install.sh` now writes `/etc/sudoers.d/murphys-bench`, granting the app
  user passwordless `restart`, `stop`, `start`, `status` and `is-active` of that
  one service and nothing else. `stop` and `start` are there because rollback needs
  them; a rule covering only the restart lets an update finish but leaves a failed
  update unable to undo itself. The rule is validated with `visudo` before it is
  installed. `update.sh` checks every one of those verbs up front and refuses to
  start an update it could not finish or undo, changing nothing. **An existing
  install gets the rule by re-running `scripts/install.sh` once, from a terminal.**

  Two rounds were needed here, and the first was wrong in a way worth recording.
  It granted only the restart, and an earlier draft of this changelog claimed it
  granted stop and start when it did not. Worse, the check written to verify it
  used `sudo -n -l`, which asks whether a command is *permitted*. That is true for a
  command permitted with a password, so on any box where the app user has
  ordinary sudo it reported success whether or not the rule existed at all. Both
  were caught by an outside review, not by us. The check now runs a privileged
  command and reads sudo's own refusal.

- **A failed update kept reporting failure forever, even on a box that had since
  been updated successfully by hand.** Nothing but the update runner ever wrote the
  status file, so the red banner outlived the state it described. A result is now
  suppressed once the installed version no longer matches it; the log stays
  reachable.

- **`--skip-web` silently turned off backups, email fetching, SLA checks, log
  rotation and both in-app buttons, and said nothing.** People pass this flag for good
  reasons: an existing nginx, their own reverse proxy, a host that is not on systemd.
  It also skips every systemd unit, the sudoers rule and the logrotate config, so such
  an install has no scheduled backups, no inbound email polling, no SLA checks, no log
  rotation, and its **Back up now** and **Update** buttons write a trigger file that
  nothing on the box consumes. The buttons stay visible and nothing reports any of it,
  which is the same silently-absent shape as the defect this release fixes.

  The behaviour is unchanged and the fix is disclosure. Skipping all of it is a policy
  rather than a necessity, and the warning now says so honestly: only the gunicorn
  unit, the Update button's path unit and the sudoers rule actually depend on the
  service a `--skip-web` run does not create. The backup, email and SLA jobs are just
  scripts, and the Back up now one-shot uses no sudo at all, so nothing about those
  four needs the missing service. Whether they can be scheduled depends on the host,
  since this flag is also for boxes with no systemd. There is no supported way to wire
  a subset, so the installer wires none and hands over the whole deployment layer.

  The installer now says all of that at the moment it skips them and again in its
  closing summary, naming each capability lost and who owns it. It prints the commands
  to schedule those jobs by hand, as **absolute paths for this install**, with the
  working directory named: a relative path works when pasted into a shell and fails
  silently in cron or a hand-written unit, which is the same never-runs failure the
  warning exists to prevent. It also states plainly that `scripts/update.sh` is **not**
  a safe update path on such a box. That script assumes the standard systemd and nginx
  contract, and run from a terminal it does not refuse: it asks for a password and
  continues. `INSTALL.md` carries the same detail.

- **The Updates page could go silent on exactly the box that most needed a warning.**
  Suppressing a stale result (above) assumed a failed update always rolls back, so
  the box returns to the version it started on. When the rollback fails too
  ("MANUAL RECOVERY NEEDED"), the box is stranded on the new version it never
  finished verifying, which looked like "moved on since", so the failure banner
  was hidden, the log was collapsed and labelled as an earlier attempt, and the
  page said "You're on the latest release." The update runner now records the
  updater's exit code, which is the only thing that tells a failed-but-rolled-back
  update apart from a failed rollback, and a stranded box gets an open log and an
  unmissable banner naming the recovery command. **This precision applies to
  updates run on this release or later**. A status file written by an older
  version carries no exit code, and is still treated as stale rather than risking a
  false alarm on a healthy box.

  To be exact about scope, because an earlier draft of this entry was not: the
  suppression rule above was introduced in this same release and has never shipped,
  so no existing install has ever been affected by it. This entry describes a defect
  found and fixed before release, not one anybody experienced.

- **Recovering a broken box left the alarm sounding on it forever.** The
  stranded-update banner tells the operator to run `scripts/install.sh`. Doing so
  repairs the box completely, and the banner stayed, because the update runner was the
  only thing that ever wrote the status file and the installer never touched it. So
  following the instruction produced a healthy machine still displaying "the app may
  be down", inviting the recovery to be run again. A successful install now clears a
  terminal update result, and only a terminal one: a queued or running update is left
  alone, because that status belongs to an update happening right now. The file is
  removed rather than rewritten as a success, since the installer performed no update;
  `logs/update.log` is untouched, so the detail of what went wrong survives. Found by
  walking the documented recovery on a real box, and confirmed there after the fix.

- **`ALLOWED_HOSTS` picked one address at random on a box with more than one
  network interface.** The installer took the first address `hostname -I` printed,
  which on a machine running Tailscale or a second adapter is not the address the
  shop browses to. That box then rejected its own LAN address, and the update's
  health probe checked a host nobody uses. Every local address is now listed.

- **Log rotation was never installed on any box but the author's.** The logrotate
  config hardcoded one install path and username and was applied by a copy-paste
  command in `deploy/README.md`, so elsewhere the gunicorn access log grew without
  limit. It is now a template rendered by `scripts/install_units.sh`, like the
  systemd units, and its syntax is verified at install time.

### Changed

- **The release gate now exercises rollback, not just a successful update.**
  A green update says nothing about the path that runs when one fails, which is
  the moment it matters and the moment a tester actually hit. The gate now drives
  `restore.sh`, the real rollback, with no cached password and no controlling
  terminal, and confirms the app comes back healthy afterwards.

- **The release gate now runs an update instead of inspecting one.**
  `scripts/verify_install.sh` used to check that the in-app Update button was
  wired, with a comment saying that actually running an update "proves little" on
  a verification box. It proved the one thing that mattered. The gate now drives
  the real trigger file, waits for the update to finish, and fails the release if
  it does not come back healthy. It also asserts passwordless restart directly.

- **A new `clean-room` CI job installs Murphy's Bench on a bare machine and runs
  that gate on every push.** It removes the runner's blanket passwordless sudo
  after installing, keeping only what the installer itself granted, so the job runs
  under the same privileges a real shop box has. Without that step it would pass on
  a build that grants nothing, a green light for exactly the defect it exists to
  catch.

## v0.9.0 — 2026-07-30

> **Read the "Changed" section before you upgrade.** This batch alters three
> defaults, and one of them can affect an install that is reachable from the
> internet.

**On the version number.** This release jumps from 0.4.52 to 0.9.0. Nothing was
skipped. The numbering had been tracking infrastructure rather than the product:
the only minor bumps Murphy's Bench ever had were the in-app update button, the
Django 5.2 upgrade and CSP, while Contracts and Assets, the SLA overhaul,
Estimates, Sales, recurring billing, the POS Register, configurable backups and
time tracking all shipped as patch releases. The number now reflects what is in
the product. From here, a new capability or a change that isn't a clean upgrade
bumps the minor; bug fixes bump the patch. See "Versioning" in the README.

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

- **A walk-in-only shop could have demo data added to its live database.** The guard
  that stops the installer seeding demonstration records into a working system only
  asked whether any clients existed. Murphy's Bench supports work with no client
  attached — counter sales, prospects, walk-in devices and work orders — so a shop
  doing only over-the-counter business can genuinely have zero clients, and rerunning
  the installer there would have added fake clients, tickets and work orders to real
  data while the documentation promised it could not. The installer now marks a system
  as set up when it finishes, and seeding refuses on any marked system regardless of
  what the database contains. As a second layer it also refuses when operational data
  of any kind is present, which covers systems installed before this change.

- **The installer described genuine failures as "this install already has client
  records".** All output from the seeding step was discarded and every failure was
  reported with that one message, so a real problem — a missing encryption key, a
  failed migration, a missing dependency — was announced as a harmless re-run and the
  install carried on. Declining to seed and failing to seed are now separate outcomes:
  a re-run says nothing changed, and an actual failure is reported as a failure with
  the error kept intact.

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

  > Reading this later: `git pull` was right for the boxes this note was written for,
  > because their updater died before it ever ran and their checkout was still on a
  > branch. On a box that has since taken an update, the checkout is detached and
  > `git pull` refuses — use the sequence in the newest release's note instead.
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
