# Murphy's Bench Database Schema

**Version**: 1.1  
**Last Updated**: June 4, 2026  
**Database**: SQLite (production and dev; PostgreSQL supported via DB_ENGINE but not used)

---

## Entity Relationship Diagram (ERD)

```
┌─────────────────────────────────────────────────────────────────┐
│                         CORE ENTITIES                            │
├─────────────────────────────────────────────────────────────────┤

    User/Technician
           ↓
           ├─→ Ticket (created_by)
           ├─→ WorkOrder (assigned_to)
           └─→ Mileage (technician)

    Client
           ├─→ Contact (belongs to)
           ├─→ Device (belongs to)
           ├─→ Ticket (reported by)
           └─→ WorkOrder (for)

    Device
           ├─→ RepairType (categorized by)
           └─→ WorkOrder (associated with)

    Ticket
           ├─→ Client (reported by)
           ├─→ Device (about device)
           ├─→ TicketReply (conversation thread)
           └─→ WorkOrder (converts to)

    TicketReply
           ├─→ Ticket (belongs to)
           └─→ User (created by)

    WorkOrder
           ├─→ Client (for)
           ├─→ Device (affects)
           ├─→ Ticket (created from)
           ├─→ RepairType (categorized by)
           ├─→ Technician (assigned to)
           ├─→ WorkOrderNote (contains)
           ├─→ WorkOrderItem (contains)
           └─→ Checklist (uses)

    RepairType
           ├─→ Checklist (has default)
           └─→ CannedResponse (associated with)

    Checklist
           └─→ ChecklistItem (contains)

└─────────────────────────────────────────────────────────────────┘
```

---

## Table Definitions

### 1. User / Technician

**Purpose**: Staff members who create tickets, work on jobs, and log time

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| username | String(100) | UNIQUE, NOT NULL | Login credential |
| email | String(255) | NOT NULL | Contact info |
| password_hash | String(255) | NOT NULL | Bcrypt hashed |
| first_name | String(100) | NOT NULL | |
| last_name | String(100) | NOT NULL | |
| phone | String(20) | | Optional contact |
| is_active | Boolean | DEFAULT true | Soft deactivation |
| role | String(20) | DEFAULT 'technician' | 'admin', 'technician', 'viewer' |
| created_at | DateTime | DEFAULT now() | Audit |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | Audit |

**Indexes**: username (unique), email, is_active

---

### 2. Client

**Purpose**: Companies/customers requesting service

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| name | String(255) | NOT NULL, UNIQUE | Company name |
| email | String(255) | | Main contact email |
| phone | String(20) | | Main contact phone |
| address_street | String(255) | | Physical address |
| address_city | String(100) | | |
| address_state | String(2) | | |
| address_zip | String(10) | | |
| notes | Text | | General notes |
| is_active | Boolean | DEFAULT true | |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: name (unique), is_active

---

### 3. Contact

**Purpose**: Individual people at client companies

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| client_id | Integer | FK → Client, NOT NULL | |
| first_name | String(100) | NOT NULL | |
| last_name | String(100) | NOT NULL | |
| email | String(255) | | |
| phone | String(20) | | |
| title | String(100) | | Job title |
| is_primary | Boolean | DEFAULT false | Primary contact for client |
| is_active | Boolean | DEFAULT true | |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: client_id, email, phone, is_primary

---

### 4. Device

**Purpose**: Equipment being serviced

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| client_id | Integer | FK → Client, NOT NULL | |
| repair_type_id | Integer | FK → RepairType, NOT NULL | |
| name | String(255) | NOT NULL | Device name (e.g., "Mike's Laptop") |
| device_type | String(50) | NOT NULL | 'laptop', 'desktop', 'server', 'mobile', etc. |
| serial_number | String(100) | UNIQUE | |
| model | String(100) | | |
| manufacturer | String(100) | | |
| notes | Text | | Device-specific notes |
| is_active | Boolean | DEFAULT true | |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: client_id, device_type, serial_number

---

### 5. Ticket

**Purpose**: Initial service request (starts workflow)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| ticket_number | String(20) | UNIQUE | "TKT-20260603-0001" |
| client_id | Integer | FK → Client, NOT NULL | |
| device_id | Integer | FK → Device | Optional (may not know device yet) |
| subject | String(255) | NOT NULL | Brief description |
| description | Text | NOT NULL | Full problem description |
| source | String(20) | NOT NULL | 'email', 'phone', 'web', 'rmm' |
| status | String(20) | DEFAULT 'new' | 'new', 'open', 'in_progress', 'waiting_on_customer', 'resolved', 'closed', 'converted' |
| created_by_id | Integer | FK → User | Tech who created ticket |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: ticket_number (unique), client_id, status, source, created_at

---

### 5a. TicketReply

**Purpose**: Threaded conversation on a ticket (replies, internal notes, status updates)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| ticket_id | Integer | FK → Ticket, NOT NULL | Parent ticket |
| reply_type | String(20) | NOT NULL | 'customer_visible', 'internal' |
| content | Text | NOT NULL | The reply text |
| created_by_id | Integer | FK → User | Staff author (null if system-generated) |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: ticket_id, reply_type, created_at

---

### 6. WorkOrder

**Purpose**: Repair job (main entity)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| work_order_number | String(20) | UNIQUE | "WO-20260603-0001" |
| ticket_id | Integer | FK → Ticket | Created from ticket |
| client_id | Integer | FK → Client, NOT NULL | |
| device_id | Integer | FK → Device | |
| repair_type_id | Integer | FK → RepairType, NOT NULL | |
| assigned_to_id | Integer | FK → User | Assigned technician |
| status | String(20) | DEFAULT 'new' | 'new', 'assigned', 'in_progress', 'completed', 'closed', 'cancelled' |
| priority | String(20) | DEFAULT 'normal' | 'low', 'normal', 'high', 'urgent' |
| time_spent_minutes | Integer | DEFAULT 0 | Actual time spent |
| scheduled_date | Date | | When it should be done |
| completed_date | DateTime | | When actually completed |
| notes_internal | Text | | Technician-only notes |
| notes_customer_visible | Text | | What customer sees |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: work_order_number (unique), client_id, device_id, status, assigned_to_id, created_at

