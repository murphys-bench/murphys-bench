# Murphy's Bench Screenshots

A tour of Murphy's Bench v0.18.0, grouped the way the sidebar groups them. It covers most screens but not all: Email Templates, Users, Logs, Notifications, and the Work Orders, Estimates, Mileage and Prospects lists are not shown yet. The [README](../README.md#screenshots) has a shorter tour.

All screenshots use demonstration data and dark mode. Light mode is a toggle at the bottom of the sidebar.

## Contents

- [Board](#board)
- [Work](#work): tickets, work orders, devices
- [Desk](#desk): clients, prospects, contracts
- [Register](#register)
- [Back Office](#back-office): catalog, knowledge base, queues, reports
- [Settings](#settings)

## Board

What needs attention now: past-SLA tickets, unanswered client replies, unsorted inbound email, and escalations. Administrators also see work ready to bill and unpaid invoices, as here. Below that are the open tickets, the bench, and the unsorted inbound list.

![Board](../screenshots/board.png)

## Work

### Tickets

Active and closed tickets, with filters for past SLA, awaiting reply, unsorted, status, and technician.

![Tickets](../screenshots/tickets.png)

### New ticket

Who it is for, what is wrong, and who it is assigned to. The New button at the top of the sidebar also starts a work order, client, estimate, or prospect.

![New ticket](../screenshots/ticket-new.png)

### Ticket and work order

A ticket converted to a work order stays on one page: client and device, the request, the client conversation, the work order, work performed, the checklist, bench notes, settlement, timer, attachments, and history.

![Ticket and its work order](../screenshots/ticket-work-order.png)

### Devices

Every device, its owner, type, make and model, and serial number.

![Devices](../screenshots/devices.png)

### Device detail

The machine's specifications, stored credentials, and repair history.

![Device detail](../screenshots/device-detail.png)

### Editing a device

Owner and contact, type, hardware, operating system, and the service agreement that covers it. A device with no owner is a walk-in device.

![Editing a device](../screenshots/device-edit.png)

## Desk

### Clients

Business and residential accounts, with their primary contact and device and work-order counts.

![Clients](../screenshots/clients.png)

### Client detail

A managed client: open work, service agreements, contacts, devices, work history, and follow-ups sent.

![Client detail](../screenshots/client-detail.png)

### Customer detail

The same page for a customer with no service agreement. Adding an active agreement makes them a client.

![Customer detail](../screenshots/customer-detail.png)

### New prospect

Someone you are talking to who is not a customer yet.

![New prospect](../screenshots/prospect-new.png)

### Contracts

Service agreements with their billing cadence, status, and recurring amount. Billing run opens a worklist where you prepare draft invoices for the agreements that are due and review them before anything is sent.

![Contracts](../screenshots/contracts.png)

### Contract detail

Terms, recurring charges, and the devices the agreement covers. The billing run produces draft invoices; Murphy's Bench never charges automatically.

![Contract detail](../screenshots/contract-detail.png)

### Editing a contract

Title, status, billing cadence, invoice day, dates, and auto-renew.

![Editing a contract](../screenshots/contract-edit.png)

## Register

Settle a finished work order or ring up a counter sale.

![Register](../screenshots/register.png)

## Back Office

### Products & Services

The priced catalog that work orders, contracts, and counter sales draw from.

![Products and Services](../screenshots/products-services.png)

### Knowledge Base

Shop reference and bench working material.

![Knowledge Base](../screenshots/knowledge-base.png)

### Ticket queues

Saved ticket filters. System queues are shared; personal queues belong to one user.

![Ticket queues](../screenshots/queues.png)

### Reports

Four report tabs over a date range, each with CSV, print, and PDF export.

Financial: invoiced, collected, outstanding, counter sales, and revenue.

![Financial report](../screenshots/reports-financial.png)

Tickets: volume over time, open tickets by status, tickets by client, and technician workload.

![Ticket report](../screenshots/reports-tickets.png)

Work orders: by status, per week, by client, and mileage by technician.

![Work order report](../screenshots/reports-work-orders.png)

Business metrics: resolution time, SLA compliance, backlog health, ticket-to-work-order conversion, and time logged.

![Business metrics report](../screenshots/reports-business-metrics.png)

## Settings

Settings is in the Back Office room and is for administrators. Its tabs are grouped in six sections. See [SETUP.md](../SETUP.md) for what to configure first.

### Company & Branding

Company: name, address, phone, email, and logo, with a preview of the repair report header.

![Company settings](../screenshots/settings-company.png)

Colors: sidebar and sign-in logos, the accent color, and one color for each room.

![Color settings](../screenshots/settings-colors.png)

Display: text size, stored in this browser only.

![Display settings](../screenshots/settings-display.png)

### Email

Outbound email: SMTP, which sending address each kind of email uses, suppression patterns, and internal notifications.

![Outbound email settings](../screenshots/settings-outbound-email.png)

Inbound email: the IMAP or POP3 mailbox that creates tickets and threads replies, the reopen window, and blocked senders.

![Inbound email settings](../screenshots/settings-inbound-email.png)

### Tickets & Work Orders

Repair types, grouped into categories or left uncategorised.

![Repair types](../screenshots/settings-repair-types.png)

Device types, with an icon each.

![Device types](../screenshots/settings-device-types.png)

Checklist items, optionally scoped to device types.

![Checklist items](../screenshots/settings-checklist-items.png)

Canned responses for customer notes and internal tech notes.

![Canned responses](../screenshots/settings-canned-responses.png)

Ticket and work order statuses. System statuses can change color but cannot be renamed or deleted.

![Statuses](../screenshots/settings-statuses.png)

Help topics, each with an optional default SLA.

![Help topics](../screenshots/settings-help-topics.png)

SLA plans, and the default SLA for business and residential clients.

![SLA plans](../screenshots/settings-sla-plans.png)

Tech skills.

![Tech skills](../screenshots/settings-tech-skills.png)

Custom fields on tickets or work orders, optionally scoped to a help topic or repair type.

![Custom fields](../screenshots/settings-custom-fields.png)

Knowledge base categories.

![KB categories](../screenshots/settings-kb-categories.png)

### Integrations

Invoice Ninja: a finished work order's priced lines go to Invoice Ninja as a draft invoice.

![Invoice Ninja settings](../screenshots/settings-invoice-ninja.png)

Attachments: upload size limit, blocked file types, and local or S3-compatible storage.

![Attachment settings](../screenshots/settings-attachments.png)

Mileage: the Google Maps key and the shop address that onsite trips start from.

![Mileage settings](../screenshots/settings-mileage.png)

### Access & Security

Account security: your own two-factor authentication.

![Account security](../screenshots/settings-account-security.png)

Roles: selected sensitive permissions are marked.

![Roles](../screenshots/settings-roles.png)

The organization credentials vault. Revealing or editing a credential is logged. Deleting a credential also removes its access history, including the record of the deletion.

![Credentials vault](../screenshots/settings-credentials.png)

Security: require two-factor authentication for every user.

![Security settings](../screenshots/settings-security.png)

### System

Maintenance: backup destinations and schedules, restore, and software updates.

![Maintenance](../screenshots/settings-maintenance.png)
