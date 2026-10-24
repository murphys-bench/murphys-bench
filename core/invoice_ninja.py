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
# (connect, read) seconds. Connecting is fast or hopeless, so a down IN fails
# in 5 seconds. The read wait stays at 20: a shorter one only turns slow but
# successful creates into lost answers.
TIMEOUT = (5, 20)

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
    """True only when a requests ConnectionError proves nothing was sent: the
    connection itself could not be opened (refused, no route, bad name).
    Everything else, TLS errors included, may have happened after the request
    went out, so it counts as "maybe done"."""
    if isinstance(exc, requests.exceptions.SSLError):
        return False
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
    """Turn an IN error response into the exception the user should see. The
    raw body rides along as `.body` so callers can look for evidence in it."""
    code = resp.status_code
    msg = _in_message(resp)
    if code in (401, 403):
        # IN answers a wrong, deleted or archived token with 403 "Invalid token"
        # (tested Oct 2 2026), not 401.
        err = InvoiceNinjaError(
            f'Invoice Ninja refused the API token ({code}{": " + msg if msg else ""}). '
            'Check the token in Settings: it may be mistyped, deleted or archived in '
            'Invoice Ninja, or its user may not have permission.'
        )
    elif code == 429 and 'duplicate' in msg.lower():
        # IN's one-second guard: an identical request arrived just before this
        # one. That earlier request may have done the work.
        err = InvoiceNinjaOutcomeUnknown('Invoice Ninja is still handling the same request sent a moment ago.')
    elif code == 429:
        # IN sends no "try again in" time with this (tested Oct 2 2026); a
        # minute cleared it.
        err = InvoiceNinjaError(
            'Invoice Ninja is limiting requests right now (too many in a short time). '
            'Wait a minute and try again.'
        )
    elif code >= 500:
        # A server or gateway error says nothing about whether the work was
        # done before it failed (RFC 9110 15.6), so it is never "nothing happened".
        if not msg:
            logger.warning('Invoice Ninja error page %s: %s', code, resp.text[:300])
        err = InvoiceNinjaOutcomeUnknown(
            f'Invoice Ninja returned {code}: {msg}' if msg else
            f'Invoice Ninja returned an error page ({code}). It may be down, restarting '
            'or overloaded; try again shortly.'
        )
    else:
        err = InvoiceNinjaError(f'Invoice Ninja returned {code}: {msg or "error"}')
    err.body = resp.text[:4000] if isinstance(resp.text, str) else ''
    return err


