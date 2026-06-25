# Murphy's Bench — Overview & Architecture

> **Shelf:** Infrastructure · **Book:** Murphy's Bench
> Operations-focused documentation for the in-house repair-tracking platform.

## What Murphy's Bench is

Murphy's Bench (MB) is **self-hosted, internal-first repair-tracking software** for a small field-service / MSP business. It is in **daily production use at Shamrock Computer Services (SCS)** and has moved past the prototype stage.

It replaces the workflow previously handled by the legacy PHP "SCS Repair Tracker" app, and is the intended successor to OSTicket + ITFlow for ticketing and job tracking.

### The core workflow

```
Ticket (intake + email replies) → Triage → Work Order (the repair)
   → Notes / Checklist / Labor → Closed → Invoice Ninja (billing)
```

- A **Ticket** is the single client-facing channel — all customer contact happens through it.
- A **Work Order** is the actual repair job. It can be created from a ticket or stand alone (work doesn't always arrive as a ticket).
- MB tracks **billing state only** (uninvoiced / invoiced / paid / paid_direct / disputed). Invoice Ninja remains the authoritative financial system.

## Where it sits in the SCS stack

| Concern | System |
|---|---|
| Ticketing + work orders + devices + mileage | **Murphy's Bench** (this) |
| Formal invoicing / receipts | Invoice Ninja (Square for card processing) |
| Documentation | BookStack (this book lives here) |
| Virtualization | Proxmox homelab |

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · Django 4.2 |
| Frontend | Tailwind CSS (compiled, self-hosted) · HTMX · Alpine.js (vendored/pinned, no CDN) |
| Database | SQLite (production and local dev) · PostgreSQL supported via DB_ENGINE (unused) |
| Auth | Django session auth + django-two-factor-auth (TOTP) |
| App server | Gunicorn |
| Web server / TLS termination | Nginx |
| Host | Ubuntu 24.04 VM on Proxmox |

The frontend is **fully self-hosted — no CDN**: Tailwind is compiled to a static stylesheet via the standalone CLI (a build step run on deploy by `scripts/build_css.sh`, no Node), and HTMX + Alpine.js are vendored/pinned in `static/js/`. There is **no async queue** (synchronous email is sufficient at MSP scale) and **no OAuth2** for mail (standard cPanel IMAP/POP3 credentials).

## Request path

```
Browser (LAN client)
   │  HTTP on 10.58.58.82  (no public domain yet)
   ▼
Nginx  ──►  Gunicorn (murphys-bench.service)  ──►  Django app  ──►  SQLite (db.sqlite3)
                                                        │
                                                        ├─► outbound SMTP (cPanel mail)
                                                        └─► inbound POP3 poll (systemd timer)
```

Background work is driven by **systemd timers**, not cron (this VM has no cron):

- `fetch_inbound_email` — every 2 min, turns inbound mail into tickets/replies
- `check_sla_overdue` — every 15 min, flags overdue tickets
- nightly backup (SQLite snapshot + files → Backblaze B2, immutable) — 02:15

## Operating principles (why the system is shaped this way)

- **Internal-first / self-hosted.** Runs on the business's own network; other shops could self-host their own copy.
- **One face to the client.** Only the ticket tech contacts the customer. Bench techs message the ticket tech *internally* — never email the client directly from a work order.
- **Visual status is first-class.** Colour + icons communicate state faster than text; this is a requirement, not polish.
- **Soft-delete everything.** Hard deletes require a deliberate, type-to-confirm admin action.
- **Stabilize before adding.** The project is in a stabilization phase — depth and trustworthiness over new features.

## Current status

- **Phase 1 — active**, deployed internally on the LAN (HTTP only, no public domain).
- HTTPS / Cloudflare tunnel cutover is **deliberately deferred** (see *Operations & Maintenance*).
- The one approved post-stabilization feature is the **Invoice Ninja bridge**.

## Related pages in this book

- **Deployment & Infrastructure** — the VM, services, paths, SSH.
- **Development & Deploy Workflow** — how a change reaches production.
- **Operations & Maintenance** — timers, mailbox, logs, day-to-day admin.
- **Backup & Disaster Recovery** — the nightly dump and how to restore it.
- **Email System** — inbound + outbound mail.
- **Data Model & Settings Reference**.
- **Conventions, Gotchas & Locked Decisions**.
