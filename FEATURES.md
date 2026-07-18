# Murphy's Bench — What It Does

Murphy's Bench is self-hosted service-management software for small MSPs and repair shops.

It runs on your own server, and the main application and database stay under your control. It can connect to outside services such as email, Invoice Ninja, payment gateways, Cloudflare, and offsite backup, but only where you choose to use them.

This is the practical overview: what Murphy's Bench does in the course of a normal day. Installation and deployment are covered in `INSTALL.md`.

---

## The Two Main Workflows

Murphy's Bench handles two kinds of work:

1. managed clients with contracts, covered assets, and recurring billing
2. support and repair work that moves from ticket to work order to billing

Those two sides share the same client records, contacts, devices, and history, but they are not forced into the same workflow.

The managed side handles the work that repeats. The ticket and work-order side handles the work that comes in as it happens.

## Tickets and Work Orders

A ticket is the customer conversation.

A work order is the actual job.

A phone call or email can become a ticket. Once it turns into real work, the ticket can be converted to a work order with one click. The two stay linked, so it is easy to move between what the customer reported and what was actually done.

Closing the work order does not automatically close the ticket. Finishing the repair and finishing the customer conversation are not always the same thing.

### Tickets

Tickets include:

- email-to-ticket creation
- threaded customer replies
- internal notes
- outbound HTML email with company branding and signatures
- automatic acknowledgements
- collapsed quoted email history
- standard and custom statuses
- related-ticket links
- saved queues and filters
- warnings when another user already has the ticket open

Customer communication stays in the ticket. Staff can coordinate internally without creating multiple conversations with the customer.

### Work Orders

Work orders can be used for in-shop, onsite, or remote work.

They include:

- client, contact, and device information
- the reported issue
- internal notes
- labor and parts
- built-in time tracking
- manual time entries
- mileage for onsite work
- pre-repair and post-repair checklists
- timestamped work-performed notes
- repair reports
- encrypted device credentials

The work-performed log becomes the basis of the customer-facing repair report, so the record written during the job is also the record handed back to the client.

Stored credentials are hidden by default. Access can be limited by role, and each reveal is logged.

## Clients, Contacts, Devices, and Assets

Murphy's Bench is organized around the client.

A client can have:

- multiple contacts
- multiple phone numbers
- multiple devices
- tickets
- work orders
- estimates
- contracts
- managed assets
- billing history

Business and residential clients use the same basic records, with differences only where they are useful.

### Managed and Non-Managed Clients

A client becomes managed when it has an active service contract.

A client without a contract can still have tickets, work orders, estimates, counter sales, and billing. The same client can move into or out of managed status without creating a new record or splitting its history.

### Devices and Managed Assets

Devices are used for normal repair and support history.

Managed assets are equipment being tracked as part of an ongoing client relationship. A device can be promoted to a managed asset without losing the work already attached to it.

Assets can be covered by a contract and show their own recent work.

This is managed-device tracking, not inventory. Murphy's Bench does not currently track parts stock, purchasing, receiving, or reordering.

## Contracts and Recurring Billing

Service contracts are what make the managed side work.

A contract can include:

- recurring services and agreed pricing
- monthly, quarterly, or annual billing
- its own billing day
- term and renewal dates
- covered assets

When contracts come due, Murphy's Bench prepares them in a billing worklist. The user reviews the batch before anything is sent to Invoice Ninja.

Nothing is charged automatically. Murphy's Bench creates draft invoices, and the billing step remains deliberate.

There is also a simpler client-level monthly billing mode for shops that do not need full contracts.

## Estimates, Quotes, and Prospects

Prospects can be entered before they become clients and moved through a simple sales process.

When the work is accepted, the prospect can be promoted to a client.

Estimates can include:

- services
- parts
- pricing
- notes
- comparative options
- PDF output

An accepted estimate can be turned into a work order without re-entering the job.

## Register and Payments

Murphy's Bench includes a small Register for:

