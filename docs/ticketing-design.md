# Murphy's Bench Ticketing System Design

**Date**: June 4, 2026  
**Status**: Design Finalized — Data Layer Complete, Views In Progress

---

## Overview

Murphy's Bench includes a **built-in ticketing system** that serves as the entry point for all service work. Tickets are converted to Work Orders, creating a unified workflow from initial request through completion and invoicing.

---

## 1. Ticket Creation & Ingestion

### Primary Sources

Tickets are created from three sources:

1. **Email (Primary)**
   - Direct emails to a domain-based address (e.g., `tickets@company.domain`)
   - RMM system emails (N-Central, ConnectWise, etc.)
   - Phone notifications that someone transcribes
   - **Approach**: Email parser extracts subject, body, sender, attachments
   - **Implementation**: Email handler service (Phase 1 or 1.5)

2. **Taskbar Utility App** (Future)
   - Simple notification/quick-entry app in system tray
   - Tech can quickly create a ticket from their desk
   - Sends ticket via email to Murphy's Bench (SMTP)
   - **Status**: Designed (API endpoint ready), separate project

3. **Phone (Admin Quick-Entry)**
   - Receptionist or admin enters tickets via web form
   - Quick form in admin panel for expedited entry
   - **Status**: Will be part of Phase 1 views

### Email-Based Workflow

```
Email arrives → Parser extracts:
  - From: sender email (match to existing contact or create)
  - To: tickets@company.domain (identifies company)
  - Subject: → ticket subject
  - Body: → ticket description
  - Attachments: → stored with ticket
  ↓
Create Ticket record:
  - client: matched from email domain or contact
  - device: optional (may not be known yet)
  - subject, description: from email
  - source: 'email'
  - status: 'open'
  - created_by: system or matched technician
  ↓
Send confirmation email back to sender
  - Ticket number (TKT-20260604-0001)
  - Status updates will come via reply-to-same-thread
```

### Key Design Decisions

- **Email is stateless**: Messages are parsed into tickets/notes, not stored as blobs
- **No vendor lock-in**: Works with any email provider
- **Audit trail**: All communication is in the ticket via notes
- **Offline-safe**: Email works even if system is down temporarily

---

## 2. Ticket → Work Order Conversion Workflow

### When to Convert

