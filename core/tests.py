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
def test_duplicate_message_id_is_skipped():
    """The orphan-multiplication guard: a Message-ID already recorded in
    InboundEmailLog (non-error) is never processed again. This is what stops a
    'leave on server' poll from re-creating the same ticket on every cycle."""
    from core.management.commands.fetch_inbound_email import _process_message
    from core.models import InboundEmailLog
    site = SiteSettings.get()
    InboundEmailLog.objects.create(
        message_id='<fresh-001@davis.example>', status='new_ticket',
        from_email='wayne@davis.example', subject='x',
    )
    before = Ticket.objects.count()

    status, detail, ticket = _process_message(_raw_new_email(), site, verbosity=0)

    assert status == 'duplicate', f'Expected duplicate, got {status}: {detail}'
    assert ticket is None
    assert Ticket.objects.count() == before, 'A duplicate must not create a ticket.'


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
def test_business_domain_groups_senders_free_email_is_per_person():
    """Two senders at the same business domain land under one client; a consumer
    (free-email) sender gets their own client record."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()

    _process_message(_raw_new_email(
        from_email='alice@acmecorp.example', from_name='Alice A',
        message_id='<biz-1@acmecorp.example>'), site, verbosity=0)
    _process_message(_raw_new_email(
        from_email='bob@acmecorp.example', from_name='Bob B',
        message_id='<biz-2@acmecorp.example>'), site, verbosity=0)
    assert Client.objects.filter(name='acmecorp.example').count() == 1
    biz = Client.objects.get(name='acmecorp.example')
    assert biz.contacts.count() == 2, 'Same business domain groups under one client.'

    _process_message(_raw_new_email(
        from_email='someone@gmail.com', from_name='Jane Doe',
        message_id='<free-1@gmail.com>'), site, verbosity=0)
    assert not Client.objects.filter(name='gmail.com').exists(), \
        'A free-email sender must NOT be grouped under the provider domain.'
    assert Client.objects.filter(name='Jane Doe').exists()


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

def _raw_t2_email(real_name='Mike McCall', real_email='redacted@example.com',
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
    body = '--- Forwarded message ---\nFrom: "Mike McCall" <redacted@example.com>\nDate: x\n'
    assert _extract_forwarded_sender(body) == ('Mike McCall', 'redacted@example.com')
    assert _extract_forwarded_sender('no headers here') == (None, None)
    assert _extract_forwarded_sender('') == (None, None)


@pytest.mark.django_db
def test_t2_email_maps_to_existing_contact_not_relay(client_obj):
    """A button ticket whose forwarded sender is a known contact files under that
    contact's client — never under the tier2tickets relay."""
    from core.management.commands.fetch_inbound_email import _process_message
    contact = Contact.objects.create(
        client=client_obj, first_name='Mike', last_name='McCall',
        email='redacted@example.com', is_primary=True,
    )
    site = SiteSettings.get()

    status, _, ticket = _process_message(_raw_t2_email(), site, verbosity=0)

    assert status == 'new_ticket'
    assert ticket.contact_id == contact.id
    assert ticket.client_id == client_obj.id
    assert not Client.objects.filter(name__icontains='tier2tickets').exists(), \
        'A button ticket must never create a tier2tickets relay client.'


@pytest.mark.django_db
def test_t2_email_unknown_sender_uses_normal_fallback():
    """An unknown button-ticket sender falls through to MB's normal new-sender
    handling on the REAL address (free-email -> per-person client), not the relay."""
    from core.management.commands.fetch_inbound_email import _process_message
    site = SiteSettings.get()

    status, _, ticket = _process_message(_raw_t2_email(), site, verbosity=0)

    assert status == 'new_ticket'
    assert ticket.contact.email == 'redacted@example.com'
    assert not Client.objects.filter(name__icontains='tier2tickets').exists()
    # gmail is a free-email domain -> the sender gets their own client
    assert Client.objects.filter(name='Mike McCall').exists()


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
    # A custom/quick-labor Work Performed entry has labor_item=None; the print
    # report grouped by labor_item.category and 500'd on None. Regression guard.
    from core.models import WorkPerformed
    wo = WorkOrder.objects.create(client=client_obj)
    WorkPerformed.objects.create(
        work_order=wo, labor_item=None, custom_label='Reseated RAM', notes='Was loose',
    )
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
    monkeypatch.setenv('SSH_CONNECTION', 'REDACTED-IP 51234 REDACTED-IP 22')

    call_command('reset_mfa', 'soleadmin', '--note', 'lost authenticator')

    assert not list(devices_for_user(target)), 'CLI reset must clear devices.'
    log = MFAResetLog.objects.get(target=target)
    assert log.actor is None            # no authenticated web user on the CLI path
    assert log.source == 'cli'
    # Highest-risk path stays traceable: stamp who/where, not an anonymous null.
    assert 'scs-tech' in log.note
    assert 'REDACTED-IP' in log.note
    assert 'lost authenticator' in log.note


@pytest.mark.django_db
def test_cli_reset_unknown_user_errors():
    from django.core.management import call_command
    from django.core.management.base import CommandError
    with pytest.raises(CommandError):
        call_command('reset_mfa', 'nobody-here')
