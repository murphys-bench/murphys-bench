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
def test_reply_to_closed_ticket_reopens_and_threads(client_obj):
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()
    ticket = Ticket.objects.create(
        client=client_obj, subject='S', description='D',
        ticket_number='TKT-20260610-0002', status='closed',
    )
    before = Ticket.objects.count()

    status, detail, _ = _process_message(
        _raw_reply_email('TKT-20260610-0002'), site, verbosity=0)

    assert status == 'reply', f'Expected reply, got {status}: {detail}'
    assert Ticket.objects.count() == before
    ticket.refresh_from_db()
    assert ticket.replies.count() == 1
    assert ticket.status == 'open', 'A reply to a closed ticket should reopen it.'
    assert ticket.needs_response is True


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
def test_dashboard_shows_triage_card_for_admin(client, admin_user):
    bucket = Client.get_unsorted()
    Ticket.objects.create(client=bucket, subject='unsorted', description='d',
                          ticket_number='TKT-D-1', status='new')
    client.force_login(admin_user)
    resp = client.get(reverse('core:dashboard'))
    assert resp.status_code == 200
    assert 'Unsorted — needs triage' in resp.content.decode()


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

def _raw_t2_email(real_name='Mike McCall', real_email='mccall.mike@gmail.com',
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
    body = '--- Forwarded message ---\nFrom: "Mike McCall" <mccall.mike@gmail.com>\nDate: x\n'
    assert _extract_forwarded_sender(body) == ('Mike McCall', 'mccall.mike@gmail.com')
    assert _extract_forwarded_sender('no headers here') == (None, None)
    assert _extract_forwarded_sender('') == (None, None)


@pytest.mark.django_db
def test_t2_email_maps_to_existing_contact_not_relay(client_obj):
    """A button ticket whose forwarded sender is a known contact files under that
    contact's client — never under the tier2tickets relay."""
    from core.management.commands.fetch_inbound_email import _process_message
    contact = Contact.objects.create(
        client=client_obj, first_name='Mike', last_name='McCall',
        email='mccall.mike@gmail.com', is_primary=True,
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
    assert ticket.contact.email == 'mccall.mike@gmail.com'
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
    # A custom labor line has no source_labor_item; the print report groups by
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

    monkeypatch.setattr('getpass.getuser', lambda: 'scs-tech')
    monkeypatch.setenv('SSH_CONNECTION', '10.58.58.5 51234 10.58.58.82 22')

    call_command('reset_mfa', 'soleadmin', '--note', 'lost authenticator')

    assert not list(devices_for_user(target)), 'CLI reset must clear devices.'
    log = MFAResetLog.objects.get(target=target)
    assert log.actor is None            # no authenticated web user on the CLI path
    assert log.source == 'cli'
    # Highest-risk path stays traceable: stamp who/where, not an anonymous null.
    assert 'scs-tech' in log.note
    assert '10.58.58.5' in log.note
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
    from core.models import QuickLaborItem, LineItem
    wo = WorkOrder.objects.create(client=client_obj)
    item = QuickLaborItem.objects.create(label='Virus Removal', category='Software',
                                         default_price=Decimal('120.00'))
    client.force_login(admin_user)
    resp = client.post(reverse('core:work_performed_log', args=[wo.pk, item.pk]))
    assert resp.status_code == 200
    li = LineItem.objects.get(object_id=wo.pk, description='Virus Removal')
    assert li.kind == 'labor'
    assert li.unit_price == Decimal('120.00')
    assert li.source_labor_item_id == item.pk


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
def test_send_to_in_duplicate_guard(client, client_obj, admin_user, monkeypatch):
    from core import invoice_ninja
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, invoice_ninja_id='123', invoice_ninja_ref='INV-1')
    calls = []
    monkeypatch.setattr(invoice_ninja, 'push_work_order', lambda w: calls.append(w))
    client.force_login(admin_user)
    # Already pushed, no confirm → no second push
    client.post(reverse('core:work_order_send_in', args=[wo.pk]))
    assert calls == []
    # Confirmed re-send → pushes
    client.post(reverse('core:work_order_send_in', args=[wo.pk]), {'confirm_resend': '1'})
    assert len(calls) == 1


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
    """The reported bug: 'Send to Invoice Ninja' gave no visible feedback.

    With IN disabled (the default) the view adds an error message and redirects
    to the WO detail page. That page must render the message (proving base.html
    renders the messages framework), not swallow it until logout.
    """
    wo = WorkOrder.objects.create(client=client_obj)
    client.force_login(admin_user)

    resp = client.post(reverse('core:work_order_send_in', args=[wo.pk]), follow=True)

    assert resp.status_code == 200
    assert b'Invoice Ninja is not enabled' in resp.content, \
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

from core.models import Estimate, Prospect as _Prospect, QuickLaborItem, EstimateOption


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
    item = QuickLaborItem.objects.create(label='Virus Removal', category='Software', default_price='75.00')
    est = Estimate.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:estimate_labor_log', args=[est.pk, item.pk]))
    assert resp.status_code == 200
    li = est.line_items.get()
    assert li.description == 'Virus Removal'
    assert li.source_labor_item_id == item.pk


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
    item = QuickLaborItem.objects.create(label='Data Transfer', category='Software', default_price='45.00')
    sale = Sale.objects.create(client=client_obj)
    client.force_login(admin_user)
    resp = client.post(reverse('core:sale_labor_log', args=[sale.pk, item.pk]))
    assert resp.status_code == 200
    li = sale.line_items.get()
    assert li.description == 'Data Transfer'
    assert li.source_labor_item_id == item.pk


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

