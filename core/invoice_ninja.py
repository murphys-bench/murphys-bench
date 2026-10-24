"""
Invoice Ninja integration — one-directional push from a Work Order.

Authority model: MB is L1 (operational, may drift), Invoice Ninja is L2 (the
billing system of record). Data only flows UP. MB hands IN the WO's priced line
items as a DRAFT invoice; IN owns assembly, numbering, payment. Nothing
authoritative flows back. See memory project_in_integration for the full design.

Everything here fails LOUD: a problem raises InvoiceNinjaError with a readable
message that the (user-triggered) view surfaces, rather than silently swallowing.
"""
import logging
import secrets
from datetime import timedelta

import requests
from urllib3.exceptions import NewConnectionError

logger = logging.getLogger('core')

# IN v5 REST API. Token auth via header; XMLHttpRequest header required by IN.
API_PREFIX = '/api/v1'
# (connect, read) seconds. Connecting is fast or hopeless; IN answers in well
# under a second when healthy (tested Oct 2 2026: 25-180 ms), so a page never
# needs to hang 20 seconds waiting on an IN that is down.
TIMEOUT = (5, 15)

# The invoice custom field MB writes its own reference into, so an invoice whose
# answer was lost can be found again before anything is resent. IN ignores
# idempotency keys on invoices (tested Oct 2 2026), so this is MB's only reliable
# handle. SCS confirmed custom field 4 is unused (Oct 23 2026).
MB_KEY_FIELD = 'custom_value4'

# How long a send that has not finished counts as still in progress. A second
# click inside this window is refused instead of racing the first.
IN_FLIGHT = timedelta(seconds=30)


class InvoiceNinjaError(Exception):
    """Any failure talking to Invoice Ninja. Carries a user-readable message."""


class InvoiceNinjaOutcomeUnknown(InvoiceNinjaError):
    """The request may have reached IN, but no answer came back, so whether IN
    acted on it is unknown. Callers that create things must look before they
    send again; never read this as "nothing happened"."""


def _config():
    """Return (base_url, token) from SiteSettings, or raise if not configured."""
    from .models import SiteSettings
    s = SiteSettings.get()
    base = (s.invoice_ninja_url or '').strip().rstrip('/')
    token = (s.invoice_ninja_token or '').strip()
    if not base or not token:
        raise InvoiceNinjaError('Invoice Ninja is not configured (set the URL and API token in Settings).')
    return base, token


def _headers(token):
    return {
        'X-API-TOKEN': token,
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }


def _never_connected(exc):
    """True when a requests ConnectionError happened before anything was sent
    (refused, no route, bad name, TLS handshake). Anything else, such as the
    connection dropping while waiting for the answer, may have been acted on."""
    if isinstance(exc, requests.exceptions.SSLError):
        return True
    reason = getattr(exc.args[0], 'reason', None) if exc.args else None
    return isinstance(reason, NewConnectionError)


def _in_message(resp):
    """IN's own error text from a JSON error body, or '' when the body is not
    IN's JSON (a proxy's HTML error page, say)."""
    try:
        body = resp.json()
    except ValueError:
        return ''
    if not isinstance(body, dict):
        return ''
    msg = str(body.get('message') or '')
    errors = body.get('errors')
    if isinstance(errors, dict):
        # A 422's useful part is the first field error, not "The given data was invalid."
        for field_errors in errors.values():
            first = field_errors[0] if isinstance(field_errors, list) and field_errors else field_errors
            if first:
                return f'{msg} {first}'.strip()
    return msg


