# Batch 11 — Foundational Rebuild Plan

> **Session source:** Full legacy app audit (SCS Repair Tracker at 10.58.58.235) vs Murphy's Bench,
> completed June 8, 2026. Every item here was verified by direct visual comparison of both apps.
> No code should be written until this plan is reviewed and confirmed.

---

## The Core Problem

Murphy's Bench treats Clients, Contacts, Devices, and Work Orders as independent peer objects with
loose relationships. The legacy app is **client-centric** — everything flows through the client.
This is not just a layout preference; it's a workflow and data integrity requirement.

**Target model:** Client → Contacts → Devices → Work Orders

---

## Build Order (priority sequence)

### 1. Device Model — Add Missing Fields

The Device model is missing four fields present in the legacy app.

**Fields to add:**
- `os` — CharField (e.g., "Windows", "macOS", "Linux", "iOS", "Android", "ChromeOS", "Other")
- `os_version` — CharField (e.g., "11 Pro 10.0.26200.0") — free text, not a dropdown
- `condition_at_intake` — CharField (e.g., "Used", "New", "Damaged") — dropdown or free text
- `assigned_contact` — ForeignKey to Contact, optional (null/blank=True), on_delete=SET_NULL

**Label for `assigned_contact`:** "Whose device is this?" (matches legacy app language)

**Relationship rule:**
- Devices MUST be tied to a Client (already true)
- `assigned_contact` must be a Contact belonging to that same Client (enforce in form validation)
- Cross-client device use case: reporting only (aggregate stats — total count, device type, OS
  type/version breakdown)

**Form changes:**
- Add the four fields to DeviceForm
- `assigned_contact` queryset must be filtered to contacts of the selected client
- On the device form, when launched from a client page (`?client_id=`), the Client field should
  be pre-filled and read-only (or hidden), and the `assigned_contact` dropdown should auto-populate
  with that client's contacts
- Add a **"Save & Create Work Order →"** button (submits form, then redirects to new WO
  pre-filled with this device)

**Navigation:**
- Remove Device from the top-level nav bar (currently "Devices" nav link)
- Devices are accessed through the Client detail page only
- Device list/standalone view is no longer a primary entry point; remove or restrict

**Migration:** create and apply migration for the four new fields.

---

### 2. Client Detail Page — Become the Hub

The client detail page must become the central work surface for everything related to a client.

