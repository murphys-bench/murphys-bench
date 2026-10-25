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
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
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

# How long a request with no activity counts as still in progress. A request
# renews its activity right before every call to IN (_touch), and one call
# takes at most TIMEOUT (5 + 20 seconds), so a request that is alive never
# loses its claim. Past this, the owner is gone (a crash; gunicorn kills a
# worker after 30 seconds) and another request may take over, looking first
# with the same key. An owner that lost its claim sends nothing more.
IN_FLIGHT = timedelta(seconds=30)

# A card charge IN accepted runs later, on IN's side. Until Check IN reads Paid
# or this long has passed, another charge or payment on the record is refused.
CHARGE_COOLDOWN = timedelta(minutes=5)


class InvoiceNinjaError(Exception):
    """Any failure talking to Invoice Ninja. Carries a user-readable message."""


class InvoiceNinjaOutcomeUnknown(InvoiceNinjaError):
    """The request may have reached IN, but no answer came back, so whether IN
    acted on it is unknown. Callers that create things must look before they
    send again; never read this as "nothing happened"."""


class InvoiceNinjaRefused(InvoiceNinjaError):
    """MB did not send anything: another request on this record is in progress,
    or it is held until a person has checked. Not an attempt at IN."""


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
    mutation = method != 'GET'
    try:
        # A mutation never follows a redirect: requests would replay it as a GET
        # to wherever it points, and a failure on that second hop would hide
        # that the first one reached IN.
        resp = requests.request(
            method, url, headers=_headers(token), params=params, json=json, timeout=TIMEOUT,
            allow_redirects=not mutation,
        )
        if resp.status_code >= 400:
            raise _http_error(resp)
        if mutation and 300 <= resp.status_code < 400:
            raise InvoiceNinjaOutcomeUnknown(
                f'Invoice Ninja answered with a redirect ({resp.status_code}) to '
                f'{resp.headers.get("Location") or "another address"}. Check the URL in Settings '
                '(it may need https://).'
            )
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
# Request records. Everything MB asks IN to do is an InvoiceNinjaRequest row,
# written before the call. The row is the claim (one active request per record
# and lane, enforced by the database), it carries exactly what was sent, and
# only the request holding its token can move it on. A request MB cannot
# settle by looking is `held` until a person has checked: that is a hold.
# See models.InvoiceNinjaRequest and mb-docs DESIGN-in-request-records.md.
# ---------------------------------------------------------------------------

_IN_PROGRESS_MSG = 'This is already being sent to Invoice Ninja. Wait a few seconds and try again.'


def _req_model():
    from .models import InvoiceNinjaRequest
    return InvoiceNinjaRequest


def _host_field(host):
    from .models import Client, Sale, WorkOrder
    if isinstance(host, WorkOrder):
        return 'work_order'
    if isinstance(host, Sale):
        return 'sale'
    if isinstance(host, Client):
        return 'client'
    raise TypeError(f'{type(host).__name__} cannot hold Invoice Ninja requests')


def requests_for(host):
    return _req_model().objects.filter(**{_host_field(host): host})


def money(value):
    """A dollar amount for a person to read: two places, rounded the way the
    Register's own buttons show it (Django's floatformat). Display only; never
    use it for an amount MB sends or stores. A value that is not a number is
    shown as it is: a message must never fail because of what it displays."""
    from decimal import Decimal, InvalidOperation
    from django.template.defaultfilters import floatformat
    try:
        return floatformat(Decimal(str(value)), 2)
    except (InvalidOperation, ValueError):
        return str(value)


def _when(dt):
    return timezone.localtime(dt).strftime('%b %-d at %-I:%M %p')


def _move(req, state, *, from_states=None, **fields):
    """Move `req` to `state` only if it is still ours (token) and still in one
    of `from_states` (default: its current state). Returns False when another
    request finished it or took it over first."""
    R = _req_model()
    from_states = from_states or (req.state,)
    if state not in R.ACTIVE:
        fields.setdefault('finished_at', timezone.now())
    moved = R.objects.filter(pk=req.pk, token=req.token, state__in=from_states).update(state=state, **fields)
    if moved:
        req.state = state
        for name, value in fields.items():
            setattr(req, name, value)
    return bool(moved)


def _gate(host):
    """Serialize money decisions on one record. Call first inside
    transaction.atomic(): updating the host's own row takes SQLite's write
    lock until the transaction ends, so no hold can be written between this
    transaction's check and its claim or paid write."""
    type(host).objects.filter(pk=host.pk).update(invoice_ninja_id=F('invoice_ninja_id'))


def _touch(req):
    """Renew the request's activity right before a call to IN, and refuse to
    make the call if another request has taken it over meanwhile."""
    R = _req_model()
    now = timezone.now()
    if not R.objects.filter(pk=req.pk, token=req.token, state=R.SENDING).update(active_at=now):
        raise InvoiceNinjaRefused(
            'Another request took this over after a long wait, so MB sent nothing more. '
            'Reload the page before doing anything else.')
    req.active_at = now


def _held_message(req):
    return f'{req.reason} MB will not do this until a person has checked and cleared it.'


def _busy_message(req):
    R = _req_model()
    if req.state == R.HELD:
        return _held_message(req)
    if req.kind == R.KIND_CHARGE and req.state == R.QUEUED:
        return (f'A card charge was started {_when(req.started_at)}. Invoice Ninja runs it on its '
                'side, so use "Check IN" to see whether it posted before doing anything else.')
    if req.kind == R.KIND_CHARGE:
        return 'A card charge is being sent to Invoice Ninja right now.'
    if req.kind == R.KIND_PAYMENT and req.looks_lost:
        p = req.payload
        return (f'A payment of ${money(p.get("amount"))} ({p.get("method_label") or "unspecified"}) sent '
                f'{_when(req.started_at)} got no answer from Invoice Ninja. Pay again: if Invoice Ninja '
                'still shows the invoice unpaid, MB sends that same payment, which Invoice Ninja '
                'refuses twice; if it shows a payment already there, MB holds it for you to check.')
    if req.kind == R.KIND_PAYMENT:
        return 'A payment is being recorded in Invoice Ninja right now. Wait a few seconds and try again.'
    return _IN_PROGRESS_MSG