def _request(method, path, *, params=None, json=None):
    """Make an IN API call. Raises InvoiceNinjaError on any transport/HTTP error,
    and its subclass InvoiceNinjaOutcomeUnknown whenever the request may have
    been acted on without MB getting a usable answer. Only a connection that
    never opened, or a 4xx refusal, counts as "nothing happened"."""
    base, token = _config()
    url = f'{base}{API_PREFIX}{path}'
    try:
        resp = requests.request(
            method, url, headers=_headers(token), params=params, json=json, timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            raise _http_error(resp)
        return resp.json()
    except InvoiceNinjaError:
        raise
    except ValueError:
        # A success code with a body that is not JSON: the request was accepted,
        # but there is nothing to read the result from.
        raise InvoiceNinjaOutcomeUnknown(
            'Invoice Ninja answered with a web page instead of API data. Check the URL '
            'in Settings: it should be the API address (Cloud: https://invoicing.co).'
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
        if isinstance(e, requests.exceptions.SSLError):
            raise InvoiceNinjaOutcomeUnknown('A secure-connection (TLS) error interrupted Invoice Ninja.')
        raise InvoiceNinjaOutcomeUnknown('The connection to Invoice Ninja dropped before it answered.')
    except requests.Timeout as e:
        logger.warning('Invoice Ninja request failed: %s %s — %s', method, path, e)
        raise InvoiceNinjaOutcomeUnknown(f'Invoice Ninja did not answer within {TIMEOUT[1]} seconds.')
    except requests.RequestException as e:
        # A broken or cut-off answer (ChunkedEncodingError and friends): the
        # request went out, the reply did not arrive whole.
        logger.warning('Invoice Ninja request failed: %s %s — %s', method, path, e)
        raise InvoiceNinjaOutcomeUnknown('Invoice Ninja\'s answer was cut off.')


def test_connection():
    """Cheap auth/URL check used by the Settings 'Test Connection' button.
    Returns a short success string or raises InvoiceNinjaError."""
    _request('GET', '/clients', params={'per_page': 1})
    return 'Connected to Invoice Ninja successfully.'


# ---------------------------------------------------------------------------
# Holds. When MB cannot tell what IN did with a money action, it holds the
# record until a person has looked (see models.InvoiceNinjaHold). Pay and
# charge refuse while held; resolve_hold records who cleared it and when.
# ---------------------------------------------------------------------------

def set_hold(host, kind, reason):
    from django.utils import timezone
    host.in_hold_kind = kind
    host.in_hold_reason = reason
    host.in_hold_at = timezone.now()
    host.save(update_fields=['in_hold_kind', 'in_hold_reason', 'in_hold_at'])


def _release_hold(host, kind):
    """Drop a hold MB itself placed, without recording a person's decision."""
    type(host).objects.filter(pk=host.pk, in_hold_kind=kind).update(
        in_hold_kind='', in_hold_reason='', in_hold_at=None,
    )
    if host.in_hold_kind == kind:
        host.in_hold_kind, host.in_hold_reason, host.in_hold_at = '', '', None


def _record_resolution(host, user, label, note, *, clear_hold):
    from django.utils import timezone
    host.in_hold_resolved_by = user
    host.in_hold_resolved_at = timezone.now()
    host.in_hold_resolved_note = f'{label}: {note}'
    fields = ['in_hold_resolved_by', 'in_hold_resolved_at', 'in_hold_resolved_note']
    if clear_hold:
        host.in_hold_kind, host.in_hold_reason, host.in_hold_at = '', '', None
        fields += ['in_hold_kind', 'in_hold_reason', 'in_hold_at']
    host.save(update_fields=fields)
    logger.info('Invoice Ninja %s on %s %s by %s: %s',
                label, type(host).__name__, host.pk, user or 'Check IN', note)


def resolve_hold(host, user, note):
    """A person (or, with user=None, a Check IN that read Paid) has looked and
    clears the hold. What was cleared, by whom and when, stays on the record."""
    if not host.in_hold_kind:
        return
    _record_resolution(host, user, f'Hold cleared ({host.get_in_hold_kind_display()})', note, clear_hold=True)


def require_no_hold(host):
    host.refresh_from_db(fields=['in_hold_kind', 'in_hold_reason'])
    if host.in_hold_kind:
        raise InvoiceNinjaError(f'{host.in_hold_reason} MB will not take a payment or charge until this is cleared.')


# ---------------------------------------------------------------------------
# Creating things in IN at most once. A lost answer (tested Oct 2 2026) leaves
# MB not knowing whether IN created the record, and a plain retry then makes a
# second one. Every create below claims the record first, sends with MB's own
# key, and when the answer is lost, looks for the record before anyone can
# send again.
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


def _store_pushed_invoice(host, invoice, extra=None):
    """Record IN's invoice on the host and release its pending send, in one
    write, so the host is never left both pushed and pending. `extra` fields
    (a sale's payment-pending flag, say) are saved in the same write."""
    host.invoice_ninja_id = str(invoice['id'])
    host.invoice_ninja_ref = str(invoice.get('number') or '')
    host.in_push_key = ''
    host.in_push_started_at = None
    fields = ['invoice_ninja_id', 'invoice_ninja_ref', 'in_push_key', 'in_push_started_at']
    for name, value in (extra or {}).items():
        setattr(host, name, value)
        fields.append(name)
    host.save(update_fields=fields)


def _release_push(host):
    type(host).objects.filter(pk=host.pk).update(in_push_key='', in_push_started_at=None)
    host.in_push_key = ''
    host.in_push_started_at = None


def _refuse_stale(host):
    """A claim lost because the record changed under this request: say what
    happened rather than "try again"."""
    current = type(host).objects.filter(pk=host.pk).values_list('invoice_ninja_id', 'invoice_ninja_ref').first()
    if current and current[0] and current[0] != (host.invoice_ninja_id or ''):
        raise InvoiceNinjaError(
            f'This was just sent to Invoice Ninja as invoice #{current[1] or current[0]}. '
            'Reload the page before doing anything else with it.'
        )
    raise InvoiceNinjaError(_IN_PROGRESS_MSG)


def _claim_push(host):
    """Mark `host` as being sent and return the reference key to send with.

    The claim is one conditional UPDATE that also requires the stored IN
    invoice id to be the one this request loaded, so a second click that
    loaded the page before the first send finished loses, and so does a
    re-send racing another re-send (SQLite serializes the writes). Returns
    (key, existing_invoice): when an earlier send whose answer was lost turns
    out to have landed, existing_invoice is that invoice and nothing is sent."""
    from django.utils import timezone
    model = type(host)
    now = timezone.now()
    seen_id = host.invoice_ninja_id or ''
    host.refresh_from_db(fields=['in_push_key', 'in_push_started_at'])
    key = host.in_push_key
    if not key:
        key = f'mb-{model._meta.model_name}-{host.pk}-{secrets.token_hex(4)}'
        if not model.objects.filter(pk=host.pk, in_push_key='', invoice_ninja_id=seen_id).update(
                in_push_key=key, in_push_started_at=now):
            _refuse_stale(host)
    else:
        found = _find_invoice_by_key(key)
        if found:
            return key, found
        started = host.in_push_started_at
        if started and now - started < IN_FLIGHT:
            raise InvoiceNinjaError(_IN_PROGRESS_MSG)
        # The earlier send never landed. Take it over, keeping its key so a late
        # arrival would still be recognized.
        if not model.objects.filter(pk=host.pk, in_push_key=key, in_push_started_at=started,
                                    invoice_ninja_id=seen_id).update(in_push_started_at=now):
            _refuse_stale(host)
    host.in_push_key = key
    host.in_push_started_at = now
    return key, None


def _payload_total(payload):
    from decimal import Decimal
    return sum((Decimal(str(li.get('cost') or 0)) * Decimal(str(li.get('quantity') or 0))
                for li in payload.get('line_items') or []), Decimal('0')).quantize(Decimal('0.01'))


def _check_recovered_total(host, invoice, payload):
    """An invoice found after a lost answer was built from the lines as they
    were then. If they have changed since, MB keeps the link but holds the
    record: nobody should pay or charge until a person has compared them."""
    from decimal import Decimal, InvalidOperation
    from .models import InvoiceNinjaHold
    try:
        in_total = Decimal(str(invoice.get('amount'))).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        in_total = None
    mb_total = _payload_total(payload)
    if in_total is not None and in_total == mb_total:
        return
    ref = invoice.get('number') or invoice.get('id')
    reason = (
        f'Invoice #{ref} was created in Invoice Ninja before its answer was lost, for '
        f'{"$" + str(in_total) if in_total is not None else "an amount MB could not read"}. '
        f'MB\'s lines now total ${mb_total}. Make them match in Invoice Ninja or MB, '
        'then clear this hold.'
    )
    set_hold(host, InvoiceNinjaHold.HOLD_AMOUNT, reason)
    raise InvoiceNinjaError(reason)


def _create_invoice(host, payload, *, resend=False, extra=None):
    """POST `payload` as a new IN invoice for `host` (a WorkOrder or Sale) at
    most once, store IN's id and number on the host (with `extra` fields in
    the same write), and return (invoice, recovered). `recovered` is True when
    the invoice came from an earlier send whose answer was lost.

    IN ignores idempotency keys on invoices, so MB writes its own key into the
    invoice (MB_KEY_FIELD) and keeps it on the host until IN's answer is in.
    `resend` must be True to create another invoice for a host that already
    has one; otherwise that is refused.
    """
    if host.invoice_ninja_id and not resend:
        raise InvoiceNinjaError(
            f'This is already in Invoice Ninja as invoice #{host.invoice_ninja_ref or host.invoice_ninja_id}.'
        )
    key, existing = _claim_push(host)
    if existing:
        _store_pushed_invoice(host, existing, extra)
        _check_recovered_total(host, existing, payload)
        return existing, True
    try:
        data = _request('POST', '/invoices', json={**payload, MB_KEY_FIELD: key})
        invoice = data['data']
        str(invoice['id'])
    except InvoiceNinjaOutcomeUnknown as e:
        return _recover_invoice(host, key, payload, extra, str(e))
    except InvoiceNinjaError:
        # IN refused it, or was never reached: nothing was created.
        _release_push(host)
        raise
    except (KeyError, TypeError):
        # Accepted, but the answer does not say what was created.
        return _recover_invoice(host, key, payload, extra, 'Invoice Ninja\'s answer did not include the invoice.')
    _store_pushed_invoice(host, invoice, extra)
    return invoice, False


def _recover_invoice(host, key, payload, extra, why):
    try:
        found = _find_invoice_by_key(key)
    except InvoiceNinjaError:
        found = None
    if found:
        _store_pushed_invoice(host, found, extra)
        _check_recovered_total(host, found, payload)
        return found, True
    # Keep the key: the next attempt looks for this invoice before sending.
    raise InvoiceNinjaOutcomeUnknown(
        f'{why} It may or may not have created the invoice. MB will look for it '
        'before sending again, so it is safe to try again in a minute.'
    )


def clear_pending_send(host, user):
    """A person has checked IN for an invoice whose answer was lost. If MB can
    now find it, it is linked; otherwise the pending send is cleared so a new
    one can go. Returns the linked invoice, or None."""
    from django.utils import timezone
    host.refresh_from_db(fields=['in_push_key', 'in_push_started_at'])
    if not host.in_push_key:
        return None
    if host.in_push_started_at and timezone.now() - host.in_push_started_at < IN_FLIGHT:
        raise InvoiceNinjaError(_IN_PROGRESS_MSG)
    found = None
    try:
        found = _find_invoice_by_key(host.in_push_key)
    except InvoiceNinjaError as e:
        logger.warning('Pending-send lookup failed for %s %s: %s', type(host).__name__, host.pk, e)
    if found:
        _store_pushed_invoice(host, found)
        return found
    _release_push(host)
    _record_resolution(host, user, 'Pending send cleared',
                       'checked Invoice Ninja, the invoice from the lost send is not there', clear_hold=False)
    return None


class InvoiceNinjaAlreadyPaid(InvoiceNinjaError):
    """IN refused MB's payment because the invoice is already paid, and nothing
    shows it was MB's own payment. MB must not record its payment details."""


def _create_payment(payload, *, key):
    """POST a payment once and return only when MB's own payment is recorded.

    IN honours idempotency_key on payments (tested Oct 2 2026), and `key` is
    derived from the invoice. After a lost answer the same payment is sent
    once more with the same key: IN either records it (the first never
    landed) or refuses the key as used (the first did land, so it is ours).
    A refusal because the invoice is already paid, without that key evidence,
    means someone else paid it: InvoiceNinjaAlreadyPaid.
    """
    try:
        _request('POST', '/payments', json={**payload, 'idempotency_key': key})
        return
    except InvoiceNinjaOutcomeUnknown as first:
        try:
            _request('POST', '/payments', json={**payload, 'idempotency_key': key})
            return
        except InvoiceNinjaOutcomeUnknown:
            raise InvoiceNinjaOutcomeUnknown(
                f'{first} The payment may or may not have been recorded. Trying again is '
                'safe: Invoice Ninja refuses the same payment twice.'
            )
        except InvoiceNinjaError as e:
            _classify_payment_refusal(e, payload, key)
    except InvoiceNinjaError as e:
        _classify_payment_refusal(e, payload, key)


def _classify_payment_refusal(e, payload, key):
    body = (getattr(e, 'body', '') or '').lower()
    if 'idempotency' in body:
        # IN has seen this exact key: MB's payment is the one recorded.
        logger.info('Invoice Ninja payment %s already recorded (%s)', key, e)
        return
    invoice_id = payload['invoices'][0]['invoice_id']
    paid = 'have been paid' in body
    if not paid:
        try:
            paid = _invoice_label(invoice_id) == 'Paid'
        except InvoiceNinjaError:
            paid = False
    if paid:
        raise InvoiceNinjaAlreadyPaid(
            'Invoice Ninja already shows this invoice as Paid, and not by this payment. '
            'MB did not post a payment there. Check Invoice Ninja for how it was paid.'
        )
    raise e


def _invoice_label(invoice_id):
    data = _request('GET', f'/invoices/{invoice_id}')
    return status_label((data.get('data') or {}).get('status_id'))


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


def _client_email(client):
    """The one email that identifies this client to IN: sent on create AND used
    to find it again, so a lost answer can always be looked up by it."""
    primary = client.contacts.filter(is_primary=True).first() or client.contacts.first()
    return (primary.email if primary else '') or client.email or ''


def _client_payload(client):
    """Build IN's client create payload. NOTE: IN replaces the whole contacts
    array on POST/PUT (it does not merge) — we only ever CREATE, so sending the
    single primary contact is correct. Do not 'improve' this into a merge."""
    primary = client.contacts.filter(is_primary=True).first() or client.contacts.first()
    email = _client_email(client)
    contact = {}
    if primary:
        contact = {
            'first_name': primary.first_name,
            'last_name': primary.last_name,
            'email': email,
            'phone': primary.phone if hasattr(primary, 'phone') else '',
            'send_email': getattr(primary, 'receives_email', True),
        }
    elif email:
        # No contact on file, only the client's own address: send it as the
        # contact, or MB could never find this client by email again.
        contact = {'email': email, 'send_email': True}
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


def _find_client_by_email(email):
    rows = _request('GET', '/clients', params={'email': email}).get('data') or []
    return str(rows[0]['id']) if rows else None


def find_or_create_client(client):
    """Return the IN client id for an MB Client, creating it if needed.

    Link once, don't sync: if we already stored an IN id, use it. Else match by
    the client's email (the same one a create sends); else create. Saves the id
    back on the MB Client.

    A create whose answer is lost is found again by that email. A client with
    no email has nothing that identifies it, so MB does not guess (a same-name
    match can be another company): it holds the client until a person checks.
    """
    from django.utils import timezone
    from .models import Client
    if client.invoice_ninja_id:
        return client.invoice_ninja_id
    client.refresh_from_db(fields=['in_hold_kind', 'in_hold_reason', 'in_create_started_at'])
    if client.in_hold_kind:
        raise InvoiceNinjaError(client.in_hold_reason)

    email = _client_email(client)
    if email:
        in_id = _find_client_by_email(email)
        if in_id:
            _store_client_link(client, in_id)
            return in_id

    now = timezone.now()
    started = client.in_create_started_at
    if started and now - started < IN_FLIGHT:
        raise InvoiceNinjaError(_IN_PROGRESS_MSG)
    # Claim the create, so two requests cannot both send it.
    if not Client.objects.filter(pk=client.pk, invoice_ninja_id='', in_create_started_at=started).update(
            in_create_started_at=now):
        raise InvoiceNinjaError(_IN_PROGRESS_MSG)
    client.in_create_started_at = now

    payload = _client_payload(client)
    try:
        data = _request('POST', '/clients', json=payload)
        in_id = str(data['data']['id'])
    except InvoiceNinjaOutcomeUnknown as e:
        return _recover_client(client, email, payload['name'], str(e))
    except InvoiceNinjaError:
        Client.objects.filter(pk=client.pk).update(in_create_started_at=None)
        client.in_create_started_at = None
        raise
    except (KeyError, TypeError):
        return _recover_client(client, email, payload['name'], 'Invoice Ninja\'s answer did not include the client.')
    _store_client_link(client, in_id)
    return in_id


def _store_client_link(client, in_id):
    client.invoice_ninja_id = in_id
    client.in_create_started_at = None
    client.save(update_fields=['invoice_ninja_id', 'in_create_started_at'])


def _recover_client(client, email, name, why):
    from .models import InvoiceNinjaHold
    if email:
        try:
            in_id = _find_client_by_email(email)
        except InvoiceNinjaError:
            in_id = None
        if in_id:
            _store_client_link(client, in_id)
            return in_id
        raise InvoiceNinjaOutcomeUnknown(
            f'{why} It may or may not have created the client. MB looks for the '
            'client by email first, so it is safe to try again in a minute.'
        )
    reason = (
        f'Invoice Ninja may have created the client "{name}" (no answer came back), and '
        'this client has no email for MB to find it by. Check the Invoice Ninja client '
        'list. If it is not there, clear this hold. If it is, add the same email to '
        'this client in MB and to that client in Invoice Ninja, then clear this hold: '
        'MB will find it by email.'
    )
    set_hold(client, InvoiceNinjaHold.HOLD_CLIENT, reason)
    raise InvoiceNinjaOutcomeUnknown(f'{why} {reason}')


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

    _paid_clears_charge_hold(work_order, label)
    return label


def _paid_clears_charge_hold(host, label):
    """Paid is the one read-back that settles an unconfirmed charge: there is
    nothing left to charge. Unpaid proves nothing (the job may still be queued)."""
    from .models import InvoiceNinjaHold
    host.refresh_from_db(fields=['in_hold_kind'])
    if label == 'Paid' and host.in_hold_kind == InvoiceNinjaHold.HOLD_CHARGE:
        resolve_hold(host, None, 'Check IN read Paid')


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
    _create_invoice(work_order, payload, resend=bool(work_order.invoice_ninja_id))
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


def push_sale(sale, draft=False, resend=False):
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

    ``resend=True`` is the deliberate, confirmed "send another invoice" for a
    sale that already has one.

    A sale with a client bills under that client; an anonymous walk-in bills under
    the standing 'Walk-In' client. Fail loud on any problem. A paid send whose
    invoice landed but whose payment did not keeps ``in_payment_pending`` and the
    next attempt adds the payment to that same invoice. If IN already shows the
    invoice Paid by something else, raises InvoiceNinjaAlreadyPaid after marking
    the sale's IN status Paid.
    """
    from django.utils import timezone

    line_items = _line_items_payload(sale)
    if not line_items:
        raise InvoiceNinjaError(
            'This sale has no priced line items to send. Add a price to at least '
            'one line first.'
        )
    if not draft:
        require_no_hold(sale)

    if sale.client_id:
        in_client_id = find_or_create_client(sale.client)
    else:
        in_client_id = find_or_create_walkin_client()

    resume = not draft and not resend and bool(sale.invoice_ninja_id) and sale.in_payment_pending
    if not resume:
        if draft:
            extra = {'in_status': 'Draft', 'in_status_checked_at': timezone.now(), 'in_payment_pending': False}
        else:
            # The old status belongs to the old invoice; until this payment is
            # confirmed the sale is pending, not Paid.
            extra = {'in_status': '', 'in_payment_pending': True}
        _create_invoice(sale, {
            'client_id': in_client_id,
            'po_number': sale.sale_number,  # SALE# for bank→IN→MB traceability
            'line_items': line_items,
        }, resend=resend, extra=extra)
    invoice_id = sale.invoice_ninja_id

    if draft:
        # No payment posted — the invoice stays an unpaid Draft in IN.
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
    except InvoiceNinjaAlreadyPaid as e:
        _mark_sale_paid(sale)
        raise InvoiceNinjaAlreadyPaid(f'Invoice #{sale.invoice_ninja_ref or invoice_id}: {e}')
    except InvoiceNinjaError as e:
        raise type(e)(
            f'Invoice #{sale.invoice_ninja_ref or invoice_id} is in Invoice Ninja, but '
            f'its payment was not confirmed: {e} Retrying adds the payment to that '
            'same invoice.'
        )
    _mark_sale_paid(sale)
    return sale.invoice_ninja_ref or sale.invoice_ninja_id


def _mark_sale_paid(sale):
    from django.utils import timezone
    sale.in_status = 'Paid'
    sale.in_status_checked_at = timezone.now()
    sale.in_payment_pending = False
    sale.save(update_fields=['in_status', 'in_status_checked_at', 'in_payment_pending'])


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
    fields = ['in_status', 'in_status_checked_at']
    if label == 'Paid' and sale.in_payment_pending:
        # Paid settles a pending payment either way: nothing is left to post.
        sale.in_payment_pending = False
        fields.append('in_payment_pending')
    sale.save(update_fields=fields)
    _paid_clears_charge_hold(sale, label)
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

    _claim_charge(host)
    try:
        # Fresh status from IN (updates the host's stored status) — not the
        # possibly-stale stored value. Fails loud if IN is unreachable, so no
        # charge fires blind.
        current = check_status(host)
        if current.strip().lower() == 'paid':
            raise InvoiceNinjaError(f'This {noun} is already marked Paid in Invoice Ninja — refusing to charge again.')
        _request('POST', '/invoices/bulk', json={'action': 'auto_bill', 'ids': [in_id]})
    except InvoiceNinjaOutcomeUnknown as e:
        from django.utils import timezone
        reason = (
            f'A card charge sent {timezone.localtime().strftime("%b %-d at %-I:%M %p")} was not '
            'confirmed by Invoice Ninja, so the card may have been charged. Use "Check IN": '
            'if it shows Paid this clears by itself. If Invoice Ninja shows no charge, clear '
            'this hold to allow charging again.'
        )
        from .models import InvoiceNinjaHold
        set_hold(host, InvoiceNinjaHold.HOLD_CHARGE, reason)
        raise InvoiceNinjaOutcomeUnknown(f'{e} {reason}')
    except Exception:
        _release_hold(host, _CHARGING)
        raise
    _release_hold(host, _CHARGING)

    # Best-effort immediate read-back — the async job may not have run yet, so
    # this often still reports the pre-charge status. That's expected; the
    # operator uses "Check IN" again shortly after to see the real outcome.
    try:
        return check_status(host)
    except InvoiceNinjaError:
        # The trigger itself succeeded (we didn't raise above); a read-back
        # failure right after shouldn't be reported as a charge failure.
        return stored_label() or 'Unknown'


_CHARGING = 'charge'


def _claim_charge(host):
    """Claim the record for one charge before calling IN: a conditional UPDATE,
    so of two charge clicks at once only one reaches IN. A held record (an
    unconfirmed earlier charge, a total that does not match) refuses."""
    from django.utils import timezone
    model = type(host)
    if not model.objects.filter(pk=host.pk, in_hold_kind='').update(
            in_hold_kind=_CHARGING, in_hold_reason='A card charge is being sent to Invoice Ninja right now.',
            in_hold_at=timezone.now()):
        host.refresh_from_db(fields=['in_hold_kind', 'in_hold_reason'])
        raise InvoiceNinjaError(f'{host.in_hold_reason} MB will not charge again until this is cleared.')
    host.in_hold_kind = _CHARGING


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
    (invoice_id, invoice_ref, in_client_id, recovered). `recovered` is True
    when the invoice came from an earlier send whose answer was lost: it was
    built then, so callers must read it back before paying against it.

    Raises InvoiceNinjaError if there are no priced lines or on any API failure.
    """
    line_items = _line_items_payload(host)
    if not line_items:
        raise InvoiceNinjaError(
            'This has no priced line items to send. Add a price to at least one line first.'
        )
    in_client_id = find_or_create_walkin_client() if is_walkin else find_or_create_client(client)
    invoice, recovered = _create_invoice(host, {
        'client_id': in_client_id,
        'po_number': po_number,
        'line_items': line_items,
    })
    return str(invoice['id']), str(invoice.get('number') or ''), in_client_id, recovered


def post_payment(invoice_id, in_client_id, *, amount, method_label, reference=''):
    """Post a payment against an existing IN invoice, marking it Paid.

    A thin, host-agnostic version of the payment-posting half of push_sale() —
    used by the POS to settle a WorkOrder (or a Sale reached through the POS)
    against an invoice that may have just been created or may already exist.
    Raises InvoiceNinjaAlreadyPaid when IN shows the invoice paid by something
    other than this payment.
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
