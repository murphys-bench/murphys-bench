# Murphy's Bench — Email System

> Inbound (mail → tickets) and outbound (replies + branded HTML). The most gotcha-heavy subsystem.

## Outbound email

Client-facing emails are sent as **HTML with a plain-text alternative** (`EmailMultiAlternatives`), rendered from `core/templates/core/email/base_email.html` via `email_utils.py`.

Structure: **header bar → body → signature → footer**.

### Branding (Settings → Email Templates → "Email Branding")

- `email_header_color` and `email_logo` are editable, with a live preview, and are **decoupled from the app's own colours** on purpose. Blank falls back to the app Title Bar colour / company logo.
- **Header text colour is auto-computed** from the header-bar colour for contrast — it is *not* a stored setting. (Don't reintroduce a manual text-colour field; it caused unreadable black-on-teal.)

### Logo embedding (gotcha)

The logo is embedded **inline** as a CID attachment via `multipart/related` (`msg.mixed_subtype = 'related'`, `Content-ID: logo`, referenced as `cid:logo` in the template). Without the `related` subtype, `cid:logo` won't resolve and mail clients dump the full image as an attachment instead. The image is downscaled with Pillow before embedding. (Switches to a public URL once Cloudflare/HTTPS is live.)

### Signatures

`EmailSignature` model: a default signature with per-template FK overrides. Full CRUD in Settings → Email Templates.

### Templates

Four trigger-based `EmailTemplate` records, editable in native Settings (subject + body, active toggle, variable reference). To show literal `{{ token }}` placeholders in a template's help text, wrap them in `{% verbatim %}…{% endverbatim %}`.

### Suppression / audit

- `SuppressedAddress` — exact addresses that never receive automated mail.
- `EmailSendLog` — every outbound attempt is logged (success or failure). A bad template records a *failed* `EmailSendLog` rather than failing silently.

## Inbound email

Polled by `fetch_inbound_email` (systemd timer, every 2 min) over **POP3 with delete-from-server**.

> Switched from IMAP "leave on server" → POP3 delete-from-server to kill a duplication bug: forwarded mail had no usable `Message-ID`, so the dedup guard couldn't catch it and each poll re-created the same ticket.
> ⚠️ **Tradeoff:** MB is now the only copy of inbound mail. The nightly DB backup is your only inbound-mail backup.

Configured in **Settings → Inbound Email** (mailbox, credentials, protocol).

> 📌 **Open action:** still pointed at `testing@shamrockcomputerservices.com`. Switch to the real support inbox when confident.

### Threading logic

Matching is done on the **subject-line `[TKT-…]` token**, not on `In-Reply-To`/`References` headers.

```python
TICKET_RE = re.compile(r'\[?(TKT-[\d-]+)\]?', re.IGNORECASE)
```

This matches both sequential (`TKT-00005`) and legacy date-based (`TKT-20260610-0001`) numbers.

A subject-matched reply **always threads into its ticket**:

| Ticket state | What a client reply does |
|---|---|
| open / in progress / etc. | Appends as a reply |
| **converted** (live work order) | Stays `converted`, flagged `needs_response` — never un-converts the WO |
| **closed** | **Reopens** to `open` |
| no match | Creates a new ticket |

> This guard was the root cause of the production orphan-ticket bug (TKT-00008/00009): the old check excluded `converted`/`closed`, so replies to them spawned new tickets. Fixed in session 29 with regression tests — don't reintroduce a status exclusion here.

### Quote handling

The full inbound thread is **kept** at ingestion (`strip_quoted_replies` is intentionally OFF). Quoted history is folded into a collapsible greyed blockquote **at display time** (`reply_body` / `split_reply_quote` in `mb_icons.py`), not destroyed at ingestion.

### Audit

`InboundEmailLog` records every fetched message.

## Conversation rendering (ticket replies)

Reply appearance is keyed on `reply.created_by`:

| Condition | Style | Meaning |
|---|---|---|
| `created_by` empty | green, shows contact name | inbound client reply |
| set + `internal` | yellow | internal note |
| set + `customer_visible` | blue | staff → customer |

The header reads "*&lt;who&gt; · &lt;direction&gt;*" (e.g. "Tech · to customer" / "Contact · client reply"), not "Customer Visible".

## One face to the client (important policy)

The ticket is the **only** client-facing channel. A bench tech who needs the customer contacted does **not** email them from the work order — they message the ticket tech **internally** (stored as an internal `TicketReply` + an in-app `Notification`). The ticket tech makes the actual client contact.

> An "email-from-work-order" approach was built and then **reverted** — it created a second client-facing voice. **Customer-visible WO notes mean only "shows on the printed repair report"** — passive, no email. Do not make WO notes email clients.

## Quick reference — failure triage

- **Mail not becoming tickets:** run `fetch_inbound_email` manually; confirm Settings → Inbound Email mailbox/credentials; check `InboundEmailLog`.
- **Outbound not arriving:** check `EmailSendLog` for the failed attempt + reason; confirm SMTP in Settings → Outbound Email.
- **Logo showing as an attachment:** the `multipart/related` subtype was lost — check `email_utils.py`.
- **Reply created a new ticket instead of threading:** the subject lost its `[TKT-…]` token, or the regex was changed.