def _http_error(resp):
    """Turn an IN error response into the exception the user should see."""
    code = resp.status_code
    msg = _in_message(resp)
    if code in (401, 403):
        # IN answers a wrong, deleted or archived token with 403 "Invalid token"
        # (tested Oct 2 2026), not 401.
        return InvoiceNinjaError(
            f'Invoice Ninja refused the API token ({code}{": " + msg if msg else ""}). '
            'Check the token in Settings: it may be mistyped, deleted or archived in '
            'Invoice Ninja, or its user may not have permission.'
        )
    if code == 429:
        if 'duplicate' in msg.lower():
            # IN's one-second guard: an identical request arrived just before this
            # one. That earlier request may have done the work.
            return InvoiceNinjaOutcomeUnknown(
                'Invoice Ninja is still handling the same request sent a moment ago.'
            )
        # IN sends no "try again in" time with this (tested Oct 2 2026); a
        # minute cleared it.
        return InvoiceNinjaError(
            'Invoice Ninja is limiting requests right now (too many in a short time). '
            'Wait a minute and try again.'
        )
    if not msg:
        logger.warning('Invoice Ninja error page %s: %s', code, resp.text[:300])
        text = (
            f'Invoice Ninja returned an error page ({code}). It may be down, restarting '
            'or overloaded; try again shortly.'
        )
        # A gateway error means the proxy gave up waiting, not that IN refused:
        # IN may still have done the work.
        if code in (502, 504):
            return InvoiceNinjaOutcomeUnknown(text)
        return InvoiceNinjaError(text)
    return InvoiceNinjaError(f'Invoice Ninja returned {code}: {msg}')


def _request(method, path, *, params=None, json=None):
    """Make an IN API call. Raises InvoiceNinjaError on any transport/HTTP error,
    and its subclass InvoiceNinjaOutcomeUnknown when the request may have been
    acted on without MB hearing back."""
    base, token = _config()
    url = f'{base}{API_PREFIX}{path}'
    try:
        resp = requests.request(
            method, url, headers=_headers(token), params=params, json=json, timeout=TIMEOUT,
        )
    except requests.ConnectTimeout as e:
        logger.warning('Invoice Ninja request failed: %s %s — %s', method, path, e)
        raise InvoiceNinjaError(
            f'Could not reach Invoice Ninja (no connection within {TIMEOUT[0]} seconds). '
            'Check the URL in Settings and that Invoice Ninja is running.'
        )
    except requests.ConnectionError as e:
        logger.warning('Invoice Ninja request failed: %s %s — %s', method, path, e)
        if _never_connected(e):
            raise InvoiceNinjaError(
                'Could not reach Invoice Ninja. Check the URL in Settings and that '
                'Invoice Ninja is running.'
            )
        raise InvoiceNinjaOutcomeUnknown('The connection to Invoice Ninja dropped before it answered.')
    except requests.Timeout as e:
        logger.warning('Invoice Ninja request failed: %s %s — %s', method, path, e)
        raise InvoiceNinjaOutcomeUnknown(f'Invoice Ninja did not answer within {TIMEOUT[1]} seconds.')
    except requests.RequestException as e:
        logger.warning('Invoice Ninja request failed: %s %s — %s', method, path, e)
        raise InvoiceNinjaError(f'Could not reach Invoice Ninja: {e}')
    if resp.status_code >= 400:
        raise _http_error(resp)
    try:
        return resp.json()
    except ValueError:
        raise InvoiceNinjaError(
            'Invoice Ninja answered with a web page instead of API data. Check the URL '
            'in Settings: it should be the API address (Cloud: https://invoicing.co).'
        )


def test_connection():
    """Cheap auth/URL check used by the Settings 'Test Connection' button.
    Returns a short success string or raises InvoiceNinjaError."""
    _request('GET', '/clients', params={'per_page': 1})
    return 'Connected to Invoice Ninja successfully.'


# ---------------------------------------------------------------------------
# Creating things in IN at most once. A lost answer (tested Oct 2 2026) leaves
# MB not knowing whether IN created the record, and a plain retry then makes a
# second one. Every create below records what it is about to send, sends it, and
# when the answer is lost, looks for the record before anyone can send again.
# ---------------------------------------------------------------------------

_IN_PROGRESS_MSG = 'This is already being sent to Invoice Ninja. Wait a few seconds and try again.'


def _find_invoice_by_key(key):
    """The IN invoice carrying MB's reference `key`, or None. Deleted invoices
    don't count. Each row's field is checked too, so an IN that ignored the
    filter could not hand back somebody else's invoice."""
    data = _request('GET', '/invoices', params={
        MB_KEY_FIELD: key, 'status': 'active,archived', 'per_page': 5,
    })
    for row in (data.get('data') or []):
        if str(row.get(MB_KEY_FIELD) or '') == key and not row.get('is_deleted'):
            return row
    return None


