"""Spine tests for Murphy's Bench.

This is the first of the project's tests, written alongside the stabilization
bug-fix pass. Each test locks in behavior we rely on in daily production use so
a future change can't silently regress it. Targets the spine, not coverage %.

Run with:  venv/bin/python -m pytest
"""
import json
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


# ── Email greeting name: residential → first name, business → company ───────

@pytest.mark.django_db
def test_greeting_uses_contact_first_name_for_residential():
    """Residential clients are named after the customer's last name by our data
    convention, so the greeting must come from the contact's FIRST name."""
    from core.email_utils import _resolve_ticket_contact, _greeting_name

    client = Client.objects.create(name='Davis', client_type='residential')
    Contact.objects.create(
        client=client, first_name='Wayne', last_name='Davis',
        email='wayne@example.com', is_primary=True,
    )
    ticket = Ticket.objects.create(client=client, subject='S', description='D')

    contact = _resolve_ticket_contact(ticket)
    assert _greeting_name(client, contact) == 'Wayne'


@pytest.mark.django_db
def test_greeting_uses_contact_first_name_for_business_too():
    """Business mail goes to a company but still greets a person by first name."""
    from core.email_utils import _resolve_ticket_contact, _greeting_name

    client = Client.objects.create(name='Acme Co', client_type='business')
    Contact.objects.create(
        client=client, first_name='Jane', last_name='Smith',
        email='jane@acme.example', is_primary=True,
    )
    ticket = Ticket.objects.create(client=client, subject='S', description='D')

    contact = _resolve_ticket_contact(ticket)
    assert _greeting_name(client, contact) == 'Jane'


@pytest.mark.django_db
def test_greeting_falls_back_to_client_name_without_contact():
    from core.email_utils import _resolve_ticket_contact, _greeting_name

    client = Client.objects.create(name='Davis', client_type='residential')
    ticket = Ticket.objects.create(client=client, subject='S', description='D')

    contact = _resolve_ticket_contact(ticket)
    assert _greeting_name(client, contact) == 'Davis'


# ── reset_operational_data: wipes operational data, keeps config + superusers ──

@pytest.mark.django_db
def test_reset_dry_run_changes_nothing(client_obj, admin_user):
    from django.core.management import call_command

    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    WorkOrder.objects.create(client=client_obj, ticket=ticket)
    clients_before = Client.objects.count()

    call_command('reset_operational_data')  # no --confirm → dry run

    assert Client.objects.count() == clients_before
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


# ── Sidebar nav: order + admin-only gating ──────────────────────────────────

@pytest.mark.django_db
def test_sidebar_order_and_admin_gating(client, admin_user):
    # Tech (non-staff) does NOT see admin-only links.
    tech = User.objects.create_user(username='tech1', password='x', is_staff=False)
    client.force_login(tech)
    tech_body = client.get('/').content
    for hidden in (b'title="Queues"', b'title="Mileage"', b'title="Reports"'):
        assert hidden not in tech_body
    assert b'title="Tickets"' in tech_body          # core links still present
    assert b'title="Knowledge Base"' in tech_body

    # Admin sees them, and the top order is Dashboard, Tickets, Work Orders, Clients.
    client.force_login(admin_user)
    body = client.get('/').content
    for shown in (b'title="Queues"', b'title="Mileage"', b'title="Reports"'):
        assert shown in body
    order = [body.index(b'title="%s"' % t) for t in (b'Dashboard', b'Tickets', b'Work Orders', b'Clients')]
    assert order == sorted(order)


@pytest.mark.django_db
def test_tech_dashboard_shows_my_mileage(client, client_obj, admin_user):
    from core.models import Mileage
    tech = User.objects.create_user(username='tech2', password='x', is_staff=False)
    Mileage.objects.create(technician=tech, trip_date='2026-06-11', miles=12, purpose='Onsite call')

    client.force_login(tech)
    body = client.get('/').content
    assert b'>My Mileage</h2>' in body      # tech sees the card heading
    assert b'Onsite call' in body           # ...with their own entry

    client.force_login(admin_user)
    assert b'>My Mileage</h2>' not in client.get('/').content   # admin sees Team Workload instead


@pytest.mark.django_db
def test_mileage_list_scopes_to_own_for_techs(client, admin_user):
    from core.models import Mileage
    tech = User.objects.create_user(username='tech3', password='x', is_staff=False)
    Mileage.objects.create(technician=tech, trip_date='2026-06-11', miles=5, purpose='Tech trip')
    Mileage.objects.create(technician=admin_user, trip_date='2026-06-11', miles=99, purpose='Admin trip')

    client.force_login(tech)
    body = client.get('/mileage/').content
    assert b'Tech trip' in body          # own entry
    assert b'Admin trip' not in body     # must NOT see another tech's mileage

    client.force_login(admin_user)
    admin_body = client.get('/mileage/').content
    assert b'Admin trip' in admin_body and b'Tech trip' in admin_body  # admin sees all


@pytest.mark.django_db
def test_mileage_owner_can_delete_own_entry(client, admin_user):
    from core.models import Mileage
    tech = User.objects.create_user(username='miletech', password='x', is_staff=False)
    entry = Mileage.objects.create(technician=tech, trip_date='2026-06-11', miles=7)

    client.force_login(tech)
    resp = client.post(reverse('core:mileage_delete', args=[entry.pk]))
    assert resp.status_code == 302
    assert not Mileage.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_mileage_admin_can_delete_any_entry(client, admin_user):
    from core.models import Mileage
    tech = User.objects.create_user(username='miletech2', password='x', is_staff=False)
    entry = Mileage.objects.create(technician=tech, trip_date='2026-06-11', miles=7)

    client.force_login(admin_user)
    resp = client.post(reverse('core:mileage_delete', args=[entry.pk]))
    assert resp.status_code == 302
    assert not Mileage.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_mileage_tech_cannot_delete_others_entry(client, admin_user):
    from core.models import Mileage
    owner = User.objects.create_user(username='mileowner', password='x', is_staff=False)
    other = User.objects.create_user(username='mileother', password='x', is_staff=False)
    entry = Mileage.objects.create(technician=owner, trip_date='2026-06-11', miles=7)

    client.force_login(other)
    resp = client.post(reverse('core:mileage_delete', args=[entry.pk]))
    assert resp.status_code == 403
    assert Mileage.objects.filter(pk=entry.pk).exists()  # untouched


# ── Ticket scoping + escalation levels ──────────────────────────────────────

@pytest.mark.django_db
def test_ticket_scope_own_and_unclaimed_not_others(client_obj):
    from core.views import _scope_tickets_for
    a = User.objects.create_user(username='l1a', password='x', is_staff=False, level=1)
    b = User.objects.create_user(username='l1b', password='x', is_staff=False, level=1)
    Ticket.objects.create(client=client_obj, subject='mine', description='d', assigned_to=a)
    Ticket.objects.create(client=client_obj, subject='other', description='d', assigned_to=b)
    Ticket.objects.create(client=client_obj, subject='free', description='d')

    visible = set(_scope_tickets_for(Ticket.objects.all(), a).values_list('subject', flat=True))
    assert visible == {'mine', 'free'}     # never another tech's claimed ticket


@pytest.mark.django_db
def test_escalation_surfaces_up_and_keeps_owner(client_obj):
    from core.views import _scope_tickets_for
    l1 = User.objects.create_user(username='l1', password='x', is_staff=False, level=1)
    l2 = User.objects.create_user(username='l2', password='x', is_staff=False, level=2)
    t = Ticket.objects.create(client=client_obj, subject='hard', description='d', assigned_to=l1)

    # Before escalation, an L2 cannot see an L1's claimed ticket.
    assert 'hard' not in set(_scope_tickets_for(Ticket.objects.all(), l2).values_list('subject', flat=True))

    assert t.escalate() is True
    t.refresh_from_db()
    # After: L2 can see it to take over, but L1 STILL owns it (no black hole).
    assert 'hard' in set(_scope_tickets_for(Ticket.objects.all(), l2).values_list('subject', flat=True))
    assert t.assigned_to == l1
    assert t.escalation_level == 2
    assert t.escalation_pending is True


@pytest.mark.django_db
def test_escalate_view_then_higher_claim_transfers(client, client_obj):
    l1 = User.objects.create_user(username='l1c', password='x', is_staff=False, level=1)
    l2 = User.objects.create_user(username='l2c', password='x', is_staff=False, level=2)
    t = Ticket.objects.create(client=client_obj, subject='esc', description='d', assigned_to=l1)

    client.force_login(l1)
    client.post(f'/tickets/{t.pk}/escalate/')
    t.refresh_from_db()
    assert t.escalation_level == 2 and t.assigned_to == l1   # owner unchanged

    client.force_login(l2)
    client.post(f'/tickets/{t.pk}/assign/', {'claim': '1'})  # L2 takes it over
    t.refresh_from_db()
    assert t.assigned_to == l2
    assert t.escalation_pending is False                     # resolved once the right level holds it


@pytest.mark.django_db
def test_escalate_caps_at_max_level(client_obj):
    l1 = User.objects.create_user(username='l1d', password='x', is_staff=False, level=1)
    t = Ticket.objects.create(client=client_obj, subject='cap', description='d', assigned_to=l1, escalation_level=3)
    assert t.escalate() is False
    assert t.escalation_level == 3


@pytest.mark.django_db
def test_escalate_is_relative_to_owner_level(client_obj):
    # A ticket held by an L2 tech should jump to L3, not re-hit L2.
    l2 = User.objects.create_user(username='owner2', password='x', is_staff=False, level=2)
    t = Ticket.objects.create(client=client_obj, subject='r', description='d', assigned_to=l2)
    assert t.escalation_level == 1
    assert t.can_escalate is True
    assert t.escalate() is True
    assert t.escalation_level == 3
    assert t.can_escalate is False     # nothing above L3


@pytest.mark.django_db
def test_transfer_flags_new_to_you_and_clears_on_open(client, client_obj):
    a = User.objects.create_user(username='ta', password='x', is_staff=False, level=2)
    b = User.objects.create_user(username='tb', password='x', is_staff=False, level=2)
    t = Ticket.objects.create(client=client_obj, subject='handoff', description='d', assigned_to=a)

    client.force_login(a)
    client.post(f'/tickets/{t.pk}/assign/', {'assigned_to': str(b.pk)})  # transfer to B
    t.refresh_from_db()
    assert t.assigned_to == b and t.assignment_unseen is True

    client.force_login(b)
    assert b'New to you' in client.get('/tickets/').content   # badge on B's list
    client.get(f'/tickets/{t.pk}/')                            # B opens it
    t.refresh_from_db()
    assert t.assignment_unseen is False                        # flag cleared


@pytest.mark.django_db
def test_dashboard_surfaces_escalations_to_higher_level(client, client_obj):
    l2 = User.objects.create_user(username='dl2', password='x', is_staff=False, level=2)
    l3 = User.objects.create_user(username='dl3', password='x', is_staff=False, level=3)
    Ticket.objects.create(client=client_obj, subject='escd', description='d',
                          assigned_to=l2, escalation_level=3)

    client.force_login(l3)                       # L3 it was escalated to
    body = client.get('/').content
    assert b'Escalated to You' in body and b'escd' in body

    client.force_login(l2)                       # the holder doesn't see it as escalated-to-them
    assert b'Escalated to You' not in client.get('/').content


@pytest.mark.django_db
def test_tech_cannot_open_another_techs_ticket_by_url(client, client_obj):
    a = User.objects.create_user(username='da', password='x', is_staff=False, level=1)
    b = User.objects.create_user(username='db', password='x', is_staff=False, level=1)
    t = Ticket.objects.create(client=client_obj, subject='secret', description='d', assigned_to=b)
    client.force_login(a)
    assert client.get(f'/tickets/{t.pk}/').status_code == 404


@pytest.mark.django_db
def test_ticket_detail_renders_escalation_ui(client, client_obj, admin_user):
    t = Ticket.objects.create(client=client_obj, subject='render', description='d', assigned_to=admin_user)
    client.force_login(admin_user)
    resp = client.get(f'/tickets/{t.pk}/')
    assert resp.status_code == 200
    assert b'Escalate' in resp.content
    # Badge now shows the assigned tech's own level (admin_user defaults to L1),
    # not the ticket's escalation_level — see ticket_detail.html assigned-to row.
    assert b'L1' in resp.content


@pytest.mark.django_db
def test_reply_form_defaults(client, client_obj, admin_user):
    t = Ticket.objects.create(client=client_obj, subject='form', description='d', assigned_to=admin_user)
    client.force_login(admin_user)
    body = client.get(f'/tickets/{t.pk}/').content
    assert b"replyType: 'customer_visible'" in body   # Customer Visible is the default
    assert b'name="cc_mode"' in body                  # BCC/CC selector present
    assert b'rows="8"' in body                         # larger reply box
    assert b'mb_draft_' in body                        # draft autosave wired


# ── Internal tech-to-tech messaging + notifications ─────────────────────────

@pytest.mark.django_db
def test_wo_message_notifies_ticket_tech_not_sender(client, client_obj):
    from core.models import Notification, TicketReply
    bench = User.objects.create_user(username='bench', password='x', is_staff=False)
    ticket_tech = User.objects.create_user(username='tickettech', password='x', is_staff=False)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D',
                                   assigned_to=ticket_tech)
    wo = WorkOrder.objects.create(client=client_obj, ticket=ticket, assigned_to=bench)

    client.force_login(bench)
    resp = client.post(reverse('core:wo_message_tech', args=[wo.pk]),
                       {'content': 'Please call the client.'})
    assert resp.status_code == 200
    # Message lands as an internal note in the ticket thread.
    assert TicketReply.objects.filter(
        ticket=ticket, reply_type='internal', content='Please call the client.'
    ).count() == 1
    # Exactly one notification, to the ticket tech — never to the sender.
    assert Notification.objects.count() == 1
    assert Notification.objects.first().recipient == ticket_tech
    assert Notification.objects.filter(recipient=bench).count() == 0


@pytest.mark.django_db
def test_ticket_message_notifies_bench_tech(client, client_obj):
    from core.models import Notification
    bench = User.objects.create_user(username='bench2', password='x', is_staff=False)
    ticket_tech = User.objects.create_user(username='tt2', password='x', is_staff=False)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D',
                                   assigned_to=ticket_tech)
    WorkOrder.objects.create(client=client_obj, ticket=ticket, assigned_to=bench)

    client.force_login(ticket_tech)
    resp = client.post(reverse('core:ticket_message_tech', args=[ticket.pk]),
                       {'content': 'Go ahead.'})
    assert resp.status_code == 200
    assert Notification.objects.filter(recipient=bench).count() == 1
    assert Notification.objects.filter(recipient=ticket_tech).count() == 0


@pytest.mark.django_db
def test_message_falls_back_to_admins_when_no_counterpart(client, client_obj, admin_user):
    from core.models import Notification
    bench = User.objects.create_user(username='bench3', password='x', is_staff=False)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')  # unassigned
    wo = WorkOrder.objects.create(client=client_obj, ticket=ticket, assigned_to=bench)

    client.force_login(bench)
    client.post(reverse('core:wo_message_tech', args=[wo.pk]), {'content': 'Need contact.'})
    assert Notification.objects.filter(recipient=admin_user).count() == 1   # admin caught it
    assert Notification.objects.filter(recipient=bench).count() == 0        # sender never


@pytest.mark.django_db
def test_notification_count_fragment_shows_unread(client, client_obj):
    from core.models import Notification
    u = User.objects.create_user(username='u4', password='x')
    Notification.objects.create(recipient=u, text='a', kind='tech_message')
    Notification.objects.create(recipient=u, text='b', kind='tech_message',
                                is_read=True)
    client.force_login(u)
    body = client.get(reverse('core:notification_count')).content
    assert b'>1<' in body                       # one unread → badge shows 1
    assert u.notifications.filter(is_read=False).count() == 1


@pytest.mark.django_db
def test_opening_notification_marks_read_and_redirects(client, client_obj):
    from core.models import Notification
    from django.utils import timezone as _tz
    u = User.objects.create_user(username='u5', password='x')
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    n = Notification.objects.create(recipient=u, text='hi', kind='tech_message',
                                    ticket=ticket)
    client.force_login(u)
    resp = client.get(reverse('core:notification_open', args=[n.pk]))
    assert resp.status_code == 302
    assert resp.url == reverse('core:ticket_detail', args=[ticket.pk])
    n.refresh_from_db()
    assert n.is_read and n.read_at is not None

    # A different user cannot open someone else's notification.
    other = User.objects.create_user(username='u5b', password='x')
    client.force_login(other)
    assert client.get(reverse('core:notification_open', args=[n.pk])).status_code == 404


@pytest.mark.django_db
def test_standalone_wo_message_has_no_ticket_and_no_notification(client, client_obj):
    from core.models import Notification
    bench = User.objects.create_user(username='bench6', password='x')
    wo = WorkOrder.objects.create(client=client_obj, assigned_to=bench)  # no ticket
    client.force_login(bench)
    resp = client.post(reverse('core:wo_message_tech', args=[wo.pk]), {'content': 'x'})
    assert resp.status_code == 400
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_notification_ui_surfaces(client, client_obj):
    u = User.objects.create_user(username='uui', password='x', is_staff=True)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D',
                                   assigned_to=u)
    wo = WorkOrder.objects.create(client=client_obj, ticket=ticket, assigned_to=u)
    client.force_login(u)
    assert b'title="Notifications"' in client.get('/').content               # sidebar bell
    assert client.get(reverse('core:notifications')).status_code == 200      # center page
    assert b'Message Ticket Tech' in client.get(
        reverse('core:work_order_detail', args=[wo.pk])).content             # WO affordance
    assert b'Message Bench Tech' in client.get(
        reverse('core:ticket_detail', args=[ticket.pk])).content            # ticket affordance


@pytest.mark.django_db
def test_no_notification_when_sender_holds_both_roles(client, client_obj):
    """One person assigned to both the WO and the ticket → a message to the
    'other' role is a message to themselves: no notification, and crucially no
    spam to other admins."""
    from core.models import Notification
    me = User.objects.create_user(username='solo', password='x', is_staff=True)
    User.objects.create_user(username='otheradmin', password='x', is_staff=True)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D',
                                   assigned_to=me)
    wo = WorkOrder.objects.create(client=client_obj, ticket=ticket, assigned_to=me)
    client.force_login(me)
    resp = client.post(reverse('core:wo_message_tech', args=[wo.pk]),
                       {'content': 'note to self'})
    assert resp.status_code == 200
    assert Notification.objects.count() == 0


# ── System alerts: MB's own failures become a filterable ticket + admin bell ──

@pytest.mark.django_db
def test_create_system_alert_makes_ticket_and_notifies_admin(admin_user):
    from core.system_alerts import create_system_alert
    from core.models import Notification, Ticket

    t = create_system_alert('Backup failed', 'snapshot integrity error')
    assert t.client.name == 'System Alerts'
    assert t.source == 'system'
    assert t.status == 'new'
    assert Notification.objects.filter(
        ticket=t, kind='system_alert', recipient=admin_user).exists()

    # Dedupe: same subject within the window reuses the open ticket (no spam).
    t2 = create_system_alert('Backup failed', 'again')
    assert t2.pk == t.pk
    assert Ticket.objects.filter(subject='Backup failed').count() == 1

    # Forcing past dedupe opens a fresh ticket.
    t3 = create_system_alert('Backup failed', 'third', dedupe_minutes=0)
    assert t3.pk != t.pk


@pytest.mark.django_db
def test_500_logging_handler_opens_system_alert_ticket(admin_user):
    """An unhandled 500 (django.request ERROR with a traceback) becomes a ticket
    with the traceback in the body."""
    import logging
    import sys
    from core.log_handlers import SystemAlertHandler
    from core.models import Ticket

    handler = SystemAlertHandler()
    try:
        raise ValueError('boom in a view')
    except ValueError:
        rec = logging.LogRecord(
            'django.request', logging.ERROR, __file__, 0,
            'Internal Server Error: /tickets/', None, sys.exc_info(),
        )
    handler.emit(rec)

    t = Ticket.objects.filter(source='system', subject__startswith='500:').first()
    assert t is not None
    assert 'Internal Server Error: /tickets/' in t.subject
    assert 'boom in a view' in t.description


# ── Inbound: a client reply threads into its ticket, never spawns an orphan ──
# Regression guard for the Jun 14 bug: replies to a 'converted' ticket were
# falling through and creating brand-new tickets (TKT-00008/00009).

def _raw_reply_email(ticket_number, body='Thanks, that works.', from_email='wayne@davis.example'):
    import email.message
    msg = email.message.EmailMessage()
    msg['Subject'] = f'Re: [{ticket_number}] Fwd: 494793 You say my computer memory is full?'
    msg['From'] = f'Wayne Davis <{from_email}>'
    msg['To'] = 'support@example.com'
    msg['Message-ID'] = f'<reply-{ticket_number}-unique@davis.example>'
    msg.set_content(body)
    return msg.as_bytes()


@pytest.mark.django_db
def test_reply_to_converted_ticket_threads_instead_of_new_ticket(client_obj):
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D',
        ticket_number='TKT-20260610-0001', status='converted',
    )
    before = Ticket.objects.count()

    status, detail, result_ticket = _process_message(
        _raw_reply_email('TKT-20260610-0001'), site, verbosity=0)

    assert status == 'reply', f'Expected reply, got {status}: {detail}'
    assert Ticket.objects.count() == before, 'Reply must not create a new ticket.'
    ticket.refresh_from_db()
    assert ticket.replies.count() == 1
    assert ticket.status == 'converted', 'A converted ticket must stay converted.'
    assert ticket.needs_response is True


@pytest.mark.django_db
def test_reply_to_closed_ticket_within_window_flags_but_stays_closed(client_obj):
    """SLA Slice 4: MB used to auto-reopen a closed ticket on ANY reply — a
    client's "thanks!" or re-engaging after Mike closed a stale unanswered
    ticket became busywork. Now: thread in + flag, but stay closed; a human
    explicitly Reopens or Dismisses."""
    from django.utils import timezone
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D',
        ticket_number='TKT-20260610-0002', status='closed',
    )
    ticket.closed_at = timezone.now() - timezone.timedelta(days=1)
    ticket.save(update_fields=['closed_at'])
    before = Ticket.objects.count()

    status, detail, _ = _process_message(
        _raw_reply_email('TKT-20260610-0002'), site, verbosity=0)

    assert status == 'reply_flagged', f'Expected reply_flagged, got {status}: {detail}'
    assert Ticket.objects.count() == before, 'Must not create a new ticket within the reopen window.'
    ticket.refresh_from_db()
    assert ticket.replies.count() == 1
    assert ticket.status == 'closed', 'A reply within the reopen window must NOT reopen the ticket.'
    assert ticket.needs_response is True


@pytest.mark.django_db
def test_reply_to_resolved_ticket_within_window_flags_but_stays_resolved(client_obj):
    """Same rule for 'resolved' as 'closed' — both are CLOSED_AT_STATUSES."""
    from django.utils import timezone
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D',
        ticket_number='TKT-20260610-0009', status='resolved',
    )
    ticket.closed_at = timezone.now()
    ticket.save(update_fields=['closed_at'])

    status, detail, _ = _process_message(
        _raw_reply_email('TKT-20260610-0009'), site, verbosity=0)

    assert status == 'reply_flagged'
    ticket.refresh_from_db()
    assert ticket.status == 'resolved'
    assert ticket.needs_response is True


@pytest.mark.django_db
def test_reply_to_closed_ticket_past_reopen_window_creates_linked_ticket(client_obj):
    """Past the configured reopen window, a reply to a long-closed ticket starts
    a NEW ticket (the old context is stale) but links it to the old one so the
    history isn't lost."""
    from django.utils import timezone
    from core.management.commands.fetch_inbound_email import _process_message
    from core.models import TicketLink
    site = SiteSettings.get()
    site.ticket_reopen_window_days = 14
    site.save(update_fields=['ticket_reopen_window_days'])
    old_ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D',
        ticket_number='TKT-20260610-0003', status='closed',
    )
    old_ticket.closed_at = timezone.now() - timezone.timedelta(days=30)
    old_ticket.save(update_fields=['closed_at'])
    before = Ticket.objects.count()

    status, detail, new_ticket = _process_message(
        _raw_reply_email('TKT-20260610-0003'), site, verbosity=0)

    assert status == 'new_ticket_linked', f'Expected new_ticket_linked, got {status}: {detail}'
    assert Ticket.objects.count() == before + 1
    assert new_ticket.pk != old_ticket.pk
    old_ticket.refresh_from_db()
    assert old_ticket.status == 'closed', 'The old ticket itself must not be touched.'
    assert TicketLink.objects.filter(ticket_a=old_ticket, ticket_b=new_ticket).exists()


@pytest.mark.django_db
def test_reply_to_closed_ticket_null_closed_at_stays_within_window(client_obj):
    """A closed ticket with no closed_at (pre-Slice-4 historical data, since
    this is forward-only with no backfill) is treated as still within the
    reopen window — the safer default vs. silently spawning a disconnected
    new ticket due to missing data."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D',
        ticket_number='TKT-20260610-0004', status='closed',
    )
    assert ticket.closed_at is None

    status, detail, _ = _process_message(
        _raw_reply_email('TKT-20260610-0004'), site, verbosity=0)

    assert status == 'reply_flagged'
    ticket.refresh_from_db()
    assert ticket.status == 'closed'


@pytest.mark.django_db
def test_reply_to_waiting_on_customer_ticket_still_reopens(client_obj):
    """Unchanged by Slice 4 — waiting_on_customer is not a CLOSED_AT_STATUS;
    the client responding is exactly what that status was waiting for."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D',
        ticket_number='TKT-20260610-0005', status='waiting_on_customer',
    )

    status, detail, _ = _process_message(
        _raw_reply_email('TKT-20260610-0005'), site, verbosity=0)

    assert status == 'reply'
    ticket.refresh_from_db()
    assert ticket.status == 'open'
    assert ticket.needs_response is True


@pytest.mark.django_db
def test_apply_status_change_stamps_and_clears_closed_at():
    """Ticket.apply_status_change: stamps closed_at entering resolved/closed,
    clears it leaving them, and does NOT re-stamp resolved<->closed (still
    'done', just a different flavor)."""
    from django.utils import timezone
    client_obj = Client.objects.create(name='Closed-At Co')
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    assert ticket.closed_at is None

    ticket.apply_status_change('closed')
    assert ticket.status == 'closed'
    assert ticket.closed_at is not None
    first_stamp = ticket.closed_at

    ticket.apply_status_change('resolved')
    assert ticket.closed_at == first_stamp, 'resolved<->closed must not re-stamp closed_at.'

    ticket.apply_status_change('open')
    assert ticket.closed_at is None, 'Leaving a CLOSED_AT_STATUS must clear closed_at.'


@pytest.mark.django_db
def test_ticket_close_view_stamps_closed_at(admin_user, client, client_obj):
    """The one-click Resolve shortcut (TicketCloseView) stamps closed_at too,
    not just the full edit form / quick-status dropdown."""
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_close', args=[ticket.pk]))
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == 'resolved'
    assert ticket.closed_at is not None


@pytest.mark.django_db
def test_ticket_edit_form_stamps_closed_at_and_true_old_status_for_email(admin_user, client, client_obj):
    """Regression: TicketUpdateView.form_valid used to read `self.object.status`
    for old_status AFTER Django's _post_clean() had already mutated it to the
    NEW status in memory (same class of bug as the Slice 2 client caching
    issue) — so the status-changed email condition was always false and
    closed_at would have been stamped off the wrong 'old' value. Both are now
    read from a fresh DB query before the mutation."""
    from unittest.mock import patch
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D', status='open')
    client.force_login(admin_user)

    with patch('core.email_utils.send_ticket_email') as mock_send:
        resp = client.post(reverse('core:ticket_edit', args=[ticket.pk]), {
            'client': client_obj.pk, 'subject': 'S', 'description': 'D',
            'source': 'phone', 'status': 'closed',
        })
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == 'closed'
    assert ticket.closed_at is not None
    mock_send.assert_any_call('status_changed', ticket, {'old_status': 'open'})


@pytest.mark.django_db
def test_ticket_reopen_view_one_click(admin_user, client, client_obj):
    """The Reopen button on a closed+flagged ticket's needs_response banner —
    one click, no note required (Dismiss is the one that requires a note)."""
    from django.utils import timezone
    ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D', status='closed', needs_response=True,
    )
    ticket.closed_at = timezone.now()
    ticket.save(update_fields=['closed_at'])

    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_reopen', args=[ticket.pk]))
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == 'open'
    assert ticket.closed_at is None
    assert ticket.needs_response is True, 'Reopen must not silently clear the flag — replying does that.'


@pytest.mark.django_db
def test_inbound_settings_save_persists_reopen_window(admin_user, client):
    """Settings → Inbound Email persists the new reopen-window field via the
    existing generic settings POST dispatcher."""
    client.force_login(admin_user)
    resp = client.post(reverse('core:settings'), {
        'tab': 'inbound', 'inbound-ticket_reopen_window_days': '21',
        'inbound-inbound_protocol': 'imap', 'inbound-inbound_port': '993',
        'inbound-inbound_folder': 'INBOX',
    })
    assert resp.status_code == 302
    site = SiteSettings.get()
    assert site.ticket_reopen_window_days == 21


# ── Inbound: the everyday paths — new ticket, reply threading, dedup, routing ─
# These cover the common cases the converted/closed regression tests above don't:
# a fresh email becomes a ticket, a reply to a live ticket threads, the same
# message is only processed once, and senders resolve to the right client/contact.

def _raw_new_email(subject='My computer won\'t boot',
                   body='Please help, it just beeps.',
                   from_email='wayne@davis.example',
                   from_name='Wayne Davis',
                   message_id='<fresh-001@davis.example>'):
    import email.message
    msg = email.message.EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f'{from_name} <{from_email}>'
    msg['To'] = 'support@example.com'
    if message_id:
        msg['Message-ID'] = message_id
    msg.set_content(body)
    return msg.as_bytes()


@pytest.mark.django_db
def test_fresh_email_creates_new_ticket():
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    before = Ticket.objects.count()

    status, detail, ticket = _process_message(_raw_new_email(), site, verbosity=0)

    assert status == 'new_ticket', f'Expected new_ticket, got {status}: {detail}'
    assert Ticket.objects.count() == before + 1
    assert ticket.status == 'new'
    assert ticket.source == 'email'
    assert ticket.contact is not None
    assert ticket.contact.email == 'wayne@davis.example'
    # An unknown sender is parked in the Unsorted bucket for triage.
    assert ticket.client.is_unsorted is True


@pytest.mark.django_db
def test_reply_to_open_ticket_threads_and_keeps_open(client_obj):
    """The everyday case: a reply to a live (open) ticket threads in, flags
    needs_response, and does NOT change the status away from open."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D',
        ticket_number='TKT-20260610-0050', status='open',
    )
    before = Ticket.objects.count()

    status, detail, _ = _process_message(
        _raw_reply_email('TKT-20260610-0050'), site, verbosity=0)

    assert status == 'reply', f'Expected reply, got {status}: {detail}'
    assert Ticket.objects.count() == before
    ticket.refresh_from_db()
    assert ticket.replies.count() == 1
    assert ticket.status == 'open'
    assert ticket.needs_response is True


@pytest.mark.django_db
def test_inbound_log_message_id_unique_constraint():
    """Structural guard: the DB refuses a second log row with the same non-empty
    Message-ID — this is what makes dedup atomic / race-proof. Empty Message-IDs
    are exempt, since unknowns can't be deduped."""
    from core.models import InboundEmailLog
    from django.db import IntegrityError, transaction
    InboundEmailLog.objects.create(message_id='<dup@x>', status='new_ticket')
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InboundEmailLog.objects.create(message_id='<dup@x>', status='new_ticket')
    InboundEmailLog.objects.create(message_id='', status='error')
    InboundEmailLog.objects.create(message_id='', status='error')
    assert InboundEmailLog.objects.filter(message_id='').count() == 2


@pytest.mark.django_db
def test_same_email_fetched_twice_creates_one_ticket(monkeypatch):
    """Regression for the duplicate-ticket bug: two fetch passes over the SAME
    message (overlapping runners or a re-fetch) must yield exactly ONE ticket.
    The atomic Message-ID claim, backed by the unique constraint, guarantees it."""
    from core.management.commands.fetch_inbound_email import Command
    from core.models import InboundEmailLog
    from django.core.management import call_command
    site = SiteSettings.get()
    site.inbound_email_enabled = True
    site.inbound_protocol = 'pop3'
    site.inbound_host = 'mail.example'
    site.inbound_username = 'support@example'
    site.save()

    raw = _raw_new_email(message_id='<race-1@davis.example>')
    # Same message returned twice in one batch == the worst case of a race.
    monkeypatch.setattr(Command, '_fetch_pop3', lambda self, s, d, v: [raw, raw])

    before = Ticket.objects.count()
    call_command('fetch_inbound_email', verbosity=0)

    assert Ticket.objects.count() == before + 1, 'The same email must create only one ticket.'
    assert InboundEmailLog.objects.filter(
        message_id='<race-1@davis.example>').count() == 1

@pytest.mark.django_db
def test_returning_sender_reuses_existing_contact(client_obj):
    """A known sender (matched by email) routes to their existing client/contact
    rather than spawning a duplicate client."""
    from core.management.commands.fetch_inbound_email import _process_message
    contact = Contact.objects.create(
        client=client_obj, first_name='Wayne', last_name='Davis',
        email='wayne@davis.example', is_primary=True,
    )
    site = SiteSettings.get()
    clients_before = Client.objects.count()

    status, _, ticket = _process_message(_raw_new_email(), site, verbosity=0)

    assert status == 'new_ticket'
    assert Client.objects.count() == clients_before, 'Known sender must not create a new client.'
    assert ticket.contact_id == contact.id
    assert ticket.client_id == client_obj.id


@pytest.mark.django_db
def test_unmatched_senders_all_land_in_one_unsorted_bucket():
    """Unknown senders are parked under the single 'Unsorted/Unverified' bucket
    for triage — never auto-created as junk named clients."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()

    _, _, t1 = _process_message(_raw_new_email(
        from_email='alice@acmecorp.example', from_name='Alice A',
        message_id='<biz-1@acmecorp.example>'), site, verbosity=0)
    _, _, t2 = _process_message(_raw_new_email(
        from_email='someone@gmail.com', from_name='Jane Doe',
        message_id='<free-1@gmail.com>'), site, verbosity=0)

    assert t1.client.is_unsorted and t2.client.is_unsorted
    assert t1.client_id == t2.client_id, 'There is exactly one Unsorted bucket.'
    assert Client.objects.filter(is_unsorted=True).count() == 1
    # No junk named clients from the senders' names/domains.
    assert not Client.objects.filter(name__in=['acmecorp.example', 'gmail.com', 'Jane Doe']).exists()
    # The real contacts are still recorded under the bucket for onboarding/reply.
    assert t1.contact.email == 'alice@acmecorp.example'
    assert t2.contact.email == 'someone@gmail.com'


@pytest.mark.django_db
def test_inbound_default_client_name_overrides_unsorted_bucket():
    """An admin-configured catch-all client still wins over the Unsorted bucket."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    site.inbound_default_client_name = 'Catch-All Co'
    site.save(update_fields=['inbound_default_client_name'])

    _, _, ticket = _process_message(_raw_new_email(), site, verbosity=0)

    assert ticket.client.name == 'Catch-All Co'
    assert ticket.client.is_unsorted is False


@pytest.mark.django_db
def test_get_unsorted_is_idempotent_and_unique():
    # Migration 0054 seeds exactly one bucket; get_unsorted() reuses it.
    a = Client.get_unsorted()
    b = Client.get_unsorted()
    assert a.id == b.id
    assert Client.objects.filter(is_unsorted=True).count() == 1


