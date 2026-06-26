# Murphy's Bench — System Assessment (June 2026)

> A full, honest, report-only audit of the whole system — code, data, security, operations, and process — done 2026-06-22 after a single day surfaced several critical operational failures and shook confidence in the project. Findings are graded 🟢 green / 🟡 yellow / 🔴 red, and every claim was **verified against the running system, not assumed.**

## TL;DR

**The software is well-built. The operations around it were not — and that gap is what failed us.**

- Code health, data integrity, and security fundamentals all came back **green after verification** (not assumption).
- Every failure that triggered this assessment lived in the *operational/process shell* around the code: no way to know when something breaks (observability), a whole-VM backup that silently protected the wrong machine (PBS), and a development process with nowhere to make a mistake safely.
- This is not a rotten project. It is a soundly-built application that **outran the discipline around it** — a common, fixable situation. The two worst holes (a two-weeks-empty backup and a duplicate-ticket bug) were closed on 2026-06-22.

## Root-cause thesis

The defects were not random. They share one cause: **the system was operated on assumption instead of verification.** The backup was *believed* to work, the database engine was *believed* to be PostgreSQL, the docs were *believed* to match reality, only one scheduler was *believed* to be running. None were ever *proven*. The application layer had a verification discipline (a real test suite); the infrastructure/operations layer never did. The durable fix is not "pay closer attention" — silent systems fool everyone — it is **build verification and alerting so the system reports its own failures.**

## Scorecard

| Domain | Verdict | One-line |
|---|---|---|
| A — Operations / scheduling / deploy | 🟢 | Single correct set of timers (post-fix); migrations clean; git in sync |
| B — Backup & disaster recovery | 🟢 | New B2 backup drill-proven restorable; PBS whole-VM backup ✅ **fixed Jun 22, verified All OK Jun 24** (was 🔴 broken as-found) |
| C — Data integrity | 🟢 | No corruption, no orphans, ticket numbers unique; data intact |
| D — Security | 🟢 fundamentals / 🟡 residuals | Auth/MFA/encryption/attachment-isolation solid; plain-HTTP-on-LAN + unused Postgres remain |
| E — Code health | 🟢 | Honors "fail loud" (verified — 0 real silent failures of 13 candidates); 100 tests; clean |
| F — Docs vs reality | 🟡 | Stale PBS-as-safety-net claims + a CLAUDE.md self-contradiction (some introduced same-day) |
| G — Observability | 🔴 | **None.** No failure alerting of any kind — the keystone gap |
| H — Process | 🟡→🔴 | dev=repo=prod collapsed on one box; no CI; no staging; no snapshot-before-migrate |

## Findings by domain

### A — Operations 🟢
Only the three intended systemd timers run (backup nightly, inbound fetch every 2 min, SLA check). A leftover user-level `mb-inbound` timer — the cause of a duplicate-ticket bug — was removed. Migrations show no drift and are all applied; the git working tree is clean and in sync with origin; secrets at rest are owner-only (600).

### B — Backup & Disaster Recovery 🟢 mechanism / 🔴 PBS
The new application backup (consistent SQLite snapshot + attachments + `.env` → Backblaze B2, immutable via Object Lock, restore-**drilled** from the offsite copy) is sound, and its keys live in Bitwarden so it is reachable in a real VM-loss disaster.

**✅ RESOLVED Jun 22 2026; verified All OK Jun 24 2026.** *(Finding kept below as the point-in-time assessment record.)* The VMID collisions were fixed (BookStack 102→202, Cloudflared 103→203; prod stays 103), a daily verify job was added, and prune was centralized (7/4/3). PBS content confirmed Jun 24: prod `vm/103` has 4 retained backups with **Verify State All OK**, and every group in the datastore has a distinct VMID. One open follow-up (low priority): the VM backups are **not client-side encrypted at rest** on the NAS, so a whole-VM backup includes prod's `.env` (which holds `FIELD_ENCRYPTION_KEY`) in the clear — acceptable on the trusted LAN, but PBS client-side encryption is the defense-in-depth option if ever wanted.

**🔴 (AS FOUND, 2026-06-22) The whole-VM "safety net" is broken for production.** Production is Proxmox VM 103; due to a VMID collision with a second VM in the same PBS datastore, PBS retention prunes the single real production backup. PBS verification is also off on every backup. Until 2026-06-22, between the (empty) database backup and the (mis-targeted) VM backup, **production had no working backup at all.** *(PBS storage durability — which NAS the datastore persists to — is also unconfirmed.)*