def _vanished_charge_reason(req):
    return (f'A card charge started {_when(req.started_at)} never finished, so the card may '
            'have been charged. Use "Check IN": if it shows Paid this clears by itself. If '
            'Invoice Ninja shows no charge, clear this hold to allow charging again.')


def _hold_vanished_charges(host):
    """A charge still 'being sent' long after any call could take was cut off
    mid-way (a crash, a restart): the card may have been charged. It becomes
    a hold a person can see and clear, never a request that blocks forever.
    Only the exact activity that was judged stale is held: an owner that
    renews meanwhile keeps its request."""
    R = _req_model()
    for req in requests_for(host).filter(kind=R.KIND_CHARGE, state=R.SENDING,
                                         active_at__lte=timezone.now() - IN_FLIGHT):
        R.objects.filter(pk=req.pk, token=req.token, state=R.SENDING, active_at=req.active_at).update(
            state=R.HELD, reason=_vanished_charge_reason(req))


def _take_over(active, kind, token, now):
    """Decide what a new request does about the active one in its lane, and
    return (request, refusal). (request, None): the active request is now
    ours to carry on, with its own key and payload. (None, None): the lane
    changed; look again. (None, message): refuse. Every change is
    conditional on the exact token, state and activity that were judged, so a
    request that renewed meanwhile is never taken. It does not raise, so a
    hold it records is kept by the caller's transaction."""
    R = _req_model()
    same = R.objects.filter(pk=active.pk, token=active.token, state=active.state, active_at=active.active_at)
    if active.state == R.QUEUED:
        if now - active.active_at < CHARGE_COOLDOWN:
            return None, _busy_message(active)
        same.update(state=R.LAPSED, finished_at=now)
        return None, None
    if active.state == R.HELD:
        return None, _held_message(active)
    if active.kind != kind:
        return None, _busy_message(active)
    if active.state == R.SENDING and now - active.active_at < IN_FLIGHT:
        return None, _busy_message(active)
    if kind == R.KIND_CHARGE:
        # A charge is never re-sent blind: its owner vanished mid-call, so the
        # card may have been charged. A person checks.
        if same.update(state=R.HELD, reason=_vanished_charge_reason(active)):
            active.state, active.reason = R.HELD, _vanished_charge_reason(active)
            return None, _held_message(active)
        return None, None
    if not same.update(token=token, state=R.SENDING, active_at=now, generation=F('generation') + 1):
        return None, None
    active.refresh_from_db()
    return active, None


def _already_accepted_message(done):
    return (f'Invoice Ninja already accepted MB\'s payment of ${money(done.payload.get("amount"))} on this '
            f'invoice ({_when(done.started_at)}). MB will not send another payment onto it. Use "Check IN" '
            'to see what Invoice Ninja shows.')


def _claim(host, kind, payload, *, base_invoice_id='', user=None):
    """Claim the record's lane for one request and return (request,
    carried_on). `carried_on` is True when an earlier request whose answer was
    lost is being carried on: its key and payload are the ones to use, and it
    must look before anything is sent. A money request is refused while
    anything on the record is held, and a new payment is refused onto an
    invoice that already has MB's accepted one (its "Paid" may just not show
    yet); those checks and the claim are one transaction behind _gate."""
    R = _req_model()
    field = _host_field(host)
    lane = R.LANES[kind]
    token = secrets.token_hex(16)
    for _ in range(4):
        refusal = None
        try:
            with transaction.atomic():
                _gate(host)
                now = timezone.now()
                if lane == 'money':
                    held = requests_for(host).filter(state=R.HELD).exclude(lane='money').first()
                    if held is not None:
                        refusal = _held_message(held)
                if refusal is None:
                    active = requests_for(host).filter(lane=lane, state__in=R.ACTIVE).first()
                    if active is None and kind == R.KIND_PAYMENT and base_invoice_id:
                        # Checked after `active` and in the same transaction, so a
                        # payment finishing meanwhile is seen one way or the other.
                        done = requests_for(host).filter(
                            kind=kind, state=R.DONE, base_invoice_id=base_invoice_id).first()
                        if done is not None:
                            raise InvoiceNinjaRefused(_already_accepted_message(done))
                    if active is None:
                        req = R.objects.create(
                            **{field: host}, kind=kind, lane=lane, state=R.SENDING,
                            key=f'mb-{kind[:3]}-{type(host)._meta.model_name[:4]}-{host.pk}-{secrets.token_hex(6)}',
                            token=token, payload=payload, base_invoice_id=base_invoice_id or '',
                            started_by=user, started_at=now, active_at=now,
                        )
                        return req, False
                    taken, refusal = _take_over(active, kind, token, now)
                    if taken is not None:
                        return taken, True
        except IntegrityError:
            continue  # another request claimed the lane first; look again
        if refusal is not None:
            raise InvoiceNinjaRefused(refusal)
    raise InvoiceNinjaRefused(_IN_PROGRESS_MSG)


def blocking_request(host, *, finishing=None):
    """The request that stops MB marking `host` paid or charging it: anything
    held, or a money request still in progress (a started charge counts until
    its cooldown passes). `finishing` names a kind whose own unanswered request
    the caller is about to carry on (a payment retry), so it does not block."""
    R = _req_model()
    _hold_vanished_charges(host)
    now = timezone.now()
    for req in requests_for(host).filter(state__in=R.ACTIVE).order_by('started_at'):
        if req.state == R.HELD:
            return req
        if req.lane != 'money':
            continue
        if req.state == R.QUEUED and now - req.active_at >= CHARGE_COOLDOWN:
            continue
        if req.kind == finishing and req.looks_lost:
            continue
        return req
    return None


def require_no_hold(host, *, finishing=None):
    req = blocking_request(host, finishing=finishing)
    if req is not None:
        raise InvoiceNinjaRefused(
            f'{_busy_message(req)} MB will not take a payment or charge until this is settled.')


def guarded(host, write, *, finishing=None):
    """Run `write()`, a local write that marks `host` paid or settled, only if
    nothing blocks it. The check and the write are one transaction behind
    _gate, so a hold cannot slip in between. Returns the blocking request
    (nothing was written) or None."""
    with transaction.atomic():
        _gate(host)
        blocker = blocking_request(host, finishing=finishing)
        if blocker is None:
            write()
        return blocker


