# Murphy's Bench — Setup and Administration

You have installed Murphy's Bench, can reach the login page, and have signed in with the superuser account created during installation.

This guide covers the initial configuration needed before technicians begin using the system. See `INSTALL.md` for installation and `FEATURES.md` for an overview of the application itself.

Routine configuration is handled under Settings, available to administrators in the Back Office section of the sidebar. Its tabs are grouped into Company & Branding, Email, Tickets & Work Orders, Integrations, Access & Security, and System. You should not need Django admin for normal setup or operation.

[docs/screenshots.md](docs/screenshots.md#settings) shows most Settings tabs.

## Suggested Setup Order

The tabs can be completed in any order, but this sequence avoids unnecessary backtracking:

1. Company — business identity and contact information
2. Colors — application and email branding
3. Outbound Email — sending customer mail
4. Inbound Email — creating and updating tickets from email
5. Roles and Users — permissions and technician accounts
6. Workflow — statuses, topics, SLAs, repair types, and common entries
7. Security — MFA requirements
8. Billing and Integrations — Invoice Ninja and payment behavior
9. Backups and Scheduled Jobs
10. Test Data — enough sample records to verify the workflow

You do not need to configure every optional feature before beginning. Company information, working email, user accounts, and the default workflow settings are enough for initial testing.

## 1. Company

Go to Settings → Company.

Enter the business information that should appear on customer-facing material:

- business name
- address
- phone number
- logo

This information is used on repair reports and outbound email, so complete it before sending mail or printing customer documents.

## 2. Colors and Display

Go to Settings → Colors.

Upload a sidebar logo and a sign-in page logo, or leave them blank to show the company name as text.

Set the accent color and the color for each room. The accent colors primary buttons and links everywhere and is the Work room's color. The Register, Desk, and Back Office colors show as the stripe along the top of each page in that room and the dot beside its sidebar group. Blank a field to go back to the default.

Ticket and work order status colors are set on the Statuses tab, and the customer email header color on the Email Templates tab.

The separate Display tab sets the text size. Text size and dark mode (the toggle at the bottom of the sidebar) are both saved in the browser, not on the account, so they apply to everyone who signs in from that browser and do not follow a person to another one.

## 3. Outbound Email

Go to Settings → Outbound Email.

Enter the SMTP settings for the address Murphy's Bench will use to send mail:

- SMTP server
- port
- username
- password

The password is stored encrypted.

Save the settings first: Send test email uses the saved SMTP settings, not what is typed on the page. Confirm that the message arrives before relying on outbound email for customer communication. The test goes to your own account's email address; if your account has none, to the default sending address; and if there is none, to the SMTP username.

Outbound email is used for functions such as:

- ticket replies
- acknowledgements and auto-responders
- emailed reports and quotes
- email-based notifications

The Email Templates tab controls the wording of automated messages and shows the variables available to each template.

Suppression patterns (for example `noreply@*`) and the Suppressed addresses list name addresses Murphy's Bench refuses to email. They are checked against the primary recipient of every message sent to a customer, including replies, templates, and documents sent by hand, not only automated mail. Known limitations include: CC and BCC addresses, such as those in a reply's Also send to box, are not checked, and an address entered with a display name (`Name <address>`), which Resend's Other address field allows, can fail to match the intended suppression rule.

## 4. Inbound Email

Go to Settings → Inbound Email.

Configure the mailbox used by your support address. Murphy's Bench supports IMAP and POP3.

The mailbox is checked by a scheduled polling job. New messages become tickets. Replies containing a `[TKT-…]` token in the subject are attached to the existing ticket.

**No message leaves the mailbox until Murphy's Bench has it.** A message is deleted (POP3, or IMAP with Delete messages after processing ticked) or marked read (IMAP's default) only once its ticket or reply is saved, it was refused on purpose (a blocked sender, or a client set to suppress email), or Murphy's Bench has saved its own copy because processing failed.

A message that fails is tried again from that copy on the next runs, up to 3 times in all. If it still fails, Murphy's Bench opens a ticket under the System Alerts client with the original email attached as `original-email.eml`, and notifies admins on the bell. Open the attachment and create the ticket by hand. Once a message is ticketed or handed to an alert, its saved copy is deleted a day later.

With POP3, Murphy's Bench ends the session normally only when it holds every message it downloaded; otherwise it drops the connection, so servers such as Gmail offer those messages again. With IMAP and Delete messages after processing ticked, it removes only the messages it handled. That needs a server with UIDPLUS; on one without it, handled messages are marked read and flagged deleted, and nothing is purged.

After updating from a version before this change, a message the old version recorded as failed or unfinished is not imported automatically if it arrives again. It opens a System Alerts ticket asking you to check whether a ticket already exists.

Settings → Logs, with the Inbound Email source, lists every message the poller handled, its result, and the reason for any failure or refusal.

Before using a live support mailbox, test with a separate mailbox and confirm that:

- new messages create tickets
- replies attach to the correct ticket
- processed messages are not imported again

IMAP without delete keeps every message in the mailbox, marked read. POP3 and IMAP with delete make Murphy's Bench the only record of those messages.

See `deploy/README.md` for the inbound-email timer.

## 5. Roles and Users

Configure roles before creating technician accounts.

### Roles

Go to Settings → Roles.

A role is a collection of permissions controlling access to functions such as:

- application settings
- stored credentials
- the knowledge base
- billing
- reports
- administrative actions

Murphy's Bench includes Administrator and Technician roles by default. You can adjust them or create additional roles.

