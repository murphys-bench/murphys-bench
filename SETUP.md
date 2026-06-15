# Murphy's Bench — Setup & Admin Guide

You've installed Murphy's Bench (see `INSTALL.md`) and you can log in as the
superuser. This guide walks an administrator through configuring a fresh
instance and getting it ready for techs to use. For *what the app does* from a
user's point of view, see `FEATURES.md`.

Everything below lives under **Settings** (admin only — the gear/Admin link in
the sidebar footer). Settings is organized into tabs; you do **not** need the
Django admin for any of this.

---

## Suggested order

You can do these in any order, but this sequence avoids backtracking:

1. **Company** — your shop's identity
2. **Colors** — branding
3. **Outbound Email** — so the app can send mail
4. **Inbound Email** — so emails become tickets
5. **Roles** then **Users** — who can do what, then the people
6. **Workflow config** — statuses, help topics, SLA plans, repair types,
   checklists, canned responses, quick labor
7. **Security** — MFA policy
8. **Seed data** — a few clients/devices/tickets to work with

---

## 1. Company

Settings → **Company**. Your business name, address (split into line 1/2, city,
state, zip), phone, and logo. This information appears on **printed repair
reports** and in **outbound email**, so fill it in before sending anything to a
customer.

## 2. Colors

Settings → **Colors**. Set your title-bar, sidebar, and accent colors and upload
a logo. There's a live preview. Header text color on emails is computed
automatically for readability — you don't set it manually.

> **Display** (separate tab) controls per-browser preferences like font size and
> card density. Those are stored in the browser, per user — not shop-wide.

## 3. Outbound Email (SMTP)

Settings → **Outbound Email**. Enter your SMTP host, port, username, and
password (stored encrypted). There's a **"send test email"** button — use it and
confirm the message arrives before relying on it. Outbound email powers reply
sending, auto-responders, and notifications.

- **Email Templates** tab: edit the wording of the automated emails (e.g.
  auto-responder), with a reference panel of the variables you can use.
- The **Suppressed Addresses** list (under Outbound) holds addresses that should
  never receive automated mail.

## 4. Inbound Email (email → tickets)

Settings → **Inbound Email**. Point this at the mailbox your support address
delivers to. Supports **IMAP or POP3**.

> **Important operational note:** with **IMAP**, make sure the mailbox is
> configured so processed messages are marked read or removed — otherwise the
> poller can re-read the same message. The simplest reliable setup is **POP3
> with delete-from-server**, which pulls each message exactly once. (Tradeoff:
> the mail server then keeps no copy — Murphy's Bench becomes the system of
> record for inbound mail.)

The app polls this mailbox on a schedule (see `deploy/README.md` for the timer).
New mail becomes a ticket; replies that carry a `[TKT-…]` token in the subject
thread back into their existing ticket automatically.

> **Tip for a test instance:** point inbound at a *test* mailbox first, confirm
> tickets and threading behave, then switch to your real support address.

## 5. Roles, then Users

Settings → **Roles**. Roles are sets of permissions (a grid of capability
toggles — who can manage settings, view stored credentials, manage the KB,
etc.). Two roles ship by default: **Administrator** and **Technician**. Adjust
or add roles before you create people, so you can assign the right role as you
go.

Settings → **Users**. Create an account per tech. Assign each a role and an
**escalation level (1–3)** — higher levels are your senior techs. You can set
passwords and reset MFA here. (Superuser / staff status is intentionally *not*
editable in this UI — that stays a deliberate, separate action.)

## 6. Workflow configuration

These tabs shape how tickets and work orders behave. Sensible defaults are
seeded; tune them to your shop:

- **Statuses** — the ticket and work-order statuses and their colors. Core ones
  are built in; you can add custom statuses.
- **Help Topics** — how incoming tickets are classified; each can carry a
  default SLA.
- **SLA Plans** — response-deadline targets and overdue behavior.
- **Repair Types** (with categories) — the kinds of jobs you do; used on work
  orders and to drive checklists.
- **Checklist Items** — pre/post task lists, organized by device type.
- **Canned Responses** — reusable note text (separate customer-facing and
  internal-tech streams) you can drop into a work order.
- **Quick Labor** — common labor lines for fast time entry.
- **Custom Fields** — extra fields on tickets or work orders, scoped to a help
  topic or repair type, if your shop needs data the standard fields don't cover.
- **Dashboard Tiles** — which status tiles appear on the dashboard.
- **KB Categories** — categories for knowledge-base articles.

> Don't feel you must fill every tab on day one. Company + Email + Users + a
> couple of statuses is enough to start; refine the rest as real work flows
> through.

## 7. Security / MFA

Settings → **Security**. The key control is the **require MFA** toggle. With it
on, every user must enroll an authenticator app on next login. Admins get backup
codes and can reset a user's MFA if they lose their device.

For a shared **demo/test** box you may leave MFA optional to lower friction for
testers — but turning it on at least once is worth doing, since it's a feature
worth evaluating.

## 8. Seed some data

Create a handful of records so the app isn't empty:

1. A couple of **Clients** (one residential, one business).
2. A **Contact** and a **Device** on each.
3. A **Ticket** or two, and convert one to a **Work Order** to see the full flow.

> On a test/demo instance, use **fake** data only — never load real client data
> onto a box other people can access.

---

## Day-2 admin tasks

- **Logs** tab — audit trail of email sends, inbound fetches, and credential
  access.
- **Reset a demo box** to a clean slate (keeps config, wipes operational data):

  ```bash
  venv/bin/python manage.py reset_operational_data            # dry-run
  venv/bin/python manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"
  ```

  Never use `manage.py flush` — it destroys your configuration too.

- **Break-glass:** the Django admin (`/admin/`) still exists for emergencies
  (e.g. fixing a record stuck in a bad state), but routine work never needs it.