**Current state (Murphy's Bench):** sidebar layout, contacts and devices as secondary sections,
limited inline actions.

**Target state:**

**Layout:**
- Single-column or full-width layout with sections stacking vertically
- Sections: Account Info → Contacts → Devices → Work Order History

**Account Info card:**
- Name, Client Type (editable inline as a dropdown, not just in edit form), Address, Notes

**Contacts section:**
- Each contact displayed as an expandable card
- Inline expand-in-place editing (already partially implemented)
- **Phone numbers** on contact edit must have BOTH:
  - Free-text custom label field (e.g., "Work", "Home", "Cell", "Mom's cell", etc.)
  - Type dropdown (Mobile/Home/Work/Fax/Other)
  - The legacy app has both; Murphy's Bench currently only has the type dropdown
- Per-contact action buttons: **Edit** | **+ WO** (launches new WO pre-assigned to this contact's
  device or prompts for device)
- Contact fields: Name (single field), Email, Phone Numbers (number + label + type), Notes,
  Receives Email checkbox
- "+ Add Contact" button at section bottom
- Ability to set Primary Contact

**Devices section:**
- Devices listed under their client (not standalone)
- Inline "+ Add Device" from client page, pre-assigned to this client
- Per-device: shows Make/Model, Serial, OS, Assigned Contact
- Per-device action: **View** | **+ WO**

**Work Order History section:**
- All WOs for this client, most recent first
- Columns: WO#, Status badge, Device, Opened date, Days open/closed
- Click to open WO detail

---

### 3. Client Edit Form — Deactivate + Delete Workflows

**Add to client edit form:**

**Deactivate Client:**
- Toggle/checkbox to mark client inactive (already has `is_active`? confirm)
- When deactivated: client no longer appears in default client list
- Client list has "Show Inactive" filter button (matches legacy)
- Explanation text near the control: "Inactive clients are hidden from the default view but
  their history is preserved."

**Permanently Delete:**
- Separate danger zone section at bottom of edit form
- Type-to-confirm pattern: user must type the client name exactly to enable the delete button
- Deletes client and all associated contacts, devices, WOs (or block if WOs exist — decide)
- Red styling, clear warning text

---

### 4. Work Order Detail Page — Unified Action Toolbar + Enhancements

The WO detail page needs to become a self-contained work surface.

**Unified Action Toolbar (black bar across top, matches legacy):**
```
Owner/Device:  [View Client]  [Edit Client]  [Edit Device]  [Edit WO]  [WO History]
               [🖨 Repair Report]  [Claim Ticket]  [📧 Email Report]  [Status ▼]
```
- All actions accessible from one bar without navigating away
- Status dropdown updates inline (already exists — preserve this)
- Claim Ticket is a separate print action (same template, different title — see section 5)
- Email Report sends the repair report to the client's email

**Client info visible on WO page:**
- Client name, phone, email, address — shown in a card on the WO detail
- Currently missing; must be added

**Device info visible on WO page:**
- Serial number, OS, OS Version, Condition at Intake
- Currently missing; must be added

**Work Order — Contact association:**
- WOs need a `contact` FK to Contact (nullable) — "whose WO is this?"
- Visible as a Contact column in WO History on the client detail page
- Settable when creating or editing a WO (dropdown filtered to the client's contacts)
- On device add → "Save & Create Work Order": pre-fill WO contact from the device's assigned_contact
- Displayed in the WO detail header alongside client and device info

**Work Order Info section additions:**
- **Days Open** counter — calculated from intake date to today (or to completed date if closed)
- **Completed Date** field — writable, set when status → Complete
- **Invoice Ninja Ref #** — text field for cross-referencing invoices (previously deferred)

**Credentials section:**
- Existing: device_username / password / PIN (masked display, HTMX inline save) ✅
- Add: **"+ Add note"** field for freeform credential notes (e.g., "recovery email", "security
  question answer") — stored as credential notes, not a structured field

**Notes sections:**
- "Notes for Customer" = customer-visible notes (already exists as `note_type='customer_visible'`)
- "Technician Notes" = internal notes (already exists as internal)
- Both have timestamped entries with Edit/Delete per entry ✅

**Work Performed section:**
- Currently: tag chips showing repair type labels
- Required: each logged entry shows **bold label + description text + timestamp**
- The description is what prints on the report — it must be visible on the WO page too
- Repair type buttons remain for one-click logging

**Pre/Post Checklist:**
- Currently: unknown state
- Required: collapsed by default with "▼ Show" toggle
- Pre-check and Post-check columns

---

### 5. Repair Report + Claim Ticket

**Repair Report (`/work-orders/<id>/print/`):**

Currently missing from the device section:
- OS
- OS Version
- Condition at Intake

Currently missing from the notes section:
- Timestamps on each note entry

Currently missing entirely:
- **Technician Signature & Date** line
- **Client Signature & Date** line
- **Footer** on every page: `[Company Name] • [WO#] • [Date]`

**Claim Ticket:**
- Same template as Repair Report
- Only difference: title reads "Claim Ticket" instead of "Repair Report"
- Printed at intake (when device is left); Repair Report printed at completion
- Implement as: same print view, `?type=claim` parameter changes the title
- Already partially implemented this way in legacy app

**Print layout:**
- Logo + company info (left) | WO# / Title / Intake Date / Status (right)
- CLIENT + DEVICE side-by-side cards
- Problem/Task + repair type tags
- Work Performed (bold label + description, one entry per row)
- Customer Notes (with timestamps)
- Signature lines
- Footer

---

### 6. Native Settings UI — Major Expansion

The current Settings UI at `/settings/` has: Company, Outbound Email, Inbound Email, Attachments,
Security, Mileage. All of the following must be added as native settings tabs or pages.

#### 6a. Repair Types

**Current state:** accessible via Django admin only.

**Required native UI:**
- List all repair types grouped by category
- Category headers are collapsible (▲/▼)
- Per-category: count of types shown in header
- Per-type: label + category, edit inline or via form, delete
- **Add repair type:** label + category dropdown → Save
- **Add category:** name → Save, appears immediately
- **Reorder categories:** ▲/▼ buttons per category (or drag, but ▲/▼ is simpler)

**Category list (from legacy, for reference):**
Services, Software, Malware & Security, Operating System, Hardware,
Network & Connectivity, Data, Peripherals, Other

#### 6b. Canned Responses

**Current state:** not in native UI.

**Required:**
- Two **Note Streams**: "Customer Notes" and "Tech Notes (Internal)"
- Each stream has **Categories** (user-defined, reorderable)
- Each response has: Note Stream, Category, Response Label, Default Explanation (the body text)
- Display grouped by stream → category
- Customer Notes categories (from legacy): Problem Description, Device Condition, Accessories,
  Intake Notes, Recommendations, Instructions, Follow-up, General
- Tech Notes categories (from legacy): Findings, Tests Performed, Internal Observations,
  Work Performed, Parts Used, Follow-up
- CRUD: add, edit, delete per response; add/reorder categories per stream

**On WO detail:** canned response picker for customer notes and tech notes fields — dropdown or
modal that inserts the selected response's text into the note body.

#### 6c. Quick Labor

**Current state:** QuickLaborItem model exists, populated via Django admin (Batch 10 deferred
native UI).

**Required native UI:**
- List all quick labor items grouped by category
- Categories: Software, Hardware, Data, Maintenance, General
- Per item: Button Label, Category, Print Description (what appears on the report)
- CRUD: add, edit, delete
- Group display with category headers

#### 6d. Checklist Items — Model Change Required

**Current state:** Murphy's Bench has checklist items tied to repair types (per-template model).
**Required:** Flat item bank scoped by device type (not per-repair-type).

**Model change:**
- `ChecklistItem`: name, device_types (M2M or comma-separated device type choices), is_active
- Remove repair-type association from checklist items
- Checklist items shown on a WO are filtered by the WO's device type
- Items apply to "all" device types OR a specific subset (Laptop, Desktop, Tablet, Phone, etc.)

**Legacy item list (for migration reference):**
- Wifi → Laptop, Desktop, Tablet
- Wired Network Adapter → Laptop, Desktop
- Safe Browser Homepage → all
- Missing Drivers → Laptop, Desktop
- Hard Drive SMART Test → Laptop, Desktop
- PUP/Toolbars → all
- Sound → Laptop, Desktop
- USB Ports → all
- No Malicious Browser Extensions → all
- Updates Current → all
- Current Anti-Virus → all
- Battery Condition → Laptop, Tablet, Phone
- Screen/Display → all
- Keyboard/Trackpad → Laptop
- Touchscreen → Laptop, Tablet, Phone

**Required native UI:**
- Flat list of all items
- Per item: Item Name, Device Types (multi-select or tags), Save, Retire (soft-delete)
- Add new item at top
- Retired items hidden by default, "Show Retired" toggle

#### 6e. Status Colors + Site Colors

**Current state:** not in native UI, colors are hardcoded in CSS/Tailwind.

**Required:**
- **Status Colors tab/section:**
  - Per-status row (Intake, In Progress, Waiting - Parts, Waiting - Client, Complete, Closed)
  - Per row: Background color swatch + hex input, Text color swatch + hex, Border color swatch + hex
  - Live Preview badge showing the status badge as it will appear
- **Site Colors tab/section:**
  - Buttons: Primary Button, Primary Button Hover, Accent/Focus Ring, Accent Light
  - Navigation: Nav Background, Nav Text, Nav Active Item
  - Page: Page Background, Card Background, Card Border, Primary Text, Muted Text
  - Sidebar: Sidebar Background, Sidebar Card, Sidebar Card Hover, Sidebar Text, Sidebar Muted Text
  - Each: color swatch + hex input
  - Changes apply to CSS variables; page re-renders with new colors on save

**Current legacy colors (for defaults):**
```
Primary Button:      #2d6a4f
Primary Button Hover: #1b4332
Accent/Focus Ring:   #00fcff
Accent Light:        #d8f3dc
Nav Background:      #4d8649
Nav Text:            #ffffff
Nav Active Item:     #ffffff
Page Background:     #f0f2f1
Card Background:     #ffffff
Card Border:         #d0d9d4
Primary Text:        #1a1a1a
Muted Text:          #6b7c74
Sidebar Background:  #000000
Sidebar Card:        #797979
Sidebar Card Hover:  #3a3a55
Sidebar Text:        #e0e0e0
Sidebar Muted Text:  #aaaaaa
```

#### 6f. Company Info — Additions

**Current state:** Company Info tab exists in Settings with Name, Address, Phone, Email, Logo.

**Changes needed:**
- Split Address into **Address Line 1** and **Address Line 2** (legacy: "235 Coolidge St." /
  "Silverton, Oregon 97381") — the current single address field needs to become two
- Add **Report Header Preview** — a live-rendered preview below the form showing exactly how the
  company header will appear on the Repair Report, using the current saved values (logo, name,
  address, phone, email)

#### 6g. Display Settings — Browser-Local UI Preferences

**What it is:** Per-browser UI density and font preferences, stored in localStorage. No server
round-trip. Settings do not sync across devices — each browser/device has its own preferences.

**Controls:**
- Navigation Bar: Font Size (default 13px)
- Sidebar: Font Size (default 12px), Width (default 220px)
- Main Content: Font Size (default 13px), Card Density — Compact / Normal / Comfortable
- Reset to Defaults button

**Implementation approach:**
- Settings page tab (or a floating gear/accessibility button in the corner)
- On change: write to localStorage, apply immediately via CSS variables or `data-density` attribute
  on `<body>` that Tailwind-style classes respond to
- On page load: read localStorage in a `<script>` in `<head>` (before paint) to avoid flash of
  default layout
- Card Density affects padding on list rows, WO cards, contact cards — Compact removes extra
  padding, Comfortable adds it

---

## What's Already Good (No Changes Needed)

- Email Settings (SMTP, From/Reply-To) → covered by Settings › Outbound Email ✅
- Google Maps / Mileage → covered by Settings › Mileage ✅
- Contact inline add/edit/delete → mostly implemented ✅
- Multiple phones per contact (ContactPhone model) → implemented ✅
- Quick Labor one-click logging on WO → implemented ✅
- Device Credentials (masked, HTMX save) → implemented ✅
- Client Type badge → implemented ✅
- Repair Report base structure → implemented, needs additions per section 5 ✅ (partial)

---

## Items NOT in Legacy App (Murphy's Bench Advantages — Keep)

These are Murphy's Bench capabilities that exceed the legacy app. Do not remove or regress:

- Ticket system (separate from Work Orders)
- SLA tracking + overdue alerts
- Queues with filter criteria
- Inbound email (ticket creation via email)
- Role-based access control
- MFA / two-factor authentication
- Audit log
- Mileage log with Google Maps auto-calculation
- Departments / Teams (Phase 2)
- REST API (Phase 2)

---

## Migration Sequence

When implementing, follow this order to avoid dependency issues:

1. **Device model fields** — migration first, then form/view updates
2. **Checklist model change** — requires migration + data migration for existing items
3. **Client detail page** — depends on device model being updated
4. **WO detail toolbar** — depends on client/device fields being available
5. **Repair Report** — depends on device fields (OS, condition) being present
6. **Settings UI** — independent, can be parallelized with items 3–5

---

## Open Questions (decide before coding)

1. **Permanently Delete client:** block if WOs exist, or cascade-delete everything? Recommend:
   block with a message ("This client has X work orders. Archive instead?") — data loss is
   irreversible.

2. **Address Line 1/2 split:** the existing `address` field on Client model needs to become two
   fields. How to migrate existing data? Options: (a) leave old data in Line 1 and have user
   split manually, (b) attempt auto-split on comma. Recommend: option (a), simpler and safer.

3. **Colors stored where?** SiteSettings model already exists. Add color fields to it, or use a
   separate CSS variable system? Recommend: add fields to SiteSettings, generate a `<style>` block
   in base.html from those values.

4. **Repair Type categories:** Does a `RepairTypeCategory` model already exist? Check before
   creating. If not, add it with a `sort_order` field.

5. **Assigned Contact on device form:** when launched from a client page, the contact dropdown
   must only show contacts for that client. How does the form know which client? Via `?client_id=`
   param (already used for `?next=`). Need to filter the queryset dynamically — either server-side
   on GET, or via HTMX cascade.