def attention_requests(host):
    """The requests a person should see on the record's page: held, unanswered,
    or a charge still running."""
    R = _req_model()
    _hold_vanished_charges(host)
    return list(requests_for(host).filter(state__in=(R.HELD, R.UNKNOWN, R.QUEUED, R.SENDING)).order_by('started_at'))


def resolve_request(req, user, note, *, generation=None):
    """A person has checked IN and clears a held request, or an unanswered
    payment or client create they found is not there. It applies only to the
    request as it was judged (token, state, activity) and, from a page, to
    the generation the person was shown: a request taken over or resumed
    meanwhile is left alone. Recorded on that request: who, when, what."""
    R = _req_model()
    now = timezone.now()
    lost = req.kind in (R.KIND_PAYMENT, R.KIND_CLIENT) and req.looks_lost
    if not (req.state == R.HELD or lost):
        return False
    same = R.objects.filter(pk=req.pk, token=req.token, state=req.state, active_at=req.active_at)
    if generation is not None:
        same = same.filter(generation=generation)
    if req.state == R.SENDING:
        same = same.filter(active_at__lte=now - IN_FLIGHT)
    moved = same.update(state=R.CLEARED, finished_at=now, resolved_by=user, resolved_at=now, resolved_note=note)
    if moved:
        logger.info('Invoice Ninja %s request %s on %s cleared by %s: %s',
                    req.kind, req.pk, req.host, user, note)
    return bool(moved)


def _settle_on_status(host, label, invoice_id):
    """What a read of invoice `invoice_id` settles on this record.

    Paid settles a started or unconfirmed charge ON THAT INVOICE: there is
    nothing left to charge (Mike, Oct 23 2026: an unconfirmed charge clears on
    Check IN Paid or by a person). A request on another invoice (one this
    record was re-sent from) is not touched.

    Paid or Partial does not show WHOSE payment is there, so neither settles a
    payment: an unanswered payment on that invoice becomes held, and a held
    one stays held, until a person has checked and said whether it is there
    (decision B; Partial since review R2-2). Sending it again could add a
    second payment when the one there is somebody else's. Unpaid proves
    nothing (the charge may still be queued)."""
    if label not in ('Paid', 'Partial') or not invoice_id:
        return
    R = _req_model()
    now = timezone.now()
    mine = requests_for(host).filter(base_invoice_id=invoice_id)
    if label == 'Paid':
        mine.filter(kind=R.KIND_CHARGE, state__in=(R.QUEUED, R.HELD, R.UNKNOWN)).update(
            state=R.DONE, finished_at=now, resolved_at=now, resolved_note='Check IN read Paid')
    shows = 'Paid' if label == 'Paid' else 'partly paid'
    for req in mine.filter(kind=R.KIND_PAYMENT, state__in=(R.UNKNOWN, R.SENDING)):
        if not req.looks_lost:
            continue
        p = req.payload
        R.objects.filter(pk=req.pk, token=req.token, state=req.state, active_at=req.active_at).update(
            state=R.HELD, reason=(
                f'Invoice Ninja shows this invoice {shows}, and a payment of ${money(p.get("amount"))} '
                f'({p.get("method_label") or "unspecified"}) sent {_when(req.started_at)} got no '
                'answer, so MB cannot tell whether that payment is the one there. MB will not send '
                'it again. Check Invoice Ninja, then say whether it is there.'))


def found_payment(req, user, *, generation=None):
    """A person has checked IN and found that a held payment of MB's IS there:
    it is the one recorded, so it is done, with the details it was sent with.
    Nothing is sent. Applies only to the request as it was shown (token,
    state, activity, generation), like resolve_request."""
    R = _req_model()
    if req.kind != R.KIND_PAYMENT or req.state != R.HELD:
        return False
    now = timezone.now()
    same = R.objects.filter(pk=req.pk, token=req.token, state=R.HELD, active_at=req.active_at)
    if generation is not None:
        same = same.filter(generation=generation)
    note = 'checked Invoice Ninja: the payment is there'
    moved = same.update(state=R.DONE, finished_at=now, resolved_by=user, resolved_at=now, resolved_note=note)
    if moved and req.sale_id:
        # MB's payment is at IN: nothing is left to post for this sale.
        from .models import Sale
        Sale.objects.filter(pk=req.sale_id).update(in_payment_pending=False)
    if moved:
        logger.info('Invoice Ninja payment request %s on %s found by %s', req.pk, req.host, user)
    return bool(moved)


def payment_refusal(host, invoice_id, label):
    """Why MB must not send a new payment onto invoice `invoice_id`, which IN
    has just read as `label` (not Paid), or None. MB's own payment already
    accepted there means it is waiting to show Paid, not to be paid again
    (review R2-1); a payment somebody else made is not added to; and a status
    MB cannot read as unpaid is no permission to pay."""
    R = _req_model()
    ref = getattr(host, 'invoice_ninja_ref', '') or invoice_id
    own = (requests_for(host).filter(kind=R.KIND_PAYMENT, state=R.DONE, base_invoice_id=invoice_id)
           .order_by('-finished_at').first())
    if own is not None:
        return (f'Invoice Ninja already has MB\'s payment of ${money(own.payload.get("amount"))} on invoice '
                f'#{ref}, recorded {_when(own.started_at)}, and shows the invoice {label}. MB will not send '
                'another. Settle the rest in Invoice Ninja; then Pay here records it Paid and sends nothing more.')
    if label == 'Partial':
        # A payment is already on it. Another from here would pay the whole
        # total again on top.
        return (f'Invoice Ninja shows invoice #{ref} partly paid. MB will not add a second payment. '
                'Settle the rest in Invoice Ninja; then Pay here records it Paid and sends nothing more.')
    if label not in _PAYABLE_LABELS:
        return (f'Invoice Ninja shows invoice #{ref} as {label}, so MB cannot tell whether anything is owed '
                'on it. Nothing was sent. Check the invoice in Invoice Ninja.')
    return None


def _usable_id(obj):
    """IN's id for what it created, or ValueError when the answer has none MB
    can use (missing, null, empty): that is "no usable answer", never success."""
    value = obj['id']
    text = '' if value is None else str(value).strip()
    if not text or text == 'None':
        raise ValueError('no usable id')
    return text


