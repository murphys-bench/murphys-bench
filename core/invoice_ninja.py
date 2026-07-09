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

import requests

logger = logging.getLogger('core')

# IN v5 REST API. Token auth via header; XMLHttpRequest header required by IN.
API_PREFIX = '/api/v1'
TIMEOUT = 20


class InvoiceNinjaError(Exception):
    """Any failure talking to Invoice Ninja. Carries a user-readable message."""


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


def _request(method, path, *, params=None, json=None):
    """Make an IN API call. Raises InvoiceNinjaError on any transport/HTTP error."""
    base, token = _config()
    url = f'{base}{API_PREFIX}{path}'
    try:
        resp = requests.request(
            method, url, headers=_headers(token), params=params, json=json, timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning('Invoice Ninja request failed: %s %s — %s', method, path, e)
        raise InvoiceNinjaError(f'Could not reach Invoice Ninja: {e}')
    if resp.status_code == 401:
        raise InvoiceNinjaError('Invoice Ninja rejected the API token (401). Check the token in Settings.')
    if resp.status_code >= 400:
        # Surface IN's message if present; otherwise the status.
        detail = ''
        try:
            detail = resp.json().get('message', '')
        except ValueError:
            detail = resp.text[:200]
        raise InvoiceNinjaError(f'Invoice Ninja returned {resp.status_code}: {detail or "error"}')
    try:
        return resp.json()
    except ValueError:
        raise InvoiceNinjaError('Invoice Ninja returned a non-JSON response.')


def test_connection():
    """Cheap auth/URL check used by the Settings 'Test Connection' button.
    Returns a short success string or raises InvoiceNinjaError."""
    _request('GET', '/clients', params={'per_page': 1})
    return 'Connected to Invoice Ninja successfully.'


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

    data = _request('POST', '/clients', json=_client_payload(client))
    in_id = str(data['data']['id'])
    client.invoice_ninja_id = in_id
    client.save(update_fields=['invoice_ninja_id'])
    return in_id


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
    on failure nothing is saved on the WO, so the button stays a clean 'Send'.
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
    data = _request('POST', '/invoices', json=payload)
    invoice = data['data']
    work_order.invoice_ninja_id = str(invoice['id'])
    work_order.invoice_ninja_ref = str(invoice.get('number') or '')
    work_order.save(update_fields=['invoice_ninja_id', 'invoice_ninja_ref'])
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

    data = _request('POST', '/clients', json={'name': WALKIN_CLIENT_NAME})
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

    invoice_data = _request('POST', '/invoices', json={
        'client_id': in_client_id,
        'po_number': sale.sale_number,  # SALE# for bank→IN→MB traceability
        'line_items': line_items,
    })
    invoice = invoice_data['data']
    invoice_id = str(invoice['id'])
    sale.invoice_ninja_id = invoice_id
    sale.invoice_ninja_ref = str(invoice.get('number') or '')

    if draft:
        # No payment posted — the invoice stays an unpaid Draft in IN.
        sale.in_status = 'Draft'
        sale.in_status_checked_at = timezone.now()
        sale.save(update_fields=[
            'invoice_ninja_id', 'invoice_ninja_ref', 'in_status', 'in_status_checked_at',
        ])
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
    _request('POST', '/payments', json=payment_payload)

    sale.in_status = 'Paid'
    sale.in_status_checked_at = timezone.now()
    sale.save(update_fields=[
        'invoice_ninja_id', 'invoice_ninja_ref', 'in_status', 'in_status_checked_at',
    ])
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


# Slice 5d — MB-initiated charge against a client's card on file. MB never
# touches a card or money: this triggers IN's own auto-bill action against an
# already-pushed invoice, IN charges the client's stored Square token, and MB
# reads the result back. See memory project_mb_card_payment_security.

def charge_sale_on_file(sale):
    """Trigger Invoice Ninja to charge the client's card on file for this sale's
    already-pushed invoice, via IN's bulk 'auto_bill' action (confirmed against
    IN v5-stable source: POST /invoices/bulk {action:'auto_bill', ids:[...]}
    dispatches IN's AutoBill job against the stored token).

    IMPORTANT — the charge is ASYNCHRONOUS on IN's side. This call only proves
    the charge was successfully QUEUED, never that it was actually paid. Payment
    truth always comes from check_sale_status() (the existing phase-1 read-back),
    which this function calls once immediately after triggering, purely to
    record whatever status IN reports at that moment (often still unpaid — the
    job may not have run yet). Callers must not treat this function's return
    value as proof of payment.

    Refuses (raises InvoiceNinjaError) if the sale hasn't been pushed yet or is
    already marked Paid — never re-charges. Fails loud on any IN/transport error.

    Double-charge safety: because a prior charge is async, MB's STORED in_status
    can lag the real IN state. So we do a FRESH read-back against IN first and
    refuse if it now reports Paid — this closes the window where a charge that
    has since settled would otherwise be fired again. It also means an
    unreachable IN aborts the charge (we never fire blind).
    """
    in_id = (sale.invoice_ninja_id or '').strip()
    if not in_id:
        raise InvoiceNinjaError('This sale has not been pushed to Invoice Ninja yet — send it as a draft first.')

    # Fresh status from IN (updates sale.in_status) — not the possibly-stale
    # stored value. Fails loud if IN is unreachable, so no charge fires blind.
    current = check_sale_status(sale)
    if current.strip().lower() == 'paid':
        raise InvoiceNinjaError('This sale is already marked Paid in Invoice Ninja — refusing to charge again.')

    _request('POST', '/invoices/bulk', json={'action': 'auto_bill', 'ids': [in_id]})

    # Best-effort immediate read-back — the async job may not have run yet, so
    # this often still reports the pre-charge status. That's expected; the
    # operator uses "Check IN" again shortly after to see the real outcome.
    try:
        return check_sale_status(sale)
    except InvoiceNinjaError:
        # The trigger itself succeeded (we didn't raise above); a read-back
        # failure right after shouldn't be reported as a charge failure.
        return sale.in_status or 'Unknown'
