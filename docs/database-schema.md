# Murphy's Bench Database Schema

**Version**: 2.0  
**Last Updated**: June 26, 2026  
**Database**: SQLite (production and dev — a single file, no DB server)  
**Migrations**: through 0065  

> This reference is **generated from `core/models.py`** (the live models, as of
> migration 0065). It is the field-level companion to
> `docs/bookstack/07-data-model-and-settings-reference.md` (the conceptual map).
> To regenerate after model changes, re-run the introspection helper used to
> produce this file (see the doc-sweep notes) — don't hand-edit field rows.

🔒 = encrypted at rest (AES-256 via django-encrypted-model-fields).

**47 models.** Alphabetical.

---

## Attachment
_File attachment linked to any model via GenericForeignKey._
`db_table = attachments`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| content_type | ForeignKey | → ContentType |
| object_id | PositiveIntegerField |  |
| file | FileField |  |
| original_filename | CharField |  |
| mime_type | CharField | blank |
| size_bytes | PositiveIntegerField |  |
| uploaded_by | ForeignKey | → User, null |
| created_at | DateTimeField | blank |
| content_object | GenericForeignKey |  |

## BlockedSender
_Inbound email senders that are silently dropped — no ticket or reply created._
`db_table = blocked_senders`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| pattern | CharField | unique |
| reason | CharField | blank |
| created_at | DateTimeField | blank |

## CannedResponse
`db_table = canned_responses`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| stream | CharField | choices: customer/internal |
| category | ForeignKey | → CannedResponseCategory, null, blank |
| label | CharField |  |
| body | TextField |  |
| sort_order | PositiveIntegerField |  |
| created_at | DateTimeField | blank |

## CannedResponseCategory
`db_table = canned_response_categories`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| stream | CharField | choices: customer/internal |
| name | CharField |  |
| sort_order | PositiveIntegerField |  |

## Checklist
_Templates of standard tasks for repair types_
`db_table = checklists`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| repair_type | ForeignKey | → RepairType, null, blank |
| name | CharField |  |
| description | TextField | blank |
| is_active | BooleanField |  |
| is_default | BooleanField |  |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## ChecklistItem
_Flat bank of checklist tasks, each scoped to one or more device types._
`db_table = checklist_items`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| name | CharField |  |
| device_types | JSONField | blank |
| sort_order | IntegerField |  |
| is_active | BooleanField |  |
| created_at | DateTimeField | blank |

## Client
_Companies/customers requesting service_
`db_table = clients`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| name | CharField | unique |
| client_type | CharField | choices: residential/business |
| email | CharField | blank |
| phone | CharField | blank |
| address_line1 | CharField | blank |
| address_line2 | CharField | blank |
| address_city | CharField | blank |
| address_state | CharField | blank |
| address_zip | CharField | blank |
| notes | TextField | blank |
| is_active | BooleanField |  |
| suppress_emails | BooleanField |  |
| is_unsorted | BooleanField |  |
| invoice_ninja_id | CharField | blank |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## Contact
_Individual people at client companies_
`db_table = contacts`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| client | ForeignKey | → Client |
| first_name | CharField |  |
| last_name | CharField |  |
| email | CharField | blank |
| phone | CharField | blank |
| title | CharField | blank |
| is_primary | BooleanField |  |
| is_active | BooleanField |  |
| notes | TextField | blank |
| receives_email | BooleanField |  |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## ContactPhone
_Additional phone numbers for a contact (beyond the primary phone field)._
`db_table = contact_phones`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| contact | ForeignKey | → Contact |
| number | CharField |  |
| phone_type | CharField | choices: cell/home/work/other |
| label | CharField | blank |

## CredentialAccessLog
_Audit log — every reveal, copy, edit, or delete of an OrgCredential._
`db_table = core_credentialaccesslog`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| credential | ForeignKey | → OrgCredential |
| user | ForeignKey | → User, null |
| action | CharField | choices: viewed/edited/deleted |
| accessed_at | DateTimeField | blank |