# ---------------------------------------------------------------------------
# Invoices. IN ignores idempotency keys on invoices (tested Oct 2 2026), so MB
# writes the request's key into the invoice (MB_KEY_FIELD) and, after a lost
# answer, looks for it before anything is sent again. Every way an invoice is
# linked (a normal answer, an automatic recovery, a person's "I checked")
# goes through _finish_invoice.
# ---------------------------------------------------------------------------

def _find_invoice_by_key(key):
    """The IN invoice carrying MB's reference `key`, or None. Deleted invoices
    and rows without a usable id don't count. Each row's field is checked too,
    so an IN that ignored the filter could not hand back somebody else's."""
    data = _request('GET', '/invoices', params={
        MB_KEY_FIELD: key, 'status': 'active,archived', 'per_page': 5,
    })
    for row in (data.get('data') or []):
        if str(row.get(MB_KEY_FIELD) or '') != key or row.get('is_deleted'):
            continue
        try:
            _usable_id(row)
        except (KeyError, ValueError):
            continue
        return row
    return None


def _payload_total(payload):
    """What Invoice Ninja totals these lines to: each line rounded to the cent,
    then added (models.line_amount, Invoice Ninja's own rule)."""
    from decimal import Decimal
    from .models import line_amount
    return sum((line_amount(li.get('quantity') or 0, li.get('cost') or 0)
                for li in payload.get('line_items') or []), Decimal('0.00'))


def _current_in_client(host):
    """The IN client this record bills to now: its client's IN id ('' if that
    client is not linked yet), or the standing Walk-In client."""
    from .models import SiteSettings
    fresh = type(host).objects.select_related('client').get(pk=host.pk)
    if fresh.client_id:
        return fresh.client.invoice_ninja_id or ''
    return (SiteSettings.get().invoice_ninja_walkin_client_id or '').strip()


def _recovered_problem(req, host, invoice):
    """An invoice found after a lost answer must be for the client MB sent it
    for, that client must still be the record's, and its amount must match
    what MB sent and what the record's lines total now. Returns the hold
    reason when any of that fails or cannot be read."""
    from decimal import Decimal, InvalidOperation
    ref = invoice.get('number') or invoice.get('id')
    problems = []
    sent_client = str((req.payload.get('invoice') or {}).get('client_id') or '')
    in_client = str(invoice.get('client_id') or '')
    if not in_client:
        problems.append('Invoice Ninja did not say which client it belongs to')
    elif in_client != sent_client:
        problems.append('it belongs to a different Invoice Ninja client than MB sent it for')
    if _current_in_client(host) != sent_client:
        problems.append('this record now belongs to a different client than the one it was made for')
    try:
        in_total = Decimal(str(invoice.get('amount'))).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        in_total = None
    sent = _payload_total(req.payload.get('invoice') or {})
    now_total = _payload_total({'line_items': _line_items_payload(host)})
    if not (in_total is not None and in_total == sent == now_total):
        shown = f'${in_total}' if in_total is not None else 'an amount MB could not read'
        problems.append(f'it is for {shown}; MB sent ${sent} and this record\'s lines now total ${now_total}')
    if not problems:
        return ''
    return (
        f'Invoice #{ref} was created in Invoice Ninja before its answer was lost, but '
        f'{"; ".join(problems)}. Make Invoice Ninja and MB match, then clear this hold.'
    )


def _finish_invoice(req, host, invoice, *, recovered):
    """Link IN's invoice to the record, once. The link is written only if the
    record still points where it did when this request began, and the request
    only moves on if it is still ours: a late or stale request changes nothing.
    A recovered invoice that does not match is linked and held."""
    R = _req_model()
    in_id = _usable_id(invoice)
    number = str(invoice.get('number') or '')
    hold = _recovered_problem(req, host, invoice) if recovered else ''
    fields = {'invoice_ninja_id': in_id, 'invoice_ninja_ref': number}
    for name, value in (req.payload.get('host_fields') or {}).items():
        fields[name] = timezone.now() if value == '__now__' else value
    with transaction.atomic():
        linked = type(host).objects.filter(pk=host.pk, invoice_ninja_id=req.base_invoice_id).update(**fields)
        if not linked:
            current = type(host).objects.filter(pk=host.pk).values_list('invoice_ninja_ref', 'invoice_ninja_id').first()
            hold = (
                f'Invoice #{number or in_id} was created in Invoice Ninja, but this record was linked '
                f'to invoice #{(current and (current[0] or current[1])) or "?"} meanwhile. Check '
                'Invoice Ninja for an extra invoice, then clear this hold.'
            )
        if not _move(req, R.HELD if hold else R.DONE, in_id=in_id, in_number=number, reason=hold):
            raise InvoiceNinjaRefused(
                'Another request finished this send first. Reload the page before doing anything else.')
    if linked:
        for name, value in fields.items():
            setattr(host, name, value)
    if hold:
        raise InvoiceNinjaError(hold)
    return invoice, recovered


_LOST_CREATE_MSG = (
    'An earlier send got no answer, and MB has not found that {what} in Invoice Ninja yet. '
    'Invoice Ninja may still be creating it, so MB will not send it again on its own. Check '
    'Invoice Ninja: if it is there, try again and MB links it; if it is not, press "I checked '
    'Invoice Ninja: the {what} is not there", then send.'
)


def _recover_invoice(req, host, why):
    _touch(req)
    try:
        found = _find_invoice_by_key(req.key)
    except InvoiceNinjaError:
        found = None
    if found:
        return _finish_invoice(req, host, found, recovered=True)
    # Keep the request: the next attempt looks for this invoice; nothing is
    # sent again on MB's own say-so (decision C, Oct 24 2026).
    _move(req, _req_model().UNKNOWN, error=why)
    raise InvoiceNinjaOutcomeUnknown(
        f'{why} It may or may not have created the invoice. It is safe to try again in a '
        'minute: MB looks for it first and never sends it a second time on its own.'
    )


