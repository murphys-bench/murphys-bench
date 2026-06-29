# Deployment, TLS, and HTTPS — how Murphy's Bench is meant to be served

This document explains **how Murphy's Bench (MB) handles HTTPS/TLS and why it's
built this way.** If you're evaluating or self-hosting MB and wondering "where's
the built-in HTTPS?" — this is the answer. The short version: **MB deliberately
does not terminate TLS itself; it runs behind a reverse proxy that does.** That
is the standard, correct architecture for a Django application, not an omission.

---

## The design principle

MB is an application (Django + Gunicorn + SQLite), not a static website. Like
virtually every self-hosted web app of its kind, it expects to sit **behind a
reverse proxy** (nginx, Caddy, Traefik, a Cloudflare Tunnel, or your existing web
server). The proxy is the public-facing piece that:

- terminates TLS (holds the certificate, speaks HTTPS to browsers), and
- forwards the request to MB over the local connection.

MB is **already built for this**:

- It trusts the proxy's `X-Forwarded-Proto` header (`SECURE_PROXY_SSL_HEADER`),
  so it knows when the original request was HTTPS even though the proxy→MB hop is
  plain HTTP on the same host/network.
- The hostname is configured via two environment settings — `ALLOWED_HOSTS` and
  `CSRF_TRUSTED_ORIGINS` — so MB works under whatever domain your proxy serves.
- The secure-cookie / HSTS / SSL-redirect flags are **environment toggles**
  (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`,
  `SECURE_HSTS_SECONDS`). They default off so an HTTP-on-LAN install works out of
  the box, and you turn them on once TLS is terminating in front of MB.

**Why build it this way instead of baking in HTTPS?** Because TLS is an
operational concern that varies enormously by deployment — cloud vs. LAN, domain
vs. no domain, your CA vs. Let's Encrypt vs. a CDN. Hard-coding one approach would
make MB *harder* to deploy in every environment that didn't match. Letting a
purpose-built proxy own TLS is how mature self-hosted software does it (it's the
same model as Nextcloud, GitLab, Paperless, etc.). It also means MB never has to
handle certificate files or renewals.

---

## A note on the threat model (so you can choose sensibly)

"No HTTPS" is not one risk — it depends on *where the unencrypted traffic flows*:

- **On the public internet** — never acceptable. Always terminate TLS at the edge
  (any option below).
- **Across an untrusted local network** — a malicious device on that segment could
  eavesdrop. Encrypt it.
- **On a trusted, controlled LAN segment** — the eavesdrop risk requires an
  attacker *already inside* your trusted network. Plain HTTP here is a defensible
  choice for a small/solo deployment, because the box isn't reachable from outside
  at all. (This is how the reference deployment runs internal production — see
  below.)

MB stores sensitive data (a credential vault, client info), so if your traffic
crosses anything less than a fully trusted segment, put TLS in front of it.

---

## Ways to put TLS in front of MB (pick one)

All of these are "front doors" to the same proxy-ready app. None is required by
MB; choose what fits your environment.

### 1. Cloudflare Tunnel — easiest for *remote* access, no open ports
`cloudflared` makes an **outbound** connection to Cloudflare; you never open an
inbound port or expose the box's IP. Cloudflare terminates HTTPS at its edge and
carries traffic to MB through the encrypted tunnel. Add Cloudflare Access in front
to gate it to named users. Best when you want secure access *from outside your
network* with minimal setup. (This is how the reference demo is served.)

### 2. Caddy — easiest *self-hosted* HTTPS with your own domain
Caddy fetches and renews a Let's Encrypt certificate automatically. A complete
config is roughly:

```
mb.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

That's it — Caddy handles the cert. Best when you have a domain and want HTTPS you
control without certificate babysitting.

### 3. nginx + a certificate (incl. a subdomain on a web server you already run)
The traditional approach: nginx terminates TLS and `proxy_pass`es to MB's
Gunicorn socket. The certificate can be:
- a **Let's Encrypt** cert (via certbot), or
- **DNS-01 issued** — proves domain ownership through a DNS TXT record, so you can
  get a *trusted* cert for a box that has **no open ports and isn't reachable from
  the internet** (e.g. a LAN-only host reached by name), or
- a cert you **already have for your domain** — including serving MB at a
  subdomain (`mb.yourdomain.com`) behind the cert on your existing web server, by
  adding a vhost that proxies that subdomain to MB. (A single-name website cert
  won't cover a new subdomain; you need a wildcard or a cert issued for the
  subdomain.)

Requirements that catch people: the cert must cover the **exact hostname** users
type, you must have the cert's **private key** on the proxy, and that hostname must
**resolve to the box**. Note you cannot get a public-CA cert for a bare private IP
(e.g. `https://10.0.0.5`) — serve MB by a **name**, not an IP.

### 4. Self-signed certificate / internal CA — LAN-only, no domain
If MB is internal-only and you don't have/ want a domain, a self-signed cert (or a
small internal CA) encrypts the traffic. Trade-off: browsers show a "not trusted"
warning until you install the cert/CA on the client machines.

### 5. Plain HTTP on a trusted LAN — a legitimate choice, not a missing feature
For an internal-only instance on a controlled segment, serving plain HTTP is a
reasonable, deliberate option (see the threat-model note above). If you later want
external access, move MB behind any option above.

---

## What to set on MB once TLS is in front

In MB's `.env`:

```
ALLOWED_HOSTS=mb.yourdomain.com
CSRF_TRUSTED_ORIGINS=https://mb.yourdomain.com
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=True
# Enable HSTS only once HTTPS is confirmed end-to-end (it is hard to undo):
# SECURE_HSTS_SECONDS=31536000
```

Then `python manage.py check --deploy` should come back clean.

---

## The reference deployment (how SCS runs it)

A concrete example of the model above:

- **Internal production** runs on a **trusted LAN segment over plain HTTP**, not
  reachable from the internet. This is a deliberate choice given the trusted
  network — no certificate is needed, and there is no external attack surface.
- **The public demo** runs on an **untrusted segment behind a Cloudflare Tunnel**
  with Cloudflare Access. All external traffic is encrypted (browser→Cloudflare is
  HTTPS; Cloudflare→box is the encrypted tunnel), and no inbound port is open. On
  the untrusted segment, a host firewall restricts direct access to the app port so
  the tunnel is the only way in.
- If internal production ever needs external access, it moves behind the tunnel
  too — it inherits the encrypted path automatically, with no certificate project.

**Takeaway:** MB's lack of built-in TLS is intentional and standard. You bring the
front door that suits your network; MB is ready to live behind any of them.
