"""
IN connection test: function and tolerance of Invoice Ninja's API.

Design draft (mb-docs MB-IN-FRONTEND-DESIGN-DRAFT.md, section 17): one client,
one work entry, one draft invoice, the status read back, plus a lost reply, two
clicks at once, an edit made in IN, "too many requests" and an expired key.

Every scenario that creates or changes anything runs ONLY against a throwaway
self-hosted IN. Hosted IN (invoicing.co) gets --hosted-readonly: one GET, to
read the rate-limit headers, and nothing else. The guard is in code, not in
the operator's memory.

Where it matters, the scenarios call MB's own client (core.invoice_ninja) with
its configuration patched to the test instance, so the report shows how
TODAY's MB code behaves, not just how IN behaves.

    venv/bin/python manage.py in_connection_test --url http://10.58.58.11 \
        --token-file ~/in-test-token --out in-test-results.json
"""
import json
import threading
import time
import uuid
from datetime import date
from unittest import mock
from urllib.parse import urlparse

import requests
from django.core.management.base import BaseCommand, CommandError

from core import invoice_ninja as inj

HOSTED_SUFFIXES = ('invoicing.co', 'invoiceninja.com')

SCENARIOS = (
    'client', 'invoice', 'status', 'lost_reply', 'two_clicks',
    'edit_in_in', 'too_many', 'expired_key', 'unreachable',
)
# Run only when asked: needs the IN app stopped by hand first (nginx up, PHP down).
EXTRA_SCENARIOS = ('in_down',)


def is_hosted(url):
    host = (urlparse(url).hostname or '').lower()
    return any(host == s or host.endswith('.' + s) for s in HOSTED_SUFFIXES)


class Probe:
    """Raw HTTP to IN with timing, plus a way to run MB's own client against it."""

    def __init__(self, base, token):
        self.base = base.rstrip('/')
        self.token = token
        self.timings = []

    def call(self, method, path, *, token=None, timeout=20, **kw):
        url = f'{self.base}{inj.API_PREFIX}{path}'
        started = time.monotonic()
        try:
            resp = requests.request(method, url, headers=inj._headers(token or self.token),
                                    timeout=timeout, **kw)
        finally:
            self.timings.append((method, path.split('?')[0], round(time.monotonic() - started, 3)))
        return resp

    def json(self, method, path, **kw):
        resp = self.call(method, path, **kw)
        try:
            body = resp.json()
        except ValueError:
            body = {'_non_json': resp.text[:300]}
        return resp.status_code, body

    def mb(self, token=None, timeout=None):
        """Context manager: MB's _request pointed at this instance."""
        patches = [mock.patch.object(inj, '_config', return_value=(self.base, token or self.token))]
        if timeout is not None:
            patches.append(mock.patch.object(inj, 'TIMEOUT', timeout))
        return _Stack(patches)


class _Stack:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def mb_call(probe, method, path, *, token=None, timeout=None, **kw):
    """Run MB's _request; return ('ok', data) or ('error', message)."""
    with probe.mb(token=token, timeout=timeout):
        try:
            return 'ok', inj._request(method, path, **kw)
        except inj.InvoiceNinjaError as e:
            return 'error', str(e)


def find_invoices(probe, **params):
    params.setdefault('per_page', 50)
    params.setdefault('status', 'active,archived,deleted')
    q = '&'.join(f'{k}={v}' for k, v in params.items())
    code, body = probe.json('GET', f'/invoices?{q}')
    return [r['id'] for r in (body.get('data') or [])] if code == 200 else None