def _create_invoice(host, payload, *, resend=False, host_fields=None, user=None):
    """Create IN's invoice for `host` (a WorkOrder or Sale) at most once and
    return (invoice, recovered). `recovered` is True when the invoice came from
    an earlier send whose answer was lost. `host_fields` are saved on the host
    in the same write as the link. `resend` must be True to create another
    invoice for a host that already has one."""
    R = _req_model()
    if host.invoice_ninja_id and not resend:
        raise InvoiceNinjaError(
            f'This is already in Invoice Ninja as invoice #{host.invoice_ninja_ref or host.invoice_ninja_id}.'
        )
    base = host.invoice_ninja_id or ''
    req, carried_on = _claim(host, R.KIND_INVOICE, {'invoice': payload, 'host_fields': host_fields or {}},
                             base_invoice_id=base, user=user)
    if not carried_on:
        current = type(host).objects.filter(pk=host.pk).values_list('invoice_ninja_id', 'invoice_ninja_ref').first()
        if current and (current[0] or '') != base:
            _move(req, R.FAILED, error='record changed before sending')
            raise InvoiceNinjaRefused(
                f'This was just sent to Invoice Ninja as invoice #{current[1] or current[0]}. '
                'Reload the page before doing anything else with it.'
            )
    else:
        # An earlier send of this record never got its answer: look for it,
        # with its own key.
        _touch(req)
        try:
            found = _find_invoice_by_key(req.key)
        except InvoiceNinjaError as e:
            _move(req, R.UNKNOWN, error=str(e))
            raise InvoiceNinjaOutcomeUnknown(
                f'An earlier send got no answer, and MB could not check Invoice Ninja for it: {e} '
                'Nothing new was sent. Try again in a minute.'
            )
        if found:
            return _finish_invoice(req, host, found, recovered=True)
        # Not found is not proof it never landed: Invoice Ninja may still be
        # creating it, and it has no way to refuse a second copy. MB does not
        # send it again; a person checks (decision C, Oct 24 2026).
        _move(req, R.UNKNOWN)
        raise InvoiceNinjaOutcomeUnknown(_LOST_CREATE_MSG.format(what='invoice'))
    _touch(req)
    try:
        data = _request('POST', '/invoices', json={**req.payload['invoice'], MB_KEY_FIELD: req.key})
        invoice = data['data']
        _usable_id(invoice)
    except InvoiceNinjaOutcomeUnknown as e:
        return _recover_invoice(req, host, str(e))
    except InvoiceNinjaError as e:
        # IN refused it, or was never reached: nothing was created.
        _move(req, R.FAILED, error=str(e))
        raise
    except (KeyError, TypeError, ValueError):
        # Accepted, but the answer does not say what was created.
        return _recover_invoice(req, host, 'Invoice Ninja\'s answer did not include the invoice.')
    return _finish_invoice(req, host, invoice, recovered=False)


def clear_pending_send(host, user, *, request_id, generation):
    """A person has checked IN for the invoice of ONE lost send: request
    `request_id`, as it was at `generation` when they were shown it. MB looks
    once more: if it finds the invoice, it is linked exactly as an automatic
    recovery would link it (totals and client checked, the sale's payment
    still owed); otherwise that send is cleared so a new one can go,
    recording who said so and whether MB's own lookup worked. Any other
    send, or this one since changed, is left alone. Returns the linked
    invoice, or None."""
    R = _req_model()
    req = requests_for(host).filter(pk=request_id, lane='invoice', state__in=(R.UNKNOWN, R.SENDING)).first()
    if req is None or req.generation != generation:
        raise InvoiceNinjaRefused('That send changed since the page was shown. Reload it and check again.')
    token = secrets.token_hex(16)
    if req.state == R.SENDING and timezone.now() - req.active_at < IN_FLIGHT:
        raise InvoiceNinjaRefused(_IN_PROGRESS_MSG)
    same = R.objects.filter(pk=req.pk, token=req.token, state=req.state, active_at=req.active_at,
                            generation=generation)
    if not same.update(token=token, state=R.SENDING, active_at=timezone.now(), generation=F('generation') + 1):
        raise InvoiceNinjaRefused('That send changed since the page was shown. Reload it and check again.')
    req.refresh_from_db()
    lookup_error = ''
    _touch(req)
    try:
        found = _find_invoice_by_key(req.key)
    except InvoiceNinjaError as e:
        found, lookup_error = None, str(e)
        logger.warning('Pending-send lookup failed for %s %s: %s', type(host).__name__, host.pk, e)
    if found:
        _finish_invoice(req, host, found, recovered=True)
        return found
    note = 'Checked Invoice Ninja: the invoice from the lost send is not there.'
    if lookup_error:
        note += f' MB could not look for it itself ({lookup_error}), so this rests on that check.'
    _move(req, R.CLEARED, resolved_by=user, resolved_at=timezone.now(), resolved_note=note)
    logger.info('Pending send on %s %s cleared by %s: %s', type(host).__name__, host.pk, user, note)
    return None


# ---------------------------------------------------------------------------
# Payments. IN honours idempotency_key on payments (tested Oct 2 2026). Each
# payment request has its own key and keeps the payment it sent; a retry after
# a lost answer resends that same payment, and MB records its details, never
# those of a later form. A refusal counts as "already recorded" only when IN
# says that key was already used.
# ---------------------------------------------------------------------------

class InvoiceNinjaAlreadyPaid(InvoiceNinjaError):
    """IN refused MB's payment because the invoice is already paid, and nothing
    shows it was MB's own payment. MB must not record its payment details."""


class InvoiceNinjaPartlyPaid(InvoiceNinjaError):
    """IN recorded MB's payment, but the invoice still shows money owed (or MB
    could not read it back). MB records the payment it made and what IN says,
    and does not call the record Paid."""


def invoice_state(invoice_id):
    """Read one invoice back from IN: (status label, balance or None). A payment
    being accepted is not proof the invoice is paid; this is."""
    from decimal import Decimal, InvalidOperation
    data = (_request('GET', f'/invoices/{invoice_id}') or {}).get('data') or {}
    status_id = data.get('status_id')
    try:
        label = _IN_STATUS_LABELS.get(int(status_id), f'Unknown ({status_id})')
    except (TypeError, ValueError):
        label = 'Unknown'
    try:
        balance = Decimal(str(data.get('balance')))
    except (InvalidOperation, TypeError, ValueError):
        balance = None
    return label, balance


def owed_message(paid, ref, label, balance, *, then='then use "Check IN"'):
    """The words for a payment IN recorded on an invoice it does not show Paid.
    `then` is how this record is finished once IN shows it paid."""
    owed = f' with ${money(balance)} still owed' if balance is not None and balance > 0 else ''
    return (f'Invoice Ninja recorded the payment of ${money(paid)}, but shows invoice #{ref} '
            f'{label}{owed}. MB does not call it Paid: settle the rest in Invoice Ninja, '
            f'{then}.')


