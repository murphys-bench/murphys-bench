# Murphy's Bench — Data Model & Settings Reference

> A map of the 34 data models and what the native Settings UI covers. For field-level detail see `docs/database-schema.md` in the repo.

## Core workflow objects

| Model | Role |
|---|---|
| **Client** | Company / customer. Everything is client-centric — work flows through the client. |
| **Contact** | A person at a client (supports multiple phones, notes, `receives_email`). |
| **Device** | Equipment being serviced (type, OS, serial, condition at intake, assigned contact, **encrypted credentials**). |
| **Ticket** | Initial service request + email conversation. Statuses: new → open → in_progress → waiting_on_customer → resolved → closed, plus `converted`. Has `contact` FK, `escalation_level`. |
| **TicketReply** | Threaded conversation entry (customer_visible or internal). |
| **WorkOrder** | The repair job. `service_type` (in_shop / onsite / remote), `time_spent_minutes`, optional OneToOne to originating ticket. |
| **WorkOrderNote** | Note on a WO (customer-visible = "shows on repair report"; or internal). |
| **WorkOrderItem** | Checklist items, parts, time entries (incl. pre/post-check fields). |
| **Invoice** | Billing-state tracker. OneToOne on WO, auto-created via signal. `billing_status`: uninvoiced / invoiced / paid / paid_direct / disputed. |
| **Mileage** | Travel logging (one_way / round_trip), optionally linked to a WO. |

## People & permissions

| Model | Role |
|---|---|
| **User** | Extended Django user; `role_obj` FK + `level` (1–3, for escalation) + skills M2M. |
| **Role** | Permission role with ~17 boolean flags. Seeded: Administrator, Technician. System roles protected. |
| **TechSkill** | Skill tags M2M on User (slated for retirement — superseded by levels). |

## Configuration / catalog

| Model | Role |
|---|---|
| **SiteSettings** | Singleton: SMTP, inbound mailbox, attachments, Google Maps key + shop address, colours, logo, `require_mfa`. |
| **SLAPlan** | Response-deadline config (grace period, overdue alerts). |
| **HelpTopic** | Ticket classification, optional default SLA. |
| **RepairType** | Repair category (Laptop Repair, Desktop Repair, …). |
| **Checklist / ChecklistItem** | Template task lists (scoped by device type). |
| **CannedResponse** | Reusable note templates (customer / internal streams). |
| **StatusDefinition** | Custom ticket/WO statuses with colours (`entity_type`, slug, label, colour, sort order). |
| **EmailTemplate / EmailSignature** | Outbound templates (4 triggers) + signatures. |
| **DashboardTile** | Configurable dashboard tiles (row, status filter, visible_to). |
| **CustomField / CustomFieldChoice / CustomFieldValue** | EAV extra fields scoped to HelpTopic/RepairType. |
| **KBCategory / KBArticle** | Knowledge base (Markdown-rendered; article types + `is_restricted`). |
| **TicketQueue** | Saved ticket filters (`filter_criteria` JSON; owner=null = system queue). |

## Credentials, security & audit

| Model | Role |
|---|---|
| **OrgCredential / CredentialAccessLog** | Org-level credentials vault (AES-256) + access audit. A competitive differentiator. |
| **DeviceCredentialAccessLog** | Logs every reveal/edit of device credentials. |
| **TicketLock** | Collision avoidance (OneToOne on Ticket, 10-min expiry). |
| **TicketLink** | Links related/duplicate tickets. |
| **Attachment** | GenericFK to Ticket/Reply/WO/Note; local or S3 storage. |
| **Notification** | Per-user in-app alert (sidebar bell). First producer: internal tech-to-tech messaging. |
| **SuppressedAddress / EmailSendLog / InboundEmailLog** | Mail suppression + outbound/inbound audit. |

## Number formats

- Work orders: `WO-YYYYMMDD-NNNN`
- Tickets: `TKT-YYYYMMDD-NNNN` (newer ones are sequential, e.g. `TKT-00005`)
- Assignment is **collision-resistant** (`_save_with_unique_number()` retries on `IntegrityError`).

## Encrypted fields (AES-256, `FIELD_ENCRYPTION_KEY`)

- `Device.device_username / device_password / credential_notes`
- `WorkOrder.device_username / device_password / device_pin / credential_notes`
- `SiteSettings.email_password / inbound_password`
- `OrgCredential` username / password / notes

> These store **ciphertext** in the DB. See *Backup & Disaster Recovery* — the key is required to read them back.

## Native Settings UI (`/settings/`, admin only)

Everything routine is in native Settings — Django admin is break-glass only:

**Company · Outbound Email · Inbound Email · Email Templates (+ branding + signatures) · Attachments · Security/MFA · Mileage · Statuses (+ colours) · Help Topics · SLA Plans · Repair Types · Canned Responses · Quick Labor · Checklist Items · KB Categories · Dashboard Tiles · Custom Fields · Blocked Senders · Org Credentials · Users · Roles**

Still requires Django admin (by design): superuser / `is_staff` management, and emergency fixes for records in a bad state.

## Migrations

Through **0051**. Always apply new migrations in **both** dev and prod (see *Development & Deploy Workflow*).