## CustomField
_Admin-defined extra fields for Tickets or Work Orders._
`db_table = custom_fields`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| label | CharField |  |
| field_type | CharField | choices: text/textarea/select/checkbox/date |
| applies_to | CharField | choices: ticket/workorder/both |
| is_required | BooleanField |  |
| help_text | CharField | blank |
| sort_order | IntegerField |  |
| is_active | BooleanField |  |
| scoped_to_help_topic | ForeignKey | → HelpTopic, null, blank |
| scoped_to_repair_type | ForeignKey | → RepairType, null, blank |

## CustomFieldChoice
_Options for select-type CustomFields._
`db_table = custom_field_choices`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| field | ForeignKey | → CustomField |
| label | CharField |  |
| sort_order | IntegerField |  |

## CustomFieldValue
_EAV storage — one row per (object, field) pair._
`db_table = custom_field_values`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| content_type | ForeignKey | → ContentType |
| object_id | PositiveIntegerField |  |
| field | ForeignKey | → CustomField |
| value | TextField | blank |
| content_object | GenericForeignKey |  |

## DashboardTile
_Configurable tile on the dashboard. Two rows: ticket and workorder._
`db_table = dashboard_tiles`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| row | CharField | choices: ticket/workorder |
| label | CharField |  |
| status_filter | JSONField |  |
| link_url | CharField | blank |
| sort_order | IntegerField |  |
| is_active | BooleanField |  |
| visible_to | CharField | choices: all/admin/tech |
| icon | CharField | blank |

## Device
_Equipment being serviced_
`db_table = devices`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| client | ForeignKey | → Client |
| repair_type | ForeignKey | → RepairType, null |
| assigned_contact | ForeignKey | → Contact, null, blank |
| name | CharField |  |
| device_type | CharField | choices: laptop/desktop/server/mobile/tablet/printer/other |
| serial_number | CharField | null, blank, unique |
| model | CharField | blank |
| manufacturer | CharField | blank |
| os | CharField | blank, choices: windows/macos/linux/ios/android/chromeos/other |
| os_version | CharField | blank |
| cpu | CharField | blank |
| ram | CharField | blank |
| storage | CharField | blank |
| condition_at_intake | CharField | blank, choices: used/new/damaged |
| notes | TextField | blank |
| device_username | TextField | blank, 🔒 encrypted |
| device_password | TextField | blank, 🔒 encrypted |
| credential_notes | TextField | blank, 🔒 encrypted |
| is_active | BooleanField |  |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## DeviceCredentialAccessLog
_Audit log — every reveal or edit of a Device's encrypted credentials._
`db_table = core_devicecredentialaccesslog`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| device | ForeignKey | → Device |
| user | ForeignKey | → User, null |
| action | CharField | choices: viewed/edited |
| field | CharField | blank |
| accessed_at | DateTimeField | blank |

## EmailSendLog
_Audit log of every attempted outbound email — sent or suppressed._
`db_table = email_send_log`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| ticket | ForeignKey | → Ticket, null |
| to_email | CharField | blank |
| trigger | CharField |  |
| status | CharField | choices: sent/suppressed/failed |
| reason | CharField | blank |
| detail | CharField | blank |
| created_at | DateTimeField | blank |

## EmailSignature
_Reusable email signature blocks. One can be marked as default._
`db_table = email_signatures`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField | unique |
| body | TextField |  |
| is_default | BooleanField |  |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## EmailTemplate
_Trigger-based email templates sent to clients on ticket events._
`db_table = email_templates`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| trigger | CharField | unique, choices: ticket_created/reply_added/status_changed/ticket_resolved |
| subject_template | CharField |  |
| body_template | TextField |  |
| signature | ForeignKey | → EmailSignature, null, blank |
| is_active | BooleanField |  |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## HelpTopic
_Ticket classification topic with optional default SLA._
`db_table = help_topics`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField | unique |
| description | TextField | blank |
| default_sla | ForeignKey | → SLAPlan, null, blank |
| is_active | BooleanField |  |
| sort_order | IntegerField |  |

