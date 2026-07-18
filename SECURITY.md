# Security Policy

Murphy's Bench handles customer information, device credentials, and organization credentials. Please do not report security vulnerabilities through a public GitHub issue.

## Reporting a Vulnerability

Use GitHub's private vulnerability reporting for this repository:

**Security → Report a vulnerability**

This creates a private security advisory that is visible only to the maintainer and invited participants until it is published.

If private vulnerability reporting is unavailable, open a public issue containing no technical details. State only that you may have found a security vulnerability and need a private way to share it.

Please include the following in the private report where possible:

- a description of the issue
- the affected version or commit
- steps needed to reproduce it
- the likely impact
- any suggested mitigation or fix

Do not include real customer data, credentials, or other sensitive information in the report.

## What to Expect

Murphy's Bench is maintained by one person alongside the daily work of running an IT service business. Reports will be reviewed and confirmed issues will be addressed as circumstances allow, but there is no guaranteed response or resolution time.

Murphy's Bench does not currently offer a bug bounty.

Please allow time for the issue to be investigated and corrected before disclosing it publicly.

## Scope

Reports covered by this policy include vulnerabilities in the Murphy's Bench application, such as:

- authentication or session handling
- authorization and role enforcement
- access to stored credentials
- data exposure between clients or users
- injection vulnerabilities
- unsafe file handling
- cross-site scripting or request forgery
- security-sensitive logging or audit behavior
- insecure application defaults

Murphy's Bench is self-hosted, so the operator is responsible for the security of the environment in which it runs. This includes:

- operating-system maintenance
- network exposure and firewall rules
- TLS termination
- reverse-proxy configuration
- server and database access
- backup storage and encryption
- protection of environment files and encryption keys

Deployment guidance is available in `docs/deployment-tls.md` and the installation documentation.

A deployment or configuration problem may still reveal an unsafe application default. Reports are welcome when it is unclear whether the issue belongs to Murphy's Bench or to the hosting environment.