def _store_pushed_invoice(host, invoice):
    """Record IN's invoice on the host and release its pending send, in one
    write, so the host is never left both pushed and pending."""
    host.invoice_ninja_id = str(invoice['id'])
    host.invoice_ninja_ref = str(invoice.get('number') or '')
    host.in_push_key = ''
    host.in_push_started_at = None
    host.save(update_fields=['invoice_ninja_id', 'invoice_ninja_ref', 'in_push_key', 'in_push_started_at'])


def _release_push(host):
    type(host).objects.filter(pk=host.pk).update(in_push_key='', in_push_started_at=None)
    host.in_push_key = ''
    host.in_push_started_at = None


def _claim_push(host):
    """Mark `host` as being sent and return the reference key to send with.
    The claim is a conditional UPDATE, so of two clicks at once only one wins
    (SQLite serializes the writes); the other is told to wait. Returns
    (key, existing_invoice): when an earlier send whose answer was lost turns
    out to have landed, existing_invoice is that invoice and nothing is sent."""
    from django.utils import timezone
    model = type(host)
    now = timezone.now()
    host.refresh_from_db(fields=['in_push_key', 'in_push_started_at'])
    key = host.in_push_key
    if not key:
        key = f'mb-{model._meta.model_name}-{host.pk}-{secrets.token_hex(4)}'
        if not model.objects.filter(pk=host.pk, in_push_key='').update(in_push_key=key, in_push_started_at=now):
            raise InvoiceNinjaError(_IN_PROGRESS_MSG)
    else:
        found = _find_invoice_by_key(key)
        if found:
            return key, found
        started = host.in_push_started_at
        if started and now - started < IN_FLIGHT:
            raise InvoiceNinjaError(_IN_PROGRESS_MSG)
        # The earlier send never landed. Take it over, keeping its key so a late
        # arrival would still be recognized.
        if not model.objects.filter(pk=host.pk, in_push_key=key, in_push_started_at=started).update(in_push_started_at=now):
            raise InvoiceNinjaError(_IN_PROGRESS_MSG)
    host.in_push_key = key
    host.in_push_started_at = now
    return key, None


def _create_invoice(host, payload):
    """POST `payload` as a new IN invoice for `host` (a WorkOrder or Sale) at
    most once, store IN's id and number on the host, and return IN's invoice.

    IN ignores idempotency keys on invoices, so MB writes its own key into the
    invoice (MB_KEY_FIELD) and keeps it on the host until IN's answer is in.
    """
    key, existing = _claim_push(host)
    if existing:
        _store_pushed_invoice(host, existing)
        return existing
    try:
        data = _request('POST', '/invoices', json={**payload, MB_KEY_FIELD: key})
    except InvoiceNinjaOutcomeUnknown as e:
        try:
            found = _find_invoice_by_key(key)
        except InvoiceNinjaError:
            found = None
        if found:
            _store_pushed_invoice(host, found)
            return found
        # Keep the key: the next attempt looks for this invoice before sending.
        raise InvoiceNinjaOutcomeUnknown(
            f'{e} It may or may not have created the invoice. MB will look for it '
            'before sending again, so it is safe to try again in a minute.'
        )
    except InvoiceNinjaError:
        # IN answered with a refusal, or was never reached: nothing was created.
        _release_push(host)
        raise
    invoice = data['data']
    _store_pushed_invoice(host, invoice)
    return invoice


def _invoice_label(invoice_id):
    data = _request('GET', f'/invoices/{invoice_id}')
    return status_label((data.get('data') or {}).get('status_id'))


def _create_payment(payload, *, key):
    """POST a payment once. IN honours idempotency_key on payments (tested
    Oct 2 2026), and `key` is derived from the invoice, so a retry
    after a lost answer is refused by IN rather than recorded twice. A refusal
    on a retry (key already used, or the invoice already paid) is settled by
    asking IN whether the invoice is now Paid."""
    invoice_id = payload['invoices'][0]['invoice_id']
    try:
        _request('POST', '/payments', json={**payload, 'idempotency_key': key})
        return
    except InvoiceNinjaError as e:
        try:
            paid = _invoice_label(invoice_id) == 'Paid'
        except InvoiceNinjaError:
            paid = False
        if paid:
            logger.info('Invoice Ninja payment %s: invoice %s already Paid (%s)', key, invoice_id, e)
            return
        if isinstance(e, InvoiceNinjaOutcomeUnknown):
            raise InvoiceNinjaOutcomeUnknown(
                f'{e} The payment may or may not have been recorded. Trying again is '
                'safe: Invoice Ninja refuses the same payment twice.'
            )
        raise