## InboundEmailLog
_Audit log for every message fetched from the inbound mailbox._
`db_table = inbound_email_log`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| message_id | CharField | blank |
| from_email | CharField | blank |
| subject | CharField | blank |
| ticket | ForeignKey | → Ticket, null, blank |
| status | CharField | choices: new_ticket/reply/duplicate/error/processing |
| detail | TextField | blank |
| created_at | DateTimeField | blank |

## Invoice
_Billing state tracker for a WorkOrder. Tracks status only — not an accounting module._
`db_table = invoices`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| work_order | OneToOneField | → WorkOrder, unique |
| billing_status | CharField | choices: uninvoiced/invoiced/paid/paid_direct/disputed |
| amount | DecimalField | null, blank |
| invoiced_date | DateField | null, blank |
| paid_date | DateField | null, blank |
| payment_method | CharField | blank, choices: cash/check/card/transfer/other |
| notes | TextField | blank |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## KBArticle
_Internal knowledge base article._
`db_table = kb_articles`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| title | CharField |  |
| content | TextField |  |
| category | ForeignKey | → KBCategory, null, blank |
| article_type | CharField | choices: troubleshooting/how_to/vendor/internal |
| author | ForeignKey | → User, null |
| is_active | BooleanField |  |
| is_restricted | BooleanField |  |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## KBCategory
_Knowledge base article category._
`db_table = kb_categories`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField | unique |
| description | TextField | blank |
| sort_order | IntegerField |  |

## LineItem
_A priced line on a work order (and, in a future phase, a quote). Deliberately GENERIC/attachable via a GenericForeignKey so the same primitive can hang off a WorkOrder now and a Quote later without a rebuild. MB captures and totals prices here; Invoice Ninja remains the billing authority — MB only suggests, it does not invoice._
`db_table = line_items`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| content_type | ForeignKey | → ContentType |
| object_id | PositiveIntegerField |  |
| kind | CharField | choices: labor/part |
| description | CharField |  |
| quantity | DecimalField |  |
| unit_price | DecimalField | null, blank |
| source_labor_item | ForeignKey | → QuickLaborItem, null, blank |
| notes | TextField | blank |
| logged_by | ForeignKey | → User, null |
| logged_at | DateTimeField | blank |
| content_object | GenericForeignKey |  |

## MFAResetLog
_Audit record — every reset (clearing) of a user's two-factor devices. Resets are lost-device recovery and a sensitive action, so each one is recorded: who was reset, who did it (null = CLI break-glass), and how._
`db_table = core_mfaresetlog`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| target | ForeignKey | → User |
| actor | ForeignKey | → User, null, blank |
| source | CharField | choices: web/cli |
| note | CharField | blank |
| created_at | DateTimeField | blank |

## Mileage
_Travel logging for billing/expense tracking_
`db_table = mileage`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| technician | ForeignKey | → User |
| trip_date | DateField |  |
| from_location | CharField | blank |
| to_location | CharField | blank |
| miles | DecimalField |  |
| trip_type | CharField | choices: one_way/round_trip |
| purpose | CharField | blank |
| work_order | ForeignKey | → WorkOrder, null, blank |
| notes | TextField | blank |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## Notification
_An in-app alert for a user — e.g. an internal tech message that needs a timely response. Surfaced via the sidebar bell with an unread count._
`db_table = notifications`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| recipient | ForeignKey | → User |
| actor | ForeignKey | → User, null, blank |
| kind | CharField | choices: tech_message/system_alert |
| text | CharField |  |
| ticket | ForeignKey | → Ticket, null, blank |
| work_order | ForeignKey | → WorkOrder, null, blank |
| is_read | BooleanField |  |
| created_at | DateTimeField | blank |
| read_at | DateTimeField | null, blank |

