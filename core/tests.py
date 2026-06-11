"""Spine tests for Murphy's Bench.

This is the first of the project's tests, written alongside the stabilization
bug-fix pass. Each test locks in behavior we rely on in daily production use so
a future change can't silently regress it. Targets the spine, not coverage %.

Run with:  venv/bin/python -m pytest
"""
import logging

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse

from core.models import (
    Client, Device, Ticket, WorkOrder, SiteSettings, Contact, EmailTemplate,
)

User = get_user_model()


@pytest.fixture
def client_obj(db):
    return Client.objects.create(name='Acme Co')


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username='admin', password='x', is_staff=True, is_superuser=True,
    )


# ── Bug 1: ticket delete guard actually blocks when a WO is linked ──────────

@pytest.mark.django_db
def test_ticket_with_work_order_cannot_be_deleted(client, client_obj, admin_user):
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    WorkOrder.objects.create(client=client_obj, ticket=ticket)

    client.force_login(admin_user)
    client.post(reverse('core:ticket_delete', args=[ticket.pk]))

    assert Ticket.objects.filter(pk=ticket.pk).exists(), \
        'Ticket with a linked work order must not be deletable.'


@pytest.mark.django_db
def test_ticket_without_work_order_can_be_deleted(client, client_obj, admin_user):
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')

    client.force_login(admin_user)
    client.post(reverse('core:ticket_delete', args=[ticket.pk]))

    assert not Ticket.objects.filter(pk=ticket.pk).exists()


# ── Bug 2: many serial-less devices are allowed; real serials stay unique ───

@pytest.mark.django_db
def test_multiple_blank_serial_devices_allowed(client_obj):
    Device.objects.create(client=client_obj, name='Laptop A', serial_number='')
    Device.objects.create(client=client_obj, name='Laptop B')  # serial omitted

    devices = Device.objects.filter(client=client_obj)
    assert devices.count() == 2
    assert all(d.serial_number is None for d in devices)


@pytest.mark.django_db
def test_duplicate_real_serial_still_rejected(client_obj):
    Device.objects.create(client=client_obj, name='A', serial_number='SN-123')
    with pytest.raises(IntegrityError):
        Device.objects.create(client=client_obj, name='B', serial_number='SN-123')


# ── Bug 3: number assignment survives a collision instead of crashing ───────

@pytest.mark.django_db
def test_ticket_number_collision_is_retried(client_obj):
    first = Ticket.objects.create(client=client_obj, subject='S1', description='D')

    # Simulate a concurrent insert that already took `first`'s number: build a
    # second ticket and force the same number. save() must regenerate, not crash.
    second = Ticket(client=client_obj, subject='S2', description='D')
    second.ticket_number = first.ticket_number
    second.save()

    assert second.pk is not None
    assert second.ticket_number != first.ticket_number
    assert Ticket.objects.count() == 2


@pytest.mark.django_db
def test_work_order_number_collision_is_retried(client_obj):
    first = WorkOrder.objects.create(client=client_obj)
    second = WorkOrder(client=client_obj)
    second.work_order_number = first.work_order_number
    second.save()

    assert second.pk is not None
    assert second.work_order_number != first.work_order_number


# ── Bug 4: a broken email template is logged, not silently swallowed ─────────

@pytest.mark.django_db
def test_bad_email_template_is_logged(client_obj, caplog):
    from core.email_utils import send_ticket_email

    site = SiteSettings.get()
    site.email_enabled = True
    site.save()
    Contact.objects.create(
        client=client_obj, first_name='Pat', last_name='Q',
        email='pat@example.com', is_primary=True,
    )
    EmailTemplate.objects.update_or_create(
        trigger='ticket_created',
        defaults={
            'is_active': True,
            'subject_template': '{% bad tag %}',
            'body_template': 'hi',
        },
    )
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')

    with caplog.at_level(logging.ERROR, logger='core'):
        send_ticket_email('ticket_created', ticket)  # must not raise

    assert any('template' in r.message.lower() for r in caplog.records), \
        'A template render failure should be logged on the core logger.'


# ── reset_operational_data: wipes operational data, keeps config + superusers ──

@pytest.mark.django_db
def test_reset_dry_run_changes_nothing(client_obj, admin_user):
    from django.core.management import call_command

    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    WorkOrder.objects.create(client=client_obj, ticket=ticket)

    call_command('reset_operational_data')  # no --confirm → dry run

    assert Client.objects.count() == 1
    assert Ticket.objects.count() == 1
    assert WorkOrder.objects.count() == 1