def _payment_key(invoice_id):
    # MB pays an IN invoice in full, once, so the invoice id is the payment's identity.
    return f'mb-pay-{invoice_id}'


def in_client_name(client):
    """Type-aware name that prints on the IN invoice.

    Business → the business name (Client.name). Residential → the primary
    contact's full name, because MB files residential clients by bare last name
    (Client "Dorkleputz") which would otherwise invoice as "Dorkleputz" alone.
    """
    if client.client_type == 'business':
        return client.name
    primary = client.contacts.filter(is_primary=True).first() or client.contacts.first()
    if primary:
        full = f'{primary.first_name} {primary.last_name}'.strip()
        if full:
            return full
    return client.name


def _client_payload(client):
    """Build IN's client create payload. NOTE: IN replaces the whole contacts
    array on POST/PUT (it does not merge) — we only ever CREATE, so sending the
    single primary contact is correct. Do not 'improve' this into a merge."""
    primary = client.contacts.filter(is_primary=True).first() or client.contacts.first()
    contact = {}
    if primary:
        contact = {
            'first_name': primary.first_name,
            'last_name': primary.last_name,
            'email': primary.email,
            'phone': primary.phone if hasattr(primary, 'phone') else '',
            'send_email': getattr(primary, 'receives_email', True),
        }
    payload = {
        'name': in_client_name(client),
        'address1': client.address_line1,
        'address2': client.address_line2,
        'city': client.address_city,
        'state': client.address_state,
        'postal_code': client.address_zip,
    }
    if contact:
        payload['contacts'] = [contact]
    return payload


def find_or_create_client(client):
    """Return the IN client id for an MB Client, creating it if needed.

    Link once, don't sync: if we already stored an IN id, use it. Else match by
    the primary contact's email; else create. Saves the id back on the MB Client.
    """
    from django.utils import timezone
    if client.invoice_ninja_id:
        return client.invoice_ninja_id

    primary = client.contacts.filter(is_primary=True).first() or client.contacts.first()
    email = (primary.email if primary else '') or client.email
    if email:
        data = _request('GET', '/clients', params={'email': email})
        rows = data.get('data') or []
        if rows:
            in_id = str(rows[0]['id'])
            client.invoice_ninja_id = in_id
            client.save(update_fields=['invoice_ninja_id'])
            return in_id

    payload = _client_payload(client)
    started = timezone.now()
    try:
        data = _request('POST', '/clients', json=payload)
        in_id = str(data['data']['id'])
    except InvoiceNinjaOutcomeUnknown as e:
        try:
            in_id = _find_new_client(email, payload['name'], started)
        except InvoiceNinjaError:
            in_id = None
        if not in_id:
            if email:
                raise InvoiceNinjaOutcomeUnknown(
                    f'{e} It may or may not have created the client. MB looks for the '
                    'client by email first, so it is safe to try again in a minute.'
                )
            raise InvoiceNinjaOutcomeUnknown(
                f'{e} It may or may not have created the client "{payload["name"]}". '
                'This client has no email for MB to find it by, so check the Invoice '
                'Ninja client list before trying again, or a second copy could be made.'
            )
    client.invoice_ninja_id = in_id
    client.save(update_fields=['invoice_ninja_id'])
    return in_id


def _find_new_client(email, name, since):
    """After a lost answer on a client create: the IN client it made, or None.
    By email when there is one (the same lookup the next attempt makes anyway);
    otherwise by exact name, and only among clients IN created since the attempt
    began (a minute's grace for clock drift), so an older client of the same
    name is never adopted."""
    if email:
        rows = _request('GET', '/clients', params={'email': email}).get('data') or []
        return str(rows[0]['id']) if rows else None
    rows = _request('GET', '/clients', params={'filter': name}).get('data') or []
    floor = since.timestamp() - 60
    for row in rows:
        if (row.get('name') or '').strip() == name and (row.get('created_at') or 0) >= floor:
            return str(row['id'])
    return None