@pytest.mark.django_db
def test_unsorted_bucket_cannot_be_deleted(client, admin_user):
    bucket = Client.get_unsorted()
    client.force_login(admin_user)
    resp = client.post(reverse('core:client_delete', args=[bucket.pk]),
                       {'confirm_name': bucket.name})
    assert Client.objects.filter(pk=bucket.pk).exists(), 'Triage bucket must survive a delete attempt.'


@pytest.mark.django_db
def test_owner_dashboard_renders_business_tiles(client, admin_user):
    # The owner dashboard leads with business metrics, not the old triage/attention
    # rail. Triage stays reachable via the ticket list (?triage=1), tested elsewhere.
    bucket = Client.get_unsorted()
    Ticket.objects.create(client=bucket, subject='unsorted', description='d',
                          ticket_number='TKT-D-1', status='new')
    client.force_login(admin_user)
    resp = client.get(reverse('core:dashboard'))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'Ready to bill' in body and 'Outstanding invoices' in body


@pytest.mark.django_db
def test_triage_filter_lists_only_unsorted_tickets(client, client_obj, admin_user):
    bucket = Client.get_unsorted()
    Ticket.objects.create(client=bucket, subject='unsorted one', description='d',
                          ticket_number='TKT-T-1', status='new')
    Ticket.objects.create(client=client_obj, subject='normal one', description='d',
                          ticket_number='TKT-T-2', status='new')
    client.force_login(admin_user)
    resp = client.get(reverse('core:ticket_list') + '?triage=1')
    body = resp.content.decode()
    assert 'unsorted one' in body
    assert 'normal one' not in body


@pytest.mark.django_db
def test_blocked_sender_creates_no_ticket():
    from core.management.commands.fetch_inbound_email import _process_message
    from core.models import BlockedSender
    BlockedSender.objects.create(pattern='*@davis.example')
    site = SiteSettings.get()
    before = Ticket.objects.count()

    status, detail, ticket = _process_message(_raw_new_email(), site, verbosity=0)

    assert status == 'error', f'Expected error (blocked), got {status}: {detail}'
    assert ticket is None
    assert Ticket.objects.count() == before


# ── Inbound: T2 / Helpdesk Buttons relay is unwrapped to the real end user ───
# Button tickets arrive from the no-reply relay email-connector@tier2tickets.com
# with the real sender in a forwarded-message header in the body. MB must
# attribute to that real contact (so replies route to them), not the relay.

def _raw_t2_email(real_name='Jane Doe', real_email='jane.doe@example.com',
                  subject='Fwd: E.2YVLMWK Test-2', message_id='<t2-1@tier2tickets.com>',
                  include_from=True):
    import email.message
    forwarded_from = f'From: "{real_name}" <{real_email}>\n' if include_from else ''
    body = (
        '---------- Forwarded message ---------\n'
        f'{forwarded_from}'
        'Date: Fri, Jun 19, 2026 at 04:37 PM\n'
        'Subject: Test-2\n'
        'To: "Button Ticket" <email-connector@tier2tickets.com>\n\n'
        'https://account.helpdeskbuttons.com/pressView.php?pressID=abc123\n\n'
        '[message]\nTest-2\n'
    )
    msg = email.message.EmailMessage()
    msg['Subject'] = subject
    msg['From'] = '"Button Ticket" <email-connector@tier2tickets.com>'
    msg['To'] = 'support@example.com'
    msg['Message-ID'] = message_id
    msg.set_content(body)
    return msg.as_bytes()


def test_extract_forwarded_sender_parses_and_handles_missing():
    from core.management.commands.fetch_inbound_email import _extract_forwarded_sender
    body = '--- Forwarded message ---\nFrom: "Jane Doe" <jane.doe@example.com>\nDate: x\n'
    assert _extract_forwarded_sender(body) == ('Jane Doe', 'jane.doe@example.com')
    assert _extract_forwarded_sender('no headers here') == (None, None)
    assert _extract_forwarded_sender('') == (None, None)


@pytest.mark.django_db
def test_t2_email_maps_to_existing_contact_not_relay(client_obj):
    """A button ticket whose forwarded sender is a known contact files under that
    contact's client — never under the tier2tickets relay."""
    from core.management.commands.fetch_inbound_email import _process_message
    contact = Contact.objects.create(
        client=client_obj, first_name='Jane', last_name='Doe',
        email='jane.doe@example.com', is_primary=True,
    )
    site = SiteSettings.get()

    status, _, ticket = _process_message(_raw_t2_email(), site, verbosity=0)

    assert status == 'new_ticket'
    assert ticket.contact_id == contact.id
    assert ticket.client_id == client_obj.id
    assert not Client.objects.filter(name__icontains='tier2tickets').exists(), \
        'A button ticket must never create a tier2tickets relay client.'


@pytest.mark.django_db
def test_t2_email_unknown_sender_lands_in_unsorted_bucket():
    """An unknown button-ticket sender is parked in the Unsorted bucket under the
    REAL forwarded address — never under the tier2tickets relay."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()

    status, _, ticket = _process_message(_raw_t2_email(), site, verbosity=0)

    assert status == 'new_ticket'
    assert ticket.contact.email == 'jane.doe@example.com'
    assert ticket.client.is_unsorted is True
    assert not Client.objects.filter(name__icontains='tier2tickets').exists()


@pytest.mark.django_db
def test_t2_email_unparseable_falls_back_and_logs(caplog):
    """If the forwarded sender can't be parsed, attribute to the relay (current
    behavior) but log a warning so the bad attribution is visible — fail loud."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()

    with caplog.at_level('WARNING', logger='core'):
        status, _, ticket = _process_message(
            _raw_t2_email(include_from=False), site, verbosity=0)

    assert status == 'new_ticket'
    assert ticket.contact.email == 'email-connector@tier2tickets.com'
    assert any('no parseable forwarded sender' in r.message for r in caplog.records)


@pytest.mark.django_db
def test_normal_email_is_unaffected_by_t2_unwrap(client_obj):
    """Regression guard: a normal (non-T2) email still attributes to its own
    envelope From, even if its body happens to contain a 'From:' line."""
    from core.management.commands.fetch_inbound_email import _process_message
    contact = Contact.objects.create(
        client=client_obj, first_name='Wayne', last_name='Davis',
        email='wayne@davis.example', is_primary=True,
    )
    site = SiteSettings.get()
    # body contains a quoted 'From:' line that must be ignored for a non-relay sender
    status, _, ticket = _process_message(
        _raw_new_email(body='Earlier you wrote:\nFrom: someone@else.example\nthanks'),
        site, verbosity=0)

    assert status == 'new_ticket'
    assert ticket.contact_id == contact.id, 'Non-T2 email must use its envelope From.'


# ── HTML-only inbound email (RMM alerts) renders as readable text, not markup ─

@pytest.mark.django_db
def test_html_only_email_becomes_readable_text():
    """An HTML-only alert (e.g. MSP360 RMM) must not store raw HTML markup as the
    ticket description — it should be converted to plain text."""
    import email.message
    from core.management.commands.fetch_inbound_email import _process_message

    site = SiteSettings.get()
    html = (
        '<html><body>'
        '<style>td { color: #242c3b; }</style>'
        '<table><tr><td>RMM Alert</td><td>06/15/2026 09:07:37</td></tr></table>'
        '<table><tr><td>Product version:</td><td>2.5.0.67</td></tr>'
        '<tr><td>Provider:</td><td>Shamrock Computer Services, LLC</td></tr></table>'
        '</body></html>'
    )
    msg = email.message.EmailMessage()
    msg['Subject'] = 'RMM Alert - GENELAPTOP: High Memory Usage'
    msg['From'] = 'MSP360 <alerts@msp360.example>'
    msg['To'] = 'support@example.com'
    msg['Message-ID'] = '<rmm-alert-1@msp360.example>'
    msg.set_content('   ')                     # empty/whitespace plain part (as RMM alerts send)
    msg.add_alternative(html, subtype='html')  # ...then make it multipart/alternative

    status, detail, ticket = _process_message(msg.as_bytes(), site, verbosity=0)

    assert status == 'new_ticket', f'{status}: {detail}'
    desc = ticket.description
    assert '<td' not in desc and '<table' not in desc, 'Raw HTML leaked into description'
    assert 'border-collapse' not in desc and 'color:' not in desc, 'CSS leaked into description'
    assert 'RMM Alert' in desc
    # A table row's key and value must stay on ONE line, not split across lines.
    assert 'Product version: 2.5.0.67' in desc, \
        f'Key/value should be on one line. Got:\n{desc}'


@pytest.mark.django_db
def test_html_only_singlepart_email_is_converted():
    """A single-part text/html message (no plain alternative at all) is still
    converted to text rather than stored as markup."""
    import email.message
    from core.management.commands.fetch_inbound_email import _process_message

    site = SiteSettings.get()
    msg = email.message.EmailMessage()
    msg['Subject'] = 'HTML only alert'
    msg['From'] = 'alerts@msp360.example'
    msg['To'] = 'support@example.com'
    msg['Message-ID'] = '<rmm-alert-2@msp360.example>'
    msg.set_content('<p>A problem occurred on <b>GeneLaptop</b>: High Memory Usage</p>',
                    subtype='html')

    status, detail, ticket = _process_message(msg.as_bytes(), site, verbosity=0)

    assert status == 'new_ticket', f'{status}: {detail}'
    assert '<p>' not in ticket.description and '<b>' not in ticket.description
    assert 'A problem occurred on GeneLaptop: High Memory Usage' in ticket.description


# ── clean_html_bodies: retroactively convert stored raw HTML to plain text ────

@pytest.mark.django_db
def test_clean_html_bodies_converts_only_html(client_obj):
    from django.core.management import call_command
    from core.models import Ticket

    htmlish = Ticket.objects.create(
        client=client_obj, ticket_number='TKT-HTML-1', status='new',
        subject='RMM Alert',
        description='<html><body><table><tr><td>Product version:</td>'
                    '<td>2.5.0.67</td></tr></table></body></html>',
    )
    plain = Ticket.objects.create(
        client=client_obj, ticket_number='TKT-PLAIN-1', status='new',
        subject='Normal', description='CPU load < 5 is fine. No tags here.',
    )

    call_command('clean_html_bodies', verbosity=0)          # dry run: no change
    htmlish.refresh_from_db()
    assert '<td>' in htmlish.description

    call_command('clean_html_bodies', '--apply', verbosity=0)
    htmlish.refresh_from_db()
    plain.refresh_from_db()
    assert '<td>' not in htmlish.description and '<table' not in htmlish.description
    assert 'Product version:' in htmlish.description and '2.5.0.67' in htmlish.description
    assert plain.description == 'CPU load < 5 is fine. No tags here.'


# ---------------------------------------------------------------------------
# Logo upload size guard (login_logo / site_logo branding)
# ---------------------------------------------------------------------------

def _png_upload(w, h, name='logo.png'):
    import io
    from PIL import Image
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    Image.new('RGB', (w, h), 'white').save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def test_oversized_logo_rejected():
    from django import forms
    from core.forms import validate_logo_upload, MAX_LOGO_DIMENSION
    with pytest.raises(forms.ValidationError):
        validate_logo_upload(_png_upload(MAX_LOGO_DIMENSION + 500, 100))


def test_reasonable_logo_accepted():
    from core.forms import validate_logo_upload
    f = _png_upload(1254, 1254)
    assert validate_logo_upload(f) is f


def test_non_upload_value_passes_through():
    # an existing stored file (or None) is not a fresh upload — must pass untouched
    from core.forms import validate_logo_upload
    assert validate_logo_upload(None) is None


# ---------------------------------------------------------------------------
# Repair report must not crash on custom Work Performed entries (no labor_item)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_repair_report_prints_with_custom_labor_entry(client, client_obj, admin_user):
    # A custom labor line has no catalog_item; the print report groups by
    # category and must not 500 on it (groups under "Other"). Regression guard.
    wo = WorkOrder.objects.create(client=client_obj)
    wo.line_items.create(kind='labor', description='Reseated RAM', notes='Was loose')
    client.force_login(admin_user)
    resp = client.get(reverse('core:work_order_print', args=[wo.pk]))
    assert resp.status_code == 200
    assert b'Reseated RAM' in resp.content


# ---------------------------------------------------------------------------
# MFA reset hardening — audit log, flag gate, break-glass CLI command
# ---------------------------------------------------------------------------

def _enroll_totp(user):
    """Give a user a confirmed TOTP device so a reset has something to clear."""
    from django_otp.plugins.otp_totp.models import TOTPDevice
    return TOTPDevice.objects.create(user=user, name='default', confirmed=True)


@pytest.mark.django_db
def test_web_reset_clears_devices_and_writes_log(client, admin_user):
    from django_otp import devices_for_user
    from core.models import MFAResetLog
    target = User.objects.create_user(username='lostphone', password='x')
    _enroll_totp(target)
    assert list(devices_for_user(target))  # enrolled

    client.force_login(admin_user)
    resp = client.post(reverse('core:user_mfa_reset', args=[target.pk]))

    assert resp.status_code == 302
    assert not list(devices_for_user(target)), 'Reset must clear all OTP devices.'
    log = MFAResetLog.objects.get(target=target)
    assert log.actor == admin_user
    assert log.source == 'web'


@pytest.mark.django_db
def test_web_reset_denied_without_flag(client):
    """A non-admin without can_reset_user_mfa is forbidden and writes no log."""
    from core.models import Role, MFAResetLog
    role = Role.objects.create(name='Plain Tech')  # all flags default False
    actor = User.objects.create_user(username='plain', password='x',
                                     is_staff=False, role_obj=role)
    target = User.objects.create_user(username='victim', password='x')
    _enroll_totp(target)

    client.force_login(actor)
    resp = client.post(reverse('core:user_mfa_reset', args=[target.pk]))

    assert resp.status_code == 403
    assert not MFAResetLog.objects.filter(target=target).exists()


@pytest.mark.django_db
def test_web_reset_allowed_with_flag_only(client):
    """A delegated non-admin holding only can_reset_user_mfa may reset."""
    from django_otp import devices_for_user
    from core.models import Role
    role = Role.objects.create(name='Helpdesk', can_reset_user_mfa=True)
    actor = User.objects.create_user(username='helpdesk', password='x',
                                     is_staff=False, role_obj=role)
    target = User.objects.create_user(username='locked', password='x')
    _enroll_totp(target)

    client.force_login(actor)
    resp = client.post(reverse('core:user_mfa_reset', args=[target.pk]))

    assert resp.status_code == 302
    assert not list(devices_for_user(target))


@pytest.mark.django_db
def test_cli_reset_clears_devices_and_logs_shell_identity(monkeypatch):
    from django.core.management import call_command
    from django_otp import devices_for_user
    from core.models import MFAResetLog
    target = User.objects.create_user(username='soleadmin', password='x')
    _enroll_totp(target)

    monkeypatch.setattr('getpass.getuser', lambda: 'admin-user')
    monkeypatch.setenv('SSH_CONNECTION', '192.0.2.5 51234 192.0.2.82 22')

    call_command('reset_mfa', 'soleadmin', '--note', 'lost authenticator')

    assert not list(devices_for_user(target)), 'CLI reset must clear devices.'
    log = MFAResetLog.objects.get(target=target)
    assert log.actor is None            # no authenticated web user on the CLI path
    assert log.source == 'cli'
    # Highest-risk path stays traceable: stamp who/where, not an anonymous null.
    assert 'admin-user' in log.note
    assert '192.0.2.5' in log.note
    assert 'lost authenticator' in log.note


@pytest.mark.django_db
def test_cli_reset_unknown_user_errors():
    from django.core.management import call_command
    from django.core.management.base import CommandError
    with pytest.raises(CommandError):
        call_command('reset_mfa', 'nobody-here')


# ── Ticket form device dropdown is scoped to the selected client ────────────

@pytest.mark.django_db
def test_ticket_form_device_queryset_scoped_to_client(client_obj):
    """Onboarding an unsorted ticket: the device dropdown must only offer the
    selected client's devices, not every device in the system."""
    from core.forms import TicketForm
    other = Client.objects.create(name='Other Co')
    mine = Device.objects.create(client=client_obj, name='My Laptop')
    theirs = Device.objects.create(client=other, name='Their Laptop')

    form = TicketForm(data={'client': client_obj.pk})
    device_ids = set(form.fields['device'].queryset.values_list('pk', flat=True))
    assert mine.pk in device_ids
    assert theirs.pk not in device_ids


@pytest.mark.django_db
def test_contacts_by_client_returns_scoped_device_options(client, client_obj, admin_user):
    """The HTMX cascade returns an out-of-band device <select> narrowed to the
    chosen client's devices."""
    other = Client.objects.create(name='Other Co')
    mine = Device.objects.create(client=client_obj, name='My Laptop')
    theirs = Device.objects.create(client=other, name='Their Laptop')

    client.force_login(admin_user)
    resp = client.get(reverse('core:ticket_contacts_by_client') + f'?client={client_obj.pk}')
    body = resp.content.decode()
    assert 'hx-swap-oob="true"' in body
    assert f'<option value="{mine.pk}">' in body
    assert f'<option value="{theirs.pk}">' not in body


# ── Device hardware spec fields persist ─────────────────────────────────────

@pytest.mark.django_db
def test_device_form_saves_hardware_specs(client_obj):
    from core.forms import DeviceForm
    form = DeviceForm(data={
        'client': client_obj.pk,
        'name': 'Spec Box',
        'device_type': 'desktop',
        'cpu': 'Intel Core i7-1185G7',
        'ram': '16 GB',
        'storage': '512 GB SSD',
        'is_active': True,
    })
    assert form.is_valid(), form.errors
    device = form.save()
    device.refresh_from_db()
    assert device.cpu == 'Intel Core i7-1185G7'
    assert device.ram == '16 GB'
    assert device.storage == '512 GB SSD'


# ── WO snapshots device specs on creation and syncs edits back ──────────────

@pytest.mark.django_db
def test_workorder_snapshots_device_specs_on_create(client_obj):
    device = Device.objects.create(
        client=client_obj, name='Box', cpu='Ryzen 5', ram='8 GB', storage='256 GB SSD',
    )
    wo = WorkOrder.objects.create(client=client_obj, device=device)
    assert wo.cpu == 'Ryzen 5'
    assert wo.ram == '8 GB'
    assert wo.storage == '256 GB SSD'


@pytest.mark.django_db
def test_workorder_spec_edit_syncs_back_to_device(client_obj):
    device = Device.objects.create(
        client=client_obj, name='Box', cpu='Ryzen 5', ram='8 GB', storage='256 GB SSD',
    )
    wo = WorkOrder.objects.create(client=client_obj, device=device)
    # Tech upgrades the RAM during the repair
    wo.ram = '16 GB'
    wo.save()
    changed = wo.sync_specs_to_device()
    device.refresh_from_db()
    assert 'ram' in changed
    assert device.ram == '16 GB'
    # Untouched specs are unaffected
    assert device.cpu == 'Ryzen 5'


@pytest.mark.django_db
def test_workorder_later_device_spec_change_does_not_alter_past_wo(client_obj):
    device = Device.objects.create(client=client_obj, name='Box', ram='8 GB')
    wo = WorkOrder.objects.create(client=client_obj, device=device)
    # Device is later upgraded outside this WO
    device.ram = '32 GB'
    device.save()
    wo.refresh_from_db()
    # The WO keeps its as-serviced snapshot
    assert wo.ram == '8 GB'


# ── Attachment security: storage location, access control, inbound parity ───

def _make_attachment(obj, data=b'hello', filename='note.txt'):
    from django.contrib.contenttypes.models import ContentType
    from django.core.files.base import ContentFile
    from core.models import Attachment
    a = Attachment(
        content_type=ContentType.objects.get_for_model(obj),
        object_id=obj.pk,
        original_filename=filename,
        size_bytes=len(data),
    )
    a.file.save(filename, ContentFile(data), save=True)
    return a


@pytest.mark.django_db
def test_attachment_stored_outside_media_root(client_obj):
    from django.conf import settings
    ticket = Ticket.objects.create(client=client_obj, subject='s', description='d',
                                   ticket_number='TKT-ATT-1', status='new')
    a = _make_attachment(ticket)
    path = a.file.path
    assert str(settings.PRIVATE_MEDIA_ROOT) in path
    assert str(settings.MEDIA_ROOT) not in path


@pytest.mark.django_db
def test_attachment_download_denied_for_unauthorized_tech(client, client_obj):
    """A non-admin tech must not be able to download an attachment on a ticket
    they can't see (closes the IDOR alongside the nginx fix)."""
    owner = User.objects.create_user(username='att-owner', password='x', is_staff=False, level=1)
    intruder = User.objects.create_user(username='att-intruder', password='x', is_staff=False, level=1)
    ticket = Ticket.objects.create(client=client_obj, subject='s', description='d',
                                   ticket_number='TKT-ATT-2', status='open', assigned_to=owner)
    a = _make_attachment(ticket)
    url = reverse('core:attachment_download', kwargs={'pk': a.pk})

    client.force_login(intruder)
    assert client.get(url).status_code == 404

    client.force_login(owner)
    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_attachment_download_allowed_for_admin(client, client_obj, admin_user):
    ticket = Ticket.objects.create(client=client_obj, subject='s', description='d',
                                   ticket_number='TKT-ATT-3', status='open')
    a = _make_attachment(ticket)
    client.force_login(admin_user)
    assert client.get(reverse('core:attachment_download', kwargs={'pk': a.pk})).status_code == 200


def _raw_email_with_attachments(parts, subject='Has files', from_email='wayne@davis.example'):
    """parts = list of (data_bytes, filename, subtype)."""
    import email.message
    msg = email.message.EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f'Wayne Davis <{from_email}>'
    msg['To'] = 'support@example.com'
    msg['Message-ID'] = '<att-test@davis.example>'
    msg.set_content('See attached.')
    for data, filename, subtype in parts:
        msg.add_attachment(data, maintype='application', subtype=subtype, filename=filename)
    return msg.as_bytes()


@pytest.mark.django_db
def test_inbound_attachment_blocks_dangerous_ext_and_oversize():
    from core.management.commands.fetch_inbound_email import _process_message
    from core.models import Attachment
    site = SiteSettings.get()
    site.max_attachment_size_mb = 1
    site.save()
    oversized = b'x' * (2 * 1024 * 1024)  # 2 MB, over the 1 MB cap
    raw = _raw_email_with_attachments([
        (b'MZ harmless test', 'evil.exe', 'octet-stream'),   # blocked extension
        (oversized, 'big.txt', 'octet-stream'),              # over cap
        (b'real note', 'ok.txt', 'octet-stream'),            # should be kept
    ])
    status, detail, ticket = _process_message(raw, site, verbosity=0)
    assert ticket is not None
    names = set(Attachment.objects.filter(
        object_id=ticket.pk).values_list('original_filename', flat=True))
    assert 'ok.txt' in names
    assert 'evil.exe' not in names
    assert 'big.txt' not in names


# ── Phase A: priced line items + WO total + QuickLabor default price ─────────

@pytest.mark.django_db
def test_line_item_total_math_and_unpriced(client_obj):
    from decimal import Decimal
    wo = WorkOrder.objects.create(client=client_obj)
    priced = wo.line_items.create(kind='labor', description='Tune-up', quantity=2, unit_price=Decimal('50.00'))
    unpriced = wo.line_items.create(kind='labor', description='Diagnosis')  # no price
    assert priced.line_total == Decimal('100.00')
    assert unpriced.line_total is None
    # WO total counts only priced lines
    assert wo.line_items_total == Decimal('100.00')


@pytest.mark.django_db
def test_quicklabor_button_prefills_default_price(client, client_obj, admin_user):
    from decimal import Decimal
    from core.models import CatalogItem, LineItem
    wo = WorkOrder.objects.create(client=client_obj)
    item = CatalogItem.objects.create(name='Virus Removal', category='Software',
                                      default_price=Decimal('120.00'))
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_performed_log', args=[wo.pk, item.pk]))
    assert resp.status_code == 200
    li = LineItem.objects.get(object_id=wo.pk, description='Virus Removal')
    assert li.kind == 'labor'
    assert li.unit_price == Decimal('120.00')
    assert li.catalog_item_id == item.pk


@pytest.mark.django_db
def test_custom_part_line_with_price(client, client_obj, admin_user):
    from decimal import Decimal
    from core.models import LineItem
    wo = WorkOrder.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_performed_custom', args=[wo.pk]), {
        'custom_label': '1TB SSD', 'kind': 'part', 'quantity': '2', 'unit_price': '75.50', 'notes': 'Samsung',
    })
    assert resp.status_code == 200
    li = LineItem.objects.get(object_id=wo.pk, description='1TB SSD')
    assert li.kind == 'part'
    assert li.line_total == Decimal('151.00')
    assert wo.line_items_total == Decimal('151.00')


@pytest.mark.django_db
def test_line_item_update_sets_price(client, client_obj, admin_user):
    from decimal import Decimal
    from core.models import LineItem
    wo = WorkOrder.objects.create(client=client_obj)
    li = wo.line_items.create(kind='labor', description='Cleanup')
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_performed_update', args=[li.pk]), {
        'custom_label': 'Cleanup', 'quantity': '1', 'unit_price': '40', 'notes': '',
    })
    assert resp.status_code == 200
    li.refresh_from_db()
    assert li.unit_price == Decimal('40')


# ── Phase B: Invoice Ninja push (API mocked — no live calls) ────────────────

def _enable_in(monkeypatch=None):
    s = SiteSettings.get()
    s.invoice_ninja_enabled = True
    s.invoice_ninja_url = 'https://invoicing.co'
    s.invoice_ninja_token = 'test-token'
    s.save()
    return s


@pytest.mark.django_db
def test_in_client_name_is_type_aware():
    from core import invoice_ninja
    # Business → business name
    biz = Client.objects.create(name='Acme LLC', client_type='business')
    Contact.objects.create(client=biz, first_name='Jane', last_name='Doe', is_primary=True)
    assert invoice_ninja.in_client_name(biz) == 'Acme LLC'
    # Residential (MB files by bare last name) → primary contact's full name
    res = Client.objects.create(name='Dorkleputz', client_type='residential')
    Contact.objects.create(client=res, first_name='Winky', last_name='Dorkleputz', is_primary=True)
    assert invoice_ninja.in_client_name(res) == 'Winky Dorkleputz'


@pytest.mark.django_db
def test_push_blocks_when_no_priced_lines(client_obj):
    from core import invoice_ninja
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj)
    wo.line_items.create(kind='labor', description='Diagnosis')  # unpriced
    with pytest.raises(invoice_ninja.InvoiceNinjaError):
        invoice_ninja.push_work_order(wo)


@pytest.mark.django_db
def test_push_sends_draft_with_priced_lines_and_stores_ref(client_obj, monkeypatch):
    from decimal import Decimal
    from core import invoice_ninja
    _enable_in()
    client_obj.invoice_ninja_id = '42'  # already linked → no client lookup
    client_obj.save()
    wo = WorkOrder.objects.create(client=client_obj)
    wo.line_items.create(kind='labor', description='Tune-up', quantity=1, unit_price=Decimal('80'))
    wo.line_items.create(kind='part', description='SSD', quantity=2, unit_price=Decimal('50'))
    wo.line_items.create(kind='labor', description='Diag (internal)')  # unpriced → excluded

    captured = {}
    def fake_request(method, path, *, params=None, json=None):
        captured['method'] = method; captured['path'] = path; captured['json'] = json
        return {'data': {'id': 999, 'number': 'INV-0007'}}
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)

    ref = invoice_ninja.push_work_order(wo)
    wo.refresh_from_db()
    assert wo.invoice_ninja_id == '999'
    assert wo.invoice_ninja_ref == 'INV-0007'
    assert ref == 'INV-0007'
    # Payload: draft (no number/email), WO# in po_number, only the 2 priced lines
    body = captured['json']
    assert captured['path'] == '/invoices'
    assert body['client_id'] == '42'
    assert body['po_number'] == wo.work_order_number
    assert 'number' not in body
    assert len(body['line_items']) == 2


@pytest.mark.django_db
def test_push_failure_leaves_wo_clean(client_obj, monkeypatch):
    from decimal import Decimal
    from core import invoice_ninja
    _enable_in()
    client_obj.invoice_ninja_id = '42'; client_obj.save()
    wo = WorkOrder.objects.create(client=client_obj)
    wo.line_items.create(kind='labor', description='Tune-up', unit_price=Decimal('80'))

    def boom(*a, **k):
        raise invoice_ninja.InvoiceNinjaError('401')
    monkeypatch.setattr(invoice_ninja, '_request', boom)

    with pytest.raises(invoice_ninja.InvoiceNinjaError):
        invoice_ninja.push_work_order(wo)
    wo.refresh_from_db()
    assert wo.invoice_ninja_id == ''   # nothing saved → clean retry


@pytest.mark.django_db
def test_push_work_order_still_used_directly(client_obj, monkeypatch):
    """push_work_order() itself is unchanged by the POS work (Slice 1 added
    new host-agnostic primitives alongside it, not in place of it) — its old
    UI wrapper (WorkOrderSendToINView) was retired in favor of the POS, but
    the function stays available/tested directly."""
    from core import invoice_ninja
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj)
    from core.models import LineItem
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=40)
    monkeypatch.setattr(invoice_ninja, '_request',
                        lambda method, path, **kw: {'data': {'id': 9, 'number': 'INV-9'}})
    monkeypatch.setattr(invoice_ninja, 'find_or_create_client', lambda c: 'inclient-1')
    ref = invoice_ninja.push_work_order(wo)
    assert ref == 'INV-9'


@pytest.mark.django_db
def test_find_or_create_uses_stored_id(client_obj, monkeypatch):
    from core import invoice_ninja
    _enable_in()
    client_obj.invoice_ninja_id = '77'; client_obj.save()
    # Should NOT call the API at all when id is already stored
    monkeypatch.setattr(invoice_ninja, '_request', lambda *a, **k: pytest.fail('should not call API'))
    assert invoice_ninja.find_or_create_client(client_obj) == '77'


# ── Work order hard-delete (admin only, cleans up + reopens converted ticket) ─

@pytest.mark.django_db
def test_workorder_delete_admin_cascades_and_reopens_ticket(client, client_obj, admin_user):
    from decimal import Decimal
    from core.models import Ticket, LineItem
    ticket = Ticket.objects.create(client=client_obj, subject='s', description='d',
                                   ticket_number='TKT-DEL-1', status='converted')
    wo = WorkOrder.objects.create(client=client_obj, ticket=ticket)
    wo.line_items.create(kind='labor', description='x', unit_price=Decimal('10'))
    wo_pk = wo.pk
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_order_delete', args=[wo_pk]))
    assert resp.status_code == 302
    assert not WorkOrder.objects.filter(pk=wo_pk).exists()
    assert LineItem.objects.filter(object_id=wo_pk).count() == 0  # cascaded
    ticket.refresh_from_db()
    assert ticket.status == 'open'  # converted ticket reopened, not orphaned


@pytest.mark.django_db
def test_workorder_delete_denied_for_non_admin(client, client_obj):
    tech = User.objects.create_user(username='wodel-tech', password='x', is_staff=False)
    wo = WorkOrder.objects.create(client=client_obj)
    client.force_login(tech)
    resp = client.post(reverse('core:work_order_delete', args=[wo.pk]))
    assert resp.status_code == 403
    assert WorkOrder.objects.filter(pk=wo.pk).exists()


# ── Device delete (admin only; linked work orders survive via SET_NULL) ─────

@pytest.mark.django_db
def test_device_delete_removes_duplicate(client, client_obj, admin_user):
    dupe = Device.objects.create(client=client_obj, name="Dan's Laptop")
    client.force_login(admin_user)
    resp = client.post(reverse('core:device_delete', args=[dupe.pk]))
    assert resp.status_code == 302
    assert not Device.objects.filter(pk=dupe.pk).exists()


@pytest.mark.django_db
def test_device_delete_keeps_linked_work_order(client, client_obj, admin_user):
    device = Device.objects.create(client=client_obj, name="Dan's Laptop")
    wo = WorkOrder.objects.create(client=client_obj, device=device)
    client.force_login(admin_user)
    client.post(reverse('core:device_delete', args=[device.pk]))
    assert not Device.objects.filter(pk=device.pk).exists()
    wo.refresh_from_db()
    assert wo.device_id is None  # WO survives, device reference nulled


@pytest.mark.django_db
def test_device_delete_denied_for_non_admin(client, client_obj):
    tech = User.objects.create_user(username='ddel-tech', password='x', is_staff=False)
    device = Device.objects.create(client=client_obj, name="Dan's Laptop")
    client.force_login(tech)
    resp = client.post(reverse('core:device_delete', args=[device.pk]))
    assert resp.status_code == 403
    assert Device.objects.filter(pk=device.pk).exists()


# ── Admin user delete (guards: not self, not last superuser) ────────────────

@pytest.mark.django_db
def test_user_delete_removes_test_account(client, admin_user):
    victim = User.objects.create_user(username='testacct', password='x', is_staff=False)
    client.force_login(admin_user)
    resp = client.post(reverse('core:user_delete', args=[victim.pk]))
    assert resp.status_code == 302
    assert not User.objects.filter(pk=victim.pk).exists()


@pytest.mark.django_db
def test_user_delete_blocks_self_and_last_superuser(client, admin_user):
    # admin_user is the only superuser; cannot delete self
    client.force_login(admin_user)
    client.post(reverse('core:user_delete', args=[admin_user.pk]))
    assert User.objects.filter(pk=admin_user.pk).exists()
    # A second superuser deleting the other is fine, but never the last one
    su2 = User.objects.create_user(username='su2', password='x', is_staff=True, is_superuser=True)
    client.force_login(su2)
    client.post(reverse('core:user_delete', args=[admin_user.pk]))   # now 1 left
    assert User.objects.filter(is_superuser=True).count() == 1
    resp = client.post(reverse('core:user_delete', args=[su2.pk]))    # deleting self anyway blocked
    assert User.objects.filter(pk=su2.pk).exists()


@pytest.mark.django_db
def test_user_delete_denied_for_non_admin(client):
    tech = User.objects.create_user(username='udel-tech', password='x', is_staff=False)
    victim = User.objects.create_user(username='udel-victim', password='x', is_staff=False)
    client.force_login(tech)
    resp = client.post(reverse('core:user_delete', args=[victim.pk]))
    assert resp.status_code in (403, 302)
    assert User.objects.filter(pk=victim.pk).exists()


# ── WO reported-issue field: ticket description must survive conversion ──────

@pytest.mark.django_db
def test_convert_carries_ticket_description_into_wo_reported_problem(client, client_obj, admin_user):
    problem = "Won't boot past the logo. Also wants the fans cleaned and a 2nd drive checked."
    ticket = Ticket.objects.create(client=client_obj, subject='Boot loop', description=problem)

    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_convert', args=[ticket.pk]))
    assert resp.status_code == 302

    wo = WorkOrder.objects.get(ticket=ticket)
    assert wo.reported_problem == problem, \
        'Ticket description must be carried into the WO reported_problem on conversion (was silently dropped before).'
    ticket.refresh_from_db()
    assert ticket.status == 'converted'


@pytest.mark.django_db
def test_ticket_with_open_wo_can_be_closed(client, client_obj, admin_user):
    """MB does NOT block closing a ticket whose linked WO is still open — sequencing
    ticket-close vs WO-completion is the shop's policy, not the software's opinion.
    Covers both close paths: the full edit form and the quick status dropdown."""
    # Quick status path
    t1 = Ticket.objects.create(client=client_obj, subject='A', description='D')
    WorkOrder.objects.create(client=client_obj, ticket=t1, status='open')
    client.force_login(admin_user)
    client.post(reverse('core:ticket_status_update', args=[t1.pk]), {'status': 'closed'})
    t1.refresh_from_db()
    assert t1.status == 'closed', 'Quick status change must close despite an open linked WO.'

    # Full edit form path
    t2 = Ticket.objects.create(client=client_obj, subject='B', description='D')
    WorkOrder.objects.create(client=client_obj, ticket=t2, status='open')
    client.post(reverse('core:ticket_edit', args=[t2.pk]), {
        'client': client_obj.pk,
        'subject': 'B',
        'description': 'D',
        'status': 'resolved',
        'source': 'email',
    })
    t2.refresh_from_db()
    assert t2.status == 'resolved', 'Edit form must close despite an open linked WO.'