- settling completed work orders
- recording counter sales

The Register can record:

- cash
- check
- a card payment processed elsewhere
- a charge triggered through Invoice Ninja using a client's stored payment method

Murphy's Bench records the payment reference and can print a receipt.

It does not store card data or process cards itself. Card handling stays with Square, Invoice Ninja, or the configured payment gateway.

Completed sales can be sent to Invoice Ninja as paid invoices or as drafts for later billing.

The Register is not intended to replace a retail POS. It is there because a completed job still has to be closed out and paid.

## Billing State

Murphy's Bench tracks the operational side of billing, including:

- uninvoiced
- invoiced
- paid
- paid directly
- disputed

Outstanding balances are shown by client.

Invoice Ninja remains the billing system of record. It assigns invoice numbers and maintains the payment ledger. Murphy's Bench sends billing data to it and reads status information back.

Invoice Ninja is the only billing backend currently implemented. The integration is kept separate from the rest of the billing logic so another backend can be added later without rebuilding the application around it.

Billing and invoice data can also be exported to CSV.

## Queues, Assignments, and Escalation

The dashboard shows current ticket and work-order activity, including work that is active, waiting, overdue, or completed.

Technicians can see:

- their assigned work
- unclaimed work
- saved queues

Depending on permissions, users can:

- claim work
- assign it
- transfer it
- escalate it

Escalation brings a more senior technician into the ticket without removing the current owner.

In-app notifications let users know when another staff member needs their attention.

## SLA Tracking

SLA plans can define response deadlines for different kinds of work.

Overdue tickets are flagged. Acknowledging one requires a note, so there is a record of why it slipped rather than just a changed status.

## Reporting

Murphy's Bench includes reports for areas such as:

- workload
- ticket volume
- technician activity
- completion rates
- resolution time
- billing
- mileage
- ticket-to-work-order conversion

Reports can be viewed in the application and exported to CSV, print, or PDF where supported.

The dashboard is meant for a quick look at the day. The reports are where the underlying work can be reviewed in more detail.

## Knowledge Base

The built-in knowledge base can store:

- troubleshooting notes
- internal procedures
- vendor information
- how-to articles

Articles are written in Markdown and can be marked as restricted for internal use.

## Security and Access

Murphy's Bench includes:

- login-required access
- role-based permissions
- TOTP multi-factor authentication
- administrator backup codes
- administrator-driven MFA reset
- login lockout
- encrypted device credentials
- encrypted organization credentials
- credential access logs
- audit logging
- HTTPS deployment support

The application is intended to run behind a TLS-enabled reverse proxy.

Remote access can be provided through Cloudflare Tunnel, Caddy, Nginx, or another suitable reverse-proxy setup.

## Current Limitations

Murphy's Bench is not currently:

- a full retail POS
- a payment processor
- an accounting package
- a customer portal
- a hosted SaaS service
- a full inventory system
- a multi-tenant platform

The Register does not include:

- cash-drawer control
- barcode scanning
- split tender
- retail inventory workflows

Parts can be billed on work orders, but stock levels, reorder points, purchasing, and receiving are not yet implemented.

Other planned areas include:

- billing backends other than Invoice Ninja
- deeper management reporting
- SMS
- customer self-service
- broader user documentation
- testing across more shops and workflows

These are directions, not promised features with dates.

## Current Status

Murphy's Bench is used in daily production at the shop where it is developed.

It is working software, but it is still young. It has automated tests, CI checks, backup and restore tools, access controls, and the safeguards needed for production use in one shop. It has not yet been proven across a wide range of businesses and workflows.

A self-hosted installation still needs someone who can maintain the server, manage backups, read the documentation, and troubleshoot when necessary.

Murphy's Bench may fit a solo technician or small shop that handles both managed clients and repair work and wants to keep control of the system it depends on.

It is probably the wrong fit for a shop that needs a polished hosted service, full retail inventory, enterprise MSP automation, or guaranteed vendor support.