A ticket is converted to a Work Order when:
- The problem scope is understood
- A technician is assigned
- Repair type is identified
- Device is identified (or determined it's not device-specific)

### Conversion Process

```
Ticket (open, in_progress, or closed)
  ↓
User clicks "Convert to Work Order"
  ↓
Create WorkOrder:
  - work_order_number: auto-generated (WO-20260604-0001)
  - ticket: link back to original ticket
  - client: copied from ticket
  - device: copied from ticket (or selected)
  - repair_type: selected by tech
  - assigned_to: selected technician
  - status: 'new'
  - notes_customer_visible: copied from ticket
  ↓
Update Ticket:
  - status: 'converted'
  - work_order: linked to new WO
  ↓
WorkOrder is now the source of truth
  - Ticket becomes historical record
  - All future updates go on WorkOrder
```

### Key Design Decisions

- **One-way conversion**: Ticket → WorkOrder (not bidirectional)
- **Link preserved**: Ticket is archived but linked for history
- **Flexibility**: Not all tickets need conversion (some close as-is)
- **Clean separation**: Ticket is request, WorkOrder is execution

---

## 3. Ticket & Work Order Statuses

### Ticket Statuses

| Status | Meaning | Next State |
|--------|---------|-----------|
| `new` | Just created, not yet triaged | open |
| `open` | Acknowledged, waiting for tech action | in_progress or converted |
| `in_progress` | Being evaluated/diagnosed | waiting_on_customer, converted, or resolved |
| `waiting_on_customer` | Awaiting info or action from client | in_progress or resolved |
| `resolved` | Issue resolved, pending confirmation | closed or reopened |
| `closed` | Resolved without WO (e.g., question answered) | — |
| `converted` | Converted to Work Order | — (historical) |

**Rules:**
- Once `converted` or `closed`, ticket is read-only
- Can only have ONE associated Work Order (OneToOne)
- All replies tracked via TicketReply model

### Work Order Statuses

| Status | Meaning | Next State |
|--------|---------|-----------|
| `new` | Just created from ticket | assigned |
| `assigned` | Tech assigned, not started | in_progress |
| `in_progress` | Tech actively working | completed |
| `completed` | Work finished, ready for invoice | closed |
| `closed` | Final, invoiced/paid | — |
| `cancelled` | Abandoned, won't be completed | — |

**Rules:**
- Status transitions are sequential (mostly)
- `completed_date` is set when marked completed
- Can be cancelled at any point
- Moves to Invoice Ninja when completed

---

## 4. Required Fields

### Ticket (Minimal)

- `ticket_number` (auto-generated)
- `client` (required)
- `subject` (required)
- `description` (required)
- `source` (email, phone, web, rmm)
- `created_by` (user who created it)

### Optional for Ticket

- `device` (may not know yet)
- `contact` (inferred from email)

### Work Order (Required)

- `work_order_number` (auto-generated)
- `client` (from ticket)
- `repair_type` (required to pick which checklist)
- `assigned_to` (required)
- `status`

### Optional for Work Order

- `device` (some jobs aren't device-specific)
- `scheduled_date`
- `notes_internal` (tech-only)
- `notes_customer_visible` (what customer sees)

---

## 5. Invoice Ninja Integration

### Design Approach

Murphy's Bench → Invoice Ninja is a **one-way API push**, not a bidirectional sync.

```
WorkOrder completed
  ↓
Checklist done, all items logged
  ↓
Tech marks: Status = "completed"
  ↓
Murphy's Bench API triggers:
POST /api/invoices/ to Invoice Ninja
  {
    "client_id": "...",
    "work_order_id": "WO-20260604-0001",
    "description": "Work on Mike's Laptop",
    "line_items": [
      { "description": "Diagnostic", "qty": 1, "rate": 75.00 },
      { "description": "Virus removal", "qty": 1, "rate": 150.00 },
      { "description": "System optimization", "qty": 1, "rate": 50.00 }
    ],
    "amount": 275.00
  }
  ↓
Invoice Ninja creates invoice, sends to client
  ↓
Invoice Ninja handles payment/tracking (separate system)
```

### Key Design Decisions

- **Murphy's Bench is source of truth for work**
  - Tracks what was done, time, materials
  - Generates invoice data

- **Invoice Ninja is financial backend**
  - Handles invoicing, payments, reporting
  - Murphy's Bench doesn't pull invoice status back

- **No sync loop**
  - Murphy's Bench sends invoice data once
  - If correction needed, manual adjustment in Invoice Ninja
  - Keeps systems loosely coupled

- **Phase 2+ implementation**
  - Not in Phase 1 (Phase 2 after SCS internal is stable)
  - Use Invoice Ninja REST API
  - Authentication via API token (configured in settings)

### Integration Points

**What Murphy's Bench sends to Invoice Ninja:**
- Client info (name, email, contact)
- Work description and time logged
- Parts/materials used
- Total amount

**What Invoice Ninja handles:**
- Invoice number generation
- Payment terms and due dates
- Sending invoices to client
- Payment tracking
- Tax calculations (if configured)
- Reporting/analytics

---

## 6. Email-Based Communication

### Ongoing Ticket Communication

Once a ticket exists, all further communication happens via email reply:

```
Customer replies to ticket email:
  → Email lands in tickets@ inbox
  → Parser extracts reply
  → Adds to Ticket as WorkOrderNote (type: customer_visible)
  → Ticket/WO updated in Murphy's Bench
  ↓
Tech can reply via Murphy's Bench UI:
  → Composes note in web interface
  → Selects "Send to customer"
  → Murphy's Bench sends email to customer
  → Preserves thread (same subject, Reply-To)
```

### Benefits

- **No context switching**: Customer stays in email thread
- **Audit trail**: All communication in one place
- **Asynchronous**: Works offline, doesn't require real-time sync
- **Simple**: No app integration, just email

---

## 7. Data Model Summary

### Key Relationships

```
Ticket 1 → 0..1 WorkOrder (one ticket converts to at most one WO)
Ticket N → 1 Client
Ticket N → 1 Device (optional)
Ticket 1 → N TicketReply (threaded conversation)

TicketReply N → 1 Ticket
TicketReply N → 1 User (created_by)

WorkOrder 1 → 0..1 Ticket (optional link back to originating ticket)
WorkOrder N → 1 Client
WorkOrder N → 1 Device (optional)
WorkOrder N → 1 RepairType
WorkOrder N → 1 Technician (assigned_to)
WorkOrder 1 → N WorkOrderNote (conversation)
WorkOrder 1 → N WorkOrderItem (checklist, parts, time)
```

### Auto-Generated Numbers

- **Tickets**: TKT-YYYYMMDD-0001 (daily counter)
- **Work Orders**: WO-YYYYMMDD-0001 (daily counter)
- Easy to read, searchable, sortable by date

---

## 8. Implementation Notes

### Phase 1 (Current)

- [x] Ticket model and fields
- [x] TicketReply model (threaded conversation)
- [x] Ticket statuses expanded (new, open, in_progress, waiting_on_customer, resolved, closed, converted)
- [x] WorkOrder model with ticket FK
- [x] Migration 0002 applied
- [x] Admin interface for all ticket/WO models (with inline replies)
- [ ] Views: ticket list, detail, create, reply form
- [ ] Convert ticket → work order (UI button + view)
- [ ] Native ticket create form (no admin required)
- [ ] Email ingestion: manual copy-paste into form (Phase 1 — automated is Phase 2)

### Phase 1.5 (Taskbar App)

- [ ] Simple taskbar utility (Electron — separate project)
- [ ] Quick ticket creation form
- [ ] Screenshot capture
- [ ] Send via email to Murphy's Bench

### Phase 2 (Integrations)

- [ ] Automated email parsing service (inbound SMTP/IMAP)
- [ ] Invoice Ninja API integration (one-way push on WO completion)
- [ ] Email reply-to handling (replies update ticket)
- [ ] Slack/Teams notifications (optional)

---

## Questions to Answer in Next Session

- Should email replies automatically update ticket status?
- What triggers a ticket to auto-close (timeout, customer closure)?
- Should Murphy's Bench send email notifications for status changes?
- How should attachments from email be stored/displayed?

---

**Related Files:**
- `database-schema.md` - Detailed field definitions
- `CLAUDE.md` - Project overview
- `core/models.py` - Ticket and WorkOrder models