### C — Data Integrity 🟢
SQLite foreign-key enforcement is on; `integrity_check` passes; zero FK violations; ticket numbers unique; no orphaned records; the dedup fix left no stuck claim rows. Two apparent anomalies were verified benign (a legitimate standalone work order; an intentionally-unpriced line item). The data is intact, including through the same-day dedup/delete work.

### D — Security 🟢 fundamentals / 🟡 residuals
Strong: single MFA-protected superuser, `django-axes` brute-force lockout, `DEBUG` off, field-level AES encryption, secrets in Bitwarden, attachments correctly isolated outside the web root behind a per-object-authorized view, security headers on, automatic OS security patching (`unattended-upgrades`) active. Residuals: plain HTTP on the LAN (the four deploy warnings are all TLS-cookie related, correctly gated off — TLS is a deferred decision); an **unused PostgreSQL server still running** (decommission); no `pip-audit` CVE loop; `fail2ban` inactive (low priority — SSH is key-only).

### E — Code Health 🟢
The cardinal rule ("fail loud, not silent") is genuinely honored: of 13 swallowed-exception candidates, all 13 were verified as legitimate control flow or properly logged/audited — **zero real silent failures.** 100 tests with real coverage of the data-touching spine; zero TODO/FIXME markers; dependencies pinned. One real item (really a process issue): the dev environment runs Python 3.9 while prod runs 3.12, so there is no faithful local test environment.

### F — Docs vs Reality 🟡
Most docs are now accurate, but stale/false claims remain — notably that **PBS is a reliable "safety net"** (it is not; see B), appearing in `CLAUDE.md` and in this BookStack's own backup doc. `CLAUDE.md` is internally contradictory on backup status (header says fixed; body says broken). Some of this drift was introduced the *same day* by an incomplete doc sweep — evidence that documentation needs the same verify-against-reality discipline as everything else.

### G — Observability 🔴 (the keystone)
There is effectively **no way for the system to report a failure.** No `OnFailure` handlers on any service, no monitoring agent, no mail transport (the box cannot even send an alert), no application error reporting, no backup heartbeat/dead-man's-switch, and unbounded (un-rotated) web-server logs. This absence is *the* reason every other failure went unnoticed for so long — the operator was the monitoring. Fixing this is the single highest-leverage change.

### H — Process 🟡→🔴 (the structural root)
Development, the repository, and production are collapsed onto one machine — code is edited, tested, and committed directly on the live server. There is no CI/test gate, no staging environment, no snapshot-before-migrate step, and change authority is concentrated with no independent review. There is nowhere to make a mistake safely, which is the structural reason the operational failures accumulated.

## Prioritized remediation

**Tier 1 — soonest, highest leverage**
1. **Observability keystone (G):** a backup dead-man's-switch (ping an external monitor only after the archive verifies) plus failure alerting on the jobs. Converts "trusting silence" into "the system tells me." Push/outbound model — fits the LAN-only, no-inbound-exposure posture.
2. **PBS backup fix (B):** resolve the VMID collision, create a real recurring verified job, confirm datastore storage. *(Scheduled as a hands-on learning session.)*
3. **Correct the dangerous doc claims (F):** the PBS-as-safety-net lines and the `CLAUDE.md` backup contradiction — they would mislead an actual recovery.

**Tier 2 — important, deliberate**
4. **Process (H + E):** a dedicated test VM as a real pre-prod/staging environment (in progress), aligning the dev environment to Python 3.12, snapshot-before-migrate, and ending direct-on-prod editing.
5. **Decommission the unused PostgreSQL** server and remove the dead `DB_*` lines from `.env`.
6. **Rotate the broad GitHub token** still used by the scs-repair-tracker box.

**Tier 3 — hygiene / by decision**
7. CI test gate; `pip-audit` patch loop; log rotation; `fail2ban`; the TLS decision; inbound-attachment malware scanning (ClamAV).

## Already fixed on 2026-06-22
- Database backup root-caused and rebuilt (SQLite snapshot → immutable B2, restore-drilled, fail-loud); WAL enabled.
- Duplicate-ticket bug fixed (stray scheduler removed; atomic Message-ID claim + DB constraint + run-lock; regression-tested).
- GitHub token rotated to a scoped, locked-down credential on the MB side.
- The loudest documentation falsehoods ("PostgreSQL in production") corrected.

---
*Assessment performed report-only (no changes made during the audit). This document is the durable record; remediation proceeds separately, by decision.*
