# Murphy's Bench — Book Index

> **BookStack location:** Infrastructure shelf → "Murphy's Bench" book.
> These pages are operations/infrastructure documentation. The authoritative *developer* notes remain in `CLAUDE.md` and `TODO.md` in the repo.

## How to use these files

Each numbered `.md` file = one BookStack page. Create the book "Murphy's Bench" under the Infrastructure shelf, then create one page per file (BookStack accepts Markdown directly in the page editor). Suggested page order matches the file numbers.

## Pages

1. **Overview & Architecture** — what MB is, the stack, the request path, where it fits in the SCS tooling.
2. **Deployment & Infrastructure** — the VM, services, paths, SSH, HTTPS-deferral. *Start here when something's down.*
3. **Development & Deploy Workflow** — Mac → git → SSH → pull → migrate → restart.
4. **Operations & Maintenance** — systemd timers, the inbound mailbox, logs, admin, data-wipe command.
5. **Backup & Disaster Recovery** — the nightly dump, the encryption-key dependency, restore procedures.
6. **Email System** — inbound (POP3) + outbound (branded HTML), threading, the gotchas.
7. **Data Model & Settings Reference** — the 34 models and what native Settings covers.
8. **Conventions, Gotchas & Locked Decisions** — the "don't relearn this the hard way" page.

## Maintaining this book

When a session changes infrastructure, deploy steps, the data model, or a locked decision, update the matching page here as part of the end-of-session "button up" sweep — the same time `CLAUDE.md` is updated.
