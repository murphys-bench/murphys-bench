# Murphy's Bench

**A self-hosted service management system for small MSPs that handle both managed clients and repair work.**

Murphy's Bench is the system I use to run my own IT service business.

Most of my clients are managed clients. They have a service agreement, covered devices, and recurring monthly billing. Murphy's Bench tracks those contracts, the assets attached to them, and the billing generated from them.

It also handles the break/fix side of the business. A phone call or email becomes a ticket, the ticket can become a work order, the work is documented, and the finished invoice is sent to Invoice Ninja.

The managed side is the core of the business, but both workflows are built into the same system.

Murphy's Bench runs on your own server using Django, SQLite, HTMX, Alpine.js, and server-rendered pages. There is no hosted account, per-seat charge, or outside application database.

> **Current status:** Murphy's Bench is used in daily production at one shop and is still under active development. It should be treated as an early self-hosted project, not a finished commercial product.

---

## What It Does

Murphy's Bench currently includes:

- service contracts that identify managed clients and the assets covered by each agreement
- recurring billing on monthly, quarterly, or annual schedules
- a review worklist before recurring charges are sent
- managed asset records with their own service history
- clients, contacts, devices, and prospects
- tickets created from customer emails or phone calls
- threaded customer replies and internal notes
- ticket-to-work-order conversion
- work orders with labor, parts, checklists, time tracking, mileage, device notes, and repair reports
- estimates and quotes
- prospect conversion after work is accepted
- a small Register for completed work orders and counter sales
- Invoice Ninja integration for invoicing and payments
- encrypted device and organization credentials
- a Markdown knowledge base
- reports with CSV and PDF export
- user roles, MFA, audit logs, notifications, dark mode, and system monitoring

The Register can record cash, checks, externally processed card payments, or charge a client's saved payment method through Invoice Ninja.

Murphy's Bench does not store card details or process payments directly. Invoice Ninja remains the billing system of record.

Invoice Ninja is the only billing backend currently implemented. The integration is separated from the rest of the application so other backends can be added later, but they do not exist yet.

## What It Is Not

Murphy's Bench is not currently:

- a full retail POS
- a payment processor
- an accounting system
- a hosted SaaS product
- a customer portal
- a complete inventory system
- a replacement for Invoice Ninja, Square, or QuickBooks

The Register does not support cash drawers, barcode scanners, split tender, or retail inventory.

Parts can be added to work orders, but Murphy's Bench does not yet track stock levels, purchasing, or reordering. Managed devices are tracked as assets, but asset tracking is separate from parts inventory.

The current priority is supporting the two workflows I use every day:

1. contract → managed asset → recurring billing
2. ticket → work order → invoice → payment

## Why I Built It

I wanted a system small enough to understand and maintain myself, but complete enough to run the shop without relying on several disconnected spreadsheets and applications.

Many available products are designed as large MSP platforms, retail repair systems, or hosted subscriptions. Murphy's Bench is narrower in scope. It covers the work between receiving a request or managing a contracted client and completing and billing the job.

It is not intended to replace every tool used by an IT service business. It is intended to own the operational workflow in the middle.

## Current State

Murphy's Bench is working software, but it is still young.

It runs in production in my shop. It has automated tests, CI checks, backup and restore tooling, security controls, and the operational safeguards I need to trust it with my own business.

A self-hosted installation still requires someone who is comfortable reading documentation, maintaining a server, and troubleshooting when necessary. Issues and pull requests are welcome, but support is best-effort. This is currently a one-person project developed alongside normal client work.

Planned areas of development include:

- stock levels, reorder points, and parts purchasing
- billing and payment backends other than Invoice Ninja
- deeper management and reporting tools
- SMS
- a customer self-service portal
- more complete user documentation
- testing across a wider range of shops and workflows

These are planned directions, not promised features or release dates. Feedback from shops that might use Murphy's Bench will help determine what gets built next.

## Who It Might Fit

