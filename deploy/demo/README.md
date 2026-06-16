# Demo / test instance deploy configs

Real, working configs captured from the demo VM stand-up (10.58.58.223,
Ubuntu 24.04). These resolve the `⚠ VERIFY` placeholders in the top-level
`INSTALL.md` — they are known-good, not templates.

## Files
- **`murphys-bench.service`** — Gunicorn systemd unit. Binds **TCP `127.0.0.1:8001`**
  (chosen over a unix socket to avoid nginx/www-data socket-permission fuss).
  Django reads secrets from `/opt/murphys-bench/.env` via python-decouple, so no
  `EnvironmentFile=` is needed.
- **`nginx-murphys-bench.conf`** — Nginx site. Proxies to gunicorn on :8001,
  serves `/static/` and `/media/` directly, `client_max_body_size 25M`.

## Install (already done on the demo box; here for reproduction)
```bash
sudo cp deploy/demo/murphys-bench.service /etc/systemd/system/
sudo cp deploy/demo/nginx-murphys-bench.conf /etc/nginx/sites-available/murphys-bench
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/murphys-bench /etc/nginx/sites-enabled/murphys-bench
sudo systemctl daemon-reload
sudo systemctl enable --now murphys-bench
sudo nginx -t && sudo systemctl reload nginx
```

## Notes carried back into the install knowledge
- **Login user:** the app runs as the existing `scs-tech` login user (same as prod);
  no separate `--system` user was created.
- **Private repo:** the demo box has no GitHub credentials, so code was seeded by
  `rsync` from a local checkout rather than `git clone`. A real third-party install
  needs a deploy key or PAT instead.
- **gunicorn** is NOT in `requirements.txt` — it was `pip install`ed into the venv
  separately. (Worth adding to requirements.)
- **Network posture (demo):** `.env` sets `SECURE_SSL_REDIRECT=False`,
  `SESSION_COOKIE_SECURE=False`, `CSRF_COOKIE_SECURE=False`, `SECURE_HSTS_SECONDS=0`
  so plain-HTTP LAN access keeps working alongside Cloudflare edge HTTPS. The 4
  `check --deploy` HTTPS warnings are therefore expected on this box, not a problem.
- **Cloudflare:** the `nginx` config listens on :80 for the LAN IP and `_`; when the
  tunnel is added, point it at `http://localhost:80` and append the public hostname
  to `ALLOWED_HOSTS` + `CSRF_TRUSTED_ORIGINS` in `.env`.