def _line_items_payload(host):
    """Map a host's PRICED line items to IN invoice line items. Unpriced lines
    (no unit_price) are internal/diagnostic and are not billed. `host` is any
    object exposing a `line_items` GenericRelation (WorkOrder or Sale)."""
    items = []
    for li in host.line_items.all():
        if li.unit_price is None:
            continue
        # Client-facing text: the line's print description, falling back to its label.
        notes = li.print_description() or li.description
        items.append({
            'notes': notes,
            'cost': float(li.unit_price),
            'quantity': float(li.quantity),
        })
    return items


_IN_STATUS_LABELS = {
    1: 'Draft',
    2: 'Sent',
    3: 'Partial',
    4: 'Paid',
    5: 'Cancelled',
    6: 'Reversed',
    -1: 'Overdue',
}


def check_invoice_status(work_order):
    """Read the current status of a pushed invoice from IN and record it on the Invoice row.

    Requires work_order.invoice_ninja_id to be set (i.e. the WO was already pushed).
    Updates Invoice.in_status + Invoice.in_status_checked_at; does NOT touch billing_status.
    Returns the human-readable IN status string.
    Raises InvoiceNinjaError on any communication or configuration problem.
    """
    from django.utils import timezone
    from .models import Invoice

    in_id = (work_order.invoice_ninja_id or '').strip()
    if not in_id:
        raise InvoiceNinjaError('This work order has not been pushed to Invoice Ninja yet.')

    data = _request('GET', f'/invoices/{in_id}')
    invoice_data = data.get('data', {})
    status_id = invoice_data.get('status_id')
    label = _IN_STATUS_LABELS.get(int(status_id), f'Unknown ({status_id})') if status_id is not None else 'Unknown'

    invoice, _ = Invoice.objects.get_or_create(work_order=work_order)
    invoice.invoice_ninja_id = in_id
    invoice.in_status = label
    invoice.in_status_checked_at = timezone.now()
    invoice.save(update_fields=['invoice_ninja_id', 'in_status', 'in_status_checked_at'])

    return label


def push_work_order(work_order):
    """Create a DRAFT invoice in IN from the WO's priced lines.

    IN assigns the invoice number (we omit it) and owns assembly. We stamp the
    WO number into po_number for traceability and store the returned IN invoice
    id + number on the WO. Raises InvoiceNinjaError (fail loud) on any problem;
    on a refusal nothing is saved on the WO, so the button stays a clean 'Send'.
    When IN's answer is lost, the WO keeps a pending key and the next attempt
    finds the invoice instead of creating a second one.
    """
    line_items = _line_items_payload(work_order)
    if not line_items:
        raise InvoiceNinjaError(
            'This work order has no priced line items to send. Add a price to at '
            'least one line first.'
        )

    in_client_id = (
        find_or_create_client(work_order.client) if work_order.client_id
        else find_or_create_walkin_client()
    )
    payload = {
        'client_id': in_client_id,
        'po_number': work_order.work_order_number,  # WO# for bank→IN→MB traceability
        'line_items': line_items,
        # No 'number' → IN auto-assigns from its own sequence (no POS collision).
        # No email action → created as a draft for the human to assemble in IN.
    }
    _create_invoice(work_order, payload)
    return work_order.invoice_ninja_ref or work_order.invoice_ninja_id


# Counter-sale checkout push (Slice 3b). Unlike the WO push (a DRAFT), a sale is
# pushed as a PAID invoice: create the invoice, then post a payment against it so
# IN shows Paid. MB never charges anything — it mirrors a payment Mike already took.

WALKIN_CLIENT_NAME = 'Walk-In'

# MB payment method → IN payment type_id. Left EMPTY on purpose: IN's PaymentType
# constants must be confirmed against the live instance before we send a type_id
# (a wrong id makes /payments 400). Until populated, the payment is posted without
# a categorized type — it still marks the invoice Paid; the method is captured in
# the payment's private_notes. Populate after verifying in Mike's IN instance.
_IN_PAYMENT_TYPE_IDS = {}