@pytest.mark.django_db
def test_wo_detail_shows_reported_problem(client, client_obj, admin_user):
    wo = WorkOrder.objects.create(
        client=client_obj,
        reported_problem='Replace cracked screen; also check why battery drains overnight.',
    )
    client.force_login(admin_user)
    resp = client.get(reverse('core:work_order_detail', args=[wo.pk]))
    assert resp.status_code == 200
    assert 'check why battery drains overnight' in resp.content.decode()


@pytest.mark.django_db
def test_wo_notes_have_order_toggle_defaulting_newest_first(client, client_obj, admin_user):
    """WO activity notes are reorderable (Jim's request); default newest-first, sticky
    per-browser via localStorage. Ordering is client-side, so assert the markup + default."""
    wo = WorkOrder.objects.create(client=client_obj)
    client.force_login(admin_user)
    body = client.get(reverse('core:work_order_detail', args=[wo.pk])).content.decode()
    # localStorage-backed preference with a newest-first default
    assert "mb_wo_notes_order" in body
    assert "'newest'" in body
    # the reverse-on-newest binding drives the visual order without touching DOM/HTMX swap
    assert "flex-col-reverse" in body
    # a user-facing toggle exists
    assert 'Newest first' in body and 'Oldest first' in body


@pytest.mark.django_db
def test_role_edit_page_renders(client, admin_user):
    """Regression: role_form.html used the `getfield` filter without {% load mb_icons %},
    so /roles/<id>/edit/ 500'd with TemplateSyntaxError. Lock it at 200."""
    from core.models import Role
    role = Role.objects.create(name='Bench Lead')
    client.force_login(admin_user)
    resp = client.get(reverse('core:role_edit', args=[role.pk]))
    assert resp.status_code == 200
    assert 'Edit Role' in resp.content.decode()


# ── SLA response deadline: first staff reply meets it permanently ───────────

@pytest.mark.django_db
def test_overdue_ticket_with_past_due_at_is_overdue_without_response():
    """A ticket past its SLA due time with no staff reply is overdue."""
    from django.utils import timezone
    from core.models import SLAPlan
    client_obj = Client.objects.create(name='Acme Co')
    plan = SLAPlan.objects.create(name='Business', grace_period_hours=8)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    ticket.due_at = timezone.now() - timezone.timedelta(hours=1)
    ticket.sla_plan = plan
    ticket.save(update_fields=['due_at', 'sla_plan'])

    assert ticket.is_overdue, 'Past-due ticket with no response must be overdue.'


@pytest.mark.django_db
def test_first_staff_reply_meets_sla_and_clears_overdue(client, client_obj, admin_user):
    """Posting the first customer-visible staff reply stamps first_responded_at
    and the ticket is no longer overdue even though due_at is in the past."""
    from django.utils import timezone
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    ticket.due_at = timezone.now() - timezone.timedelta(hours=1)
    ticket.save(update_fields=['due_at'])
    assert ticket.is_overdue

    client.force_login(admin_user)
    client.post(reverse('core:ticket_reply_add', args=[ticket.pk]),
                {'reply_type': 'customer_visible', 'content': 'On it.'})

    ticket.refresh_from_db()
    assert ticket.first_responded_at is not None, \
        'First staff customer-visible reply must stamp first_responded_at.'
    assert not ticket.is_overdue, \
        'Once responded, a ticket can no longer be overdue (response SLA met).'


@pytest.mark.django_db
def test_internal_note_does_not_meet_sla(client, client_obj, admin_user):
    """An internal note is not a customer response and must not clear overdue."""
    from django.utils import timezone
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    ticket.due_at = timezone.now() - timezone.timedelta(hours=1)
    ticket.save(update_fields=['due_at'])

    client.force_login(admin_user)
    client.post(reverse('core:ticket_reply_add', args=[ticket.pk]),
                {'reply_type': 'internal', 'content': 'Note to self.'})

    ticket.refresh_from_db()
    assert ticket.first_responded_at is None
    assert ticket.is_overdue, 'Internal note must not satisfy the response SLA.'


@pytest.mark.django_db
def test_sla_compliance_report_first_response_and_sets_aside_pending(client, client_obj, admin_user):
    """Report 6 (SLA Compliance) is a RESPONSE SLA measured on the first staff reply vs
    due_at (Ticket.first_responded_at), NOT on closure. A still-in-window unanswered
    ticket is SET ASIDE (not counted as a miss) until its deadline passes. This locks
    both the first-response basis and the 'judged only' denominator, and guards against
    regressing to the old closure-based logic."""
    from django.utils import timezone
    now = timezone.now()
    hour = timezone.timedelta(hours=1)

    def mk(subject, **fields):
        t = Ticket.objects.create(client=client_obj, subject=subject, description='d')
        Ticket.objects.filter(pk=t.pk).update(**fields)
        return t

    # Answered before the deadline → HIT.
    mk('answered-on-time', due_at=now, first_responded_at=now - hour)
    # Deadline passed, never answered → MISS (judged).
    mk('unanswered-overdue', due_at=now - hour, first_responded_at=None)
    # Answered, but after the deadline → MISS (judged).
    mk('answered-late', due_at=now - 2 * hour, first_responded_at=now - hour)
    # Still inside its window, not answered yet → SET ASIDE (not judged, not a miss).
    mk('still-in-window', due_at=now + hour, first_responded_at=None)

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.status_code == 200
    assert resp.context['total_sla'] == 4
    assert resp.context['responded_on_time'] == 1
    assert resp.context['judged_sla'] == 3      # on-time + overdue + late
    assert resp.context['pending_sla'] == 1      # the still-in-window ticket is set aside


# ── SLA Slice 2: client-type default SLA (every ticket gets a clock) ────────

@pytest.mark.django_db
def test_new_ticket_inherits_business_default_sla():
    """A ticket for a business client is stamped with the business default plan
    at creation, with no manual sla_plan pick."""
    from core.models import SLAPlan, SiteSettings
    biz_plan = SLAPlan.objects.create(name='Business 4h', grace_period_hours=4)
    SLAPlan.objects.create(name='Residential 24h', grace_period_hours=24)
    site = SiteSettings.get()
    site.default_business_sla = biz_plan
    site.save(update_fields=['default_business_sla'])

    biz_client = Client.objects.create(name='Acme LLC', client_type='business')
    ticket = Ticket.objects.create(client=biz_client, subject='S', description='D')

    assert ticket.sla_plan_id == biz_plan.pk
    assert ticket.due_at is not None


@pytest.mark.django_db
def test_new_ticket_inherits_residential_default_sla(client_obj):
    """A residential client's ticket gets the residential default — help topic
    plays no part in the decision."""
    from core.models import SLAPlan, SiteSettings
    res_plan = SLAPlan.objects.create(name='Residential 24h', grace_period_hours=24)
    site = SiteSettings.get()
    site.default_residential_sla = res_plan
    site.save(update_fields=['default_residential_sla'])

    assert client_obj.client_type == 'residential'
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')

    assert ticket.sla_plan_id == res_plan.pk
    assert ticket.due_at is not None


@pytest.mark.django_db
def test_unsorted_ticket_gets_residential_default_as_placeholder():
    """The system Unsorted/Unverified client is residential-typed, so an inbound
    ticket parked there rides the residential default until triaged — no
    special-casing needed, it's the same rule."""
    from core.models import SLAPlan, SiteSettings
    res_plan = SLAPlan.objects.create(name='Residential 24h', grace_period_hours=24)
    site = SiteSettings.get()
    site.default_residential_sla = res_plan
    site.save(update_fields=['default_residential_sla'])

    unsorted = Client.get_unsorted()
    assert unsorted.client_type == 'residential'
    ticket = Ticket.objects.create(client=unsorted, subject='S', description='D')

    assert ticket.sla_plan_id == res_plan.pk


@pytest.mark.django_db
def test_manual_sla_pick_overrides_client_type_default():
    """An explicit sla_plan set before save (e.g. from the ticket form) wins over
    the client-type default — the default only fills a gap, never overrides."""
    from core.models import SLAPlan, SiteSettings
    default_plan = SLAPlan.objects.create(name='Default 24h', grace_period_hours=24)
    chosen_plan = SLAPlan.objects.create(name='Rush 2h', grace_period_hours=2)
    site = SiteSettings.get()
    site.default_residential_sla = default_plan
    site.save(update_fields=['default_residential_sla'])

    res_client = Client.objects.create(name='Jane Doe')
    ticket = Ticket(client=res_client, subject='S', description='D', sla_plan=chosen_plan)
    ticket.save()

    assert ticket.sla_plan_id == chosen_plan.pk


@pytest.mark.django_db
def test_no_default_configured_leaves_ticket_clock_less(client_obj):
    """With no defaults set (the out-of-the-box state), ticket creation behaves
    exactly as before — no clock, no crash."""
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    assert ticket.sla_plan_id is None
    assert ticket.due_at is None


@pytest.mark.django_db
def test_editing_ticket_does_not_resnapshot_sla_on_ordinary_client_change(admin_user, client):
    """Reassigning a ticket between two ordinary (non-Unsorted) clients must not
    retroactively move its SLA — only the Unsorted-triage path re-snapshots."""
    from core.models import SLAPlan, SiteSettings
    biz_plan = SLAPlan.objects.create(name='Business 4h', grace_period_hours=4)
    res_plan = SLAPlan.objects.create(name='Residential 24h', grace_period_hours=24)
    site = SiteSettings.get()
    site.default_business_sla = biz_plan
    site.default_residential_sla = res_plan
    site.save(update_fields=['default_business_sla', 'default_residential_sla'])

    old_client = Client.objects.create(name='Old Residential Co')
    new_client = Client.objects.create(name='New Business LLC', client_type='business')
    ticket = Ticket.objects.create(client=old_client, subject='S', description='D')
    original_due_at = ticket.due_at
    assert ticket.sla_plan_id == res_plan.pk

    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_edit', args=[ticket.pk]), {
        'client': new_client.pk, 'subject': 'S', 'description': 'D',
        'source': 'phone', 'status': 'new', 'sla_plan': ticket.sla_plan_id,
    })
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.client_id == new_client.pk
    assert ticket.sla_plan_id == res_plan.pk, 'SLA must not move on an ordinary reassignment.'
    assert ticket.due_at == original_due_at


@pytest.mark.django_db
def test_editing_ticket_client_and_device_together_keeps_new_device(admin_user, client):
    """Regression: changing a ticket's client AND selecting a device belonging to
    that new client in the SAME submit must keep the device — it used to be
    unconditionally nulled out just because the client changed, forcing a second
    edit to make the device stick."""
    old_client = Client.objects.create(name='Old Co')
    new_client = Client.objects.create(name='New Co')
    new_device = Device.objects.create(client=new_client, name='New Co Laptop')
    ticket = Ticket.objects.create(client=old_client, subject='S', description='D')

    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_edit', args=[ticket.pk]), {
        'client': new_client.pk, 'device': new_device.pk, 'subject': 'S', 'description': 'D',
        'source': 'phone', 'status': 'new',
    })
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.client_id == new_client.pk
    assert ticket.device_id == new_device.pk, 'A device belonging to the new client must survive the same-submit client change.'


@pytest.mark.django_db
def test_editing_ticket_client_without_device_still_nulls_device(admin_user, client):
    """Existing behavior preserved: changing the client with no device reselected
    (or a stale device from the old client) still nulls the device out."""
    old_client = Client.objects.create(name='Old Co 2')
    new_client = Client.objects.create(name='New Co 2')
    old_device = Device.objects.create(client=old_client, name='Old Co Laptop')
    ticket = Ticket.objects.create(client=old_client, device=old_device, subject='S', description='D')

    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_edit', args=[ticket.pk]), {
        'client': new_client.pk, 'subject': 'S', 'description': 'D',
        'source': 'phone', 'status': 'new',
    })
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.client_id == new_client.pk
    assert ticket.device_id is None, 'No device (or a stale one from the old client) must still null the device on client change.'


@pytest.mark.django_db
def test_triage_off_unsorted_resnapshots_client_type_default(admin_user, client):
    """Reassigning an Unsorted ticket to a real business client at triage picks
    up the business default — the residential placeholder clock was provisional."""
    from core.models import SLAPlan, SiteSettings
    biz_plan = SLAPlan.objects.create(name='Business 4h', grace_period_hours=4)
    res_plan = SLAPlan.objects.create(name='Residential 24h', grace_period_hours=24)
    site = SiteSettings.get()
    site.default_business_sla = biz_plan
    site.default_residential_sla = res_plan
    site.save(update_fields=['default_business_sla', 'default_residential_sla'])

    unsorted = Client.get_unsorted()
    real_client = Client.objects.create(name='Real Business LLC', client_type='business')
    ticket = Ticket.objects.create(client=unsorted, subject='S', description='D')
    assert ticket.sla_plan_id == res_plan.pk

    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_edit', args=[ticket.pk]), {
        'client': real_client.pk, 'subject': 'S', 'description': 'D',
        'source': 'phone', 'status': 'new', 'sla_plan': ticket.sla_plan_id,
    })
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.client_id == real_client.pk
    assert ticket.sla_plan_id == biz_plan.pk, 'Triage off Unsorted must re-snapshot to the new client-type default.'


@pytest.mark.django_db
def test_triage_off_unsorted_respects_manual_sla_pick(admin_user, client):
    """If the same edit that reassigns off Unsorted ALSO picks an SLA plan by
    hand, the manual pick wins — triage auto-resnapshot never overrides it."""
    from core.models import SLAPlan, SiteSettings
    biz_plan = SLAPlan.objects.create(name='Business 4h', grace_period_hours=4)
    res_plan = SLAPlan.objects.create(name='Residential 24h', grace_period_hours=24)
    rush_plan = SLAPlan.objects.create(name='Rush 1h', grace_period_hours=1)
    site = SiteSettings.get()
    site.default_business_sla = biz_plan
    site.default_residential_sla = res_plan
    site.save(update_fields=['default_business_sla', 'default_residential_sla'])

    unsorted = Client.get_unsorted()
    real_client = Client.objects.create(name='Real Business LLC', client_type='business')
    ticket = Ticket.objects.create(client=unsorted, subject='S', description='D')

    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_edit', args=[ticket.pk]), {
        'client': real_client.pk, 'subject': 'S', 'description': 'D',
        'source': 'phone', 'status': 'new', 'sla_plan': rush_plan.pk,
    })
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.sla_plan_id == rush_plan.pk


@pytest.mark.django_db
def test_sla_defaults_save_view(admin_user, client):
    """Settings → SLA Plans defaults form persists both client-type defaults."""
    from core.models import SLAPlan, SiteSettings
    biz_plan = SLAPlan.objects.create(name='Business 4h', grace_period_hours=4)
    res_plan = SLAPlan.objects.create(name='Residential 24h', grace_period_hours=24)

    client.force_login(admin_user)
    resp = client.post(reverse('core:sla_defaults_save'), {
        'default_business_sla': biz_plan.pk,
        'default_residential_sla': res_plan.pk,
    })
    assert resp.status_code == 302

    site = SiteSettings.get()
    assert site.default_business_sla_id == biz_plan.pk
    assert site.default_residential_sla_id == res_plan.pk


# ── SLA Slice 3: diagnostic metrics (reporting only, no model change) ──────

@pytest.mark.django_db
def test_median_first_response_time_reported(admin_user, client, client_obj):
    """Median (not mean) first-response time next to the SLA %, computed from
    tickets that have actually been responded to in the period."""
    from django.utils import timezone

    def mk(hours_to_respond):
        t = Ticket.objects.create(client=client_obj, subject='S', description='D')
        Ticket.objects.filter(pk=t.pk).update(
            first_responded_at=t.created_at + timezone.timedelta(hours=hours_to_respond)
        )
        return t

    mk(1)
    mk(2)
    mk(9)  # median of [1, 2, 9] = 2

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.status_code == 200
    assert resp.context['median_response_hours'] == 2


@pytest.mark.django_db
def test_sla_breakdown_by_tech_and_client(admin_user, client):
    """SLA rate + median response time broken down per tech and per client —
    help topic plays no part, matching the client-type-only SLA design."""
    from django.utils import timezone
    now = timezone.now()
    hour = timezone.timedelta(hours=1)
    tech = User.objects.create_user(username='tech1', password='x')
    biz_client = Client.objects.create(name='Breakdown Biz', client_type='business')

    def mk(client_, assigned_to=None, **fields):
        t = Ticket.objects.create(client=client_, subject='S', description='D', assigned_to=assigned_to)
        Ticket.objects.filter(pk=t.pk).update(**fields)
        return t

    mk(biz_client, assigned_to=tech, due_at=now, first_responded_at=now - hour)
    mk(biz_client, assigned_to=tech, due_at=now - hour, first_responded_at=None)

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.status_code == 200

    tech_label = tech.get_full_name() or tech.username
    tech_row = next(r for r in resp.context['sla_by_tech'] if r['label'] == tech_label)
    assert tech_row['judged'] == 2
    assert tech_row['on_time'] == 1
    assert tech_row['sla_rate'] == 50.0

    client_row = next(r for r in resp.context['sla_by_client'] if r['label'] == 'Breakdown Biz')
    assert client_row['judged'] == 2
    assert client_row['on_time'] == 1


@pytest.mark.django_db
def test_backlog_health_is_live_snapshot_not_date_filtered(admin_user, client, client_obj):
    """Backlog health counts currently-open tickets regardless of the reports
    date range — it's forward-looking ('what's on the plate now'), not historical."""
    from django.utils import timezone
    now = timezone.now()

    def mk(age_days, status='open'):
        t = Ticket.objects.create(client=client_obj, subject='S', description='D', status=status)
        Ticket.objects.filter(pk=t.pk).update(created_at=now - timezone.timedelta(days=age_days))
        return t

    mk(0.5)   # under 1 day
    mk(2)     # 1-3 days
    mk(5)     # 3-7 days
    mk(10)    # 7+ days
    mk(20, status='closed')  # closed — excluded from backlog entirely

    client.force_login(admin_user)
    # Date range set far in the past — must NOT affect the live backlog snapshot.
    resp = client.get(reverse('core:reports'), {'start_date': '2020-01-01', 'end_date': '2020-01-02'})
    assert resp.status_code == 200
    assert resp.context['backlog_open_count'] == 4
    assert resp.context['backlog_buckets']['lt_1d'] == 1
    assert resp.context['backlog_buckets']['1_3d'] == 1
    assert resp.context['backlog_buckets']['3_7d'] == 1
    assert resp.context['backlog_buckets']['7d_plus'] == 1


@pytest.mark.django_db
def test_created_vs_closed_in_period(admin_user, client, client_obj):
    """Created-vs-closed counts within the selected date range — 'are we keeping up?'."""
    from django.utils import timezone
    now = timezone.now()

    Ticket.objects.create(client=client_obj, subject='new1', description='D')
    Ticket.objects.create(client=client_obj, subject='new2', description='D')
    closed = Ticket.objects.create(client=client_obj, subject='closed1', description='D', status='closed')
    Ticket.objects.filter(pk=closed.pk).update(updated_at=now)

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.status_code == 200
    assert resp.context['created_in_period'] == 3
    assert resp.context['closed_in_period'] == 1


@pytest.mark.django_db
def test_backlog_csv_export(admin_user, client, client_obj):
    """CSV export for the new Backlog Health report."""
    Ticket.objects.create(client=client_obj, subject='S', description='D')
    client.force_login(admin_user)
    resp = client.get(reverse('core:reports_csv', args=['backlog']))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'Open tickets (now)' in body
    assert '7+ days old' in body


@pytest.mark.django_db
def test_sla_breakdown_csv_export(admin_user, client, client_obj):
    """CSV export for the new SLA-by-tech/client breakdown report."""
    from django.utils import timezone
    now = timezone.now()
    t = Ticket.objects.create(client=client_obj, subject='S', description='D')
    Ticket.objects.filter(pk=t.pk).update(due_at=now - timezone.timedelta(hours=1), first_responded_at=None)

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports_csv', args=['sla_breakdown']))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'Group' in body and 'Client' in body


@pytest.mark.django_db
def test_overdue_queryset_matches_is_overdue_property():
    """The DB-level overdue_queryset (dashboard tile, ?overdue filter, queue
    criteria, SLA command) must agree with the is_overdue property for every
    ticket — responded, converted, and SLA-muted tickets are NOT overdue even
    when due_at is in the past."""
    from django.utils import timezone
    from core.models import SLAPlan
    client_obj = Client.objects.create(name='Acme Co')
    past = timezone.now() - timezone.timedelta(hours=1)
    muted_plan = SLAPlan.objects.create(
        name='Silent', grace_period_hours=8, disable_overdue_alerts=True)

    # Genuinely overdue: past due, no reply, open.
    overdue = Ticket.objects.create(client=client_obj, subject='overdue', description='d')
    Ticket.objects.filter(pk=overdue.pk).update(due_at=past)

    # Responded (first_responded_at set) → not overdue.
    responded = Ticket.objects.create(client=client_obj, subject='responded', description='d')
    Ticket.objects.filter(pk=responded.pk).update(due_at=past, first_responded_at=past)

    # Converted → not overdue.
    converted = Ticket.objects.create(client=client_obj, subject='converted', description='d')
    Ticket.objects.filter(pk=converted.pk).update(due_at=past, status='converted')

    # SLA alerts muted → not overdue.
    muted = Ticket.objects.create(client=client_obj, subject='muted', description='d', sla_plan=muted_plan)
    Ticket.objects.filter(pk=muted.pk).update(due_at=past)

    qs_ids = set(Ticket.overdue_queryset().values_list('pk', flat=True))
    property_ids = {t.pk for t in Ticket.objects.all() if t.is_overdue}

    assert qs_ids == property_ids, 'overdue_queryset must match the is_overdue property.'
    assert qs_ids == {overdue.pk}, 'Only the genuinely-overdue ticket should count.'


# ── export_data: portable CSV + media bundle, secrets redacted by default ───

@pytest.mark.django_db
def test_export_data_redacts_secrets_by_default(tmp_path):
    """The portable export writes a tarball and never leaks encrypted secrets
    unless explicitly asked. A device password is the canary."""
    import tarfile
    from django.core.management import call_command

    c = Client.objects.create(name='Acme Co')
    Device.objects.create(client=c, name='PC1', device_password='hunter2secret')

    call_command('export_data', output=str(tmp_path))

    archives = list(tmp_path.glob('mb-export-*.tar.gz'))
    assert len(archives) == 1, 'export must produce exactly one tarball'

    with tarfile.open(archives[0]) as tar:
        names = tar.getnames()
        device_csv = next(n for n in names if n.endswith('/csv/Device.csv'))
        body = tar.extractfile(device_csv).read().decode()

    assert 'hunter2secret' not in body, 'decrypted secret must NOT appear by default'
    assert '***REDACTED***' in body, 'a present secret should show as redacted'
    assert any(n.endswith('/README.txt') for n in names)


@pytest.mark.django_db
def test_export_data_include_secrets_writes_plaintext(tmp_path):
    import tarfile
    from django.core.management import call_command

    c = Client.objects.create(name='Acme Co')
    Device.objects.create(client=c, name='PC1', device_password='hunter2secret')

    call_command('export_data', output=str(tmp_path), include_secrets=True)

    archive = next(tmp_path.glob('mb-export-*.tar.gz'))
    with tarfile.open(archive) as tar:
        device_csv = next(n for n in tar.getnames() if n.endswith('/csv/Device.csv'))
        body = tar.extractfile(device_csv).read().decode()

    assert 'hunter2secret' in body, '--include-secrets must write the real value'


# ── In-app admin Update button (core/update_ops + Settings → Updates) ───────

@pytest.fixture
def tech_user(db):
    return User.objects.create_user(username='tech', password='x')


@pytest.mark.django_db
def test_request_update_writes_trigger_and_refuses_duplicate(settings, tmp_path):
    from core import update_ops
    settings.BASE_DIR = tmp_path
    assert update_ops.read_status() == {'state': 'idle'}
    assert update_ops.request_update() is True
    assert update_ops.trigger_path().exists()
    assert update_ops.read_status()['state'] == 'queued'
    # A second request while one is queued/running is refused — no double trigger.
    assert update_ops.is_running() is True
    assert update_ops.request_update() is False


@pytest.mark.django_db
def test_read_status_idle_on_corrupt_file(settings, tmp_path):
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text('not json {{{')
    assert update_ops.read_status() == {'state': 'idle'}


@pytest.mark.django_db
def test_is_update_available_compares_versions(monkeypatch):
    from core import update_ops
    monkeypatch.setattr(update_ops, 'available_version', lambda: 'v0.2.0')
    monkeypatch.setattr(update_ops, 'current_tag', lambda: 'v0.1.1')
    assert update_ops.is_update_available() is True
    monkeypatch.setattr(update_ops, 'current_tag', lambda: 'v0.2.0')
    assert update_ops.is_update_available() is False
    monkeypatch.setattr(update_ops, 'available_version', lambda: '')
    assert update_ops.is_update_available() is False


@pytest.mark.django_db
def test_update_views_require_admin(client, tech_user):
    client.force_login(tech_user)
    assert client.get(reverse('core:update_status')).status_code == 403
    assert client.post(reverse('core:update_start')).status_code == 403
    assert client.post(reverse('core:update_check')).status_code == 403


@pytest.mark.django_db
def test_update_trigger_view_writes_file_for_admin(client, admin_user, settings, tmp_path):
    from core import update_ops
    settings.BASE_DIR = tmp_path
    client.force_login(admin_user)
    resp = client.post(reverse('core:update_start'))
    assert resp.status_code == 200
    assert update_ops.trigger_path().exists()
    assert update_ops.read_status()['state'] == 'queued'


@pytest.mark.django_db
@pytest.mark.parametrize('state,needle', [
    ('running', 'in progress'),
    ('succeeded', 'succeeded'),
    ('failed', 'failed'),
])
def test_update_status_fragment_renders_states(client, admin_user, settings, tmp_path, state, needle):
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({'state': state}))
    client.force_login(admin_user)
    resp = client.get(reverse('core:update_status'))
    assert resp.status_code == 200
    assert needle in resp.content.decode().lower()


# ── Content-Security-Policy header + report endpoint ────────────────────────

@pytest.mark.django_db
def test_csp_header_report_only_by_default(client, settings):
    """Ships report-only: the browser reports violations but enforces nothing,
    and the policy carries the directives that actually contain an XSS."""
    settings.CSP_REPORT_ONLY = True
    resp = client.get('/')
    hdr = resp.headers.get('Content-Security-Policy-Report-Only')
    assert hdr is not None, 'Report-only CSP header must be present'
    assert 'Content-Security-Policy' not in resp.headers, 'Enforcing header must be absent in report-only mode'
    for token in ("default-src 'self'", "frame-ancestors 'none'",
                  "object-src 'none'", "base-uri 'self'", "report-uri /csp-report/"):
        assert token in hdr, f'CSP missing directive: {token}'


@pytest.mark.django_db
def test_csp_enforced_when_flag_off(client, settings):
    """CSP_REPORT_ONLY=False switches to the enforcing header."""
    settings.CSP_REPORT_ONLY = False
    resp = client.get('/')
    assert resp.headers.get('Content-Security-Policy') is not None
    assert 'Content-Security-Policy-Report-Only' not in resp.headers


@pytest.mark.django_db
def test_csp_absent_when_policy_empty(client, settings):
    """Empty CSP_POLICY emits no header — the instant .env-only rollback."""
    settings.CSP_POLICY = ''
    resp = client.get('/')
    assert 'Content-Security-Policy' not in resp.headers
    assert 'Content-Security-Policy-Report-Only' not in resp.headers


@pytest.mark.django_db
def test_csp_report_endpoint_accepts_post(client):
    """Browser-posted violation reports are accepted (204) without auth/CSRF."""
    resp = client.post(
        reverse('core:csp_report'),
        data=json.dumps({'csp-report': {'blocked-uri': 'https://evil.test',
                                        'violated-directive': 'script-src'}}),
        content_type='application/json',
    )
    assert resp.status_code == 204


@pytest.mark.django_db
def test_csp_report_endpoint_tolerates_garbage(client):
    """Non-JSON body must not error — just 204 and move on."""
    resp = client.post(reverse('core:csp_report'), data=b'not json',
                       content_type='application/csp-report')
    assert resp.status_code == 204


# ── Messages render in base.html (bug: feedback was invisible app-wide) ──────
#
# Found session 49: base.html had no messages block, so success/error feedback
# from every full-page POST→redirect flow was invisible — the queued messages
# only surfaced (stale) on the next page that rendered them, the logout page.
# These lock in that a followed redirect actually shows its message.

@pytest.mark.django_db
def test_redirect_flow_renders_message_in_base(client, client_obj, admin_user):
    """The reported bug: a POST→redirect action gave no visible feedback.

    With IN disabled (the default), asking the POS settle view to 'Bill Later'
    (draft) is refused — it needs IN — so the view adds an error message and
    redirects back to the settle screen. That page must render the message
    (proving base.html renders the messages framework), not swallow it until
    logout. (Originally reproduced via the now-retired WorkOrderSendToINView.)
    """
    from decimal import Decimal
    from core.models import LineItem
    wo = WorkOrder.objects.create(client=client_obj, status='closed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))
    client.force_login(admin_user)

    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {'action': 'draft'}, follow=True)

    assert resp.status_code == 200
    assert b'Bill Later needs Invoice Ninja' in resp.content, \
        'Error feedback must be visible on the page the user lands on.'


@pytest.mark.django_db
def test_success_message_renders_after_redirect(client, client_obj, admin_user):
    """A success flow (ticket resolve) surfaces its confirmation on the next page."""
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    client.force_login(admin_user)

    resp = client.post(reverse('core:ticket_close', args=[ticket.pk]), follow=True)

    assert resp.status_code == 200
    assert b'resolved' in resp.content, \
        'Success feedback must be visible after the redirect.'


# ── Phase 1: document email (PDF repair reports / quotes) ───────────────────
# base.html messages now render; this layer adds emailing MB-generated PDFs.
# The send helper is tested directly (no SMTP/WeasyPrint); the real PDF render
# and the full view are gated behind a skip so CI stays green if the WeasyPrint
# system libs aren't installed on the runner.

def _weasyprint_ok():
    try:
        import weasyprint
        weasyprint.HTML(string='<p>x</p>').write_pdf()
        return True
    except Exception:
        return False


pdf_skip = pytest.mark.skipif(not _weasyprint_ok(),
                              reason='WeasyPrint system libs not installed')


def _enable_email():
    from core.models import SiteSettings
    site = SiteSettings.get()
    site.email_enabled = True
    site.email_from = 'support@example.com'
    site.save()
    return site


@pytest.mark.django_db
def test_send_document_email_sends_and_logs(monkeypatch, client_obj):
    from django.core.mail import EmailMultiAlternatives
    from core.email_utils import send_document_email
    from core.models import EmailSendLog
    _enable_email()

    captured = {}

    def fake_send(self, fail_silently=False):
        captured['attachments'] = list(self.attachments)
        captured['to'] = list(self.to)
        captured['from'] = self.from_email
        return 1

    monkeypatch.setattr(EmailMultiAlternatives, 'send', fake_send)

    log = send_document_email(
        'wayne@davis.example', subject='Your Report',
        cover_body='Here is your report.',
        attachments=[('Repair-Report-WO-1.pdf', b'%PDF-fake', 'application/pdf')],
        client=client_obj, trigger='wo_report',
    )

    assert log.status == 'sent'
    assert log.trigger == 'wo_report'
    assert captured['to'] == ['wayne@davis.example']
    assert captured['from'] == 'support@example.com'
    assert any(a[0].endswith('.pdf') for a in captured['attachments']), \
        'The PDF must be attached to the email.'
    assert EmailSendLog.objects.filter(status='sent', trigger='wo_report').exists()


@pytest.mark.django_db
def test_send_document_email_honors_client_suppression(client_obj):
    from core.email_utils import send_document_email
    _enable_email()
    client_obj.suppress_emails = True
    client_obj.save()

    log = send_document_email(
        'x@example.com', subject='S', cover_body='B',
        attachments=[('a.pdf', b'%PDF', 'application/pdf')],
        client=client_obj, trigger='wo_report',
    )
    assert log.status == 'suppressed'
    assert log.reason == 'client_flag'


@pytest.mark.django_db
def test_send_document_email_no_address_is_logged(client_obj):
    from core.email_utils import send_document_email
    _enable_email()
    log = send_document_email(
        '', subject='S', cover_body='B', client=client_obj, trigger='wo_report',
    )
    assert log.status == 'suppressed'
    assert log.reason == 'no_address'


@pytest.mark.django_db
def test_send_document_email_respects_contact_optout(client_obj):
    from core.email_utils import send_document_email
    from core.models import Contact
    _enable_email()
    c = Contact.objects.create(client=client_obj, first_name='No', last_name='Mail',
                               email='no@example.com', receives_email=False)
    log = send_document_email(
        'no@example.com', subject='S', cover_body='B',
        attachments=[('a.pdf', b'%PDF', 'application/pdf')],
        client=client_obj, contact=c, trigger='wo_report',
    )
    assert log.status == 'suppressed'
    assert log.reason == 'contact_flag'


@pdf_skip
@pytest.mark.django_db
def test_render_pdf_produces_pdf_bytes():
    from core.pdf_utils import render_pdf
    out = render_pdf('<h1>Murphy\'s Bench</h1><p>Report.</p>')
    assert out[:5] == b'%PDF-'
    assert len(out) > 500


@pdf_skip
@pytest.mark.django_db
def test_email_report_view_renders_pdf_and_sends(monkeypatch, client, client_obj, admin_user):
    from django.core.mail import EmailMultiAlternatives
    from core.models import EmailSendLog, Contact
    _enable_email()
    contact = Contact.objects.create(client=client_obj, first_name='Wayne', last_name='Davis',
                                     email='wayne@davis.example', is_primary=True)
    wo = WorkOrder.objects.create(client=client_obj, contact=contact)

    captured = {}

    def fake_send(self, fail_silently=False):
        captured['attachments'] = list(self.attachments)
        return 1

    monkeypatch.setattr(EmailMultiAlternatives, 'send', fake_send)
    client.force_login(admin_user)

    resp = client.post(reverse('core:work_order_email_report', args=[wo.pk]),
                       {'contact': contact.pk}, follow=True)

    assert resp.status_code == 200
    assert EmailSendLog.objects.filter(status='sent', trigger='wo_report').exists()
    assert captured['attachments'], 'A PDF attachment must be present.'
    assert captured['attachments'][0][0] == f'Repair-Report-{wo.work_order_number}.pdf'
    assert captured['attachments'][0][1][:5] == b'%PDF-', 'Attachment must be real PDF bytes.'


@pytest.mark.django_db
def test_email_report_form_page_renders(client, client_obj, admin_user):
    """The GET recipient form renders (template smoke) with the WO's contacts."""
    from core.models import Contact
    contact = Contact.objects.create(client=client_obj, first_name='Wayne',
                                     last_name='Davis', email='wayne@davis.example',
                                     is_primary=True)
    wo = WorkOrder.objects.create(client=client_obj, contact=contact)
    client.force_login(admin_user)
    resp = client.get(reverse('core:work_order_email_report', args=[wo.pk]))
    assert resp.status_code == 200
    assert b'Email Repair Report' in resp.content
    assert b'wayne@davis.example' in resp.content


# ── Regression: device "Save & Create WO" redirects without NoReverseMatch ──
# The DeviceCreate/Update form_valid built the redirect with the wrong URL name
# ('core:workorder_create' — never existed), 500ing /devices/new/ on that path.