def _key_already_used(e):
    import json
    try:
        body = json.loads(getattr(e, 'body', '') or '')
    except ValueError:
        return False
    errors = body.get('errors') if isinstance(body, dict) else None
    messages = errors.get('idempotency_key') if isinstance(errors, dict) else None
    if isinstance(messages, str):
        messages = [messages]
    return any('already' in str(m).lower() for m in (messages or []))


def _classify_payment_refusal(req, e, *, uncertain):
    """IN refused this request's payment. Only a used key shows the payment is
    the one recorded. When an earlier attempt of this same payment got no
    answer (`uncertain`), nothing else can show that attempt did not land:
    a refusal leaves the payment open, and Paid holds it for a person."""
    R = _req_model()
    if _key_already_used(e):
        # IN has this request's own payment: it is the one recorded.
        logger.info('Invoice Ninja payment %s already recorded (%s)', req.key, e)
        _move(req, R.DONE, resolved_note='Invoice Ninja had already recorded this payment.')
        return req
    body = (getattr(e, 'body', '') or '').lower()
    paid = 'have been paid' in body
    if not paid:
        try:
            _touch(req)
            paid = _invoice_label(req.base_invoice_id) == 'Paid'
        except InvoiceNinjaRefused:
            raise
        except InvoiceNinjaError:
            paid = False
    if uncertain:
        p = req.payload
        if paid:
            reason = (
                f'Invoice Ninja shows this invoice Paid, and an earlier attempt of a payment of '
                f'${money(p.get("amount"))} ({p.get("method_label") or "unspecified"}) got no answer, so MB '
                'cannot tell whether that payment is the one there. Check Invoice Ninja, then say '
                'whether it is there.'
            )
            _move(req, R.HELD, reason=reason, error=str(e))
            raise InvoiceNinjaOutcomeUnknown(_held_message(req))
        _move(req, R.UNKNOWN, error=str(e))
        raise InvoiceNinjaOutcomeUnknown(
            f'{e} An earlier attempt of this payment got no answer, so it stays open: trying '
            'again resends the same payment with the same key, which Invoice Ninja refuses twice.'
        )
    if paid:
        _move(req, R.FAILED, error='Invoice already paid in Invoice Ninja by something else.')
        raise InvoiceNinjaAlreadyPaid(
            'Invoice Ninja already shows this invoice as Paid, and not by this payment. '
            'MB did not post a payment there. Check Invoice Ninja for how it was paid.'
        )
    _move(req, R.FAILED, error=str(e))
    raise e


def _invoice_label(invoice_id):
    data = _request('GET', f'/invoices/{invoice_id}')
    return status_label((data.get('data') or {}).get('status_id'))


def post_payment(host, invoice_id, in_client_id, *, amount, method='', method_label='', reference='',
                 type_id=None, user=None):
    """Record a payment against IN invoice `invoice_id` for `host`, once, and
    return the request. Its payload holds the payment MB sent; record THOSE
    details (`request.carried_on` is True when an earlier unanswered payment
    was finished instead of this form's). Raises InvoiceNinjaAlreadyPaid when
    IN shows the invoice paid by something else."""
    R = _req_model()
    payment = {
        'client_id': in_client_id,
        'amount': float(amount),
        'invoices': [{'invoice_id': invoice_id, 'amount': float(amount)}],
        'transaction_reference': reference or '',
        'private_notes': method_label or '',
        'date': timezone.localdate().isoformat(),
    }
    if type_id is not None:
        payment['type_id'] = type_id
    payload = {'payment': payment, 'amount': str(amount), 'method': method,
               'method_label': method_label, 'reference': reference or ''}
    # Nothing is paid against a held record, whatever lane the hold is in; this
    # record's own unanswered payment is the one exception (it is finished).
    require_no_hold(host, finishing=R.KIND_PAYMENT)
    req, carried_on = _claim(host, R.KIND_PAYMENT, payload, base_invoice_id=invoice_id, user=user)
    req.carried_on = carried_on
    if carried_on and req.base_invoice_id != invoice_id:
        _move(req, R.HELD, reason=(
            f'A payment sent {_when(req.started_at)} to an earlier Invoice Ninja invoice got no answer. '
            'Check that invoice in Invoice Ninja, then say whether the payment is there.'))
        raise InvoiceNinjaRefused(_held_message(req))
    send = {**req.payload['payment'], 'idempotency_key': req.key}
    # A carried-on request already had an attempt with no answer.
    uncertain = carried_on
    try:
        try:
            _touch(req)
            data = _request('POST', '/payments', json=send)
        except InvoiceNinjaOutcomeUnknown:
            # Once more with the same key: IN records it (the first never
            # landed) or refuses the key as used (it did, and it is this one).
            uncertain = True
            _touch(req)
            data = _request('POST', '/payments', json=send)
    except InvoiceNinjaOutcomeUnknown as e:
        _move(req, R.UNKNOWN, error=str(e))
        raise InvoiceNinjaOutcomeUnknown(
            f'{e} The payment may or may not have been recorded. Trying again sends this same '
            f'payment (${money(req.payload["amount"])}, {req.payload["method_label"] or "unspecified"}): '
            'Invoice Ninja refuses the same payment twice.'
        )
    except InvoiceNinjaRefused:
        raise
    except InvoiceNinjaError as e:
        return _classify_payment_refusal(req, e, uncertain=uncertain)
    try:
        in_id = _usable_id((data or {}).get('data') or {})
    except (KeyError, TypeError, ValueError, AttributeError):
        in_id = ''
    if not _move(req, R.DONE, in_id=in_id):
        raise InvoiceNinjaRefused(
            'Invoice Ninja recorded the payment, but another request took this one over '
            'meanwhile. Reload the page before doing anything else.')
    return req


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
    for row in _request('GET', '/clients', params={'email': email}).get('data') or []:
        try:
            return _usable_id(row)
        except (KeyError, ValueError):
            continue
    return None


