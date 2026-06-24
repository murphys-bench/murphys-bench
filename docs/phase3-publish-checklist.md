# Murphy's Bench — Phase 3 Publish Checklist

The floor for making MB installable by another small shop without you on the phone.
Nothing here is urgent. Work it in low-energy sittings, in order. Stop whenever.

> **Last refreshed:** June 24, 2026. A lot of the original "later" infrastructure is now
> done (see "Already handled" at the bottom) — MB has CI, a 109-test suite, and a full
> install/update/backup/restore/export + tagged-release/auto-rollback toolchain (current
> release **v0.1.1**, all three SCS boxes on it). What remains here is mostly the
> de-Shamrock-ification and the public-facing paperwork. See memory
> `project_mb_publish_ops_selfsufficiency`.

---

## 1. Code separation (the real work — one time)

The goal: a fresh clone runs with zero Shamrock-specific values baked into code.
`setup.sh` already generates a clean `.env` (company name, hosts, fresh keys), so the
runtime path is mostly there — this is about auditing the *code* for baked-in values.

- [ ] Grep the codebase for hardcoded business values before trusting yourself:
      shop name, your domain, `10.58.x.x` addresses, email addresses, API keys,
      MFA issuer name, any T2T / Cloudflare specifics.
- [ ] Move whatever that finds out of code into `.env` / Django settings.
      (`COMPANY_NAME`, `ALLOWED_HOSTS`, SMTP creds, encryption key are already env-driven.)
- [ ] Confirm nothing reads from a hardcoded path that assumes your homelab layout.
- [~] Ship a `sample.env` with fake placeholders + inline comments. **`.env.example`
      exists** — but fix its stale default (`DB_ENGINE=postgresql`; MB defaults to SQLite)
      so a copy-paste install isn't misled.
- [ ] Add a minimal demo fixture: one fake client, one fake device, one fake ticket —
      enough that a fresh install shows something instead of an empty shell.
      (`setup.sh` creates the superuser but seeds no data.)

## 2. Secrets / Git history (the one safety step that matters most)

- [ ] Decide: scrub history, or start clean. **Recommended: start a fresh public repo
      from current state.** Far less error-prone than rewriting history, and it
      guarantees no old secret resurfaces from an old commit.
- [ ] Before pushing: confirm no real SMTP password, API token, SECRET_KEY, or client
      data exists anywhere in the working tree (not just settings — check fixtures,
      screenshots, test files, README drafts). NOTE: `.env` is gitignored and has always
      been — secrets live there, not in tracked files — but verify before going public.
- [ ] Generate a fresh `SECRET_KEY` + `FIELD_ENCRYPTION_KEY` for any public/demo build;
      never reuse a live one. (`setup.sh` already does this on a fresh install.)

## 3. Security defaults — ✅ mostly verified (Jun 2026 posture passes)

- [x] `DEBUG = False` is the default; the app **refuses to start** with default keys.
- [x] `ALLOWED_HOSTS` is `.env`-driven, not hardcoded.
- [x] Auth/permissions use Django defaults (LoginRequired everywhere, role perms,
      per-object visibility scoping, MFA, django-axes) — no weakening shortcuts.
- [x] File uploads constrained (blocked-extension list + size cap; attachments stored
      outside the web root, served only through an authz'd view). See session 32.
- [x] Install docs don't tell people to do anything unsafe (HTTPS/TLS rationale in
      `docs/deployment-tls.md`; HTTPS hardening flags documented as deploy-time).

## 4. Documentation (a couple of focused sessions)

- [~] **README** — a draft exists (honest "what it is / where it could go" framing).
      Still to add: a loud **"what it isn't"** (internal/small-shop tool, use at your own
      risk, limited support, not hosted SaaS, no warranty), 3–5 screenshots, and a final
      pass on the install steps. Backup/restore/update are already documented
      (`INSTALL.md`, `deploy/README.md` → "Releases & updates").
- [ ] **LICENSE** file. AGPLv3 if the goal is "small shops can use it, but no one wraps
      it into a paid hosted product without sharing changes back."

---

## Already handled (was "Explicitly NOT in Phase 3" — overtaken by events)

The original checklist said to skip these until real users showed up. They got built
anyway, because they paid off for the *single* SCS instance first:

- ✅ **CI / automated tests** — GitHub Actions runs pytest (109) + `manage.py check` on
  every push; the suite is the spine guard, not a user-facing nicety.
- ✅ **Upgrade-path tooling** — `setup.sh` (install), `update.sh` (tagged, auto-rollback),
  `mb_backup.sh` + `restore.sh` (backup/restore), `export_data` (portable export),
  `release.sh` (semver tagging). The whole bus-factor bar.

Still genuinely deferred until demand is real:

- Docker / containerization (would fork the systemd model; `setup.sh` is the easy-install
  answer for now).
- Multi-OS install matrix (Windows/Mac). `setup.sh` targets the Debian/Ubuntu apt family;
  any systemd Linux with Python ≥3.10 works.
- Issue templates, contribution guidelines, public roadmap.
- The in-app admin "Update" button (the last self-sufficiency rung — a convenience for
  single-instance adopters; SCS stays staging-first).

---

## The one rule that keeps this from becoming a job

Support is a dial you control, not a faucet stuck open. You can say "PRs welcome,"
ignore feature requests, go quiet for months, and come back. Open source tolerates
intermittent capacity better than client work ever will. Set expectations once in the
README, then tend it as lightly as you want.