---

### 7. WorkOrderNote

**Purpose**: Comments/updates on a work order

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| work_order_id | Integer | FK → WorkOrder, NOT NULL | |
| note_type | String(20) | NOT NULL | 'customer_visible', 'internal' |
| content | Text | NOT NULL | The actual note |
| created_by_id | Integer | FK → User, NOT NULL | Who wrote it |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: work_order_id, note_type, created_at

**Note**: Incoming email replies are parsed and added as notes with type='customer_visible'

---

### 8. WorkOrderItem

**Purpose**: Line items on a work order (checklist, parts, time entries)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| work_order_id | Integer | FK → WorkOrder, NOT NULL | |
| item_type | String(20) | NOT NULL | 'checklist', 'part', 'time', 'other' |
| description | String(255) | NOT NULL | What is this item |
| quantity | Decimal(10,2) | DEFAULT 1 | For parts or time |
| unit | String(20) | | 'hours', 'each', 'qty', etc. |
| unit_price | Decimal(10,2) | | Cost per unit |
| is_completed | Boolean | DEFAULT false | For checklist items |
| notes | Text | | Additional info |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: work_order_id, item_type, is_completed

---

### 9. Mileage

**Purpose**: Travel logging for billing/expense tracking

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| technician_id | Integer | FK → User, NOT NULL | |
| trip_date | Date | NOT NULL | |
| from_location | String(255) | | Starting point |
| to_location | String(255) | | Ending point |
| miles | Decimal(10,2) | NOT NULL | Distance traveled |
| purpose | String(255) | | Why the trip |
| work_order_id | Integer | FK → WorkOrder | Optional link to job |
| notes | Text | | Additional info |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: technician_id, trip_date, work_order_id

---

### 10. RepairType

**Purpose**: Categories of repairs (e.g., "Laptop Repair")

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| name | String(100) | UNIQUE, NOT NULL | e.g., "Laptop Repair" |
| description | Text | | |
| default_checklist_id | Integer | FK → Checklist | Which checklist to use |
| is_active | Boolean | DEFAULT true | |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: name (unique), is_active

---

### 11. Checklist

**Purpose**: Templates of standard tasks for repair types

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| repair_type_id | Integer | FK → RepairType | |
| name | String(100) | NOT NULL | e.g., "Standard Laptop Service" |
| description | Text | | |
| is_active | Boolean | DEFAULT true | |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: repair_type_id, is_active

---

### 12. ChecklistItem

**Purpose**: Individual tasks in a checklist template

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| checklist_id | Integer | FK → Checklist, NOT NULL | |
| description | String(255) | NOT NULL | Task description |
| sort_order | Integer | DEFAULT 0 | Display order |
| is_active | Boolean | DEFAULT true | |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: checklist_id, is_active, sort_order

---

### 13. CannedResponse

**Purpose**: Template responses for common situations

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | Integer | PK, auto-increment | |
| repair_type_id | Integer | FK → RepairType | Optional categorization |
| title | String(100) | NOT NULL | e.g., "Virus Removal Complete" |
| content | Text | NOT NULL | Template text |
| is_active | Boolean | DEFAULT true | |
| created_at | DateTime | DEFAULT now() | |
| updated_at | DateTime | DEFAULT now(), AUTO UPDATE | |

**Indexes**: repair_type_id, is_active

---

## Key Relationships

### Ticket → WorkOrder
- One Ticket can create ONE WorkOrder
- A WorkOrder is created FROM a Ticket (not all tickets become WOs, some might be closed as-is)

### Client → Devices
- One Client has MANY Devices
- Devices are the assets being serviced

### WorkOrder → WorkOrderNotes
- One WorkOrder has MANY Notes
- Notes are segregated by type (customer_visible vs. internal)

### WorkOrder → WorkOrderItems
- One WorkOrder has MANY Items (checklist, parts, time)
- Provides flexibility for different repair scenarios

### RepairType → Checklist
- One RepairType can have ONE default Checklist
- Checklist defines standard tasks for that repair type

---

## Data Integrity Rules

1. **Cascade deletes**: Deleting a Client SHOULD NOT cascade (preserve history), instead soft-delete
2. **Status workflows**:
   - Ticket: new → open → in_progress → waiting_on_customer → resolved → closed OR converted (to WorkOrder)
   - WorkOrder: new → assigned → in_progress → completed → closed
3. **Work order completion**: When completed_date is set, status becomes 'completed'
4. **Audit trail**: All tables have created_at and updated_at for tracking changes

---

## Indexes (Performance)

**High-traffic lookups**:
- `tickets(client_id, status)`
- `work_orders(client_id, status, assigned_to_id)`
- `work_orders(created_at)` - for dashboards
- `work_order_items(work_order_id, is_completed)`

---

## Notes

1. **Email storage**: Email messages are NOT stored in the database. They're parsed into Tickets and Notes. This keeps storage lean.

2. **Historical data**: Soft-delete (is_active flag) is preferred over hard delete to preserve audit trail.

3. **Scalability**: For SCS's current scale (solo/small team), this schema is more than sufficient. Indexes focus on the common queries (by client, by status, by technician).

4. **Open-source ready**: Schema is generic enough for other MSPs to use without modification.
