# Murphy's Bench — What It Does

Murphy's Bench is self-hosted repair-shop software for small field-service
businesses and MSPs. It runs on your own server, on your own network. You own
the data, there's no per-seat SaaS bill, and nothing about your clients leaves
your control.

This document is for a technician or shop owner sizing it up: **what can it
actually do for you day to day.** It doesn't cover how it's built or how to
install it — see `INSTALL.md` for that.

---

## The core idea: Ticket → Work Order

Most repair work follows one shape, and Murphy's Bench is built around it:

> A request comes in (a **Ticket**) → you talk to the customer → it becomes
> actual repair work (a **Work Order**) → you do the work and track it → it's
> done and ready to bill.

The two halves are deliberately separate:

- A **Ticket** is the conversation — the customer-facing thread.
- A **Work Order** is the job — the bench/onsite work, parts, time, and checklist.

A ticket can become a work order with one click, and the two stay linked so you
can always jump between "what the customer was told" and "what was actually done."
Crucially, **closing a work order never auto-closes the ticket** — a human
decides the customer interaction is finished. No job quietly falls off the radar.

---

## Tickets — the customer conversation

- **Email in, ticket out.** Emails sent to your support address automatically
  become tickets. Customer replies thread back into the same ticket instead of
  piling up as new ones.
- **Threaded replies**, like an email client — staff-to-customer messages,
  internal notes only your team sees, and inbound customer replies are visually
  distinct so you always know who said what.
- **One voice to the customer.** The ticket is the single channel the customer
  sees. A bench tech who needs the customer contacted messages the ticket owner
  internally rather than emailing the customer directly — so the customer never
  gets two people talking at them.
- **HTML email with your branding** — your logo and colors on outbound mail,
  with signatures.
- **Auto-responder** so a customer knows their email was received.
- **Quoted history folds away** — long email chains collapse so the new message
  is what you actually read.
- **Status tracking** (New → Open → In Progress → Waiting on Customer → Resolved
  → Closed) with colors, and you can define your own custom statuses.
- **Collision avoidance** — if a teammate already has a ticket open, you're
  warned before you both step on it.
- **Link related tickets** together (duplicates, related issues).

---

## Work Orders — the actual job

- **In-shop, onsite, or remote** — each work order knows which, and onsite jobs
  get mileage tracking.
- **Built-in stopwatch** to log time as you work, plus quick-entry labor lines.
- **Checklists** — pre- and post-repair task lists, scoped by device type, so
  nothing gets skipped.
- **Work Performed log** — a running, timestamped record of what you did, which
  becomes the customer's repair report.
- **Printable Repair Report** — a clean, professional printout with your company
  info, the device, the work done, and signature lines.
- **Device credentials, encrypted** — store the login/password/PIN for the
  machine you're working on, masked by default, with every reveal logged.

---

## Clients, contacts, and devices

Everything is organized around the **client**, the way a shop actually thinks:

- A client has multiple **contacts** (people), each with multiple phone numbers.
- A client has multiple **devices**, each with its own repair history.
- Residential vs. business clients are handled differently where it matters.
- From a client's page you see their whole history and can start a new work
  order for a specific person and device in a couple of clicks.

**Managed clients vs. retail customers.** The same record can be a managed
client or a one-off retail customer — the difference is whether it has a
**service contract**. A record with an active contract is a managed client; a
record without one is a retail customer who just gets work orders and counter
sales as they come. Either can be business or residential, either can generate
work, and either can convert to the other. Both lanes stay fully separate — the
event-driven repair path is never disturbed by the managed path.

**Managed assets.** A managed client's equipment can be tracked as **assets** —
owned/managed machines, distinct from the walk-in-style device records the bench
uses. A device you start managing can be **promoted to an asset** in one step,
and its repair history follows it, so the machine keeps one continuous record.
Assets can be attached to a contract ("covered by") and show their own recent
work. This is managed-device tracking, not a stock/parts inventory.

---

## Knowing where things stand

- **Dashboard** with color-coded tiles — what's active, what's waiting, what's
  overdue, what's done — at a glance.
- **Per-technician views** — techs see their own work plus the unclaimed pool;
  admins see everything. Techs **claim, transfer, and escalate**; dispatchers
  **assign**.
- **Escalation levels** — push a ticket up to a more senior tech without losing
  the current owner, so nothing disappears into a black hole.
- **In-app notifications** — a sidebar bell tells a tech when a teammate needs
  something from them.
- **Saved queues** — build your own filtered views of tickets and reuse them.

---

## Deadlines and accountability (SLAs)

- **SLA plans** set response deadlines per type of work.
- Overdue tickets are flagged, and acknowledging one **requires a note** — so
  there's an audit trail of why something slipped, not just that it did.

---

## Quotes and sales leads

- **Prospects** — capture a sales lead (contact-first) before they're a client,
  track it through a simple pipeline, and **promote it to a full client** in one
  step when they say yes.
- **Estimates / quotes** — build a priced quote from your catalog of services and
  parts, offer **side-by-side comparative options** (e.g. good / better / best),
  email it to the customer as a **PDF**, and **accept it straight into a work
  order** when they approve.