### Users

Go to Settings → Users.

Create a separate account for each technician and assign the appropriate role.

Each user can also be assigned an escalation level from 1 through 3. Higher levels are intended for technicians who receive escalated work.

Administrators can set passwords and reset MFA enrollment from this screen.

Django superuser and staff status are intentionally not editable through the Murphy's Bench user interface.

## 6. Workflow Configuration

The workflow tabs control how tickets and work orders are classified and processed. Useful defaults are installed automatically, so begin with them and adjust the system as real work exposes a need.

Available configuration includes:

- Repair Types and Categories: classifications used by work orders and checklists
- Device Types: the machine categories offered when logging a device
- Checklist Items: pre-repair and post-repair tasks, optionally scoped to device types
- Canned Responses: reusable customer-facing or internal text
- Statuses: ticket and work-order statuses and their display colors
- Help Topics: categories for incoming tickets, including an optional default SLA
- SLA Plans: response deadlines, overdue behavior, and the default SLA for business and residential clients
- Tech Skills: skill tags for technicians
- Custom Fields: additional ticket or work-order fields tied to a help topic or repair type
- KB Categories: categories used by knowledge-base articles

Common labor, parts, and services with their prices live in Products & Services, in the Back Office section of the sidebar, not in Settings.

Do not add configuration simply because a tab exists. Start with the defaults and add fields, statuses, and categories when the existing workflow no longer describes the work accurately.

## 7. Security and MFA

Go to Settings → Security.

The main setting is Require two-factor authentication. When enabled, users who have not enrolled are prompted to configure an authenticator application at their next login.

Administrators can reset a user's MFA enrollment if the device is lost or replaced.

For a shared test system, MFA may be left optional to reduce setup friction. It should still be tested before the system is considered ready for production use.

## 8. Billing and Integrations

Configure Invoice Ninja before testing invoicing, recurring billing, saved payment methods, or the Register.

Confirm:

- the Invoice Ninja connection succeeds
- clients can be matched or created correctly
- draft invoices appear as expected
- payment status is returned correctly
- no live card or customer data is used during testing

Contract billing should first be tested with a small number of fake contracts covering different billing schedules.

Murphy's Bench prepares billing for review. Confirm the resulting drafts before introducing real client billing.

## 9. Backups and Scheduled Jobs

Go to Settings → Maintenance and use the Backups card to configure the available backup destinations.

Murphy's Bench can send backups to:

- an SMB or NAS destination
- an S3-compatible destination
- both

Store a separate secure copy of `FIELD_ENCRYPTION_KEY`. A database backup without the matching encryption key cannot recover stored credentials.

Several functions depend on scheduled systemd timers, including:

- backups
- inbound-email polling
- SLA overdue checks

See `deploy/README.md` for timer installation and verification.

## 10. Demo Data — Already There, and How to Remove It

**If you used `scripts/install.sh`, your system already contains demo data.** It is
seeded by default: two business clients, one residential, contacts, devices, an open
ticket, a ticket converted to a work order with priced labour and parts, a managed
contract, and a counter sale.

**Every record is fake** — invented business names, `example.com` addresses, `555`
phone numbers. It exists so you can walk the whole workflow before committing real
information, and so a new install is something you can actually look at.

**Remove it before you enter real client work:**

```bash
venv/bin/python manage.py reset_operational_data \
    --confirm "DELETE ALL OPERATIONAL DATA"
```

Run it without `--confirm` first for a dry run. It removes operational records only
— configuration, settings, roles, email templates, repair types and logins are all
kept. Do not use `manage.py flush`, which also destroys configuration.

Install with `--no-demo-data` if you would rather start empty. Decide at install
time: adding demo data later is deliberately hard.

`seed_demo_data` refuses on an install the installer has marked set up (every
install, including one started with `--no-demo-data`), on a production-style
install (`DEBUG=False`), and on any database that holds operational data of any
kind, including clientless work such as counter sales, prospects and walk-in
devices. Without `--force`, it cannot mix demo records into real data. On an install
that started empty, `--force` is the only way past those guards. It skips all three,
so it can also add demo records beside real ones:

```bash
venv/bin/python manage.py seed_demo_data --force
```

Use it only on a test system that holds no real operational data.

### Walking the workflow by hand

Whether you use the demo data or not, exercise these paths before going live:

1. Create one residential client and one business client.
2. Add at least one contact and device to each.
3. Create a ticket and reply to it by email.
4. Convert a ticket into a work order.
5. Add labor, notes, checklist entries, and a test credential.
6. Complete the work order and generate a repair report.
7. Create a managed contract and run a test billing cycle.
8. Test the Register and Invoice Ninja handoff without using a live payment method.

Use fake information on any test or demonstration system accessible to people outside your shop.

## Ongoing Administration

### Logs

Review the available logs for activity such as:

- outbound email
- inbound-email processing
- credential access
- administrative and audit events

Logs help confirm that scheduled jobs are running and provide a record of security-sensitive activity.

### Resetting a Test System

To remove operational records while preserving configuration:

```bash
venv/bin/python manage.py reset_operational_data
venv/bin/python manage.py reset_operational_data \
    --confirm "DELETE ALL OPERATIONAL DATA"
```

The first command is a dry run.

Do not use `manage.py flush`; it also removes application configuration.

### Emergency Access

Django admin remains available at `/admin/` for exceptional cases, such as correcting a record that cannot be repaired through the normal interface.

Routine configuration and daily work should be performed through Murphy's Bench itself.