@pytest.mark.django_db
def test_device_save_and_create_wo_redirects_to_work_order_create(client, client_obj, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse('core:device_create'), {
        'client': client_obj.pk,
        'name': 'Bench Laptop',
        'device_type': 'laptop',
        'save_and_create_wo': '1',
    })
    assert resp.status_code == 302, 'Save & Create WO must redirect, not 500.'
    device = Device.objects.get(name='Bench Laptop')
    assert resp.url == reverse('core:work_order_create') + f'?device={device.pk}'


@pytest.mark.django_db
def test_device_edit_save_and_create_wo_redirects(client, client_obj, admin_user):
    device = Device.objects.create(client=client_obj, name='Edit Me', device_type='laptop')
    client.force_login(admin_user)
    resp = client.post(reverse('core:device_edit', args=[device.pk]), {
        'client': client_obj.pk,
        'name': 'Edit Me',
        'device_type': 'laptop',
        'save_and_create_wo': '1',
    })
    assert resp.status_code == 302
    assert resp.url == reverse('core:work_order_create') + f'?device={device.pk}'


# ── New Client page: optional embedded device card ──────────────────────────
# Client name and device name are both "name" model fields, so the embedded
# device form is bound with prefix='device' (device-name, device-device_type,
# etc.) to keep them from colliding as the same POST key in one <form>.

@pytest.mark.django_db
def test_new_client_with_device_fields_creates_both(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse('core:client_create'), {
        'name': 'Wayne Enterprises',
        'client_type': 'business',
        'is_active': 'on',
        'device-name': 'Front Desk Laptop',
        'device-device_type': 'laptop',
        'device-manufacturer': 'Dell',
        'device-model': 'XPS 13',
    })
    assert resp.status_code == 302
    new_client = Client.objects.get(name='Wayne Enterprises')
    device = Device.objects.get(client=new_client)
    assert device.name == 'Front Desk Laptop'
    assert device.manufacturer == 'Dell'
    assert device.model == 'XPS 13'
    assert resp.url == reverse('core:client_detail', kwargs={'pk': new_client.pk})


@pytest.mark.django_db
def test_new_client_without_device_creates_only_client(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse('core:client_create'), {
        'name': 'Solo Client',
        'client_type': 'residential',
        'is_active': 'on',
        'device-device_type': 'laptop',
    })
    assert resp.status_code == 302
    new_client = Client.objects.get(name='Solo Client')
    assert Device.objects.filter(client=new_client).count() == 0


@pytest.mark.django_db
def test_new_client_invalid_device_serial_reblocks_client_save(client, admin_user, client_obj):
    """A duplicate serial number on the embedded device form must fail the
    whole page (no orphan client saved) rather than 500 or silently drop it."""
    Device.objects.create(client=client_obj, name='Existing', serial_number='SN-DUPE')
    client.force_login(admin_user)
    resp = client.post(reverse('core:client_create'), {
        'name': 'Should Not Save',
        'client_type': 'business',
        'is_active': 'on',
        'device-name': 'New Device',
        'device-device_type': 'laptop',
        'device-serial_number': 'SN-DUPE',
    })
    assert resp.status_code == 200
    assert not Client.objects.filter(name='Should Not Save').exists()


# ── Slice 0: Prospect (customer spine) ──────────────────────────────────────

from core.models import Prospect, Role


@pytest.mark.django_db
def test_prospect_create_via_view(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse('core:prospect_create'), {
        'contact_first_name': 'Dana',
        'contact_last_name': 'Reyes',
        'company': '',
        'client_type': 'residential',
        'email': 'dana@example.com',
        'phone': '',
        'status': 'new',
        'notes': '',
    })
    assert resp.status_code == 302
    p = Prospect.objects.get(email='dana@example.com')
    assert p.created_by == admin_user
    assert p.status == 'new'


@pytest.mark.django_db
def test_business_prospect_requires_company():
    from core.forms import ProspectForm
    form = ProspectForm(data={
        'contact_first_name': 'Sam', 'contact_last_name': 'Lee', 'company': '',
        'client_type': 'business', 'email': '', 'phone': '', 'status': 'new', 'notes': '',
    })
    assert not form.is_valid()
    assert 'company' in form.errors


@pytest.mark.django_db
def test_promote_business_creates_client_and_contact(client, admin_user):
    p = Prospect.objects.create(
        contact_first_name='Pat', contact_last_name='Kim', company='Globex',
        client_type='business', email='pat@globex.com', phone='555-1212',
    )
    client.force_login(admin_user)
    resp = client.post(reverse('core:prospect_promote', args=[p.pk]))
    assert resp.status_code == 302

    p.refresh_from_db()
    assert p.is_promoted
    assert p.status == 'won'
    new_client = p.promoted_to
    assert new_client.name == 'Globex'
    assert new_client.client_type == 'business'
    contact = new_client.contacts.get()
    assert (contact.first_name, contact.last_name) == ('Pat', 'Kim')
    assert contact.is_primary


@pytest.mark.django_db
def test_promote_residential_names_client_for_person(client, admin_user):
    p = Prospect.objects.create(
        contact_first_name='Jo', contact_last_name='Park',
        client_type='residential', email='jo@example.com',
    )
    client.force_login(admin_user)
    client.post(reverse('core:prospect_promote', args=[p.pk]))
    p.refresh_from_db()
    assert p.promoted_to.name == 'Jo Park'
    assert p.promoted_to.client_type == 'residential'


@pytest.mark.django_db
def test_prospect_cannot_be_promoted_twice(admin_user):
    p = Prospect.objects.create(
        contact_first_name='One', contact_last_name='Time', company='OnceCo',
        client_type='business',
    )
    first = p.promote_to_client()
    second = p.promote_to_client()
    assert first.pk == second.pk
    assert Client.objects.filter(name='OnceCo').count() == 1


@pytest.mark.django_db
def test_promoted_prospect_cannot_be_deleted(client, admin_user):
    p = Prospect.objects.create(
        contact_first_name='No', contact_last_name='Del', company='KeepCo',
        client_type='business',
    )
    p.promote_to_client()
    client.force_login(admin_user)
    client.post(reverse('core:prospect_delete', args=[p.pk]))
    assert Prospect.objects.filter(pk=p.pk).exists()


@pytest.mark.django_db
def test_prospect_list_hidden_when_role_blocks(client):
    role = Role.objects.create(name='No Prospects', can_view_prospects=False)
    user = User.objects.create_user(username='blocked', password='x', is_staff=False)
    user.role_obj = role
    user.save()
    client.force_login(user)
    resp = client.get(reverse('core:prospect_list'))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_mark_lost_excludes_from_default_list(client, admin_user):
    p = Prospect.objects.create(
        contact_first_name='Lost', contact_last_name='Lead',
        client_type='residential',
    )
    client.force_login(admin_user)
    client.post(reverse('core:prospect_mark_lost', args=[p.pk]))
    p.refresh_from_db()
    assert p.status == 'lost'
    resp = client.get(reverse('core:prospect_list'))
    assert p not in resp.context['prospects']


@pytest.mark.django_db
def test_prospect_form_and_detail_render(client, admin_user):
    p = Prospect.objects.create(
        contact_first_name='Ren', contact_last_name='Vox', company='Vox LLC',
        client_type='business', email='r@vox.com',
    )
    client.force_login(admin_user)
    assert client.get(reverse('core:prospect_create')).status_code == 200
    assert client.get(reverse('core:prospect_detail', args=[p.pk])).status_code == 200
    assert client.get(reverse('core:prospect_edit', args=[p.pk])).status_code == 200


# ---------------------------------------------------------------------------
# Slice 1 — IN status check
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_check_invoice_status_records_label(admin_user):
    """check_invoice_status() writes in_status + in_status_checked_at onto the Invoice."""
    from unittest.mock import patch
    from core import invoice_ninja
    from core.models import Invoice, WorkOrder, Client

    c = Client.objects.create(name='StatusCo')
    wo = WorkOrder.objects.create(client=c, invoice_ninja_id='abc123')
    Invoice.objects.get_or_create(work_order=wo)

    fake_response = {'data': {'id': 'abc123', 'status_id': 4}}
    with patch('core.invoice_ninja._request', return_value=fake_response):
        label = invoice_ninja.check_invoice_status(wo)

    assert label == 'Paid'
    inv = Invoice.objects.get(work_order=wo)
    assert inv.in_status == 'Paid'
    assert inv.invoice_ninja_id == 'abc123'
    assert inv.in_status_checked_at is not None


@pytest.mark.django_db
def test_billing_check_in_view_updates_card(client, admin_user):
    """POST to wo_billing_check_in re-renders billing_card with IN status."""
    from unittest.mock import patch
    from core.models import Invoice, WorkOrder, Client

    c = Client.objects.create(name='CheckCo')
    wo = WorkOrder.objects.create(client=c, invoice_ninja_id='xyz999')
    Invoice.objects.get_or_create(work_order=wo)

    client.force_login(admin_user)
    fake_response = {'data': {'id': 'xyz999', 'status_id': 2}}
    with patch('core.invoice_ninja._request', return_value=fake_response):
        resp = client.post(reverse('core:wo_billing_check_in', args=[wo.pk]))

    assert resp.status_code == 200
    assert b'Sent' in resp.content


# ---------------------------------------------------------------------------
# Slice 2a — Estimate model + CRUD + line items
# ---------------------------------------------------------------------------

from core.models import Estimate, Prospect as _Prospect, CatalogItem, EstimateOption


@pytest.mark.django_db
def test_estimate_number_sequential_and_unique(client_obj):
    e1 = Estimate.objects.create(client=client_obj)
    e2 = Estimate.objects.create(client=client_obj)
    assert e1.estimate_number == 'EST-00001'
    assert e2.estimate_number == 'EST-00002'


@pytest.mark.django_db
def test_estimate_requires_exactly_one_anchor(client_obj):
    from django.core.exceptions import ValidationError
    prospect = _Prospect.objects.create(
        contact_first_name='Lee', client_type='residential',
    )
    # Neither set -> invalid
    e = Estimate(scope='nothing')
    with pytest.raises(ValidationError):
        e.clean()
    # Both set -> invalid
    e2 = Estimate(client=client_obj, prospect=prospect)
    with pytest.raises(ValidationError):
        e2.clean()
    # Exactly one -> valid
    e3 = Estimate(client=client_obj)
    e3.clean()  # should not raise


@pytest.mark.django_db
def test_estimate_create_is_instant_and_lands_on_detail(client, admin_user):
    """New Estimate is a one-click action (no intermediate form): POSTing
    creates a blank unanchored draft and redirects straight to its detail
    page — Client/Prospect/Scope are set afterward via the inline Details
    card (mirrors the Sale create flow)."""
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_create'), {})
    assert resp.status_code == 302
    est = Estimate.objects.get()
    assert est.created_by == admin_user
    assert est.client_id is None
    assert est.prospect_id is None
    assert est.status == 'draft'
    assert resp.url == reverse('core:estimate_detail', args=[est.pk])


@pytest.mark.django_db
def test_estimate_quick_update_sets_client_and_scope(client, admin_user, client_obj):
    est = Estimate.objects.create(created_by=admin_user)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_quick_update', args=[est.pk]), {
        'client': client_obj.pk, 'scope': 'New laptop setup',
    })
    assert resp.status_code == 200
    est.refresh_from_db()
    assert est.client_id == client_obj.pk
    assert est.scope == 'New laptop setup'


@pytest.mark.django_db
def test_estimate_quick_update_client_clears_prospect(client, admin_user, client_obj):
    prospect = Prospect.objects.create(contact_first_name='Lee', client_type='residential')
    est = Estimate.objects.create(created_by=admin_user, prospect=prospect)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_quick_update', args=[est.pk]), {
        'client': client_obj.pk,
    })
    assert resp.status_code == 200
    est.refresh_from_db()
    assert est.client_id == client_obj.pk
    assert est.prospect_id is None


@pytest.mark.django_db
def test_estimate_quick_update_prospect_clears_client(client, admin_user, client_obj):
    prospect = Prospect.objects.create(contact_first_name='Lee', client_type='residential')
    est = Estimate.objects.create(created_by=admin_user, client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_quick_update', args=[est.pk]), {
        'prospect': prospect.pk,
    })
    assert resp.status_code == 200
    est.refresh_from_db()
    assert est.prospect_id == prospect.pk
    assert est.client_id is None


@pytest.mark.django_db
def test_estimate_quick_update_scope_only_does_not_touch_client(client, admin_user, client_obj):
    est = Estimate.objects.create(created_by=admin_user, client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_quick_update', args=[est.pk]), {
        'scope': 'Typed on blur',
    })
    assert resp.status_code == 200
    est.refresh_from_db()
    assert est.scope == 'Typed on blur'
    assert est.client_id == client_obj.pk


@pytest.mark.django_db
def test_estimate_quick_update_blocked_when_locked(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj, status='accepted')
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_quick_update', args=[est.pk]), {
        'scope': 'should not save',
    })
    assert resp.status_code == 200
    est.refresh_from_db()
    assert est.scope != 'should not save'


@pytest.mark.django_db
def test_estimate_list_hides_closed_by_default(client, admin_user, client_obj):
    open_est = Estimate.objects.create(client=client_obj)
    closed_est = Estimate.objects.create(client=client_obj, status='accepted')
    client.force_login(admin_user)
    resp = client.get(reverse('core:estimate_list'))
    ests = list(resp.context['estimates'])
    assert open_est in ests
    assert closed_est not in ests


@pytest.mark.django_db
def test_estimate_line_items_total_ignores_unpriced(client_obj):
    from decimal import Decimal
    est = Estimate.objects.create(client=client_obj)
    est.line_items.create(kind='labor', description='Diag', quantity=1, unit_price=Decimal('50'))
    est.line_items.create(kind='part', description='SSD', quantity=2, unit_price=Decimal('40'))
    est.line_items.create(kind='labor', description='Unpriced note')  # no unit_price
    assert est.line_items_total == Decimal('130')


@pytest.mark.django_db
def test_estimate_mark_sent_transitions_draft_to_sent(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_mark_sent', args=[est.pk]))
    est.refresh_from_db()
    assert resp.status_code == 302
    assert est.status == 'sent'


@pytest.mark.django_db
def test_estimate_delete_blocked_when_accepted(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj, status='accepted')
    client.force_login(admin_user)
    client.post(reverse('core:estimate_delete', args=[est.pk]))
    assert Estimate.objects.filter(pk=est.pk).exists()


@pytest.mark.django_db
def test_estimate_access_mixin_blocks_on_role_flag(client, client_obj):
    role = Role.objects.create(name='NoEstimates', can_view_estimates=False)
    user = User.objects.create_user(username='tech1', password='x', role_obj=role)
    client.force_login(user)
    resp = client.get(reverse('core:estimate_list'))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_estimate_labor_log_creates_line_item_on_estimate(client, admin_user, client_obj):
    item = CatalogItem.objects.create(name='Virus Removal', category='Software', default_price='75.00')
    est = Estimate.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_labor_log', args=[est.pk, item.pk]))
    assert resp.status_code == 200
    li = est.line_items.get()
    assert li.description == 'Virus Removal'
    assert li.catalog_item_id == item.pk


@pytest.mark.django_db
def test_estimate_custom_log_creates_line_item_on_estimate(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_custom_log', args=[est.pk]), {
        'kind': 'part', 'custom_label': '1TB SSD', 'quantity': '1', 'unit_price': '60',
    })
    assert resp.status_code == 200
    li = est.line_items.get()
    assert li.kind == 'part'
    assert li.description == '1TB SSD'


# ---------------------------------------------------------------------------
# Slice 2b — Quote PDF + sales email
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_quote_email_uses_sales_from_when_set(client_obj):
    from django.core.mail import EmailMultiAlternatives
    from core.email_utils import send_document_email
    site = _enable_email()
    site.email_sales_from = 'sales@example.com'
    site.save()

    captured = {}

    def fake_send(self, fail_silently=False):
        captured['from'] = self.from_email
        return 1

    import unittest.mock
    with unittest.mock.patch.object(EmailMultiAlternatives, 'send', fake_send):
        log = send_document_email(
            'x@example.com', subject='Quote', cover_body='B',
            from_email=site.email_sales_from,
            attachments=[('a.pdf', b'%PDF', 'application/pdf')],
            client=client_obj, trigger='estimate_quote',
        )
    assert log.status == 'sent'
    assert captured['from'] == 'sales@example.com'


@pytest.mark.django_db
def test_quote_email_falls_back_to_support_from_when_sales_blank(client_obj):
    from django.core.mail import EmailMultiAlternatives
    from core.email_utils import send_document_email
    site = _enable_email()
    assert not site.email_sales_from

    sales_from = site.email_sales_from or site.email_from or None
    captured = {}

    def fake_send(self, fail_silently=False):
        captured['from'] = self.from_email
        return 1

    import unittest.mock
    with unittest.mock.patch.object(EmailMultiAlternatives, 'send', fake_send):
        log = send_document_email(
            'x@example.com', subject='Quote', cover_body='B',
            from_email=sales_from,
            attachments=[('a.pdf', b'%PDF', 'application/pdf')],
            client=client_obj, trigger='estimate_quote',
        )
    assert log.status == 'sent'
    assert captured['from'] == site.email_from


@pdf_skip
@pytest.mark.django_db
def test_quote_email_view_client_anchored_sends_and_marks_sent(monkeypatch, client, client_obj, admin_user):
    from django.core.mail import EmailMultiAlternatives
    from core.models import EmailSendLog, Contact
    _enable_email()
    contact = Contact.objects.create(client=client_obj, first_name='Wayne', last_name='Davis',
                                     email='wayne@davis.example', is_primary=True)
    est = Estimate.objects.create(client=client_obj, contact=contact, scope='New laptop')
    est.line_items.create(kind='labor', description='Setup', quantity=1, unit_price='100')
    assert est.status == 'draft'

    captured = {}

    def fake_send(self, fail_silently=False):
        captured['attachments'] = list(self.attachments)
        return 1

    monkeypatch.setattr(EmailMultiAlternatives, 'send', fake_send)
    client.force_login(admin_user)

    resp = client.post(reverse('core:estimate_quote_email', args=[est.pk]),
                       {'contact': contact.pk}, follow=True)

    assert resp.status_code == 200
    assert EmailSendLog.objects.filter(status='sent', trigger='estimate_quote').exists()
    assert captured['attachments'][0][0] == f'Quote-{est.estimate_number}.pdf'
    assert captured['attachments'][0][1][:5] == b'%PDF-'
    est.refresh_from_db()
    assert est.status == 'sent'


@pdf_skip
@pytest.mark.django_db
def test_quote_email_view_prospect_anchored_uses_custom_address(monkeypatch, client, admin_user):
    from django.core.mail import EmailMultiAlternatives
    from core.models import EmailSendLog
    _enable_email()
    prospect = _Prospect.objects.create(
        contact_first_name='Lee', contact_last_name='Voss',
        client_type='residential', email='lee@example.com',
    )
    est = Estimate.objects.create(prospect=prospect, scope='Desktop build')
    est.line_items.create(kind='part', description='GPU', quantity=1, unit_price='400')

    captured = {}

    def fake_send(self, fail_silently=False):
        captured['to'] = list(self.to)
        return 1

    monkeypatch.setattr(EmailMultiAlternatives, 'send', fake_send)
    client.force_login(admin_user)

    resp = client.post(reverse('core:estimate_quote_email', args=[est.pk]),
                       {'custom_email': 'lee@example.com'}, follow=True)

    assert resp.status_code == 200
    assert EmailSendLog.objects.filter(status='sent', trigger='estimate_quote').exists()
    assert captured['to'] == ['lee@example.com']
    est.refresh_from_db()
    assert est.status == 'sent'


@pdf_skip
@pytest.mark.django_db
def test_quote_email_does_not_revert_already_sent_estimate(monkeypatch, client, client_obj, admin_user):
    from django.core.mail import EmailMultiAlternatives
    _enable_email()
    est = Estimate.objects.create(client=client_obj, status='sent')
    est.line_items.create(kind='labor', description='Diag', quantity=1, unit_price='50')

    monkeypatch.setattr(EmailMultiAlternatives, 'send', lambda self, fail_silently=False: 1)
    client.force_login(admin_user)
    client.post(reverse('core:estimate_quote_email', args=[est.pk]), {'custom_email': 'x@example.com'})

    est.refresh_from_db()
    assert est.status == 'sent'


@pytest.mark.django_db
def test_quote_print_view_renders_with_total(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj)
    est.line_items.create(kind='labor', description='Diag', quantity=1, unit_price='75')
    client.force_login(admin_user)
    resp = client.get(reverse('core:estimate_quote_print', args=[est.pk]))
    assert resp.status_code == 200
    assert est.estimate_number.encode() in resp.content
    assert b'75.00' in resp.content


# ---------------------------------------------------------------------------
# Slice 2c — Estimate lifecycle: accept / decline / revise
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_accept_client_estimate_creates_wo_with_copied_lines(client, admin_user, client_obj):
    from decimal import Decimal
    est = Estimate.objects.create(client=client_obj, scope='Tune-up + SSD')
    est.line_items.create(kind='labor', description='Tune-up', quantity=1, unit_price=Decimal('80'))
    est.line_items.create(kind='part', description='SSD', quantity=2, unit_price=Decimal('50'))
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_accept', args=[est.pk]))

    assert resp.status_code == 302
    est.refresh_from_db()
    assert est.status == 'accepted'
    assert est.accepted_at is not None
    assert est.work_order is not None
    assert est.is_locked
    wo = est.work_order
    assert wo.client_id == client_obj.pk
    assert wo.reported_problem == 'Tune-up + SSD'
    assert wo.line_items.count() == 2
    assert wo.line_items_total == Decimal('180')


# ── Estimate Options — comparative pricing choices on one quote ─────────────

@pytest.mark.django_db
def test_estimate_option_create_and_totals_independent(client, admin_user, client_obj):
    from decimal import Decimal
    est = Estimate.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_option_create', args=[est.pk]), {'label': 'Budget'})
    assert resp.status_code == 200
    resp = client.post(reverse('core:estimate_option_create', args=[est.pk]), {'label': 'Premium'})
    assert resp.status_code == 200
    assert est.options.count() == 2
    budget, premium = est.options.order_by('sort_order')
    assert budget.label == 'Budget'
    assert premium.label == 'Premium'

    budget.line_items.create(kind='part', description='Refurb SSD', quantity=1, unit_price=Decimal('150'))
    premium.line_items.create(kind='part', description='New NVMe', quantity=1, unit_price=Decimal('400'))
    assert budget.total == Decimal('150')
    assert premium.total == Decimal('400')
    # Options are self-contained — an item on one never bleeds into the other's total.
    assert est.line_items_total == Decimal('0')


@pytest.mark.django_db
def test_estimate_general_subtotal_still_shown_when_options_exist(client, admin_user, client_obj):
    """Regression: General items had no visible subtotal at all once any
    option existed (the template hid the whole block instead of just
    relabeling it) — found live via a real quote with a General item plus
    two options, where the General total simply vanished on both the detail
    page and the printed quote."""
    from decimal import Decimal
    est = Estimate.objects.create(client=client_obj)
    est.line_items.create(kind='labor', description='All Ubiquity', quantity=1, unit_price=Decimal('10000'))
    est.options.create(label='All Cisco')
    client.force_login(admin_user)

    resp = client.get(reverse('core:estimate_detail', args=[est.pk]))
    body = resp.content.decode()
    assert 'Subtotal' in body
    assert '10000.00' in body

    resp = client.get(reverse('core:estimate_quote_print', args=[est.pk]))
    body = resp.content.decode()
    assert '10000.00' in body
    assert 'Subtotal' in body or 'Total' in body


@pytest.mark.django_db
def test_estimate_general_label_defaults_and_renames(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj)
    assert est.general_label == 'General'
    est.options.create(label='Cisco')  # label only renders once options exist

    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_general_label_update', args=[est.pk]), {
        'general_label': 'Common Costs',
    })
    assert resp.status_code == 200
    est.refresh_from_db()
    assert est.general_label == 'Common Costs'
    assert 'Common Costs' in resp.content.decode()


@pytest.mark.django_db
def test_estimate_general_label_blank_falls_back_to_default(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj, general_label='Custom')
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_general_label_update', args=[est.pk]), {
        'general_label': '   ',
    })
    assert resp.status_code == 200
    est.refresh_from_db()
    assert est.general_label == 'General'


@pytest.mark.django_db
def test_estimate_general_label_blocked_when_locked(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj, status='accepted', general_label='Original')
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_general_label_update', args=[est.pk]), {
        'general_label': 'Should not save',
    })
    assert resp.status_code == 200
    est.refresh_from_db()
    assert est.general_label == 'Original'


@pytest.mark.django_db
def test_estimate_option_select_clears_sibling_selection(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj)
    a = est.options.create(label='A')
    b = est.options.create(label='B', is_selected=True)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_option_select', args=[a.pk]))
    assert resp.status_code == 200
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.is_selected is True
    assert b.is_selected is False


@pytest.mark.django_db
def test_estimate_option_delete_removes_its_line_items(client, admin_user, client_obj):
    from decimal import Decimal
    est = Estimate.objects.create(client=client_obj)
    option = est.options.create(label='Standard')
    option.line_items.create(kind='part', description='Battery', quantity=1, unit_price=Decimal('60'))
    li_pk = option.line_items.first().pk
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_option_delete', args=[option.pk]))
    assert resp.status_code == 200
    assert not EstimateOption.objects.filter(pk=option.pk).exists()
    from core.models import LineItem
    assert not LineItem.objects.filter(pk=li_pk).exists()


@pytest.mark.django_db
def test_estimate_option_custom_log_creates_scoped_line_item(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj)
    option = est.options.create(label='Standard')
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_option_custom_log', args=[option.pk]), {
        'kind': 'part', 'custom_label': '1TB SSD', 'quantity': '1', 'unit_price': '120',
    })
    assert resp.status_code == 200
    assert option.line_items.count() == 1
    li = option.line_items.first()
    assert li.description == '1TB SSD'
    assert li.content_object == option
    assert est.line_items.count() == 0


@pytest.mark.django_db
def test_estimate_accept_requires_selection_when_options_exist(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj)
    est.options.create(label='A')
    est.options.create(label='B')
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_accept', args=[est.pk]))
    assert resp.status_code == 302
    est.refresh_from_db()
    assert est.status == 'draft'
    assert est.work_order is None


@pytest.mark.django_db
def test_estimate_accept_copies_only_selected_option_lines(client, admin_user, client_obj):
    from decimal import Decimal
    est = Estimate.objects.create(client=client_obj, scope='Replace device')
    est.line_items.create(kind='labor', description='Diagnostic', quantity=1, unit_price=Decimal('40'))
    budget = est.options.create(label='Budget')
    budget.line_items.create(kind='part', description='Refurb unit', quantity=1, unit_price=Decimal('150'))
    premium = est.options.create(label='Premium', is_selected=True)
    premium.line_items.create(kind='part', description='New unit', quantity=1, unit_price=Decimal('400'))
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_accept', args=[est.pk]))
    assert resp.status_code == 302
    est.refresh_from_db()
    assert est.status == 'accepted'
    wo = est.work_order
    descriptions = set(wo.line_items.values_list('description', flat=True))
    assert descriptions == {'Diagnostic', 'New unit'}
    assert wo.line_items_total == Decimal('440')


@pytest.mark.django_db
def test_estimate_option_actions_blocked_when_locked(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj, status='accepted')
    option = est.options.create(label='Standard')
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_option_create', args=[est.pk]), {'label': 'New Option'})
    assert resp.status_code == 200
    assert est.options.count() == 1
    resp = client.post(reverse('core:estimate_option_custom_log', args=[option.pk]), {
        'kind': 'part', 'custom_label': 'Should not save', 'quantity': '1', 'unit_price': '10',
    })
    assert resp.status_code == 200
    assert option.line_items.count() == 0


@pytest.mark.django_db
def test_estimate_quote_print_blocked_when_unanchored(client, admin_user):
    """A brand-new blank draft (Round 1: creation lands unanchored) must not
    crash the quote print/PDF view — it should redirect with a message
    instead of hitting AttributeError on a None prospect."""
    est = Estimate.objects.create()
    client.force_login(admin_user)
    resp = client.get(reverse('core:estimate_quote_print', args=[est.pk]))
    assert resp.status_code == 302
    assert resp.url == reverse('core:estimate_detail', args=[est.pk])


@pytest.mark.django_db
def test_estimate_quote_email_blocked_when_unanchored(client, admin_user):
    est = Estimate.objects.create()
    client.force_login(admin_user)
    resp = client.get(reverse('core:estimate_quote_email', args=[est.pk]))
    assert resp.status_code == 302
    resp = client.post(reverse('core:estimate_quote_email', args=[est.pk]), {'custom_email': 'x@example.com'})
    assert resp.status_code == 302


@pytest.mark.django_db
def test_accept_prospect_estimate_promotes_and_reanchors(client, admin_user):
    from core.models import Client as ClientModel
    prospect = _Prospect.objects.create(
        contact_first_name='Pat', contact_last_name='Quinn',
        client_type='business', company='Quinn LLC', email='pat@quinn.example',
    )
    est = Estimate.objects.create(prospect=prospect, scope='Network setup')
    est.line_items.create(kind='labor', description='Install', quantity=1, unit_price='200')
    client.force_login(admin_user)
    client.post(reverse('core:estimate_accept', args=[est.pk]))

    est.refresh_from_db()
    prospect.refresh_from_db()
    assert prospect.is_promoted
    new_client = ClientModel.objects.get(name='Quinn LLC')
    assert new_client.contacts.filter(is_primary=True).exists()
    assert est.client_id == new_client.pk
    assert est.prospect_id is None
    assert est.status == 'accepted'
    assert est.work_order is not None


@pytest.mark.django_db
def test_accept_when_ticket_already_has_wo_creates_standalone(client, admin_user, client_obj):
    from core.models import Ticket
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    WorkOrder.objects.create(client=client_obj, ticket=ticket)  # ticket already converted
    est = Estimate.objects.create(client=client_obj, ticket=ticket, scope='More work')
    est.line_items.create(kind='labor', description='Extra', quantity=1, unit_price='40')
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_accept', args=[est.pk]))

    assert resp.status_code == 302  # no IntegrityError
    est.refresh_from_db()
    assert est.status == 'accepted'
    assert est.work_order.ticket_id is None  # standalone — didn't steal the OneToOne


@pytest.mark.django_db
def test_accept_rejected_from_invalid_status(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj, status='declined')
    client.force_login(admin_user)
    client.post(reverse('core:estimate_accept', args=[est.pk]))
    est.refresh_from_db()
    assert est.status == 'declined'
    assert est.work_order is None


@pytest.mark.django_db
def test_decline_requires_reason_and_records_it(client, admin_user, client_obj):
    est = Estimate.objects.create(client=client_obj, status='sent')
    client.force_login(admin_user)
    # No reason → no transition
    client.post(reverse('core:estimate_decline', args=[est.pk]), {'decline_reason': '  '})
    est.refresh_from_db()
    assert est.status == 'sent'
    # With reason → declined
    client.post(reverse('core:estimate_decline', args=[est.pk]), {'decline_reason': 'Too expensive'})
    est.refresh_from_db()
    assert est.status == 'declined'
    assert est.decline_reason == 'Too expensive'


@pytest.mark.django_db
def test_revise_creates_linked_draft_and_freezes_original(client, admin_user, client_obj):
    from decimal import Decimal
    old = Estimate.objects.create(client=client_obj, status='sent', scope='v1')
    old.line_items.create(kind='labor', description='Work', quantity=1, unit_price=Decimal('100'))
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_revise', args=[old.pk]))

    assert resp.status_code == 302
    new = Estimate.objects.exclude(pk=old.pk).get()
    assert new.revision_of_id == old.pk
    assert new.status == 'draft'
    assert new.client_id == client_obj.pk
    assert new.line_items.count() == 1
    assert new.line_items_total == Decimal('100')
    old.refresh_from_db()
    assert old.is_locked  # superseded → read-only


@pytest.mark.django_db
def test_revise_rejected_from_draft(client, admin_user, client_obj):
    old = Estimate.objects.create(client=client_obj, status='draft')
    client.force_login(admin_user)
    client.post(reverse('core:estimate_revise', args=[old.pk]))
    assert Estimate.objects.count() == 1  # no revision spawned


# ---------------------------------------------------------------------------
# Slice 3a — Sale model + CRUD + line items (Counter lane)
# ---------------------------------------------------------------------------

from core.models import Sale


@pytest.mark.django_db
def test_sale_number_sequential_and_unique(client_obj):
    s1 = Sale.objects.create(client=client_obj)
    s2 = Sale.objects.create(client=client_obj)
    assert s1.sale_number == 'SALE-00001'
    assert s2.sale_number == 'SALE-00002'


@pytest.mark.django_db
def test_sale_client_is_optional_for_anonymous_walkin():
    sale = Sale.objects.create()
    assert sale.client_id is None
    assert sale.display_name == 'Walk-in'


@pytest.mark.django_db
def test_sale_create_is_instant_and_lands_on_detail(client, admin_user):
    """New Sale is a one-click action (no intermediate form): POSTing creates
    a blank draft and redirects straight to its detail page."""
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_create'), {})
    assert resp.status_code == 302
    sale = Sale.objects.get()
    assert sale.created_by == admin_user
    assert sale.client_id is None
    assert sale.status == 'draft'
    assert resp.url == reverse('core:sale_detail', args=[sale.pk])


@pytest.mark.django_db
def test_sale_quick_update_sets_customer_and_notes(client, admin_user, client_obj):
    sale = Sale.objects.create(created_by=admin_user)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_quick_update', args=[sale.pk]), {
        'client': client_obj.pk, 'notes': 'Cable + adapter',
    })
    assert resp.status_code == 200
    sale.refresh_from_db()
    assert sale.client_id == client_obj.pk
    assert sale.notes == 'Cable + adapter'


@pytest.mark.django_db
def test_sale_quick_update_client_only_saves_and_returns_card(client, admin_user, client_obj):
    """Client and Notes auto-save independently (different hx-trigger each) —
    a Client-only POST must not touch/blank out existing Notes."""
    sale = Sale.objects.create(created_by=admin_user, notes='Existing note')
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_quick_update', args=[sale.pk]), {
        'client': client_obj.pk,
    })
    assert resp.status_code == 200
    assert client_obj.name in resp.content.decode()
    sale.refresh_from_db()
    assert sale.client_id == client_obj.pk
    assert sale.notes == 'Existing note'


@pytest.mark.django_db
def test_sale_quick_update_notes_only_saves(client, admin_user, client_obj):
    """Notes-only POST (blur) must not touch the already-set Client."""
    sale = Sale.objects.create(created_by=admin_user, client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_quick_update', args=[sale.pk]), {
        'notes': 'Typed on blur',
    })
    assert resp.status_code == 200
    sale.refresh_from_db()
    assert sale.notes == 'Typed on blur'
    assert sale.client_id == client_obj.pk


@pytest.mark.django_db
def test_sale_quick_update_blocked_when_locked(client, admin_user, client_obj):
    sale = _completed_sale(client_obj)
    original_client_id = sale.client_id
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_quick_update', args=[sale.pk]), {
        'client': '', 'notes': 'should not save',
    })
    assert resp.status_code == 200
    sale.refresh_from_db()
    assert sale.client_id == original_client_id
    assert sale.notes != 'should not save'


@pytest.mark.django_db
def test_sale_quick_update_role_block_403(client, client_obj):
    role = Role.objects.create(name='NoSales3', can_view_sales=False)
    user = User.objects.create_user(username='tech4', password='x', role_obj=role)
    sale = Sale.objects.create(client=client_obj)
    client.force_login(user)
    resp = client.post(reverse('core:sale_quick_update', args=[sale.pk]), {'client': client_obj.pk})
    assert resp.status_code == 403


@pytest.mark.django_db
def test_sale_list_hides_void_by_default(client, admin_user, client_obj):
    open_sale = Sale.objects.create(client=client_obj)
    void_sale = Sale.objects.create(client=client_obj, status='void')
    client.force_login(admin_user)
    resp = client.get(reverse('core:sale_list'))
    sales = list(resp.context['sales'])
    assert open_sale in sales
    assert void_sale not in sales