def find_or_create_walkin_client():
    """Return the IN client id for anonymous counter sales, creating a standing
    'Walk-In' client if needed. Link-once: cache the id on SiteSettings so we
    never create duplicates (same philosophy as Client.invoice_ninja_id)."""
    from .models import SiteSettings
    s = SiteSettings.get()
    cached = (s.invoice_ninja_walkin_client_id or '').strip()
    if cached:
        return cached

    # Match an existing 'Walk-In' by exact name before creating one.
    data = _request('GET', '/clients', params={'filter': WALKIN_CLIENT_NAME})
    for row in (data.get('data') or []):
        if (row.get('name') or '').strip() == WALKIN_CLIENT_NAME:
            in_id = str(row['id'])
            s.invoice_ninja_walkin_client_id = in_id
            s.save(update_fields=['invoice_ninja_walkin_client_id'])
            return in_id

    try:
        data = _request('POST', '/clients', json={'name': WALKIN_CLIENT_NAME})
    except InvoiceNinjaOutcomeUnknown as e:
        # The name lookup above finds it next time, so a retry cannot duplicate it.
        raise InvoiceNinjaOutcomeUnknown(
            f'{e} It may or may not have created the Walk-In client. MB looks for '
            'it by name first, so it is safe to try again in a minute.'
        )
    in_id = str(data['data']['id'])
    s.invoice_ninja_walkin_client_id = in_id
    s.save(update_fields=['invoice_ninja_walkin_client_id'])
    return in_id


def push_sale(sale, draft=False):
    """Create an invoice in IN from a sale's priced lines.

    ``draft=False`` (counter lane, Lane B): after creating the invoice we post a
    payment for the sale's recorded amount so IN marks it **Paid** — MB is
    mirroring a payment already taken at the counter.

    ``draft=True`` (recurring lane, Lane C): create the invoice **only**, no
    payment posted → it lands in IN as an unpaid **Draft**. MB charges nothing;
    the operator charges the tokenized card (or receives the check) by hand in
    IN, and MB learns the result later via ``check_sale_status``. This is the
    crawl-walk-run phase-1 path — prove each card individually before any
    automation.

    A sale with a client bills under that client; an anonymous walk-in bills under
    the standing 'Walk-In' client. Fail loud on any problem; on failure nothing
    partial is trusted on the sale (the caller surfaces the error and offers a retry).
    """
    from django.utils import timezone

    line_items = _line_items_payload(sale)
    if not line_items:
        raise InvoiceNinjaError(
            'This sale has no priced line items to send. Add a price to at least '
            'one line first.'
        )

    if sale.client_id:
        in_client_id = find_or_create_client(sale.client)
    else:
        in_client_id = find_or_create_walkin_client()

    # A paid send whose invoice landed but whose payment did not (in_status is
    # only set once the whole send finished) resumes with the payment instead
    # of making a second invoice.
    resume = not draft and bool(sale.invoice_ninja_id) and not sale.in_status
    if not resume:
        _create_invoice(sale, {
            'client_id': in_client_id,
            'po_number': sale.sale_number,  # SALE# for bank→IN→MB traceability
            'line_items': line_items,
        })
    invoice_id = sale.invoice_ninja_id

    if draft:
        # No payment posted — the invoice stays an unpaid Draft in IN.
        sale.in_status = 'Draft'
        sale.in_status_checked_at = timezone.now()
        sale.save(update_fields=['in_status', 'in_status_checked_at'])
        return sale.invoice_ninja_ref or sale.invoice_ninja_id

    # Post the payment → IN marks the invoice Paid (not Draft).
    amount = float(sale.amount if sale.amount is not None else sale.line_items_total)
    payment_payload = {
        'client_id': in_client_id,
        'amount': amount,
        'invoices': [{'invoice_id': invoice_id, 'amount': amount}],
        'transaction_reference': sale.reference or '',
        'private_notes': f'Counter sale {sale.sale_number} — {sale.get_payment_method_display() or sale.payment_method or "unspecified"}',
        'date': timezone.localdate().isoformat(),
    }
    type_id = _IN_PAYMENT_TYPE_IDS.get(sale.payment_method)
    if type_id is not None:
        payment_payload['type_id'] = type_id
    try:
        _create_payment(payment_payload, key=_payment_key(invoice_id))
    except InvoiceNinjaError as e:
        raise type(e)(
            f'Invoice #{sale.invoice_ninja_ref or invoice_id} is in Invoice Ninja, but '
            f'its payment was not confirmed: {e} Retrying adds the payment to that '
            'same invoice.'
        )

    sale.in_status = 'Paid'
    sale.in_status_checked_at = timezone.now()
    sale.save(update_fields=['in_status', 'in_status_checked_at'])
    return sale.invoice_ninja_ref or sale.invoice_ninja_id