## OrgCredential
_Shared organizational credential vault entry. Encrypted at rest._
`db_table = core_orgcredential`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField |  |
| username | TextField | blank, 🔒 encrypted |
| password | TextField | blank, 🔒 encrypted |
| url | CharField | blank |
| category | CharField | choices: email/remote/cloud/network/vendor/other |
| notes | TextField | blank, 🔒 encrypted |
| admin_only | BooleanField |  |
| created_by | ForeignKey | → User, null |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## QuickLaborItem
_Admin-managed labor buttons shown on WO detail. Clicking one logs a labor LineItem._
`db_table = quick_labor_items`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| label | CharField |  |
| category | CharField |  |
| print_description | TextField | blank |
| default_price | DecimalField | null, blank |
| is_active | BooleanField |  |
| sort_order | IntegerField |  |

## RepairType
_Categories of repairs (e.g., Laptop Repair, Desktop Repair)_
`db_table = repair_types`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| category | ForeignKey | → RepairTypeCategory, null, blank |
| name | CharField | unique |
| description | TextField | blank |
| sort_order | PositiveIntegerField |  |
| is_active | BooleanField |  |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## RepairTypeCategory
_Grouping for repair types, e.g. Hardware, Software, Networking._
`db_table = repair_type_categories`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField | unique |
| sort_order | PositiveIntegerField |  |

## Role
_Permission role assigned to users._
`db_table = roles`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField | unique |
| description | TextField | blank |
| is_system | BooleanField |  |
| can_manage_settings | BooleanField |  |
| can_view_all_tickets | BooleanField |  |
| can_close_tickets | BooleanField |  |
| can_manage_users | BooleanField |  |
| can_view_reports | BooleanField |  |
| can_view_restricted_kb | BooleanField |  |
| can_manage_kb | BooleanField |  |
| can_create_ticket | BooleanField |  |
| can_edit_ticket | BooleanField |  |
| can_delete_ticket | BooleanField |  |
| can_assign_ticket | BooleanField |  |
| can_reply_internal | BooleanField |  |
| can_reply_customer | BooleanField |  |
| can_view_device_credentials | BooleanField |  |
| can_reset_user_mfa | BooleanField |  |
| can_create_workorder | BooleanField |  |
| can_edit_workorder | BooleanField |  |
| can_close_workorder | BooleanField |  |

## SLAPlan
_Service Level Agreement — defines response/resolution deadline._
`db_table = sla_plans`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField | unique |
| grace_period_hours | PositiveIntegerField |  |
| is_active | BooleanField |  |
| is_transient | BooleanField |  |
| disable_overdue_alerts | BooleanField |  |