@pytest.mark.django_db
def test_sale_line_items_total_ignores_unpriced(client_obj):
    from decimal import Decimal
    sale = Sale.objects.create(client=client_obj)
    sale.line_items.create(kind='part', description='Cable', quantity=2, unit_price=Decimal('10'))
    sale.line_items.create(kind='labor', description='Setup', quantity=1, unit_price=Decimal('25'))
    sale.line_items.create(kind='part', description='Unpriced note')  # no unit_price
    assert sale.line_items_total == Decimal('45')


@pytest.mark.django_db
def test_sale_delete_blocked_when_completed(client, admin_user, client_obj):
    sale = Sale.objects.create(client=client_obj, status='completed')
    client.force_login(admin_user)
    client.post(reverse('core:sale_delete', args=[sale.pk]))
    assert Sale.objects.filter(pk=sale.pk).exists()


@pytest.mark.django_db
def test_sale_delete_allowed_when_draft(client, admin_user, client_obj):
    sale = Sale.objects.create(client=client_obj, status='draft')
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_delete', args=[sale.pk]))
    assert resp.status_code == 302
    assert not Sale.objects.filter(pk=sale.pk).exists()


@pytest.mark.django_db
def test_sale_access_mixin_blocks_on_role_flag(client, client_obj):
    role = Role.objects.create(name='NoSales', can_view_sales=False)
    user = User.objects.create_user(username='tech2', password='x', role_obj=role)
    client.force_login(user)
    resp = client.get(reverse('core:sale_list'))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_sale_labor_log_creates_line_item_on_sale(client, admin_user, client_obj):
    item = CatalogItem.objects.create(name='Data Transfer', category='Software', default_price='45.00')
    sale = Sale.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_labor_log', args=[sale.pk, item.pk]))
    assert resp.status_code == 200
    li = sale.line_items.get()
    assert li.description == 'Data Transfer'
    assert li.catalog_item_id == item.pk


@pytest.mark.django_db
def test_sale_custom_log_creates_line_item_on_sale(client, admin_user, client_obj):
    sale = Sale.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_custom_log', args=[sale.pk]), {
        'kind': 'part', 'custom_label': 'USB Hub', 'quantity': '1', 'unit_price': '15',
    })
    assert resp.status_code == 200
    li = sale.line_items.get()
    assert li.kind == 'part'
    assert li.description == 'USB Hub'


@pytest.mark.django_db
def test_sale_custom_log_refreshes_checkout_card_out_of_band(client, admin_user, client_obj):
    """Regression: the Checkout card lives outside #sale-line-items-section
    (the in-band HTMX swap target), so logging the first priced line left it
    stuck showing 'Add at least one priced line item' until a full reload.
    The response must also carry an OOB swap of #sale-checkout-card."""
    sale = Sale.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_custom_log', args=[sale.pk]), {
        'kind': 'labor', 'custom_label': 'Diagnostic', 'quantity': '1', 'unit_price': '40',
    })
    body = resp.content.decode()
    assert 'id="sale-checkout-card"' in body
    assert 'hx-swap-oob="true"' in body
    assert 'Add at least one priced line item' not in body
    assert 'Complete Sale' in body


@pytest.mark.django_db
def test_sale_line_item_delete_and_update_reuse_shared_endpoints(client, admin_user, client_obj):
    """LineItem edit/delete are host-agnostic (content_object) — confirms Sale
    rides the same WorkPerformedUpdateView/DeleteView as WorkOrder/Estimate."""
    from decimal import Decimal
    sale = Sale.objects.create(client=client_obj)
    sale.line_items.create(kind='part', description='Cable', quantity=1, unit_price=Decimal('10'))
    li = sale.line_items.get()
    client.force_login(admin_user)

    resp = client.post(reverse('core:work_performed_update', args=[li.pk]), {
        'custom_label': 'USB-C Cable', 'quantity': '2', 'unit_price': '12',
    })
    assert resp.status_code == 200
    li.refresh_from_db()
    assert li.description == 'USB-C Cable'
    assert li.quantity == Decimal('2')

    resp = client.post(reverse('core:work_performed_delete', args=[li.pk]))
    assert resp.status_code == 200
    assert sale.line_items.count() == 0


# ── Slice 3b — Sale checkout + Send-to-IN (paid invoice; API mocked) ─────────

def _priced_draft_sale(client_obj=None):
    """A draft sale with one priced line, ready for checkout."""
    from decimal import Decimal
    sale = Sale.objects.create(client=client_obj)
    sale.line_items.create(kind='part', description='Widget', quantity=1, unit_price=Decimal('30'))
    return sale


@pytest.mark.django_db
def test_sale_checkout_amount_prefill_is_quantized_to_cents(client, admin_user, client_obj):
    """Regression: a fractional quantity (e.g. 0.5 hrs labor) can make
    line_items_total carry more than 2 decimal places, which then fails the
    checkout amount field's own step=0.01 validation if submitted unedited."""
    from decimal import Decimal
    sale = Sale.objects.create(client=client_obj)
    sale.line_items.create(kind='labor', description='Setup', quantity=Decimal('0.5'), unit_price=Decimal('60'))
    assert sale.line_items_total == Decimal('30.000')  # confirms the bug precondition
    client.force_login(admin_user)
    resp = client.get(reverse('core:sale_detail', args=[sale.pk]))
    assert resp.context['checkout_form'].initial['amount'] == Decimal('30.00')


@pytest.mark.django_db
def test_sale_checkout_records_payment_and_completes(client, admin_user, client_obj):
    """IN disabled → checkout records the payment locally and completes, no push."""
    from decimal import Decimal
    sale = _priced_draft_sale(client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_checkout', args=[sale.pk]), {
        'payment_method': 'cash', 'amount': '30.00', 'reference': '',
    })
    assert resp.status_code == 302
    sale.refresh_from_db()
    assert sale.status == 'completed'
    assert sale.payment_method == 'cash'
    assert sale.amount == Decimal('30.00')
    assert sale.paid_at is not None
    assert sale.invoice_ninja_id == ''  # IN disabled → nothing pushed


@pytest.mark.django_db
def test_sale_checkout_blocked_without_priced_lines(client, admin_user, client_obj):
    sale = Sale.objects.create(client=client_obj)  # no priced lines
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_checkout', args=[sale.pk]), {
        'payment_method': 'cash', 'amount': '10.00',
    })
    assert resp.status_code == 302
    sale.refresh_from_db()
    assert sale.status == 'draft'  # not completed


@pytest.mark.django_db
def test_sale_checkout_pushes_when_in_enabled(client, admin_user, client_obj, monkeypatch):
    """IN enabled → checkout calls push_sale after recording the payment."""
    from core import invoice_ninja
    _enable_in()
    sale = _priced_draft_sale(client_obj)
    calls = []
    monkeypatch.setattr(invoice_ninja, 'push_sale', lambda s: calls.append(s) or 'INV-5')
    client.force_login(admin_user)
    client.post(reverse('core:sale_checkout', args=[sale.pk]), {
        'payment_method': 'card', 'amount': '30.00', 'reference': 'AUTH123',
    })
    sale.refresh_from_db()
    assert sale.status == 'completed'
    assert len(calls) == 1  # pushed once


@pytest.mark.django_db
def test_sale_checkout_push_failure_completes_locally(client, admin_user, client_obj, monkeypatch):
    """A push failure must NOT roll back the recorded payment (fail loud, keep the record)."""
    from core import invoice_ninja
    _enable_in()
    sale = _priced_draft_sale(client_obj)

    def boom(s):
        raise invoice_ninja.InvoiceNinjaError('IN down')
    monkeypatch.setattr(invoice_ninja, 'push_sale', boom)
    client.force_login(admin_user)
    client.post(reverse('core:sale_checkout', args=[sale.pk]), {
        'payment_method': 'cash', 'amount': '30.00',
    })
    sale.refresh_from_db()
    assert sale.status == 'completed'      # payment kept
    assert sale.invoice_ninja_id == ''     # push failed → retry available


@pytest.mark.django_db
def test_push_sale_creates_paid_invoice_for_client(client_obj, monkeypatch):
    """push_sale posts an invoice THEN a payment (→ IN shows Paid) and stores the ref."""
    from decimal import Decimal
    from core import invoice_ninja
    _enable_in()
    client_obj.invoice_ninja_id = '42'; client_obj.save()  # already linked → no client lookup
    sale = _priced_draft_sale(client_obj)
    sale.payment_method = 'check'; sale.amount = Decimal('30'); sale.reference = 'CHK-9'
    sale.status = 'completed'; sale.save()

    calls = []
    def fake_request(method, path, *, params=None, json=None):
        calls.append((method, path, json))
        if path == '/invoices':
            return {'data': {'id': 999, 'number': 'INV-0007'}}
        return {'data': {'id': 1}}
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)

    ref = invoice_ninja.push_sale(sale)
    sale.refresh_from_db()
    assert ref == 'INV-0007'
    assert sale.invoice_ninja_id == '999'
    assert sale.invoice_ninja_ref == 'INV-0007'
    assert sale.in_status == 'Paid'
    # Order: invoice first, then payment applied to it for the recorded amount.
    paths = [p for _, p, _ in calls]
    assert paths == ['/invoices', '/payments']
    inv_body = calls[0][2]
    assert inv_body['client_id'] == '42'
    assert inv_body['po_number'] == sale.sale_number
    pay_body = calls[1][2]
    assert pay_body['client_id'] == '42'
    assert pay_body['amount'] == 30.0
    assert pay_body['invoices'] == [{'invoice_id': '999', 'amount': 30.0}]


@pytest.mark.django_db
def test_push_sale_walkin_creates_and_caches_client(monkeypatch):
    """Anonymous sale → resolves/creates the standing 'Walk-In' client, caches the
    id on SiteSettings, and reuses it on the next push (no duplicate create)."""
    from decimal import Decimal
    from core import invoice_ninja
    _enable_in()

    def make_sale():
        s = Sale.objects.create()  # no client → anonymous
        s.line_items.create(kind='part', description='Cable', quantity=1, unit_price=Decimal('12'))
        s.payment_method = 'cash'; s.amount = Decimal('12'); s.status = 'completed'; s.save()
        return s

    calls = []
    def fake_request(method, path, *, params=None, json=None):
        calls.append((method, path))
        if path == '/clients' and method == 'GET':
            return {'data': []}                       # no existing Walk-In
        if path == '/clients' and method == 'POST':
            return {'data': {'id': 500}}              # create Walk-In
        if path == '/invoices':
            return {'data': {'id': 999, 'number': 'INV-9'}}
        return {'data': {'id': 1}}
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)

    invoice_ninja.push_sale(make_sale())
    assert SiteSettings.get().invoice_ninja_walkin_client_id == '500'

    # Second push: cached id reused → no /clients calls at all this time.
    calls.clear()
    invoice_ninja.push_sale(make_sale())
    assert not any(path == '/clients' for _, path in calls)


@pytest.mark.django_db
def test_push_sale_blocks_when_no_priced_lines(client_obj):
    from core import invoice_ninja
    _enable_in()
    sale = Sale.objects.create(client=client_obj)  # no priced lines
    with pytest.raises(invoice_ninja.InvoiceNinjaError):
        invoice_ninja.push_sale(sale)


@pytest.mark.django_db
def test_sale_send_in_duplicate_guard(client, admin_user, client_obj, monkeypatch):
    from core import invoice_ninja
    _enable_in()
    sale = Sale.objects.create(client=client_obj, status='completed',
                               invoice_ninja_id='123', invoice_ninja_ref='INV-1')
    calls = []
    monkeypatch.setattr(invoice_ninja, 'push_sale', lambda s: calls.append(s))
    client.force_login(admin_user)
    client.post(reverse('core:sale_send_in', args=[sale.pk]))               # no confirm → skip
    assert calls == []
    client.post(reverse('core:sale_send_in', args=[sale.pk]), {'confirm_resend': '1'})
    assert len(calls) == 1


@pytest.mark.django_db
def test_sale_checkout_role_block_403(client, client_obj):
    role = Role.objects.create(name='NoSales2', can_view_sales=False)
    user = User.objects.create_user(username='tech3', password='x', role_obj=role)
    sale = _priced_draft_sale(client_obj)
    client.force_login(user)
    resp = client.post(reverse('core:sale_checkout', args=[sale.pk]), {
        'payment_method': 'cash', 'amount': '30.00',
    })
    assert resp.status_code == 403


@pytest.mark.django_db
def test_sale_detail_does_not_leak_template_comment(client, admin_user, client_obj):
    """Regression: a multi-line {# #} comment in sale_checkout_card.html isn't
    valid Django comment syntax (only single-line) and was rendering as literal
    text on the page. Must use {% comment %}...{% endcomment %} for multi-line."""
    sale = _priced_draft_sale(client_obj)
    client.force_login(admin_user)
    resp = client.get(reverse('core:sale_detail', args=[sale.pk]))
    assert b'Checkout / payment card for a Sale' not in resp.content


# ── Slice 3c — Sale receipt PDF/email (mirrors the Slice 2b quote pattern) ───

def _completed_sale(client_obj=None, amount='30.00'):
    from decimal import Decimal
    from django.utils import timezone
    sale = _priced_draft_sale(client_obj)
    sale.payment_method = 'cash'
    sale.amount = Decimal(amount)
    sale.status = 'completed'
    sale.paid_at = timezone.now()
    sale.save()
    return sale


@pytest.mark.django_db
def test_receipt_print_view_renders_with_total(client, admin_user, client_obj):
    sale = _completed_sale(client_obj)
    client.force_login(admin_user)
    resp = client.get(reverse('core:sale_receipt_print', args=[sale.pk]))
    assert resp.status_code == 200
    assert sale.sale_number.encode() in resp.content
    assert b'30.00' in resp.content


@pytest.mark.django_db
def test_receipt_print_blocked_when_not_completed(client, admin_user, client_obj):
    sale = _priced_draft_sale(client_obj)  # still draft
    client.force_login(admin_user)
    resp = client.get(reverse('core:sale_receipt_print', args=[sale.pk]))
    assert resp.status_code == 302  # redirected back, not rendered


@pdf_skip
@pytest.mark.django_db
def test_receipt_email_view_client_anchored_sends(monkeypatch, client, client_obj, admin_user):
    from django.core.mail import EmailMultiAlternatives
    from core.models import EmailSendLog, Contact
    _enable_email()
    contact = Contact.objects.create(client=client_obj, first_name='Wayne', last_name='Davis',
                                     email='wayne@davis.example', is_primary=True)
    sale = _completed_sale(client_obj)

    captured = {}
    def fake_send(self, fail_silently=False):
        captured['attachments'] = list(self.attachments)
        captured['to'] = list(self.to)
        return 1
    monkeypatch.setattr(EmailMultiAlternatives, 'send', fake_send)
    client.force_login(admin_user)

    resp = client.post(reverse('core:sale_receipt_email', args=[sale.pk]),
                       {'contact': contact.pk}, follow=True)

    assert resp.status_code == 200
    assert EmailSendLog.objects.filter(status='sent', trigger='sale_receipt').exists()
    assert captured['to'] == ['wayne@davis.example']
    assert captured['attachments'][0][0] == f'Receipt-{sale.sale_number}.pdf'
    assert captured['attachments'][0][1][:5] == b'%PDF-'


@pdf_skip
@pytest.mark.django_db
def test_receipt_email_view_walkin_uses_custom_address(monkeypatch, client, admin_user):
    from django.core.mail import EmailMultiAlternatives
    from core.models import EmailSendLog
    _enable_email()
    sale = _completed_sale(client_obj=None)  # anonymous walk-in

    captured = {}
    def fake_send(self, fail_silently=False):
        captured['to'] = list(self.to)
        return 1
    monkeypatch.setattr(EmailMultiAlternatives, 'send', fake_send)
    client.force_login(admin_user)

    resp = client.post(reverse('core:sale_receipt_email', args=[sale.pk]),
                       {'custom_email': 'walkin@example.com'}, follow=True)

    assert resp.status_code == 200
    assert EmailSendLog.objects.filter(status='sent', trigger='sale_receipt').exists()
    assert captured['to'] == ['walkin@example.com']


@pytest.mark.django_db
def test_receipt_email_blocked_when_not_completed(client, admin_user, client_obj):
    sale = _priced_draft_sale(client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_receipt_email', args=[sale.pk]),
                       {'custom_email': 'x@example.com'})
    assert resp.status_code == 302
    from core.models import EmailSendLog
    assert not EmailSendLog.objects.filter(trigger='sale_receipt').exists()


@pytest.mark.django_db
def test_receipt_email_requires_address(client, admin_user, client_obj):
    sale = _completed_sale(client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_receipt_email', args=[sale.pk]), {})
    assert resp.status_code == 302
    from core.models import EmailSendLog
    assert not EmailSendLog.objects.filter(trigger='sale_receipt').exists()


# ── Walk-in (client-less) Work Orders + Devices ──────────────────────────────
# WorkOrder.client and Device.client went nullable (SET_NULL) so an anonymous
# repair is a real, permanent WorkOrder/Device row instead of piling onto a
# shared placeholder Client that would grow forever.

def _wo_post_payload(**overrides):
    payload = {
        'service_type': 'in_shop',
        'status': 'new',
        'priority': 'normal',
        'device-device_type': 'laptop',
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_work_order_create_without_client_is_walkin(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_order_create'), _wo_post_payload())
    assert resp.status_code == 302
    wo = WorkOrder.objects.get()
    assert wo.client_id is None
    assert str(wo) == f'{wo.work_order_number}: Walk-in'


@pytest.mark.django_db
def test_work_order_create_with_client_still_works(client, admin_user, client_obj):
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_order_create'), _wo_post_payload(client=client_obj.pk))
    assert resp.status_code == 302
    wo = WorkOrder.objects.get()
    assert wo.client_id == client_obj.pk


@pytest.mark.django_db
def test_work_order_create_with_new_walkin_device(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_order_create'), _wo_post_payload(**{
        'device-name': 'Counter Laptop',
        'device-manufacturer': 'Dell',
    }))
    assert resp.status_code == 302
    wo = WorkOrder.objects.get()
    assert wo.device is not None
    assert wo.device.name == 'Counter Laptop'
    assert wo.device.client_id is None
    assert str(wo.device) == 'Counter Laptop (Walk-in)'


@pytest.mark.django_db
def test_work_order_create_with_new_device_attaches_to_selected_client(client, admin_user, client_obj):
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_order_create'), _wo_post_payload(**{
        'client': client_obj.pk,
        'device-name': "Front Desk PC",
    }))
    assert resp.status_code == 302
    wo = WorkOrder.objects.get()
    assert wo.device.client_id == client_obj.pk


@pytest.mark.django_db
def test_work_order_form_device_queryset_scoped_to_client(client_obj):
    """Regression: the device dropdown was never scoped to the selected
    client — every device for every client showed in one flat list."""
    from core.forms import WorkOrderForm
    from core.models import Client as ClientModel
    other_client = ClientModel.objects.create(name='Other Co')
    own_device = Device.objects.create(client=client_obj, name='Mine')
    other_device = Device.objects.create(client=other_client, name='Not Mine')
    form = WorkOrderForm(client_id=client_obj.pk)
    device_qs = form.fields['device'].queryset
    assert own_device in device_qs
    assert other_device not in device_qs


@pytest.mark.django_db
def test_work_order_detail_renders_for_walkin(client, admin_user):
    wo = WorkOrder.objects.create()
    client.force_login(admin_user)
    resp = client.get(reverse('core:work_order_detail', args=[wo.pk]))
    assert resp.status_code == 200
    assert b'Walk-in' in resp.content


@pytest.mark.django_db
def test_device_detail_renders_for_walkin(client, admin_user):
    device = Device.objects.create(name='Loose Laptop')
    client.force_login(admin_user)
    resp = client.get(reverse('core:device_detail', args=[device.pk]))
    assert resp.status_code == 200
    assert b'Walk-in' in resp.content


@pytest.mark.django_db
def test_reset_operational_data_deletes_walkin_wo_and_device(admin_user):
    from django.core.management import call_command
    WorkOrder.objects.create()
    Device.objects.create(name='Orphan Device')
    call_command('reset_operational_data', confirm='DELETE ALL OPERATIONAL DATA')
    assert WorkOrder.objects.count() == 0
    assert Device.objects.count() == 0



# ── Monthly Clients (Lane C recurring — reuses Sale, no new model) ──────────

@pytest.mark.django_db
def test_client_form_saves_is_managed_and_monthly_amount(client, admin_user, client_obj):
    client.force_login(admin_user)
    resp = client.post(reverse('core:client_edit', args=[client_obj.pk]), {
        'name': client_obj.name, 'client_type': client_obj.client_type,
        'is_managed': 'on', 'monthly_amount': '75.00',
    })
    client_obj.refresh_from_db()
    assert resp.status_code == 302
    assert client_obj.is_managed is True
    from decimal import Decimal
    assert client_obj.monthly_amount == Decimal('75.00')


@pytest.mark.django_db
def test_monthly_clients_list_filters_to_managed_only(client, admin_user, client_obj):
    from core.models import Client as ClientModel
    client_obj.is_managed = True
    client_obj.save()
    unmanaged = ClientModel.objects.create(name='Unmanaged Co')
    client.force_login(admin_user)
    resp = client.get(reverse('core:monthly_clients_list'))
    clients_shown = [row['client'] for row in resp.context['rows']]
    assert client_obj in clients_shown
    assert unmanaged not in clients_shown


@pytest.mark.django_db
def test_monthly_clients_list_ignores_non_recurring_sale_this_month(client, admin_user, client_obj):
    """A regular counter Sale for a managed client this month must NOT count
    as this month's recurring charge — proves is_recurring is load-bearing."""
    client_obj.is_managed = True
    client_obj.save()
    Sale.objects.create(client=client_obj, is_recurring=False)
    client.force_login(admin_user)
    resp = client.get(reverse('core:monthly_clients_list'))
    row = next(r for r in resp.context['rows'] if r['client'] == client_obj)
    assert row['sale'] is None


@pytest.mark.django_db
def test_charge_now_creates_draft_sale_with_prefilled_line_item(client, admin_user, client_obj):
    from decimal import Decimal
    client_obj.is_managed = True
    client_obj.monthly_amount = Decimal('50.00')
    client_obj.save()
    client.force_login(admin_user)
    resp = client.post(reverse('core:client_prepare_monthly', args=[client_obj.pk]))
    assert resp.status_code == 302
    sale = Sale.objects.get(client=client_obj, is_recurring=True)
    assert resp.url == reverse('core:sale_detail', args=[sale.pk])
    assert sale.status == 'draft'
    li = sale.line_items.get()
    assert li.description == 'Monthly Service'
    assert li.unit_price == Decimal('50.00')


@pytest.mark.django_db
def test_charge_now_blank_monthly_amount_creates_unpriced_line(client, admin_user, client_obj):
    client_obj.is_managed = True
    client_obj.save()
    client.force_login(admin_user)
    client.post(reverse('core:client_prepare_monthly', args=[client_obj.pk]))
    sale = Sale.objects.get(client=client_obj, is_recurring=True)
    li = sale.line_items.get()
    assert li.unit_price is None
    assert sale.line_items_total == 0
    # Existing checkout guard blocks completion until a price is entered.
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_checkout', args=[sale.pk]), {
        'payment_method': 'cash', 'amount': '0',
    })
    sale.refresh_from_db()
    assert sale.status == 'draft'


@pytest.mark.django_db
def test_charge_now_is_idempotent_within_the_month(client, admin_user, client_obj):
    client_obj.is_managed = True
    client_obj.save()
    client.force_login(admin_user)
    client.post(reverse('core:client_prepare_monthly', args=[client_obj.pk]))
    first_sale = Sale.objects.get(client=client_obj, is_recurring=True)
    client.post(reverse('core:client_prepare_monthly', args=[client_obj.pk]))
    assert Sale.objects.filter(client=client_obj, is_recurring=True).count() == 1
    assert Sale.objects.get(client=client_obj, is_recurring=True).pk == first_sale.pk


@pytest.mark.django_db
def test_monthly_clients_list_role_block_403(client, client_obj):
    role = Role.objects.create(name='NoSales', can_view_sales=False)
    user = User.objects.create_user(username='tech3', password='x', role_obj=role)
    client.force_login(user)
    resp = client.get(reverse('core:monthly_clients_list'))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_charge_now_role_block_403(client, client_obj):
    role = Role.objects.create(name='NoSales2', can_view_sales=False)
    user = User.objects.create_user(username='tech4', password='x', role_obj=role)
    client_obj.is_managed = True
    client_obj.save()
    client.force_login(user)
    resp = client.post(reverse('core:client_prepare_monthly', args=[client_obj.pk]))
    assert resp.status_code == 403


# ── Lane C Slice 5b: billing day, draft push, batch + safety catch ──────────

@pytest.mark.django_db
def test_billing_day_month_end_clamp():
    """A billing_day past a short month's end resolves to that month's last day —
    31 → Feb 28 in a common year, not an invalid date."""
    from datetime import date
    c = Client.objects.create(name='Clamp Co', is_managed=True, billing_day=31)
    assert c.effective_billing_date(2026, 2) == date(2026, 2, 28)   # 2026 not a leap year
    assert c.effective_billing_date(2024, 2) == date(2024, 2, 29)   # leap year
    assert c.effective_billing_date(2026, 1) == date(2026, 1, 31)   # long month unchanged


@pytest.mark.django_db
def test_is_billing_due_respects_client_day():
    """Due only once the client's own billing day has arrived — no hard-coded 1st."""
    from datetime import date
    on_5th = Client.objects.create(name='Fifth Co', is_managed=True, billing_day=5)
    on_15th = Client.objects.create(name='Fifteenth Co', is_managed=True, billing_day=15)
    tenth = date(2026, 3, 10)
    assert on_5th.is_billing_due(tenth) is True       # 5th has passed
    assert on_15th.is_billing_due(tenth) is False     # 15th not reached


@pytest.mark.django_db
def test_push_sale_draft_creates_invoice_without_payment(client_obj, monkeypatch):
    """Draft push posts /invoices only — never /payments — and marks the sale
    Draft (not Paid). This is the phase-1 guarantee: MB charges nothing."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    sale = Sale.objects.create(client=client_obj, is_recurring=True)
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('100.00'))

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices':
            return {'data': {'id': 987, 'number': 'INV-987'}}
        raise AssertionError(f'Unexpected IN call in draft mode: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    monkeypatch.setattr(invoice_ninja, 'find_or_create_client', lambda c: 'inclient-1')

    ref = invoice_ninja.push_sale(sale, draft=True)
    sale.refresh_from_db()
    assert ('POST', '/invoices') in calls
    assert ('POST', '/payments') not in calls          # nothing charged
    assert sale.invoice_ninja_id == '987'
    assert sale.in_status == 'Draft'
    assert ref == 'INV-987'


@pytest.mark.django_db
def test_push_sale_paid_still_posts_payment(client_obj, monkeypatch):
    """Regression: the counter lane (draft=False) still posts /payments and marks Paid."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    sale = Sale.objects.create(client=client_obj, amount=Decimal('40.00'))
    LineItem.objects.create(content_object=sale, kind='labor', description='Bench',
                            quantity=1, unit_price=Decimal('40.00'))
    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices':
            return {'data': {'id': 5, 'number': 'INV-5'}}
        return {'data': {'id': 1}}
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    monkeypatch.setattr(invoice_ninja, 'find_or_create_client', lambda c: 'inclient-1')

    invoice_ninja.push_sale(sale, draft=False)
    sale.refresh_from_db()
    assert ('POST', '/payments') in calls
    assert sale.in_status == 'Paid'


@pytest.mark.django_db
def test_check_sale_status_reads_back(client_obj, monkeypatch):
    """check_sale_status maps IN's status_id to a label and records it on the sale."""
    from core import invoice_ninja
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='77')
    monkeypatch.setattr(invoice_ninja, '_request',
                        lambda method, path, **kw: {'data': {'status_id': '4'}})  # 4 = Paid
    label = invoice_ninja.check_sale_status(sale)
    sale.refresh_from_db()
    assert label == 'Paid'
    assert sale.in_status == 'Paid'


@pytest.mark.django_db
def test_send_draft_view_pushes_draft(client, admin_user, client_obj, monkeypatch):
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    sale = Sale.objects.create(client=client_obj, is_recurring=True)
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('60.00'))
    captured = {}
    def fake_push(s, draft=False):
        captured['draft'] = draft
        s.invoice_ninja_id = '111'; s.in_status = 'Draft'
        s.save(update_fields=['invoice_ninja_id', 'in_status'])
        return 'INV-111'
    monkeypatch.setattr(invoice_ninja, 'push_sale', fake_push)

    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_send_draft', args=[sale.pk]))
    assert resp.status_code == 302
    assert captured['draft'] is True
    sale.refresh_from_db()
    assert sale.in_status == 'Draft'


@pytest.mark.django_db
def test_batch_prepare_only_due_clients(client, admin_user):
    """'Prepare all due' creates drafts for due managed clients only, and is
    idempotent (a second run adds nothing)."""
    from datetime import date, datetime
    from unittest.mock import patch
    from django.utils import timezone as dj_tz
    due = Client.objects.create(name='Due Co', is_managed=True, billing_day=1, monthly_amount=50)
    not_due = Client.objects.create(name='Later Co', is_managed=True, billing_day=28, monthly_amount=50)
    client.force_login(admin_user)
    # Freeze "today" to the 10th so the 1st client is due, the 28th isn't. Freeze
    # timezone.now() to the same month too, so the idempotency key (the sale's
    # created_at month) lines up with the frozen billing month — in prod these
    # are always the same clock; only a test that jumps months can split them.
    frozen_now = dj_tz.make_aware(datetime(2026, 6, 10, 12, 0))
    with patch('core.views.timezone.localdate', return_value=date(2026, 6, 10)), \
         patch('django.utils.timezone.now', return_value=frozen_now):
        client.post(reverse('core:monthly_batch_prepare'))
        assert Sale.objects.filter(client=due, is_recurring=True).count() == 1
        assert Sale.objects.filter(client=not_due, is_recurring=True).count() == 0
        client.post(reverse('core:monthly_batch_prepare'))   # idempotent
        assert Sale.objects.filter(client=due, is_recurring=True).count() == 1


@pytest.mark.django_db
def test_batch_send_confirmation_lists_prepared_and_total(client, admin_user, client_obj):
    """The safety catch: GET shows exactly what will be sent + the grand total,
    and pushes NOTHING (no IN call happens on the confirmation view)."""
    from decimal import Decimal
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    client_obj.is_managed = True; client_obj.save()
    sale = Sale.objects.create(client=client_obj, is_recurring=True)
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('125.00'))
    client.force_login(admin_user)
    resp = client.get(reverse('core:monthly_batch_send'))
    assert resp.status_code == 200
    assert resp.context['count'] == 1
    assert resp.context['total'] == Decimal('125.00')


