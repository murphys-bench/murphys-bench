# Murphy's Bench — Development & Deploy Workflow

> How a change gets from the Mac to production. Follow the steps in order.

## The big picture

```
Edit on Mac (VS Code)  →  git commit + push  →  SSH to VM
   →  git pull  →  migrate (if needed)  →  restart service  →  test in browser
```

Code is never edited directly on the VM. The VM only ever **pulls** from GitHub.

## Local development

```bash
cd ~/Documents/Claude/murphys-bench
source venv/bin/activate
python manage.py runserver
# http://localhost:8000   ·   local-dev login: admin / password123
```

Local dev uses **SQLite** and a local `.env` with `DEBUG=True`. Production uses **PostgreSQL** with `DEBUG=False`.

### Before committing

```bash
python manage.py check                 # confirm no config errors
venv/bin/python -m pytest              # run the test suite
```

**Tests are required for anything touching data** — deletion, billing state, ticket/WO lifecycle, email routing, number generation, or permissions. Any such change ships *with* a test that locks in the behaviour. No exceptions.

If you add a model or change a field:

```bash
python manage.py makemigrations
python manage.py migrate               # apply locally first
```

## Deploy to production

1. **On the Mac** — commit and push:
   ```bash
   git add -A
   git commit -m "…"
   git push
   ```

2. **SSH to the VM:**
   ```bash
   ssh -i ~/.ssh/id_ed25519 scs-tech@10.58.58.82
   ```

3. **Pull and apply migrations** (from `/opt/murphys-bench/`):
   ```bash
   cd /opt/murphys-bench
   git pull
   venv/bin/python manage.py migrate     # only if there are new migrations
   ```

4. **Restart the app server:**
   ```bash
   sudo systemctl restart murphys-bench
   ```

5. **Verify:** load the app in a browser on the LAN and confirm the change is live and nothing 500s. Check `journalctl -u murphys-bench -f` if anything looks wrong.

## Migrations — apply in BOTH places

A new migration must be applied **locally (dev) and on the VM (prod)**. The deploy `migrate` step (3) is what applies it to production. Skipping it leaves the prod database schema behind the code and causes errors.

> ⚠️ **Never run `manage.py flush`** — it destroys configuration too. To wipe operational data while keeping all config + superusers, use the purpose-built command (see *Operations & Maintenance*).

## End-of-session hygiene

When wrapping up a meaningful change, do a full "button things up" sweep:

- Update `CLAUDE.md` (state, decisions, gotchas) and `TODO.md` (roadmap).
- Update `docs/next-session-prompt.md`.
- Commit and push.
- Deploy to the VM if the change is meant to be live.

## Working norms

- **Plan before building anything non-trivial** — get the approach approved first.
- **Review before it goes live** for anything touching money, credentials, permissions, or data deletion.
- **Fail loud, not silent** — no `except: pass` / `fail_silently` that hides a real failure. Catch so the user isn't crashed, but log it.
- **Every config option is a permanent cost** — prefer a good hardcoded default over a new toggle until a real need appears.
- Match the model to the task: a frontier reasoning model for planning / architecture / review / gnarly debugging; a fast model for routine CRUD, forms, views, templates.
