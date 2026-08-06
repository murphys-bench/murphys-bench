# Roadmap

Where Murphy's Bench is going, in three buckets: what I'm working on now, what I'm
considering, and what I've decided against.

This is direction, not a release schedule. I run a repair shop; Murphy's Bench is built
alongside that work. Nothing here is a promised date, and things move between buckets as
real shops try it and tell me what's missing.

If something you need isn't here, say so — open an issue. Feedback from shops that might
actually use this is what decides what gets built next.

## Working On

- **Install and first-run experience.** Making a fresh install work the first time, on the
  first try, without reading the script. Driven directly by tester reports.
- **Counter sales reporting.** Work-order settlements already flow into the billing report;
  counter sales from the Register don't appear in any report yet.
- **Design and UI consistency.** A page-by-page pass so every screen uses the same header,
  search, and card patterns.
- **Documentation.** Filling the gaps between "it installed" and "I know how to run it."

## Considering

Real candidates, in no particular order. Being listed here means I think it's worth
building — not that it's scheduled.

- **Data import.** Bringing clients, devices, and history in from an existing system.
  Nothing built yet, and the hard part is that every source system exports differently.
  If you want this, tell me what you'd be importing *from* — that's the part I can't guess.
- **Parts inventory.** Stock levels, reorder points, and purchasing. Parts can be put on a
  work order today, but nothing tracks what's on the shelf.
- **Billing backends other than Invoice Ninja.** The integration already sits behind a seam
  so another backend can be added without touching the rest of the app.
- **Calendar and scheduling.** A shop view of what's booked: appointments, promised-by dates,
  and on-site visits. Requested by a tester; nothing designed yet.
- **Deeper management and reporting tools.**
- **SMS notifications.**
- **Testing across a wider range of shops and workflows.** The most valuable thing on this
  list, and the one I can't do alone.

## Not Planned

Not "never" — but not on the path, and I'd want a strong reason to revisit.

- **Multi-tenancy / hosted SaaS.** Murphy's Bench is self-hosted software that one shop runs
  for itself. Running other people's shops is a different product with different obligations.
- **Processing card payments directly.** Murphy's Bench never stores or handles card data.
  Payment processing belongs to a real processor; the app records the outcome.
- **Terminating TLS itself.** It runs behind whatever reverse proxy you already use —
  Caddy, nginx, Cloudflare. See `docs/deployment-tls.md`.