def find_or_create_client(client, user=None):
    """Return the IN client id for an MB Client, creating it if needed.

    Link once, don't sync: if we already stored an IN id, use it. Else match by
    the client's email (the same one a create sends); else create. Saves the id
    back on the MB Client.

    A create whose answer is lost is looked for by the email it was SENT with,
    never re-sent on MB's own say-so (decision C, Oct 24 2026): until it is
    found or a person says it is not there, no other create or lookup for this
    client starts. A client with no email has nothing that identifies it, so
    MB does not guess (a same-name match can be another company): that create
    is held until a person checks.
    """
    R = _req_model()
    if client.invoice_ninja_id:
        return client.invoice_ninja_id
    held = requests_for(client).filter(state=R.HELD).first()
    if held is not None:
        raise InvoiceNinjaRefused(_held_message(held))

    pending = requests_for(client).filter(kind=R.KIND_CLIENT, state__in=(R.UNKNOWN, R.SENDING)).exists()
    email = _client_email(client)
    if email and not pending:
        in_id = _find_client_by_email(email)
        if in_id:
            return _link_client(client, in_id)

    req, carried_on = _claim(client, R.KIND_CLIENT, {'client': _client_payload(client), 'email': email}, user=user)
    if carried_on:
        return _carry_on_client(req, client)
    payload = req.payload['client']
    _touch(req)
    try:
        data = _request('POST', '/clients', json=payload)
        in_id = _usable_id(data['data'])
    except InvoiceNinjaOutcomeUnknown as e:
        return _recover_client(req, client, str(e))
    except InvoiceNinjaError as e:
        _move(req, R.FAILED, error=str(e))
        raise
    except (KeyError, TypeError, ValueError):
        return _recover_client(req, client, 'Invoice Ninja\'s answer did not include the client.')
    _move(req, R.DONE, in_id=in_id)
    return _link_client(client, in_id)


def _carry_on_client(req, client):
    """An earlier create of this client got no answer. Look for it by the
    email it was sent with; never send it again on MB's own say-so."""
    R = _req_model()
    email = req.payload.get('email') or ''
    name = (req.payload.get('client') or {}).get('name') or client.name
    if not email:
        # Nothing to look it up by: sending again could make a second one.
        _move(req, R.HELD, reason=_no_email_reason(name))
        raise InvoiceNinjaOutcomeUnknown(_held_message(req))
    _touch(req)
    try:
        in_id = _find_client_by_email(email)
    except InvoiceNinjaError as e:
        _move(req, R.UNKNOWN, error=str(e))
        raise InvoiceNinjaOutcomeUnknown(
            f'An earlier client create got no answer, and MB could not check Invoice Ninja for it: '
            f'{e} Nothing new was sent. Try again in a minute.')
    if in_id:
        _move(req, R.DONE, in_id=in_id)
        return _link_client(client, in_id)
    _move(req, R.UNKNOWN)
    raise InvoiceNinjaOutcomeUnknown(
        f'{_LOST_CREATE_MSG.format(what="client")} MB looks for it by the email it was sent with ({email}).')


def _link_client(client, in_id):
    """Store IN's id on the client unless another request linked one first,
    and return whichever id the client now has."""
    from .models import Client
    Client.objects.filter(pk=client.pk, invoice_ninja_id='').update(invoice_ninja_id=in_id)
    client.invoice_ninja_id = Client.objects.filter(pk=client.pk).values_list('invoice_ninja_id', flat=True).first() or in_id
    return client.invoice_ninja_id


def _no_email_reason(name):
    return (
        f'Invoice Ninja may have created the client "{name}" (no answer came back), and '
        'this client has no email for MB to find it by. Check the Invoice Ninja client '
        'list. If it is not there, clear this hold. If it is, add the same email to '
        'this client in MB and to that client in Invoice Ninja, then clear this hold: '
        'MB will find it by email.'
    )


def _recover_client(req, client, why):
    """This request's own create got no usable answer: look once by the email
    it sent. Not found, it stays open and is never sent again on its own."""
    R = _req_model()
    email = req.payload.get('email') or ''
    name = (req.payload.get('client') or {}).get('name') or client.name
    if email:
        _touch(req)
        try:
            in_id = _find_client_by_email(email)
        except InvoiceNinjaError:
            in_id = None
        if in_id:
            _move(req, R.DONE, in_id=in_id)
            return _link_client(client, in_id)
        _move(req, R.UNKNOWN, error=why)
        raise InvoiceNinjaOutcomeUnknown(
            f'{why} It may or may not have created the client. It is safe to try again in a '
            f'minute: MB looks for it by the email it sent ({email}) and never sends it a '
            'second time on its own.'
        )
    _move(req, R.HELD, reason=_no_email_reason(name), error=why)
    raise InvoiceNinjaOutcomeUnknown(f'{why} {_no_email_reason(name)}')


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

# What an invoice MB may still pay onto reads as. Anything else (Unknown, a
# status MB does not know, Cancelled, Reversed) is not permission to pay.
_PAYABLE_LABELS = ('Draft', 'Sent', 'Overdue')


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

    _settle_on_status(work_order, label, in_id)
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