def check_sale_status(sale):
    """Read a pushed sale's current invoice status back from IN and record it on
    the sale — the read-back that keeps MB's mirror complete once the operator has
    charged the card (or the check has cleared) in IN, without MB re-entering
    anything. Mirrors check_invoice_status (WorkOrder), but writes the sale's own
    in_status trio. Returns the human-readable status; raises on any problem."""
    from django.utils import timezone

    in_id = (sale.invoice_ninja_id or '').strip()
    if not in_id:
        raise InvoiceNinjaError('This sale has not been pushed to Invoice Ninja yet.')

    data = _request('GET', f'/invoices/{in_id}')
    invoice_data = data.get('data', {})
    status_id = invoice_data.get('status_id')
    label = _IN_STATUS_LABELS.get(int(status_id), f'Unknown ({status_id})') if status_id is not None else 'Unknown'

    sale.in_status = label
    sale.in_status_checked_at = timezone.now()
    sale.save(update_fields=['in_status', 'in_status_checked_at'])
    return label


def list_client_invoices(in_client_id):
    """All non-deleted invoices IN holds for one IN client — the read behind
    Pull from IN (mirroring invoices IN's own recurring engine generated, so
    MB's billing worklist can reflect them instead of duplicating them).
    Read-only toward IN; raises InvoiceNinjaError on any problem.

    Follows IN's pagination to the end rather than quietly reading page one —
    a missed page would report 'no candidate' and steer the operator into
    preparing a duplicate draft. Fails loud past a sanity cap instead of
    scanning forever."""
    rows, page = [], 1
    while True:
        data = _request('GET', '/invoices',
                        params={'client_id': in_client_id, 'per_page': 100, 'page': page})
        rows.extend(r for r in (data.get('data') or []) if not r.get('is_deleted'))
        pagination = ((data.get('meta') or {}).get('pagination') or {})
        total_pages = int(pagination.get('total_pages') or 1)
        if page >= total_pages:
            return rows
        page += 1
        if page > 25:
            raise InvoiceNinjaError(
                'Invoice Ninja reports more than 2,500 invoices for this client — '
                'refusing to scan further. Link this one by hand.'
            )


def status_label(status_id):
    """Human label for an IN invoice status_id (shared vocabulary with the
    read-back paths)."""
    if status_id is None:
        return 'Unknown'
    return _IN_STATUS_LABELS.get(int(status_id), f'Unknown ({status_id})')


# Slice 5d — MB-initiated charge against a client's card on file. MB never
# touches a card or money: this triggers IN's own auto-bill action against an
# already-pushed invoice, IN charges the client's stored Square token, and MB
# reads the result back. See memory project_mb_card_payment_security.