@pytest.mark.django_db
def test_reset_deletes_operational_keeps_config(client_obj, admin_user):
    from django.core.management import call_command
    from core.models import HelpTopic, StatusDefinition, Mileage, Device

    # Operational data
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    WorkOrder.objects.create(client=client_obj, ticket=ticket)
    Device.objects.create(client=client_obj, name='Box')
    Mileage.objects.create(technician=admin_user, trip_date='2026-06-11', miles=10)
    grunt = User.objects.create_user(username='tech', password='x')  # non-superuser

    # Configuration that must survive
    HelpTopic.objects.create(name='General')
    status_count_before = StatusDefinition.objects.count()  # seeded by migration

    call_command('reset_operational_data', confirm='DELETE ALL OPERATIONAL DATA')

    # Operational data gone
    assert Client.objects.count() == 0
    assert Ticket.objects.count() == 0
    assert WorkOrder.objects.count() == 0
    assert Device.objects.count() == 0
    assert Mileage.objects.count() == 0
    assert not User.objects.filter(pk=grunt.pk).exists()

    # Configuration + superuser preserved
    assert User.objects.filter(pk=admin_user.pk).exists()
    assert HelpTopic.objects.count() == 1
    assert StatusDefinition.objects.count() == status_count_before


@pytest.mark.django_db
def test_reset_can_keep_named_user(client_obj, admin_user):
    from django.core.management import call_command

    keep = User.objects.create_user(username='dispatcher', password='x')
    drop = User.objects.create_user(username='temp', password='x')

    call_command(
        'reset_operational_data',
        confirm='DELETE ALL OPERATIONAL DATA',
        keep_users='dispatcher',
    )

    assert User.objects.filter(pk=keep.pk).exists()
    assert not User.objects.filter(pk=drop.pk).exists()


# ── Conversation view: quoted-reply folding ─────────────────────────────────

def test_split_reply_quote_separates_new_text_from_quote():
    from core.templatetags.mb_icons import split_reply_quote

    content = (
        "I took it outside and ran a hose on it.\r\n\r\n"
        "On 6/10/26 5:43 PM, testing@example.com wrote:\r\n"
        "> Re: [TKT-00006] Desktop on Fire\r\n"
        "> Did you put it out?\r\n"
    )
    new_text, quoted = split_reply_quote(content)
    assert new_text == 'I took it outside and ran a hose on it.'
    assert 'On 6/10/26' in quoted
    assert 'Did you put it out?' in quoted


def test_split_reply_quote_no_quote_returns_empty():
    from core.templatetags.mb_icons import split_reply_quote

    new_text, quoted = split_reply_quote('Just a plain reply, no quote.')
    assert new_text == 'Just a plain reply, no quote.'
    assert quoted == ''


def test_reply_body_folds_quote_and_escapes_html():
    from core.templatetags.mb_icons import reply_body

    html = str(reply_body("Hello <script>alert(1)</script>\n\nOn x wrote:\n> hi"))
    assert '<details' in html               # quote folded into a disclosure
    assert '&lt;script&gt;' in html          # user HTML escaped, not live
    assert '<script>' not in html


# ── Email header: readable text on the title bar ────────────────────────────

def test_email_contrast_text_color():
    from core.email_utils import _contrast_text_color
    assert _contrast_text_color('#1f5f5b') == '#ffffff'   # dark teal bar -> white text
    assert _contrast_text_color('#111827') == '#ffffff'   # near-black bar -> white text
    assert _contrast_text_color('#ffffff') == '#1f2937'   # white bar -> dark text
    assert _contrast_text_color('') == '#ffffff'          # bad input -> safe default


@pytest.mark.django_db
def test_email_branding_falls_back_to_app_settings():
    from core.email_utils import _email_header_color, _email_logo_field
    from core.models import SiteSettings
    s = SiteSettings.get()
    s.email_header_color = ''
    s.color_title_bar = '#123456'
    s.save()
    assert _email_header_color(s) == '#123456'    # blank -> app Title Bar color
    s.email_header_color = '#abcdef'
    s.save()
    assert _email_header_color(s) == '#abcdef'     # dedicated email value wins
    assert not _email_logo_field(s)                # no email/company logo -> falsy


@pytest.mark.django_db
def test_settings_email_templates_tab_renders(client, admin_user):
    client.force_login(admin_user)
    resp = client.get('/settings/?tab=email_templates')
    assert resp.status_code == 200
    assert b'Email Branding' in resp.content


@pytest.mark.django_db
def test_email_branding_save_post(client, admin_user):
    from core.models import SiteSettings
    client.force_login(admin_user)
    resp = client.post('/settings/email-branding/save/', {'email_header_color': '#1f5f5b'})
    assert resp.status_code == 302  # would have caught the missing reverse import
    assert SiteSettings.get().email_header_color == '#1f5f5b'