@pytest.mark.django_db
def test_batch_send_post_pushes_drafts(client, admin_user, client_obj, monkeypatch):
    """Confirming the batch pushes each prepared sale as a DRAFT."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    client_obj.is_managed = True; client_obj.save()
    sale = Sale.objects.create(client=client_obj, is_recurring=True)
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('125.00'))
    modes = []
    def fake_push(s, draft=False):
        modes.append(draft)
        s.invoice_ninja_id = '222'; s.save(update_fields=['invoice_ninja_id'])
        return 'INV-222'
    monkeypatch.setattr(invoice_ninja, 'push_sale', fake_push)

    client.force_login(admin_user)
    resp = client.post(reverse('core:monthly_batch_send'))
    assert resp.status_code == 302
    assert modes == [True]                 # pushed as draft
    sale.refresh_from_db()
    assert sale.invoice_ninja_id == '222'


@pytest.mark.django_db
def test_worklist_states_reflect_lifecycle(client, admin_user, client_obj, monkeypatch):
    """Worklist row state moves not_prepared → prepared → draft_in_in → paid."""
    from decimal import Decimal
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    client_obj.is_managed = True; client_obj.save()
    client.force_login(admin_user)

    def state_for(c):
        resp = client.get(reverse('core:monthly_clients_list'))
        return next(r['state'] for r in resp.context['rows'] if r['client'] == c)

    assert state_for(client_obj) == 'not_prepared'
    sale = Sale.objects.create(client=client_obj, is_recurring=True)
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('50.00'))
    assert state_for(client_obj) == 'prepared'
    sale.invoice_ninja_id = '333'; sale.in_status = 'Draft'; sale.save()
    assert state_for(client_obj) == 'draft_in_in'
    sale.in_status = 'Paid'; sale.save()
    assert state_for(client_obj) == 'paid'


# ── Lane C: per-client recurring line templates (cloned into the monthly draft) ──

@pytest.mark.django_db
def test_prepare_clones_client_recurring_template_lines(admin_user):
    """A managed client's recurring template lines (multiple services, quantities,
    negotiated prices) are cloned into the month's draft — not a single generic
    Monthly Service line."""
    from decimal import Decimal
    from core.models import LineItem, CatalogItem
    from core.views import _prepare_recurring_sale
    c = Client.objects.create(name='Multi Svc Co', is_managed=True)
    svc = CatalogItem.objects.create(name='Managed IT', category='Managed', item_type='service')
    # Template: a catalog service + a per-endpoint line at a negotiated qty/price.
    c.line_items.create(kind='labor', description='Managed IT', quantity=1,
                        unit_price=Decimal('150.00'), catalog_item=svc)
    c.line_items.create(kind='labor', description='Managed Workstation', quantity=4,
                        unit_price=Decimal('45.00'))

    sale, created = _prepare_recurring_sale(c, admin_user)
    assert created
    lines = {li.description: li for li in sale.line_items.all()}
    assert set(lines) == {'Managed IT', 'Managed Workstation'}
    assert lines['Managed Workstation'].quantity == Decimal('4')
    assert lines['Managed Workstation'].unit_price == Decimal('45.00')
    assert lines['Managed IT'].catalog_item_id == svc.pk
    # 150 + 4×45 = 330
    assert sale.line_items_total == Decimal('330.00')


@pytest.mark.django_db
def test_prepare_falls_back_to_monthly_amount_without_template(admin_user):
    """A managed client with NO template lines still gets the simple single
    Monthly Service line at monthly_amount — simple clients stay simple."""
    from decimal import Decimal
    from core.views import _prepare_recurring_sale
    c = Client.objects.create(name='Simple Co', is_managed=True, monthly_amount=Decimal('75.00'))
    sale, _created = _prepare_recurring_sale(c, admin_user)
    li = sale.line_items.get()
    assert li.description == 'Monthly Service'
    assert li.unit_price == Decimal('75.00')


@pytest.mark.django_db
def test_client_recurring_total_sums_priced_lines():
    from decimal import Decimal
    c = Client.objects.create(name='Total Co', is_managed=True)
    c.line_items.create(kind='labor', description='A', quantity=2, unit_price=Decimal('10.00'))
    c.line_items.create(kind='labor', description='B', quantity=1, unit_price=None)  # unpriced ignored
    assert c.recurring_total == Decimal('20.00')


@pytest.mark.django_db
def test_client_recurring_catalog_and_custom_log_views(client, admin_user):
    """Adding catalog + custom lines to a client's recurring template via the
    HTMX views, re-rendering the client recurring partial."""
    from decimal import Decimal
    from core.models import CatalogItem
    c = Client.objects.create(name='Editable Co', is_managed=True)
    svc = CatalogItem.objects.create(name='Backup', category='Managed', item_type='service',
                                     default_price=Decimal('40.00'))
    client.force_login(admin_user)
    r1 = client.post(reverse('core:client_recurring_log', args=[c.pk, svc.pk]))
    assert r1.status_code == 200
    r2 = client.post(reverse('core:client_recurring_custom', args=[c.pk]), {
        'kind': 'labor', 'custom_label': 'Onsite hour', 'quantity': '2', 'unit_price': '90',
    })
    assert r2.status_code == 200
    descs = sorted(li.description for li in c.line_items.all())
    assert descs == ['Backup', 'Onsite hour']
    assert c.recurring_total == Decimal('40.00') + Decimal('180.00')


@pytest.mark.django_db
def test_client_recurring_line_edit_and_delete_rerender_client_partial(client, admin_user):
    """The host-agnostic WorkPerformed update/delete views handle a Client-hosted
    line and re-render the client recurring partial (not a WO/Sale one)."""
    c = Client.objects.create(name='EditDel Co', is_managed=True)
    li = c.line_items.create(kind='labor', description='Svc', quantity=1, unit_price=10)
    client.force_login(admin_user)
    r = client.post(reverse('core:work_performed_update', args=[li.pk]), {
        'custom_label': 'Svc renamed', 'quantity': '3', 'unit_price': '15',
    })
    assert r.status_code == 200
    assert b'client-recurring-entry' in r.content   # re-rendered the CLIENT partial
    li.refresh_from_db()
    assert li.description == 'Svc renamed' and li.quantity == 3
    client.post(reverse('core:work_performed_delete', args=[li.pk]))
    assert c.line_items.count() == 0


@pytest.mark.django_db
def test_client_detail_shows_recurring_card_only_for_managed(client, admin_user):
    managed = Client.objects.create(name='Managed Detail Co', is_managed=True)
    plain = Client.objects.create(name='Plain Detail Co', is_managed=False)
    client.force_login(admin_user)
    r_managed = client.get(reverse('core:client_detail', args=[managed.pk]))
    r_plain = client.get(reverse('core:client_detail', args=[plain.pk]))
    assert b'Recurring monthly charges' in r_managed.content
    assert b'Recurring monthly charges' not in r_plain.content


@pytest.mark.django_db
def test_client_recurring_role_block_403(client):
    role = Role.objects.create(name='NoSalesRec', can_view_sales=False)
    user = User.objects.create_user(username='techrec', password='x', role_obj=role)
    c = Client.objects.create(name='Blocked Co', is_managed=True)
    client.force_login(user)
    resp = client.post(reverse('core:client_recurring_custom', args=[c.pk]), {
        'custom_label': 'X', 'quantity': '1',
    })
    assert resp.status_code == 403


# ── Products & Services catalog (was QuickLaborItem) ────────────────────────

@pytest.mark.django_db
def test_catalog_item_line_kind_maps_type_to_kind():
    svc = CatalogItem.objects.create(name='Tune-up', category='Software', item_type='service')
    prod = CatalogItem.objects.create(name='1TB SSD', category='Hardware', item_type='product')
    assert svc.line_kind == 'labor'
    assert prod.line_kind == 'part'


@pytest.mark.django_db
def test_logging_product_creates_part_line(client, admin_user, client_obj):
    from decimal import Decimal
    from core.models import LineItem
    wo = WorkOrder.objects.create(client=client_obj)
    prod = CatalogItem.objects.create(name='1TB SSD', category='Hardware',
                                      item_type='product', default_price=Decimal('90.00'))
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_performed_log', args=[wo.pk, prod.pk]))
    assert resp.status_code == 200
    li = LineItem.objects.get(object_id=wo.pk, description='1TB SSD')
    assert li.kind == 'part'
    assert li.unit_price == Decimal('90.00')
    assert li.catalog_item_id == prod.pk


@pytest.mark.django_db
def test_catalog_list_visible_to_all_but_edit_admin_only(client, client_obj):
    role = Role.objects.create(name='Tech', can_manage_settings=False)
    tech = User.objects.create_user(username='techc', password='x', role_obj=role)
    CatalogItem.objects.create(name='Tune-up', category='Software')
    client.force_login(tech)
    resp = client.get(reverse('core:catalog_list'))
    assert resp.status_code == 200          # list visible to a non-admin
    assert resp.context['can_edit'] is False


@pytest.mark.django_db
def test_catalog_list_items_alphabetical_within_category(client, admin_user):
    # Items within a category sort alphabetically by name (case-insensitive),
    # ignoring sort_order (which carries legacy values with no UI to edit).
    CatalogItem.objects.create(name='Tutoring', category='General', sort_order=1)
    CatalogItem.objects.create(name='New System Setup', category='General', sort_order=2)
    CatalogItem.objects.create(name='printer install', category='General', sort_order=3)
    client.force_login(admin_user)
    resp = client.get(reverse('core:catalog_list'))
    names = [i.name for i in resp.context['services_by_category']['General']]
    assert names == ['New System Setup', 'printer install', 'Tutoring']


@pytest.mark.django_db
def test_catalog_create_and_delete_gated_to_admin(client, client_obj):
    role = Role.objects.create(name='Tech2', can_manage_settings=False)
    tech = User.objects.create_user(username='techd', password='x', role_obj=role)
    client.force_login(tech)
    resp = client.post(reverse('core:catalog_create'), {
        'name': 'Sneaky', 'category': 'Software', 'item_type': 'service',
    })
    assert resp.status_code == 403
    assert not CatalogItem.objects.filter(name='Sneaky').exists()


@pytest.mark.django_db
def test_catalog_create_by_admin(client, admin_user):
    from decimal import Decimal
    client.force_login(admin_user)
    resp = client.post(reverse('core:catalog_create'), {
        'name': 'Data Recovery', 'category': 'Data', 'item_type': 'service',
        'default_price': '150.00',
    })
    assert resp.status_code == 302
    item = CatalogItem.objects.get(name='Data Recovery')
    assert item.item_type == 'service'
    assert item.default_price == Decimal('150.00')


@pytest.mark.django_db
def test_catalog_list_search_filters(client, admin_user):
    CatalogItem.objects.create(name='Tune-up', category='Software', item_type='service')
    CatalogItem.objects.create(name='1TB SSD', category='Hardware', item_type='product')
    client.force_login(admin_user)
    resp = client.get(reverse('core:catalog_list'), {'search': 'tune'})
    names = [i.name for i in resp.context['items']]
    assert names == ['Tune-up']


@pytest.mark.django_db
def test_catalog_list_splits_services_and_products_by_category(client, admin_user):
    CatalogItem.objects.create(name='Widget', category='Hardware', item_type='product')
    CatalogItem.objects.create(name='Fix', category='Software', item_type='service')
    CatalogItem.objects.create(name='Cleanup', category='Software', item_type='service')
    client.force_login(admin_user)
    resp = client.get(reverse('core:catalog_list'))
    services = resp.context['services_by_category']
    products = resp.context['products_by_category']
    # Services grouped by category (a divider per category); products separate.
    assert list(services.keys()) == ['Software']
    assert [i.name for i in services['Software']] == ['Cleanup', 'Fix']
    assert list(products.keys()) == ['Hardware']
    assert [i.name for i in products['Hardware']] == ['Widget']
    assert resp.context['services_count'] == 2
    assert resp.context['products_count'] == 1


@pytest.mark.django_db
def test_catalog_card_does_not_leak_template_comment(client, admin_user):
    """Regression: the catalog_card partial opened with a multi-line {# #}
    comment, which Django only treats as a comment on a single line — it
    rendered as literal text once per card. Must use {% comment %}."""
    CatalogItem.objects.create(name='Tune-up', category='Software')
    client.force_login(admin_user)
    resp = client.get(reverse('core:catalog_list'))
    assert b'A collapsible catalog card' not in resp.content


# ---------------------------------------------------------------------------
# Slice 5d — MB-initiated charge against a card on file (Path C, guarded)
# ---------------------------------------------------------------------------

from core.models import Role, PaymentChargeAttempt


@pytest.mark.django_db
def test_charge_sale_on_file_posts_bulk_auto_bill(client_obj, monkeypatch):
    """charge_sale_on_file triggers IN's bulk auto_bill action against the
    pushed invoice id, then reads the status back — it never marks Paid
    itself (the charge is async on IN's side)."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('100.00'))

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get('json')))
        if path == '/invoices/bulk':
            return {'data': []}
        if path == '/invoices/42':
            return {'data': {'status_id': '2'}}  # 2 = Sent — still not paid yet
        raise AssertionError(f'Unexpected IN call: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)

    label = invoice_ninja.charge_sale_on_file(sale)
    assert ('POST', '/invoices/bulk', {'action': 'auto_bill', 'ids': ['42']}) in calls
    assert label == 'Sent'  # async — not Paid yet, and that's expected
    sale.refresh_from_db()
    assert sale.in_status == 'Sent'


@pytest.mark.django_db
def test_charge_sale_on_file_refuses_when_not_pushed(client_obj):
    """Can't charge a sale that hasn't been sent to Invoice Ninja yet."""
    from core import invoice_ninja
    sale = Sale.objects.create(client=client_obj, is_recurring=True)
    with pytest.raises(invoice_ninja.InvoiceNinjaError, match='not been pushed'):
        invoice_ninja.charge_sale_on_file(sale)


@pytest.mark.django_db
def test_charge_sale_on_file_refuses_when_fresh_status_is_paid(client_obj, monkeypatch):
    """Double-charge safety: even if the STORED status is stale (Draft), a fresh
    read-back showing Paid must block the charge — the bulk auto_bill trigger is
    NEVER fired."""
    from core import invoice_ninja
    sale = Sale.objects.create(client=client_obj, is_recurring=True,
                                invoice_ninja_id='42', in_status='Draft')  # stale stored value
    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices/42':
            return {'data': {'status_id': '4'}}  # 4 = Paid — the real current state
        raise AssertionError(f'Must not fire the charge: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    with pytest.raises(invoice_ninja.InvoiceNinjaError, match='already marked Paid'):
        invoice_ninja.charge_sale_on_file(sale)
    assert ('POST', '/invoices/bulk') not in calls  # never triggered


@pytest.mark.django_db
def test_charge_sale_on_file_aborts_if_status_unreadable(client_obj, monkeypatch):
    """If IN can't be reached for the pre-charge status read, the charge is
    aborted (fail loud) — we never fire a charge blind."""
    from core import invoice_ninja
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices/42':
            raise invoice_ninja.InvoiceNinjaError('Could not reach Invoice Ninja')
        raise AssertionError(f'Must not fire the charge: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    with pytest.raises(invoice_ninja.InvoiceNinjaError, match='Could not reach'):
        invoice_ninja.charge_sale_on_file(sale)
    assert ('POST', '/invoices/bulk') not in calls


@pytest.mark.django_db
def test_charge_sale_on_file_propagates_trigger_error(client_obj, monkeypatch):
    """A failure on the bulk auto_bill call itself is fail-loud."""
    from core import invoice_ninja
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    def fake_request(method, path, **kwargs):
        if path == '/invoices/42':
            return {'data': {'status_id': '2'}}  # pre-charge read-back: Sent (unpaid)
        if path == '/invoices/bulk':
            raise invoice_ninja.InvoiceNinjaError('Invoice Ninja returned 422: no payment method on file')
        raise AssertionError(f'Unexpected call {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    with pytest.raises(invoice_ninja.InvoiceNinjaError, match='no payment method on file'):
        invoice_ninja.charge_sale_on_file(sale)


@pytest.mark.django_db
def test_sale_charge_view_requires_can_process_payments(client, admin_user, client_obj):
    """403 for a user without can_process_payments — even an otherwise-admin
    role that can view/manage sales. Charging money is opt-in, not
    admin-by-default. No IN call is made and no attempt is recorded."""
    role = Role.objects.create(name='SalesOnly', can_view_sales=True, can_process_payments=False)
    user = User.objects.create_user(username='tech3', password='x', role_obj=role)
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    client.force_login(user)

    resp = client.get(reverse('core:sale_charge', args=[sale.pk]))
    assert resp.status_code == 403
    resp = client.post(reverse('core:sale_charge', args=[sale.pk]))
    assert resp.status_code == 403
    assert PaymentChargeAttempt.objects.count() == 0


@pytest.mark.django_db
def test_sale_charge_view_confirm_screen_shows_server_amount(client, admin_user, client_obj):
    """GET renders the confirm screen with the server-computed amount — the
    amount is never taken from the request."""
    from decimal import Decimal
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('75.00'))
    client.force_login(admin_user)
    resp = client.get(reverse('core:sale_charge', args=[sale.pk]))
    assert resp.status_code == 200
    assert resp.context['amount'] == Decimal('75.00')


@pytest.mark.django_db
def test_sale_charge_view_success_records_attempt_and_message(client, admin_user, client_obj, monkeypatch):
    """A successful trigger writes a success PaymentChargeAttempt with the
    server-computed amount, and does NOT itself mark the sale Paid — only the
    read-back inside charge_sale_on_file (mocked here) determines in_status."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('60.00'))

    def fake_charge(s):
        s.in_status = 'Sent'  # async — still not Paid right after triggering
        s.save(update_fields=['in_status'])
        return 'Sent'
    monkeypatch.setattr(invoice_ninja, 'charge_sale_on_file', fake_charge)

    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_charge', args=[sale.pk]), {'amount': '99999.00'})
    assert resp.status_code == 302
    attempt = PaymentChargeAttempt.objects.get(sale=sale)
    assert attempt.result == 'success'
    assert attempt.amount == Decimal('60.00')  # server-derived, ignores the posted 99999.00
    assert attempt.initiated_by == admin_user
    assert attempt.in_status_after == 'Sent'
    sale.refresh_from_db()
    assert sale.in_status != 'Paid'  # async — the trigger alone never marks Paid


@pytest.mark.django_db
def test_sale_charge_view_cooldown_blocks_rapid_second_charge(client, admin_user, client_obj, monkeypatch):
    """Double-charge safety: a second charge on the same sale within the cooldown
    of a prior successful trigger is refused — no IN call, no new attempt row.
    Kills double-clicks / in-flight re-charges before the async job settles."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('60.00'))
    # A prior successful trigger exists moments ago.
    PaymentChargeAttempt.objects.create(sale=sale, invoice_ninja_id='42',
                                        amount=Decimal('60.00'), result='success')

    def must_not_charge(s):
        raise AssertionError('Must not fire a charge during the cooldown window')
    monkeypatch.setattr(invoice_ninja, 'charge_sale_on_file', must_not_charge)

    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_charge', args=[sale.pk]))
    assert resp.status_code == 302
    # Still just the one prior attempt — no new row written.
    assert PaymentChargeAttempt.objects.filter(sale=sale).count() == 1


@pytest.mark.django_db
def test_sale_charge_view_failure_records_attempt_and_error(client, admin_user, client_obj, monkeypatch):
    """An IN failure writes a failed PaymentChargeAttempt with the error
    message, surfaces the error, and never marks the sale Paid."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('60.00'))

    def fake_charge(s):
        raise invoice_ninja.InvoiceNinjaError('Invoice Ninja returned 422: no payment method on file')
    monkeypatch.setattr(invoice_ninja, 'charge_sale_on_file', fake_charge)

    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_charge', args=[sale.pk]))
    assert resp.status_code == 302
    attempt = PaymentChargeAttempt.objects.get(sale=sale)
    assert attempt.result == 'failed'
    assert 'no payment method on file' in attempt.error_message
    sale.refresh_from_db()
    assert sale.in_status != 'Paid'


@pytest.mark.django_db
def test_sale_charge_view_refuses_when_in_disabled(client, admin_user, client_obj):
    """No PaymentChargeAttempt row is written if IN isn't enabled — the view
    guards before ever calling into invoice_ninja."""
    from core.models import SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = False; site.save()
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_charge', args=[sale.pk]))
    assert resp.status_code == 302
    assert PaymentChargeAttempt.objects.count() == 0


@pytest.mark.django_db
def test_recurring_card_charge_button_gated_on_permission(client, client_obj):
    """The 'Charge card on file' button only renders for a user with
    can_process_payments — a sales-only role never sees it."""
    from decimal import Decimal
    from core.models import LineItem, SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = True; site.save()
    role = Role.objects.create(name='SalesOnly2', can_view_sales=True, can_process_payments=False)
    user = User.objects.create_user(username='tech4', password='x', role_obj=role)
    sale = Sale.objects.create(client=client_obj, is_recurring=True, invoice_ninja_id='42')
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('60.00'))
    client.force_login(user)
    resp = client.get(reverse('core:sale_detail', args=[sale.pk]))
    assert b'Charge card on file' not in resp.content


# ---------------------------------------------------------------------------
# Light POS — Slice 6: card-on-file charge in the Register (WorkOrder host)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_charge_on_file_dispatches_to_work_order_status_check(client_obj, monkeypatch):
    """charge_on_file(work_order) triggers the same bulk auto_bill action as
    the Sale path, but reads/writes status via the WO's Invoice row, not a
    field on the host itself."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem, Invoice
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    Invoice.objects.get_or_create(work_order=wo)
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('50.00'))

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get('json')))
        if path == '/invoices/bulk':
            return {'data': []}
        if path == '/invoices/77':
            return {'data': {'status_id': '2'}}  # Sent — not paid yet
        raise AssertionError(f'Unexpected IN call: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)

    label = invoice_ninja.charge_on_file(wo)
    assert ('POST', '/invoices/bulk', {'action': 'auto_bill', 'ids': ['77']}) in calls
    assert label == 'Sent'
    inv = Invoice.objects.get(work_order=wo)
    assert inv.in_status == 'Sent'


@pytest.mark.django_db
def test_charge_on_file_refuses_work_order_when_not_pushed(client_obj):
    from core import invoice_ninja
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    with pytest.raises(invoice_ninja.InvoiceNinjaError, match='not been pushed'):
        invoice_ninja.charge_on_file(wo)


@pytest.mark.django_db
def test_charge_on_file_refuses_work_order_when_fresh_status_is_paid(client_obj, monkeypatch):
    """Same double-charge safety as the Sale path: a fresh read-back showing
    Paid blocks the charge even if MB's stored status is stale."""
    from core import invoice_ninja
    from core.models import Invoice
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    inv, _ = Invoice.objects.get_or_create(work_order=wo)
    inv.in_status = 'Draft'  # stale stored value
    inv.save()

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices/77':
            return {'data': {'status_id': '4'}}  # Paid — the real current state
        raise AssertionError(f'Must not fire the charge: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    with pytest.raises(invoice_ninja.InvoiceNinjaError, match='already marked Paid'):
        invoice_ninja.charge_on_file(wo)
    assert ('POST', '/invoices/bulk') not in calls


@pytest.mark.django_db
def test_charge_on_file_rejects_unsupported_host():
    from core import invoice_ninja
    with pytest.raises(TypeError):
        invoice_ninja.charge_on_file(object())


@pytest.mark.django_db
def test_pos_wo_charge_view_requires_can_process_payments(client, client_obj):
    """403 for a user without can_process_payments, even one who can view
    sales / use the register. No IN call, no attempt recorded."""
    role = Role.objects.create(name='RegisterOnly', can_view_sales=True, can_process_payments=False)
    user = User.objects.create_user(username='reg_tech', password='x', role_obj=role)
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    client.force_login(user)

    resp = client.get(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 403
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 403
    assert PaymentChargeAttempt.objects.count() == 0


@pytest.mark.django_db
def test_pos_wo_charge_view_confirm_screen_shows_server_amount(client, admin_user, client_obj):
    from decimal import Decimal
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('85.00'))
    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 200
    assert resp.context['amount'] == Decimal('85.00')


@pytest.mark.django_db
def test_pos_wo_charge_view_success_records_attempt_on_work_order(client, admin_user, client_obj, monkeypatch):
    """A successful trigger writes a PaymentChargeAttempt with work_order set
    (sale left null) and the server-computed amount; the WO is never marked
    Paid by the trigger itself — only the async read-back does that."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem, Invoice
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    inv, _ = Invoice.objects.get_or_create(work_order=wo)
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('65.00'))

    def fake_charge(host):
        inv.in_status = 'Sent'
        inv.save(update_fields=['in_status'])
        return 'Sent'
    monkeypatch.setattr(invoice_ninja, 'charge_on_file', fake_charge)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]), {'amount': '99999.00'})
    assert resp.status_code == 302
    attempt = PaymentChargeAttempt.objects.get(work_order=wo)
    assert attempt.sale is None
    assert attempt.result == 'success'
    assert attempt.amount == Decimal('65.00')  # server-derived, ignores the posted 99999.00
    assert attempt.initiated_by == admin_user
    assert attempt.in_status_after == 'Sent'
    inv.refresh_from_db()
    assert inv.in_status != 'Paid'


@pytest.mark.django_db
def test_pos_wo_charge_view_cooldown_blocks_rapid_second_charge(client, admin_user, client_obj, monkeypatch):
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('65.00'))
    PaymentChargeAttempt.objects.create(work_order=wo, invoice_ninja_id='77',
                                        amount=Decimal('65.00'), result='success')

    def must_not_charge(host):
        raise AssertionError('Must not fire a charge during the cooldown window')
    monkeypatch.setattr(invoice_ninja, 'charge_on_file', must_not_charge)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 302
    assert PaymentChargeAttempt.objects.filter(work_order=wo).count() == 1


@pytest.mark.django_db
def test_pos_wo_charge_view_failure_records_attempt_and_error(client, admin_user, client_obj, monkeypatch):
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('65.00'))

    def fake_charge(host):
        raise invoice_ninja.InvoiceNinjaError('Invoice Ninja returned 422: no payment method on file')
    monkeypatch.setattr(invoice_ninja, 'charge_on_file', fake_charge)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 302
    attempt = PaymentChargeAttempt.objects.get(work_order=wo)
    assert attempt.result == 'failed'
    assert 'no payment method on file' in attempt.error_message


@pytest.mark.django_db
def test_pos_wo_charge_view_refuses_when_in_disabled(client, admin_user, client_obj):
    from core.models import SiteSettings
    site = SiteSettings.get(); site.invoice_ninja_enabled = False; site.save()
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 302
    assert PaymentChargeAttempt.objects.count() == 0


@pytest.mark.django_db
def test_pos_wo_settle_charge_button_hidden_for_walkin(client, admin_user):
    """A walk-in (client-less) WO has no card on file to charge — the button
    must not render even for a permitted user (admin_user is a superuser, so
    can_process_payments is satisfied), regardless of push state."""
    _enable_in()
    wo = WorkOrder.objects.create(client=None, status='completed', invoice_ninja_id='77')
    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_wo_settle', args=[wo.pk]))
    assert b'Charge card on file' not in resp.content


@pytest.mark.django_db
def test_pos_wo_settle_charge_button_gated_on_permission(client, client_obj):
    """The register's 'Charge card on file' link only renders for a user with
    can_process_payments — matches the Sale-side gating."""
    from decimal import Decimal
    from core.models import LineItem
    _enable_in()
    role = Role.objects.create(name='RegisterOnly2', can_view_sales=True, can_process_payments=False)
    user = User.objects.create_user(username='reg_tech2', password='x', role_obj=role)
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('65.00'))
    client.force_login(user)
    resp = client.get(reverse('core:pos_wo_settle', args=[wo.pk]))
    assert b'Charge card on file' not in resp.content


@pytest.mark.django_db
def test_pos_wo_settle_charge_button_shows_for_permitted_user(client, admin_user, client_obj):
    """Positive case: the 'Charge card on file' button renders on the settle
    screen for a permitted user with a client-owned WO that has priced lines —
    even before the WO has been pushed to IN (the push happens on charge)."""
    from decimal import Decimal
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')  # NOT pushed yet
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('113.00'))
    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_wo_settle', args=[wo.pk]))
    assert b'Charge card on file' in resp.content


@pytest.mark.django_db
def test_pos_wo_charge_confirm_screen_works_before_push(client, admin_user, client_obj):
    """The confirm screen renders (200, not a redirect) for a WO that hasn't
    been pushed to IN yet — the old 'settle it first' hard block is gone."""
    from decimal import Decimal
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')  # NOT pushed
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('90.00'))
    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 200
    assert resp.context['amount'] == Decimal('90.00')


@pytest.mark.django_db
def test_pos_wo_charge_pushes_draft_then_charges_when_unpushed(client, admin_user, client_obj, monkeypatch):
    """One-click path: charging a not-yet-pushed WO first creates the IN draft
    (push_host_invoice), saves the returned id/ref on the WO, THEN fires the
    charge — a success attempt is recorded against the now-pushed invoice."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')  # NOT pushed
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('120.00'))

    pushed = {}
    def fake_push(host, **kwargs):
        pushed['called'] = True
        return ('900', 'INV-900', 'inclient-1')
    def fake_charge(host):
        assert host.invoice_ninja_id == '900'  # push ran first
        return 'Sent'
    monkeypatch.setattr(invoice_ninja, 'push_host_invoice', fake_push)
    monkeypatch.setattr(invoice_ninja, 'charge_on_file', fake_charge)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 302
    assert pushed.get('called') is True
    wo.refresh_from_db()
    assert wo.invoice_ninja_id == '900'
    assert wo.invoice_ninja_ref == 'INV-900'
    attempt = PaymentChargeAttempt.objects.get(work_order=wo)
    assert attempt.result == 'success'
    assert attempt.amount == Decimal('120.00')


@pytest.mark.django_db
def test_pos_wo_charge_push_failure_not_recorded_as_charge_attempt(client, admin_user, client_obj, monkeypatch):
    """If the draft push itself fails, it's reported plainly and no
    PaymentChargeAttempt row is written (the audit table is charge-only), and
    the charge is never fired."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')  # NOT pushed
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('120.00'))

    def fake_push(host, **kwargs):
        raise invoice_ninja.InvoiceNinjaError('IN unreachable')
    def must_not_charge(host):
        raise AssertionError('Charge must not fire when the push failed')
    monkeypatch.setattr(invoice_ninja, 'push_host_invoice', fake_push)
    monkeypatch.setattr(invoice_ninja, 'charge_on_file', must_not_charge)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 302
    assert PaymentChargeAttempt.objects.count() == 0
    wo.refresh_from_db()
    assert not wo.invoice_ninja_id  # never got an id


@pytest.mark.django_db
def test_pos_wo_charge_rejects_walkin(client, admin_user, monkeypatch):
    """A walk-in (client-less) WO can't be charged — no card on file. The guard
    redirects and never pushes or charges."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=None, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('50.00'))
    def must_not_call(*a, **k):
        raise AssertionError('walk-in must not reach IN')
    monkeypatch.setattr(invoice_ninja, 'push_host_invoice', must_not_call)
    monkeypatch.setattr(invoice_ninja, 'charge_on_file', must_not_call)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 302
    assert PaymentChargeAttempt.objects.count() == 0


@pytest.mark.django_db
def test_pos_wo_charge_rejects_when_no_priced_lines(client, admin_user, client_obj, monkeypatch):
    """No priced lines → nothing to charge; guard redirects, no IN call."""
    from core import invoice_ninja
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='77')
    def must_not_call(*a, **k):
        raise AssertionError('must not reach IN with a zero amount')
    monkeypatch.setattr(invoice_ninja, 'charge_on_file', must_not_call)
    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_charge', args=[wo.pk]))
    assert resp.status_code == 302
    assert PaymentChargeAttempt.objects.count() == 0


# ---------------------------------------------------------------------------
# Light POS — Slice 1: the register (non-charging settlement paths)
# ---------------------------------------------------------------------------

from core.models import Invoice as _POSInvoice


@pytest.mark.django_db
def test_pos_home_search_finds_finished_wo_by_number_and_client(client, admin_user, client_obj):
    """A finished WO is eligible for the register. MB's real terminal state is
    'completed' (what techs actually set); 'closed' is also accepted. An
    unfinished WO (in_progress) must not appear."""
    completed = WorkOrder.objects.create(client=client_obj, status='completed')
    closed = WorkOrder.objects.create(client=client_obj, status='closed')
    open_wo = WorkOrder.objects.create(client=client_obj, status='in_progress')
    client.force_login(admin_user)

    resp = client.get(reverse('core:pos_home'), {'q': completed.work_order_number})
    numbers = [w.work_order_number for w in resp.context['results']]
    assert completed.work_order_number in numbers

    # Both completed and closed appear; in_progress does not (search by customer)
    resp = client.get(reverse('core:pos_home'), {'q': client_obj.name})
    numbers = [w.work_order_number for w in resp.context['results']]
    assert completed.work_order_number in numbers
    assert closed.work_order_number in numbers
    assert open_wo.work_order_number not in numbers


@pytest.mark.django_db
def test_pos_home_no_query_browses_recent_finished_wos(client, admin_user, client_obj):
    """With no search entered, the register lists recently completed WOs so a
    walk-in or unnamed-client job can be found by browsing, not by having to
    guess its exact client name."""
    completed = WorkOrder.objects.create(client=client_obj, status='completed')
    walkin = WorkOrder.objects.create(client=None, status='completed')
    open_wo = WorkOrder.objects.create(client=client_obj, status='in_progress')
    client.force_login(admin_user)

    resp = client.get(reverse('core:pos_home'))
    numbers = [w.work_order_number for w in resp.context['results']]
    assert completed.work_order_number in numbers
    assert walkin.work_order_number in numbers
    assert open_wo.work_order_number not in numbers
    assert resp.context['browsing'] is True


@pytest.mark.django_db
def test_pos_home_default_browse_excludes_already_paid_wos(client, admin_user, client_obj):
    """The default (no-search) list is action-focused — an already-paid WO
    doesn't belong in 'what still needs settling', so it's excluded. An
    explicit search still finds it (e.g. to pull its receipt back up)."""
    unpaid = WorkOrder.objects.create(client=client_obj, status='completed')
    paid = WorkOrder.objects.create(client=client_obj, status='completed')
    paid.invoice.billing_status = 'paid'
    paid.invoice.save()

    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_home'))
    numbers = [w.work_order_number for w in resp.context['results']]
    assert unpaid.work_order_number in numbers
    assert paid.work_order_number not in numbers

    # Explicit search still surfaces the paid one.
    resp = client.get(reverse('core:pos_home'), {'q': paid.work_order_number})
    numbers = [w.work_order_number for w in resp.context['results']]
    assert paid.work_order_number in numbers


@pytest.mark.django_db
def test_pos_home_lists_recent_completed_sales(client, admin_user, client_obj, monkeypatch):
    """The register's 'Recent sales' card shows completed counter sales with a
    receipt link — sales previously had zero visibility on the Register page."""
    from decimal import Decimal
    from django.utils import timezone
    completed = Sale.objects.create(client=client_obj, status='completed',
                                    amount=Decimal('25.00'), payment_method='cash')
    Sale.objects.filter(pk=completed.pk).update(paid_at=timezone.now())
    draft = Sale.objects.create(client=client_obj, status='draft')

    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_home'))
    sale_numbers = [s.sale_number for s in resp.context['recent_sales']]
    assert completed.sale_number in sale_numbers
    assert draft.sale_number not in sale_numbers
    assert completed.sale_number.encode() in resp.content


@pytest.mark.django_db
def test_pos_home_browse_sorts_null_completed_date_by_created_at(client, admin_user, client_obj):
    """completed_date is only stamped by WorkOrder.mark_completed() — a WO whose
    status was set to 'completed' by any other path (e.g. a quick status update)
    has it NULL. That must not sort it out of order against dated WOs (a real bug
    caught live: the newest WO landed at the bottom of the list instead of top)."""
    older = WorkOrder.objects.create(client=client_obj, status='in_progress')
    older.mark_completed()  # stamps a real completed_date

    newer = WorkOrder.objects.create(client=client_obj, status='completed')  # no mark_completed() -> completed_date NULL
    assert newer.completed_date is None

    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_home'))
    numbers = [w.work_order_number for w in resp.context['results']]
    assert numbers.index(newer.work_order_number) < numbers.index(older.work_order_number)


@pytest.mark.django_db
def test_pos_wo_settle_get_blocked_if_not_closed(client, admin_user, client_obj):
    wo = WorkOrder.objects.create(client=client_obj, status='in_progress')
    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_wo_settle', args=[wo.pk]))
    assert resp.status_code == 302
    assert resp.url == reverse('core:pos_home')


@pytest.mark.django_db
def test_pos_wo_settle_pushes_and_pays_in_one_invoice(client, admin_user, client_obj, monkeypatch):
    """A closed WO with no prior IN push: settling with 'pay' creates exactly
    ONE invoice (via push_host_invoice) and posts ONE payment against it —
    never two separate calls that could create two invoices."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices':
            return {'data': {'id': 501, 'number': 'INV-501'}}
        if path == '/payments':
            return {'data': {'id': 1}}
        raise AssertionError(f'Unexpected call {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    monkeypatch.setattr(invoice_ninja, 'find_or_create_client', lambda c: 'inclient-1')

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {
        'action': 'pay', 'payment_method': 'cash', 'reference': '',
    })
    assert resp.status_code == 302
    assert [c for c in calls if c[1] == '/invoices'] == [('POST', '/invoices')]
    assert [c for c in calls if c[1] == '/payments'] == [('POST', '/payments')]

    wo.refresh_from_db()
    assert wo.invoice_ninja_id == '501'
    assert wo.invoice_ninja_ref == 'INV-501'
    invoice = wo.invoice
    invoice.refresh_from_db()
    assert invoice.billing_status == 'paid'
    assert invoice.amount == Decimal('40.00')
    assert invoice.payment_method == 'cash'
    assert invoice.paid_at is not None


@pytest.mark.django_db
def test_pos_wo_settle_draft_does_not_mark_paid(client, admin_user, client_obj, monkeypatch):
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices':
            return {'data': {'id': 601, 'number': 'INV-601'}}
        raise AssertionError(f'Draft must not post a payment: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    monkeypatch.setattr(invoice_ninja, 'find_or_create_client', lambda c: 'inclient-1')

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {'action': 'draft'})
    assert resp.status_code == 302
    assert ('POST', '/payments') not in calls
    wo.refresh_from_db()
    assert wo.invoice_ninja_id == '601'
    assert wo.invoice.billing_status != 'paid'


@pytest.mark.django_db
def test_pos_wo_settle_cash_without_in_records_locally(client, admin_user, client_obj, monkeypatch):
    """MB stands alone: with Invoice Ninja OFF, settling a WO in cash records the
    payment on MB's own Invoice, generates MB's receipt, and never calls IN — no
    hard block, no 'Invoice Ninja is not enabled' nag (the reviewer's bug)."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    # IN is off by default (no _enable_in()). Make any IN call an outright failure.
    def no_in(*a, **k):
        raise AssertionError('IN must not be called when Invoice Ninja is disabled')
    monkeypatch.setattr(invoice_ninja, '_request', no_in)

    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {
        'action': 'pay', 'payment_method': 'cash', 'reference': 'CASH-1',
    })
    assert resp.status_code == 302
    assert resp.url == reverse('core:pos_wo_receipt', args=[wo.pk])

    invoice = wo.invoice
    invoice.refresh_from_db()
    assert invoice.billing_status == 'paid'
    assert invoice.amount == Decimal('40.00')
    assert invoice.payment_method == 'cash'
    assert invoice.reference == 'CASH-1'
    assert invoice.paid_at is not None
    wo.refresh_from_db()
    assert not wo.invoice_ninja_id  # never pushed


@pytest.mark.django_db
def test_pos_wo_settle_bill_later_blocked_without_in(client, admin_user, client_obj):
    """'Bill Later (Draft)' only means 'push an unpaid draft to IN' — with IN off
    it has no local equivalent yet, so it's refused rather than silently no-op."""
    from decimal import Decimal
    from core.models import LineItem
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {'action': 'draft'})
    assert resp.status_code == 302
    assert resp.url == reverse('core:pos_wo_settle', args=[wo.pk])
    wo.refresh_from_db()
    assert wo.invoice.billing_status != 'paid'