## Getting paid — the Register

Murphy's Bench has a **light point-of-sale register** for taking payment without
leaving the app. It settles two kinds of sale:

- a **finished work order** (the tech closes it; the cashier rings it up), or
- an **ad-hoc counter sale** (a walk-in retail/product sale, no work order).

At the register you:

- **Record the payment** — cash, check, or a card you ran in Square — or trigger
  a **charge against a client's stored card on file**, and MB records the
  transaction reference.
- **Print an MB receipt** for the customer, with that reference on it.
- The sale is pushed to **[Invoice Ninja](https://invoiceninja.com/)** as a paid
  invoice (or a draft, to bill later).

**MB never stores or processes card data itself.** Card payments happen in Square
or through Invoice Ninja's gateway; MB only *triggers* and *records* them. It is
not a payment processor and not an accounting package.

## Recurring / managed billing

- **Service contracts** are the managed-client mechanism. A contract carries a
  reusable set of **recurring line items** (the client's services, at negotiated
  prices), a **cadence** (monthly, quarterly, or annual), its own **billing day**,
  a term and renewal, and any **covered assets**. Creating a contract is what
  makes a record a managed client.
- A **contract billing worklist** prepares each due contract's invoice for the
  current period, with a batch review-and-confirm step before anything is sent —
  so a period's billing is one reviewed action, not manual re-entry. It knows
  which contracts are actually due this period based on their cadence.
- Everything MB produces here is a **draft** invoice. MB never auto-charges — you
  review, send the drafts to Invoice Ninja, and settle them there. Nothing is
  charged without a deliberate step.

*(A simpler per-client monthly-billing mode also exists for shops that don't want
full contracts.)*

## Billing state and export

Murphy's Bench tracks billing **state** — it is not a full accounting package.

- Each work order has a billing status: uninvoiced, invoiced, paid, paid-direct
  (cash/walk-in), or disputed. **Outstanding balances** roll up per client.
- **CSV export** of invoices and billing so it drops into whatever accounting
  system you already use.
- **[Invoice Ninja](https://invoiceninja.com/) stays the system of record** — it
  assigns invoice numbers, owns assembly, and holds the payment ledger; MB feeds
  it and reads status back.
- **Invoice Ninja is the backend because it's what the building shop runs.** The
  integration sits behind a deliberate seam, and MB records every transaction
  itself no matter what's behind it — so support for other billing/payment
  backends can be added later without rebuilding the app. That's planned, not
  built: today Invoice Ninja is the one that works.

---

## Reporting

- A reports page with several built-in reports — workload, technician
  performance (completion rate, average resolution time), billing summaries,
  mileage, and more — with charts.
- **Every report exports** to CSV, print, or PDF.

---

## Knowledge base

- A built-in **KB** for troubleshooting guides, how-tos, vendor notes, and
  internal-only articles.
- Articles are written in Markdown and render with proper formatting.
- Some articles can be marked internal/restricted.

---

## Security and access control

- **Multi-factor authentication (TOTP)** with authenticator apps, backup codes
  for admins, and admin-driven MFA reset for a lost device.
- **Roles and permissions** — fine-grained control over who can see and do what
  (including who may reveal stored credentials).
- **Encrypted credential storage** for both device and organization-level
  secrets (Wi-Fi, portal logins, etc.), with a full **access log** — every view
  of a secret is recorded.
- **Login lockout** protection against password guessing.
- The whole app is login-only and built to sit behind HTTPS.

---

## Why self-hosted

- **Your data stays yours** — on your hardware, on your network. Sensitive
  customer and credential data never sits on someone else's cloud.
- **No per-seat subscription.** Add techs without adding to a monthly bill.
- **You control updates and uptime.** Nothing changes under you on a vendor's
  schedule.
- Accessible securely from anywhere via a **Cloudflare Tunnel** when you want
  remote access, without exposing your network.

---

## Honest about where it is

Murphy's Bench is in **active daily production use** at the shop that builds it,
and it's being hardened for use by others. It's deliberately scoped: it does
ticketing, work orders, devices, mileage, email, credentials, quoting, a light
sales register, and reporting **well**, rather than trying to be everything. It
is not a full retail POS (no stock/parts inventory, cash drawer, or barcodes), a
payment processor, a customer self-service portal, a full accounting system, or a
multi-tenant SaaS — those are out of scope by choice. (It does track *managed
assets* — a client's managed machines — but that is device tracking, not stock
inventory.)

**Planned, but not built yet.** A few things are on the roadmap and honestly
called out so you're not surprised: parts **stock/inventory** (levels, reorder
points, purchasing — parts can be billed on a work order today, but there's no
stock behind them); **billing/payment backends other than Invoice Ninja** (the
seam is there, the other implementations aren't); a deeper management
**reporting** layer beyond today's built-in reports; **SMS**; and a **customer
self-service portal**. These are direction, not dated commitments.

If you run a small shop and want repair-tracking software you fully control,
this is built for exactly that.
