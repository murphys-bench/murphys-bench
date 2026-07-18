# Security Policy

Murphy's Bench handles customer data, device credentials, and organization credentials for the shops that run it. If you find a security issue, please don't open a public GitHub issue for it.

## Reporting a Vulnerability

Use GitHub's private vulnerability reporting for this repo: go to the **Security** tab → **Report a vulnerability**. That opens a private advisory only visible to the maintainer until it's resolved.

If you can't use that for some reason, open a regular issue with as little detail as possible (e.g. "possible auth bypass, will share privately") and ask for another way to reach out.

## What to Expect

This is a solo-maintained project run around real shop work, not a company with a security team. I'll do my best to respond promptly and fix confirmed issues, but there's no guaranteed response time or bug bounty.

## Scope

Murphy's Bench is self-hosted software — you run it on your own server. Most of its security posture (TLS termination, network exposure, OS hardening, backup encryption) is the self-hoster's responsibility, covered in `docs/deployment-tls.md` and the install docs. Reports about the application code itself (auth, permissions, injection, credential handling, data exposure between clients/techs) are the ones this policy covers.