class Command(BaseCommand):
    help = 'Test Invoice Ninja API function and tolerance against a throwaway self-hosted IN.'

    def add_arguments(self, parser):
        parser.add_argument('--url', required=True, help='IN base URL, e.g. http://10.58.58.11')
        parser.add_argument('--token-file', required=True, help='File holding the IN API token.')
        parser.add_argument('--out', help='Write the full results as JSON here.')
        parser.add_argument('--only', help='Comma list of scenarios. Default: all. '
                            f'Choices: {", ".join(SCENARIOS + EXTRA_SCENARIOS)}')
        parser.add_argument('--hosted-readonly', action='store_true',
                            help='One read-only GET; report status and rate-limit headers. Safe on hosted IN.')

    def handle(self, *args, **opts):
        with open(opts['token_file']) as f:
            token = f.read().strip()
        if not token:
            raise CommandError('Token file is empty.')
        probe = Probe(opts['url'], token)

        if opts['hosted_readonly']:
            results = {'hosted_readonly': self.hosted_readonly(probe)}
        else:
            if is_hosted(opts['url']):
                raise CommandError(
                    'Refusing: this test creates and changes records, and that URL is hosted IN. '
                    'Use --hosted-readonly for hosted, or point --url at a throwaway self-hosted IN.')
            names = opts['only'].split(',') if opts['only'] else list(SCENARIOS)
            unknown = set(names) - set(SCENARIOS + EXTRA_SCENARIOS)
            if unknown:
                raise CommandError(f'Unknown scenario(s): {", ".join(sorted(unknown))}')
            self.run_id = uuid.uuid4().hex[:8]
            self.ctx = {}
            results = {'run_id': self.run_id, 'url': opts['url']}
            if names != ['in_down']:  # in_down runs with IN's app stopped on purpose
                code, ping = probe.json('GET', '/clients?per_page=1')
                resp = probe.call('GET', '/clients?per_page=1')
                results['in_version'] = resp.headers.get('X-APP-VERSION')
                if code != 200:
                    raise CommandError(f'Token check failed: {code} {ping}')
            for name in names:
                self.stdout.write(f'-- {name}')
                try:
                    results[name] = getattr(self, f's_{name}')(probe)
                except Exception as e:  # report and carry on; one broken scenario must not hide the rest
                    results[name] = {'crashed': f'{type(e).__name__}: {e}'}
                self.stdout.write(json.dumps(results[name], indent=2, default=str))

        results['timings'] = summarize_timings(probe.timings)
        self.stdout.write(json.dumps(results['timings'], indent=2))
        if opts['out']:
            with open(opts['out'], 'w') as f:
                json.dump(results, f, indent=2, default=str)
            self.stdout.write(self.style.SUCCESS(f'wrote {opts["out"]}'))

    # -- hosted, read-only --------------------------------------------------

    def hosted_readonly(self, probe):
        resp = probe.call('GET', '/clients?per_page=1')
        keep = ('X-APP-VERSION', 'X-Api-Version', 'X-RateLimit-Limit', 'X-RateLimit-Remaining',
                'Retry-After', 'X-RateLimit-Reset', 'Server', 'CF-Cache-Status')
        return {
            'status': resp.status_code,
            'headers': {k: resp.headers.get(k) for k in keep if resp.headers.get(k) is not None},
            'rows_returned': len((resp.json().get('data') or [])) if resp.ok else None,
        }

    # -- function -----------------------------------------------------------

    def s_client(self, probe):
        email = f'mbtest+{self.run_id}@example.com'
        payload = {'name': f'MB Test Client {self.run_id}',
                   'contacts': [{'first_name': 'Test', 'last_name': f'Contact {self.run_id}',
                                 'email': email, 'send_email': False}]}
        kind, data = mb_call(probe, 'POST', '/clients', json=payload)
        if kind != 'ok':
            return {'created': False, 'mb_error': data}
        cid = data['data']['id']
        self.ctx['client_id'] = cid
        kind, found = mb_call(probe, 'GET', '/clients', params={'email': email})
        rows = (found.get('data') or []) if kind == 'ok' else []
        return {'created': True, 'client_id': cid, 'name': data['data']['name'],
                'find_by_email_matches': [r['id'] for r in rows],
                'find_by_email_exact': [r['id'] for r in rows] == [cid]}

    def _draft(self, probe, suffix, cost=95.0, key=None):
        key = key or uuid.uuid4().hex
        payload = {
            'client_id': self.ctx['client_id'],
            'po_number': f'MBTEST-{self.run_id}-{suffix}',
            'custom_value4': key,
            'private_notes': f'mb-key:{key}',
            'line_items': [{
                'product_key': 'Bench labor', 'notes': 'Replaced SSD, reinstalled OS (TEST)',
                'quantity': 1.5, 'cost': cost,
                'custom_value1': 'TKT-TEST-0001', 'custom_value2': 'Test Laptop SN123',
            }],
        }
        return payload, key

    def s_invoice(self, probe):
        payload, key = self._draft(probe, 'MAIN')
        kind, data = mb_call(probe, 'POST', '/invoices', json=payload)
        if kind != 'ok':
            return {'created': False, 'mb_error': data}
        inv = data['data']
        self.ctx['invoice_id'] = inv['id']
        code, back = probe.json('GET', f'/invoices/{inv["id"]}')
        b = back.get('data', {})
        line = (b.get('line_items') or [{}])[0]
        return {
            'created': True, 'invoice_id': inv['id'], 'number_assigned_by_in': inv.get('number'),
            'status_on_create': inj.status_label(inv.get('status_id')),
            'amount': b.get('amount'), 'expected_amount': 142.5,
            'po_number_kept': b.get('po_number') == payload['po_number'],
            'invoice_custom_value4_kept': b.get('custom_value4') == key,
            'line_custom_value1_kept': line.get('custom_value1') == 'TKT-TEST-0001',
            'line_custom_value2_kept': line.get('custom_value2') == 'Test Laptop SN123',
            'line_type_id': line.get('type_id'),
            'find_by_custom_value4': find_invoices(probe, custom_value4=key),
        }

    def s_status(self, probe):
        iid = self.ctx['invoice_id']
        seen = []

        def read():
            code, b = probe.json('GET', f'/invoices/{iid}')
            d = b.get('data', {})
            seen.append({'status': inj.status_label(d.get('status_id')), 'raw_status_id': d.get('status_id'),
                         'balance': d.get('balance'), 'paid_to_date': d.get('paid_to_date')})

        read()
        code, b = probe.json('POST', '/invoices/bulk', json={'action': 'mark_sent', 'ids': [iid]})
        mark_sent = code
        read()
        pay_key = uuid.uuid4().hex
        code, b = probe.json('POST', '/payments', json={
            'client_id': self.ctx['client_id'], 'amount': 142.5, 'date': date.today().isoformat(),
            'invoices': [{'invoice_id': iid, 'amount': 142.5}], 'idempotency_key': pay_key,
            'private_notes': 'MB test payment'})
        payment = code
        read()
        return {'mark_sent_http': mark_sent, 'payment_http': payment, 'read_backs': seen,
                'raw_status_id_type': type(seen[0]['raw_status_id']).__name__}

    # -- tolerance ----------------------------------------------------------

    def s_lost_reply(self, probe):
        out = {}
        # 1. Raw: send, drop the connection before the answer arrives, then look.
        payload, key = self._draft(probe, 'LOST')
        try:
            probe.call('POST', '/invoices', json=payload, timeout=(5, 0.05))
            out['raw_reply'] = 'arrived (could not make it get lost)'
        except requests.exceptions.ReadTimeout:
            out['raw_reply'] = 'lost (read timeout after sending)'
        time.sleep(5)
        out['raw_found_by_custom_value4'] = find_invoices(probe, custom_value4=key)
        out['raw_found_by_po_number_filter'] = find_invoices(probe, filter=payload['po_number'])
        # 2. Same thing through MB's own client, to see what today's MB tells the user.
        payload2, key2 = self._draft(probe, 'LOST-MB')
        kind, msg = mb_call(probe, 'POST', '/invoices', timeout=0.05, json=payload2)
        out['mb_result'] = kind
        out['mb_message'] = msg if kind == 'error' else 'no error'
        time.sleep(5)
        found = find_invoices(probe, custom_value4=key2)
        out['mb_invoice_exists_anyway'] = found
        # 3. What a plain retry (the user clicking again) does.
        kind, _ = mb_call(probe, 'POST', '/invoices', json=payload2)
        time.sleep(1)
        out['after_plain_retry_count'] = len(find_invoices(probe, custom_value4=key2) or [])
        return out

    def _concurrent(self, fn, n=2):
        barrier = threading.Barrier(n)
        results = [None] * n

        def worker(i):
            barrier.wait()
            try:
                results[i] = fn()
            except Exception as e:
                results[i] = f'{type(e).__name__}: {e}'

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def s_two_clicks(self, probe):
        out = {}
        # a. Two identical invoice creates at the same instant.
        payload, key = self._draft(probe, 'DOUBLE')
        codes = self._concurrent(lambda: probe.call('POST', '/invoices', json=payload).status_code)
        time.sleep(1)
        out['invoice_create_http'] = codes
        out['invoices_created'] = len(find_invoices(probe, custom_value4=key) or [])
        # IN's guard is a 1-second lock on a hash of the body (StoreInvoiceRequest). The
        # same click 2 seconds later:
        time.sleep(2)
        out['same_create_2s_later_http'] = probe.call('POST', '/invoices', json=payload).status_code
        time.sleep(1)
        out['invoices_after_2s_retry'] = len(find_invoices(probe, custom_value4=key) or [])
        # Does IN honour idempotency_key on invoices, as it does on payments?
        p3, key3 = self._draft(probe, 'IDEMKEY')
        p3['idempotency_key'] = uuid.uuid4().hex
        first = probe.call('POST', '/invoices', json=p3).status_code
        time.sleep(2)
        second = probe.call('POST', '/invoices', json=p3)
        time.sleep(1)
        out['invoice_idempotency_key'] = {'http': [first, second.status_code], 'body2': second.text[:120]
                                          if second.status_code >= 400 else 'ok',
                                          'invoices_created': len(find_invoices(probe, custom_value4=key3) or [])}
        # b. Two identical payments (same idempotency_key) at the same instant.
        p2, _ = self._draft(probe, 'PAYDOUBLE', cost=40.0)
        code, b = probe.json('POST', '/invoices', json=p2)
        iid = b['data']['id']
        probe.json('POST', '/invoices/bulk', json={'action': 'mark_sent', 'ids': [iid]})
        pay = {'client_id': self.ctx['client_id'], 'amount': 60.0, 'date': date.today().isoformat(),
               'invoices': [{'invoice_id': iid, 'amount': 60.0}], 'idempotency_key': uuid.uuid4().hex}

        def post_pay():
            r = probe.call('POST', '/payments', json=pay)
            return r.status_code, r.text[:160] if r.status_code >= 400 else 'ok'
        out['payment_concurrent'] = self._concurrent(post_pay)
        # c. The same payment again a moment later (a retry after a lost reply).
        out['payment_retry_later'] = post_pay()
        code, b = probe.json('GET', f'/invoices/{iid}')
        d = b.get('data', {})
        out['invoice_after'] = {'status': inj.status_label(d.get('status_id')),
                                'paid_to_date': d.get('paid_to_date'), 'balance': d.get('balance')}
        code, b = probe.json('GET', f'/payments?client_id={self.ctx["client_id"]}&per_page=100')
        out['payments_against_invoice'] = sum(
            1 for p in (b.get('data') or []) for pi in (p.get('paymentables') or [])
            if pi.get('invoice_id') == iid) or sum(
            1 for p in (b.get('data') or []) for inv in (p.get('invoices') or []) if inv.get('id') == iid)
        return out

    def s_edit_in_in(self, probe):
        out = {}
        payload, key = self._draft(probe, 'EDIT')
        code, b = probe.json('POST', '/invoices', json=payload)
        iid = b['data']['id']
        before = b['data']
        time.sleep(2)
        since = int(time.time())
        time.sleep(2)
        # Someone edits the invoice in IN: price changed, a note added.
        edited = dict(before)
        edited['line_items'] = [dict(before['line_items'][0], cost=120.0)]
        edited['public_notes'] = 'Edited in IN by a person'
        code, b = probe.json('PUT', f'/invoices/{iid}', json=edited)
        out['edit_http'] = code
        out['amount_before_after'] = [before.get('amount'), b.get('data', {}).get('amount')]
        out['line_custom_values_after_edit'] = [
            b.get('data', {}).get('line_items', [{}])[0].get(k) for k in ('custom_value1', 'custom_value2')]
        changed = find_invoices(probe, updated_at=since, client_id=self.ctx['client_id'])
        out['changed_since_query_finds_it'] = iid in (changed or [])
        out['updated_at_vs_since'] = [b.get('data', {}).get('updated_at'), since]
        # Archive, then delete, in IN: can MB still see what happened?
        since2 = int(time.time())
        time.sleep(1)
        probe.json('POST', '/invoices/bulk', json={'action': 'archive', 'ids': [iid]})
        code, b = probe.json('GET', f'/invoices/{iid}')
        out['after_archive'] = {'get_http': code, 'archived_at': b.get('data', {}).get('archived_at'),
                                'in_default_list': iid in (find_invoices(probe, status='active') or []),
                                'in_changed_since_all_statuses': iid in (find_invoices(probe, updated_at=since2) or [])}
        time.sleep(1)
        since3 = int(time.time())
        time.sleep(1)
        probe.json('POST', '/invoices/bulk', json={'action': 'delete', 'ids': [iid]})
        code, b = probe.json('GET', f'/invoices/{iid}')
        out['after_delete'] = {'get_http': code, 'is_deleted': b.get('data', {}).get('is_deleted'),
                               'in_changed_since_all_statuses': iid in (find_invoices(probe, updated_at=since3) or []),
                               'in_changed_since_default_status': iid in (find_invoices(
                                   probe, updated_at=since3, status='') or [])}
        # A list call with no status parameter at all, as MB's list_client_invoices makes.
        code, b = probe.json('GET', f'/invoices?client_id={self.ctx["client_id"]}&per_page=100')
        rows = {r['id']: r for r in (b.get('data') or [])}
        out['no_status_param_list'] = {'includes_deleted': iid in rows,
                                       'is_deleted_flag': rows.get(iid, {}).get('is_deleted')}
        return out

    def s_too_many(self, probe):
        """Self-hosted IN has no general API limit (RouteServiceProvider: Limit::none() when
        self-hosted; hosted = 1000/min per IP and per token). The reports routes carry their
        own throttle:20,1 on every install, so they produce a genuine IN 429."""
        out = {'normal_reply_has_ratelimit_headers': bool(
            probe.call('GET', '/clients?per_page=1').headers.get('X-RateLimit-Limit'))}
        body = {'send_email': False, 'date_range': 'all', 'report_keys': []}
        first_429 = None
        for i in range(1, 31):
            r = probe.call('POST', '/reports/clients', json=body)
            if r.status_code == 429:
                first_429 = (i, r)
                break
        if not first_429:
            out['got_429'] = False
            return out
        i, r = first_429
        out['got_429'] = True
        out['429_on_request_number'] = i
        out['429_headers'] = {k: r.headers.get(k) for k in
                              ('Retry-After', 'X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset')}
        out['429_body'] = r.text[:200]
        kind, msg = mb_call(probe, 'POST', '/reports/clients', json=body)
        out['mb_message_on_429'] = msg
        out['other_routes_still_fine'] = probe.call('GET', '/clients?per_page=1').status_code
        wait = int(r.headers.get('Retry-After') or 60) + 1
        time.sleep(wait)
        out[f'after_waiting_{wait}s'] = probe.call('POST', '/reports/clients', json=body).status_code
        return out

    def s_expired_key(self, probe):
        out = {}
        for how in ('archive', 'delete'):
            code, b = probe.json('POST', '/tokens', json={'name': f'mbtest-{how}-{self.run_id}'})
            tok = b.get('data', {})
            secret, tid = tok.get('token'), tok.get('id')
            if not secret:
                out[how] = {'create_http': code, 'note': 'no token string returned', 'body_keys': list(tok)}
                continue
            works = probe.call('GET', '/clients?per_page=1', token=secret).status_code
            bulk_code, _ = probe.json('POST', '/tokens/bulk', json={'action': how, 'ids': [tid]})
            r = probe.call('GET', '/clients?per_page=1', token=secret)
            kind, msg = mb_call(probe, 'GET', '/clients', token=secret, params={'per_page': 1})
            out[how] = {'works_before': works, f'{how}_http': bulk_code,
                        'after_http': r.status_code, 'after_body': r.text[:160],
                        'mb_message': msg if kind == 'error' else 'no error (token still works)'}
        r = probe.call('GET', '/clients?per_page=1', token='not-a-real-token')
        kind, msg = mb_call(probe, 'GET', '/clients', token='not-a-real-token')
        out['garbage_token'] = {'http': r.status_code, 'body': r.text[:160], 'mb_message': msg}
        return out

    def s_unreachable(self, probe):
        host = urlparse(probe.base).hostname
        dead = Probe(f'http://{host}:9', probe.token)
        started = time.monotonic()
        kind, msg = mb_call(dead, 'GET', '/clients')
        return {'mb_message': msg, 'seconds_to_fail': round(time.monotonic() - started, 2)}

    def s_in_down(self, probe):
        try:
            r = probe.call('GET', '/clients?per_page=1', timeout=90)
            raw = {'http': r.status_code, 'content_type': r.headers.get('Content-Type'), 'body': r.text[:120]}
        except requests.RequestException as e:
            raw = {'error': type(e).__name__}
        raw['seconds'] = probe.timings[-1][2]
        started = time.monotonic()
        kind, msg = mb_call(probe, 'GET', '/clients')
        return {'raw_wait_up_to_90s': raw, 'mb_message': msg,
                'mb_seconds': round(time.monotonic() - started, 2)}


def summarize_timings(timings):
    by = {}
    for method, path, secs in timings:
        by.setdefault(f'{method} {path}', []).append(secs)
    return {k: {'calls': len(v), 'median_s': sorted(v)[len(v) // 2], 'max_s': max(v)} for k, v in sorted(by.items())}