@pytest.mark.django_db
def test_pos_wo_settle_no_charge_records_zero_and_receipts(client, admin_user, client_obj, monkeypatch):
    """No Charge settles a WO at $0 as a documented no-charge job — recorded on
    MB's own Invoice, never touching IN, and yields a printable receipt. Works
    even with priced lines present (they're waived to $0)."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    monkeypatch.setattr(invoice_ninja, '_request',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('IN must not be called for No Charge')))
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Warranty fix',
                            quantity=1, unit_price=Decimal('40.00'))

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {'action': 'no_charge'})
    assert resp.status_code == 302
    assert resp.url == reverse('core:pos_wo_receipt', args=[wo.pk])

    invoice = wo.invoice
    invoice.refresh_from_db()
    assert invoice.billing_status == 'paid'
    assert invoice.amount == 0
    assert invoice.payment_method == 'no_charge'
    assert invoice.paid_at is not None
    wo.refresh_from_db()
    assert not wo.invoice_ninja_id

    # Receipt prints and reads "No charge"
    receipt = client.get(reverse('core:pos_wo_receipt', args=[wo.pk]))
    assert receipt.status_code == 200
    assert b'No charge' in receipt.content


@pytest.mark.django_db
def test_pos_wo_settle_zero_total_offers_only_no_charge(client, admin_user, client_obj):
    """A WO with no priced lines ($0) can't be 'Mark Paid' (that just errors) —
    the settle screen must hide the pay action and offer only No Charge."""
    wo = WorkOrder.objects.create(client=client_obj, status='completed')  # no line items -> $0
    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_wo_settle', args=[wo.pk]))
    assert resp.status_code == 200
    assert b'value="no_charge"' in resp.content
    assert b'value="pay"' not in resp.content


@pytest.mark.django_db
def test_pos_wo_settle_no_charge_never_pushes_even_with_in_on(client, admin_user, client_obj, monkeypatch):
    """A no-charge event has no money to reconcile, so it stays local even when
    Invoice Ninja is enabled — no invoice/payment is pushed."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    monkeypatch.setattr(invoice_ninja, '_request',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('IN must not be called for No Charge')))
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Goodwill',
                            quantity=1, unit_price=Decimal('25.00'))

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {'action': 'no_charge'})
    assert resp.status_code == 302
    wo.refresh_from_db()
    assert wo.invoice.billing_status == 'paid'
    assert wo.invoice.payment_method == 'no_charge'
    assert not wo.invoice_ninja_id


@pytest.mark.django_db
def test_sale_no_charge_completes_at_zero_without_priced_lines(client, admin_user, client_obj, monkeypatch):
    """A counter Sale can be completed as No Charge with no priced lines at all
    (a goodwill handout), recorded at $0, never pushed to IN, receipt printable."""
    from core import invoice_ninja
    monkeypatch.setattr(invoice_ninja, '_request',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('IN must not be called for No Charge')))
    sale = Sale.objects.create(client=client_obj, status='draft')

    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_checkout', args=[sale.pk]), {'action': 'no_charge'})
    assert resp.status_code == 302
    sale.refresh_from_db()
    assert sale.status == 'completed'
    assert sale.amount == 0
    assert sale.payment_method == 'no_charge'
    assert sale.paid_at is not None
    assert not sale.invoice_ninja_id

    receipt = client.get(reverse('core:sale_receipt_print', args=[sale.pk]))
    assert receipt.status_code == 200
    assert b'No charge' in receipt.content


@pytest.mark.django_db
def test_pos_wo_settle_reuses_existing_invoice_never_double_pushes(client, admin_user, client_obj, monkeypatch):
    """State-aware settlement: a WO that already has an invoice_ninja_id (e.g.
    a draft sent earlier) must NOT push a second invoice when later settled
    as paid — this is the plan's 'one job = one invoice' guarantee."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed',
                                   invoice_ninja_id='999', invoice_ninja_ref='INV-999')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices':
            raise AssertionError('Must not push a second invoice for an already-pushed WO')
        if path == '/invoices/999':
            return {'data': {'status_id': '2'}}  # pre-pay read-back: Sent, not yet Paid
        if path == '/payments':
            return {'data': {'id': 1}}
        raise AssertionError(f'Unexpected call {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    monkeypatch.setattr(invoice_ninja, 'find_or_create_client', lambda c: 'inclient-1')

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {
        'action': 'pay', 'payment_method': 'check', 'reference': 'CHK-1002',
    })
    assert resp.status_code == 302
    assert ('POST', '/invoices') not in calls
    wo.refresh_from_db()
    assert wo.invoice_ninja_id == '999'  # unchanged — reused, not re-pushed
    invoice = wo.invoice
    invoice.refresh_from_db()
    assert invoice.billing_status == 'paid'
    assert invoice.reference == 'CHK-1002'


@pytest.mark.django_db
def test_pos_wo_settle_already_paid_in_in_posts_no_second_payment(client, admin_user, client_obj, monkeypatch):
    """Money-safety: a WO already pushed AND already Paid directly in Invoice
    Ninja (MB's stored billing_status doesn't know) must NOT get a second
    payment posted. The fresh pre-pay read-back catches it and self-heals MB's
    record. (The near-term real case: legacy WOs settled in IN before the POS.)"""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed',
                                   invoice_ninja_id='888', invoice_ninja_ref='INV-888')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))
    # MB's stored status is stale (not paid) — IN is the truth.
    assert wo.invoice.billing_status != 'paid'

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == '/invoices/888':
            return {'data': {'status_id': '4'}}  # 4 = Paid in IN
        raise AssertionError(f'Must not post a payment: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {
        'action': 'pay', 'payment_method': 'cash',
    })
    assert resp.status_code == 302
    assert ('POST', '/payments') not in calls  # never double-posted
    wo.refresh_from_db()
    assert wo.invoice.billing_status == 'paid'  # self-healed from IN


@pytest.mark.django_db
def test_pos_wo_settle_refuses_when_already_paid(client, admin_user, client_obj, monkeypatch):
    from decimal import Decimal
    from core import invoice_ninja
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='777')
    wo.invoice.billing_status = 'paid'
    wo.invoice.save()

    def must_not_call(*a, **kw):
        raise AssertionError('Must not call IN for an already-paid WO')
    monkeypatch.setattr(invoice_ninja, '_request', must_not_call)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {
        'action': 'pay', 'payment_method': 'cash',
    })
    assert resp.status_code == 302
    assert resp.url == reverse('core:pos_wo_settle', kwargs={'pk': wo.pk})


@pytest.mark.django_db
def test_pos_wo_settle_walkin_routes_to_walkin_client(client, admin_user, monkeypatch):
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=None, status='completed')  # anonymous walk-in
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('25.00'))

    calls = []
    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get('json')))
        if path == '/invoices':
            assert kwargs['json']['client_id'] == 'walkin-in-id'
            return {'data': {'id': 701, 'number': 'INV-701'}}
        if path == '/payments':
            return {'data': {'id': 1}}
        raise AssertionError(f'Unexpected call {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    monkeypatch.setattr(invoice_ninja, 'find_or_create_walkin_client', lambda: 'walkin-in-id')

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {
        'action': 'pay', 'payment_method': 'cash',
    })
    assert resp.status_code == 302
    wo.refresh_from_db()
    assert wo.invoice.billing_status == 'paid'


@pytest.mark.django_db
def test_pos_wo_settle_amount_is_server_computed_not_from_post(client, admin_user, client_obj, monkeypatch):
    """The amount charged is always the WO's own priced-line total — a posted
    'amount' field (if any) is ignored, same discipline as Slice 5d."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))

    captured = {}
    def fake_request(method, path, **kwargs):
        if path == '/invoices':
            return {'data': {'id': 801, 'number': 'INV-801'}}
        if path == '/payments':
            captured['amount'] = kwargs['json']['amount']
            return {'data': {'id': 1}}
        raise AssertionError(f'Unexpected call {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)
    monkeypatch.setattr(invoice_ninja, 'find_or_create_client', lambda c: 'inclient-1')

    client.force_login(admin_user)
    client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {
        'action': 'pay', 'payment_method': 'cash', 'amount': '999999.00',
    })
    assert captured['amount'] == 40.0
    wo.invoice.refresh_from_db()
    assert wo.invoice.amount == Decimal('40.00')


@pytest.mark.django_db
def test_pos_wo_settle_no_priced_lines_refused(client, admin_user, client_obj):
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {
        'action': 'pay', 'payment_method': 'cash',
    })
    assert resp.status_code == 302
    wo.refresh_from_db()
    assert wo.invoice_ninja_id == ''


@pytest.mark.django_db
def test_pos_wo_receipt_shows_reference(client, admin_user, client_obj):
    """The MB-generated receipt prints the transaction reference — the whole
    point of MB taking over the counter receipt from Invoice Ninja."""
    from decimal import Decimal
    from django.utils import timezone
    from core.models import LineItem
    wo = WorkOrder.objects.create(client=client_obj, status='completed', invoice_ninja_id='42')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('40.00'))
    invoice = wo.invoice
    invoice.billing_status = 'paid'
    invoice.amount = Decimal('40.00')
    invoice.payment_method = 'card'
    invoice.reference = 'SQ-CONF-9182'
    invoice.paid_at = timezone.now()
    invoice.save()

    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_wo_receipt', args=[wo.pk]))
    assert resp.status_code == 200
    assert b'SQ-CONF-9182' in resp.content


@pytest.mark.django_db
def test_pos_wo_receipt_blocked_before_paid(client, admin_user, client_obj):
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_wo_receipt', args=[wo.pk]))
    assert resp.status_code == 302


@pytest.mark.django_db
def test_pos_access_blocked_on_role_flag(client, client_obj):
    role = Role.objects.create(name='NoPOS', can_view_sales=False)
    user = User.objects.create_user(username='cashier1', password='x', role_obj=role)
    client.force_login(user)
    resp = client.get(reverse('core:pos_home'))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_pos_sale_start_lands_on_pos_settle_screen(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_sale_start'))
    assert resp.status_code == 302
    new_sale = Sale.objects.latest('created_at')
    assert resp.url == reverse('core:pos_sale_settle', kwargs={'pk': new_sale.pk})


@pytest.mark.django_db
def test_pos_sale_settle_screen_renders(client, admin_user, client_obj):
    """The POS sale screen reuses the same, unchanged checkout card/endpoints
    Sale detail always used — just reached from a different URL."""
    from decimal import Decimal
    from core.models import LineItem
    _enable_in()
    sale = Sale.objects.create(client=client_obj, created_by=admin_user)
    LineItem.objects.create(content_object=sale, kind='labor', description='Retail item',
                            quantity=1, unit_price=Decimal('15.00'))
    client.force_login(admin_user)
    resp = client.get(reverse('core:pos_sale_settle', args=[sale.pk]))
    assert resp.status_code == 200
    assert b'Complete Sale' in resp.content


@pytest.mark.django_db
def test_sale_detail_no_longer_has_inline_checkout_for_counter_sale(client, admin_user, client_obj):
    """Retirement check: a non-recurring (counter) Sale's detail page no
    longer shows the inline Complete Sale form — settlement is POS-only."""
    from decimal import Decimal
    from core.models import LineItem
    sale = Sale.objects.create(client=client_obj, created_by=admin_user)
    LineItem.objects.create(content_object=sale, kind='labor', description='Retail item',
                            quantity=1, unit_price=Decimal('15.00'))
    client.force_login(admin_user)
    resp = client.get(reverse('core:sale_detail', args=[sale.pk]))
    assert resp.status_code == 200
    assert b'Complete Sale' not in resp.content
    assert reverse('core:pos_sale_settle', args=[sale.pk]).encode() in resp.content


@pytest.mark.django_db
def test_work_order_detail_no_longer_has_send_to_in_button(client, admin_user, client_obj):
    """Retirement check: the WO detail page no longer offers a direct
    'Send to Invoice Ninja' action — a closed WO links to the POS instead."""
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    client.force_login(admin_user)
    resp = client.get(reverse('core:work_order_detail', args=[wo.pk]))
    assert resp.status_code == 200
    assert b'Send to Invoice Ninja' not in resp.content
    assert reverse('core:pos_wo_settle', args=[wo.pk]).encode() in resp.content


@pytest.mark.django_db
def test_recurring_sale_detail_unaffected_by_pos_change(client, admin_user, client_obj):
    """The recurring (Lane C) draft-push card is a different lane and must be
    completely unaffected by the POS/counter-sale retirement above."""
    from decimal import Decimal
    from core.models import LineItem
    _enable_in()
    sale = Sale.objects.create(client=client_obj, is_recurring=True, created_by=admin_user)
    LineItem.objects.create(content_object=sale, kind='labor', description='Monthly Service',
                            quantity=1, unit_price=Decimal('60.00'))
    client.force_login(admin_user)
    resp = client.get(reverse('core:sale_detail', args=[sale.pk]))
    assert resp.status_code == 200
    assert b'Send draft to Invoice Ninja' in resp.content


# ---------------------------------------------------------------------------
# Reports — Counter Sales section (fills the gap left by removing the Sales
# nav tab: sales history is a reporting concern, not a prominent page)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_reports_counter_sales_totals_and_excludes_recurring(client, admin_user, client_obj):
    """The Counter Sales report totals only completed, non-recurring sales
    paid within the date range — a recurring (Lane C) sale must NOT count,
    since that lane has its own reporting via Monthly Clients."""
    from decimal import Decimal
    from django.utils import timezone

    now = timezone.now()
    counter = Sale.objects.create(client=client_obj, status='completed',
                                   amount=Decimal('50.00'), payment_method='cash')
    Sale.objects.filter(pk=counter.pk).update(paid_at=now)

    recurring = Sale.objects.create(client=client_obj, status='completed', is_recurring=True,
                                     amount=Decimal('200.00'), payment_method='card')
    Sale.objects.filter(pk=recurring.pk).update(paid_at=now)

    draft = Sale.objects.create(client=client_obj, status='draft')  # not paid, excluded

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.status_code == 200
    assert resp.context['counter_sales_total'] == Decimal('50.00')
    assert resp.context['counter_sales_count'] == 1
    numbers = [s.sale_number for s in resp.context['counter_sales_list']]
    assert counter.sale_number in numbers
    assert recurring.sale_number not in numbers
    assert draft.sale_number not in numbers


@pytest.mark.django_db
def test_reports_counter_sales_walkin_shows_walkin(client, admin_user):
    """An anonymous walk-in counter sale still appears in the report, listed
    under its own record (display_name = 'Walk-in'), not dropped."""
    from decimal import Decimal
    from django.utils import timezone
    sale = Sale.objects.create(client=None, status='completed',
                                amount=Decimal('20.00'), payment_method='cash')
    Sale.objects.filter(pk=sale.pk).update(paid_at=timezone.now())

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    numbers = [s.sale_number for s in resp.context['counter_sales_list']]
    assert sale.sale_number in numbers


@pytest.mark.django_db
def test_reports_counter_sales_csv_export(client, admin_user, client_obj):
    from decimal import Decimal
    from django.utils import timezone
    sale = Sale.objects.create(client=client_obj, status='completed',
                                amount=Decimal('35.00'), payment_method='check', reference='CHK-42')
    Sale.objects.filter(pk=sale.pk).update(paid_at=timezone.now())

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports_csv', args=['counter_sales']))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert sale.sale_number in body
    assert 'CHK-42' in body
    assert client_obj.name in body


@pytest.mark.django_db
def test_sales_list_reached_from_reports_not_sidebar(client, admin_user):
    """Sales history is a management concern, so it lives under Reports (a
    management surface), not its own sidebar tab: the sidebar must NOT link
    /sales/, and the Reports page MUST (via the Counter Sales section)."""
    client.force_login(admin_user)
    dash = client.get(reverse('core:dashboard'))
    assert reverse('core:sale_list').encode() not in dash.content

    reports = client.get(reverse('core:reports'))
    assert reverse('core:sale_list').encode() in reports.content


# ---------------------------------------------------------------------------
# Reports restructure Slice 1 — domain side-menu (Financial/Tickets/Work
# Orders) replacing the single flat scroll of ~11 report sections.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_reports_default_domain_is_financial(client, admin_user):
    """No ?domain= given -> Financial, showing only its own sections."""
    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.status_code == 200
    assert resp.context['domain'] == 'financial'
    assert b'id="section-billing"' in resp.content
    assert b'id="section-countersales"' in resp.content
    assert b'id="section-volume"' not in resp.content   # Tickets domain
    assert b'id="section-mileage"' not in resp.content  # Work Orders domain


@pytest.mark.django_db
def test_reports_tickets_domain_shows_only_ticket_sections(client, admin_user):
    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'tickets'})
    assert resp.status_code == 200
    assert b'id="section-volume"' in resp.content
    assert b'id="section-sla"' in resp.content
    assert b'id="section-backlog"' in resp.content
    assert b'id="section-billing"' not in resp.content     # Financial domain
    assert b'id="section-mileage"' not in resp.content     # Work Orders domain


@pytest.mark.django_db
def test_reports_workorders_domain_shows_techperf_and_mileage(client, admin_user, client_obj):
    """Technician Performance and Mileage sit far apart in the template (the
    old flat scroll) but must both land in the Work Orders domain."""
    from datetime import date
    from core.models import Mileage
    WorkOrder.objects.create(client=client_obj, status='completed', assigned_to=admin_user)
    Mileage.objects.create(technician=admin_user, trip_date=date.today(), miles=10, trip_type='one_way')

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'workorders'})
    assert resp.status_code == 200
    assert b'id="section-techperf"' in resp.content
    assert b'id="section-mileage"' in resp.content
    assert b'id="section-billing"' not in resp.content  # Financial domain
    assert b'id="section-volume"' not in resp.content   # Tickets domain


@pytest.mark.django_db
def test_reports_invalid_domain_falls_back_to_financial(client, admin_user):
    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'nonsense'})
    assert resp.status_code == 200
    assert resp.context['domain'] == 'financial'


@pytest.mark.django_db
def test_reports_page_no_longer_shows_sales_nav_link_but_has_receipt_link(client, admin_user, client_obj):
    """Sanity check that the report's receipt links resolve to the correct
    (still-live) sale_receipt_print URL."""
    from decimal import Decimal
    from django.utils import timezone
    sale = Sale.objects.create(client=client_obj, status='completed',
                                amount=Decimal('10.00'), payment_method='cash')
    Sale.objects.filter(pk=sale.pk).update(paid_at=timezone.now())

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert reverse('core:sale_receipt_print', args=[sale.pk]).encode() in resp.content


@pytest.mark.django_db
def test_reports_collected_merges_wo_and_counter_sales(client, admin_user, client_obj):
    """'Collected (period)' in Billing Summary is TRUE revenue in the door —
    it must include counter sales, not just WO payments. Mike caught this: a
    shop running mostly counter sales was seeing Collected=$0.00, which read
    as 'no revenue' rather than 'no WO revenue.' Invoiced/Outstanding stay
    Work-Order-only (accrual concepts a counter sale doesn't have)."""
    from decimal import Decimal
    from django.utils import timezone
    wo = WorkOrder.objects.create(client=client_obj)
    _POSInvoice.objects.filter(work_order=wo).update(
        billing_status='paid', amount=Decimal('75.00'), paid_date=timezone.localdate(),
    )
    sale = Sale.objects.create(client=client_obj, status='completed',
                                amount=Decimal('25.00'), payment_method='cash')
    Sale.objects.filter(pk=sale.pk).update(paid_at=timezone.now())

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.context['paid_total'] == Decimal('100.00')  # 75 (WO) + 25 (counter sale)
    assert resp.context['counter_sales_total'] == Decimal('25.00')  # unchanged, still its own figure


# ---------------------------------------------------------------------------
# Security: org credential vault reveal is flag-gated at the ENDPOINT
# (the Settings UI is admin-only, but the reveal endpoint was reachable by
# any logged-in user via direct URL — external-review finding, Jul 10 2026)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_org_cred_reveal_denied_without_flag(client, client_obj):
    """A plain logged-in user (no can_view_org_credentials, not admin) can no
    longer reveal a non-admin_only vault entry by hitting the endpoint."""
    from core.models import OrgCredential
    role = Role.objects.create(name='PlainUser')  # no cred flags
    user = User.objects.create_user(username='plain1', password='x', role_obj=role)
    cred = OrgCredential.objects.create(name='Shop WiFi', username='admin', password='secret', admin_only=False)

    client.force_login(user)
    resp = client.get(reverse('core:cred_reveal', args=[cred.pk, 'password']))
    assert resp.status_code == 403
    assert b'secret' not in resp.content


@pytest.mark.django_db
def test_org_cred_reveal_allowed_with_flag(client, client_obj):
    """A user granted can_view_org_credentials CAN reveal a non-admin_only
    entry, and the access is logged."""
    from core.models import OrgCredential, CredentialAccessLog
    role = Role.objects.create(name='VaultViewer', can_view_org_credentials=True)
    user = User.objects.create_user(username='viewer1', password='x', role_obj=role)
    cred = OrgCredential.objects.create(name='Shop WiFi', username='admin', password='secret', admin_only=False)

    client.force_login(user)
    resp = client.get(reverse('core:cred_reveal', args=[cred.pk, 'password']))
    assert resp.status_code == 200
    assert resp.content == b'secret'
    assert CredentialAccessLog.objects.filter(credential=cred, user=user, action='viewed').exists()


@pytest.mark.django_db
def test_org_cred_reveal_admin_only_entry_still_admin_only(client, client_obj):
    """The extra admin_only tier survives: even a flag-holder can't reveal an
    entry marked admin_only unless they're an admin."""
    from core.models import OrgCredential
    role = Role.objects.create(name='VaultViewer2', can_view_org_credentials=True)
    user = User.objects.create_user(username='viewer2', password='x', role_obj=role)
    cred = OrgCredential.objects.create(name='Root PW', username='root', password='topsecret', admin_only=True)

    client.force_login(user)
    resp = client.get(reverse('core:cred_reveal', args=[cred.pk, 'password']))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_org_cred_reveal_admin_always_allowed(client, admin_user):
    """An admin reveals both normal and admin_only entries (unchanged)."""
    from core.models import OrgCredential
    normal = OrgCredential.objects.create(name='WiFi', username='u', password='p1', admin_only=False)
    restricted = OrgCredential.objects.create(name='Root', username='root', password='p2', admin_only=True)
    client.force_login(admin_user)
    assert client.get(reverse('core:cred_reveal', args=[normal.pk, 'password'])).content == b'p1'
    assert client.get(reverse('core:cred_reveal', args=[restricted.pk, 'password'])).content == b'p2'


# ── Security #1: object-level authorization on WO detail + mutations,        ─
# ── and ticket mutations (external review, Jul 10 2026) ─────────────────────
#
# Before this fix, WorkOrderDetailView and every WO/ticket mutation endpoint
# fetched by raw pk with no visibility check — a logged-in non-admin tech
# could view/act on any WO or ticket by guessing/incrementing the URL, not
# just their own + the unclaimed pool. These lock in that a non-owning,
# non-admin tech now 404s (mirroring the scoping TicketDetailView already
# had), while the claim/take-over paths that scoping is designed to preserve
# still work.

@pytest.mark.django_db
def test_wo_detail_404s_for_non_owning_non_admin_tech(client, client_obj):
    owner = User.objects.create_user(username='wo_owner', password='x', is_staff=False)
    other = User.objects.create_user(username='wo_other', password='x', is_staff=False)
    wo = WorkOrder.objects.create(client=client_obj, assigned_to=owner)

    client.force_login(other)
    resp = client.get(reverse('core:work_order_detail', args=[wo.pk]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_wo_detail_visible_to_owner_unclaimed_pool_and_admin(client, client_obj, admin_user):
    owner = User.objects.create_user(username='wo_owner2', password='x', is_staff=False)
    picker = User.objects.create_user(username='wo_picker', password='x', is_staff=False)
    owned = WorkOrder.objects.create(client=client_obj, assigned_to=owner)
    unclaimed = WorkOrder.objects.create(client=client_obj)

    client.force_login(owner)
    assert client.get(reverse('core:work_order_detail', args=[owned.pk])).status_code == 200

    client.force_login(picker)
    assert client.get(reverse('core:work_order_detail', args=[unclaimed.pk])).status_code == 200

    client.force_login(admin_user)
    assert client.get(reverse('core:work_order_detail', args=[owned.pk])).status_code == 200


@pytest.mark.django_db
def test_wo_quick_update_404s_for_non_owning_tech(client, client_obj):
    owner = User.objects.create_user(username='wo_owner3', password='x', is_staff=False)
    other = User.objects.create_user(username='wo_other3', password='x', is_staff=False)
    wo = WorkOrder.objects.create(client=client_obj, assigned_to=owner)

    client.force_login(other)
    resp = client.post(reverse('core:work_order_quick_update', args=[wo.pk]), {'status': 'in_progress'})
    assert resp.status_code == 404
    wo.refresh_from_db()
    assert wo.status != 'in_progress'


@pytest.mark.django_db
def test_wo_claim_still_works_on_unclaimed_pool(client, client_obj):
    """Scoping includes the unassigned pool — a tech must still be able to
    claim unclaimed work, the core reason the pool is in the queryset."""
    tech = User.objects.create_user(username='wo_claimer', password='x', is_staff=False)
    wo = WorkOrder.objects.create(client=client_obj)

    client.force_login(tech)
    resp = client.post(reverse('core:wo_claim', args=[wo.pk]))
    assert resp.status_code == 302
    wo.refresh_from_db()
    assert wo.assigned_to == tech


@pytest.mark.django_db
def test_ticket_convert_404s_for_non_owning_tech(client, client_obj):
    owner = User.objects.create_user(username='tkt_owner', password='x', is_staff=False)
    other = User.objects.create_user(username='tkt_other', password='x', is_staff=False)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D', assigned_to=owner)

    client.force_login(other)
    resp = client.get(reverse('core:ticket_convert', args=[ticket.pk]))
    assert resp.status_code == 404
    assert not WorkOrder.objects.filter(ticket=ticket).exists()


@pytest.mark.django_db
def test_ticket_delete_still_admin_gated_not_broken_by_scoping(client, client_obj, admin_user):
    """Admins bypass scoping entirely (existing _is_admin short-circuit), so
    the pre-existing staff-only delete guard is unaffected by this change."""
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_delete', args=[ticket.pk]))
    assert resp.status_code == 302
    assert not Ticket.objects.filter(pk=ticket.pk).exists()


@pytest.mark.django_db
def test_ticket_edit_404s_for_non_owning_tech(client, client_obj):
    owner = User.objects.create_user(username='tkt_owner2', password='x', is_staff=False)
    other = User.objects.create_user(username='tkt_other2', password='x', is_staff=False)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D', assigned_to=owner)

    client.force_login(other)
    resp = client.get(reverse('core:ticket_edit', args=[ticket.pk]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_wo_edit_404s_for_non_owning_tech(client, client_obj):
    owner = User.objects.create_user(username='wo_owner4', password='x', is_staff=False)
    other = User.objects.create_user(username='wo_other4', password='x', is_staff=False)
    wo = WorkOrder.objects.create(client=client_obj, assigned_to=owner)

    client.force_login(other)
    resp = client.get(reverse('core:work_order_edit', args=[wo.pk]))
    assert resp.status_code == 404


# ── Security #4: KB markdown = stored XSS (external review, Jul 10 2026) ────
#
# markdownify() ran python-markdown output straight through mark_safe with no
# sanitizer — raw HTML (including <script>) in an article's Markdown source
# rendered verbatim. KB articles are staff-authored (can_manage_kb), so this
# was stored-XSS-by-a-trusted-writer rather than an open injection point, but
# a compromised staff account or a pasted-in hostile snippet shouldn't get a
# live <script> into every reader's browser. Fix: bleach allowlist.

def test_markdownify_strips_script_tags():
    """The <script> element itself is stripped — its text content may survive
    as inert plain text (bleach's default strip=True behavior), but it can no
    longer execute since there's no surrounding <script> tag."""
    from core.templatetags.mb_icons import markdownify
    html = str(markdownify('Hello <script>alert(1)</script> world'))
    assert '<script' not in html
    assert '</script>' not in html


def test_markdownify_strips_inline_event_handlers():
    from core.templatetags.mb_icons import markdownify
    html = str(markdownify('<img src=x onerror="alert(1)">'))
    assert 'onerror' not in html


def test_markdownify_preserves_legitimate_formatting():
    from core.templatetags.mb_icons import markdownify
    html = str(markdownify('# Heading\n\n**bold** and _em_\n\n- item one\n- item two'))
    assert '<h1' in html
    assert '<strong>bold</strong>' in html
    assert '<li>item one</li>' in html


@pytest.mark.django_db
def test_kb_detail_view_sanitizes_stored_script(client, admin_user):
    from core.models import KBArticle
    article = KBArticle.objects.create(
        title='Test', content='Notes <script>alert(document.cookie)</script>',
    )
    client.force_login(admin_user)
    resp = client.get(reverse('core:kb_detail', args=[article.pk]))
    assert resp.status_code == 200
    body = resp.content.decode()
    # base.html legitimately carries its own <script> blocks (dark mode/font
    # size boot script) — check the injected payload specifically, not every
    # <script> tag on the page.
    assert '<script>alert(document.cookie)</script>' not in body


# ── Security #5: s3_secret_key + google_maps_api_key now encrypted at rest ──
#
# Both were plain CharFields while every other secret (IN token, email/inbound
# passwords, device+org creds) was already EncryptedCharField — a consistency
# gap flagged by the Jul 10 2026 review. Migration 0084 converts the field
# class and re-saves each existing row so old plaintext values get encrypted
# on upgrade (verified manually against a scratch DB during the build: raw
# column held plaintext pre-migration, Fernet ciphertext post-migration, and
# the ORM round-trips back to the original value). This test locks in that
# the fields are genuinely encrypted going forward, not just typed that way.

@pytest.mark.django_db
def test_s3_secret_key_and_maps_key_encrypted_at_rest():
    from django.db import connection
    settings_obj = SiteSettings.objects.create(
        google_maps_api_key='AIzaRealLookingKey123',
        s3_secret_key='real-s3-secret-value',
    )
    with connection.cursor() as cur:
        cur.execute(
            'SELECT google_maps_api_key, s3_secret_key FROM site_settings WHERE id = %s',
            [settings_obj.pk],
        )
        raw_maps_key, raw_s3_secret = cur.fetchone()
    # Raw bytes on disk must not be the plaintext value.
    assert raw_maps_key != 'AIzaRealLookingKey123'
    assert raw_s3_secret != 'real-s3-secret-value'
    # But the ORM decrypts transparently.
    settings_obj.refresh_from_db()
    assert settings_obj.google_maps_api_key == 'AIzaRealLookingKey123'
    assert settings_obj.s3_secret_key == 'real-s3-secret-value'

# ── MFA setup: no dead-end Cancel link while enrollment is mandatory ────────

@pytest.mark.django_db
def test_mfa_setup_hides_cancel_when_mandatory_and_no_device(client):
    from core.models import SiteSettings
    SiteSettings.get().__class__.objects.update(require_mfa=True)
    tech = User.objects.create_user(username='newtech', password='x', is_staff=False)
    client.force_login(tech)
    resp = client.get(reverse('setup'))
    assert resp.status_code == 200
    # The stock two_factor Cancel link points at '/', which — while MFA is
    # mandatory and this user has no device — bounces right back here via a
    # GET and silently resets the wizard's secret. It must not be offered.
    assert b'>Cancel<' not in resp.content


@pytest.mark.django_db
def test_mfa_setup_shows_cancel_when_adding_a_second_device(client, admin_user):
    from core.models import SiteSettings
    from django_otp.plugins.otp_totp.models import TOTPDevice
    SiteSettings.get().__class__.objects.update(require_mfa=True)
    TOTPDevice.objects.create(user=admin_user, name='existing', confirmed=True)
    client.force_login(admin_user)
    resp = client.get(reverse('setup'))
    assert resp.status_code == 200
    # Already has a confirmed device — Cancel is safe here, nothing to trap.
    assert b'>Cancel<' in resp.content


@pytest.mark.django_db
def test_mfa_setup_survives_intervening_get(client):
    """A GET to /setup/ mid-enrollment (favicon 302, reload, background poll)
    must NOT invalidate the QR the user already scanned. Reproduces the real
    bug: without the resume-on-GET fix, the code from the shown QR is rejected."""
    import base64
    from core.models import SiteSettings
    from django_otp.oath import totp
    SiteSettings.get().__class__.objects.update(require_mfa=True)
    u = User.objects.create_user(username='enrollee', password='x', is_staff=False)
    client.force_login(u)
    P = 'mfa_setup_view'
    client.get('/account/two_factor/setup/')
    client.post('/account/two_factor/setup/', {P + '-current_step': 'welcome'})
    client.post('/account/two_factor/setup/', {P + '-current_step': 'method', 'method-method': 'generator'})
    secret = client.session.get('django_two_factor-qr_secret_key')
    assert secret, 'QR secret should be in session on the generator step'

    # The QR is now on screen. Simulate a stray GET (the thing that used to break it).
    client.get('/account/two_factor/setup/')

    # User submits the code from the QR they scanned.
    code = str(totp(base64.b32decode(secret))).zfill(6)
    r = client.post('/account/two_factor/setup/', {P + '-current_step': 'generator', 'generator-token': code})
    assert r.status_code == 302, 'Setup must complete despite the intervening GET (was 200/rejected before fix)'
    from django_otp.plugins.otp_totp.models import TOTPDevice
    assert TOTPDevice.objects.filter(user=u, confirmed=True).exists()

# ── Owner dashboard: business metrics, billing filters, backlog age bands ────

@pytest.mark.django_db
def test_owner_dashboard_business_metrics(client, client_obj, admin_user):
    from datetime import timedelta
    from django.utils import timezone

    # Ready to bill: completed WO whose auto-invoice is still uninvoiced.
    ready = WorkOrder.objects.create(client=client_obj, status='completed')
    # Outstanding: a WO billed (invoiced) and waiting on payment.
    billed = WorkOrder.objects.create(client=client_obj, status='completed')
    inv = billed.invoice
    inv.billing_status = 'invoiced'
    inv.amount = 150
    inv.save()
    # Paid WO must count toward neither figure.
    paid = WorkOrder.objects.create(client=client_obj, status='completed')
    paid.invoice.billing_status = 'paid'
    paid.invoice.amount = 999
    paid.invoice.save()
    # An open WO for the open-count.
    WorkOrder.objects.create(client=client_obj, status='in_progress')
    # An open ticket 5 days old for the backlog band.
    old = Ticket.objects.create(client=client_obj, subject='old', description='d')
    Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=5))

    client.force_login(admin_user)
    resp = client.get(reverse('core:dashboard'))
    ctx = resp.context

    assert ctx['ready_to_bill_count'] == 1
    assert float(ctx['outstanding_total']) == 150.0   # billed only, not the paid 999
    assert ctx['open_wo_count'] == 1                   # only the in_progress one
    assert ctx['backlog_buckets']['b3to7'] == 1


@pytest.mark.django_db
def test_workorder_list_billing_ready_filter(client, client_obj, admin_user):
    ready = WorkOrder.objects.create(client=client_obj, status='completed')
    billed = WorkOrder.objects.create(client=client_obj, status='completed')
    billed.invoice.billing_status = 'invoiced'
    billed.invoice.save()

    client.force_login(admin_user)
    resp = client.get(reverse('core:work_order_list') + '?billing=ready')
    pks = {wo.pk for wo in resp.context['work_orders']}
    assert ready.pk in pks and billed.pk not in pks


@pytest.mark.django_db
def test_workorder_list_billing_outstanding_filter(client, client_obj, admin_user):
    ready = WorkOrder.objects.create(client=client_obj, status='completed')
    billed = WorkOrder.objects.create(client=client_obj, status='completed')
    billed.invoice.billing_status = 'invoiced'
    billed.invoice.save()

    client.force_login(admin_user)
    resp = client.get(reverse('core:work_order_list') + '?billing=outstanding')
    pks = {wo.pk for wo in resp.context['work_orders']}
    assert billed.pk in pks and ready.pk not in pks


@pytest.mark.django_db
def test_ticket_list_age_band_filter(client, client_obj, admin_user):
    from datetime import timedelta
    from django.utils import timezone

    fresh = Ticket.objects.create(client=client_obj, subject='fresh', description='d')
    old = Ticket.objects.create(client=client_obj, subject='old', description='d')
    Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=10))

    client.force_login(admin_user)
    resp = client.get(reverse('core:ticket_list') + '?age=gt7')
    pks = {t.pk for t in resp.context['tickets']}
    assert old.pk in pks and fresh.pk not in pks


