# Murphy's Bench — Setup and Administration

You have installed Murphy's Bench, can reach the login page, and have signed in with the superuser account created during installation.

This guide covers the initial configuration needed before technicians begin using the system. See `INSTALL.md` for installation and `FEATURES.md` for an overview of the application itself.

Routine configuration is handled under Settings, available to administrators from the gear icon in the sidebar footer. You should not need Django admin for normal setup or operation.

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

Set the title-bar, sidebar, and accent colors. A live preview shows how the application will look. Email header text is selected automatically to remain readable against the chosen color.

The separate Display tab controls browser-specific preferences such as font size and card density. These settings are stored per browser and user, not across the entire shop.

## 3. Outbound Email

Go to Settings → Outbound Email.

Enter the SMTP settings for the address Murphy's Bench will use to send mail:

- SMTP server
- port
- username
- password

The password is stored encrypted.

Use Send Test Email and confirm that the message arrives before relying on outbound email for customer communication.

Outbound email is used for functions such as:

- ticket replies
- acknowledgements and auto-responders
- emailed reports and quotes
- email-based notifications

The Email Templates tab controls the wording of automated messages and shows the variables available to each template.

The Suppressed Addresses list contains addresses that must not receive automated mail.

## 4. Inbound Email

Go to Settings → Inbound Email.

Configure the mailbox used by your support address. Murphy's Bench supports IMAP and POP3.

The mailbox is checked by a scheduled polling job. New messages become tickets. Replies containing a `[TKT-…]` token in the subject are attached to the existing ticket.

Before using a live support mailbox, test the process with a separate mailbox and confirm that:

- new messages create tickets
- replies attach to the correct ticket
- processed messages are not imported again
- failed imports remain available for another attempt

Choose the mailbox protocol and post-processing behavior that matches how you want mail retained.

With POP3, deleting successfully processed messages from the server provides a simple one-time intake workflow, but Murphy's Bench then becomes the primary record of those messages.

With IMAP, confirm how the mailbox and poller identify messages that have already been processed so the same message is not imported repeatedly.

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

- Statuses — ticket and work-order statuses and their display colors
- Help Topics — categories for incoming tickets, including an optional default SLA
- SLA Plans — response deadlines and overdue behavior
- Repair Types and Categories — classifications used by work orders and checklists
- Checklist Items — pre-repair and post-repair tasks, grouped by device type
- Canned Responses — reusable customer-facing or internal text
- Quick Labor — common labor entries
- Custom Fields — additional ticket or work-order fields tied to a help topic or repair type
- Dashboard Tiles — status groups shown on the dashboard
- KB Categories — categories used by knowledge-base articles

Do not add configuration simply because a tab exists. Start with the defaults and add fields, statuses, and categories when the existing workflow no longer describes the work accurately.

## 7. Security and MFA

Go to Settings → Security.

The main setting is Require MFA. When enabled, users who have not enrolled are prompted to configure an authenticator application at their next login.

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

Go to Settings → Maintenance → Backups to configure the available backup destinations.

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

## 10. Create Test Data

Before entering real client information, create enough fake data to test the main workflows:

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