Murphy's Bench may be a good fit for a shop that:

- is run by one person or a small team
- has managed clients with recurring billing
- also performs walk-in or break/fix work
- is comfortable self-hosting a Django application
- wants to keep customer and credential data on its own system
- uses Invoice Ninja or is willing to use it for billing

It is probably not a good fit for a shop that needs:

- a polished hosted service
- enterprise MSP automation
- a full retail POS
- mature inventory management
- guaranteed vendor support

---

## Screenshots

**Dashboard** — ticket and work-order queues at a glance.

![Dashboard](screenshots/dashboard.png)

**Ticket detail** — customer conversation, internal notes, linked work order, and available actions.

![Ticket detail](screenshots/ticket-detail.png)

**Work order** — reported issue, device details, labor, parts, time tracking, checklists, notes, and encrypted credentials.

![Work order detail](screenshots/work-order-detail.png)

**Clients** — client accounts, contact information, and device and work-order counts.

![Clients](screenshots/clients.png)

**Client detail** — contacts, devices, contracts, managed assets, and work-order history.

![Client detail](screenshots/client-detail.png)

**Register** — settle a completed work order or create a counter sale.

![Register](screenshots/register.png)

**Reports** — billing summaries, ticket volume, conversion reporting, and export options.

![Reports & analytics](screenshots/reports.png)

**Settings** — most routine configuration is handled inside the application rather than Django admin.

![Settings](screenshots/settings.png)

**Dark mode**

![Dashboard in dark mode](screenshots/dashboard-dark.png)
![Ticket detail in dark mode](screenshots/ticket-detail-dark.png)
![Work order in dark mode](screenshots/work-order-detail-dark.png)

_(Screenshots use demonstration data.)_

## Tech Stack

- Python 3.12
- Django 5.2 LTS
- HTMX
- Alpine.js
- Tailwind CSS, compiled locally with the standalone CLI
- SQLite
- Gunicorn
- Nginx
- Invoice Ninja for billing

The frontend is self-hosted and does not require a CDN.

Murphy's Bench is intended to run behind a TLS-terminating reverse proxy such as Cloudflare Tunnel, Caddy, or Nginx. See [docs/deployment-tls.md](docs/deployment-tls.md).

Content Security Policy middleware is included. The repository defaults to report-only mode so a new installation is less likely to break unexpectedly. Production and demo deployments can set `CSP_REPORT_ONLY=False` after the policy has been tested.

## Installation

The `scripts/setup.sh` script can take a fresh Ubuntu 24.04 installation to a working local login page. It installs the required system packages, application, database, and web server.

See [INSTALL.md](INSTALL.md) for installation instructions, exceptions to the standard setup, and options for exposing the application outside the local network.

## Backup and Restore

Murphy's Bench includes a backup script at `scripts/mb_backup.sh`.

The backup process:

- creates a consistent SQLite snapshot
- includes uploaded attachments and the `.env` file
- sends the bundle to an SMB network share, an S3/B2-compatible bucket, or both
- supports separate schedules and retention settings for each destination

Backup destinations are configured under Settings → Maintenance → Backups.

The local VM is used only for temporary staging. Local backup bundles are deleted after they have been copied to their configured destinations.

Restore is intentionally straightforward: restore the database and application files to their expected locations.

The most important requirement is preserving `FIELD_ENCRYPTION_KEY`. Without that key, encrypted credentials in a restored backup cannot be decrypted. Store a separate copy somewhere secure.

Additional information is available in [deploy/README.md](deploy/README.md).

## License

Murphy's Bench is licensed under the GNU Affero General Public License v3.0. See [LICENSE](LICENSE).

You may use, self-host, and modify it. If you distribute a modified version or make a modified version available as a network service, the AGPL requires the corresponding source changes to be made available under the same license.

Murphy's Bench is provided without warranty.

Copyright © 2026 Shamrock Computer Services LLC

---

Built for my own bench first. If it helps yours too, good.