## SiteSettings
_Singleton — site-wide configuration editable from admin._
`db_table = site_settings`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| max_attachment_size_mb | IntegerField |  |
| blocked_extensions | TextField |  |
| storage_backend | CharField | choices: local/s3 |
| local_storage_path | CharField | blank |
| s3_bucket_name | CharField | blank |
| s3_access_key | CharField | blank |
| s3_secret_key | CharField | blank |
| s3_endpoint_url | CharField | blank |
| s3_region | CharField | blank |
| email_enabled | BooleanField |  |
| email_host | CharField | blank |
| email_port | IntegerField |  |
| email_use_tls | BooleanField |  |
| email_username | CharField | blank |
| email_password | TextField | blank, 🔒 encrypted |
| email_from | CharField | blank |
| email_suppression_patterns | TextField |  |
| inbound_email_enabled | BooleanField |  |
| inbound_protocol | CharField | choices: imap/pop3 |
| inbound_host | CharField | blank |
| inbound_port | IntegerField |  |
| inbound_ssl | BooleanField |  |
| inbound_username | CharField | blank |
| inbound_password | TextField | blank, 🔒 encrypted |
| inbound_folder | CharField |  |
| inbound_delete_after_fetch | BooleanField |  |
| strip_quoted_replies | BooleanField |  |
| require_mfa | BooleanField |  |
| inbound_default_client_name | CharField | blank |
| company_name | CharField | blank |
| company_address_line1 | CharField | blank |
| company_address_line2 | CharField | blank |
| company_phone | CharField | blank |
| company_email | CharField | blank |
| company_logo | FileField | null, blank |
| email_logo | FileField | null, blank |
| email_header_color | CharField | blank |
| google_maps_api_key | CharField | blank |
| shop_address | CharField | blank |
| invoice_ninja_enabled | BooleanField |  |
| invoice_ninja_url | CharField | blank |
| invoice_ninja_token | TextField | blank, 🔒 encrypted |
| color_status_new | CharField | blank |
| color_status_assigned | CharField | blank |
| color_status_in_progress | CharField | blank |
| color_status_completed | CharField | blank |
| color_status_closed | CharField | blank |
| color_status_cancelled | CharField | blank |
| site_logo | FileField | null, blank |
| login_logo | FileField | null, blank |
| color_primary | CharField | blank |
| color_nav_text | CharField | blank |
| color_accent | CharField | blank |
| color_sidebar_bg | CharField | blank |
| color_sidebar_text | CharField | blank |
| color_page_bg | CharField | blank |
| color_page_title | CharField | blank |
| color_title_bar | CharField | blank |
| color_section_header | CharField | blank |
| color_section_header_text | CharField | blank |

## StatusDefinition
_Configurable status labels and colors for tickets and work orders._
`db_table = core_statusdefinition`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| entity_type | CharField | choices: ticket/workorder |
| slug | CharField |  |
| label | CharField |  |
| color | CharField |  |
| is_system | BooleanField |  |
| is_active | BooleanField |  |
| sort_order | IntegerField |  |

## SuppressedAddress
_Exact email addresses that should never receive automated emails._
`db_table = suppressed_addresses`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| email | CharField | unique |
| reason | CharField | blank |
| created_at | DateTimeField | blank |

## TechSkill
_Skills that can be assigned to technicians for future routing._
`db_table = tech_skills`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField | unique |
| description | TextField | blank |

## Ticket
_Initial service request (starts workflow)_
`db_table = tickets`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| ticket_number | CharField | unique |
| client | ForeignKey | → Client |
| device | ForeignKey | → Device, null, blank |
| help_topic | ForeignKey | → HelpTopic, null, blank |
| sla_plan | ForeignKey | → SLAPlan, null, blank |
| due_at | DateTimeField | null, blank |
| overdue_acknowledged_by | ForeignKey | → User, null, blank |
| overdue_acknowledged_at | DateTimeField | null, blank |
| first_responded_at | DateTimeField | null, blank |
| subject | CharField |  |
| description | TextField |  |
| source | CharField | choices: email/phone/web/rmm/system |
| status | CharField |  |
| contact | ForeignKey | → Contact, null, blank |
| assigned_to | ForeignKey | → User, null, blank |
| created_by | ForeignKey | → User, null |
| needs_response | BooleanField |  |
| wo_complete | BooleanField |  |
| escalation_level | PositiveSmallIntegerField |  |
| assignment_unseen | BooleanField |  |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |
| attachments | ManyToManyField | → Attachment, null, blank |

## TicketLink
_Links two related or duplicate tickets together_
`db_table = ticket_links`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| ticket_a | ForeignKey | → Ticket |
| ticket_b | ForeignKey | → Ticket |
| link_type | CharField | choices: related/duplicate |
| created_by | ForeignKey | → User, null |
| created_at | DateTimeField | blank |

## TicketLock
_Tracks who is currently viewing/editing a ticket to prevent collision_
`db_table = ticket_locks`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| ticket | OneToOneField | → Ticket, unique |
| locked_by | ForeignKey | → User |
| locked_at | DateTimeField | blank |