def push_sale(sale, draft=False, resend=False, user=None):
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
    line_items = _line_items_payload(sale)
    if not line_items:
        raise InvoiceNinjaError(
            'This sale has no priced line items to send. Add a price to at least '
            'one line first.'
        )
    if not draft:
        require_no_hold(sale, finishing=_req_model().KIND_PAYMENT)

    if sale.client_id:
        in_client_id = find_or_create_client(sale.client, user=user)
    else:
        in_client_id = find_or_create_walkin_client()

    resume = not draft and not resend and bool(sale.invoice_ninja_id) and sale.in_payment_pending
    if not resume:
        if draft:
            host_fields = {'in_status': 'Draft', 'in_status_checked_at': '__now__', 'in_payment_pending': False}
        else:
            # The old status belongs to the old invoice; until this payment is
            # confirmed the sale is pending, not Paid. Saved with the link, so
            # however the invoice is found (answer, recovery, a person's
            # check), the payment is still owed.
            host_fields = {'in_status': '', 'in_payment_pending': True}
        _create_invoice(sale, {
            'client_id': in_client_id,
            'po_number': sale.sale_number,  # SALE# for bank→IN→MB traceability
            'line_items': line_items,
        }, resend=resend, host_fields=host_fields, user=user)
    invoice_id = sale.invoice_ninja_id

    if draft:
        # No payment posted — the invoice stays an unpaid Draft in IN.
        return sale.invoice_ninja_ref or sale.invoice_ninja_id

    # Post the payment → IN marks the invoice Paid (not Draft).
    amount = sale.amount if sale.amount is not None else sale.line_items_total
    try:
        post_payment(
            sale, invoice_id, in_client_id, amount=amount, method=sale.payment_method,
            method_label=f'Counter sale {sale.sale_number} — {sale.get_payment_method_display() or sale.payment_method or "unspecified"}',
            reference=sale.reference or '', type_id=_IN_PAYMENT_TYPE_IDS.get(sale.payment_method), user=user,
        )
    except InvoiceNinjaAlreadyPaid as e:
        _mark_sale_paid(sale)
        raise InvoiceNinjaAlreadyPaid(f'Invoice #{sale.invoice_ninja_ref or invoice_id}: {e}')
    except InvoiceNinjaError as e:
        raise type(e)(
            f'Invoice #{sale.invoice_ninja_ref or invoice_id} is in Invoice Ninja, but '
            f'its payment was not confirmed: {e} Retrying adds the payment to that '
            'same invoice.'
        )
    # The payment is recorded at IN: nothing is left to post. Paid only when IN
    # says so; the amount recorded here may be short of the invoice (an edited
    # checkout amount, or one recorded before line totals were rounded).
    ref = sale.invoice_ninja_ref or invoice_id
    try:
        label, balance = invoice_state(invoice_id)
    except InvoiceNinjaError as e:
        _record_sale_payment_state(sale, None)
        raise InvoiceNinjaPartlyPaid(
            f'Invoice Ninja recorded the payment of ${money(amount)} on invoice #{ref}, but MB '
            f'could not read the invoice back to confirm it is paid: {e} Use "Check IN".')
    if label != 'Paid':
        _record_sale_payment_state(sale, label)
        raise InvoiceNinjaPartlyPaid(owed_message(amount, ref, label, balance))
    _mark_sale_paid(sale)
    return sale.invoice_ninja_ref or sale.invoice_ninja_id


def _record_sale_payment_state(sale, label):
    """MB's payment is at IN but IN does not (or could not be read to) show the
    invoice Paid: the payment is no longer pending; the status is IN's word."""
    from django.utils import timezone
    sale.in_payment_pending = False
    fields = ['in_payment_pending']
    if label:
        sale.in_status = label
        sale.in_status_checked_at = timezone.now()
        fields += ['in_status', 'in_status_checked_at']
    sale.save(update_fields=fields)


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
    _settle_on_status(sale, label, in_id)
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

def charge_on_file(host, user=None):
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

    # The claim is a request row, written before anything is sent and kept
    # active (queued) after IN accepts the trigger, so a second charge is
    # refused until Check IN reads Paid or CHARGE_COOLDOWN passes.
    R = _req_model()
    # Nothing is charged on a held record, whatever lane the hold is in.
    require_no_hold(host)
    req, _ = _claim(host, R.KIND_CHARGE, {'invoice_id': in_id}, base_invoice_id=in_id, user=user)
    try:
        # Fresh status from IN (updates the host's stored status) — not the
        # possibly-stale stored value. Fails loud if IN is unreachable, so no
        # charge fires blind.
        _touch(req)
        current = check_status(host)
    except InvoiceNinjaError as e:
        _move(req, R.FAILED, error=f'Status check before charging failed: {e}')
        raise InvoiceNinjaError(f'MB could not check Invoice Ninja before charging, so nothing was charged: {e}')
    if current.strip().lower() == 'paid':
        _move(req, R.FAILED, error='Already Paid in Invoice Ninja.')
        raise InvoiceNinjaError(f'This {noun} is already marked Paid in Invoice Ninja — refusing to charge again.')
    _touch(req)
    try:
        _request('POST', '/invoices/bulk', json={'action': 'auto_bill', 'ids': [in_id]})
    except InvoiceNinjaOutcomeUnknown as e:
        reason = (
            f'A card charge sent {_when(req.started_at)} was not confirmed by Invoice Ninja, so the '
            'card may have been charged. Use "Check IN": if it shows Paid this clears by itself. If '
            'Invoice Ninja shows no charge, clear this hold to allow charging again.'
        )
        _move(req, R.HELD, reason=reason, error=str(e))
        raise InvoiceNinjaOutcomeUnknown(f'{e} {reason}')
    except InvoiceNinjaError as e:
        _move(req, R.FAILED, error=str(e))
        raise
    if not _move(req, R.QUEUED, active_at=timezone.now()):
        # IN accepted the trigger, but this request had been taken for gone
        # and its row held meanwhile: the hold already says the card may have
        # been charged.
        raise InvoiceNinjaOutcomeUnknown(_held_message(req))

    # Best-effort immediate read-back — the async job may not have run yet, so
    # this often still reports the pre-charge status. That's expected; the
    # operator uses "Check IN" again shortly after to see the real outcome.
    try:
        return check_status(host)
    except InvoiceNinjaError:
        # The trigger itself succeeded (we didn't raise above); a read-back
        # failure right after shouldn't be reported as a charge failure.
        return stored_label() or 'Unknown'


def charge_sale_on_file(sale, user=None):
    """Back-compat alias — the Sale-only entry point kept so any external
    reference (or a stale import) doesn't break. New code should call
    charge_on_file() directly."""
    return charge_on_file(sale, user=user)


# ---------------------------------------------------------------------------
# Light POS — host-agnostic settlement primitives (WorkOrder or Sale). Neither
# push_sale() nor push_work_order() above are touched by this — they remain the
# tested, working paths for their existing callers. These are additive building
# blocks the POS uses to settle a WorkOrder without duplicating push_sale's
# Sale-specific field writes. The caller (the POS view) writes the payment
# fields onto its own host, since a WorkOrder and a Sale disagree on where
# payment fields live (Sale stores them on itself; WorkOrder on its Invoice).
# ---------------------------------------------------------------------------

def push_host_invoice(host, *, po_number, client=None, is_walkin=False, user=None):
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
    in_client_id = find_or_create_walkin_client() if is_walkin else find_or_create_client(client, user=user)
    invoice, recovered = _create_invoice(host, {
        'client_id': in_client_id,
        'po_number': po_number,
        'line_items': line_items,
    }, user=user)
    return str(invoice['id']), str(invoice.get('number') or ''), in_client_id, recovered