@pytest.mark.django_db
def test_settings_colors_tab_has_dashboard_block(client, admin_user):
    client.force_login(admin_user)
    resp = client.get('/settings/?tab=colors')
    assert resp.status_code == 200
    body = resp.content
    assert b'Dashboard Colors' in body
    assert b'colors-color_dash_tickets_bg' in body
    assert b'colors-color_dash_backlog4_text' in body


@pytest.mark.django_db
def test_admin_dashboard_counts_and_marks_triage(client, admin_user):
    # Triage tickets are open + unassigned, so they count in the admin's Open
    # tickets and get a "Needs triage" marker in the worklist card.
    bucket = Client.get_unsorted()
    Ticket.objects.create(client=bucket, subject='unsorted inbound', description='d',
                          ticket_number='TKT-TR-1', status='new')
    client.force_login(admin_user)
    resp = client.get(reverse('core:dashboard'))
    assert resp.context['open_ticket_count'] == 1
    assert b'Needs triage' in resp.content


@pytest.mark.django_db
def test_tech_dashboard_shows_triage_pool_tile(client, client_obj):
    tech = User.objects.create_user(username='dtech', password='x', is_staff=False, level=1)
    bucket = Client.get_unsorted()
    Ticket.objects.create(client=bucket, subject='inbound', description='d',
                          ticket_number='TKT-TR-2', status='new')
    client.force_login(tech)
    resp = client.get(reverse('core:dashboard'))
    assert resp.status_code == 200
    assert b'Triage pool' in resp.content


# ── Backup destinations + schedule (Settings → Maintenance → Backups) ─────────
# The admin configures onsite/offsite destinations, retention and schedule in the
# app; Django renders backup-config.env + .rclone.conf (secret-bearing, 0600) that
# the out-of-band scripts read. The MB VM is never a destination. These tests point
# BASE_DIR at a tmp dir so the rendered files land there, not in the repo.

def _backup_post(onsite=False, offsite=False, **over):
    data = {
        'tab': 'backups',
        'backups-backup_onsite_retention_mode': 'count',
        'backups-backup_onsite_retention_value': '14',
        'backups-backup_onsite_schedule_days': 'daily',
        'backups-backup_onsite_schedule_times': '02:00',
        'backups-backup_offsite_retention_mode': 'age',
        'backups-backup_offsite_retention_value': '30',
        'backups-backup_offsite_schedule_days': 'daily',
        'backups-backup_offsite_schedule_times': '02:00',
    }
    if onsite:
        data['backups-backup_onsite_enabled'] = 'on'
        data.setdefault('backups-backup_onsite_host', '192.0.2.50')
        data.setdefault('backups-backup_onsite_share', 'VM')
        data.setdefault('backups-backup_onsite_username', 'mike')
        data.setdefault('backups-backup_onsite_password', 'nassecret')
        data.setdefault('backups-backup_onsite_folder', 'mb-backups')
    if offsite:
        data['backups-backup_offsite_enabled'] = 'on'
        data.setdefault('backups-backup_s3_endpoint', 's3.us-west-002.backblazeb2.com')
        data.setdefault('backups-backup_s3_bucket', 'my-bucket')
        data.setdefault('backups-backup_s3_path', 'mb')
        data.setdefault('backups-backup_s3_access_key', 'AKIAtest')
        data.setdefault('backups-backup_s3_secret_key', 'secret123')
    data.update(over)
    return data


@pytest.mark.django_db
def test_onsite_test_destination_probes_share_root_not_folder(settings, tmp_path):
    """The Test button probes the SHARE root, not share/folder — the folder
    doesn't need to pre-exist (rclone creates it on the first real copy); an
    `lsd` on a not-yet-existing subfolder would otherwise always fail."""
    settings.BASE_DIR = tmp_path
    from unittest.mock import patch, MagicMock
    from core import backup_ops
    site = SiteSettings.get()
    site.backup_onsite_enabled = True
    site.backup_onsite_host = '192.0.2.50'
    site.backup_onsite_share = 'VM'
    site.backup_onsite_username = 'mike'
    site.backup_onsite_folder = 'mb-backups'  # does NOT exist yet on the NAS
    site.save()
    with patch.object(backup_ops, 'rclone_bin', return_value=tmp_path / 'fake-rclone'):
        (tmp_path / 'fake-rclone').write_text('')  # just needs to exist
        with patch('core.backup_ops.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
            backup_ops.test_destination(site, 'onsite')
    probed_remote = mock_run.call_args[0][0][-1]
    assert probed_remote == 'mbonsite:VM', 'must probe the share root, not share/folder'
    # But the actual backup ship target still includes the folder.
    assert backup_ops.onsite_remote_target(site) == 'mbonsite:VM/mb-backups'


@pytest.mark.django_db
def test_backup_settings_both_destinations_render_files(admin_user, client, settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    from core import backup_ops
    monkeypatch.setattr(backup_ops, '_obscure', lambda binary, plaintext: 'obscured-placeholder')
    client.force_login(admin_user)
    resp = client.post(reverse('core:settings'), _backup_post(
        onsite=True, offsite=True,
        **{'backups-backup_onsite_retention_value': '7',
           'backups-backup_offsite_retention_value': '45',
           'backups-backup_onsite_schedule_days': 'daily',
           'backups-backup_onsite_schedule_times': '06:00,18:00',
           'backups-backup_offsite_schedule_days': 'mon,wed,fri',
           'backups-backup_offsite_schedule_times': '02:00'}))
    assert resp.status_code == 302
    assert resp['Location'].endswith('tab=maintenance')
    site = SiteSettings.get()
    assert site.backup_onsite_enabled and site.backup_offsite_enabled
    assert site.backup_s3_secret_key == 'secret123'  # encrypted at rest, decrypts here

    manifest = backup_ops.manifest_path().read_text()
    assert 'BACKUP_ONSITE_ENABLED="1"' in manifest
    assert 'BACKUP_ONSITE_RCLONE_REMOTE="mbonsite:VM/mb-backups"' in manifest
    assert 'BACKUP_ONSITE_RETENTION_MODE="count"' in manifest
    assert 'BACKUP_ONSITE_RETENTION_VALUE="7"' in manifest
    assert 'BACKUP_ONSITE_SCHEDULE_TIMES="06:00,18:00"' in manifest
    assert 'BACKUP_OFFSITE_ENABLED="1"' in manifest
    assert 'BACKUP_RCLONE_REMOTE="mbbackup:my-bucket/mb"' in manifest
    assert 'BACKUP_OFFSITE_RETENTION_MODE="age"' in manifest
    assert 'BACKUP_OFFSITE_RETENTION_VALUE="45"' in manifest
    assert 'BACKUP_OFFSITE_SCHEDULE_DAYS="mon,wed,fri"' in manifest
    assert 'BACKUP_OFFSITE_SCHEDULE_TIMES="02:00"' in manifest
    # Independent schedules — onsite twice daily, offsite Mon/Wed/Fri once.
    assert site.backup_onsite_schedule_times == '06:00,18:00'
    assert site.backup_offsite_schedule_days == 'mon,wed,fri'

    conf_path = backup_ops.rclone_conf_path()
    conf = conf_path.read_text()
    assert '[mbbackup]' in conf and 'secret_access_key = secret123' in conf
    assert '[mbonsite]' in conf and 'type = smb' in conf and 'host = 192.0.2.50' in conf
    # The onsite password must be rclone-OBSCURED, never stored in plaintext.
    assert 'nassecret' not in conf, 'onsite password must not appear in plaintext in .rclone.conf'
    import os, stat
    assert stat.S_IMODE(os.stat(conf_path).st_mode) == 0o600, 'secret file must be owner-only'


def _rclone_ok():
    from core import backup_ops
    return backup_ops.rclone_bin().exists()


rclone_skip = pytest.mark.skipif(not _rclone_ok(), reason='rclone binary not vendored on this runner')


@rclone_skip
def test_obscure_password_is_not_plaintext():
    """rclone's SMB backend needs the password in rclone's own obfuscation
    format, not plaintext, in .rclone.conf."""
    from core import backup_ops
    obscured = backup_ops._obscure(backup_ops.rclone_bin(), 'nassecret')
    assert obscured and obscured != 'nassecret'


def test_obscure_raises_on_rclone_failure(monkeypatch):
    """A supplied password that rclone can't obscure must fail loud, never
    silently return '' (which render_config would then write as a blank
    password in .rclone.conf)."""
    from core import backup_ops

    class _FakeResult:
        returncode = 1
        stdout = ''
        stderr = 'exit status 1'

    monkeypatch.setattr(backup_ops.subprocess, 'run', lambda *a, **k: _FakeResult())
    with pytest.raises(backup_ops.BackupConfigError):
        backup_ops._obscure(backup_ops.rclone_bin(), 'nassecret')


@pytest.mark.django_db
def test_render_config_writes_nothing_when_obscure_fails(settings, tmp_path, monkeypatch):
    """render_config must not write a blank-password .rclone.conf (or touch
    the manifest at all) when rclone obscure fails — last-good config stays
    in place until the failure is fixed."""
    settings.BASE_DIR = tmp_path
    from core import backup_ops

    def _boom(*a, **k):
        raise backup_ops.BackupConfigError('rclone obscure exited 1: boom')

    monkeypatch.setattr(backup_ops, '_obscure', _boom)
    site = SiteSettings.get()
    site.backup_onsite_enabled = True
    site.backup_onsite_host = '192.0.2.50'
    site.backup_onsite_share = 'VM'
    site.backup_onsite_username = 'mike'
    site.backup_onsite_password = 'nassecret'
    site.save()
    with pytest.raises(backup_ops.BackupConfigError):
        backup_ops.render_config(site)
    assert not backup_ops.rclone_conf_path().exists()
    assert not backup_ops.manifest_path().exists()


@pytest.mark.django_db
def test_backup_settings_view_shows_error_when_obscure_fails(admin_user, client, settings, tmp_path, monkeypatch):
    """The settings form save must surface the failure to the admin instead
    of silently reporting success with a broken config underneath."""
    settings.BASE_DIR = tmp_path
    from core import backup_ops

    def _boom(*a, **k):
        raise backup_ops.BackupConfigError('rclone obscure exited 1: boom')

    monkeypatch.setattr(backup_ops, '_obscure', _boom)
    client.force_login(admin_user)
    resp = client.post(reverse('core:settings'), _backup_post(onsite=True), follow=True)
    assert resp.status_code == 200
    assert b'could not be' in resp.content.lower()
    assert not backup_ops.rclone_conf_path().exists()


@pytest.mark.django_db
def test_backup_settings_offsite_only_clears_stale_when_disabled(admin_user, client, settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    from core import backup_ops
    # This test is about stale-stanza clearing, not rclone availability —
    # mock obscure so it doesn't need a real vendored binary.
    monkeypatch.setattr(backup_ops, '_obscure', lambda binary, plaintext: 'obscured-placeholder')
    client.force_login(admin_user)
    # First enable offsite → renders rclone.conf with [mbbackup].
    client.post(reverse('core:settings'), _backup_post(offsite=True))
    assert '[mbbackup]' in backup_ops.rclone_conf_path().read_text()
    # Now switch to onsite-only → offsite's stanza+secret must be gone, but the
    # file persists (onsite is also an rclone remote now, via [mbonsite]).
    resp = client.post(reverse('core:settings'), _backup_post(onsite=True))
    assert resp.status_code == 302
    manifest = backup_ops.manifest_path().read_text()
    assert 'BACKUP_OFFSITE_ENABLED="0"' in manifest
    assert 'BACKUP_RCLONE_REMOTE=""' in manifest
    conf = backup_ops.rclone_conf_path().read_text()
    assert '[mbbackup]' not in conf, 'stale offsite stanza+secret must be cleared'
    assert 'secret123' not in conf
    assert '[mbonsite]' in conf


@pytest.mark.django_db
def test_render_config_removes_conf_when_no_destination_enabled(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    from core import backup_ops
    site = SiteSettings.get()
    site.backup_offsite_enabled = True
    site.backup_s3_bucket = 'b'
    site.save()
    backup_ops.render_config(site)
    assert backup_ops.rclone_conf_path().exists()
    site.backup_offsite_enabled = False
    site.save()
    backup_ops.render_config(site)
    assert not backup_ops.rclone_conf_path().exists()


@pytest.mark.django_db
def test_backup_settings_requires_a_destination(admin_user, client, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    client.force_login(admin_user)
    resp = client.post(reverse('core:settings'), _backup_post())  # neither enabled
    assert resp.status_code == 200  # invalid → re-render, not saved
    assert SiteSettings.get().backup_onsite_enabled is False
    assert b'at least one destination' in resp.content.lower()


@pytest.mark.django_db
def test_backup_settings_onsite_requires_host_share_username(admin_user, client, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    client.force_login(admin_user)
    resp = client.post(reverse('core:settings'),
                       _backup_post(onsite=True, **{'backups-backup_onsite_host': ''}))
    assert resp.status_code == 200
    assert b'onsite host is required' in resp.content.lower()


@pytest.mark.django_db
def test_backup_settings_rejects_bad_time(admin_user, client, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    client.force_login(admin_user)
    resp = client.post(reverse('core:settings'),
                       _backup_post(onsite=True, **{'backups-backup_onsite_schedule_times': '25:00'}))
    assert resp.status_code == 200
    assert b'invalid time' in resp.content.lower()


@pytest.mark.django_db
def test_maintenance_tab_shows_backup_status_and_updates(admin_user, client, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    from core import backup_ops
    backup_ops._logs_dir().mkdir(parents=True, exist_ok=True)
    backup_ops.status_path().write_text(json.dumps({
        'state': 'succeeded', 'finished_at': '2026-07-13T10:00:00+00:00',
        'size': '4.2M', 'destination': 'S3: mbbackup:my-bucket/mb',
    }))
    client.force_login(admin_user)
    resp = client.get('/settings/?tab=maintenance')
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'Last backup succeeded' in body
    assert '4.2M' in body
    assert 'Software Updates' in body  # Backups + Updates cards share the Maintenance tab


def test_request_backup_now_writes_trigger_and_refuses_double(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    from core import backup_ops
    assert backup_ops.request_backup_now() is True
    assert backup_ops.trigger_path().exists()
    assert backup_ops.read_status()['state'] == 'queued'
    # A second request while queued/running must be refused (no double run).
    assert backup_ops.request_backup_now() is False


def test_mb_backup_sh_staging_only_succeeds_on_near_empty_db(tmp_path):
    """Regression: scripts/mb_backup.sh used to reject the finished archive if
    it was under a fixed 100KB floor -- which a genuinely fresh/near-empty
    install's DB (a handful of tables, no real data yet) can easily be under,
    failing every backup a brand-new self-hoster tries to run. The check was
    replaced with confirming the DB snapshot is actually present in the
    archive by name, which doesn't care how much data the DB holds. Runs the
    real script (via MB_BACKUP_APP, a test-only override -- every real deploy
    runs with it unset) against a scratch app dir with a structurally valid
    but empty-of-data SQLite file."""
    import os
    import shutil
    import sqlite3
    import subprocess
    import tarfile
    from pathlib import Path

    app_dir = tmp_path / "app"
    (app_dir / "protected").mkdir(parents=True)
    (app_dir / "media").mkdir(parents=True)
    (app_dir / "logs").mkdir(parents=True)
    (app_dir / "backups").mkdir(parents=True)
    (app_dir / ".env").write_text("FIELD_ENCRYPTION_KEY=test\n")

    # A minimal but structurally valid DB: >=50 tables (the script's own
    # sanity floor), no real rows -- mirrors a fresh install before any
    # client/ticket data exists.
    db_path = app_dir / "db.sqlite3"
    conn = sqlite3.connect(db_path)
    for i in range(55):
        conn.execute(f"CREATE TABLE t{i} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    repo_root = Path(__file__).resolve().parent.parent
    venv_python = repo_root / "venv" / "bin" / "python"
    if not venv_python.exists():
        pytest.skip("no local venv to point the script's snapshot step at")
    (app_dir / "venv").symlink_to(repo_root / "venv")

    out = tmp_path / "proof.tar.gz"
    env = {**os.environ, "MB_BACKUP_APP": str(app_dir)}
    result = subprocess.run(
        ["bash", str(repo_root / "scripts" / "mb_backup.sh"), "--staging-only", str(out)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert out.exists()
    with tarfile.open(out) as tf:
        names = tf.getnames()
    assert any(n.startswith("db-") and n.endswith(".sqlite3") for n in names), names


@pytest.mark.django_db
def test_render_backup_config_command_runs(settings, tmp_path):
    """The on-deploy render command must not crash and must write the manifest."""
    settings.BASE_DIR = tmp_path
    from django.core.management import call_command
    from core import backup_ops
    site = SiteSettings.get()
    site.backup_offsite_enabled = True
    site.backup_s3_bucket = 'b'
    site.backup_s3_endpoint = 'e'
    site.save()
    call_command('render_backup_config')
    assert 'BACKUP_OFFSITE_ENABLED="1"' in backup_ops.manifest_path().read_text()


@pytest.mark.django_db
def test_render_backup_config_command_runs_with_onsite_enabled(settings, tmp_path, monkeypatch):
    """Onsite-enabled must not crash the command (regression: stale backup_onsite_path ref).
    Mocks rclone obscure — this test is about the command's onsite_remote_target
    reference, not rclone availability (see test_obscure_raises_on_rclone_failure
    for the fail-closed-on-missing-rclone behavior)."""
    settings.BASE_DIR = tmp_path
    from django.core.management import call_command
    from core import backup_ops
    monkeypatch.setattr(backup_ops, '_obscure', lambda binary, plaintext: 'obscured-placeholder')
    site = SiteSettings.get()
    site.backup_onsite_enabled = True
    site.backup_onsite_host = 'nas.local'
    site.backup_onsite_share = 'VM'
    site.backup_onsite_username = 'u'
    site.backup_onsite_password = 'p'
    site.save()
    call_command('render_backup_config')
    assert 'BACKUP_ONSITE_ENABLED="1"' in backup_ops.manifest_path().read_text()


@pytest.mark.django_db
def test_backup_run_view_queues_out_of_band(admin_user, client, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    from core import backup_ops
    client.force_login(admin_user)
    resp = client.post(reverse('core:backup_run'))
    assert resp.status_code == 200
    assert b'Backing up' in resp.content  # in-progress fragment
    assert backup_ops.trigger_path().exists()


# ── Assets (managed inventory — Slice 1) ────────────────────────────────

from core.models import Asset


@pytest.mark.django_db
def test_asset_create_attaches_to_client(client, client_obj, admin_user):
    client.force_login(admin_user)
    resp = client.post(
        reverse('core:asset_create', args=[client_obj.pk]),
        {'name': 'Reception PC', 'asset_type': 'workstation',
         'identifier': 'RCP-01', 'is_active': 'on'},
    )
    assert resp.status_code == 302
    asset = Asset.objects.get(name='Reception PC')
    assert asset.client_id == client_obj.pk
    assert asset.asset_type == 'workstation'
    assert asset.identifier == 'RCP-01'
    assert asset.is_active is True


@pytest.mark.django_db
def test_asset_edit_updates_fields(client, client_obj, admin_user):
    asset = Asset.objects.create(client=client_obj, name='DC01', asset_type='server')
    client.force_login(admin_user)
    resp = client.post(
        reverse('core:asset_edit', args=[asset.pk]),
        {'name': 'DC01', 'asset_type': 'server', 'identifier': 'srv-dc01',
         'is_active': 'on'},
    )
    assert resp.status_code == 302
    asset.refresh_from_db()
    assert asset.identifier == 'srv-dc01'


@pytest.mark.django_db
def test_asset_delete_requires_admin(client, client_obj, admin_user, tech_user):
    asset = Asset.objects.create(client=client_obj, name='Old Printer')

    # A non-admin tech is forbidden and the asset survives.
    client.force_login(tech_user)
    resp = client.post(reverse('core:asset_delete', args=[asset.pk]))
    assert resp.status_code == 403
    assert Asset.objects.filter(pk=asset.pk).exists()

    # An admin can delete it.
    client.force_login(admin_user)
    resp = client.post(reverse('core:asset_delete', args=[asset.pk]))
    assert resp.status_code == 302
    assert not Asset.objects.filter(pk=asset.pk).exists()


@pytest.mark.django_db
def test_asset_card_renders_on_client_detail(client, client_obj, admin_user):
    Asset.objects.create(client=client_obj, name='Reception PC', identifier='RCP-01')
    client.force_login(admin_user)
    resp = client.get(reverse('core:client_detail', args=[client_obj.pk]))
    assert resp.status_code == 200
    assert b'Reception PC' in resp.content
    assert b'RCP-01' in resp.content


# ── Contracts (managed-client layer — Slice 2) ──────────────────────────

from core.models import Contract, CatalogItem as _CatalogItem, LineItem as _LineItem


@pytest.mark.django_db
def test_contract_create_designates_managed_and_numbers(client, client_obj, admin_user):
    client.force_login(admin_user)
    resp = client.post(
        reverse('core:contract_create', args=[client_obj.pk]),
        {'title': 'Managed Services', 'status': 'active', 'billing_cadence': 'monthly',
         'billing_day': 1},
    )
    assert resp.status_code == 302
    contract = Contract.objects.get(client=client_obj)
    assert contract.contract_number.startswith('AGR-')
    assert contract.status == 'active'
    assert client_obj.contracts.count() == 1


@pytest.mark.django_db
def test_contract_numbers_are_sequential(client_obj):
    a = Contract.objects.create(client=client_obj, title='A')
    b = Contract.objects.create(client=client_obj, title='B')
    assert a.contract_number == 'AGR-00001'
    assert b.contract_number == 'AGR-00002'


@pytest.mark.django_db
def test_contract_recurring_line_and_total(client, client_obj, admin_user):
    contract = Contract.objects.create(client=client_obj, title='MSP')
    client.force_login(admin_user)
    resp = client.post(
        reverse('core:contract_line_custom', args=[contract.pk]),
        {'custom_label': 'Monitoring', 'kind': 'labor', 'quantity': '3', 'unit_price': '10'},
    )
    assert resp.status_code == 200
    assert contract.line_items.count() == 1
    from decimal import Decimal
    assert contract.line_items_total == Decimal('30')


@pytest.mark.django_db
def test_contract_covers_asset_and_delete_unlinks(client, client_obj, admin_user):
    contract = Contract.objects.create(client=client_obj, title='MSP')
    asset = Asset.objects.create(client=client_obj, name='PC1', contract=contract)
    assert list(contract.assets.all()) == [asset]

    # Deleting the contract nulls the asset's coverage link (SET_NULL), asset survives.
    client.force_login(admin_user)
    resp = client.post(reverse('core:contract_delete', args=[contract.pk]))
    assert resp.status_code == 302
    asset.refresh_from_db()
    assert asset.contract_id is None
    assert not Contract.objects.filter(pk=contract.pk).exists()


@pytest.mark.django_db
def test_contract_delete_requires_admin(client, client_obj, admin_user, tech_user):
    contract = Contract.objects.create(client=client_obj, title='MSP')
    client.force_login(tech_user)
    resp = client.post(reverse('core:contract_delete', args=[contract.pk]))
    assert resp.status_code == 403
    assert Contract.objects.filter(pk=contract.pk).exists()


@pytest.mark.django_db
def test_contract_detail_and_list_render(client, client_obj, admin_user):
    contract = Contract.objects.create(client=client_obj, title='Managed Services')
    client.force_login(admin_user)
    detail = client.get(reverse('core:contract_detail', args=[contract.pk]))
    assert detail.status_code == 200
    assert contract.contract_number.encode() in detail.content
    listing = client.get(reverse('core:contract_list'))
    assert listing.status_code == 200
    assert b'Managed Services' in listing.content


# ── Contract billing run (Slice 4) ──────────────────────────────────────

from datetime import date as _date


@pytest.mark.django_db
def test_contract_prepare_clones_lines_and_is_idempotent(client, client_obj, admin_user):
    contract = Contract.objects.create(client=client_obj, title='MSP', status='active')
    contract.line_items.create(kind='labor', description='Monitoring', quantity=2, unit_price=15)
    client.force_login(admin_user)
    r1 = client.post(reverse('core:contract_billing_prepare', args=[contract.pk]))
    assert r1.status_code == 302
    from core.models import Sale
    sales = Sale.objects.filter(contract=contract)
    assert sales.count() == 1
    sale = sales.first()
    assert sale.is_recurring and sale.client_id == client_obj.pk
    assert sale.line_items.count() == 1
    from decimal import Decimal
    assert sale.line_items_total == Decimal('30')
    # Second prepare in the same period is idempotent — no duplicate draft.
    client.post(reverse('core:contract_billing_prepare', args=[contract.pk]))
    assert Sale.objects.filter(contract=contract).count() == 1


@pytest.mark.django_db
def test_contract_billing_lane_isolated_from_lane_c(client_obj, admin_user):
    """A contract-generated recurring sale must not be picked up by the Lane C
    (Client-level) worklist, and vice versa."""
    from core.views import _recurring_sale_this_month, _contract_sale_for_period
    contract = Contract.objects.create(client=client_obj, title='MSP', status='active')
    from core.views import _prepare_contract_sale
    csale, _ = _prepare_contract_sale(contract, admin_user)
    # Lane C sees no sale for this client (the contract sale is excluded).
    assert _recurring_sale_this_month(client_obj) is None
    # Contract lane finds its own sale.
    assert _contract_sale_for_period(contract) == csale


@pytest.mark.django_db
def test_contract_cadence_due_logic():
    from core.models import Client as C, Contract as Ct
    c = C.objects.create(name='Cadence Co')
    # Monthly: due every month once billing_day passes.
    m = Ct.objects.create(client=c, title='M', status='active', billing_cadence='monthly', billing_day=1)
    assert m.is_billing_due(_date(2026, 3, 15)) is True
    # Annual anchored to start month (July): due in July, not March.
    a = Ct.objects.create(client=c, title='A', status='active', billing_cadence='annual',
                          billing_day=1, start_date=_date(2026, 7, 1))
    assert a.is_billing_month(_date(2026, 7, 10)) is True
    assert a.is_billing_month(_date(2026, 3, 10)) is False
    assert a.is_billing_due(_date(2026, 7, 10)) is True
    assert a.is_billing_due(_date(2026, 3, 10)) is False
    # Quarterly anchored to July: billing months Jan/Apr/Jul/Oct.
    q = Ct.objects.create(client=c, title='Q', status='active', billing_cadence='quarterly',
                          billing_day=1, start_date=_date(2026, 7, 1))
    assert q.is_billing_month(_date(2026, 10, 5)) is True
    assert q.is_billing_month(_date(2026, 8, 5)) is False
    # Draft/expired contracts are never due.
    d = Ct.objects.create(client=c, title='D', status='draft', billing_cadence='monthly', billing_day=1)
    assert d.is_billing_due(_date(2026, 3, 15)) is False


@pytest.mark.django_db
def test_contract_period_key_by_cadence():
    from core.models import Client as C, Contract as Ct
    c = C.objects.create(name='PK Co')
    m = Ct.objects.create(client=c, title='M', billing_cadence='monthly')
    q = Ct.objects.create(client=c, title='Q', billing_cadence='quarterly')
    a = Ct.objects.create(client=c, title='A', billing_cadence='annual')
    assert m.period_key(_date(2026, 7, 9)) == '2026-07'
    assert q.period_key(_date(2026, 7, 9)) == '2026-Q3'
    assert a.period_key(_date(2026, 7, 9)) == '2026'


@pytest.mark.django_db
def test_contract_batch_prepare_only_due(client, client_obj, admin_user):
    from core.models import Sale
    due = Contract.objects.create(client=client_obj, title='Due', status='active',
                                  billing_cadence='monthly', billing_day=1)
    due.line_items.create(kind='labor', description='X', quantity=1, unit_price=10)
    # An annual contract anchored to a different month is not due most months.
    not_due = Contract.objects.create(client=client_obj, title='Annual', status='active',
                                      billing_cadence='annual', billing_day=1,
                                      start_date=_date(2000, 1, 1))
    client.force_login(admin_user)
    client.post(reverse('core:contract_billing_prepare_all'))
    prepared_contracts = set(Sale.objects.filter(contract__isnull=False).values_list('contract_id', flat=True))
    assert due.pk in prepared_contracts
    # not_due only prepares if today is January; assert it's absent unless January.
    import datetime as _dt
    if _dt.date.today().month != 1:
        assert not_due.pk not in prepared_contracts


@pytest.mark.django_db
def test_contract_billing_list_renders(client, client_obj, admin_user):
    Contract.objects.create(client=client_obj, title='Managed', status='active')
    client.force_login(admin_user)
    r = client.get(reverse('core:contract_billing_list'))
    assert r.status_code == 200
    assert b'Contract Billing' in r.content


# ── Device → Asset promotion (Slice 5) ──────────────────────────────────


@pytest.mark.django_db
def test_device_promote_to_asset_moves_history_and_retires(client, client_obj, admin_user):
    device = Device.objects.create(client=client_obj, name='Reception PC',
                                   serial_number='SN123', manufacturer='Dell', model='7090')
    wo = WorkOrder.objects.create(client=client_obj, device=device)
    client.force_login(admin_user)
    resp = client.post(reverse('core:device_promote_asset', args=[device.pk]))
    assert resp.status_code == 302
    device.refresh_from_db()
    asset = device.promoted_to_asset
    assert asset is not None
    assert asset.client_id == client_obj.pk
    assert asset.name == 'Reception PC'
    assert asset.identifier == 'SN123'
    assert asset.manufacturer == 'Dell'
    # History followed the machine onto the asset.
    wo.refresh_from_db()
    assert wo.asset_id == asset.pk
    assert list(asset.work_orders.all()) == [wo]
    # Device retired, not deleted.
    assert device.is_active is False
    assert Device.objects.filter(pk=device.pk).exists()


@pytest.mark.django_db
def test_device_promote_is_idempotent(client_obj):
    device = Device.objects.create(client=client_obj, name='PC')
    a1 = device.promote_to_asset()
    a2 = device.promote_to_asset()
    assert a1.pk == a2.pk
    from core.models import Asset
    assert Asset.objects.filter(client=client_obj).count() == 1


@pytest.mark.django_db
def test_walkin_device_cannot_be_promoted(client, admin_user):
    device = Device.objects.create(client=None, name='Walk-in laptop')
    client.force_login(admin_user)
    resp = client.post(reverse('core:device_promote_asset', args=[device.pk]))
    assert resp.status_code == 302  # redirected with an error message
    device.refresh_from_db()
    assert device.promoted_to_asset_id is None
    with pytest.raises(ValueError):
        device.promote_to_asset()


@pytest.mark.django_db
def test_asset_detail_shows_recent_work(client, client_obj, admin_user):
    from core.models import Asset
    asset = Asset.objects.create(client=client_obj, name='DC01')
    WorkOrder.objects.create(client=client_obj, asset=asset)
    client.force_login(admin_user)
    resp = client.get(reverse('core:asset_detail', args=[asset.pk]))
    assert resp.status_code == 200
    assert b'DC01' in resp.content
    assert b'Recent work' in resp.content


# ── Contract billing hardening (P2-1/2/3) ───────────────────────────────

from django.db import IntegrityError as _IntegrityError


@pytest.mark.django_db
def test_contract_past_end_date_not_due_even_if_active(client_obj):
    from datetime import date
    c = Contract.objects.create(
        client=client_obj, title='Ending', status='active',
        billing_cadence='monthly', billing_day=1,
        end_date=date(2026, 6, 30),
    )
    # On/before the end date it still bills; after, it does not — even though the
    # status was never manually flipped to expired.
    assert c.is_billing_due(date(2026, 6, 15)) is True
    assert c.is_billing_due(date(2026, 6, 30)) is True
    assert c.is_billing_due(date(2026, 7, 1)) is False


@pytest.mark.django_db
def test_duplicate_contract_period_sale_blocked_by_db(client_obj):
    from core.models import Sale
    contract = Contract.objects.create(client=client_obj, title='MSP', status='active')
    Sale.objects.create(client=client_obj, is_recurring=True, contract=contract, billing_period='2026-07')
    with pytest.raises(_IntegrityError):
        Sale.objects.create(client=client_obj, is_recurring=True, contract=contract, billing_period='2026-07')


@pytest.mark.django_db
def test_non_contract_sales_not_constrained(client_obj):
    from core.models import Sale
    # Two counter sales (no contract, blank billing_period) must coexist fine.
    Sale.objects.create(client=client_obj)
    Sale.objects.create(client=client_obj)
    assert Sale.objects.filter(contract__isnull=True).count() == 2


@pytest.mark.django_db
def test_contract_views_require_sales_gate(client, client_obj, tech_user):
    # tech_user has no role → not a sales viewer → blocked from contract surfaces.
    client.force_login(tech_user)
    assert client.post(reverse('core:contract_create', args=[client_obj.pk]),
                       {'title': 'X', 'status': 'active', 'billing_cadence': 'monthly',
                        'billing_day': 1}).status_code == 403
    contract = Contract.objects.create(client=client_obj, title='MSP')
    assert client.post(reverse('core:contract_line_custom', args=[contract.pk]),
                       {'custom_label': 'Y', 'kind': 'labor'}).status_code == 403


@pytest.mark.django_db
def test_line_edit_gate_is_host_aware(client, client_obj, tech_user):
    """A plain tech may edit Work Order lines (login-only) but NOT Contract lines
    (billing-gated), through the shared line-edit endpoint."""
    from core.models import LineItem
    # WorkOrder line — editable by a tech.
    wo = WorkOrder.objects.create(client=client_obj)
    wo_line = wo.line_items.create(kind='labor', description='Fix', quantity=1, unit_price=50)
    # Contract line — gated.
    contract = Contract.objects.create(client=client_obj, title='MSP')
    c_line = contract.line_items.create(kind='labor', description='MSP', quantity=1, unit_price=100)

    client.force_login(tech_user)
    ok = client.post(reverse('core:work_performed_update', args=[wo_line.pk]),
                     {'custom_label': 'Fix', 'quantity': '1', 'unit_price': '55'})
    assert ok.status_code == 200
    blocked = client.post(reverse('core:work_performed_update', args=[c_line.pk]),
                          {'custom_label': 'MSP', 'quantity': '1', 'unit_price': '1'})
    assert blocked.status_code == 403
    # The contract line price was NOT changed by the blocked request.
    c_line.refresh_from_db()
    assert c_line.unit_price == 100


# ── dump_schema: regenerates the schema doc from the live models ──

@pytest.mark.django_db
def test_dump_schema_reflects_live_models():
    """dump_schema emits a Markdown schema doc covering every core model, with
    the current migration number, encryption markers, and per-model field tables."""
    from io import StringIO
    from django.apps import apps
    from django.core.management import call_command

    buf = StringIO()
    call_command('dump_schema', stdout=buf)
    out = buf.getvalue()

    core_models = list(apps.get_app_config('core').get_models())

    # Header reports the true model count and a real migration number (not the ????
    # fallback), so a stale count/migration can't silently ship.
    assert f"**{len(core_models)} models.**" in out
    import re
    m = re.search(r"\*\*Migrations\*\*: through (\d{4})", out)
    assert m and m.group(1) != "0000"

    # Every core model has its own section and db_table line.
    for model in core_models:
        assert f"## {model.__name__}\n" in out
        assert f"`db_table = {model._meta.db_table}`" in out

    # Spot-check content that must survive regeneration: a recently-added model,
    # an encrypted field marker, and a choices row.
    assert "## Contract" in out
    assert "\U0001f512 encrypted" in out
    assert "choices:" in out

    # Output is a pure function of the models + migrations — regenerating with no
    # schema change is a true no-op (the "Last Updated" date comes from the latest
    # migration's header, not today(), so it can't churn the diff day-to-day).
    buf2 = StringIO()
    call_command('dump_schema', stdout=buf2)
    assert buf2.getvalue() == out
