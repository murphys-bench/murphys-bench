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


def _line_items_payload(work_order):
    """Map the WO's PRICED line items to IN invoice line items. Unpriced lines
    (no unit_price) are internal/diagnostic and are not billed."""
    items = []
    for li in work_order.line_items.all():
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
    label = _IN_STATUS_LABELS.get(status_id, f'Unknown ({status_id})')

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

    in_client_id = find_or_create_client(work_order.client)
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