## TicketQueue
_Saved, filterable ticket views. owner=None means system queue (visible to all)._
`db_table = ticket_queues`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| name | CharField |  |
| owner | ForeignKey | → User, null, blank |
| filter_criteria | JSONField | blank |
| sort_field | CharField |  |
| sort_direction | CharField | choices: asc/desc |
| is_active | BooleanField |  |
| created_at | DateTimeField | blank |

## TicketReply
_Threaded conversation on a ticket (replies, updates, status changes)_
`db_table = ticket_replies`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| ticket | ForeignKey | → Ticket |
| reply_type | CharField | choices: customer_visible/internal |
| content | TextField |  |
| created_by | ForeignKey | → User, null |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |
| attachments | ManyToManyField | → Attachment, null, blank |

## User
_Extended user model for technicians and admins_
`db_table = users`

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField | PK, blank |
| password | CharField |  |
| last_login | DateTimeField | null, blank |
| is_superuser | BooleanField |  |
| username | CharField | unique |
| first_name | CharField | blank |
| last_name | CharField | blank |
| email | CharField | blank |
| is_staff | BooleanField |  |
| is_active | BooleanField |  |
| date_joined | DateTimeField |  |
| role | CharField | choices: admin/technician/viewer |
| role_obj | ForeignKey | → Role, null, blank |
| phone | CharField | blank |
| level | PositiveSmallIntegerField | choices: 1/2/3 |
| groups | ManyToManyField | → Group, blank |
| user_permissions | ManyToManyField | → Permission, blank |
| skills | ManyToManyField | → TechSkill, blank |

## WorkOrder
_Repair job (main entity)_
`db_table = work_orders`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| work_order_number | CharField | unique |
| ticket | OneToOneField | → Ticket, null, blank, unique |
| client | ForeignKey | → Client |
| device | ForeignKey | → Device, null, blank |
| contact | ForeignKey | → Contact, null, blank |
| repair_type | ForeignKey | → RepairType, null |
| reported_problem | TextField | blank |
| assigned_to | ForeignKey | → User, null, blank |
| service_type | CharField | choices: in_shop/onsite/remote |
| status | CharField |  |
| priority | CharField | choices: low/normal/high/urgent |
| time_spent_minutes | IntegerField |  |
| scheduled_date | DateField | null, blank |
| completed_date | DateTimeField | null, blank |
| cpu | CharField | blank |
| ram | CharField | blank |
| storage | CharField | blank |
| notes_internal | TextField | blank |
| notes_customer_visible | TextField | blank |
| invoice_ninja_ref | CharField | blank |
| invoice_ninja_id | CharField | blank |
| device_username | TextField | blank, 🔒 encrypted |
| device_password | TextField | blank, 🔒 encrypted |
| device_pin | TextField | blank, 🔒 encrypted |
| credential_notes | TextField | blank, 🔒 encrypted |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |
| attachments | ManyToManyField | → Attachment, null, blank |
| line_items | ManyToManyField | → LineItem, null, blank |

## WorkOrderItem
_Line items on a work order (checklist, parts, time entries)_
`db_table = work_order_items`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| work_order | ForeignKey | → WorkOrder |
| item_type | CharField | choices: checklist/part/time/other |
| description | CharField |  |
| quantity | DecimalField |  |
| unit | CharField | blank |
| unit_price | DecimalField | null, blank |
| is_completed | BooleanField |  |
| pre_check | CharField | blank, choices: pass/fail/na |
| post_check | CharField | blank, choices: pass/fail/na |
| notes | TextField | blank |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |

## WorkOrderNote
_Comments/updates on a work order_
`db_table = work_order_notes`

| Field | Type | Notes |
|---|---|---|
| id | AutoField | PK, blank |
| work_order | ForeignKey | → WorkOrder |
| note_type | CharField | choices: customer_visible/internal |
| content | TextField |  |
| created_by | ForeignKey | → User, null |
| created_at | DateTimeField | blank |
| updated_at | DateTimeField | blank |
| attachments | ManyToManyField | → Attachment, null, blank |