def charge_on_file(host):
    """Trigger Invoice Ninja to charge the client's card on file for `host`'s
    already-pushed invoice, via IN's bulk 'auto_bill' action (confirmed against
    IN v5-stable source: POST /invoices/bulk {action:'auto_bill', ids:[...]}
    dispatches IN's AutoBill job against the stored token). `host` is a Sale
    (Lane C recurring charge, Slice 5d) or a WorkOrder (Register charge,
    Slice 6) — both carry invoice_ninja_id directly and have a status-check
    function that reads IN and records the result on the host.

    IMPORTANT — the charge is ASYNCHRONOUS on IN's side. This call only proves
    the charge was successfully QUEUED, never that it was actually paid. Payment
    truth always comes from the status-check read-back, which this function
    calls once immediately after triggering, purely to record whatever status
    IN reports at that moment (often still unpaid — the job may not have run
    yet). Callers must not treat this function's return value as proof of
    payment.

    Refuses (raises InvoiceNinjaError) if the host hasn't been pushed yet or is
    already marked Paid — never re-charges. Fails loud on any IN/transport error.

    Double-charge safety: because a prior charge is async, MB's STORED status
    can lag the real IN state. So we do a FRESH read-back against IN first and
    refuse if it now reports Paid — this closes the window where a charge that
    has since settled would otherwise be fired again. It also means an
    unreachable IN aborts the charge (we never fire blind).
    """
    from .models import Sale, WorkOrder

    if isinstance(host, Sale):
        noun, check_status, stored_label = 'sale', check_sale_status, lambda: host.in_status
    elif isinstance(host, WorkOrder):
        noun, check_status, stored_label = 'work order', check_invoice_status, lambda: host.invoice.in_status
    else:
        raise TypeError(f'charge_on_file() does not support {type(host).__name__}')

    in_id = (host.invoice_ninja_id or '').strip()
    if not in_id:
        raise InvoiceNinjaError(f'This {noun} has not been pushed to Invoice Ninja yet — send it as a draft first.')

    # Fresh status from IN (updates the host's stored status) — not the
    # possibly-stale stored value. Fails loud if IN is unreachable, so no
    # charge fires blind.
    current = check_status(host)
    if current.strip().lower() == 'paid':
        raise InvoiceNinjaError(f'This {noun} is already marked Paid in Invoice Ninja — refusing to charge again.')

    try:
        _request('POST', '/invoices/bulk', json={'action': 'auto_bill', 'ids': [in_id]})
    except InvoiceNinjaOutcomeUnknown as e:
        raise InvoiceNinjaOutcomeUnknown(
            f'{e} The charge may have started. Use "Check IN" in a minute to see '
            'whether it went through before charging again.'
        )

    # Best-effort immediate read-back — the async job may not have run yet, so
    # this often still reports the pre-charge status. That's expected; the
    # operator uses "Check IN" again shortly after to see the real outcome.
    try:
        return check_status(host)
    except InvoiceNinjaError:
        # The trigger itself succeeded (we didn't raise above); a read-back
        # failure right after shouldn't be reported as a charge failure.
        return stored_label() or 'Unknown'


def charge_sale_on_file(sale):
    """Back-compat alias — the Sale-only entry point kept so any external
    reference (or a stale import) doesn't break. New code should call
    charge_on_file() directly."""
    return charge_on_file(sale)


# ---------------------------------------------------------------------------
# Light POS — host-agnostic settlement primitives (WorkOrder or Sale). Neither
# push_sale() nor push_work_order() above are touched by this — they remain the
# tested, working paths for their existing callers. These two functions are
# new, additive building blocks the POS uses to settle a WorkOrder (which had
# no paid-push before) without duplicating push_sale's Sale-specific field
# writes. Both take already-resolved values and return raw results; the caller
# (the POS view) is responsible for writing fields onto its own host, since a
# WorkOrder and a Sale disagree on where payment fields live (Sale stores them
# on itself; WorkOrder stores them on its related Invoice row).
# ---------------------------------------------------------------------------

def push_host_invoice(host, *, po_number, client=None, is_walkin=False):
    """Create a DRAFT invoice in IN from any host exposing a `line_items`
    GenericRelation (WorkOrder or Sale). Stores IN's id and number on the host
    (the create-once guard needs them there) and returns
    (invoice_id, invoice_ref, in_client_id).

    Raises InvoiceNinjaError if there are no priced lines or on any API failure.
    """
    line_items = _line_items_payload(host)
    if not line_items:
        raise InvoiceNinjaError(
            'This has no priced line items to send. Add a price to at least one line first.'
        )
    in_client_id = find_or_create_walkin_client() if is_walkin else find_or_create_client(client)
    invoice = _create_invoice(host, {
        'client_id': in_client_id,
        'po_number': po_number,
        'line_items': line_items,
    })
    return str(invoice['id']), str(invoice.get('number') or ''), in_client_id


def post_payment(invoice_id, in_client_id, *, amount, method_label, reference=''):
    """Post a payment against an existing IN invoice, marking it Paid.

    A thin, host-agnostic version of the payment-posting half of push_sale() —
    used by the POS to settle a WorkOrder (or a Sale reached through the POS)
    against an invoice that may have just been created or may already exist.
    """
    from django.utils import timezone
    payload = {
        'client_id': in_client_id,
        'amount': float(amount),
        'invoices': [{'invoice_id': invoice_id, 'amount': float(amount)}],
        'transaction_reference': reference or '',
        'private_notes': method_label or '',
        'date': timezone.localdate().isoformat(),
    }
    _create_payment(payload, key=_payment_key(invoice_id))
