"""Spine tests for Murphy's Bench.

This is the first of the project's tests, written alongside the stabilization
bug-fix pass. Each test locks in behavior we rely on in daily production use so
a future change can't silently regress it. Targets the spine, not coverage %.

Run with:  venv/bin/python -m pytest
"""
import json
import logging
import re

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


@pytest.mark.django_db
def test_reset_leaves_no_audit_entries_naming_deleted_records(client_obj, admin_user):
    """The wipe must not leave an audit trail of itself naming real customers.

    auditlog records this command's OWN deletions: Ticket, TicketReply, WorkOrder
    and WorkOrderNote are registered with it, and cascade deletes fire it too. The
    audit log was being wiped partway through the sequence, so every record
    destroyed after that point wrote a fresh entry, and the reset finished with
    rows carrying live client names and ticket subjects on a box it had just
    reported clean.

    Found by running the real wipe on mb-test. A dry run cannot surface it,
    because nothing is deleted and so nothing is logged — which is why the
    ordering is asserted here as well as in the registry.
    """
    from auditlog.models import LogEntry
    from django.core.management import call_command

    ticket = Ticket.objects.create(client=client_obj, subject='Real customer subject',
                                   description='D')
    WorkOrder.objects.create(client=client_obj, ticket=ticket)
    # Sanity: these models really are audited, or the test proves nothing.
    assert LogEntry.objects.exists(), 'no audit entries created — is auditlog still wired?'

    call_command('reset_operational_data', confirm='DELETE ALL OPERATIONAL DATA')

    survivors = list(LogEntry.objects.values_list('object_repr', flat=True))
    assert not survivors, f'reset left audit entries naming deleted records: {survivors}'


@pytest.mark.django_db
def test_reset_keeps_attachment_files_when_the_transaction_rolls_back(client_obj, admin_user):
    """A rolled-back reset must not have already destroyed the files on disk.

    The command deleted attachment files from storage INSIDE its transaction, so
    a failure further down rolled the rows back and left restored Attachment rows
    pointing at files that no longer existed. The rollback advertised in the
    command's own docstring was therefore only true of the database. Files are
    now unlinked after the commit.
    """
    from unittest.mock import patch
    from django.core.files.base import ContentFile
    from django.core.management import call_command
    from core import operational_data
    from core.models import Attachment, Ticket

    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    att = Attachment.objects.create(
        content_object=ticket,
        file=ContentFile(b'real customer file', name='invoice.pdf'),
        uploaded_by=admin_user,
    )
    storage, name = att.file.storage, att.file.name
    assert storage.exists(name)

    # Fail late: let the real plan run, then blow up before the commit.
    real_plan = operational_data.deletion_plan

    class Exploding:
        class objects:
            @staticmethod
            def all():
                raise RuntimeError('disk full partway through the wipe')

    def failing_plan():
        return list(real_plan()) + [('Exploding', Exploding)]

    with patch.object(operational_data, 'deletion_plan', failing_plan):
        with pytest.raises(RuntimeError):
            call_command('reset_operational_data', confirm='DELETE ALL OPERATIONAL DATA')

    # Rows rolled back...
    assert Attachment.objects.filter(pk=att.pk).exists()
    # ...and the file they point at is still there, which is the actual fix.
    assert storage.exists(name), 'attachment file was destroyed by a rolled-back reset'


@pytest.mark.django_db
def test_reset_deletes_attachment_files_on_success(client_obj, admin_user):
    """The happy path must still actually remove the files, not just the rows."""
    from django.core.files.base import ContentFile
    from django.core.management import call_command
    from core.models import Attachment, Ticket

    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    att = Attachment.objects.create(
        content_object=ticket,
        file=ContentFile(b'demo data', name='photo.jpg'),
        uploaded_by=admin_user,
    )
    storage, name = att.file.storage, att.file.name
    assert storage.exists(name)

    call_command('reset_operational_data', confirm='DELETE ALL OPERATIONAL DATA')

    assert Attachment.objects.count() == 0
    assert not storage.exists(name), 'attachment file survived a successful reset'


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
    assert b'title="Notifications"' in client.get('/').content               # header bell
    assert client.get(reverse('core:notifications')).status_code == 200      # center page
    assert b'Message Ticket Tech' in client.get(
        reverse('core:work_order_detail', args=[wo.pk])).content             # WO affordance
    assert b'Message Bench Tech' in client.get(
        reverse('core:ticket_detail', args=[ticket.pk])).content            # ticket affordance


@pytest.mark.django_db
def test_notice_clears_itself_when_its_ticket_settles(client, client_obj):
    """The reported bug: a System Alert became a ticket, the ticket was closed,
    the notice stayed in the bell forever. Filtering on the ticket's own status
    fixes it retroactively — and reverses if the ticket is reopened."""
    from core.models import Notification
    u = User.objects.create_user(username='uclear', password='x')
    ticket = Ticket.objects.create(client=client_obj, subject='500 error',
                                   description='trace', assigned_to=u)
    n = Notification.objects.create(recipient=u, text='500 error',
                                    kind='system_alert', ticket=ticket)
    client.force_login(u)
    assert b'>1<' in client.get(reverse('core:notification_count')).content

    ticket.status = 'closed'
    ticket.save(update_fields=['status'])
    assert u.notifications.live().count() == 0
    assert b'>1<' not in client.get(reverse('core:notification_count')).content
    assert n.text.encode() not in client.get(reverse('core:notifications')).content
    # Nothing was destroyed, and reopening brings the notice back on its own.
    n.refresh_from_db()
    assert n.dismissed_at is None
    ticket.status = 'open'
    ticket.save(update_fields=['status'])
    assert u.notifications.live().count() == 1


@pytest.mark.django_db
def test_notice_clears_when_its_work_order_completes(client, client_obj):
    from core.models import Notification
    u = User.objects.create_user(username='uwo', password='x')
    wo = WorkOrder.objects.create(client=client_obj, assigned_to=u)
    Notification.objects.create(recipient=u, text='msg', kind='tech_message',
                                work_order=wo)
    assert u.notifications.live().count() == 1
    wo.status = 'completed'
    wo.save(update_fields=['status'])
    assert u.notifications.live().count() == 0


@pytest.mark.django_db
def test_dismiss_hides_notice_but_keeps_the_who_saw_it_record(client, client_obj):
    """Dismiss is deliberately NOT a delete — recipient + read_at are the only
    record in MB of which tech saw an alert and when (the audit log tracks
    changes, not reads), which is accountability data in a multi-tech shop."""
    from core.models import Notification
    u = User.objects.create_user(username='udis', password='x')
    n = Notification.objects.create(recipient=u, text='alert', kind='system_alert')
    client.force_login(u)
    resp = client.post(reverse('core:notification_dismiss', args=[n.pk]))
    assert resp.status_code == 302

    n.refresh_from_db()
    assert n.dismissed_at is not None
    assert n.is_read and n.read_at is not None      # dismissing is acknowledging
    assert Notification.objects.filter(pk=n.pk).exists()   # row survives
    assert u.notifications.live().count() == 0
    assert b'>1<' not in client.get(reverse('core:notification_count')).content

    # Another user can't dismiss someone else's notice.
    other = User.objects.create_user(username='udis2', password='x')
    n2 = Notification.objects.create(recipient=u, text='b', kind='system_alert')
    client.force_login(other)
    assert client.post(
        reverse('core:notification_dismiss', args=[n2.pk])).status_code == 404


@pytest.mark.django_db
def test_dismiss_all_clears_live_notices_only(client, client_obj):
    from core.models import Notification
    u = User.objects.create_user(username='udall', password='x')
    other = User.objects.create_user(username='udall2', password='x')
    Notification.objects.create(recipient=u, text='a', kind='system_alert')
    Notification.objects.create(recipient=u, text='b', kind='system_alert')
    theirs = Notification.objects.create(recipient=other, text='c', kind='system_alert')

    client.force_login(u)
    client.post(reverse('core:notification_dismiss_all'))
    assert u.notifications.live().count() == 0
    assert Notification.objects.filter(recipient=u, dismissed_at__isnull=False).count() == 2
    theirs.refresh_from_db()
    assert theirs.dismissed_at is None              # never touches another user's


@pytest.mark.django_db
def test_bell_includes_tickets_awaiting_reply_scoped_to_the_tech(client, client_obj):
    """A client reply is Ticket.needs_response, not a Notification row — the bell
    reads it live so it stays self-clearing and survives reassignment."""
    tech = User.objects.create_user(username='ubell', password='x', is_staff=False)
    othertech = User.objects.create_user(username='ubell2', password='x', is_staff=False)
    mine = Ticket.objects.create(client=client_obj, subject='Mine', description='D',
                                 assigned_to=tech, needs_response=True)
    Ticket.objects.create(client=client_obj, subject='Theirs', description='D',
                          assigned_to=othertech, needs_response=True)

    client.force_login(tech)
    assert b'>1<' in client.get(reverse('core:notification_count')).content
    page = client.get(reverse('core:notifications')).content
    assert mine.ticket_number.encode() in page
    assert b'Theirs' not in page                    # not this tech's to answer

    # Replying clears the flag on the ticket, so the bell empties on its own —
    # there is no notification row to dismiss.
    mine.needs_response = False
    mine.save(update_fields=['needs_response'])
    assert b'>1<' not in client.get(reverse('core:notification_count')).content


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
    client.post(reverse('core:client_delete', args=[bucket.pk]),
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
    client.post(reverse('core:user_delete', args=[su2.pk]))    # deleting self anyway blocked
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
def test_incomplete_install_replaces_the_success_tick_in_the_ui(settings, tmp_path, client):
    """A succeeded update on an incomplete install must NOT render a green tick.

    v0.11.0 made update.sh report an install it could not finish, but only on
    stderr. The in-app Update button captures that into a log it renders
    COLLAPSED beneath "✓ Last update succeeded", so the path nearly everyone
    uses showed a tick and hid the warning. The exit code was honest and the
    product was not.

    update.sh now writes logs/update-incomplete, and the card reports the
    CONSEQUENCE instead of the exit code for as long as that file exists.
    """
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    admin = User.objects.create_superuser(username='boss', password='x')
    client.force_login(admin)

    update_ops.status_path().write_text(json.dumps({
        'state': 'succeeded', 'exit_code': 0, 'finished_at': '2026-08-03T00:00:00Z',
    }))
    update_ops.incomplete_path().write_text(
        'These system services are not installed:\n'
        '  murphys-bench-restore.path\n'
        'FIX: scripts/install_units.sh\n'
    )

    body = client.get(reverse('core:update_status')).content.decode()
    assert 'This install is incomplete' in body
    assert 'murphys-bench-restore.path' in body, 'the card must name what is missing'
    assert 'scripts/install_units.sh' in body, 'the card must give the fix'
    assert 'Last update succeeded' not in body, (
        'a green success banner alongside an incomplete install is the exact '
        'defect this exists to remove'
    )

    # Cleared the moment the install is whole again: a warning that outlives the
    # problem is how people learn to ignore warnings.
    update_ops.incomplete_path().unlink()
    body = client.get(reverse('core:update_status')).content.decode()
    assert 'This install is incomplete' not in body
    assert 'Last update succeeded' in body


@pytest.mark.django_db
def test_card_discards_a_marker_that_names_no_real_unit(settings, tmp_path, client):
    """A garbled marker must be discarded, not rendered at the user.

    Reproduced on a real 24.04 box 2026-08-04: a box on v0.11.1 updating forward
    to a release carrying deploy/manifest.sh runs its OWN update.sh, whose awk
    parser finds no literal UNITS block to stop at, swallows install_units.sh and
    writes 269 lines of shell fragments here. The update itself succeeded — exit
    0, service healthy — and the card told the operator their install was broken
    and listed nonsense.

    The sample below is real captured output, trimmed. A warning nobody can act
    on teaches people to ignore the one that matters.
    """
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    admin = User.objects.create_superuser(username='boss2', password='x')
    client.force_login(admin)

    update_ops.status_path().write_text(json.dumps({
        'state': 'succeeded', 'exit_code': 0, 'finished_at': '2026-08-04T00:00:00Z',
    }))
    update_ops.incomplete_path().write_text(
        'These system services are not installed:\n'
        '  if\n  $WITH_DISK_CHECK\n  =\n  1\n  ];\n  then\n  fi\n'
        '  log\n  rendering\n  ${\n  for\n  u\n  in\n  do\n'
        '  src=$APP/deploy/$u\n  sed\n  -e\n  s|__APP__|$APP|g\n'
    )

    assert update_ops.read_incomplete() == '', 'garbled marker was not discarded'
    body = client.get(reverse('core:update_status')).content.decode()
    assert 'This install is incomplete' not in body
    assert '$WITH_DISK_CHECK' not in body, 'shell fragments reached the user'
    assert 'Last update succeeded' in body, (
        'the update genuinely succeeded, so with the bogus marker discarded the '
        'card should report success'
    )


@pytest.mark.django_db
def test_card_still_shows_a_warning_that_names_no_units_but_is_not_about_units(
    settings, tmp_path,
):
    """Don't over-suppress: update.sh also warns about static permissions.

    That text names no systemd unit at all, and it is a real condition a user
    must act on. Only markers that CLAIM missing services while naming none are
    untrustworthy.
    """
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()

    # update.sh's REAL wording (scripts/update.sh:303-305). An earlier version of
    # this test invented the text, so it proved nothing about the actual product.
    update_ops.incomplete_path().write_text(
        "The web server cannot read this install's stylesheets (HTTP 403).\n"
        'Pages render as unstyled HTML with no logo.\n'
        '\nFIX: cd /opt/murphys-bench && scripts/install.sh\n'
    )
    kept = update_ops.read_incomplete()
    assert 'stylesheets (HTTP 403)' in kept
    assert 'unstyled HTML' in kept
    assert 'scripts/install.sh' in kept, 'the fix must survive with the problem'


@pytest.mark.django_db
def test_mixed_marker_keeps_the_real_warning_and_drops_the_bogus_block(
    settings, tmp_path, client,
):
    """A garbled services block must not take a real warning down with it.

    update.sh writes BOTH problems into one file (scripts/update.sh:293-311), and
    the FIX line differs depending on which one exists. An all-or-nothing discard
    therefore hid a genuine "your site renders unstyled" warning whenever the
    services block happened to be garbage — which is exactly the case on a
    v0.11.1 box updating forward. Silence about a real problem is a worse failure
    than the noise it replaced.

    Caught in review, 2026-08-04.
    """
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    admin = User.objects.create_superuser(username='boss3', password='x')
    client.force_login(admin)

    update_ops.status_path().write_text(json.dumps({
        'state': 'succeeded', 'exit_code': 0, 'finished_at': '2026-08-04T00:00:00Z',
    }))
    update_ops.incomplete_path().write_text(
        'These system services are not installed:\n'
        '  if\n  $WITH_DISK_CHECK\n  =\n  1\n  ];\n  then\n'
        'The web server cannot read this install\'s stylesheets (HTTP 403).\n'
        'Pages render as unstyled HTML with no logo.\n'
        '\n'
        'FIX: cd /home/tester/murphys-bench && scripts/install.sh\n'
        'Run it in a terminal. It needs a password.\n'
    )

    kept = update_ops.read_incomplete()
    assert 'stylesheets (HTTP 403)' in kept, 'the real warning was discarded'
    assert 'scripts/install.sh' in kept, 'the actionable fix was discarded'
    assert '$WITH_DISK_CHECK' not in kept, 'shell fragments survived'
    assert 'These system services' not in kept, 'the bogus block survived'

    body = client.get(reverse('core:update_status')).content.decode()
    assert 'This install is incomplete' in body
    assert 'stylesheets (HTTP 403)' in body
    assert '$WITH_DISK_CHECK' not in body


@pytest.mark.django_db
def test_card_discards_an_absurdly_long_marker(settings, tmp_path):
    """Length alone is a tell: the honest vocabulary here is a few unit names."""
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()

    update_ops.incomplete_path().write_text(
        'These system services are not installed:\n'
        + '  murphys-bench-restore.path\n' * 200
    )
    assert update_ops.read_incomplete() == '', (
        'a 200-line marker is a parser accident even when the lines parse as '
        'unit names; it must not become a wall of text in the UI'
    )


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



# ── Release-tag selection: a prerelease must never look like the latest release ──
# Git's version sort ranks a prerelease ABOVE the release it precedes: with v0.10.0
# and v0.10.0-rc1 both present, `--sort=-v:refname` and `sort -V` both pick the RC.
# Every "what is the newest release" site used one of those unfiltered, so pushing a
# single RC tag would have made every install in the field offer AND INSTALL a
# prerelease as the latest release — and a pushed tag cannot be withdrawn once boxes
# have fetched it. Found while about to push exactly such a tag.

_TAG_LISTING = "\n".join([
    'v1.0.0-beta',      # what -v:refname puts first, unfiltered
    'v0.10.0-rc1',
    'v0.10.0',          # the real answer
    'v0.9.10',
    'v0.9.0',
])


@pytest.mark.django_db
def test_available_version_ignores_prerelease_tags(monkeypatch):
    from core import update_ops
    monkeypatch.setattr(update_ops, '_git', lambda *a: _TAG_LISTING)
    assert update_ops.available_version() == 'v0.10.0'


@pytest.mark.django_db
def test_update_is_not_offered_for_a_prerelease(monkeypatch):
    """The button must not light up because someone tagged a release candidate."""
    from core import update_ops
    monkeypatch.setattr(update_ops, '_git', lambda *a: _TAG_LISTING)
    monkeypatch.setattr(update_ops, 'current_tag', lambda: 'v0.10.0')
    assert update_ops.is_update_available() is False


@pytest.mark.django_db
def test_a_real_newer_release_is_still_offered(monkeypatch):
    """The filter must not swallow genuine releases — including the non-rolling
    scheme, where v0.9.10 is newer than v0.9.9 but older than v0.10.0."""
    from core import update_ops
    monkeypatch.setattr(update_ops, '_git', lambda *a: _TAG_LISTING)
    monkeypatch.setattr(update_ops, 'current_tag', lambda: 'v0.9.10')
    assert update_ops.is_update_available() is True
    assert update_ops.available_version() == 'v0.10.0'


def test_default_target_line_survives_a_prerelease_only_repo():
    """EXECUTE update.sh's default-target line under its real shell flags.

    The filter that excludes prereleases is a `grep`, and grep exits 1 when it
    matches nothing — which is precisely the "only prerelease tags exist" case.
    update.sh runs under `set -euo pipefail`, so without a guard the script dies at
    the assignment and the explanatory failure on the NEXT line never runs. It
    still fails closed, but the operator loses the one message written to explain
    this exact situation.

    The structural test above cannot see this: the pipeline looks correct. Only
    running it under the script's own flags does.
    """
    import subprocess, tempfile, os
    src = (_repo_root() / 'scripts' / 'update.sh').read_text()
    line = next(ln.strip() for ln in src.splitlines()
                if ln.strip().startswith('TARGET="$(git tag -l'))

    with tempfile.TemporaryDirectory() as d:
        run = lambda *c: subprocess.run(c, cwd=d, capture_output=True, text=True)
        run('git', 'init', '-q', '.')
        run('git', 'config', 'user.email', 't@t')
        run('git', 'config', 'user.name', 't')
        open(os.path.join(d, 'f'), 'w').write('x')
        run('git', 'add', 'f')
        run('git', 'commit', '-qm', 'x')
        # ONLY prereleases — no strict vX.Y.Z tag anywhere.
        for tag in ('v1.0.0-beta', 'v0.10.0-rc1'):
            run('git', 'tag', tag)

        script = f'set -euo pipefail\n{line}\n[ -n "$TARGET" ] || {{ echo REACHED_DIAGNOSTIC; exit 3; }}\necho "PICKED=$TARGET"\n'
        out = subprocess.run(['bash', '-c', script], cwd=d,
                             capture_output=True, text=True)

    assert 'REACHED_DIAGNOSTIC' in out.stdout, (
        'update.sh dies at the tag-selection assignment when only prerelease tags '
        'exist, so its "no release tags exist yet" message never prints. '
        f'stdout={out.stdout!r} rc={out.returncode}'
    )
    assert 'PICKED=' not in out.stdout, 'a prerelease was selected as the target'


def test_every_newest_release_tag_site_filters_prereleases():
    """update.sh, run_update.sh, verify_install.sh and the documented recovery
    command each pick "the newest tag" independently. If one drifts, the UI offers
    one version and the updater installs another. This is the check that would have
    caught the original defect in any of them."""
    root = _repo_root()
    targets = [
        root / 'scripts' / 'update.sh',
        root / 'scripts' / 'run_update.sh',
        root / 'scripts' / 'verify_install.sh',
        root / 'CHANGELOG.md',
    ]
    offenders = []
    for f in targets:
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if line.lstrip().startswith('#') or line.lstrip().startswith('>' ) and 'git tag' not in line:
                continue
            if 'git tag -l' not in line:
                continue
            if 'v[0-9]' not in line:
                offenders.append(f'{f.name}:{i}: {line.strip()}')
    assert not offenders, (
        'these pick a newest tag without excluding prereleases, so an RC tag would '
        f'be treated as the latest release: {offenders}'
    )


# ── Update card: a result that no longer describes the box goes quiet ────────
# A tester updated by hand after the in-app update failed, and was left staring
# at a red "last update failed" banner on a box that was running the new release
# correctly. Nothing but run_update.sh writes that file, so it never aged out.

@pytest.mark.django_db
def test_failed_status_is_stale_once_the_box_has_moved_on(client, admin_user, settings, tmp_path, monkeypatch):
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({
        'state': 'failed', 'from_version': 'v0.4.52', 'target': 'v0.9.0',
        'log_tail': 'sudo: A terminal is required to authenticate',
    }))
    # The box was updated by hand afterwards and is now on the newer version.
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.9.0')
    assert update_ops.read_status()['stale'] is True

    client.force_login(admin_user)
    body = client.get(reverse('core:update_status')).content.decode()
    assert 'was automatically rolled back' not in body
    # The log stays reachable — hiding the banner must not destroy the evidence.
    assert 'earlier update attempt' in body.lower()


@pytest.mark.django_db
def test_genuine_failure_still_reports_because_rollback_returns_the_box(settings, tmp_path, monkeypatch):
    """A real failure rolls back, so the box ends on from_version and the banner
    must stay. The staleness rule must not swallow live failures."""
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({
        'state': 'failed', 'from_version': 'v0.4.52', 'target': 'v0.9.0',
    }))
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.4.52')
    assert update_ops.read_status()['stale'] is False


# ── Update card: a failed rollback must SHOUT, not go quiet ─────────────────
# The staleness rule assumes a failed update rolls back, so the box returns to
# from_version. When the rollback ITSELF fails ("MANUAL RECOVERY NEEDED") the box
# is stranded on the target, staleness fires, and the UI hides the failure banner
# and mislabels the log — on the one box that most needs to be told. A tester hit
# exactly this and reported seeing no log.

@pytest.mark.django_db
def test_rc2_is_stranded_even_though_the_box_is_on_the_target(settings, tmp_path, monkeypatch):
    """update.sh exit 2 = manual_abort: the rollback itself failed."""
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({
        'state': 'failed', 'from_version': 'v0.9.0', 'target': 'v0.10.0',
        'exit_code': 2,
    }))
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.10.0')
    status = update_ops.read_status()
    assert status['stranded'] is True
    assert status['stale'] is False


@pytest.mark.django_db
def test_rc1_is_not_stranded(settings, tmp_path, monkeypatch):
    """exit 1 = the update failed but the rollback returned the box."""
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({
        'state': 'failed', 'from_version': 'v0.9.0', 'target': 'v0.10.0',
        'exit_code': 1,
    }))
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.9.0')
    status = update_ops.read_status()
    assert status['stranded'] is False
    assert status['stale'] is False


@pytest.mark.django_db
def test_legacy_status_without_exit_code_on_the_target_is_stale(settings, tmp_path, monkeypatch):
    """Written before this release: no exit code, so the old rule applies. This
    deliberately stays quiet on a genuinely stranded OLD box rather than
    reintroducing the false alarm on a healthy hand-updated one."""
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({
        'state': 'failed', 'from_version': 'v0.9.0', 'target': 'v0.10.0',
    }))
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.10.0')
    status = update_ops.read_status()
    assert status['stranded'] is False
    assert status['stale'] is True


@pytest.mark.django_db
def test_legacy_status_without_exit_code_that_rolled_back_still_reports(settings, tmp_path, monkeypatch):
    """The common legacy case: rollback worked, box is back on from_version.
    The failure banner must still show."""
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({
        'state': 'failed', 'from_version': 'v0.9.0', 'target': 'v0.10.0',
    }))
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.9.0')
    status = update_ops.read_status()
    assert status['stranded'] is False
    assert status['stale'] is False


@pytest.mark.django_db
def test_stranded_box_gets_a_loud_banner_and_an_open_log(client, admin_user, settings, tmp_path, monkeypatch):
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({
        'state': 'failed', 'from_version': 'v0.9.0', 'target': 'v0.10.0',
        'exit_code': 2,
        'log_tail': 'MANUAL RECOVERY NEEDED - the app may be down',
    }))
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.10.0')
    client.force_login(admin_user)
    body = client.get(reverse('core:update_status')).content.decode()
    # It must say the rollback failed, name the risk, and give the fix.
    assert 'could not be rolled back' in body.lower()
    assert 'scripts/install.sh' in body
    # ...and NOT `git pull`: update.sh deploys with `git checkout --detach`, so the
    # box is on a detached HEAD where `git pull` exits 1. Handing a broken-box owner
    # a command that stops immediately is what an outside reviewer caught here.
    assert 'git pull' not in re.sub(r'<!--.*?-->', '', body, flags=re.S).split('<pre')[1]
    # The log must be visible, not collapsed behind "an earlier attempt".
    assert 'earlier update attempt' not in body.lower()
    assert '<details class="mt-3" open>' in body
    # And it must not reassure.
    assert "you're on the latest release" not in body.lower()


@pytest.mark.django_db
def test_success_banner_stays_while_the_box_is_on_the_version_it_installed(settings, tmp_path, monkeypatch):
    import json
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    update_ops.status_path().write_text(json.dumps({
        'state': 'succeeded', 'from_version': 'v0.4.52', 'target': 'v0.9.0',
    }))
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.9.0')
    assert update_ops.read_status()['stale'] is False
    # ...and goes quiet once the box has moved past it again.
    monkeypatch.setattr(update_ops, 'current_version', lambda: 'v0.9.1')
    assert update_ops.read_status()['stale'] is True


# ── Update card: "View changelog" link + changelog view ─────────────────────

def test_changelog_for_version_extracts_just_that_section(monkeypatch):
    """The section runs from its own '## vX.Y.Z ...' heading to the next
    '## ' heading (or end of file) — not the whole CHANGELOG.md."""
    from core import update_ops
    fake_changelog = (
        "# Changelog\n\n"
        "## v0.4.46 — 2026-07-19\n\n"
        "### Fixed\n- Newest thing.\n\n"
        "## v0.4.45 — 2026-07-19\n\n"
        "### Added\n- Older thing.\n"
    )
    monkeypatch.setattr(update_ops, '_git', lambda *a: fake_changelog if a[0] == 'show' else '')
    section = update_ops.changelog_for_version('v0.4.46')
    assert 'Newest thing' in section
    assert 'Older thing' not in section
    assert section.startswith('## v0.4.46')


def test_changelog_for_version_empty_when_tag_or_section_missing(monkeypatch):
    from core import update_ops
    monkeypatch.setattr(update_ops, '_git', lambda *a: '')
    assert update_ops.changelog_for_version('v9.9.9') == ''
    assert update_ops.changelog_for_version('') == ''

    monkeypatch.setattr(update_ops, '_git', lambda *a: '# Changelog\n\n## v0.1.0\nsomething\n')
    assert update_ops.changelog_for_version('v9.9.9') == ''


@pytest.mark.django_db
def test_update_changelog_view_requires_admin(client, client_obj):
    from core.models import User
    non_admin = User.objects.create_user(username='tech', password='x')
    client.force_login(non_admin)
    assert client.get(reverse('core:update_changelog')).status_code == 403


@pytest.mark.django_db
def test_update_changelog_view_renders_for_admin(client, admin_user, monkeypatch):
    from core import update_ops
    monkeypatch.setattr(update_ops, 'available_version', lambda: 'v0.4.46')
    monkeypatch.setattr(update_ops, 'changelog_for_version', lambda v: '## v0.4.46\n- Something new.')
    client.force_login(admin_user)
    resp = client.get(reverse('core:update_changelog'))
    assert resp.status_code == 200
    assert b'Something new' in resp.content


@pytest.mark.django_db
def test_update_card_links_to_changelog_when_update_available(client, admin_user, settings, tmp_path, monkeypatch):
    from core import update_ops
    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(update_ops, 'available_version', lambda: 'v0.4.46')
    monkeypatch.setattr(update_ops, 'is_update_available', lambda: True)
    client.force_login(admin_user)
    resp = client.get(reverse('core:update_status'))
    assert reverse('core:update_changelog').encode() in resp.content


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
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
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
    client.post(reverse('core:sale_checkout', args=[sale.pk]), {
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
    from core.models import CatalogItem
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

from core.models import PaymentChargeAttempt


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
    """A finished WO is eligible for the register.

    'completed' is the ONLY finished state now — 'closed' was removed as a work
    order status in migration 0104. This test used to assert both were settleable,
    which was the register quietly agreeing that a status the rest of the app
    called active meant finished. A cancelled WO is not settleable either: it is
    work that never happened, not work to be paid for."""
    completed = WorkOrder.objects.create(client=client_obj, status='completed')
    cancelled = WorkOrder.objects.create(client=client_obj, status='cancelled')
    open_wo = WorkOrder.objects.create(client=client_obj, status='in_progress')
    client.force_login(admin_user)

    resp = client.get(reverse('core:pos_home'), {'q': completed.work_order_number})
    numbers = [w.work_order_number for w in resp.context['results']]
    assert completed.work_order_number in numbers

    # Only the completed WO appears; neither cancelled nor in_progress is settleable.
    resp = client.get(reverse('core:pos_home'), {'q': client_obj.name})
    numbers = [w.work_order_number for w in resp.context['results']]
    assert completed.work_order_number in numbers
    assert cancelled.work_order_number not in numbers
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
def test_pos_wo_settle_draft_records_invoiced_on_mbs_own_invoice(client, admin_user, client_obj, monkeypatch):
    """Bill Later must land on MB's Invoice too, not just the WO.

    The bug this covers (found on prod WO-00017, Aug 14): the draft push wrote
    invoice_ninja_id onto the WORK ORDER and never touched MB's Invoice row, so
    a job sitting in Invoice Ninja as invoice #1931 still read as 'uninvoiced'
    here and stayed in the billing-ready queue with no amount recorded."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('225.00'))

    monkeypatch.setattr(invoice_ninja, '_request',
                        lambda method, path, **kw: {'data': {'id': 931, 'number': '1931'}})
    monkeypatch.setattr(invoice_ninja, 'find_or_create_client', lambda c: 'inclient-1')

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {'action': 'draft'})
    assert resp.status_code == 302

    wo.refresh_from_db()
    inv = wo.invoice
    inv.refresh_from_db()
    assert inv.billing_status == 'invoiced'
    assert inv.amount == Decimal('225.00')
    assert inv.invoiced_date is not None
    assert inv.invoice_ninja_id == wo.invoice_ninja_id
    # Still not a payment.
    assert inv.paid_at is None


@pytest.mark.django_db
def test_pos_wo_settle_draft_self_heals_a_stranded_record(client, admin_user, client_obj, monkeypatch):
    """A WO already pushed to IN but left 'uninvoiced' locally corrects itself.

    Records stranded by the original bug are fixed by revisiting Bill Later; no
    data migration needed. The push is NOT repeated (one job = one invoice), and
    the AMOUNT is deliberately not written: the lines may have changed since the
    original push, and Invoice Ninja is the money authority — MB must not record
    a figure IN was never sent. A healed record's amount stays blank for the
    operator to reconcile (outside-review finding, Aug 15)."""
    from decimal import Decimal
    from core import invoice_ninja
    from core.models import LineItem
    _enable_in()
    wo = WorkOrder.objects.create(client=client_obj, status='completed',
                                  invoice_ninja_id='931', invoice_ninja_ref='1931')
    # A line added AFTER the original push — today's total is not IN's total.
    LineItem.objects.create(content_object=wo, kind='labor', description='Bench work',
                            quantity=1, unit_price=Decimal('225.00'))
    assert wo.invoice.billing_status == 'uninvoiced'

    def fake_request(method, path, **kwargs):
        raise AssertionError(f'Must not push a second invoice: {method} {path}')
    monkeypatch.setattr(invoice_ninja, '_request', fake_request)

    client.force_login(admin_user)
    resp = client.post(reverse('core:pos_wo_settle', args=[wo.pk]), {'action': 'draft'})
    assert resp.status_code == 302

    inv = wo.invoice
    inv.refresh_from_db()
    assert inv.billing_status == 'invoiced'
    assert inv.invoice_ninja_id == '931'
    assert inv.amount is None, 'a self-heal must not invent an amount IN does not hold'


@pytest.mark.django_db
def test_invoiced_with_no_amount_cannot_be_quick_marked_paid(client, admin_user, client_obj):
    """Round-2 review finding: the self-heal's deliberately blank amount could
    still one-click into 'paid, no amount' via the billing card's Mark Paid,
    silently dropping the job from every revenue total. The quick action is
    refused server-side (a hidden button is not a mechanism); the full edit,
    which records the amount, remains the path through."""
    from decimal import Decimal
    from core.models import Invoice
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    Invoice.objects.filter(work_order=wo).update(billing_status='invoiced', amount=None)

    client.force_login(admin_user)
    url = reverse('core:wo_billing_update', args=[wo.pk])

    # Quick Mark Paid: refused, status unchanged, card explains why.
    resp = client.post(url, {'billing_status': 'paid'})
    assert resp.status_code == 200
    assert 'Enter the amount first' in resp.content.decode()
    inv = wo.invoice
    inv.refresh_from_db()
    assert inv.billing_status == 'invoiced'
    assert inv.paid_date is None

    # The card renders the guidance instead of the quick Mark Paid button.
    body = client.get(reverse('core:work_order_detail', args=[wo.pk])).content.decode()
    assert 'no amount recorded' in body

    # Full edit with an amount goes through.
    resp = client.post(url, {'billing_status': 'paid', 'full_edit': '1',
                             'amount': '150.00', 'payment_method': 'check'})
    assert resp.status_code == 200
    inv.refresh_from_db()
    assert inv.billing_status == 'paid'
    assert inv.amount == Decimal('150.00')

    # Regression: an invoiced record WITH an amount still quick-marks paid.
    wo2 = WorkOrder.objects.create(client=client_obj, status='completed')
    Invoice.objects.filter(work_order=wo2).update(billing_status='invoiced',
                                                  amount=Decimal('60.00'))
    resp = client.post(reverse('core:wo_billing_update', args=[wo2.pk]),
                       {'billing_status': 'paid'})
    assert resp.status_code == 200
    inv2 = wo2.invoice
    inv2.refresh_from_db()
    assert inv2.billing_status == 'paid'


@pytest.mark.django_db
def test_uninvoiced_with_no_amount_cannot_be_quick_paid_direct(client, admin_user, client_obj):
    """Round-3 review finding: the same 'paid with no amount' shape existed via
    the uninvoiced record's quick Paid Direct button, and prod held 3 such
    records, all invisible to revenue totals. Guard widened on Mike's call: no
    one-click paid status with a blank amount from ANY status. Paid Direct with
    an amount recorded still works."""
    from decimal import Decimal
    from core.models import Invoice
    wo = WorkOrder.objects.create(client=client_obj, status='completed')
    assert wo.invoice.billing_status == 'uninvoiced' and wo.invoice.amount is None

    client.force_login(admin_user)
    url = reverse('core:wo_billing_update', args=[wo.pk])

    # Quick Paid Direct with no amount: refused, record unchanged.
    resp = client.post(url, {'billing_status': 'paid_direct'})
    assert resp.status_code == 200
    assert 'Enter the amount first' in resp.content.decode()
    inv = wo.invoice
    inv.refresh_from_db()
    assert inv.billing_status == 'uninvoiced'
    assert inv.paid_date is None

    # The card offers guidance instead of the quick Paid Direct button.
    body = client.get(reverse('core:work_order_detail', args=[wo.pk])).content.decode()
    assert 'Paid Direct needs an amount' in body

    # Mark Invoiced (no money claim) is still one click.
    resp = client.post(url, {'billing_status': 'invoiced'})
    inv.refresh_from_db()
    assert inv.billing_status == 'invoiced'

    # And with an amount on record, uninvoiced quick Paid Direct still works.
    wo2 = WorkOrder.objects.create(client=client_obj, status='completed')
    Invoice.objects.filter(work_order=wo2).update(amount=Decimal('45.00'))
    resp = client.post(reverse('core:wo_billing_update', args=[wo2.pk]),
                       {'billing_status': 'paid_direct'})
    assert resp.status_code == 200
    inv2 = wo2.invoice
    inv2.refresh_from_db()
    assert inv2.billing_status == 'paid_direct'
    assert inv2.paid_date is not None


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
    assert b'id="section-techperf"' not in resp.content  # Business Metrics domain


@pytest.mark.django_db
def test_reports_tickets_domain_shows_only_raw_activity(client, admin_user):
    """Tickets domain is raw activity data — volume/status/by-client/by-tech —
    NOT performance metrics (those moved to Business Metrics)."""
    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'tickets'})
    assert resp.status_code == 200
    assert b'id="section-volume"' in resp.content
    assert b'id="section-status"' in resp.content
    assert b'id="section-byclient"' in resp.content
    assert b'id="section-bytech"' in resp.content
    assert b'id="section-sla"' not in resp.content        # Business Metrics domain
    assert b'id="section-backlog"' not in resp.content    # Business Metrics domain
    assert b'id="section-billing"' not in resp.content    # Financial domain


@pytest.mark.django_db
def test_reports_workorders_domain_shows_raw_activity_and_mileage(client, admin_user, client_obj):
    from datetime import date
    from core.models import Mileage
    Mileage.objects.create(technician=admin_user, trip_date=date.today(), miles=10, trip_type='one_way')

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'workorders'})
    assert resp.status_code == 200
    assert b'id="section-wostatus"' in resp.content
    assert b'id="section-wobyclient"' in resp.content
    assert b'id="section-wolist"' in resp.content
    assert b'id="section-mileage"' in resp.content
    assert b'id="section-techperf"' not in resp.content  # Business Metrics domain
    assert b'id="section-billing"' not in resp.content   # Financial domain
    assert b'id="section-volume"' not in resp.content    # Tickets domain


@pytest.mark.django_db
def test_reports_wo_status_includes_finished_work_orders(client, admin_user, client_obj):
    """Unlike Tickets' 'by status' view (which excludes closed on purpose),
    Work Orders by Status must include finished WOs — Mike's exact report: 'no
    open WOs, but there are 5 closed ones, should I see them here?' Hiding them
    would repeat the same gap.

    Asserts 'Completed' and 'Cancelled' rather than the retired 'Closed': a work
    order finishes as completed, and migration 0104 removed the third state.
    """
    WorkOrder.objects.create(client=client_obj, status='completed')
    WorkOrder.objects.create(client=client_obj, status='cancelled')
    WorkOrder.objects.create(client=client_obj, status='new')

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'workorders'})
    labels = {row['label'] for row in resp.context['wo_status_counts']}
    assert 'Completed' in labels
    assert 'Cancelled' in labels
    assert 'New' in labels
    numbers = [wo.work_order_number for wo in resp.context['wo_list']]
    assert len(numbers) == 3


@pytest.mark.django_db
def test_reports_wo_raw_activity_only_computed_for_workorders_domain(client, admin_user, client_obj):
    WorkOrder.objects.create(client=client_obj, status='completed')
    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'financial'})
    assert resp.context['wo_status_counts'] == []
    assert resp.context['wo_list'] == []


@pytest.mark.django_db
def test_reports_metrics_domain_shows_all_performance_sections(client, admin_user, client_obj):
    """Business Metrics groups every performance number (SLA, resolution time,
    conversion rate, technician performance, backlog health) in one place,
    separate from Financial (money) and Tickets/Work Orders (raw activity) —
    Mike's explicit call: these are 'how are we doing' numbers, not money or
    activity logs."""
    WorkOrder.objects.create(client=client_obj, status='completed', assigned_to=admin_user)

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'metrics'})
    assert resp.status_code == 200
    assert b'id="section-techperf"' in resp.content
    assert b'id="section-resolution"' in resp.content
    assert b'id="section-sla"' in resp.content
    assert b'id="section-backlog"' in resp.content
    assert b'id="section-conversion"' in resp.content
    assert b'id="section-billing"' not in resp.content   # Financial domain
    assert b'id="section-volume"' not in resp.content    # Tickets domain
    assert b'id="section-mileage"' not in resp.content   # Work Orders domain


@pytest.mark.django_db
def test_reports_ticket_time_per_ticket_breakdown(client, admin_user, client_obj, django_user_model):
    """Admin should see, in Reports, the time on each ticket and everyone who
    worked on it — without opening each ticket."""
    other = django_user_model.objects.create_user(username='tech2', password='x', first_name='Tess', last_name='Two')
    ticket = Ticket.objects.create(client=client_obj, subject='Terminal logout', description='D')
    TicketWorkLog.objects.create(ticket=ticket, minutes=10, logged_by=admin_user)
    TicketWorkLog.objects.create(ticket=ticket, minutes=5, logged_by=other)

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'metrics'})
    assert resp.status_code == 200
    assert b'id="section-tickettime"' in resp.content

    by_ticket = resp.context['ticket_time_by_ticket']
    assert len(by_ticket) == 1
    row = by_ticket[0]
    assert row['ticket'].pk == ticket.pk
    assert row['minutes'] == 15            # total time on the ticket
    assert row['entries'] == 2
    # Both techs who worked on it, with their split, most-first
    techs = dict(row['techs'])
    assert techs[admin_user.get_full_name() or admin_user.username] == 10
    assert techs['Tess Two'] == 5


@pytest.mark.django_db
def test_reports_wo_time_logged_section(client, admin_user, client_obj, django_user_model):
    """Business Metrics must report WO stopwatch time too — it had a Ticket
    Time section but no equivalent for Work Orders, a real reporting gap."""
    other = django_user_model.objects.create_user(username='tech3', password='x', first_name='Rae', last_name='Three')
    wo1 = WorkOrder.objects.create(client=client_obj, assigned_to=admin_user, time_spent_minutes=30)
    wo2 = WorkOrder.objects.create(client=client_obj, assigned_to=other, time_spent_minutes=20)
    WorkOrder.objects.create(client=client_obj, assigned_to=admin_user, time_spent_minutes=0)  # excluded: no time

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'metrics'})
    assert resp.status_code == 200
    assert b'id="section-wotime"' in resp.content

    assert resp.context['wo_time_total'] == 50
    assert resp.context['wo_time_wo_count'] == 2

    by_wo = {wo.pk: wo for wo in resp.context['wo_time_by_wo']}
    assert set(by_wo) == {wo1.pk, wo2.pk}

    by_tech = {row['assigned_to__first_name']: row for row in resp.context['wo_time_by_tech']}
    assert by_tech['Rae']['minutes'] == 20
    assert by_tech['Rae']['wo_count'] == 1


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
# Reports restructure Slice 2 — Financial "Revenue" breakdown (period /
# client type / category / source). A REVENUE statement, not a P&L: MB has
# no cost/expense data, so profit can't be honestly computed.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_revenue_only_computed_for_financial_domain(client, admin_user, client_obj):
    """The revenue breakdown is a real query cost — skip it entirely unless
    the Financial domain is actually being viewed."""
    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'), {'domain': 'tickets'})
    assert resp.context['revenue_total'] == 0
    assert resp.context['revenue_by_period'] == []


@pytest.mark.django_db
def test_revenue_combines_wo_and_sale_totals(client, admin_user, client_obj):
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
    assert resp.context['revenue_total'] == Decimal('100.00')
    source = dict(resp.context['revenue_by_source'])
    assert source['Work Orders'] == Decimal('75.00')
    assert source['Counter Sales'] == Decimal('25.00')


@pytest.mark.django_db
def test_revenue_by_client_type_buckets_walkin_separately(client, admin_user, client_obj):
    from decimal import Decimal
    from django.utils import timezone
    client_obj.client_type = 'business'
    client_obj.save()
    wo_biz = WorkOrder.objects.create(client=client_obj)
    _POSInvoice.objects.filter(work_order=wo_biz).update(
        billing_status='paid', amount=Decimal('50.00'), paid_date=timezone.localdate(),
    )
    walkin_sale = Sale.objects.create(client=None, status='completed',
                                      amount=Decimal('10.00'), payment_method='cash')
    Sale.objects.filter(pk=walkin_sale.pk).update(paid_at=timezone.now())

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    by_type = dict(resp.context['revenue_by_client_type'])
    assert by_type['Business'] == Decimal('50.00')
    assert by_type['Walk-in'] == Decimal('10.00')


@pytest.mark.django_db
def test_revenue_by_category_buckets_uncategorized(client, admin_user, client_obj):
    """A line item with no catalog item (a custom-typed entry) still counts
    toward total revenue but has no category — bucketed as 'Uncategorized'
    rather than silently dropped, per Mike's call."""
    from decimal import Decimal
    from django.utils import timezone
    from core.models import LineItem, CatalogItem
    cat_item = CatalogItem.objects.create(name='Virus Removal', category='Repair',
                                          item_type='service', default_price=Decimal('40.00'))
    wo = WorkOrder.objects.create(client=client_obj)
    LineItem.objects.create(content_object=wo, kind='labor', description='Virus Removal',
                            quantity=1, unit_price=Decimal('40.00'), catalog_item=cat_item)
    LineItem.objects.create(content_object=wo, kind='labor', description='Custom fix',
                            quantity=1, unit_price=Decimal('20.00'))  # no catalog_item
    _POSInvoice.objects.filter(work_order=wo).update(
        billing_status='paid', amount=Decimal('60.00'), paid_date=timezone.localdate(),
    )

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    by_cat = dict(resp.context['revenue_by_category'])
    assert by_cat['Repair'] == Decimal('40.00')
    assert by_cat['Uncategorized'] == Decimal('20.00')


@pytest.mark.django_db
def test_revenue_by_category_excludes_no_charge_lines(client, admin_user, client_obj):
    """A no-charge WO's priced line items didn't actually generate revenue
    (waived to $0) — they must not inflate the category breakdown even
    though the line items themselves still carry a price."""
    from decimal import Decimal
    from django.utils import timezone
    from core.models import LineItem, CatalogItem
    cat_item = CatalogItem.objects.create(name='Diagnostic', category='Repair',
                                          item_type='service', default_price=Decimal('30.00'))
    wo = WorkOrder.objects.create(client=client_obj)
    LineItem.objects.create(content_object=wo, kind='labor', description='Diagnostic',
                            quantity=1, unit_price=Decimal('30.00'), catalog_item=cat_item)
    _POSInvoice.objects.filter(work_order=wo).update(
        billing_status='paid', amount=Decimal('0.00'), payment_method='no_charge',
        paid_date=timezone.localdate(),
    )

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.context['revenue_total'] == Decimal('0.00')
    assert dict(resp.context['revenue_by_category']) == {}


@pytest.mark.django_db
def test_revenue_granularity_defaults_to_month_and_is_selectable(client, admin_user, client_obj):
    from decimal import Decimal
    from django.utils import timezone
    wo = WorkOrder.objects.create(client=client_obj)
    _POSInvoice.objects.filter(work_order=wo).update(
        billing_status='paid', amount=Decimal('10.00'), paid_date=timezone.localdate(),
    )

    client.force_login(admin_user)
    resp = client.get(reverse('core:reports'))
    assert resp.context['revenue_granularity'] == 'month'

    resp = client.get(reverse('core:reports'), {'granularity': 'day'})
    assert resp.context['revenue_granularity'] == 'day'

    resp = client.get(reverse('core:reports'), {'granularity': 'bogus'})
    assert resp.context['revenue_granularity'] == 'month'


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
    WorkOrder.objects.create(client=client_obj, status='completed')
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
    # A least-privilege S3 key scoped to one bucket cannot preflight the bucket,
    # so rclone must be told not to try. Asserted under [mbbackup] specifically:
    # it belongs to the S3 stanza, not the SMB one.
    offsite_stanza = conf.split('[mbbackup]', 1)[1].split('[', 1)[0]
    assert 'no_check_bucket = true' in offsite_stanza, (
        'without this, a bucket-scoped B2/S3 key fails every offsite copy on a '
        'permission check for a bucket operation Murphy\'s Bench never needs'
    )
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

from core.models import Contract


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


# ── Ticket time logging (lightweight, non-billable via TicketWorkLog) ───────

from core.models import TicketWorkLog


@pytest.mark.django_db
def test_ticket_add_time_creates_worklog_entries(client, client_obj, admin_user):
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    client.force_login(admin_user)

    client.post(reverse('core:ticket_add_time', args=[ticket.pk]),
                {'minutes': 15, 'note': 'account unlock'})
    client.post(reverse('core:ticket_add_time', args=[ticket.pk]), {'minutes': 10})

    logs = TicketWorkLog.objects.filter(ticket=ticket)
    assert logs.count() == 2                      # per-entry rows, not a counter
    assert ticket.time_spent_minutes == 25        # computed total sums the rows
    first = logs.order_by('logged_at').first()
    assert first.minutes == 15
    assert first.note == 'account unlock'
    assert first.logged_by == admin_user


@pytest.mark.django_db
def test_ticket_add_time_ignores_bad_input(client, client_obj, admin_user):
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    client.force_login(admin_user)

    client.post(reverse('core:ticket_add_time', args=[ticket.pk]), {'minutes': -5})
    client.post(reverse('core:ticket_add_time', args=[ticket.pk]), {'minutes': 'abc'})

    assert TicketWorkLog.objects.filter(ticket=ticket).count() == 0
    assert ticket.time_spent_minutes == 0


@pytest.mark.django_db
def test_ticket_time_spent_shown_in_details_card_not_timer(client, client_obj, admin_user):
    """Time Spent must render inside the Details card, matching the Work
    Order pattern -- not on the Timer card itself (the previous layout)."""
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D')
    TicketWorkLog.objects.create(ticket=ticket, minutes=15, logged_by=admin_user)
    client.force_login(admin_user)

    resp = client.get(reverse('core:ticket_detail', args=[ticket.pk]))
    content = resp.content.decode()
    details_idx = content.index('id="ticket-time-spent-wrapper"')
    timer_idx = content.index('>Timer<')
    assert details_idx < timer_idx, 'Time Spent must appear before the Timer card (i.e. inside Details)'
    assert 'Time Spent' in content


# ── Ticket/WO detail layout standardization + shared Device card ────────────
# (session Jul 22 2026): Ticket detail's right-rail "Details" accordion was
# crowded and had nowhere for device notes; WO detail already had the right
# shape (Client + Device cards up top, tools-only right rail) but its Device
# card had no notes. Both pages now share one Device card partial (collapsed
# by default, expands to specs + notes), and low-frequency metadata moved to
# a dedicated "Details & history" sub-page per record type.

@pytest.mark.django_db
def test_ticket_detail_shows_device_card_with_notes_collapsed(client, client_obj, admin_user):
    device = Device.objects.create(client=client_obj, name='Cindis-Mac-mini', notes='8GB RAM, slow boot')
    ticket = Ticket.objects.create(client=client_obj, device=device, subject='S', description='D')
    client.force_login(admin_user)

    resp = client.get(reverse('core:ticket_detail', args=[ticket.pk]))
    content = resp.content.decode()
    assert resp.status_code == 200
    assert 'Cindis-Mac-mini' in content
    assert 'Device Notes' in content
    assert '8GB RAM, slow boot' in content
    # Retired standalone Device Notes card / old sidebar Details accordion
    assert content.count('Device Notes') == 1


@pytest.mark.django_db
def test_wo_detail_shows_device_card_with_notes(client, client_obj, admin_user):
    device = Device.objects.create(client=client_obj, name='Dell Laptop', notes='Battery swollen — handle with care')
    wo = WorkOrder.objects.create(client=client_obj, device=device)
    client.force_login(admin_user)

    resp = client.get(reverse('core:work_order_detail', args=[wo.pk]))
    content = resp.content.decode()
    assert resp.status_code == 200
    assert 'Dell Laptop' in content
    assert 'Device Notes' in content
    assert 'Battery swollen' in content


@pytest.mark.django_db
def test_ticket_meta_page_renders_and_holds_linked_tickets(client, client_obj, admin_user):
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D', created_by=admin_user)
    client.force_login(admin_user)

    resp = client.get(reverse('core:ticket_meta', args=[ticket.pk]))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'Details &amp; History' in content or 'Details & History' in content
    assert 'Linked Tickets' in content


@pytest.mark.django_db
def test_wo_meta_page_renders(client, client_obj, admin_user):
    wo = WorkOrder.objects.create(client=client_obj)
    client.force_login(admin_user)

    resp = client.get(reverse('core:work_order_meta', args=[wo.pk]))
    assert resp.status_code == 200
    assert 'Days Open' in resp.content.decode()


@pytest.mark.django_db
def test_ticket_meta_404s_for_non_owning_non_admin_tech(client, client_obj):
    owner = User.objects.create_user(username='meta_owner', password='x', is_staff=False)
    other = User.objects.create_user(username='meta_other', password='x', is_staff=False)
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D', assigned_to=owner)

    client.force_login(other)
    resp = client.get(reverse('core:ticket_meta', args=[ticket.pk]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_wo_meta_404s_for_non_owning_non_admin_tech(client, client_obj):
    owner = User.objects.create_user(username='wo_meta_owner', password='x', is_staff=False)
    other = User.objects.create_user(username='wo_meta_other', password='x', is_staff=False)
    wo = WorkOrder.objects.create(client=client_obj, assigned_to=owner)

    client.force_login(other)
    resp = client.get(reverse('core:work_order_meta', args=[wo.pk]))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Device credentials — single store on Device, ASYMMETRIC gating.
#
# Read is tight (flag AND (admin OR assigned)); write is deliberately looser
# (flag alone). Blocking the write path pushes new passwords into plaintext
# notes or loses them, which is worse than the overwrite risk it would prevent.
# Accountability for writes comes from the audit log. Do NOT "tighten" the
# write gate to match the read gate — test_device_cred_unassigned_tech_can_write
# exists specifically to catch that.
# See ~/.claude/plans/device-credentials-single-store.md
# ---------------------------------------------------------------------------

@pytest.fixture
def cred_device(db, client_obj):
    return Device.objects.create(
        client=client_obj, name='Bench PC',
        device_username='localadmin', device_password='hunter2',
    )


@pytest.fixture
def cred_tech(db):
    from core.models import Role
    role = Role.objects.create(name='CredTech', can_view_device_credentials=True)
    return User.objects.create_user(username='credtech', password='x', role_obj=role)


@pytest.fixture
def plain_tech(db):
    from core.models import Role
    role = Role.objects.create(name='NoCredTech')  # flag absent
    return User.objects.create_user(username='plaintech', password='x', role_obj=role)


@pytest.mark.django_db
def test_device_cred_assigned_tech_can_reveal(client, client_obj, cred_device, cred_tech):
    """Flag + assigned to the work order the reveal is made from -> allowed, logged
    with the job context."""
    from core.models import DeviceCredentialAccessLog
    wo = WorkOrder.objects.create(client=client_obj, device=cred_device, assigned_to=cred_tech)

    client.force_login(cred_tech)
    resp = client.get(reverse('core:device_cred_reveal', args=[cred_device.pk, 'password']),
                      {'wo': wo.pk})
    assert resp.status_code == 200
    assert resp.content == b'hunter2'
    log = DeviceCredentialAccessLog.objects.get(device=cred_device, user=cred_tech, action='viewed')
    assert log.work_order_id == wo.pk


@pytest.mark.django_db
def test_device_cred_unassigned_tech_cannot_reveal(client, client_obj, cred_device, cred_tech):
    """Flag but NOT assigned -> denied, and the secret never reaches the response."""
    other = User.objects.create_user(username='otherguy', password='x')
    wo = WorkOrder.objects.create(client=client_obj, device=cred_device, assigned_to=other)

    client.force_login(cred_tech)
    resp = client.get(reverse('core:device_cred_reveal', args=[cred_device.pk, 'password']),
                      {'wo': wo.pk})
    assert resp.status_code == 403
    assert b'hunter2' not in resp.content


@pytest.mark.django_db
def test_device_cred_reveal_on_device_page_is_admin_only(client, cred_device, cred_tech):
    """No job context (the device page) -> no assignment to check, so admin only."""
    client.force_login(cred_tech)
    resp = client.get(reverse('core:device_cred_reveal', args=[cred_device.pk, 'password']))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_device_cred_admin_reveals_without_assignment(client, cred_device, admin_user):
    client.force_login(admin_user)
    resp = client.get(reverse('core:device_cred_reveal', args=[cred_device.pk, 'password']))
    assert resp.status_code == 200
    assert resp.content == b'hunter2'


@pytest.mark.django_db
def test_device_cred_assigned_tech_can_reveal_from_ticket(client, client_obj, cred_device, cred_tech):
    """A ticket is a valid job context — this is why credentials moved onto Device."""
    ticket = Ticket.objects.create(client=client_obj, subject='S', description='D',
                                   device=cred_device, assigned_to=cred_tech)
    client.force_login(cred_tech)
    resp = client.get(reverse('core:device_cred_reveal', args=[cred_device.pk, 'password']),
                      {'ticket': ticket.pk})
    assert resp.status_code == 200
    assert resp.content == b'hunter2'


@pytest.mark.django_db
def test_device_cred_unassigned_tech_can_write(client, client_obj, cred_device, cred_tech):
    """THE ASYMMETRY. A flag-holder records a new password on a job that isn't theirs.

    This is intentional: clients volunteer password changes mid-job, and a tech who
    can't record one puts it somewhere worse. If this test starts failing because
    someone added an assignment check to the write path, that's the regression —
    not the test.
    """
    from core.models import DeviceCredentialAccessLog
    other = User.objects.create_user(username='someoneelse', password='x')
    wo = WorkOrder.objects.create(client=client_obj, device=cred_device, assigned_to=other)

    client.force_login(cred_tech)
    resp = client.post(reverse('core:device_cred_update', args=[cred_device.pk]), {
        'device_username': 'localadmin',
        'device_password': 'newpass99',
        'credential_notes': '',
        'wo': wo.pk,
    })
    assert resp.status_code == 200
    cred_device.refresh_from_db()
    assert cred_device.device_password == 'newpass99'
    log = DeviceCredentialAccessLog.objects.get(device=cred_device, user=cred_tech, action='edited')
    assert log.work_order_id == wo.pk
    assert log.replaced_existing is True


@pytest.mark.django_db
def test_device_cred_write_without_flag_denied(client, cred_device, plain_tech):
    """The flag is still the floor for both paths."""
    client.force_login(plain_tech)
    resp = client.post(reverse('core:device_cred_update', args=[cred_device.pk]), {
        'device_username': 'x', 'device_password': 'nope', 'credential_notes': '',
    })
    assert resp.status_code == 403
    cred_device.refresh_from_db()
    assert cred_device.device_password == 'hunter2'


@pytest.mark.django_db
def test_device_cred_reveal_without_flag_denied(client, client_obj, cred_device, plain_tech):
    """Assignment alone is not enough — the flag is required too."""
    wo = WorkOrder.objects.create(client=client_obj, device=cred_device, assigned_to=plain_tech)
    client.force_login(plain_tech)
    resp = client.get(reverse('core:device_cred_reveal', args=[cred_device.pk, 'password']),
                      {'wo': wo.pk})
    assert resp.status_code == 403
    assert b'hunter2' not in resp.content


@pytest.mark.django_db
def test_device_cred_first_write_not_marked_as_replacement(client, client_obj, cred_tech):
    """replaced_existing distinguishes 'recorded' from 'overwrote'."""
    from core.models import DeviceCredentialAccessLog
    blank = Device.objects.create(client=client_obj, name='Fresh PC')
    client.force_login(cred_tech)
    client.post(reverse('core:device_cred_update', args=[blank.pk]), {
        'device_username': '', 'device_password': 'first', 'credential_notes': '',
    })
    log = DeviceCredentialAccessLog.objects.get(device=blank, action='edited')
    assert log.replaced_existing is False


@pytest.mark.django_db
def test_device_cred_blank_password_keeps_current(client, cred_device, admin_user):
    """Saving the form with the password field left blank must not wipe the stored one."""
    client.force_login(admin_user)
    client.post(reverse('core:device_cred_update', args=[cred_device.pk]), {
        'device_username': 'localadmin', 'device_password': '', 'credential_notes': 'note',
    })
    cred_device.refresh_from_db()
    assert cred_device.device_password == 'hunter2'
    assert cred_device.credential_notes == 'note'


# ── Migration 0099: WO credentials merge onto the Device, nothing discarded ──

@pytest.mark.parametrize('wo_creds,device_creds,expect', [
    # prod's actual case: WO holds a password, device is empty
    ({'password': 'hunter2'}, {}, {'username': '', 'password': 'hunter2', 'notes': ''}),
    # device already holds the same value -> no duplicate note
    ({'password': 'same'}, {'password': 'same'}, {'username': '', 'password': 'same', 'notes': ''}),
])
def test_migration_0099_merge_simple(wo_creds, device_creds, expect):
    from importlib import import_module
    mod = import_module('core.migrations.0099_retire_workorder_credentials')
    assert mod.merge_credentials(wo_creds, device_creds, 'WO-00007') == expect


def test_migration_0099_conflicting_value_is_carried_not_lost():
    """The device is the master, but a differing WO value must survive in notes."""
    from importlib import import_module
    mod = import_module('core.migrations.0099_retire_workorder_credentials')
    out = mod.merge_credentials(
        {'password': 'from-wo', 'pin': '1234', 'notes': 'recovery: bob@x.com'},
        {'password': 'from-device'},
        'WO-00007',
    )
    assert out['password'] == 'from-device'          # master wins
    assert 'from-wo' in out['notes']                 # nothing discarded
    assert 'PIN from WO-00007: 1234' in out['notes'] # pin has no device field
    assert 'recovery: bob@x.com' in out['notes']


@pytest.mark.django_db
def test_inbound_fetch_skips_when_another_run_holds_the_lock(monkeypatch, settings):
    """The single-runner lock is what stops overlapping fetches racing on one
    message (the original duplicate-ticket cause). Locking the configured path
    must make a second fetch a no-op rather than a second set of tickets.

    Also pins the lock to a SETTING: it used to be a fixed BASE_DIR path shared by
    every process on the host, so a concurrent test run stole it and this area of
    the suite failed spuriously.
    """
    import fcntl
    from core.management.commands.fetch_inbound_email import Command
    from django.core.management import call_command

    site = SiteSettings.get()
    site.inbound_email_enabled = True
    site.inbound_protocol = 'pop3'
    site.inbound_host = 'mail.example'
    site.inbound_username = 'support@example'
    site.save()

    raw = _raw_new_email(message_id='<locked-1@davis.example>')
    monkeypatch.setattr(Command, '_fetch_pop3', lambda self, s, d, v: [raw])

    before = Ticket.objects.count()
    holder = open(settings.INBOUND_FETCH_LOCK_PATH, 'w')
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        call_command('fetch_inbound_email', verbosity=0)
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()

    assert Ticket.objects.count() == before, 'A fetch must not run while another holds the lock.'

    # Lock released -> the same message is processed normally.
    call_command('fetch_inbound_email', verbosity=0)
    assert Ticket.objects.count() == before + 1


# ── Settings → Logs: unified search across every audit trail ────────────────

@pytest.fixture
def log_fixtures(db, client_obj):
    """One row in each of the sources the Logs tab merges."""
    from core.models import (EmailSendLog, InboundEmailLog, OrgCredential,
                             CredentialAccessLog, Device, DeviceCredentialAccessLog)
    u = User.objects.create_user(username='logtech', password='x', first_name='Log',
                                 last_name='Tech')
    ticket = Ticket.objects.create(client=client_obj, subject='Printer jam',
                                   description='D')
    EmailSendLog.objects.create(ticket=ticket, to_email='wayne@example.com',
                                trigger='ticket_reply', status='sent')
    InboundEmailLog.objects.create(from_email='wayne@example.com',
                                   subject='Re: Printer jam', ticket=ticket,
                                   status='reply')
    cred = OrgCredential.objects.create(name='Router admin')
    CredentialAccessLog.objects.create(credential=cred, user=u, action='viewed')
    device = Device.objects.create(client=client_obj, name='Front desk PC')
    DeviceCredentialAccessLog.objects.create(device=device, user=u, action='viewed',
                                             field='password')
    return {'user': u, 'ticket': ticket, 'device': device}


@pytest.mark.django_db
def test_logs_tab_merges_every_source_by_default(client, admin_user, log_fixtures):
    client.force_login(admin_user)
    body = client.get(reverse('core:settings'), {'tab': 'logs'}).content
    assert b'wayne@example.com' in body          # outbound email
    assert b'Re: Printer jam' in body            # inbound email
    assert b'Router admin' in body               # org credential
    assert b'Front desk PC' in body              # device credential
    assert b'All Activity' in body


@pytest.mark.django_db
def test_logs_search_spans_sources_and_source_filter_narrows(client, admin_user, log_fixtures):
    client.force_login(admin_user)
    # One term, two different logs — the thing a stacked-tables page can't do.
    hit = client.get(reverse('core:settings'),
                     {'tab': 'logs', 'log_q': 'wayne'}).content
    assert b'ticket_reply' in hit and b'Re: Printer jam' in hit
    assert b'Router admin' not in hit

    only = client.get(reverse('core:settings'),
                      {'tab': 'logs', 'log_source': 'org_cred'}).content
    assert b'Router admin' in only
    assert b'wayne@example.com' not in only


@pytest.mark.django_db
def test_logs_date_range_filters_and_bad_input_is_ignored(client, admin_user, log_fixtures):
    client.force_login(admin_user)
    future = client.get(reverse('core:settings'),
                        {'tab': 'logs', 'log_from': '2099-01-01'}).content
    assert b'wayne@example.com' not in future
    assert b'Nothing matched' in future

    # A half-typed date must not blank the page the user is reading.
    ok = client.get(reverse('core:settings'),
                    {'tab': 'logs', 'log_from': '2026-0'}).content
    assert b'wayne@example.com' in ok


@pytest.mark.django_db
def test_filtered_log_pages_past_the_old_200_row_ceiling(client, admin_user, client_obj):
    """The gap this closes: the tab used to render a flat most-recent-200, so
    older entries were unreachable in the UI at all."""
    from core.models import EmailSendLog
    for i in range(205):
        EmailSendLog.objects.create(to_email=f'user{i}@example.com',
                                    trigger='ticket_reply', status='sent')
    client.force_login(admin_user)
    url = reverse('core:settings')
    p1 = client.get(url, {'tab': 'logs', 'log_source': 'email_out'})
    assert p1.context['log_page'].paginator.count == 205
    assert p1.context['log_page'].paginator.num_pages == 3

    p3 = client.get(url, {'tab': 'logs', 'log_source': 'email_out', 'page': 3})
    assert len(p3.context['log_rows']) == 5      # the tail, previously invisible

    # And a targeted search finds a row that sat past the old ceiling.
    found = client.get(url, {'tab': 'logs', 'log_source': 'email_out',
                             'log_q': 'user203@example.com'})
    assert found.context['log_page'].paginator.count == 1


@pytest.mark.django_db
def test_logs_search_covers_status_and_action_words(client, admin_user, log_fixtures):
    """"failed", "error", "viewed" are the words someone actually types. They
    live in status/action columns, so those have to be searchable too."""
    from core.models import EmailSendLog
    EmailSendLog.objects.create(to_email='bounce@example.com', trigger='ticket_reply',
                                status='failed', reason='send_error')
    client.force_login(admin_user)
    url = reverse('core:settings')

    failed = client.get(url, {'tab': 'logs', 'log_q': 'failed'}).content
    assert b'bounce@example.com' in failed
    assert b'wayne@example.com' not in failed        # the sent one is excluded

    # An inbound status word, and a credential action word.
    assert b'Re: Printer jam' in client.get(url, {'tab': 'logs', 'log_q': 'reply'}).content
    assert b'Router admin' in client.get(url, {'tab': 'logs', 'log_q': 'viewed'}).content


def test_no_multiline_django_comments_in_templates():
    """`{# #}` is single-line only — a comment spanning lines renders as visible
    text on the page. This has shipped twice now (sale_checkout_card.html in the
    v0.4.15 hotfix, then three templates in the log-search work), and neither
    time did any view test catch it, because the page still returns 200. Use
    `{% comment %}` for anything multi-line."""
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent
    offenders = []
    for tpl in base.glob('**/templates/**/*.html'):
        if 'venv' in tpl.parts or 'node_modules' in tpl.parts:
            continue
        for lineno, line in enumerate(tpl.read_text().splitlines(), 1):
            if '{#' in line and '#}' not in line.split('{#', 1)[1]:
                offenders.append(f'{tpl.relative_to(base)}:{lineno}')
    assert not offenders, (
        'Unterminated {# #} comment renders as visible page text; '
        'use {% comment %}…{% endcomment %} instead:\n  ' + '\n  '.join(offenders)
    )


@pytest.mark.django_db
def test_dismissed_notices_stay_readable_in_the_log(client, admin_user, client_obj):
    """Retaining dismissed rows is only defensible if they can be read back.
    The bell hides them; Settings → Logs is where the who-saw-it-when record
    actually lives."""
    from core.models import Notification
    tech = User.objects.create_user(username='seen', password='x',
                                    first_name='Gene', last_name='Oster')
    n = Notification.objects.create(recipient=tech, kind='system_alert',
                                    text='Job failed: murphys-bench-sla-check')
    client.force_login(tech)
    client.post(reverse('core:notification_dismiss', args=[n.pk]))
    assert tech.notifications.live().count() == 0        # gone from the bell

    client.force_login(admin_user)
    body = client.get(reverse('core:settings'),
                      {'tab': 'logs', 'log_source': 'notification'}).content
    assert b'Job failed: murphys-bench-sla-check' in body
    assert b'Gene Oster' in body                          # who it reached
    assert b'Dismissed' in body                           # and what they did

    # And it is findable by the tech's name across the merged stream.
    merged = client.get(reverse('core:settings'),
                        {'tab': 'logs', 'log_q': 'Gene'}).content
    assert b'Job failed: murphys-bench-sla-check' in merged


@pytest.mark.django_db
def test_failed_send_records_why_not_just_that_it_failed(client, admin_user, client_obj):
    """A log row reading only "Failed · send_error" tells an admin nothing they
    can act on — the real SMTP cause used to reach murphys_bench.log and stop
    there, invisible from the UI."""
    from unittest.mock import patch
    from smtplib import SMTPAuthenticationError
    from core.models import EmailSendLog, EmailTemplate, SiteSettings

    site = SiteSettings.get()
    site.email_enabled = True
    site.email_host = 'mail.example.com'
    site.email_from = 'shop@example.com'
    site.save()
    EmailTemplate.objects.update_or_create(
        trigger='ticket_created',
        defaults={'subject_template': 'Hi', 'body_template': 'Body', 'is_active': True},
    )
    contact = Contact.objects.create(client=client_obj, first_name='Wayne',
                                     last_name='B', email='wayne@example.com',
                                     is_primary=True)
    ticket = Ticket.objects.create(client=client_obj, contact=contact,
                                   subject='S', description='D')

    boom = SMTPAuthenticationError(535, b'5.7.8 Authentication credentials invalid')
    with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=boom):
        from core.email_utils import send_ticket_email
        send_ticket_email('ticket_created', ticket)

    entry = EmailSendLog.objects.filter(status='failed').latest('created_at')
    assert 'SMTPAuthenticationError' in entry.detail
    assert 'Authentication credentials invalid' in entry.detail
    assert len(entry.detail) <= 255

    # And it is visible on the Logs page, not just in the database.
    client.force_login(admin_user)
    body = client.get(reverse('core:settings'),
                      {'tab': 'logs', 'log_source': 'email_out'}).content
    assert b'Authentication credentials invalid' in body
    # Searchable too — "authentication" is what someone would actually type.
    hit = client.get(reverse('core:settings'),
                     {'tab': 'logs', 'log_q': 'authentication'}).content
    assert b'wayne@example.com' in hit


# ── Static refs actually resolve under production (manifest) storage ─────────
#
# conftest's _plain_static_storage swaps the test suite onto plain StaticFilesStorage
# so a fresh install that hasn't run build_css.sh + collectstatic isn't buried in 120
# "Missing staticfiles manifest entry" failures. That trade would otherwise lose one
# real regression class: a {% static %} in a TEMPLATE naming a file that doesn't exist.
# Plain storage returns a URL for anything; collectstatic only walks static dirs, never
# templates. So neither would catch the typo.
#
# This test buys that coverage back. It opts BACK IN to ManifestStaticFilesStorage and
# renders the main pages, which is exactly when a bad ref raises. It skips when no
# manifest exists (a working tree that hasn't collected static yet) rather than failing
# — same precedent as the rclone-binary skip above. CI runs collectstatic before pytest,
# so it always executes there, which is the environment that gates a release.

def _staticfiles_manifest_ok():
    import os
    from django.conf import settings
    root = getattr(settings, 'STATIC_ROOT', None)
    return bool(root) and os.path.exists(os.path.join(str(root), 'staticfiles.json'))


manifest_skip = pytest.mark.skipif(
    not _staticfiles_manifest_ok(),
    reason='no staticfiles manifest on this runner — run build_css.sh + collectstatic',
)


@manifest_skip
@pytest.mark.django_db
def test_static_refs_resolve_under_manifest_storage(client, admin_user):
    """Every {% static %} on the main pages must name a file that really exists.

    Under ManifestStaticFilesStorage a missing entry raises ValueError, so a 200 here
    means every static reference on the page resolved.
    """
    from django.test import override_settings

    manifest = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'
        },
    }
    with override_settings(STORAGES=manifest):
        # Unauthenticated page — base_focus.html chrome.
        assert client.get(reverse('two_factor:login')).status_code == 200

        # Authenticated pages — base.html chrome, the sidebar, and the compiled CSS.
        client.force_login(admin_user)
        for name in ('core:dashboard', 'core:ticket_list', 'core:work_order_list',
                     'core:client_list', 'core:settings'):
            resp = client.get(reverse(name))
            assert resp.status_code == 200, f'{name} did not render under manifest storage'


# ---------------------------------------------------------------------------
# Deployment-layer portability
#
# In July 2026 every shell script and systemd unit hardcoded /opt/murphys-bench
# and User=scs-tech — the author's own server. On any other install the in-app
# Back up now and Update buttons queued work nothing ever picked up, scheduled
# backups never ran, and none of it reported a failure. An outside tester found
# it, because nothing here could: pytest runs Django in-process and never looks
# at the deployment layer.
#
# scripts/verify_install.sh is the real gate (it asserts these features WORK on
# a clean box), but it needs a VM. These two tests are the cheap half that runs
# on every push, so a hardcoded path can't quietly come back between clean-room
# runs.
# ---------------------------------------------------------------------------

def _repo_root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


# ── --skip-web: the commands we print must be runnable where we tell people to run
# them ─────────────────────────────────────────────────────────────────────────
# A --skip-web install prints hand-wiring commands for the jobs it did not schedule.
# The first version printed them RELATIVE ("venv/bin/python manage.py ..."), which
# works when pasted into a shell sitting in the app directory and fails silently in
# cron or a hand-written systemd unit, neither of which inherits that working
# directory. That is the defect this whole warning exists to prevent, reproduced
# inside the fix for it.
#
# Prose is deliberately NOT asserted here — a grep for wording rots the moment the
# wording improves, which PR #65 demonstrated over six review rounds. What is
# asserted is a structural invariant: every command example in that block is an
# absolute path under the install directory.

def _skip_web_summary(app='/opt/mb-test-render'):
    """Render install.sh's --skip-web closing block with a known APP."""
    import subprocess
    src = (_repo_root() / 'scripts' / 'install.sh').read_text()
    start = src.index('    cat <<DONE3')
    body_start = src.index('\n', start) + 1
    end = src.index('\nDONE3\n', body_start) + len('\nDONE3\n')
    script = f'APP={app}\nRUN_USER=mb\n' + src[start:end]
    return subprocess.run(['bash', '-c', script], capture_output=True, text=True).stdout


def test_skip_web_hand_wiring_commands_are_absolute():
    app = '/opt/mb-test-render'
    out = _skip_web_summary(app)
    assert out.strip(), 'the --skip-web closing summary rendered nothing'

    # Command examples are the indented lines naming a script or manage.py.
    #
    # A `cd <app> && ...` recipe is EXEMPT and that is not a loophole: it sets its
    # own working directory, so a relative path after it resolves correctly. The
    # invariant being protected is about commands handed to a SCHEDULER, which
    # supplies no working directory of its own.
    # Includes the WATCHED PATH, not only executables: the Back up now advice names
    # a trigger file for a path unit to watch, and `logs/backup-trigger` relative
    # would watch the wrong place — the same silent never-fires failure as a
    # relative ExecStart. The reviewer spotted that this guard did not cover it.
    examples = [ln.strip() for ln in out.splitlines()
                if ln.startswith('    ')
                and ('.sh' in ln or 'manage.py' in ln or 'trigger' in ln)]
    examples = [ln for ln in examples
                if not (ln.startswith('cd ') and '&&' in ln)]
    assert examples, 'no command examples found in the --skip-web summary'

    relative = [ln for ln in examples
                if not all(tok.startswith(app) for tok in ln.split()
                           if '/' in tok and not tok.startswith('-'))]
    assert not relative, (
        'these --skip-web instructions use relative paths, so they fail in cron or a '
        f'hand-written unit: {relative}'
    )


def test_skip_web_summary_names_the_working_directory():
    """Absolute paths alone are not enough — the trigger-watching advice and any
    job a reader adapts still need the app dir as the working directory."""
    app = '/opt/mb-test-render'
    out = _skip_web_summary(app)
    assert 'working directory' in out.lower()
    assert app in out


# ── --skip-web must not install a web server ────────────────────────────────
# The apt block is gated on --skip-apt, so `nginx` sitting in its package list ran
# even under --skip-web — whose whole promise is "don't touch gunicorn/nginx/
# systemd". Ubuntu's nginx package starts and enables itself, so such an install
# ended up with an active nginx on port 80, contending with whatever proxy the
# operator passed the flag to keep. Found by running the installer on a clean VM;
# reading the file five times did not surface it.
#
# Asserted structurally: nginx may only be added to the package list inside a
# SKIP_WEB branch. Wording and package order can change freely.

# ── A successful install clears a stale update RESULT ───────────────────────
# The stranded-update banner tells the operator to run scripts/install.sh. They
# did, on a real box; it recovered fully; and the banner stayed, because
# run_update.sh is the only writer of update-status.json and nothing cleared it.
# A permanent "the app may be down" warning on a healthy machine, inviting the
# recovery to be run again.
#
# Exercised by RUNNING the installer's clearing block, not by inspecting it — the
# defect it fixes was invisible to inspection for exactly that reason.

def _run_install_clear_block(state, exit_code=None):
    """Run install.sh's status-clearing block against a status file in `state`.

    Returns (file_still_exists, stdout).
    """
    import subprocess, tempfile, json, os, sys
    src = (_repo_root() / 'scripts' / 'install.sh').read_text()
    start = src.index('STATUS_FILE="$APP/logs/update-status.json"')
    end = src.index('\n# 12) Done.', start)
    block = src[start:end]

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, 'logs'))
        payload = {'state': state, 'from_version': 'v0.10.0', 'target': 'v0.10.1',
                   'log_tail': 'MANUAL RECOVERY NEEDED'}
        if exit_code is not None:
            payload['exit_code'] = exit_code
        status = os.path.join(d, 'logs', 'update-status.json')
        with open(status, 'w') as f:
            json.dump(payload, f)
        script = (f'APP={d}\nVENV={os.path.dirname(sys.executable)}\n'
                  'log() { echo "$*"; }\n' + block)
        out = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
        return os.path.exists(status), out.stdout


def test_successful_install_clears_a_stranded_failed_result():
    """The exact state the live rehearsal left behind: failed, exit_code 2."""
    exists, out = _run_install_clear_block('failed', exit_code=2)
    assert not exists, (
        'a stranded failed/exit_code:2 result survived a successful install, so the '
        'banner telling the operator to run it would never go away'
    )
    assert 'cleared' in out


def test_successful_install_clears_a_succeeded_result():
    exists, _ = _run_install_clear_block('succeeded')
    assert not exists


def test_successful_install_leaves_an_in_flight_update_alone():
    """queued/running means an update is happening right now — removing the file
    would leave the polling UI with nothing to poll."""
    for state in ('queued', 'running'):
        exists, out = _run_install_clear_block(state)
        assert exists, f'{state} status was deleted; an in-flight update would lose its UI'
        assert 'leaving' in out


def _manifest_array(name):
    """Read an array out of deploy/manifest.sh by SOURCING it, not parsing it.

    install.sh, install_units.sh, verify_install.sh and update.sh all source that
    file. So does this. Re-parsing it in Python would make these tests a second
    reader with its own idea of the format, which is the exact defect the
    manifest exists to remove.
    """
    import subprocess

    manifest = _repo_root() / 'deploy' / 'manifest.sh'
    result = subprocess.run(
        ['bash', '-c', f'. "{manifest}" && printf "%s\\n" "${{{name}[@]}}"'],
        capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def test_skip_web_does_not_install_a_web_server():
    base = _manifest_array('MB_APT_PACKAGES')
    conditional = _manifest_array('MB_APT_PACKAGES_WEB')

    for server in ('nginx', 'apache2', 'caddy', 'lighttpd'):
        assert server not in base, (
            f'{server} is in the unconditional package list, so --skip-web would '
            'install a web server it promises not to touch'
        )
    assert 'nginx' in conditional, (
        'nginx is no longer installed for a standard install either — the normal '
        'path needs it'
    )


def _unit_templates_on_disk():
    deploy = _repo_root() / 'deploy'
    found = {p.name for ext in ('*.service', '*.timer', '*.path')
             for p in deploy.glob(ext)}
    assert found, 'no unit templates found in deploy/ — the repo is not intact'
    return found


def _units_listed_in_manifest():
    return set(_manifest_array('MB_UNITS')) | set(_manifest_array('MB_UNITS_OPTIONAL'))


def test_every_unit_template_is_listed_in_the_manifest():
    """A unit file in deploy/ that nothing installs is the July 2026 defect.

    Sharing one manifest stops the installer and the verifier disagreeing, but it
    cannot notice a template someone added to deploy/ and never listed. That unit
    is never installed, so the button or timer behind it silently does nothing —
    which is exactly how in-app restore shipped a dead button. This is the one
    drift a shared file cannot prevent by construction, so it is a test.
    """
    unlisted = sorted(_unit_templates_on_disk() - _units_listed_in_manifest())
    assert not unlisted, (
        'These unit templates exist in deploy/ but are in no manifest list, so '
        'nothing installs them and the feature behind each one silently does '
        f'nothing: {unlisted}'
    )


def test_every_manifest_unit_has_a_template_on_disk():
    """The mirror of the above: a listed unit with no template makes
    install_units.sh fail partway through a real install."""
    missing = sorted(_units_listed_in_manifest() - _unit_templates_on_disk())
    assert not missing, (
        'The manifest lists units with no template in deploy/, so install_units.sh '
        f'will fail partway through a real install: {missing}'
    )


def test_legacy_unit_block_matches_the_manifest():
    """install_units.sh's legacy literal block must equal MB_UNITS exactly.

    Releases at or before v0.11.1 awk-parse this file for a literal `UNITS=(`
    block to learn what units to expect, and that parser is already shipped. The
    block is a duplicate list, which is normally the exact thing the manifest
    exists to abolish — it is tolerable ONLY because this test makes drift
    impossible.

    Verified on a real box: with no literal block the old parser swallows the
    script and writes 269 lines of shell fragments into logs/update-incomplete,
    which the Updates card renders verbatim on a healthy install. With an EMPTY
    block it is worse — that parser ends in `grep -v '^$'`, which exits 1 on
    empty input, and the old update.sh runs under `set -euo pipefail`, so the
    update fails outright.
    """
    import subprocess

    installer = _repo_root() / 'scripts' / 'install_units.sh'
    # Parsed with the SAME awk+sed pipeline the shipped v0.11.1 update.sh uses,
    # so this asserts what that code will actually see, not what we hope it sees.
    legacy = subprocess.run(
        ['bash', '-c',
         "awk '/^UNITS=\\(/{f=1;next} f&&/^\\)/{exit} f{print}' \"$1\" "
         "| sed -e 's/#.*//' -e \"s/['\\\"]//g\" -e 's/^[[:space:]]*//' "
         "-e 's/[[:space:]]*$//' | grep -v '^$'",
         '_', str(installer)],
        capture_output=True, text=True, check=False,
    ).stdout.split()

    assert legacy, (
        'the legacy literal UNITS block is gone or unparseable. An old box '
        'updating forward would get an EMPTY list, and its parser exits 1 on '
        'empty under set -e — the update would fail, not just warn.'
    )
    assert legacy == _manifest_array('MB_UNITS'), (
        'the legacy literal block and MB_UNITS disagree, so a box on v0.11.1 or '
        'earlier would be told the wrong units are missing.\n'
        f'  legacy:   {legacy}\n  manifest: {_manifest_array("MB_UNITS")}'
    )


# These four were one test with four assertions. Split so that a failure
# identifies itself: which invariant broke is now the test name, not something you
# work out by reading the source. That also means a mutation sweep needs no text
# matching to tell them apart — the earlier combined version let a planted defect
# aimed at the install list be satisfied by the enable-list assertion instead.

def test_enabled_units_are_all_installed_units():
    units = set(_manifest_array('MB_UNITS'))
    stray = sorted(set(_manifest_array('MB_UNITS_ENABLE')) - units)
    assert not stray, f'enabled but never installed: {stray}'


def test_optional_enabled_units_are_all_optional_installed_units():
    optional = set(_manifest_array('MB_UNITS_OPTIONAL'))
    stray = sorted(set(_manifest_array('MB_UNITS_OPTIONAL_ENABLE')) - optional)
    assert not stray, f'optional units enabled but never installed: {stray}'


def test_alert_hook_jobs_are_installed_units():
    """An OnFailure drop-in is written into <job>.service.d, so the job has to be a
    real installed service or the hook lands on nothing and the failure it exists
    to report passes silently."""
    units = set(_manifest_array('MB_UNITS'))
    hooks = [f'{job}.service' for job in _manifest_array('MB_ALERT_HOOK_JOBS')]
    stray = sorted(set(hooks) - units)
    assert not stray, (
        f'failure alerts are wired to units that are never installed: {stray}'
    )


def test_every_path_unit_has_its_companion_service_installed():
    """A .path unit triggers the .service of the same name. Installing one without
    the other is a button that queues a job nothing consumes — the spinner."""
    units = set(_manifest_array('MB_UNITS'))
    orphans = [u for u in units if u.endswith('.path')
               and u[: -len('.path')] + '.service' not in units]
    assert not orphans, (
        f'these .path units have no companion .service, so whatever writes their '
        f'trigger file will spin forever: {sorted(orphans)}'
    )


# Three separate guards on one real drift found 2026-08-03: install.sh INSTALLED
# five sudo verbs, its own fallback instructions named three, and
# verify_install.sh checked three — so a box missing status and is-active passed
# the clean-room gate. All three now derive from MB_SUDO_VERBS.

def test_sudo_grant_keeps_the_verbs_rollback_needs():
    verbs = _manifest_array('MB_SUDO_VERBS')
    missing = [v for v in ('restart', 'stop', 'start') if v not in verbs]
    assert not missing, (
        f'{missing} removed from the sudo grant. Rollback runs restore.sh, which '
        'stops the service, restores the database and starts it again — trimming '
        'this list breaks recovery in the one situation it exists for.'
    )


def test_installer_does_not_hand_write_the_sudo_grant():
    install = (_repo_root() / 'scripts' / 'install.sh').read_text()
    assert 'restart murphys-bench, ' not in install, (
        'install.sh hand-writes a sudo grant string again. Build it from '
        'MB_SUDO_VERBS so the rule and the printed instructions cannot diverge.'
    )


def test_verifier_does_not_keep_its_own_sudo_verb_list():
    verify = (_repo_root() / 'scripts' / 'verify_install.sh').read_text()
    assert 'for verb in restart' not in verify, (
        'verify_install.sh keeps its own verb list again, so it can pass a box '
        'the installer built differently. Loop over MB_SUDO_VERBS.'
    )


def test_no_script_hardcodes_the_authors_install_path_or_user():
    """Executable lines in scripts/ must not name /opt/murphys-bench or scs-tech.

    Comments are exempt — several deliberately mention the old values to explain
    why they're gone. Two scripts are exempt entirely because the strings are
    their subject matter, not their configuration: release.sh prints example
    deploy commands for a human to read, and verify_install.sh greps for these
    very strings as its own check.
    """
    import re

    exempt = {'release.sh', 'verify_install.sh'}
    offenders = []
    for path in sorted((_repo_root() / 'scripts').glob('*.sh')):
        if path.name in exempt:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.match(r'\s*#', line) or not line.strip():
                continue
            if '/opt/murphys-bench' in line or 'scs-tech' in line:
                offenders.append(f'{path.name}:{n}: {line.strip()}')

    assert not offenders, (
        'These lines hardcode the author\'s install path or app user, which '
        'silently breaks backups/updates on every other install:\n  '
        + '\n  '.join(offenders)
    )


def test_unit_templates_are_templates_not_baked_for_one_box():
    """deploy/*.service|timer|path must use placeholders, not literal values.

    scripts/install_units.sh substitutes __APP__/__RUN_USER__/__RUN_GROUP__ at
    install time. A unit that ships with a literal path loads fine under systemd
    and fails only when it eventually fires — the silent shape this whole change
    exists to remove.
    """
    deploy = _repo_root() / 'deploy'
    units = sorted(
        p for ext in ('*.service', '*.timer', '*.path') for p in deploy.glob(ext)
    )
    assert units, 'no unit templates found in deploy/'

    baked, unsubstituted = [], []
    for path in units:
        text = path.read_text()
        if '/opt/murphys-bench' in text or 'User=scs-tech' in text:
            baked.append(path.name)
        # Anything with an ExecStart or a watched path must reference __APP__.
        if ('ExecStart=' in text or 'PathExists=' in text) and '__APP__' not in text:
            unsubstituted.append(path.name)

    assert not baked, f'unit templates carry a baked-in path/user: {baked}'
    assert not unsubstituted, (
        f'unit templates reference no __APP__ placeholder: {unsubstituted}'
    )


def test_update_sh_derives_its_expected_units_from_install_units(tmp_path):
    """update.sh's completeness check must not carry its own unit list.

    It used to name three units inline, so it only ever knew about the units that
    existed when that line was written. In-app restore added two more and the check
    stayed silent about them: the update reported success, the button appeared, and
    nothing had installed what it needed. Reading the list from deploy/manifest.sh
    means a release that adds a unit is covered without anyone remembering to.

    Proven by planting a unit the real manifest has never contained and requiring
    update.sh's own lookup to surface it.
    """
    import re
    import subprocess

    # expected_units() moved out of update.sh into check_install.sh when
    # install_units.sh and install.sh had to run the same check. These tests
    # follow the function, not the file it used to live in.
    check_sh = (_repo_root() / 'scripts' / 'check_install.sh').read_text()
    m = re.search(r'^expected_units\(\)\s*\{.*?^\}', check_sh, re.S | re.M)
    assert m, 'check_install.sh no longer defines expected_units()'

    fake = tmp_path / 'deploy'
    fake.mkdir(parents=True)
    (fake / 'manifest.sh').write_text(
        "MB_UNITS=(\n"
        "    murphys-bench.service              # gunicorn\n"
        "    'murphys-bench-alert@.service'     # quoted template unit\n"
        "    murphys-bench-planted.path         # never existed before this test\n"
        ")\n"
        "MB_UNITS_OPTIONAL=(\n"
        "    murphys-bench-disk-check.timer\n"
        ")\n"
    )

    out = subprocess.run(
        ['bash', '-c', f'{m.group(0)}\nAPP={tmp_path}\nexpected_units'],
        capture_output=True, text=True, timeout=30,
    )
    found = out.stdout.split()

    assert 'murphys-bench-planted.path' in found, (
        f'update.sh did not pick up a newly added unit — its check has drifted '
        f'back to a hardcoded list. Got: {found}'
    )
    # Comments and quotes must be stripped, or systemctl is handed a garbage name
    # and every box reports a phantom missing unit.
    assert 'murphys-bench-alert@.service' in found, f'quoted entry mangled: {found}'
    assert not any(f.startswith('#') for f in found), f'comment leaked: {found}'
    # The disk-check units are opt-in (--with-disk-check). Treating them as expected
    # would warn every box that deliberately does not run them, and a warning that
    # cries wolf is one nobody reads.
    assert 'murphys-bench-disk-check.timer' not in found, (
        f'opt-in units must not be treated as expected: {found}'
    )


def test_expected_units_cannot_fail_the_update_when_the_manifest_is_absent(tmp_path):
    """A missing manifest must yield an empty list, never a non-zero exit.

    update.sh runs under `set -e`, and by the time this function is called the
    tree has ALREADY been checked out to the target — so on a rollback, or a
    downgrade to any release older than the manifest, the file is not there. The
    first version of this returned non-zero in that case, which killed the update
    at `units="$(expected_units)"` AFTER every real step had succeeded: migrate,
    css build and collectstatic all done, service healthy, and the UI reporting a
    failed update. The empty-list fallback below it never got to run.

    Found by the clean-room gate on a real box, not by any test — which is why
    this one exists.
    """
    import re
    import subprocess

    # expected_units() moved out of update.sh into check_install.sh when
    # install_units.sh and install.sh had to run the same check. These tests
    # follow the function, not the file it used to live in.
    check_sh = (_repo_root() / 'scripts' / 'check_install.sh').read_text()
    m = re.search(r'^expected_units\(\)\s*\{.*?^\}', check_sh, re.S | re.M)
    assert m, 'check_install.sh no longer defines expected_units()'

    # tmp_path has no deploy/manifest.sh — the rollback-target shape.
    out = subprocess.run(
        ['bash', '-c', f'set -euo pipefail\n{m.group(0)}\nAPP={tmp_path}\n'
                       'units="$(expected_units)"\necho "SURVIVED:[$units]"'],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, (
        'expected_units failed the caller when the manifest was missing, so an '
        'update that had already succeeded would report failure. '
        f'stderr: {out.stderr.strip()}'
    )
    assert 'SURVIVED:[]' in out.stdout, (
        f'expected an empty unit list, got: {out.stdout.strip()}'
    )


def test_manifest_less_target_still_expects_that_release_s_own_units(tmp_path):
    """A rollback target with no manifest must still be checked properly.

    Caught in review 2026-08-04. The manifest-less path returned nothing and fell
    through to a three-unit fallback, while v0.11.1 — the release you actually
    roll back to — declares FOURTEEN. So restore, inbound-email and SLA units went
    unchecked and the update could report the install clean while they were
    missing. Under-reporting on the recovery path is precisely what this check
    exists to prevent.

    Driven against a CHECKED-IN copy of v0.11.1's install_units.sh, deliberately
    not `git show v0.11.1:...`. The first version of this test read the tag and
    skipped when it was missing — and CI's checkout is shallow with no tags
    (`.github/workflows/ci.yml` sets no fetch-depth), so the test guarding this
    defect would have silently skipped in the one place it most needed to run.
    A test whose authority depends on repo tag availability is decoration.
    Caught in review, 2026-08-04.

    `test_legacy_fixture_matches_the_real_tag` keeps the fixture honest, and that
    one is allowed to skip because nothing load-bearing rests on it.
    """
    import re
    import subprocess

    # expected_units() moved out of update.sh into check_install.sh when
    # install_units.sh and install.sh had to run the same check. These tests
    # follow the function, not the file it used to live in.
    check_sh = (_repo_root() / 'scripts' / 'check_install.sh').read_text()
    m = re.search(r'^expected_units\(\)\s*\{.*?^\}', check_sh, re.S | re.M)
    assert m, 'check_install.sh no longer defines expected_units()'

    # A tree shaped like a pre-manifest release: its own install_units.sh, no manifest.
    scripts = tmp_path / 'scripts'
    scripts.mkdir(parents=True)
    fixture = _repo_root() / 'core' / 'testdata' / 'install_units_v0.11.1.sh'
    assert fixture.exists(), (
        'the checked-in v0.11.1 installer fixture is missing, so this test has '
        'nothing to assert against — it must never degrade to a skip'
    )
    (scripts / 'install_units.sh').write_text(fixture.read_text())

    out = subprocess.run(
        ['bash', '-c', f'set -euo pipefail\n{m.group(0)}\nAPP={tmp_path}\n'
                       'units="$(expected_units)"\necho "$units"'],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, f'expected_units failed: {out.stderr.strip()}'
    found = out.stdout.split()

    assert len(found) == 14, (
        f'a manifest-less rollback target must still be checked against its own '
        f'14 units; got {len(found)}: {found}'
    )
    for critical in (
        'murphys-bench-restore.path',
        'murphys-bench-fetch-email.timer',
        'murphys-bench-sla-check.timer',
    ):
        assert critical in found, (
            f'{critical} went unchecked on the rollback path — the box could be '
            'told it is clean while that unit is missing'
        )


def test_legacy_fixture_matches_the_real_tag():
    """The checked-in v0.11.1 fixture must still match what v0.11.1 shipped.

    This is the ONLY test here allowed to skip. Nothing load-bearing rests on it:
    it exists so the fixture cannot quietly drift from the release it claims to
    copy. The test that actually guards the rollback under-reporting defect reads
    the fixture directly and never skips, which is the whole point of splitting
    the two — CI checks out shallow with no tags.
    """
    import subprocess

    real = subprocess.run(
        ['git', 'show', 'v0.11.1:scripts/install_units.sh'],
        cwd=str(_repo_root()), capture_output=True, text=True,
    )
    if real.returncode != 0:
        pytest.skip('tag v0.11.1 not reachable here (shallow checkout); the '
                    'fixture-driven test above still ran')

    fixture = (_repo_root() / 'core' / 'testdata' / 'install_units_v0.11.1.sh').read_text()
    assert fixture == real.stdout, (
        'core/testdata/install_units_v0.11.1.sh no longer matches what v0.11.1 '
        'actually shipped, so the rollback test is asserting against fiction'
    )



# ── System alerts are the owner's, not the technicians' ─────────────────────
# MB writes its own operational failures (failed backup, disk pressure, an
# unhandled 500) into itself as tickets, so they land somewhere already watched.
# They arrived unassigned, which put them in the unclaimed pool every technician
# sees, on a client whose default type gave them a response SLA. So a Level-1 got
# "Backup failed" in their queue with a clock ticking on it, could close it, and
# the miss counted against the shop's own SLA compliance figure.

@pytest.mark.django_db
def test_system_alert_is_invisible_to_a_technician(client, django_user_model):
    from core.system_alerts import create_system_alert
    alert = create_system_alert('Backup failed: rclone auth error')

    tech = django_user_model.objects.create_user(
        username='l1tech', password='x' * 14, is_staff=False,
    )
    client.force_login(tech)

    listing = client.get(reverse('core:ticket_list'))
    assert alert.ticket_number not in listing.content.decode()
    # And not reachable by guessing the URL either.
    assert client.get(reverse('core:ticket_detail', args=[alert.pk])).status_code == 404


@pytest.mark.django_db
def test_system_alert_is_still_visible_to_an_admin(client, admin_user):
    from core.system_alerts import create_system_alert
    alert = create_system_alert('Disk nearly full on /')
    client.force_login(admin_user)
    assert client.get(reverse('core:ticket_detail', args=[alert.pk])).status_code == 200


@pytest.mark.django_db
def test_system_alert_gets_no_sla_clock_even_when_a_default_is_set():
    """The System Alerts client is created with the default client_type
    ('residential'), so before this fix a configured residential default stamped
    a due_at on every alert and the alert went overdue on its own."""
    from core.models import SLAPlan, SiteSettings
    from core.system_alerts import create_system_alert

    plan = SLAPlan.objects.create(name='Residential 24h', grace_period_hours=24)
    site = SiteSettings.get()
    site.default_residential_sla = plan
    site.save()

    alert = create_system_alert('Backup failed: destination unreachable')
    assert alert.due_at is None
    assert alert.sla_plan is None
    assert alert.is_overdue is False


def test_every_installed_config_in_deploy_is_a_template():
    """The check above only looked at *.service|timer|path, so a config with no
    such extension could still ship hardcoded — and one did. The logrotate config
    named the author's path and username and was installed by a copy-paste line in
    deploy/README.md, so on every other box the gunicorn logs never rotated.
    Silent, unbounded, and invisible to a check that only knew about unit files.

    Scan everything installed from deploy/ instead of an extension whitelist.
    """
    deploy = _repo_root() / 'deploy'
    offenders = []
    for path in sorted(deploy.rglob('*')):
        if not path.is_file() or path.suffix == '.md':
            continue
        # deploy/demo/ records how the retired demo box was built by hand. It is
        # reference material — nothing installs from it — so its literal values
        # are the point, not a defect.
        if 'demo' in path.relative_to(deploy).parts:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith('#'):
                continue
            if '/opt/murphys-bench' in line or 'scs-tech' in line:
                offenders.append(f'{path.relative_to(deploy)}:{lineno}')

    assert not offenders, (
        'a file installed from deploy/ hardcodes the author\'s install path or '
        'user instead of using __APP__/__RUN_USER__:\n  ' + '\n  '.join(offenders)
    )


# ══ Security review July 2026: three P0 authorization defects ═══════════════
#
# All three were real server-side defects found by an external repository review
# and confirmed against the code before fixing. Each test below fails on the
# pre-fix code. See docs/ for the review write-up.


# ── P0-1: credential reveal must be bound to the REQUESTED device ───────────

@pytest.mark.django_db
def test_device_cred_job_from_another_device_cannot_reveal(client, client_obj, cred_device, cred_tech):
    """THE defect: assignment to Job A must not unlock Device B.

    The reveal gate authorized on assignment to the job id supplied by the browser,
    and only loaded the requested device afterwards, without ever checking the two
    were related. So a tech assigned to any single work order could read every
    device password in the shop by passing their own WO id with someone else's
    device. Pre-fix this returned 200 and the plaintext secret.
    """
    from core.models import DeviceCredentialAccessLog
    other_device = Device.objects.create(client=client_obj, name='Someone Elses PC',
                                         device_password='secret-b')
    # The tech is legitimately assigned to a job — but on a DIFFERENT device.
    own_wo = WorkOrder.objects.create(client=client_obj, device=cred_device,
                                      assigned_to=cred_tech)

    client.force_login(cred_tech)
    resp = client.get(reverse('core:device_cred_reveal', args=[other_device.pk, 'password']),
                      {'wo': own_wo.pk})

    assert resp.status_code == 403
    assert b'secret-b' not in resp.content
    # And nothing may be logged against the unrelated job.
    assert not DeviceCredentialAccessLog.objects.filter(device=other_device).exists()


@pytest.mark.django_db
def test_device_cred_ticket_from_another_device_cannot_reveal(client, client_obj, cred_device, cred_tech):
    """Same defect via the ?ticket= path — both job types carry a device FK."""
    other_device = Device.objects.create(client=client_obj, name='Other PC',
                                         device_password='secret-c')
    own_ticket = Ticket.objects.create(client=client_obj, subject='S', description='D',
                                       device=cred_device, assigned_to=cred_tech)

    client.force_login(cred_tech)
    resp = client.get(reverse('core:device_cred_reveal', args=[other_device.pk, 'password']),
                      {'ticket': own_ticket.pk})

    assert resp.status_code == 403
    assert b'secret-c' not in resp.content


@pytest.mark.django_db
def test_device_cred_write_logs_only_matching_job_context(client, client_obj, cred_device, cred_tech):
    """The write path shares _job_context, so an unrelated job id must not be
    recorded as the context for an edit either — the audit log is the whole
    accountability story for writes, so it must not be forgeable."""
    from core.models import DeviceCredentialAccessLog
    unrelated_wo = WorkOrder.objects.create(client=client_obj, assigned_to=cred_tech)

    client.force_login(cred_tech)
    resp = client.post(reverse('core:device_cred_update', args=[cred_device.pk]),
                       {'device_username': 'u', 'device_password': 'p',
                        'credential_notes': '', 'wo': unrelated_wo.pk})

    assert resp.status_code == 200  # write is deliberately not assignment-gated
    log = DeviceCredentialAccessLog.objects.get(device=cred_device, action='edited')
    assert log.work_order_id is None


# ── P0-2: every /settings/ route is admin-only ──────────────────────────────

@pytest.mark.django_db
def test_every_settings_route_is_admin_only(client, db):
    """Enumerates the URLconf and fails on ANY settings route a non-admin reaches.

    This is the real guard, not the individual mixin additions: 22 directly-routable
    mutation views under /settings/ had drifted to LoginRequired-only while the
    Settings page itself was gated, so denying the page secured nothing. A
    per-view judgement call is what allowed the drift, so the invariant is the
    whole prefix. A new settings route cannot silently forget the gate.

    Genuinely tech-facing endpoints must live OUTSIDE settings/ rather than being
    excepted here — the canned-response picker was moved out for exactly this.

    ⚠ The check is STRUCTURAL (does the view class carry the mixin), not merely a
    response-code check. Accepting 404/405 as "gate fired" was a hole: a view that
    forgot the mixin entirely still passed if its placeholder pk of 1 happened to
    404, or if it only accepted the other HTTP method. The response codes are still
    asserted as a second layer, but they can no longer stand in for the gate.
    """
    from django.urls import get_resolver
    from core.models import Role
    from core.views import SettingsAdminMixin

    # The ONE deliberate exception, documented on the view itself: revealing an org
    # credential is flag-gated (can_view_credentials) rather than admin-only, so a
    # tech can read a shared shop password and be logged doing it. Named here so
    # adding a second exception is a visible edit, not a silent drift.
    STRUCTURAL_EXCEPTIONS = {'core:cred_reveal'}

    role = Role.objects.create(name='PlainTechRoutes')  # no can_manage_settings
    tech = User.objects.create_user(username='routetech', password='x', role_obj=role)
    client.force_login(tech)

    checked, failures, ungated = 0, [], []
    for pattern in get_resolver().url_patterns:
        for sub in getattr(pattern, 'url_patterns', [pattern]):
            route = str(sub.pattern)
            if not route.startswith('settings/'):
                continue
            checked += 1

            # Structural: the view class itself must carry the gate.
            name = f'core:{sub.name}' if sub.name else str(sub.pattern)
            view_class = getattr(sub.callback, 'view_class', None)
            if name not in STRUCTURAL_EXCEPTIONS:
                if view_class is None:
                    ungated.append(f'{name} ({route}) is not a class-based view')
                elif not issubclass(view_class, SettingsAdminMixin):
                    ungated.append(f'{name} ({route}) -> {view_class.__name__} '
                                   'does not inherit SettingsAdminMixin')

            # Behavioural second layer: a tech must not actually get through.
            url = '/' + re.sub(r'<(int|str):[a-z_]+>', '1', route)
            if '<' in url:
                continue
            for method in ('get', 'post'):
                resp = getattr(client, method)(url)
                # 403 = gate fired. 404/405 = route exists but not reachable this
                # way. Anything 2xx/3xx means a tech got through.
                if resp.status_code not in (403, 404, 405):
                    failures.append(f'{method.upper()} {url} -> {resp.status_code}')

    assert checked >= 50, f'enumeration found only {checked} settings routes'
    assert not ungated, 'settings routes missing SettingsAdminMixin:\n' + '\n'.join(ungated)
    assert not failures, 'non-admin reached settings routes:\n' + '\n'.join(failures)


@pytest.mark.django_db
def test_settings_admin_without_is_staff_can_manage_suppressed_addresses(client, db):
    """A can_manage_settings admin must not be blocked by a leftover is_staff check.

    Both suppressed-address views kept a hand-rolled `is_staff` check inside a
    SettingsAdminMixin view. The mixin authorizes superuser OR the role flag, so
    the inner check silently narrowed those two routes to Django-staff only: a
    shop that delegates settings to a non-staff role admin got a 403 on the email
    suppression list alone, with every neighbouring settings page working.
    """
    from core.models import Role, SuppressedAddress

    role = Role.objects.create(name='SettingsAdmin', can_manage_settings=True)
    admin = User.objects.create_user(username='rolesadmin', password='x', role_obj=role)
    assert not admin.is_staff  # the whole point of the case
    client.force_login(admin)

    resp = client.post(reverse('core:suppressed_address_add'),
                       {'email': 'noisy@example.com', 'reason': 'bounces'})
    assert resp.status_code == 302
    entry = SuppressedAddress.objects.get(email='noisy@example.com')

    resp = client.post(reverse('core:suppressed_address_delete', args=[entry.pk]))
    assert resp.status_code == 302
    assert not SuppressedAddress.objects.filter(pk=entry.pk).exists()


# ── In-app restore ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('name', [
    '../../etc/passwd',
    'backups/../../../etc/shadow',
    '/etc/passwd',
    'mb-backup-20260801-120000.tar.gz; rm -rf /',
    'mb-backup-$(whoami).tar.gz',
    'mb-backup-20260801-120000.tar.gz ',
    'evil.tar.gz',
    'mb-backup-2026-08-01.tar.gz',
    '',
])
def test_restore_refuses_anything_that_is_not_a_bare_archive_name(name):
    """The archive name reaches a shell script, so the boundary must be strict.

    An admin who can reach the restore view can already replace the database, so
    this is not about privilege — it is about not letting a filename become an
    arbitrary path or a second command.
    """
    from core import restore_ops
    assert not restore_ops.is_valid_archive_name(name), f'accepted {name!r}'


@pytest.mark.parametrize('name', [
    'mb-backup-20260801-120000.tar.gz',
    'preupdate-20260731-235959.tar.gz',
])
def test_restore_accepts_real_archive_names(name):
    from core import restore_ops
    assert restore_ops.is_valid_archive_name(name)


@pytest.mark.django_db
def test_restore_request_writes_trigger_and_rejects_bad_input(tmp_path, settings, admin_user):
    """A valid request drops the trigger the .path unit watches; a bad one does not.

    The trigger must be written LAST — its appearance is what starts the one-shot,
    so a half-written request must never be able to fire one.
    """
    from unittest.mock import patch
    from core import restore_ops

    logs = tmp_path / 'logs'
    logs.mkdir()
    with patch.object(restore_ops.backup_ops, '_logs_dir', lambda: logs):
        with pytest.raises(ValueError):
            restore_ops.request_restore('offsite', '../../etc/passwd')
        assert not restore_ops.trigger_path().exists(), 'a refused request still armed the trigger'

        with pytest.raises(ValueError):
            restore_ops.request_restore('somewhere-else', 'mb-backup-20260801-120000.tar.gz')
        assert not restore_ops.trigger_path().exists()

        restore_ops.request_restore('offsite', 'mb-backup-20260801-120000.tar.gz')
        payload = json.loads(restore_ops.trigger_path().read_text())
        assert payload == {'source': 'offsite', 'archive': 'mb-backup-20260801-120000.tar.gz'}
        assert restore_ops.is_running()

        # No second restore may be queued on top of a running one.
        with pytest.raises(ValueError):
            restore_ops.request_restore('local', 'mb-backup-20260801-130000.tar.gz')


@pytest.mark.django_db
def test_restore_refuses_a_submission_with_nothing_selected(client, admin_user, tmp_path):
    """The disabled button is client-side only; the server must refuse too.

    Choosing and restoring are two separate acts, so "restore" with no selection
    is a real submission shape (JS off, a stale fragment, a crafted POST) and it
    must not fall through to some default archive.
    """
    from unittest.mock import patch
    from core import restore_ops

    logs = tmp_path / 'logs'
    logs.mkdir()
    client.force_login(admin_user)
    with patch.object(restore_ops.backup_ops, '_logs_dir', lambda: logs):
        resp = client.post(reverse('core:restore_run'), {'source': '', 'archive': ''})
        assert resp.status_code == 200
        assert not restore_ops.trigger_path().exists()


def test_restore_reads_the_backup_time_from_the_name_not_the_file():
    """The user needs "how much work am I about to lose?", and a copy to a NAS or
    S3 rewrites mtime — so the timestamp must come from the name mb_backup.sh
    baked in."""
    from core import restore_ops

    dt = restore_ops.taken_at('mb-backup-20260801-184430.tar.gz')
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 8, 1, 18, 44)

    assert restore_ops.taken_at('preupdate-20260731-235959.tar.gz') is not None
    # Unparseable must degrade to None, never raise into the list view.
    assert restore_ops.taken_at('mb-backup-20261301-999999.tar.gz') is None
    assert restore_ops.taken_at('nonsense') is None
    assert restore_ops.taken_at('') is None


def test_restore_ui_separates_choosing_from_restoring():
    """A per-row Restore button made picking the wrong backup and running it the
    same click, on rows that are near-identical timestamps."""
    from pathlib import Path
    tpl = (Path(__file__).resolve().parent / 'templates' / 'core' / 'partials'
           / 'restore_archives.html').read_text()

    # Selection is its own control, and the action button is gated on it.
    assert 'type="radio"' in tpl
    assert ':disabled="!chosen' in tpl
    # The confirmation names the chosen backup rather than asking a generic question.
    assert 'chosenLabel' in tpl
    # And the consequence is stated, including that it is not a one-click undo.
    assert 'will be gone' in tpl
    assert 'pre-restore' in tpl


@pytest.mark.django_db
def test_restore_views_are_admin_only(client, db):
    """All three restore routes live under settings/ and must be gated."""
    from core.models import Role

    role = Role.objects.create(name='PlainTechRestore')  # no can_manage_settings
    tech = User.objects.create_user(username='restoretech', password='x', role_obj=role)
    client.force_login(tech)

    assert client.get(reverse('core:restore_archives')).status_code == 403
    assert client.get(reverse('core:restore_status')).status_code == 403
    assert client.post(reverse('core:restore_run'),
                       {'source': 'local',
                        'archive': 'mb-backup-20260801-120000.tar.gz'}).status_code == 403


@pytest.mark.django_db
def test_restore_run_rejects_a_forged_archive_name_from_the_browser(client, admin_user, tmp_path):
    """A crafted POST must not arm the trigger, and must say so rather than 500."""
    from unittest.mock import patch
    from core import restore_ops

    logs = tmp_path / 'logs'
    logs.mkdir()
    client.force_login(admin_user)
    with patch.object(restore_ops.backup_ops, '_logs_dir', lambda: logs):
        resp = client.post(reverse('core:restore_run'),
                           {'source': 'local', 'archive': '../../../etc/passwd'})
        assert resp.status_code == 200
        assert not restore_ops.trigger_path().exists()


@pytest.mark.django_db
def test_restore_archive_list_survives_an_unreachable_destination(tmp_path):
    """One dead remote must not hide the archives sitting on the other one.

    A restore screen is used on the worst day; a destination being unreachable is
    exactly when it must still show what it can and say what it could not read.
    """
    from unittest.mock import patch
    from core import restore_ops
    from core.models import SiteSettings

    site = SiteSettings.get()
    backups = tmp_path / 'backups'
    backups.mkdir()
    (backups / 'mb-backup-20260801-120000.tar.gz').write_bytes(b'x')
    (backups / 'not-a-backup.txt').write_bytes(b'x')

    with patch.object(restore_ops, 'backups_dir', lambda: backups), \
         patch.object(restore_ops, '_list_remote',
                      lambda s, source: ([], f'Could not list {source}: host is down')):
        archives, errors = restore_ops.list_archives(site)

    assert [a['name'] for a in archives] == ['mb-backup-20260801-120000.tar.gz']
    assert len(errors) == 2 and all('host is down' in e for e in errors)


def test_restore_script_env_handling_is_automatic_and_safe():
    """--with-env must be automatic on a fresh box and NEVER silent on a live one.

    Adopting the bundled .env unconditionally would overwrite working secrets on a
    same-box rollback; requiring a flag meant a fresh-box disaster recovery failed
    on a forgotten argument. So: adopt only when there is no live .env.
    """
    from pathlib import Path
    sh = Path(__file__).resolve().parent.parent / 'scripts' / 'restore.sh'
    text = sh.read_text()
    assert 'WITH_ENV=auto' in text
    assert '--keep-env' in text, 'no way to force keeping the live .env'
    # The auto branch must require BOTH "no live .env" and "archive has one".
    assert '[ ! -f "$APP/.env" ] && [ -f "$WORK/.env" ]' in text
    # And it must actually verify the key rather than telling the user to watch
    # for garbled credentials.
    assert 'ENCRYPTED DATA WILL NOT DECRYPT' in text


def test_restore_units_are_templates_and_registered():
    """The units must ship as templates AND be installed and gated.

    A unit that exists in deploy/ but is never installed is the exact defect that
    left in-app buttons spinning forever on every box but the author's.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent

    for unit in ('murphys-bench-restore.path', 'murphys-bench-restore.service'):
        text = (root / 'deploy' / unit).read_text()
        assert '__APP__' in text, f'{unit} is not a template'
        assert '/opt/murphys-bench' not in text, f'{unit} hardcodes the author path'


def test_restore_units_are_installed():
    """Registration moved out of install_units.sh into deploy/manifest.sh, which
    the installer, the verifier and the update-time drift check all read. The
    assertion is unchanged: these units must actually be installed."""
    installed = _manifest_array('MB_UNITS')
    missing = [u for u in ('murphys-bench-restore.path', 'murphys-bench-restore.service')
               if u not in installed]
    assert not missing, (
        f'{missing} not in MB_UNITS, so nothing installs them and the Restore '
        'button queues a job nothing picks up'
    )


def test_restore_path_unit_is_enabled():
    assert 'murphys-bench-restore.path' in _manifest_array('MB_UNITS_ENABLE'), (
        'the restore .path unit is installed but never enabled, so the trigger '
        'file it watches for is never noticed'
    )


def test_run_restore_wrapper_revalidates_the_archive_name():
    """Defence in depth: the wrapper is what turns the name into a path."""
    from pathlib import Path
    sh = Path(__file__).resolve().parent.parent / 'scripts' / 'run_restore.sh'
    text = sh.read_text()
    assert '^(mb-backup|preupdate)-[0-9]{8}-[0-9]{6}\\.tar\\.gz$' in text
    assert 'RESTORE_YES=1' in text, 'the wrapper would hang on the interactive prompt'


# ── Operational-data registry ───────────────────────────────────────────────

@pytest.mark.django_db
def test_operational_registry_classifies_every_model(db):
    """Every core model must be classified as operational or explicitly not.

    This is the forcing function that makes core.operational_data a registry
    rather than a third hand-maintained list. seed_demo_data and
    reset_operational_data used to keep separate lists of what counts as
    operational data; adding a model meant remembering two files, and nothing
    failed if you only remembered one. The consequences were asymmetric and both
    bad: a seeder that misses a model injects demo records into a working shop,
    and a reset that misses one leaves real records behind while telling the user
    the box is clean.

    So a new model now fails this test until someone decides which it is. Adding
    it to NON_OPERATIONAL with a reason is a perfectly good answer — the point is
    that the decision is made and visible, not that everything gets wiped.
    """
    from django.apps import apps
    from core import operational_data

    registered = {e.model for e in operational_data.REGISTRY}
    classified = registered | set(operational_data.NON_OPERATIONAL)

    unclassified = [
        f'core.{model.__name__}'
        for model in apps.get_app_config('core').get_models()
        if f'core.{model.__name__}' not in classified
    ]

    assert not unclassified, (
        'These core models are in neither core.operational_data.REGISTRY nor '
        'NON_OPERATIONAL, so seed_demo_data and reset_operational_data both '
        'ignore them:\n  ' + '\n  '.join(sorted(unclassified))
    )


@pytest.mark.django_db
def test_operational_registry_deletion_plan_is_resolvable_and_ordered(db):
    """The deletion plan must resolve to real models in a stable order.

    A typo'd dotted path would otherwise surface only when someone ran a real
    reset on a real box, which is the worst possible time to find out.
    """
    from core import operational_data

    plan = operational_data.deletion_plan()
    assert plan, 'deletion plan is empty'

    # Client deletes last among the registry entries: it cascades to contacts,
    # tickets and everything under them, so anything that must be deleted
    # explicitly has to go first.
    labels = [label for label, _model in plan]

    # The audit log records this command's own deletions, so it must be wiped
    # after everything it would record — including Client, which cascades to
    # tickets and replies. See the LogEntry entry in the registry.
    assert labels[-1] == 'Audit-log entries', (
        f'audit log must delete last, got {labels[-1]}')
    assert labels.index('Clients') < labels.index('Audit-log entries')

    # Client still deletes after everything that cannot rely on cascading.
    assert labels[-2] == 'Clients', f'Clients must delete after the rest, got {labels[-2]}'

    # Every entry resolves (raises LookupError otherwise) and is a real model.
    for _label, model in plan:
        assert hasattr(model, 'objects'), f'{model} is not a manager-bearing model'

    # Nothing that only cascades should be in the plan.
    planned = {m.__name__ for _l, m in plan}
    assert 'Contact' not in planned, 'Contact cascades from Client, do not delete it explicitly'
    assert 'Ticket' not in planned, 'Ticket cascades from Client, do not delete it explicitly'


@pytest.mark.django_db
def test_settings_routes_still_work_for_admin(client, admin_user):
    """The gate must not have locked admins out of their own settings page."""
    client.force_login(admin_user)
    assert client.get('/settings/').status_code == 200


@pytest.mark.django_db
def test_canned_response_picker_still_reachable_by_tech(client, db):
    """Moved OUT of settings/ deliberately: it is fetched from the work order page
    by ordinary techs. Blanket-gating the whole prefix would have broken the bench."""
    from core.models import Role
    role = Role.objects.create(name='PickerTech')
    tech = User.objects.create_user(username='pickertech', password='x', role_obj=role)
    client.force_login(tech)
    resp = client.get(reverse('core:cr_picker'), {'stream': 'customer'})
    assert resp.status_code == 200


# ── P0-3: Django admin requires a verified OTP session ──────────────────────

@pytest.mark.django_db
def test_admin_denied_to_authenticated_unverified_superuser(client, admin_user):
    """Password-only staff session must not reach admin. Pre-fix this was a 200:
    the MFA middleware exempted /admin/ and stock admin auth checks no OTP."""
    client.force_login(admin_user)
    resp = client.get('/admin/')
    # Denied at the index, and the whole chain must end at enrolment rather than
    # rendering any admin page or looping.
    assert resp.status_code == 302
    chain = client.get('/admin/', follow=True)
    final_url = chain.redirect_chain[-1][0]
    assert 'two_factor/setup' in final_url, chain.redirect_chain
    assert b'<div id="content-main">' not in chain.content


@pytest.mark.django_db
def test_admin_allowed_for_otp_verified_superuser(client, admin_user):
    """A verified session gets in — the gate checks verification, not just staff."""
    from django_otp.plugins.otp_totp.models import TOTPDevice
    TOTPDevice.objects.create(user=admin_user, name='d', confirmed=True)
    client.force_login(admin_user)
    session = client.session
    session['otp_device_id'] = str(TOTPDevice.objects.get(user=admin_user).persistent_id)
    session.save()
    resp = client.get('/admin/')
    assert resp.status_code == 200


@pytest.mark.django_db
def test_admin_otp_required_even_when_site_mfa_disabled(client, admin_user):
    """UNCONDITIONAL by decision (Mike, July 29 2026): admin can rewrite any record
    and read the decrypted credential vault, so it is gated regardless of the
    site-wide require_mfa toggle. require_mfa governs the app; this governs the keys."""
    site = SiteSettings.get()
    site.require_mfa = False
    site.save()
    client.force_login(admin_user)
    assert client.get('/admin/').status_code == 302


@pytest.mark.django_db
def test_unenrolled_superuser_is_routed_to_setup_not_a_loop(client, admin_user):
    """THE lockout trap. Upstream AdminSiteOTPRequired sends an unverified user to
    LOGIN_REDIRECT_URL; for a superuser with NO device that is a silent bounce
    between /admin/ and / with no way to enrol — the same failure shape as the
    July 2026 setup-wizard bug. They must land on the enrolment wizard."""
    client.force_login(admin_user)
    resp = client.get('/admin/login/')
    assert resp.status_code == 302
    assert 'two_factor/setup' in resp['Location']


# ── Email test views: relay removal + no reflected input ────────────────────

@pytest.mark.django_db
def test_outbound_email_test_ignores_browser_supplied_recipient(client, admin_user, monkeypatch):
    """The relay. This view used to mail ANY address the browser supplied using the
    shop's stored SMTP credentials, so any logged-in user could send as the shop and
    burn its domain reputation. The recipient is now derived server-side and the
    request body is not consulted at all."""
    admin_user.email = 'owner@example.com'
    admin_user.save()
    site = SiteSettings.get()
    site.email_host, site.email_username = 'smtp.example.com', 'user'
    site.save()

    sent_to = {}

    class FakeSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **kw): pass
        def login(self, *a): pass
        def sendmail(self, frm, to, msg): sent_to['to'] = to

    monkeypatch.setattr('smtplib.SMTP', FakeSMTP)
    client.force_login(admin_user)
    resp = client.post(reverse('core:settings_test_outbound'),
                       {'to': 'attacker@evil.example'})

    assert resp.status_code == 200
    assert sent_to['to'] == ['owner@example.com']
    assert 'evil.example' not in resp.content.decode()


@pytest.mark.django_db
def test_outbound_email_test_does_not_leak_smtp_error(client, admin_user, monkeypatch):
    """Failure text used to be echoed to the browser; SMTP errors routinely carry the
    server banner, software version and sometimes the login name."""
    admin_user.email = 'owner@example.com'
    admin_user.save()
    site = SiteSettings.get()
    site.email_host, site.email_username = 'smtp.example.com', 'user'
    site.save()

    def boom(*a, **kw):
        raise Exception('535 mail.internal.example: auth failed for user svc-mailer')

    monkeypatch.setattr('smtplib.SMTP', boom)
    client.force_login(admin_user)
    resp = client.post(reverse('core:settings_test_outbound'))
    body = resp.content.decode()

    assert resp.status_code == 200
    assert 'svc-mailer' not in body
    assert 'mail.internal.example' not in body


@pytest.mark.django_db
def test_email_test_views_are_admin_only(client, db):
    """Both were LoginRequired-only, so any tech could trigger them."""
    from core.models import Role
    role = Role.objects.create(name='MailTech')
    tech = User.objects.create_user(username='mailtech', password='x', role_obj=role)
    client.force_login(tech)
    assert client.post(reverse('core:settings_test_outbound')).status_code == 403
    assert client.post(reverse('core:settings_test_inbound')).status_code == 403


@pytest.mark.django_db
def test_account_security_is_linked_from_the_admin_settings_nav(client, admin_user):
    """Backup codes must be reachable — but from the admin section, not the sidebar.

    The page had been dropped from the sidebar in the session-20 nav redesign and
    was linked from exactly one sentence of body text on /users/, so there was no
    discoverable way to generate MFA backup codes at all. That is load-bearing now
    that Django admin requires OTP. Placement is Settings → Access & Security
    (Mike, July 29 2026): backup codes are an administrative capability.
    """
    client.force_login(admin_user)
    resp = client.get('/settings/?tab=security')
    assert resp.status_code == 200
    assert reverse('two_factor:profile').encode() in resp.content


@pytest.mark.django_db
def test_account_security_is_not_in_the_sidebar(client, client_obj, admin_user):
    """Deliberately absent from the sidebar — it belongs to the admin section."""
    client.force_login(admin_user)
    resp = client.get(reverse('core:dashboard'))
    assert resp.status_code == 200
    assert reverse('two_factor:profile').encode() not in resp.content


@pytest.mark.django_db
def test_backup_tokens_denied_to_non_admin(client, db):
    """No employee generates their own backup codes (Mike, July 29 2026).

    Pre-fix ANY user with a confirmed device got 200 here, despite CLAUDE.md having
    claimed "backup codes for admin only" since Batch 8 — a documented control that
    was never implemented. Self-service codes would also route around the
    accountability of an admin reset, which writes an MFAResetLog entry.

    Guards the URL override in murphys_bench/urls.py: if that route stops being
    registered ahead of two_factor's own urlpatterns, upstream's ungated view
    silently takes over and this test fails.
    """
    from django_otp.plugins.otp_totp.models import TOTPDevice
    from core.models import Role
    role = Role.objects.create(name='BackupTech')
    tech = User.objects.create_user(username='backuptech', password='x', role_obj=role)
    device = TOTPDevice.objects.create(user=tech, name='d', confirmed=True)

    client.force_login(tech)
    session = client.session
    session['otp_device_id'] = str(device.persistent_id)
    session.save()

    for method in ('get', 'post'):
        resp = getattr(client, method)(reverse('two_factor:backup_tokens'))
        assert resp.status_code == 403, f'{method} returned {resp.status_code}'


@pytest.mark.django_db
def test_backup_tokens_allowed_for_admin(client, admin_user):
    """The admin path must still work — this is the recovery capability itself."""
    from django_otp.plugins.otp_totp.models import TOTPDevice
    device = TOTPDevice.objects.create(user=admin_user, name='d', confirmed=True)
    client.force_login(admin_user)
    session = client.session
    session['otp_device_id'] = str(device.persistent_id)
    session.save()
    assert client.get(reverse('two_factor:backup_tokens')).status_code == 200


# NOTE: the old test_backup_tokens_button_hidden_from_non_admin was removed here,
# superseded by test_two_factor_profile_and_disable_are_admin_only below: the whole
# profile page is now 403 for employees, so there is no page on which to hide a
# button. The template guard is kept as defence in depth, not as the gate.


# ── Employees see nothing from the admin back end (Mike, July 29 2026) ──────

@pytest.fixture
def enrolled_tech(db):
    """A non-admin with a confirmed authenticator and a verified session."""
    from core.models import Role
    role = Role.objects.create(name='SecTech')
    return User.objects.create_user(username='sectech', password='x', role_obj=role)


def _verify(client, user):
    from django_otp.plugins.otp_totp.models import TOTPDevice
    device = TOTPDevice.objects.create(user=user, name='Authenticator', confirmed=True)
    client.force_login(user)
    session = client.session
    session['otp_device_id'] = str(device.persistent_id)
    session.save()
    return device


@pytest.mark.django_db
def test_two_factor_profile_and_disable_are_admin_only(client, enrolled_tech):
    """The stock two_factor profile page is an admin-section page: it carries backup
    codes and Disable 2FA. Employees must not reach it at all — they get
    core:my_security. Guards the URL overrides in murphys_bench/urls.py; if those
    stop being registered ahead of two_factor's include, upstream's ungated views
    silently take over and this fails."""
    _verify(client, enrolled_tech)
    assert client.get(reverse('two_factor:profile')).status_code == 403
    assert client.get(reverse('two_factor:disable')).status_code == 403


@pytest.mark.django_db
def test_admin_can_still_reach_account_security(client, admin_user):
    _verify(client, admin_user)
    assert client.get(reverse('two_factor:profile')).status_code == 200


@pytest.mark.django_db
def test_employee_security_page_shows_status_without_admin_controls(client, enrolled_tech):
    """My Security reports state and offers nothing administrative: no backup codes,
    no disable. Recovery is an admin reset, which leaves an MFAResetLog entry."""
    _verify(client, enrolled_tech)
    resp = client.get(reverse('core:my_security'))
    body = resp.content.decode()
    assert resp.status_code == 200
    assert 'Two-factor authentication is on' in body
    assert reverse('two_factor:backup_tokens') not in body
    assert reverse('two_factor:disable') not in body
    assert reverse('two_factor:profile') not in body


@pytest.mark.django_db
def test_employee_security_page_offers_setup_when_unenrolled(client, enrolled_tech):
    client.force_login(enrolled_tech)
    resp = client.get(reverse('core:my_security'))
    assert resp.status_code == 200
    assert 'not set up' in resp.content.decode()


@pytest.mark.django_db
def test_sidebar_shows_my_security_to_employees_only(client, client_obj, enrolled_tech, admin_user):
    """Employees get My Security in the sidebar; admins use Settings → Account
    Security, so the sidebar entry would be a second door to a different page."""
    _verify(client, enrolled_tech)
    tech_body = client.get(reverse('core:dashboard')).content.decode()
    assert reverse('core:my_security') in tech_body

    client.logout()
    _verify(client, admin_user)
    admin_body = client.get(reverse('core:dashboard')).content.decode()
    assert reverse('core:my_security') not in admin_body


@pytest.mark.django_db
def test_admin_otp_lockout_recovery_drill(client):
    """END-TO-END: lose the authenticator, get locked out of /admin/, recover.

    Kept as a test rather than a one-off script because this is the ONLY way back
    into an OTP-required admin, so it has to keep working. Proves the full path
    Mike approved on July 29 2026 before the unconditional admin-OTP gate shipped.
    """
    from django_otp.plugins.otp_totp.models import TOTPDevice
    from django_otp import devices_for_user
    from core.models import MFAResetLog
    from django.core.management import call_command

    admin = User.objects.create_user(username='drilladmin', password='x',
                                     is_staff=True, is_superuser=True)

    # 1. Enrolled and OTP-verified: admin works.
    device = TOTPDevice.objects.create(user=admin, name='Bitwarden', confirmed=True)
    client.force_login(admin)
    session = client.session
    session['otp_device_id'] = str(device.persistent_id)
    session.save()
    assert client.get('/admin/').status_code == 200

    # 2. Authenticator lost — the device row still exists, but no session can be
    #    verified against it. Admin is closed.
    client.logout()
    client.force_login(admin)  # password-only
    assert client.get('/admin/').status_code == 302

    # 3. No self-rescue: backup codes need a verified session too.
    assert client.get(reverse('two_factor:backup_tokens')).status_code == 302

    # 4. The recovery: the real management command, run on the box. Clears devices
    #    and writes an audit record stamped with the shell identity.
    before = MFAResetLog.objects.count()
    call_command('reset_mfa', 'drilladmin', '--note', 'drill: lost authenticator')
    assert list(devices_for_user(admin)) == []
    assert MFAResetLog.objects.count() == before + 1
    assert MFAResetLog.objects.latest('id').source == 'cli'

    # 5. With no device, /admin/ now routes to ENROLMENT rather than looping.
    client.logout()
    client.force_login(admin)
    resp = client.get('/admin/login/')
    assert resp.status_code == 302
    assert 'two_factor/setup' in resp['Location']

    # 6. Re-enrol -> access restored.
    new_device = TOTPDevice.objects.create(user=admin, name='Bitwarden (new)', confirmed=True)
    client.logout()
    client.force_login(admin)
    session = client.session
    session['otp_device_id'] = str(new_device.persistent_id)
    session.save()
    assert client.get('/admin/').status_code == 200


# ── Shipped security defaults (July 2026 review, slice 2) ───────────────────
#
# These assert what a STRANGER gets after scripts/install.sh, not what the
# author's two boxes have in their .env. Both of these were correct in the
# author's deployment and wrong as shipped defaults, which is the whole finding.

def test_csp_ships_enforcing_not_report_only():
    """CSP defaulted to report-only, so it enforced nothing on every install except
    the two whose .env said otherwise — the author's. A header that protects only
    its author is not a shipped control."""
    from django.conf import settings
    assert settings.CSP_REPORT_ONLY is False
    assert "default-src 'self'" in settings.CSP_POLICY
    assert "frame-ancestors 'none'" in settings.CSP_POLICY


def test_proxy_header_trust_is_off_by_default():
    """X-Forwarded-Proto/Host are forgeable by anyone who can reach the app port.
    Trusting them unconditionally let an unproxied deployment believe an
    attacker-supplied Host, poisoning absolute URLs in outbound email."""
    from django.conf import settings
    assert settings.TRUST_PROXY_HEADERS is False
    assert getattr(settings, 'SECURE_PROXY_SSL_HEADER', None) is None
    assert getattr(settings, 'USE_X_FORWARDED_HOST', False) is False


def test_installer_writes_both_security_defaults_explicitly():
    """The installer must state these in the generated .env rather than leaning on a
    settings default a later release could change underneath an existing install —
    the same reasoning as the explicit cookie flags already there."""
    from pathlib import Path
    installer = (Path(__file__).resolve().parent.parent / 'scripts' / 'install.sh').read_text()
    assert 'TRUST_PROXY_HEADERS=False' in installer
    assert 'CSP_REPORT_ONLY' in installer


def test_installer_http_checks_retry_and_verify_the_app_not_just_static():
    """The installer's own success checks must not lie, and must not lie flakily.

    Two defects, both found by rebuilding mb-test from the current installer:

    1. The v0.4.52 static probe curled once, immediately after `systemctl reload
       nginx`. That reload returns when the signal is sent; nginx drains old workers
       asynchronously, so the probe can be answered by a worker still holding the
       old config. On a real install it duly reported "INSTALL FAILED ... HTTP 404"
       while the identical request returned 200 moments later. An intermittent false
       failure is worse than no check: it teaches people to ignore the installer.

    2. /static/ is an nginx alias served straight off disk, so that probe passes
       with gunicorn completely dead — verified on the box, static 200 / app 502.
       That is how a re-run printed DONE and "running at ..." over an app returning
       502 to every request.
    """
    from pathlib import Path
    sh = (Path(__file__).resolve().parent.parent / 'scripts' / 'install.sh').read_text()

    # A retrying probe helper exists and the static check goes through it.
    assert 'http_probe()' in sh
    assert 'code="$(http_probe' in sh
    # The app itself is verified through nginx, not only static files.
    assert "http_probe \"http://127.0.0.1/\" '2*|3*'" in sh
    # And the app is restarted first: `enable --now` does not restart a running
    # service, so a re-run that changed the unit would leave the old process bound.
    assert 'systemctl restart murphys-bench' in sh
    # 403 and 404 must not share one guess-the-cause message.
    assert 'static_probe_hint' in sh
    # No single-shot curl left behind that could race a reload.
    assert "code=\"$(curl -s -o /dev/null -w '%{http_code}' \"http://127.0.0.1/static" not in sh
# ══ seed_demo_data — fake demo/evaluation data ══════════════════════════════

@pytest.mark.django_db
def test_seed_demo_data_creates_a_coherent_workflow(settings):
    """One command must replace SETUP.md §10's 8-step manual checklist, and the
    result has to exercise the actual spine — a converted ticket with a work order
    carrying priced lines — not just isolated rows."""
    from django.core.management import call_command
    from core.models import Client, Contract, Device, LineItem, Sale
    settings.DEBUG = True

    call_command('seed_demo_data', verbosity=0)

    real = Client.objects.filter(is_unsorted=False)   # the system Unsorted bucket
    assert real.filter(client_type='business').count() == 2   # is residential-typed
    assert real.filter(client_type='residential').count() == 1
    # The spine: a ticket converted to a work order.
    wo = WorkOrder.objects.get(ticket__isnull=False)
    assert wo.ticket.status == 'converted'
    assert wo.line_items_total > 0
    # A standalone WO too — work does not always arrive as a ticket.
    assert WorkOrder.objects.filter(ticket__isnull=True).exists()
    # Managed lane and counter lane both represented.
    assert Contract.objects.filter(status='active').exists()
    assert Sale.objects.filter(client__isnull=True).exists()
    # The encrypted-credential path is exercised.
    assert Device.objects.exclude(device_password='').exists()
    assert LineItem.objects.filter(kind='part').exists()


@pytest.mark.django_db
def test_seed_demo_data_refuses_on_a_production_install(settings):
    """DEBUG=False means this looks like a real install. Fake client records mixed
    into a real client list have to be cleaned up by hand, so refuse."""
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from core.models import Client
    settings.DEBUG = False

    with pytest.raises(CommandError, match='DEBUG=False'):
        call_command('seed_demo_data', verbosity=0)
    assert not Client.objects.filter(is_unsorted=False).exists()


@pytest.mark.django_db
def test_seed_demo_data_refuses_when_clients_already_exist(settings, client_obj):
    """Guard 3, independent of DEBUG: never interleave demo records with data that
    is already here. reset_operational_data is the way to clear a test box.

    Declining exits 3, not 1 — see the exit-code test below for why that matters.
    """
    from django.core.management import call_command
    settings.DEBUG = True

    with pytest.raises(SystemExit) as exc:
        call_command('seed_demo_data', verbosity=0)
    assert exc.value.code == 3


@pytest.mark.django_db
def test_seed_demo_data_refuses_on_a_clientless_shop_with_only_counter_sales(settings):
    """THE regression test for the July 30 2026 review finding.

    MB supports clientless work, so a shop doing only walk-in counter sales sits at
    zero clients forever. The guard used to count Client alone, which meant an
    installer re-run would cheerfully seed fake records straight into that shop's
    live data while claiming it could not.
    """
    from django.core.management import call_command
    from core.models import Client, Sale
    settings.DEBUG = True

    Sale.objects.create(client=None)
    assert not Client.objects.filter(is_unsorted=False).exists()  # the old guard saw nothing

    with pytest.raises(SystemExit) as exc:
        call_command('seed_demo_data', '--new-install', verbosity=0)
    assert exc.value.code == 3
    assert not Client.objects.filter(is_unsorted=False).exists()


@pytest.mark.django_db
def test_seed_demo_data_refuses_once_the_install_is_marked_initialised(settings):
    """Guard 2, and the only one that works on an EMPTY database — which is exactly
    what a `--no-demo-data` install leaves behind. Without the marker, a re-run of
    install.sh on such a shop would seed fake data into a deliberately empty box."""
    from django.core.management import call_command
    from core.models import Client
    settings.DEBUG = True

    call_command('mark_install_initialized', verbosity=0)

    with pytest.raises(SystemExit) as exc:
        call_command('seed_demo_data', '--new-install', verbosity=0)
    assert exc.value.code == 3
    assert not Client.objects.filter(is_unsorted=False).exists()


@pytest.mark.django_db
def test_seed_decline_and_seed_failure_are_distinguishable(settings):
    """scripts/install.sh branches on this. A declined seed must exit 3 so it can be
    reported as a harmless no-op, and a genuine failure must NOT — the installer
    used to describe every nonzero exit as "already has client records", which was
    a false statement printed to the user whenever seeding actually broke."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    settings.DEBUG = True
    call_command('mark_install_initialized', verbosity=0)
    with pytest.raises(SystemExit) as declined:
        call_command('seed_demo_data', '--new-install', verbosity=0)
    assert declined.value.code == 3

    # A real error (here: a production-shaped install with no waiver) is a
    # CommandError, which manage.py exits 1 on — never mistaken for a re-run.
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command('seed_demo_data', verbosity=0)


@pytest.mark.django_db
def test_mark_install_initialized_is_idempotent():
    """A re-run of install.sh must not move the date — the first stamp is the truth."""
    from django.core.management import call_command
    from core.models import SiteSettings

    call_command('mark_install_initialized', verbosity=0)
    first = SiteSettings.get().install_initialized_at
    assert first is not None

    call_command('mark_install_initialized', verbosity=0)
    assert SiteSettings.get().install_initialized_at == first


@pytest.mark.django_db
def test_seed_demo_data_force_overrides_both_guards(settings, client_obj):
    """--force is the documented escape hatch for a box that genuinely is a test
    box but does not look like one."""
    from django.core.management import call_command
    from core.models import Client
    settings.DEBUG = False

    call_command('seed_demo_data', '--force', verbosity=0)
    assert Client.objects.count() > 1


@pytest.mark.django_db
def test_seeded_data_is_unmistakably_fake(settings):
    """MB is public and this data lands on demo boxes other people see. The July
    2026 hygiene pass had to scrub real prod IPs and a real name out of test
    fixtures; this locks the conventions so it cannot happen again.

    RFC 2606 reserves example.com/.org; 555 is reserved for fiction.
    """
    from django.core.management import call_command
    from core.models import Client, Contact
    settings.DEBUG = True
    call_command('seed_demo_data', verbosity=0)

    for email in list(Client.objects.exclude(email='').values_list('email', flat=True)) + \
                 list(Contact.objects.exclude(email='').values_list('email', flat=True)):
        assert email.endswith(('example.com', 'example.org')), email

    for phone in list(Client.objects.exclude(phone='').values_list('phone', flat=True)) + \
                 list(Contact.objects.exclude(phone='').values_list('phone', flat=True)):
        assert '555' in phone, phone

    # No real internal addresses may ever appear in seeded records.
    blob = ' '.join(Client.objects.values_list('address_line1', flat=True))
    assert '10.58.' not in blob


@pytest.mark.django_db
def test_seed_new_install_flag_waives_debug_but_not_existing_data(settings, client_obj):
    """scripts/install.sh writes DEBUG=False, so it must waive that guard to seed a
    fresh box at all. It must NOT waive the existing-data guard: install.sh is
    documented as safe to re-run over an existing install (it is the v0.4.52
    recovery path), and a re-run on a live shop must never inject demo records into
    real client data.
    """
    from django.core.management import call_command
    from core.models import Client
    settings.DEBUG = False

    # A client already exists (client_obj) -> refuse, even with --new-install.
    with pytest.raises(SystemExit) as exc:
        call_command('seed_demo_data', '--new-install', verbosity=0)
    assert exc.value.code == 3
    # The system Unsorted bucket is migration-created and always present, so count
    # real clients only — the same exclusion the command's own guard uses.
    assert Client.objects.filter(is_unsorted=False).count() == 1

    # Empty database + DEBUG=False -> the installer's case -> allowed.
    Client.objects.filter(is_unsorted=False).delete()
    call_command('seed_demo_data', '--new-install', verbosity=0)
    assert Client.objects.filter(is_unsorted=False).count() == 3


def test_installer_seeds_by_default_and_says_so():
    """The installer must seed a fresh box, must offer an opt-out, must use
    --new-install rather than --force (which would waive the existing-data guard),
    and must TELL the user the data is fake and how to clear it. Silence here is
    the failure mode: unexplained fake clients read as a botched import."""
    from pathlib import Path
    sh = (Path(__file__).resolve().parent.parent / 'scripts' / 'install.sh').read_text()

    assert 'SEED_DEMO=1' in sh                      # default on
    assert '--no-demo-data) SEED_DEMO=0' in sh      # opt-out exists
    assert 'seed_demo_data --new-install' in sh     # correct flag
    assert 'seed_demo_data --force' not in sh       # never the blanket bypass
    assert 'DEMO DATA IS PRESENT' in sh             # user is told
    assert 'reset_operational_data' in sh           # and told how to clear it


# ══ CI gate properties (July 2026 review, slice 3) ══════════════════════════

def _ci_workflow():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / '.github' / 'workflows' / 'ci.yml').read_text()


def test_ci_keeps_the_status_check_name_branch_protection_requires():
    """main's branch protection requires a status check named exactly "test".

    Turning the single `test` job into a matrix renamed its contexts to
    "test (3.12)" / "test (3.14)", so a check named "test" would never report again
    and every future PR would be silently unmergeable through the normal path — a
    gate quietly broken by the change meant to strengthen it. An aggregate job named
    "test" preserves the contract: green only when lint and every matrix leg passed.

    If the required check is ever renamed in branch protection, update this test with
    it — do not delete it.
    """
    ci = _ci_workflow()
    assert '\n  test:\n' in ci, 'a job named exactly "test" must exist'
    assert 'needs: [lint, suite]' in ci
    # It must actually fail when a dependency failed, not just run after it.
    assert 'needs.suite.result' in ci and 'needs.lint.result' in ci
    assert 'if: always()' in ci, 'must run even when a dependency failed, to report red'


def test_ci_tests_every_supported_python_runtime():
    """MB claims support for Ubuntu 24.04 (Python 3.12, what prod runs) AND 26.04
    (Python 3.14, what the installer targets and mb-test runs). CI tested only 3.12,
    so half of that support claim was verified once by hand on a throwaway VM that
    was then destroyed — the same "verified against a box we built" gap as the
    installer defects."""
    ci = _ci_workflow()
    assert "python-version: ['3.12', '3.14']" in ci
    assert 'fail-fast: false' in ci, 'one version failing must not hide the other'


def test_ci_runs_the_production_deployment_check():
    """`check --deploy` exists to catch exactly the misconfigurations self-hosters
    make, and CI ran plain `check` instead."""
    ci = _ci_workflow()
    assert 'check --deploy' in ci


def test_ci_lints_for_real_errors_and_blocks_on_them():
    """flake8 was pinned in requirements.txt and never run. Enforced on the error
    classes only (F, E9) — undefined names, dead imports, dead assignments, syntax
    errors. Style is deliberately not enforced yet; see the workflow comment.

    It must BLOCK. A non-failing check is the report-only-CSP mistake again.
    """
    ci = _ci_workflow()
    assert 'flake8 --select=F,E9' in ci
    assert 'continue-on-error' not in ci, 'a check that cannot fail is not a check'


def test_ci_actions_are_pinned_to_commit_shas():
    """A version tag can be repointed at new code by whoever controls the action's
    repository; a commit SHA cannot. This workflow runs on every push with repo
    read access, so it pins SHAs."""
    import re
    ci = _ci_workflow()
    uses = re.findall(r'uses:\s*(\S+)', ci)
    assert uses, 'expected at least one action'
    for ref in uses:
        assert '@' in ref, ref
        pinned = ref.split('@', 1)[1]
        assert re.fullmatch(r'[0-9a-f]{40}', pinned), \
            f'{ref} is not pinned to a 40-char commit SHA'


def test_no_dead_imports_or_undefined_names_in_the_codebase():
    """Runs the same gate locally so the tree stays clean between CI runs. This
    caught a 7-week-old dead DB query in WorkOrderUpdateView.form_valid, left behind
    when the AUTO_RESOLVE_TICKET_ON_WO_CLOSE block was deliberately removed."""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, '-m', 'flake8', '--select=F,E9',
         'core/', 'murphys_bench/', 'accounts/'],
        cwd=root, capture_output=True, text=True)
    if proc.returncode != 0 and 'No module named' in (proc.stderr or ''):
        import pytest as _pytest
        _pytest.skip('flake8 not installed in this environment')
    assert proc.returncode == 0, f'flake8 found real errors:\n{proc.stdout}'


@pytest.mark.django_db
def test_reset_operational_data_actually_clears_seeded_demo_data(settings):
    """The instruction shipped in CHANGELOG.md and in the installer's own output.

    It was NOT true. reset_operational_data was written in session 27 and never
    learned about Sale, Estimate, Prospect, Contract, Asset, PaymentChargeAttempt or
    Notification. None of those cascade reliably from Client — a counter sale and a
    prospect need no client at all, and an Estimate can anchor to a Prospect — so a
    seeded install cleared with the documented command still held SALE-00001 and its
    priced line item while the docs claimed everything was gone.

    Proven empirically before fixing, on a real box and on a scratch database.

    This test pairs the two commands so the promise cannot drift from the code again:
    seed, clear, then assert that NO operational record of any kind remains.
    """
    from django.apps import apps
    from django.core.management import call_command
    settings.DEBUG = True

    call_command('seed_demo_data', verbosity=0)
    call_command('reset_operational_data', '--confirm',
                 'DELETE ALL OPERATIONAL DATA', verbosity=0)

    operational = {
        'Client', 'Contact', 'ContactPhone', 'Device', 'Ticket', 'TicketReply',
        'TicketLink', 'TicketLock', 'TicketWorkLog', 'WorkOrder', 'WorkOrderNote',
        'WorkOrderItem', 'Invoice', 'Mileage', 'Sale', 'Estimate', 'EstimateOption',
        'Prospect', 'Contract', 'Asset', 'PaymentChargeAttempt', 'Notification',
        'LineItem', 'Attachment', 'CustomFieldValue', 'DeviceCredentialAccessLog',
    }
    survivors = {
        m.__name__: m.objects.count()
        for m in apps.get_app_config('core').get_models()
        if m.__name__ in operational and m.objects.count()
    }
    assert not survivors, f'operational records survived the documented clear: {survivors}'


@pytest.mark.django_db
def test_reset_operational_data_keeps_configuration_including_the_catalog(settings):
    """The other half of the promise: clearing must not cost a shop its setup.

    The Products & Services catalog is deliberately KEPT — a price list is
    configuration. Demo data seeds five catalog entries, so they survive too, which
    is why the command now reports them explicitly instead of letting a user believe
    'clear it all' removed everything.
    """
    from django.core.management import call_command
    from core.models import CatalogItem, RepairType, StatusDefinition, SiteSettings
    settings.DEBUG = True

    call_command('seed_demo_data', verbosity=0)
    call_command('reset_operational_data', '--confirm',
                 'DELETE ALL OPERATIONAL DATA', verbosity=0)

    assert CatalogItem.objects.count() == 5, 'the price list is configuration'
    assert RepairType.objects.exists()
    assert StatusDefinition.objects.exists()
    assert SiteSettings.objects.exists()


# ---------------------------------------------------------------------------
# The install-completeness marker must not outlive the problem
# ---------------------------------------------------------------------------

def _fake_install(tmp_path, marker_text=None):
    """A tree shaped like a real install, for running check_install.sh against.

    The script derives APP from its own location, so it is copied in rather than
    run from the repo — otherwise every one of these tests would write to the
    developer's own logs/ directory.
    """
    import shutil

    (tmp_path / 'scripts').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'deploy').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'logs').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'staticfiles' / 'css').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'staticfiles' / 'css' / 'app.css').write_text('body{}')

    src = _repo_root() / 'scripts' / 'check_install.sh'
    shutil.copy(src, tmp_path / 'scripts' / 'check_install.sh')
    shutil.copy(_repo_root() / 'deploy' / 'manifest.sh', tmp_path / 'deploy' / 'manifest.sh')

    if marker_text is not None:
        (tmp_path / 'logs' / 'update-incomplete').write_text(marker_text)
    return tmp_path / 'logs' / 'update-incomplete'


def _stub_tools(tmp_path, systemctl_exit=0, http_code='200'):
    """A PATH where systemctl and curl answer however the test needs.

    Stubbing the real binaries beats adding test-only switches to the script:
    what runs under test is then byte-identical to what runs on a server.
    """
    import os
    import stat

    binp = tmp_path / 'stubbin'
    binp.mkdir(exist_ok=True)
    (binp / 'systemctl').write_text('#!/bin/sh\nexit %d\n' % systemctl_exit)
    (binp / 'curl').write_text('#!/bin/sh\nprintf %s\n' % http_code)
    for f in ('systemctl', 'curl'):
        (binp / f).chmod((binp / f).stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    env = dict(os.environ)
    env['PATH'] = '%s:%s' % (binp, env.get('PATH', ''))
    return env


def _run_check(app, env, *args):
    import subprocess
    return subprocess.run(
        ['bash', str(app / 'scripts' / 'check_install.sh'), *args],
        capture_output=True, text=True, timeout=60, env=env,
    )


def test_repairing_the_install_clears_the_warning_that_asked_for_the_repair(tmp_path):
    """Running the fix must clear the banner that told you to run it.

    THE DEFECT, hit on prod 2026-08-05: update.sh was the only thing that ever
    wrote or deleted logs/update-incomplete. The marker tells the operator to run
    install_units.sh; install_units.sh did not touch the marker. So Mike ran the
    command the app asked for, his server was genuinely repaired, and the page
    went on saying "This install is incomplete" — with no way to clear it short
    of waiting for the next release.

    Same shape as v0.11.0's warning that never reached the UI: the fix reached the
    machine and not the product.
    """
    marker = _fake_install(tmp_path, marker_text='These system services are not installed:\n  murphys-bench-restore.path\n')
    env = _stub_tools(tmp_path)          # every unit present, stylesheet serves

    out = _run_check(tmp_path, env, '--no-static-probe')

    assert out.returncode == 0, f'the check must never fail its caller: {out.stderr}'
    assert not marker.exists(), (
        'the install is whole and the marker survived, so the app would keep '
        'reporting a problem that no longer exists — the exact prod defect'
    )


def test_repairing_the_units_does_not_clear_a_real_stylesheet_warning(tmp_path):
    """install_units.sh fixes units. It must not speak for the stylesheet.

    A box can be missing units AND serving unstyled pages. Installing the units
    repairs one of those, and silently dropping the other half would be the same
    class of lie as never showing it: the operator would see the banner clear and
    conclude the site was fixed.
    """
    stale = (
        'These system services are not installed:\n'
        '  murphys-bench-restore.path\n'
        "The web server cannot read this install's stylesheets (HTTP 403).\n"
        'Pages render as unstyled HTML with no logo.\n'
        '\n'
        'FIX: cd /opt/murphys-bench && scripts/install.sh\n'
    )
    marker = _fake_install(tmp_path, marker_text=stale)
    env = _stub_tools(tmp_path)          # units now all present

    out = _run_check(tmp_path, env, '--no-static-probe')

    assert out.returncode == 0, out.stderr
    assert marker.exists(), 'the stylesheet problem is real and was thrown away'
    body = marker.read_text()
    assert 'murphys-bench-restore.path' not in body, (
        'the units were repaired and the marker still names them as missing'
    )
    assert 'cannot read this install' in body, 'the surviving warning lost its text'
    assert 'scripts/install.sh' in body, (
        'a stylesheet problem needs the full installer; install_units.sh does not '
        'touch static permissions'
    )


def test_check_reports_missing_units_and_still_exits_clean(tmp_path):
    """The failing half, proven: a missing unit is named, and nothing dies.

    The exit code matters as much as the text. update.sh calls this after migrate,
    css and collectstatic have all succeeded, under `set -e` — a non-zero exit
    here reports a FAILED update on a box that updated perfectly, which is exactly
    what the clean-room gate caught on 2026-08-04.
    """
    marker = _fake_install(tmp_path)
    env = _stub_tools(tmp_path, systemctl_exit=1)   # nothing is installed

    out = _run_check(tmp_path, env, '--no-static-probe')

    assert out.returncode == 0, f'the check failed its caller: {out.stderr}'
    assert marker.exists(), 'units are missing and the marker was not written'
    body = marker.read_text()
    assert 'murphys-bench-restore.path' in body, body
    assert 'scripts/install_units.sh' in body, 'the marker must carry the fix'
    assert 'THIS INSTALL IS INCOMPLETE' in out.stderr, (
        'a terminal user must be told too, not only the app'
    )


def _call_block(script_name, first_line_startswith, after=None):
    """The lines of a script that invoke check_install.sh, ready to run.

    Asserting that a script CONTAINS the string "check_install.sh" is not a test:
    it passes when the call sits in a comment, in a dead branch, behind the wrong
    condition, or passes the wrong mode. That is exactly how the --skip-web
    regression below got past the first version of this file. So the real block is
    lifted out of the real script and executed.
    """
    lines = (_repo_root() / 'scripts' / script_name).read_text().splitlines()
    start = 0
    if after is not None:
        start = next(n for n, ln in enumerate(lines) if ln.startswith(after))
    i = next(n for n, ln in enumerate(lines[start:], start)
             if ln.startswith(first_line_startswith))
    if not lines[i].startswith('if '):
        return lines[i]
    depth, j = 0, i
    while j < len(lines):
        st = lines[j].strip()
        if st.startswith('if '):
            depth += 1
        elif st == 'fi':
            depth -= 1
            if depth == 0:
                return '\n'.join(lines[i:j + 1])
        j += 1
    raise AssertionError(f'unterminated if-block in {script_name}')


def _run_call_block(tmp_path, block, script_present=True, **env_vars):
    """Run a lifted call block against a stub check_install.sh that records itself."""
    import subprocess

    app = tmp_path / 'app'
    (app / 'scripts').mkdir(parents=True, exist_ok=True)
    (app / 'logs').mkdir(parents=True, exist_ok=True)
    record = app / 'logs' / 'called'
    if script_present:
        stub = app / 'scripts' / 'check_install.sh'
        stub.write_text('#!/bin/sh\necho "ARGS:[$*]" >> "%s"\n' % record)
        stub.chmod(0o755)

    setup = [f'{k}={v}' for k, v in env_vars.items()]
    prelude = '\n'.join([
        'set -uo pipefail',
        f'APP={app}',
        *setup,
        f'log() {{ echo "LOG:$*" >> {record}; }}',
    ])
    out = subprocess.run(['bash', '-c', prelude + '\n' + block],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f'the call block failed: {out.stderr}'
    return record.read_text() if record.exists() else ''


def test_a_skip_web_install_does_not_get_told_it_is_incomplete(tmp_path):
    """--skip-web installs no units and no web server. Nothing to check.

    THE REGRESSION, caught in review before this shipped: install.sh ran the check
    with --no-static-probe on a --skip-web box, believing that made it safe. It
    does not. That flag suppresses the stylesheet probe only; every unit is still
    checked, and a --skip-web box deliberately has none. The result was a marker
    naming all 14 units as missing, telling the operator to run install_units.sh,
    which is the standard systemd setup they explicitly opted out of.

    A permanent "This install is incomplete" on an install working exactly as
    documented is the same defect this branch exists to remove, aimed at a
    different user.
    """
    called = _run_call_block(tmp_path, _call_block('install.sh', 'if [ "$SKIP_WEB" = 0 ]; then', after='# 11b)'),
                             SKIP_WEB=1)

    assert 'ARGS:' not in called, (
        'the completeness check ran on a --skip-web install, so a correct custom '
        f'install is told it is broken. Recorded: {called!r}'
    )
    assert 'LOG:' in called, (
        'skipping it silently is its own failure: an operator who later wonders '
        'why nothing checks their install deserves the reason in the log'
    )


def test_a_standard_install_re_checks_everything_including_the_stylesheet(tmp_path):
    """The normal path must run the FULL check, stylesheet included.

    install.sh is the fix a stylesheet warning names, so it is the one caller that
    must be able to clear that half.
    """
    called = _run_call_block(tmp_path, _call_block('install.sh', 'if [ "$SKIP_WEB" = 0 ]; then', after='# 11b)'),
                             SKIP_WEB=0)

    assert 'ARGS:[]' in called, (
        f'a standard install must run the full check with no flags, got: {called!r}'
    )


def test_the_unit_installer_re_checks_without_speaking_for_the_stylesheet(tmp_path):
    """install_units.sh must re-check, and must pass --no-static-probe.

    Without the call, the original defect returns: the command the banner names
    repairs the box and cannot clear the banner. Without the flag, installing
    units would clear or invent a stylesheet verdict it has no basis for.
    """
    called = _run_call_block(tmp_path, _call_block('install_units.sh', 'bash "$APP/scripts/check_install.sh"'))

    assert 'ARGS:[--no-static-probe]' in called, (
        f'install_units.sh must re-check units only, got: {called!r}'
    )


def test_update_runs_the_check_and_says_so_when_the_release_has_none(tmp_path):
    """update.sh calls it, and is honest on a rollback target that lacks it.

    update.sh is loaded by bash before the checkout and calls this script from the
    tree AFTER it, so rolling back to any release older than this one finds no
    script. Passing silently there would be a completeness check that quietly does
    nothing, which is the failure mode the check exists to prevent.
    """
    block = _call_block('update.sh', 'if [ -f "$APP/scripts/check_install.sh" ]; then')

    present = _run_call_block(tmp_path / 'a', block, script_present=True)
    assert 'ARGS:[]' in present, f'update.sh did not run the check: {present!r}'

    absent = _run_call_block(tmp_path / 'b', block, script_present=False)
    assert 'ARGS:' not in absent, 'a missing script cannot have run'
    assert 'LOG:' in absent, (
        'an update that skipped the completeness check must say so, not pass in '
        'silence'
    )


def test_static_warning_prefix_is_the_same_string_everywhere():
    """Three files quote this sentence. A copy edit must not split them.

    check_install.sh writes it and greps for it to carry it through a units-only
    repair; core/update_ops.py matches it to keep the section when it sanitizes
    the marker. The shell side now uses one variable for write and read. This
    locks the Python side to it, because a drift there means the app throws away
    a real warning rather than showing too much.
    """
    from core.update_ops import _STATIC_HEADER_PREFIX

    src = (_repo_root() / 'scripts' / 'check_install.sh').read_text()
    line = next(ln for ln in src.splitlines() if ln.startswith('STATIC_HEADER='))
    shell_text = line.split('=', 1)[1].strip().strip('"')

    assert shell_text.startswith(_STATIC_HEADER_PREFIX), (
        f'the app parser looks for {_STATIC_HEADER_PREFIX!r} and the scripts now '
        f'write {shell_text!r}, so the stylesheet warning would be dropped'
    )


def test_completeness_check_is_defined_in_exactly_one_place():
    """One definition of "complete", not one per script.

    update.sh used to own this logic outright. Copying it into install_units.sh
    would have fixed the prod symptom and recreated the drift that
    deploy/manifest.sh exists to prevent — two descriptions of a correct install,
    diverging quietly.
    """
    owners = [
        name for name in ('update.sh', 'install_units.sh', 'install.sh', 'check_install.sh')
        if 'These system services are not installed:' in (_repo_root() / 'scripts' / name).read_text()
    ]
    assert owners == ['check_install.sh'], (
        f'the marker text is written in more than one script: {owners}'
    )


@pytest.mark.django_db
def test_the_incomplete_banner_does_not_diagnose_the_wrong_problem(settings, tmp_path, client):
    """The card's own prose must not name a cause the marker does not claim.

    The marker has two independent halves: missing system services, and a web
    server that cannot read this install's stylesheets. The banner's lead-in said
    "it could not install the system services that code needs" for BOTH, so a box
    whose real problem was file permissions was told, in bold, that it was missing
    services, directly above the text that said otherwise.

    Found on mb-test by running the drill rather than the suite: repair the units
    on a doubly-broken box and look at what the operator actually sees. Reachable
    before this branch too, but repairing units is now a routine way to land in
    exactly this state.
    """
    from core import update_ops
    settings.BASE_DIR = tmp_path
    (tmp_path / 'logs').mkdir()
    admin = User.objects.create_superuser(username='boss2', password='x')
    client.force_login(admin)

    update_ops.incomplete_path().write_text(
        "The web server cannot read this install's stylesheets (HTTP 403).\n"
        'Pages render as unstyled HTML with no logo.\n'
        '\n'
        'FIX: cd /opt/murphys-bench && scripts/install.sh\n'
    )

    raw = client.get(reverse('core:update_status')).content.decode()
    # ⚠ Collapse whitespace FIRST. The template wraps this sentence across lines,
    # so the phrase never appears contiguously in the raw HTML and a plain
    # `not in` assertion passes against the buggy template too. The first version
    # of this test did exactly that and sailed through the planted regression.
    body = ' '.join(raw.split())

    assert 'This install is incomplete' in body, 'the warning itself must still show'
    assert 'cannot read this install' in body, 'the real problem must be named'
    assert 'scripts/install.sh' in body, 'the fix for THIS half must be shown'
    assert 'system services that code needs' not in body, (
        'the banner diagnosed missing services on a box whose services are fine'
    )


# ── Role flags are enforced, not decorative (v0.12.0) ───────────────────────
#
# Eleven Role flags were rendered in Settings → Roles and in Django admin while
# NO endpoint consulted them. An outside review of the public repo found the six
# ticket ones; checking the rest showed replies and work orders were the same.
# The matrix was a picture of a permission system: unchecking "Close/Resolve
# Tickets" changed nothing, and checking "Delete Tickets" granted nothing because
# deletion tested Django's is_staff instead.
#
# The reason it survived a suite this size is that no test ever set one of these
# flags. Volume is not coverage — a whole concept was missing, not a branch. So
# every flag below gets a PAIR: off ⇒ 403 and the state is unchanged, on ⇒ the
# action actually goes through. A one-sided test would still pass against a view
# that denies everyone, which is its own bug.

def _role_tech(username, **flags):
    """A non-admin user whose role has exactly the flags passed.

    Defaults every flag OFF so each test states the grants it depends on, rather
    than inheriting model defaults that may change. can_manage_settings stays off
    or the user would be an admin and bypass all of this.
    """
    from core.models import Role
    base = dict(
        can_manage_settings=False,
        can_view_all_tickets=False, can_create_ticket=False, can_edit_ticket=False,
        can_close_tickets=False, can_delete_ticket=False, can_assign_ticket=False,
        can_reply_internal=False, can_reply_customer=False,
        can_create_workorder=False, can_edit_workorder=False, can_close_workorder=False,
    )
    base.update(flags)
    role = Role.objects.create(name=f'Role-{username}', **base)
    return User.objects.create_user(username=username, password='x', is_staff=False, role_obj=role)


def _ticket_for(tech, client_obj, **kwargs):
    """A ticket assigned to `tech`, so visibility scoping is never what fails.

    Without this the flag tests would pass for the wrong reason: an unassigned,
    unscoped ticket 404s before any permission check runs.
    """
    kwargs.setdefault('status', 'open')
    return Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(),
        client=client_obj, subject='S', description='D',
        assigned_to=tech, **kwargs
    )


@pytest.mark.django_db
def test_close_ticket_denied_without_flag(client, client_obj):
    tech = _role_tech('t_close_off')
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    resp = client.post(reverse('core:ticket_close', args=[ticket.pk]))
    assert resp.status_code == 403
    ticket.refresh_from_db()
    assert ticket.status == 'open', 'ticket was closed despite the 403'


@pytest.mark.django_db
def test_close_ticket_allowed_with_flag(client, client_obj):
    tech = _role_tech('t_close_on', can_close_tickets=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    client.post(reverse('core:ticket_close', args=[ticket.pk]))
    ticket.refresh_from_db()
    assert ticket.status == 'resolved'


@pytest.mark.django_db
def test_reopen_ticket_denied_without_close_flag(client, client_obj):
    tech = _role_tech('t_reopen_off')
    ticket = _ticket_for(tech, client_obj, status='resolved')
    client.force_login(tech)
    resp = client.post(reverse('core:ticket_reopen', args=[ticket.pk]))
    assert resp.status_code == 403
    ticket.refresh_from_db()
    assert ticket.status == 'resolved'


@pytest.mark.django_db
def test_reopen_ticket_allowed_with_close_flag(client, client_obj):
    tech = _role_tech('t_reopen_on', can_close_tickets=True)
    ticket = _ticket_for(tech, client_obj, status='resolved')
    client.force_login(tech)
    client.post(reverse('core:ticket_reopen', args=[ticket.pk]))
    ticket.refresh_from_db()
    assert ticket.status == 'open'


@pytest.mark.django_db
def test_status_dropdown_cannot_close_without_close_flag(client, client_obj):
    """The quick-status dropdown is a second door onto the same act.

    Gating only TicketCloseView would leave this one wide open, which is exactly
    how the original gap spread: the permission was thought about per button
    rather than per outcome.
    """
    tech = _role_tech('t_status_close', can_edit_ticket=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    resp = client.post(reverse('core:ticket_status_update', args=[ticket.pk]), {'status': 'resolved'})
    assert resp.status_code == 403
    ticket.refresh_from_db()
    assert ticket.status == 'open'


@pytest.mark.django_db
def test_status_dropdown_non_closing_move_needs_only_edit(client, client_obj):
    tech = _role_tech('t_status_edit', can_edit_ticket=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    client.post(reverse('core:ticket_status_update', args=[ticket.pk]), {'status': 'in_progress'})
    ticket.refresh_from_db()
    assert ticket.status == 'in_progress'


@pytest.mark.django_db
def test_edit_form_cannot_close_without_close_flag(client, client_obj):
    """The full edit form carries a status field too — the third door.

    ⚠ This test is why TicketUpdateView reads the old status from the DB instead
    of self.object.status: by form_valid() time _post_clean() has already written
    the new status onto the instance, so the naive check compares 'resolved' to
    'resolved', never fires, and this test fails.
    """
    tech = _role_tech('t_edit_close', can_edit_ticket=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    resp = client.post(reverse('core:ticket_edit', args=[ticket.pk]), {
        'client': client_obj.pk, 'subject': 'S', 'description': 'D',
        'source': 'email', 'status': 'resolved',
    })
    # A 200 here would mean the FORM was rejected, not the permission — the test
    # would then pass for the wrong reason if the gate were removed.
    assert resp.status_code == 403, f'expected a permission refusal, got {resp.status_code}'
    ticket.refresh_from_db()
    assert ticket.status == 'open'


@pytest.mark.django_db
def test_edit_ticket_denied_without_flag(client, client_obj):
    tech = _role_tech('t_edit_off')
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    assert client.get(reverse('core:ticket_edit', args=[ticket.pk])).status_code == 403


@pytest.mark.django_db
def test_edit_ticket_allowed_with_flag(client, client_obj):
    tech = _role_tech('t_edit_on', can_edit_ticket=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    assert client.get(reverse('core:ticket_edit', args=[ticket.pk])).status_code == 200


@pytest.mark.django_db
def test_create_ticket_denied_without_flag(client, client_obj):
    tech = _role_tech('t_new_off')
    client.force_login(tech)
    assert client.get(reverse('core:ticket_create')).status_code == 403


@pytest.mark.django_db
def test_create_ticket_allowed_with_flag(client, client_obj):
    tech = _role_tech('t_new_on', can_create_ticket=True)
    client.force_login(tech)
    assert client.get(reverse('core:ticket_create')).status_code == 200


@pytest.mark.django_db
def test_delete_ticket_denied_without_flag(client, client_obj):
    tech = _role_tech('t_del_off')
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    resp = client.post(reverse('core:ticket_delete', args=[ticket.pk]))
    assert resp.status_code == 403
    assert Ticket.objects.filter(pk=ticket.pk).exists()


@pytest.mark.django_db
def test_delete_ticket_flag_GRANTS_it_to_a_non_admin(client, client_obj):
    """The direction that was impossible before.

    Deletion tested is_staff, so ticking "Delete Tickets" on a role was inert —
    an operator could grant a capability and nothing happened. That silent
    no-op is the under-permission half of the defect and this locks it shut.
    """
    tech = _role_tech('t_del_on', can_delete_ticket=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    client.post(reverse('core:ticket_delete', args=[ticket.pk]))
    assert not Ticket.objects.filter(pk=ticket.pk).exists()


@pytest.mark.django_db
def test_transfer_ticket_denied_without_assign_flag(client, client_obj):
    tech = _role_tech('t_assign_off')
    other = _role_tech('t_assign_other')
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    resp = client.post(reverse('core:ticket_assign', args=[ticket.pk]), {'assigned_to': other.pk})
    assert resp.status_code == 403
    ticket.refresh_from_db()
    assert ticket.assigned_to_id == tech.pk


@pytest.mark.django_db
def test_transfer_ticket_allowed_with_assign_flag(client, client_obj):
    tech = _role_tech('t_assign_on', can_assign_ticket=True)
    other = _role_tech('t_assign_on_other')
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    client.post(reverse('core:ticket_assign', args=[ticket.pk]), {'assigned_to': other.pk})
    ticket.refresh_from_db()
    assert ticket.assigned_to_id == other.pk


@pytest.mark.django_db
def test_claiming_is_deliberately_not_gated_on_assign_flag(client, client_obj):
    """Claiming unowned work stays open to every tech — by design, not by omission.

    The unclaimed pool only functions if any technician can pick work up, and
    claiming takes nothing from anyone. If someone later "tightens" this to
    require can_assign_ticket, THIS test is what should stop them, the same way
    test_device_cred_unassigned_tech_can_write guards the credential asymmetry.
    """
    tech = _role_tech('t_claim')
    ticket = Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(),
        client=client_obj, subject='S', description='D', status='open',
    )
    client.force_login(tech)
    client.post(reverse('core:ticket_assign', args=[ticket.pk]), {'claim': '1'})
    ticket.refresh_from_db()
    assert ticket.assigned_to_id == tech.pk


@pytest.mark.django_db
def test_customer_reply_denied_without_flag(client, client_obj):
    tech = _role_tech('t_reply_cust_off', can_reply_internal=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    resp = client.post(reverse('core:ticket_reply_add', args=[ticket.pk]), {
        'reply_type': 'customer_visible', 'content': 'hello client',
    })
    assert resp.status_code == 403
    assert ticket.replies.count() == 0, 'reply was stored despite the 403'


@pytest.mark.django_db
def test_internal_reply_denied_without_flag(client, client_obj):
    tech = _role_tech('t_reply_int_off', can_reply_customer=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    resp = client.post(reverse('core:ticket_reply_add', args=[ticket.pk]), {
        'reply_type': 'internal', 'content': 'note',
    })
    assert resp.status_code == 403
    assert ticket.replies.count() == 0


@pytest.mark.django_db
def test_each_reply_flag_allows_its_own_kind(client, client_obj):
    tech = _role_tech('t_reply_both', can_reply_internal=True, can_reply_customer=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    client.post(reverse('core:ticket_reply_add', args=[ticket.pk]), {
        'reply_type': 'internal', 'content': 'note',
    })
    client.post(reverse('core:ticket_reply_add', args=[ticket.pk]), {
        'reply_type': 'customer_visible', 'content': 'hello',
    })
    assert ticket.replies.count() == 2


@pytest.mark.django_db
def test_customer_only_replier_gets_a_usable_form(client, client_obj):
    """A user with one grant must not be handed a form that 403s itself.

    The view defaults reply_type to 'internal' when the POST omits it. Dropping
    the radios for a customer-only replier without emitting a hidden field would
    make every reply they send fall into the internal branch and be refused —
    a permission bug dressed as a UI tidy-up.
    """
    tech = _role_tech('t_reply_cust_only', can_reply_customer=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    body = client.get(reverse('core:ticket_detail', args=[ticket.pk])).content.decode()
    assert 'name="reply_type" value="customer_visible"' in body, (
        'the form must state which reply kind this user may send'
    )


@pytest.mark.django_db
def test_view_all_tickets_flag_widens_visibility_both_ways(client, client_obj):
    """Both directions in one test, because the flag is a widening, not a gate.

    Off: the tech sees only their own plus the unclaimed pool (unchanged
    behaviour). On: they see another tech's ticket too.
    """
    mine = _role_tech('t_vis_mine')
    theirs = _role_tech('t_vis_theirs')
    other_ticket = _ticket_for(theirs, client_obj)

    client.force_login(mine)
    assert client.get(reverse('core:ticket_detail', args=[other_ticket.pk])).status_code == 404

    mine.role_obj.can_view_all_tickets = True
    mine.role_obj.save()
    assert client.get(reverse('core:ticket_detail', args=[other_ticket.pk])).status_code == 200


@pytest.mark.django_db
def test_view_all_tickets_never_exposes_system_alerts(client, client_obj):
    """MB's own operational alerts stay owner-facing even under "view all".

    They are written as tickets so they land somewhere watched, but a technician
    can act on none of them and closing one hides a real outage. "All tickets"
    means all shop work, not the owner's alert feed.
    """
    tech = _role_tech('t_vis_sys', can_view_all_tickets=True)
    alert = Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(),
        client=client_obj, subject='Backup failed', description='rclone auth error',
        status='open', source='system',
    )
    client.force_login(tech)
    assert client.get(reverse('core:ticket_detail', args=[alert.pk])).status_code == 404


@pytest.mark.django_db
def test_create_workorder_denied_without_flag(client, client_obj):
    tech = _role_tech('t_wo_new_off')
    client.force_login(tech)
    assert client.get(reverse('core:work_order_create')).status_code == 403


@pytest.mark.django_db
def test_create_workorder_allowed_with_flag(client, client_obj):
    tech = _role_tech('t_wo_new_on', can_create_workorder=True)
    client.force_login(tech)
    assert client.get(reverse('core:work_order_create')).status_code == 200


@pytest.mark.django_db
def test_ticket_convert_needs_the_workorder_create_flag(client, client_obj):
    """Convert-to-WO is a WorkOrder creation wearing a ticket-page button.

    Gating it on a ticket flag would let a role denied WO creation reach one
    through the side door.
    """
    tech = _role_tech('t_convert', can_edit_ticket=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    assert client.get(reverse('core:ticket_convert', args=[ticket.pk])).status_code == 403


@pytest.mark.django_db
def test_converted_is_not_offered_in_the_ticket_status_dropdowns(client, admin_user, client_obj):
    """Mike's Aug 14 ruling: remove it from the dropdown.

    'Converted to Work Order' named an action it could not perform. Picking it
    renamed the ticket, created no work order, and drove the ticket into a state
    that hid the real Convert button.
    """
    from core.models import StatusDefinition
    from core.forms import TicketForm
    sd = StatusDefinition.objects.get(entity_type='ticket', slug='converted')
    assert sd.operator_selectable is False, 'migration 0105 should have cleared this'

    # Not in the full edit form's choices...
    slugs = [s for s, _ in TicketForm().fields['status'].choices]
    assert 'converted' not in slugs

    # ...and not in the ticket detail quick dropdown.
    ticket = Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(), client=client_obj,
        subject='x', description='x', status='new',
    )
    client.force_login(admin_user)
    body = client.get(reverse('core:ticket_detail', args=[ticket.pk])).content.decode()
    assert '<option value="converted"' not in body

    # Posting it by hand is refused rather than silently saved.
    resp = client.post(reverse('core:ticket_status_update', args=[ticket.pk]),
                       {'status': 'converted'})
    assert resp.status_code == 403
    ticket.refresh_from_db()
    assert ticket.status == 'new'


@pytest.mark.django_db
def test_quick_dropdown_on_a_converted_ticket_shows_converted_selected(client, admin_user, client_obj):
    """Outside-review finding (Aug 15): hiding 'converted' from the quick
    dropdown left a converted ticket with NO matching <option>, so the browser
    silently displayed the first choice ('New') and clicking Set moved the
    ticket out of 'converted' by accident. The ticket's own status must render
    as the selected option even when it is action-owned."""
    ticket = Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(), client=client_obj,
        subject='Converted for real', description='x', status='converted',
    )
    client.force_login(admin_user)
    body = client.get(reverse('core:ticket_detail', args=[ticket.pk])).content.decode()
    assert '<option value="converted" selected>' in body

    # Re-posting the pre-selected current status is an allowed no-op, so an
    # operator who clicks Set without touching the dropdown changes nothing.
    resp = client.post(reverse('core:ticket_status_update', args=[ticket.pk]),
                       {'status': 'converted'})
    assert resp.status_code in (200, 302)
    ticket.refresh_from_db()
    assert ticket.status == 'converted'

    # And a ticket NOT at 'converted' still never sees it offered.
    other = Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(), client=client_obj,
        subject='Plain', description='x', status='open',
    )
    body = client.get(reverse('core:ticket_detail', args=[other.pk])).content.decode()
    assert '<option value="converted"' not in body


@pytest.mark.django_db
def test_an_already_converted_ticket_stays_editable(client, admin_user, client_obj):
    """Removing a status from the dropdown must not make records holding it
    unsavable. The ticket's own current status is always a valid choice."""
    from core.forms import TicketForm
    ticket = Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(), client=client_obj,
        subject='Already converted', description='x', status='converted',
    )
    slugs = [s for s, _ in TicketForm(instance=ticket).fields['status'].choices]
    assert 'converted' in slugs, 'a converted ticket could not keep its own status'

    client.force_login(admin_user)
    resp = client.post(reverse('core:ticket_edit', args=[ticket.pk]), {
        'client': client_obj.pk, 'subject': 'Edited while converted',
        'description': 'x', 'source': 'web', 'status': 'converted',
    })
    assert resp.status_code == 302, 'editing a converted ticket must not fail validation'
    ticket.refresh_from_db()
    assert ticket.subject == 'Edited while converted'
    assert ticket.status == 'converted'


@pytest.mark.django_db
def test_a_hand_marked_converted_ticket_can_still_be_converted(client, admin_user, client_obj):
    """The trap from TKT-00041.

    "Converted to Work Order" is also a plain status in the quick dropdown, and
    picking it there creates nothing. The convert view used to refuse any ticket
    whose status was 'converted', so a ticket mislabelled that way could never be
    converted for real, and the ticket page hid the working button at the same
    time. Nothing on the screen led anywhere.
    """
    ticket = Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(), client=client_obj,
        subject='Marked converted by hand', description='x', status='converted',
    )
    client.force_login(admin_user)

    # The real route must still open, and still work.
    assert client.get(reverse('core:ticket_convert', args=[ticket.pk])).status_code == 200
    resp = client.post(reverse('core:ticket_convert', args=[ticket.pk]), {})
    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.work_order_created is not None
    assert ticket.work_order_created.client_id == client_obj.pk


@pytest.mark.django_db
def test_converted_ticket_offers_a_route_to_its_work_order(client, admin_user, client_obj):
    """Never a dead end: once a WO exists the button becomes a link to it."""
    ticket = Ticket.objects.create(
        ticket_number=Ticket.generate_ticket_number(), client=client_obj,
        subject='Converted', description='x', status='new',
    )
    client.force_login(admin_user)
    client.post(reverse('core:ticket_convert', args=[ticket.pk]), {})
    ticket.refresh_from_db()
    wo = ticket.work_order_created

    body = client.get(reverse('core:ticket_detail', args=[ticket.pk])).content.decode()
    assert f'Go to {wo.work_order_number}' in body
    assert reverse('core:work_order_detail', args=[wo.pk]) in body
    # And it must not offer a second conversion.
    assert 'Convert to Work Order</a>' not in body

    # A second attempt at the convert view is refused, on the work order's
    # existence rather than on the status string.
    assert client.get(reverse('core:ticket_convert', args=[ticket.pk])).status_code == 302


@pytest.mark.django_db
def test_ticket_device_picker_hides_retired_devices(client_obj):
    """The ticket picker offers only active devices, matching the HTMX cascade
    endpoint. A promoted-to-Asset device is therefore unpickable on a ticket
    until the Device/Asset merge un-retires it — the ruled fix for the Aug-14
    empty-picker defect. This pins the interim behavior so the merge, when it
    lands, changes it deliberately rather than by accident."""
    from core.forms import TicketForm
    active = Device.objects.create(client=client_obj, name='Front Desk PC')
    retired = Device.objects.create(client=client_obj, name='Reception PC')
    retired.promote_to_asset()
    retired.refresh_from_db()
    assert retired.is_active is False

    choices = TicketForm(data={'client': client_obj.pk}).fields['device'].queryset
    assert active in choices
    assert retired not in choices


@pytest.mark.django_db
def test_edit_workorder_denied_without_flag(client, client_obj):
    tech = _role_tech('t_wo_edit_off')
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress',
    )
    client.force_login(tech)
    assert client.get(reverse('core:work_order_edit', args=[wo.pk])).status_code == 403


@pytest.mark.django_db
def test_quick_update_cannot_complete_a_wo_without_close_flag(client, client_obj):
    """Same status-split as tickets, and the same in-memory trap.

    WorkOrderQuickUpdateView reads the incoming status into a local BEFORE
    assigning it onto the instance; comparing after the assignment would test
    the new value against itself and let this through.
    """
    tech = _role_tech('t_wo_close_off', can_edit_workorder=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress',
    )
    client.force_login(tech)
    resp = client.post(reverse('core:work_order_quick_update', args=[wo.pk]), {'status': 'completed'})
    assert resp.status_code == 403
    wo.refresh_from_db()
    assert wo.status == 'in_progress'


@pytest.mark.django_db
def test_quick_update_completes_a_wo_with_close_flag(client, client_obj):
    tech = _role_tech('t_wo_close_on', can_edit_workorder=True, can_close_workorder=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress',
    )
    client.force_login(tech)
    client.post(reverse('core:work_order_quick_update', args=[wo.pk]), {'status': 'completed'})
    wo.refresh_from_db()
    assert wo.status == 'completed'


@pytest.mark.django_db
def test_existing_roles_keep_the_ability_to_close_tickets_after_upgrade():
    """Migration 0102's whole reason for existing.

    Before enforcement every technician could close tickets whatever the box
    said, so enforcing at the old default=False would have taken that away from
    working shops that changed no setting. A role created the ordinary way must
    come out able to close.
    """
    from core.models import Role
    role = Role.objects.create(name='FreshRole')
    assert role.can_close_tickets is True


@pytest.mark.django_db
def test_every_role_flag_is_actually_consulted_somewhere():
    """Structural guard: a flag that no code reads must not reach the UI.

    This is the check whose absence let eleven decorative checkboxes ship. It is
    deliberately structural rather than behavioural — a per-flag behaviour test
    can only cover flags someone remembered to write one for, which is precisely
    the failure being prevented. A new Role flag now fails the suite until it is
    either enforced or removed.
    """
    import inspect
    from pathlib import Path
    from core.models import Role

    flags = [
        f.name for f in Role._meta.get_fields()
        if getattr(f, 'get_internal_type', lambda: None)() == 'BooleanField'
        and f.name.startswith('can_')
    ]
    root = Path(inspect.getfile(Role)).parent
    # What counts as ENFORCEMENT is server-side Python only.
    #
    # ⚠ Templates and context_processors.py are deliberately NOT scanned. An
    # outside reviewer pointed out that the first version of this test accepted
    # any surviving mention of the flag, so a future flag that merely hides a
    # button in a template — with no server-side check behind it — would satisfy
    # the guard while granting nothing. Hiding a control is not a permission; the
    # endpoint is. Excluded for the same reason: models.py (the field), forms.py
    # (the role editor), admin.py (fieldsets), and tests.py, whose own exception
    # list below would otherwise read as proof the flag was handled.
    display_only = {'models.py', 'forms.py', 'admin.py', 'tests.py',
                    'context_processors.py'}
    enforcement = '\n'.join(
        p.read_text()
        for p in root.glob('*.py')
        if p.name not in display_only
    )
    # views.py holds the role-editor's own label list, which is display too. Drop
    # those lines so a flag cannot look enforced just by being labelled there.
    enforcement = '\n'.join(
        line for line in enforcement.splitlines()
        if not re.match(r"\s*\('can_\w+',\s+'", line)
    )
    # ⚠ EMPTY, and it should stay that way. This list once held can_manage_users,
    # the twelfth decorative flag this test found. Mike's ruling closed it: a
    # checkbox and its enforcement must agree, so "Manage Users" now actually
    # grants user management rather than being ignored in favour of an admin
    # check. The guard below is what forced the list to be emptied — it fails
    # while an entry is listed as dead but has since been enforced, so an
    # exception cannot quietly outlive the problem it documented.
    KNOWN_UNENFORCED = set()

    unenforced = [f for f in flags if f not in enforcement and f not in KNOWN_UNENFORCED]
    assert not unenforced, (
        'these Role flags are shown to operators but no code reads them, so the '
        f'checkbox does nothing: {unenforced}'
    )
    resolved = [f for f in KNOWN_UNENFORCED if f in enforcement]
    assert not resolved, (
        'a flag listed as known-unenforced is now enforced — remove it from '
        f'KNOWN_UNENFORCED: {sorted(resolved)}'
    )


# ── Reviewer-found bypasses in the first cut of the enforcement work ────────
#
# Both were reproduced with live probes by an outside reviewer against e847228,
# after the branch's own 30 tests were green. They are recorded here as their own
# block because the lesson is not "two bugs" — it is that a permission constant
# borrowed from a different question, and a whole-record write behind a narrow
# gate, are the two shapes this kind of work fails in.

@pytest.mark.django_db
def test_there_is_no_closed_work_order_status():
    """A work order finishes as 'completed'. There is no third terminal state.

    'closed' existed for months as a status the app could not agree about: the
    Register settled it, Reports counted it finished, the work order list called
    it active, and a linked ticket was never told the work was done. It had
    already been switched off in Settings → Statuses — SCS's own server had it
    inactive with zero work orders holding it — but the views still accepted it
    from a posted form, which is how it survived to become a permission bypass an
    edit-only role could use to finish a job.

    Two earlier fixes tried to reconcile the disagreement rather than end it.
    Removing the status ended it.
    """
    from core.models import WorkOrder
    slugs = [s for s, _ in WorkOrder.STATUS_CHOICES]
    assert 'closed' not in slugs, (
        "'closed' is back as a work order status; a work order completes, it does "
        'not close (tickets close — that is Ticket.STATUS_CHOICES)'
    )
    assert 'completed' in slugs and 'cancelled' in slugs


@pytest.mark.django_db
def test_tickets_still_close(client, client_obj):
    """⚠ The removal was scoped to work orders. Tickets close all day.

    Migration 0104 filters on entity_type='workorder' precisely so it cannot
    retire the ticket status half the ticket workflow depends on. If someone
    widens that filter, this fails.
    """
    from core.models import Ticket, StatusDefinition
    assert 'closed' in [s for s, _ in Ticket.STATUS_CHOICES]
    assert StatusDefinition.objects.filter(entity_type='ticket', slug='closed').exists()
    assert not StatusDefinition.objects.filter(entity_type='workorder', slug='closed').exists()

    tech = _role_tech('t_ticket_still_closes', can_close_tickets=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    client.post(reverse('core:ticket_close', args=[ticket.pk]))
    ticket.refresh_from_db()
    assert ticket.status == 'resolved'


@pytest.mark.django_db
def test_existing_closed_work_orders_became_completed(client_obj):
    """Migration 0104 must not strand rows in a status that no longer exists.

    Anything that WAS closed is work that got finished, so it lands on
    'completed' — not 'cancelled', which means the job never happened.
    """
    from core.models import WorkOrder
    assert not WorkOrder.objects.filter(status='closed').exists()


@pytest.mark.django_db
def test_close_only_role_cannot_rewrite_other_fields_while_closing(client_obj, client):
    """Permission to FINISH a work order is not permission to rewrite it.

    The quick-update panel posts every field in one request. Allowing a closing
    request through wholesale handed a close-only role the priority, assignee,
    contact, device, repair type, schedule and invoice ref as well. Reproduced by
    the reviewer with status=completed&priority=urgent.
    """
    tech = _role_tech('t_wo_close_only', can_close_workorder=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress', priority='normal',
    )
    client.force_login(tech)
    client.post(reverse('core:work_order_quick_update', args=[wo.pk]), {
        'status': 'completed', 'priority': 'urgent',
    })
    wo.refresh_from_db()
    assert wo.status == 'completed', 'the close itself must still work'
    assert wo.priority == 'normal', 'a close-only role rewrote a field it may not edit'


@pytest.mark.django_db
def test_close_only_role_still_gets_a_working_complete_button(client, client_obj):
    """The narrowing must not break the legitimate path.

    Ignoring the rest of the request rather than rejecting it means the ordinary
    Complete action still works for a close-only user; a 403 here would have made
    the grant useless in the UI that posts the whole panel.
    """
    tech = _role_tech('t_wo_close_works', can_close_workorder=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress',
    )
    client.force_login(tech)
    client.post(reverse('core:work_order_quick_update', args=[wo.pk]), {'status': 'completed'})
    wo.refresh_from_db()
    assert wo.status == 'completed'
    assert wo.completed_date is not None, 'completing must still stamp the date'


@pytest.mark.django_db
def test_structural_flag_guard_does_not_accept_ui_only_references(tmp_path):
    """The guard must require SERVER-side enforcement, not a hidden button.

    Its first version scanned templates and the context processor too, so a flag
    that only hid a control would have satisfied it while granting nothing —
    precisely the defect the guard exists to catch, one level up. Verified by
    planting a UI-exposed, never-enforced flag: the old scan passed it, the
    current one fails it.
    """
    import inspect
    from pathlib import Path
    from core.models import Role

    root = Path(inspect.getfile(Role)).parent
    scanned = {
        p.name for p in root.glob('*.py')
        if p.name not in {'models.py', 'forms.py', 'admin.py', 'tests.py',
                          'context_processors.py'}
    }
    assert 'views.py' in scanned, 'the guard must still read the views'
    assert 'context_processors.py' not in scanned, (
        'exposing a flag to templates is not enforcing it'
    )


@pytest.mark.django_db
def test_closing_a_linked_wo_tells_its_ticket_the_work_is_done(client, client_obj):
    """One definition of "finished", including the linked-ticket workflow.

    A reviewer probed the ancestor of this: quick-updating a linked WO to the
    then-existing `closed` status saved fine and left ticket.wo_complete False, so
    the "work is complete — ready for final contact" banner and the Close Ticket
    button never appeared and the ticket was stranded behind a work order the app
    insisted was still open. `closed` is gone now, but the hole it came through —
    a finishing status the ticket half does not know about — is reachable again
    the moment someone adds a status without checking here.

    Asserted for every finishing status, so adding a third cannot quietly skip it.
    """
    admin = User.objects.create_user(username='wo_done_admin', password='x',
                                     is_staff=True, is_superuser=True)
    client.force_login(admin)
    for status in ('completed', 'cancelled'):
        ticket = Ticket.objects.create(
            ticket_number=Ticket.generate_ticket_number(),
            client=client_obj, subject=f'S-{status}', description='D', status='open',
        )
        wo = WorkOrder.objects.create(
            work_order_number=WorkOrder.generate_work_order_number(),
            client=client_obj, ticket=ticket, status='in_progress',
        )
        client.post(reverse('core:work_order_quick_update', args=[wo.pk]), {'status': status})
        ticket.refresh_from_db()
        assert ticket.wo_complete is True, (
            f'a linked WO moved to {status!r} left its ticket showing the work as unfinished'
        )


@pytest.mark.django_db
def test_finished_work_order_statuses_stay_in_step_with_the_status_list():
    """Every finishing status must be a status a work order can actually hold.

    The `closed` saga was this invariant broken in both directions at once: a
    status existed that the finished-list omitted, and later a finished-list
    entry for a status being removed. Cheap to assert, and it fails the moment
    the two drift again.
    """
    from core.models import WorkOrder
    from core.views import WO_CLOSED_STATUSES
    slugs = {s for s, _ in WorkOrder.STATUS_CHOICES}
    stray = [s for s in WO_CLOSED_STATUSES if s not in slugs]
    assert not stray, f'finished-status list names statuses a WO cannot hold: {stray}'


@pytest.mark.django_db
def test_only_one_definition_of_a_finished_work_order(client_obj):
    """There must not be a second "finished" list that can drift from this one.

    The first fix for the `closed` bypass added WO_FINISHING_STATUSES for
    permissions while leaving the ticket workflow on the old constant, which is
    how the linked-ticket half stayed broken. Two descriptions of one fact is the
    failure mode deploy/manifest.sh and check_install.sh both exist to prevent.
    """
    import inspect
    from core import views
    src = inspect.getsource(views)
    assert 'WO_FINISHING_STATUSES' not in src, (
        'a second "finished work order" constant is back; fold it into '
        'WO_CLOSED_STATUSES so the app cannot disagree with itself'
    )


@pytest.mark.django_db
def test_upgrading_does_not_widen_technician_ticket_visibility(client, client_obj):
    """The seeded Technician role must not start seeing every ticket on upgrade.

    ⚠ This is the mirror image of migration 0102 and the more dangerous direction.
    The Technician role has carried can_view_all_tickets=True since Batch 4, which
    predates the ticket visibility model (own + unclaimed pool + escalations)
    entirely. Nothing revisited it when that model arrived, because nothing read
    the flag. Enforcing it in v0.12.0 without migration 0103 handed every
    technician on every existing install sight of every ticket, silently undoing a
    deliberate design, with no setting changed by the operator.

    Found from a screenshot of the live Roles page, not from this suite — the flag
    was ticked in plain sight and three review rounds went past it.
    """
    from core.models import Role
    tech_role = Role.objects.get(name='Technician')
    assert tech_role.can_view_all_tickets is False, (
        'the seeded Technician role can see every ticket; migration 0103 is meant '
        'to leave non-admin roles on the unclaimed-pool model'
    )

    mine = User.objects.create_user(username='pool_a', password='x', role_obj=tech_role)
    theirs = User.objects.create_user(username='pool_b', password='x', role_obj=tech_role)
    theirs_ticket = _ticket_for(theirs, client_obj)

    client.force_login(mine)
    assert client.get(reverse('core:ticket_detail', args=[theirs_ticket.pk])).status_code == 404
    listing = client.get(reverse('core:ticket_list'))
    numbers = [t.ticket_number for t in listing.context['tickets']]
    assert theirs_ticket.ticket_number not in numbers


@pytest.mark.django_db
def test_admin_roles_keep_view_all_tickets_ticked():
    """Migration 0103 deliberately skips admin roles, and this pins why.

    _is_admin() short-circuits ticket scoping, so an admin role sees everything
    whatever this flag says. Writing False there would leave the Roles page
    showing an unticked box beside a role that plainly does see all tickets — a
    checkbox that lies about what it does, which is the entire defect v0.12.0
    exists to end. Honest display beats a tidy-looking backfill.
    """
    from core.models import Role
    admin_role = Role.objects.get(name='Administrator')
    assert admin_role.can_manage_settings is True
    assert admin_role.can_view_all_tickets is True


@pytest.mark.django_db
def test_a_role_may_still_be_granted_view_all_tickets(client, client_obj):
    """The migration preserves behaviour; it must not disable the capability.

    Turning the box back on has to work, or 0103 would have replaced one lying
    checkbox with another.
    """
    tech = _role_tech('t_widened', can_view_all_tickets=True)
    other = _role_tech('t_widened_other')
    theirs = _ticket_for(other, client_obj)
    client.force_login(tech)
    assert client.get(reverse('core:ticket_detail', args=[theirs.pk])).status_code == 200


@pytest.mark.django_db
def test_quick_update_rejects_a_status_that_does_not_exist(client, client_obj):
    """The inline panel must not accept any string as a work order status.

    It assigned request.POST['status'] straight onto the model, and the field has
    no `choices=` (migration 0036 moved status definitions into the database), so
    anything saved. WorkOrderForm validated the same field all along; only this
    endpoint did not.

    That is what kept `closed` reachable by POST after Settings → Statuses had
    switched it off — removing it from the model and the table was not enough on
    its own — and it accepts typos and stale bookmarks just as happily, leaving a
    work order in a state no list, report or register recognises.
    """
    from core.models import StatusDefinition
    admin = User.objects.create_user(username='qu_admin', password='x',
                                     is_staff=True, is_superuser=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, status='in_progress',
    )
    client.force_login(admin)

    assert not StatusDefinition.objects.filter(entity_type='workorder', slug='closed').exists()
    for bogus in ('closed', 'banana', ''):
        resp = client.post(reverse('core:work_order_quick_update', args=[wo.pk]),
                           {'status': bogus})
        wo.refresh_from_db()
        assert wo.status == 'in_progress', (
            f'posting status={bogus!r} was saved (HTTP {resp.status_code})'
        )

    # A real status still works — the guard must not break the panel.
    client.post(reverse('core:work_order_quick_update', args=[wo.pk]), {'status': 'completed'})
    wo.refresh_from_db()
    assert wo.status == 'completed'


@pytest.mark.django_db
def test_quick_update_still_saves_a_wo_holding_a_retired_status(client, client_obj):
    """A legacy status must not make the rest of the panel unusable.

    If a row somehow still holds a status that has since been retired, refusing
    every save would strand it — the operator could not even change its priority.
    The WO's own current status is always allowed through.
    """
    admin = User.objects.create_user(username='qu_admin2', password='x',
                                     is_staff=True, is_superuser=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, status='in_progress', priority='normal',
    )
    WorkOrder.objects.filter(pk=wo.pk).update(status='some_retired_status')
    client.force_login(admin)
    client.post(reverse('core:work_order_quick_update', args=[wo.pk]),
                {'status': 'some_retired_status', 'priority': 'urgent'})
    wo.refresh_from_db()
    assert wo.priority == 'urgent', 'a legacy status blocked an unrelated edit'


@pytest.mark.django_db
def test_every_role_permission_appears_in_exactly_one_group():
    """The grouped Roles screen must cover every permission, once.

    Grouping is only an improvement if it is total. A flag left out of every
    group would vanish from Settings → Roles entirely — still enforced, still
    stored, but no longer configurable or visible, which is a worse version of
    the decorative-checkbox problem it was introduced to fix. A flag in two
    groups would render two checkboxes writing the same field.
    """
    from core.models import Role
    from core.views import _ROLE_FLAG_GROUPS

    declared = [f for g in _ROLE_FLAG_GROUPS for f, _label, _s in g['flags']]
    model_flags = {
        f.name for f in Role._meta.get_fields()
        if getattr(f, 'get_internal_type', lambda: None)() == 'BooleanField'
        and f.name.startswith('can_')
    }

    assert len(declared) == len(set(declared)), (
        f'a permission is in more than one group: '
        f'{sorted(f for f in declared if declared.count(f) > 1)}'
    )
    missing = model_flags - set(declared)
    assert not missing, f'permissions missing from Settings → Roles entirely: {sorted(missing)}'
    stray = set(declared) - model_flags
    assert not stray, f'groups name permissions that do not exist: {sorted(stray)}'


@pytest.mark.django_db
def test_consequential_permissions_are_marked(client):
    """The permissions with an effect outside MB carry the marker.

    Twenty-three identical checkboxes is how a switch that charges a customer's
    card came to look exactly like one that shows a list. Pinned so the marking
    cannot quietly be dropped in a later restyle.
    """
    from core.views import _ROLE_FLAG_GROUPS
    marked = {f for g in _ROLE_FLAG_GROUPS for f, _l, s in g['flags'] if s}
    assert marked == {
        'can_process_payments',          # charges a real card on file
        'can_view_device_credentials',   # reveals a stored password
        'can_view_org_credentials',      # reveals the shop's own passwords
        'can_reset_user_mfa',            # clears someone's two-factor
        'can_manage_settings',           # full run of the shop's configuration
        'can_manage_users',              # creates logins
        'can_delete_ticket',             # destroys a record permanently
    }, 'the set of consequential permissions changed — deliberate, or an accident?'

    admin = User.objects.create_user(username='rolegrid', password='x',
                                     is_staff=True, is_superuser=True)
    client.force_login(admin)
    body = client.get(reverse('core:role_list')).content.decode()
    # escape() because "Sales & Money" renders as "Sales &amp; Money" — comparing
    # the raw label would fail on the ampersand and look like a missing group.
    from django.utils.html import escape
    for group in _ROLE_FLAG_GROUPS:
        assert escape(group['label']) in body, f"group {group['label']!r} is not rendered"


@pytest.mark.django_db
def test_a_negative_price_is_refused_not_silently_dropped(client, client_obj):
    """Typing a discount must not produce a line worth nothing.

    `_parse_price` returned None for negatives, so the custom-line form saved a
    line named "Goodwill discount" with NO price: HTTP 200, no message, total
    unchanged. The operator had every reason to think a discount was recorded.

    MB has no discount concept — LineItem is labor or part — and how a price
    reduction should be represented belongs to the native money layer, not to an
    invention here. Until then the honest answer is to refuse and say why.
    """
    admin = User.objects.create_user(username='negprice', password='x',
                                     is_staff=True, is_superuser=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, status='in_progress',
    )
    client.force_login(admin)
    resp = client.post(reverse('core:work_performed_custom', args=[wo.pk]), {
        'custom_label': 'Goodwill discount', 'kind': 'part',
        'quantity': '1', 'unit_price': '-60.00',
    })
    assert resp.status_code == 400, 'a negative price must be refused, not accepted'
    assert b'negative' in resp.content.lower()
    assert wo.line_items.count() == 0, 'a worthless line was created anyway'


@pytest.mark.django_db
def test_a_blank_price_still_means_unpriced(client, client_obj):
    """Refusing negatives must not break the legitimate unpriced line.

    A blank price is a real and supported case — work logged without a figure —
    and it must keep returning None rather than being caught up in the new guard.
    """
    admin = User.objects.create_user(username='blankprice', password='x',
                                     is_staff=True, is_superuser=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, status='in_progress',
    )
    client.force_login(admin)
    resp = client.post(reverse('core:work_performed_custom', args=[wo.pk]), {
        'custom_label': 'Diagnostics', 'kind': 'labor', 'quantity': '1', 'unit_price': '',
    })
    assert resp.status_code == 200
    line = wo.line_items.get()
    assert line.unit_price is None
    assert wo.line_items_total == 0


# ── Checkbox and enforcement must agree (Mike's ruling, Aug 6) ──────────────
#
# The first pass gated the obvious screens and left ~22 smaller actions open, so
# "Edit Work Orders" could be off while a technician still added notes, logged
# time, ticked checklists and rewrote priced lines. Mike's ruling: where a
# checkbox and its enforcement disagree, fix the disagreement. These lock the
# actions to the box that claims to cover them.

@pytest.mark.django_db
def test_work_order_actions_obey_the_edit_work_orders_box(client, client_obj):
    """Every action that changes a work order needs the box that says so."""
    from decimal import Decimal
    from core.models import WorkOrderItem, LineItem
    tech = _role_tech('t_wo_actions')          # can_edit_workorder = False
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress',
    )
    item = WorkOrderItem.objects.create(work_order=wo, description='Check PSU')
    line = LineItem.objects.create(content_object=wo, kind='labor',
                                   description='Bench', quantity=1, unit_price='50.00')
    client.force_login(tech)

    attempts = [
        ('add a note',        reverse('core:work_order_note_add', args=[wo.pk]),
         {'content': 'x', 'note_type': 'internal'}),
        ('log time',          reverse('core:work_order_add_time', args=[wo.pk]), {'minutes': '30'}),
        ('apply a checklist', reverse('core:work_order_apply_checklist', args=[wo.pk]), {}),
        ('tick a checklist item', reverse('core:work_order_item_check', args=[item.pk]), {}),
        ('add a priced line', reverse('core:work_performed_custom', args=[wo.pk]),
         {'custom_label': 'Extra', 'kind': 'part', 'quantity': '1', 'unit_price': '99.00'}),
        ('rewrite a priced line', reverse('core:work_performed_update', args=[line.pk]),
         {'description': 'Rewritten', 'quantity': '1', 'unit_price': '1.00'}),
        ('delete a priced line', reverse('core:work_performed_delete', args=[line.pk]), {}),
        ('change billing',    reverse('core:wo_billing_update', args=[wo.pk]),
         {'billing_status': 'invoiced'}),
    ]
    for label, url, payload in attempts:
        resp = client.post(url, payload)
        assert resp.status_code == 403, f'a tech without edit rights could {label}'

    wo.refresh_from_db(); line.refresh_from_db()
    assert wo.line_items.count() == 1, 'line items changed despite the refusals'
    assert line.unit_price == Decimal('50.00'), 'a price changed despite the refusal'
    assert wo.notes.count() == 0


@pytest.mark.django_db
def test_work_order_actions_work_once_the_box_is_ticked(client, client_obj):
    """The gate must not break the job — the same actions succeed when granted."""
    tech = _role_tech('t_wo_actions_on', can_edit_workorder=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress',
    )
    client.force_login(tech)
    client.post(reverse('core:work_order_note_add', args=[wo.pk]),
                {'content': 'real note', 'note_type': 'internal'})
    client.post(reverse('core:work_performed_custom', args=[wo.pk]),
                {'custom_label': 'Bench work', 'kind': 'labor',
                 'quantity': '1', 'unit_price': '80.00'})
    wo.refresh_from_db()
    assert wo.notes.count() == 1
    assert wo.line_items.count() == 1


@pytest.mark.django_db
def test_ticket_actions_obey_the_edit_tickets_box(client, client_obj):
    """Every action that changes a ticket needs the box that says so."""
    tech = _role_tech('t_tkt_actions')          # can_edit_ticket = False
    ticket = _ticket_for(tech, client_obj, needs_response=True)
    other = _ticket_for(tech, client_obj)
    client.force_login(tech)

    attempts = [
        ('escalate',              reverse('core:ticket_escalate', args=[ticket.pk]), {}),
        ('dismiss needs-response', reverse('core:ticket_dismiss_response', args=[ticket.pk]),
         {'note': 'x'}),
        ('acknowledge overdue',   reverse('core:ticket_acknowledge_overdue', args=[ticket.pk]),
         {'note': 'x'}),
        ('log time',              reverse('core:ticket_add_time', args=[ticket.pk]), {'minutes': '15'}),
        ('link a ticket',         reverse('core:ticket_link_add', args=[ticket.pk]),
         {'ticket_number': other.ticket_number, 'link_type': 'related'}),
    ]
    for label, url, payload in attempts:
        resp = client.post(url, payload)
        assert resp.status_code == 403, f'a tech without edit rights could {label}'

    ticket.refresh_from_db()
    assert ticket.escalation_level == 1, 'the ticket was escalated despite the refusal'
    assert ticket.needs_response is True, 'the flag was cleared despite the refusal'


@pytest.mark.django_db
def test_converting_a_ticket_needs_both_grants(client, client_obj):
    """Convert creates a work order AND ends the ticket, so it needs both boxes."""
    wo_only = _role_tech('t_conv_wo', can_create_workorder=True)   # no ticket edit
    tkt_only = _role_tech('t_conv_tkt', can_edit_ticket=True)      # no WO create
    both = _role_tech('t_conv_both', can_create_workorder=True, can_edit_ticket=True)

    for user, expected in ((wo_only, 403), (tkt_only, 403), (both, 200)):
        ticket = _ticket_for(user, client_obj)
        client.force_login(user)
        resp = client.get(reverse('core:ticket_convert', args=[ticket.pk]))
        assert resp.status_code == expected, (
            f'{user.username} got {resp.status_code}, expected {expected}'
        )


@pytest.mark.django_db
def test_manage_users_box_now_grants_user_management(client):
    """The twelfth decorative checkbox, closed.

    User management tested _is_admin, so ticking "Manage Users" on a role did
    nothing at all. Both directions are asserted: the box grants it, and a role
    without it is refused.
    """
    granted = _role_tech('t_users_on')
    granted.role_obj.can_manage_users = True
    granted.role_obj.save()
    denied = _role_tech('t_users_off')

    client.force_login(granted)
    assert client.get(reverse('core:user_list')).status_code == 200
    assert client.get(reverse('core:user_create')).status_code == 200

    client.force_login(denied)
    assert client.get(reverse('core:user_list')).status_code == 403


# ── "Manage Users" must not be a route to administrator ─────────────────────
#
# Enforcing the previously-dead can_manage_users checkbox created a full
# privilege escalation, found by an outside reviewer who reproduced it live. The
# user form exposes is_staff and role_obj, and the set-password view took any
# target, so a non-admin user-manager had three independent ways to become or
# impersonate an administrator. Each one gets its own test.

def _user_manager(username):
    """A non-admin whose only power is managing users."""
    from core.models import Role
    role = Role.objects.create(name=f'UserMgr-{username}', can_manage_settings=False,
                               can_manage_users=True)
    return User.objects.create_user(username=username, password='x',
                                    is_staff=False, role_obj=role)


@pytest.mark.django_db
def test_user_manager_cannot_make_themselves_staff(client):
    """The reviewer's exact reproduction: POST your own edit form with is_staff=on.

    It returned 302 and is_staff became True, which satisfies _is_admin() and
    hands over the whole application.
    """
    mgr = _user_manager('esc_staff')
    client.force_login(mgr)
    resp = client.post(reverse('core:user_edit', args=[mgr.pk]), {
        'first_name': '', 'last_name': '', 'username': mgr.username,
        'email': '', 'phone': '', 'level': mgr.level,
        'role_obj': mgr.role_obj_id, 'is_staff': 'on', 'is_active': 'on',
    })
    mgr.refresh_from_db()
    assert mgr.is_staff is False, (
        f'a user-manager promoted themselves to admin (HTTP {resp.status_code})'
    )


@pytest.mark.django_db
def test_user_manager_cannot_assign_themselves_an_admin_role(client):
    """The same escalation wearing a different hat.

    Dropping is_staff from the form is not enough on its own: any role carrying
    can_manage_settings satisfies _is_admin() just as well.
    """
    from core.models import Role
    mgr = _user_manager('esc_role')
    admin_role = Role.objects.create(name='BackdoorAdmin', can_manage_settings=True)
    client.force_login(mgr)
    client.post(reverse('core:user_edit', args=[mgr.pk]), {
        'first_name': '', 'last_name': '', 'username': mgr.username,
        'email': '', 'phone': '', 'level': mgr.level,
        'role_obj': admin_role.pk, 'is_active': 'on',
    })
    mgr.refresh_from_db()
    assert mgr.role_obj_id != admin_role.pk, 'a user-manager gave themselves a Settings role'
    assert not _is_admin_for_test(mgr)


def _is_admin_for_test(user):
    from core.views import _is_admin
    return _is_admin(user)


@pytest.mark.django_db
def test_user_manager_cannot_reset_an_admins_password(client):
    """Otherwise they simply log in as the owner.

    Reported by inspection in the same review; the set-password view accepted any
    target. Both the form and the POST are checked, since rendering the page to a
    user who may not act is its own disclosure.
    """
    mgr = _user_manager('esc_pw')
    owner = User.objects.create_user(username='the_owner', password='ownerpass',
                                     is_staff=True, is_superuser=True)
    client.force_login(mgr)
    assert client.get(reverse('core:user_set_password', args=[owner.pk])).status_code == 403
    resp = client.post(reverse('core:user_set_password', args=[owner.pk]),
                       {'password1': 'hijacked-pw-1234', 'password2': 'hijacked-pw-1234'})
    assert resp.status_code == 403
    owner.refresh_from_db()
    assert owner.check_password('ownerpass'), "an admin's password was reset"


@pytest.mark.django_db
def test_user_manager_cannot_edit_or_delete_an_admin(client):
    mgr = _user_manager('esc_edit')
    owner = User.objects.create_user(username='owner2', password='x',
                                     is_staff=True, is_superuser=True)
    client.force_login(mgr)
    assert client.get(reverse('core:user_edit', args=[owner.pk])).status_code == 403
    assert client.post(reverse('core:user_delete', args=[owner.pk])).status_code == 403
    assert User.objects.filter(pk=owner.pk).exists()


@pytest.mark.django_db
def test_user_manager_cannot_create_an_admin(client):
    """Creation is the same escalation one step removed — make an admin, log in as it."""
    mgr = _user_manager('esc_create')
    client.force_login(mgr)
    client.post(reverse('core:user_create'), {
        'first_name': '', 'last_name': '', 'username': 'plantedadmin',
        'email': '', 'phone': '', 'level': 1, 'role_obj': '',
        'is_staff': 'on', 'is_active': 'on',
        'password1': 'a-long-enough-password-1', 'password2': 'a-long-enough-password-1',
    })
    planted = User.objects.filter(username='plantedadmin').first()
    if planted is not None:
        assert planted.is_staff is False, 'a user-manager created an administrator'


@pytest.mark.django_db
def test_a_real_admin_can_still_do_all_of_it(client):
    """The guard must restrict delegation, not break administration."""
    from core.models import Role
    admin = User.objects.create_user(username='realadmin', password='x',
                                     is_staff=True, is_superuser=True)
    staff_role = Role.objects.create(name='SettingsRole', can_manage_settings=True)
    target = User.objects.create_user(username='sometech', password='x')
    client.force_login(admin)
    resp = client.post(reverse('core:user_edit', args=[target.pk]), {
        'first_name': '', 'last_name': '', 'username': target.username,
        'email': '', 'phone': '', 'level': target.level,
        'role_obj': staff_role.pk, 'is_staff': 'on', 'is_active': 'on',
    })
    target.refresh_from_db()
    assert target.is_staff is True, f'an admin could not promote a user (HTTP {resp.status_code})'
    assert target.role_obj_id == staff_role.pk


@pytest.mark.django_db
def test_line_item_edits_are_authorized_by_their_host_not_by_work_orders(client, client_obj):
    """A shared endpoint must ask the record, not a single flag.

    WorkPerformedUpdateView/DeleteView are shared by six hosts. A blanket
    can_edit_workorder check was added ahead of the host-aware guard, which
    locked sales-only and estimate-only roles out of their own line items —
    reproduced by an outside reviewer on both. The permission has to come from
    what is being edited.
    """
    from decimal import Decimal
    from core.models import LineItem, Sale, Estimate

    sale = Sale.objects.create(sale_number=Sale.generate_sale_number(), client=client_obj)
    est = Estimate.objects.create(estimate_number=Estimate.generate_estimate_number(),
                                  client=client_obj)
    sale_line = LineItem.objects.create(content_object=sale, kind='part',
                                        description='Counter part', quantity=1,
                                        unit_price=Decimal('20.00'))
    est_line = LineItem.objects.create(content_object=est, kind='labor',
                                       description='Quoted work', quantity=1,
                                       unit_price=Decimal('30.00'))

    # Sales-only: no work-order rights at all, but must own its own sale lines.
    seller = _role_tech('t_sales_only', can_view_sales=True)
    client.force_login(seller)
    resp = client.post(reverse('core:work_performed_update', args=[sale_line.pk]),
                       {'custom_label': 'Counter part v2', 'quantity': '1', 'unit_price': '25.00'})
    assert resp.status_code == 200, 'a sales role was refused its own sale line'
    sale_line.refresh_from_db()
    assert sale_line.unit_price == Decimal('25.00')

    # Estimate-only: same, on a quote.
    quoter = _role_tech('t_est_only', can_view_estimates=True)
    client.force_login(quoter)
    resp = client.post(reverse('core:work_performed_update', args=[est_line.pk]),
                       {'custom_label': 'Quoted work v2', 'quantity': '1', 'unit_price': '35.00'})
    assert resp.status_code == 200, 'an estimate role was refused its own quote line'


@pytest.mark.django_db
def test_the_shared_line_endpoint_is_not_a_backdoor_to_other_hosts(client, client_obj):
    """Host-aware must still mean gated — the point is the right gate, not none.

    A sales-only role has no work-order rights, so it must not reach a work
    order's priced lines through the same shared endpoint.
    """
    from decimal import Decimal
    from core.models import LineItem

    wo = WorkOrder.objects.create(work_order_number=WorkOrder.generate_work_order_number(),
                                  client=client_obj, status='in_progress')
    wo_line = LineItem.objects.create(content_object=wo, kind='labor', description='Bench',
                                      quantity=1, unit_price=Decimal('50.00'))
    seller = _role_tech('t_sales_backdoor', can_view_sales=True)
    client.force_login(seller)
    resp = client.post(reverse('core:work_performed_update', args=[wo_line.pk]),
                       {'custom_label': 'hijacked', 'quantity': '1', 'unit_price': '1.00'})
    assert resp.status_code == 403
    wo_line.refresh_from_db()
    assert wo_line.unit_price == Decimal('50.00')
    assert client.post(reverse('core:work_performed_delete', args=[wo_line.pk])).status_code == 403


@pytest.mark.django_db
def test_ticket_quick_status_rejects_a_status_that_does_not_exist(client, client_obj):
    """The ticket twin of the work-order status validation.

    The quick dropdown gated close-vs-edit correctly but never checked that the
    submitted status exists. Ticket.status has no `choices=` and
    apply_status_change() assigns whatever it receives, so a user with edit
    rights could invent a status by POST and leave the ticket in a state no
    list, queue or report recognises.
    """
    tech = _role_tech('t_status_bogus', can_edit_ticket=True, can_close_tickets=True)
    ticket = _ticket_for(tech, client_obj)
    client.force_login(tech)
    for bogus in ('banana', 'in-progress', ''):
        client.post(reverse('core:ticket_status_update', args=[ticket.pk]), {'status': bogus})
        ticket.refresh_from_db()
        assert ticket.status == 'open', f'posting status={bogus!r} was saved'

    client.post(reverse('core:ticket_status_update', args=[ticket.pk]), {'status': 'in_progress'})
    ticket.refresh_from_db()
    assert ticket.status == 'in_progress', 'a real status must still work'


@pytest.mark.django_db
def test_the_ui_does_not_offer_controls_the_server_refuses(client, client_obj):
    """Buttons and the server must agree about what a role can do.

    An outside reviewer noted the server denied correctly while the pages still
    rendered Convert, Dismiss, Escalate, the ticket timer, Apply Checklist, the
    line-item buttons and the checklist dropdowns. Not a security hole — the
    server refuses — but every one of those is a control that fails when clicked,
    which teaches people the app is broken rather than that they lack a permission.
    """
    from core.models import WorkOrderItem
    tech = _role_tech('t_ui_matches')      # no edit rights of any kind
    ticket = _ticket_for(tech, client_obj, needs_response=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress',
    )
    # ⚠ item_type='checklist' matters: the detail view filters on it, so a
    # default-type item renders no checklist at all and both assertions below
    # would pass without proving anything.
    WorkOrderItem.objects.create(work_order=wo, item_type='checklist',
                                 description='Check PSU')
    client.force_login(tech)

    tb = client.get(reverse('core:ticket_detail', args=[ticket.pk])).content.decode()
    wb = client.get(reverse('core:work_order_detail', args=[wo.pk])).content.decode()

    assert 'Convert to Work Order' not in tb
    assert 'dismissOpen = !dismissOpen' not in tb
    assert '/escalate/' not in tb
    assert 'tk-timer-log-form' not in tb, 'the timer posts time and would be refused'
    assert 'Apply Checklist' not in wb
    assert '+ Custom' not in wb
    assert '/items/' not in wb and '/check/' not in wb, (
        'the checklist dropdowns post to a refused endpoint'
    )


@pytest.mark.django_db
def test_the_ui_still_offers_everything_to_a_role_that_may_use_it(client, client_obj):
    """The mirror: hiding must be driven by permission, not by hiding everything."""
    from core.models import WorkOrderItem
    tech = _role_tech('t_ui_full', can_edit_ticket=True, can_close_tickets=True,
                      can_create_workorder=True, can_edit_workorder=True)
    ticket = _ticket_for(tech, client_obj, needs_response=True)
    wo = WorkOrder.objects.create(
        work_order_number=WorkOrder.generate_work_order_number(),
        client=client_obj, assigned_to=tech, status='in_progress',
    )
    # ⚠ item_type='checklist' matters: the detail view filters on it, so a
    # default-type item renders no checklist at all and both assertions below
    # would pass without proving anything.
    WorkOrderItem.objects.create(work_order=wo, item_type='checklist',
                                 description='Check PSU')
    client.force_login(tech)

    tb = client.get(reverse('core:ticket_detail', args=[ticket.pk])).content.decode()
    wb = client.get(reverse('core:work_order_detail', args=[wo.pk])).content.decode()

    assert 'Convert to Work Order' in tb
    assert 'dismissOpen = !dismissOpen' in tb
    assert 'tk-timer-log-form' in tb
    assert 'Apply Checklist' in wb
    assert '+ Custom' in wb
    assert '/check/' in wb, 'the checklist dropdowns must be offered to a role that may use them'


@pytest.mark.django_db
def test_user_manager_cannot_grant_a_role_carrying_powers_they_lack(client):
    """The general rule, not a list of "sensitive" flags.

    The first version excluded only can_manage_settings, so a delegated manager
    could still hand themselves a role carrying card charging, MFA reset, the org
    credential vault or ticket deletion — a reviewer reproduced it and the POST
    returned 302 with role_obj changed. A role is assignable only if everything
    it grants is something the actor already holds.
    """
    from core.models import Role
    mgr = _user_manager('esc_flags')
    for flag in ('can_process_payments', 'can_reset_user_mfa',
                 'can_view_org_credentials', 'can_delete_ticket',
                 'can_view_device_credentials'):
        juicy = Role.objects.create(name=f'Juicy-{flag}', **{flag: True})
        client.force_login(mgr)
        client.post(reverse('core:user_edit', args=[mgr.pk]), {
            'first_name': '', 'last_name': '', 'username': mgr.username,
            'email': '', 'phone': '', 'level': mgr.level,
            'role_obj': juicy.pk, 'is_active': 'on',
        })
        mgr.refresh_from_db()
        assert mgr.role_obj_id != juicy.pk, f'a manager granted themselves {flag}'


@pytest.mark.django_db
def test_a_manager_can_still_assign_a_role_within_their_own_powers(client):
    """The rule restricts delegation; it must not make delegation useless."""
    from core.models import Role
    mgr = _user_manager('deleg_ok')
    mgr.role_obj.can_edit_ticket = True
    mgr.role_obj.save()
    plain = Role.objects.create(name='PlainBench', can_edit_ticket=True)
    target = User.objects.create_user(username='benchie', password='x')
    client.force_login(mgr)
    client.post(reverse('core:user_edit', args=[target.pk]), {
        'first_name': '', 'last_name': '', 'username': target.username,
        'email': '', 'phone': '', 'level': 1,
        'role_obj': plain.pk, 'is_active': 'on',
    })
    target.refresh_from_db()
    assert target.role_obj_id == plain.pk, 'a manager could not assign a role within their powers'


@pytest.mark.django_db
def test_user_manager_cannot_raise_their_own_escalation_level(client):
    """Level is a permission: ticket visibility reads it.

    _scope_tickets_for() shows a technician tickets escalated up to their own
    level, so raising it widens what they can read. A reviewer took an L1
    delegated manager to L3 through this form; the POST returned 302.
    """
    mgr = _user_manager('esc_level')
    assert mgr.level == 1
    client.force_login(mgr)
    client.post(reverse('core:user_edit', args=[mgr.pk]), {
        'first_name': '', 'last_name': '', 'username': mgr.username,
        'email': '', 'phone': '', 'level': 3,
        'role_obj': mgr.role_obj_id, 'is_active': 'on',
    })
    mgr.refresh_from_db()
    assert mgr.level == 1, 'a manager raised their own escalation level'


@pytest.mark.django_db
def test_user_manager_cannot_act_on_someone_holding_powers_they_lack(client):
    """Half the rule is not the rule.

    Blocking role assignment but still allowing a password reset on a
    better-equipped colleague leaves the same door open: sign in as them and use
    the capability. "Outranks" is measured across the whole permission set.
    """
    from core.models import Role
    mgr = _user_manager('esc_lateral')
    payer_role = Role.objects.create(name='Cashier', can_process_payments=True)
    payer = User.objects.create_user(username='cashier_x', password='cashpass',
                                     role_obj=payer_role)
    client.force_login(mgr)
    assert client.get(reverse('core:user_edit', args=[payer.pk])).status_code == 403
    resp = client.post(reverse('core:user_set_password', args=[payer.pk]),
                       {'password1': 'taken-over-1234', 'password2': 'taken-over-1234'})
    assert resp.status_code == 403
    payer.refresh_from_db()
    assert payer.check_password('cashpass')


@pytest.mark.django_db
def test_an_admin_is_still_unrestricted_by_any_of_this(client):
    from core.models import Role
    admin = User.objects.create_user(username='boss_unrestricted', password='x',
                                     is_staff=True, is_superuser=True)
    juicy = Role.objects.create(name='EverythingRole', can_process_payments=True,
                                can_manage_settings=True)
    target = User.objects.create_user(username='promote_me', password='x')
    client.force_login(admin)
    client.post(reverse('core:user_edit', args=[target.pk]), {
        'first_name': '', 'last_name': '', 'username': target.username,
        'email': '', 'phone': '', 'level': 3,
        'role_obj': juicy.pk, 'is_staff': 'on', 'is_active': 'on',
    })
    target.refresh_from_db()
    assert target.role_obj_id == juicy.pk and target.level == 3 and target.is_staff


@pytest.mark.django_db
def test_user_manager_cannot_act_on_a_higher_level_account(client):
    """Escalation level is rank, so it belongs in the outranks comparison.

    Reproduced by a reviewer: an L1 delegated manager and an L3 technician on the
    SAME non-admin role — identical flags, so the flag comparison passed — and
    the manager reset the L3's password (200 on the page, 302 on the POST).
    Taking over that account buys ticket visibility an L1 never had, because
    _scope_tickets_for() shows tickets escalated up to the viewer's level.

    Every view that acts on a user is covered, not just the one that was probed.
    """
    from core.models import Role
    shared = Role.objects.create(name='SharedBench', can_manage_users=True)
    mgr = User.objects.create_user(username='lvl1_mgr', password='x',
                                   role_obj=shared, level=1)
    senior = User.objects.create_user(username='lvl3_tech', password='seniorpass',
                                      role_obj=shared, level=3)
    client.force_login(mgr)

    assert client.get(reverse('core:user_set_password', args=[senior.pk])).status_code == 403
    assert client.post(reverse('core:user_set_password', args=[senior.pk]),
                       {'password1': 'stolen-1234abcd',
                        'password2': 'stolen-1234abcd'}).status_code == 403
    assert client.get(reverse('core:user_edit', args=[senior.pk])).status_code == 403
    assert client.post(reverse('core:user_delete', args=[senior.pk])).status_code == 403
    assert client.post(reverse('core:user_mfa_reset', args=[senior.pk])).status_code == 403

    senior.refresh_from_db()
    assert senior.check_password('seniorpass'), "a higher-level account's password was reset"
    assert User.objects.filter(pk=senior.pk).exists()


@pytest.mark.django_db
def test_a_manager_can_still_act_on_peers_and_juniors(client):
    """Rank blocks upward, not sideways or down — delegation must stay usable."""
    from core.models import Role
    shared = Role.objects.create(name='SharedBench2', can_manage_users=True)
    mgr = User.objects.create_user(username='lvl2_mgr', password='x',
                                   role_obj=shared, level=2)
    junior = User.objects.create_user(username='lvl1_tech', password='old',
                                      role_obj=shared, level=1)
    peer = User.objects.create_user(username='lvl2_tech', password='old',
                                    role_obj=shared, level=2)
    client.force_login(mgr)
    for target in (junior, peer):
        resp = client.post(reverse('core:user_set_password', args=[target.pk]),
                           {'password1': 'newpass-1234abcd', 'password2': 'newpass-1234abcd'})
        assert resp.status_code == 302, f'blocked on {target.username} (level {target.level})'
        target.refresh_from_db()
        assert target.check_password('newpass-1234abcd')


@pytest.mark.django_db
def test_mfa_reset_requires_outranking_the_target(client):
    """Holding can_reset_user_mfa means you may clear somebody's, not anybody's.

    This view acted on any target with no rank check at all — it was the only one
    of the five that did. Clearing two-factor on an account above you removes the
    last control between a stolen password and that account.
    """
    from core.models import Role
    resetter_role = Role.objects.create(name='MFAHelper', can_reset_user_mfa=True)
    helper = User.objects.create_user(username='mfa_helper', password='x',
                                      role_obj=resetter_role, level=1)
    owner = User.objects.create_user(username='mfa_owner', password='x',
                                     is_staff=True, is_superuser=True)
    client.force_login(helper)
    assert client.post(reverse('core:user_mfa_reset', args=[owner.pk])).status_code == 403


@pytest.mark.django_db
def test_every_authority_dimension_is_compared():
    """A dimension declared as rank must actually be enforced.

    Three rounds of findings came from implementing "outranks" one dimension at a
    time, each fix adding whichever had just been exploited. This asserts each
    declared dimension independently blocks, so adding one to the list without
    wiring it up fails here rather than in a review.
    """
    from core.models import Role
    from core.views import _AUTHORITY_DIMENSIONS, _outranks_or_equal

    assert set(_AUTHORITY_DIMENSIONS) == {'admin', 'flags', 'level'}

    plain = Role.objects.create(name='DimPlain')
    base = User.objects.create_user(username='dim_base', password='x',
                                    role_obj=plain, level=1)

    # admin
    boss = User.objects.create_user(username='dim_admin', password='x',
                                    is_staff=True, is_superuser=True)
    assert not _outranks_or_equal(base, boss), 'admin status is not compared'

    # flags
    juicy = Role.objects.create(name='DimJuicy', can_process_payments=True)
    payer = User.objects.create_user(username='dim_payer', password='x',
                                     role_obj=juicy, level=1)
    assert not _outranks_or_equal(base, payer), 'permission flags are not compared'

    # level
    senior = User.objects.create_user(username='dim_senior', password='x',
                                      role_obj=plain, level=3)
    assert not _outranks_or_equal(base, senior), 'escalation level is not compared'

    # and an equal peer is fine in every direction
    peer = User.objects.create_user(username='dim_peer', password='x',
                                    role_obj=plain, level=1)
    assert _outranks_or_equal(base, peer) and _outranks_or_equal(peer, base)


@pytest.mark.django_db
def test_user_list_only_offers_actions_the_server_will_allow(client):
    """The user list must not draw buttons that 403 on click.

    Every action was rendered for every account: Edit, Set Password, Delete,
    Reset MFA, and Manage Roles. The server refuses them correctly, so this was
    never a bypass — but in the one area of the app being hardened for privilege
    escalation, showing a delegated manager a Set Password link for the owner is
    the worst possible place to teach people that buttons lie.

    Each row is now annotated with the same _may_act_on_user() result the views
    use, so the page cannot disagree with them.
    """
    from core.models import Role
    shared = Role.objects.create(name='ListMgr', can_manage_users=True)
    mgr = User.objects.create_user(username='list_mgr', password='x',
                                   role_obj=shared, level=1)
    senior = User.objects.create_user(username='list_senior', password='x',
                                      role_obj=shared, level=3)
    owner = User.objects.create_user(username='list_owner', password='x',
                                     is_staff=True, is_superuser=True)
    junior = User.objects.create_user(username='list_junior', password='x',
                                      role_obj=shared, level=1)

    client.force_login(mgr)
    body = client.get(reverse('core:user_list')).content.decode()

    for unreachable in (senior, owner):
        assert reverse('core:user_edit', args=[unreachable.pk]) not in body, (
            f'Edit offered for {unreachable.username}, who outranks the manager'
        )
        assert reverse('core:user_set_password', args=[unreachable.pk]) not in body
        assert reverse('core:user_delete', args=[unreachable.pk]) not in body
        assert reverse('core:user_mfa_reset', args=[unreachable.pk]) not in body

    # A peer stays fully actionable — this hides by rank, not by hiding everything.
    assert reverse('core:user_edit', args=[junior.pk]) in body
    assert reverse('core:user_set_password', args=[junior.pk]) in body

    # Roles are Settings, and a delegated manager is not an admin.
    assert reverse('core:role_list') not in body


@pytest.mark.django_db
def test_an_admin_still_sees_every_user_action(client):
    """The mirror — the gating must be rank-driven, not blanket."""
    from core.models import Role
    admin = User.objects.create_user(username='list_admin', password='x',
                                     is_staff=True, is_superuser=True)
    other = User.objects.create_user(username='list_other', password='x',
                                     role_obj=Role.objects.create(name='ListPlain'))
    client.force_login(admin)
    body = client.get(reverse('core:user_list')).content.decode()
    assert reverse('core:user_edit', args=[other.pk]) in body
    assert reverse('core:user_set_password', args=[other.pk]) in body
    assert reverse('core:user_delete', args=[other.pk]) in body
    assert reverse('core:role_list') in body, 'an admin must still reach Manage Roles'


@pytest.mark.django_db
def test_a_delegated_user_manager_can_actually_reach_the_user_list(client):
    """A granted permission must have a route to it.

    The only navigation to Settings was gated on Django `is_staff`, and the user
    list lives under Settings — so a role granted "Manage Users" had no way to
    reach the thing it had just been granted, short of typing the URL. That is
    the same lying checkbox this branch exists to end, pointing the other way:
    enforcement agreed with the box, the product never offered it.
    """
    mgr = _user_manager('nav_mgr')
    client.force_login(mgr)
    body = client.get(reverse('core:dashboard')).content.decode()
    assert reverse('core:user_list') in body, (
        'a user manager has no link to the user list'
    )
    assert reverse('core:settings') not in body, (
        'Settings is admin-only; offering it here is a link to a 403'
    )


@pytest.mark.django_db
def test_a_settings_admin_without_django_staff_can_reach_settings(client):
    """`is_admin` is staff OR can_manage_settings — the nav only honoured the first.

    views._is_admin() has always accepted a role carrying can_manage_settings, so
    such a user could open Settings by URL while the app showed them no way in.
    Pre-existing, and exactly the audience the role exists for.
    """
    from core.models import Role
    role = Role.objects.create(name='SettingsAdminRole', can_manage_settings=True)
    admin_by_role = User.objects.create_user(username='role_admin', password='x',
                                             is_staff=False, role_obj=role)
    client.force_login(admin_by_role)
    body = client.get(reverse('core:dashboard')).content.decode()
    assert reverse('core:settings') in body, (
        'a can_manage_settings role has no link to Settings'
    )
    assert client.get(reverse('core:settings')).status_code == 200


@pytest.mark.django_db
def test_the_user_list_backlink_goes_somewhere_the_viewer_can_open(client):
    """The reviewer's finding: ← Settings was drawn for people Settings refuses."""
    mgr = _user_manager('backlink_mgr')
    client.force_login(mgr)
    body = client.get(reverse('core:user_list')).content.decode()
    assert reverse('core:settings') not in body
    assert reverse('core:dashboard') in body

    admin = User.objects.create_user(username='backlink_admin', password='x',
                                     is_staff=True, is_superuser=True)
    client.force_login(admin)
    body = client.get(reverse('core:user_list')).content.decode()
    assert reverse('core:settings') in body, 'an admin should still go back to Settings'


@pytest.mark.django_db
def test_a_plain_technician_sees_neither_settings_nor_users(client, client_obj):
    """The mirror — the new Users link must not leak to roles without the grant."""
    tech = _role_tech('nav_plain')
    client.force_login(tech)
    body = client.get(reverse('core:dashboard')).content.decode()
    assert reverse('core:settings') not in body
    assert reverse('core:user_list') not in body


@pytest.mark.django_db
def test_the_chrome_and_the_server_agree_for_every_shape_of_user(client, rf):
    """The UI's idea of a permission must BE the server's, not resemble it.

    Twice now the context processor has paraphrased a rule and drifted from it.
    `is_admin` was written as `is_staff or role_obj.can_manage_settings`, dropping
    the legacy `role == 'admin'` fallback that has_perm_flag honours — so a legacy
    admin got 200 from /settings/ while the nav offered no way in and the user list
    drew them the non-admin backlink. `can_manage_users` used has_perm_flag where
    the server uses _role_flag, which differ for a user with no role at all.

    Rather than assert today's answers, this compares the context processor against
    the view helper for every shape of account the app can produce. A new user
    shape, or a new paraphrase, fails here.
    """
    from core.context_processors import site_settings
    from core.models import Role
    from core import views as v

    settings_role = Role.objects.create(name='CtxSettings', can_manage_settings=True)
    users_role = Role.objects.create(name='CtxUsers', can_manage_users=True)
    plain_role = Role.objects.create(name='CtxPlain')

    shapes = {
        'django staff':      User.objects.create_user(username='ctx_staff', password='x', is_staff=True),
        'superuser':         User.objects.create_user(username='ctx_super', password='x',
                                                      is_staff=True, is_superuser=True),
        'settings role':     User.objects.create_user(username='ctx_setrole', password='x',
                                                      role_obj=settings_role),
        'user-manager role': User.objects.create_user(username='ctx_umrole', password='x',
                                                      role_obj=users_role),
        'plain role':        User.objects.create_user(username='ctx_plain', password='x',
                                                      role_obj=plain_role),
        'no role at all':    User.objects.create_user(username='ctx_norole', password='x'),
        # ⚠ The legacy path: role_obj is None and the old CharField carries 'admin'.
        # has_perm_flag() still returns True for everything here, so the server
        # treats this account as an admin. This is the shape the reviewer found.
        'legacy admin':      User.objects.create_user(username='ctx_legacy', password='x',
                                                      is_staff=False, role='admin'),
    }
    # ⚠ DERIVED from what the chrome actually publishes — not a list anyone has to
    # remember to extend. Two earlier versions were hand-written: the first compared
    # six values and omitted the eleven this branch is mostly about, so a reviewer
    # probed those by hand; the second added the eleven but still named the six, so
    # a NEW permission would have slipped through. Every boolean the context exposes
    # must have a server helper of the same name, and must equal it.
    probe = rf.get('/')
    probe.user = User.objects.create_user(username='ctx_probe', password='x')
    published = [
        key for key, value in site_settings(probe).items()
        if isinstance(value, bool)
    ]
    missing = [k for k in published if not hasattr(v, f'_{k}')]
    assert not missing, (
        'the chrome publishes permissions with no server helper to follow, so '
        f'nothing can keep them in step: {missing}'
    )
    checks = {key: getattr(v, f'_{key}') for key in published}
    assert len(checks) >= 17, (
        f'expected at least the 17 known permissions, found {len(checks)} — has the '
        'context stopped publishing some?'
    )

    mismatches = []
    for shape, user in shapes.items():
        request = rf.get('/')
        request.user = user
        ctx = site_settings(request)
        for key, helper in checks.items():
            if bool(ctx[key]) != bool(helper(user)):
                mismatches.append(
                    f'{shape}: template {key}={ctx[key]!r} but server says {helper(user)!r}'
                )
    assert not mismatches, (
        'the chrome disagrees with the server about what these users may do:\n  '
        + '\n  '.join(mismatches)
    )


@pytest.mark.django_db
def test_a_legacy_admin_is_offered_the_settings_it_can_open(client):
    """The reviewer's exact case, end to end rather than by helper comparison."""
    legacy = User.objects.create_user(username='legacy_admin_nav', password='x',
                                      is_staff=False, role='admin')
    client.force_login(legacy)
    assert client.get(reverse('core:settings')).status_code == 200, (
        'precondition: the server treats a legacy admin as an admin'
    )
    body = client.get(reverse('core:dashboard')).content.decode()
    assert reverse('core:settings') in body, 'the nav offered no way into Settings'
    users_page = client.get(reverse('core:user_list')).content.decode()
    assert reverse('core:settings') in users_page, 'sent back to a page it can open'
    assert '← Dashboard' not in users_page, (
        'a legacy admin was given the non-admin backlink'
    )


@pytest.mark.django_db
def test_recently_closed_shows_completed_work(client, client_obj):
    """The dashboard panel must show finished jobs, which means 'completed'.

    It filtered a hand-written ['closed', 'cancelled'] — a second definition of
    "finished" written three lines below the constant that exists to be the only
    one. Retiring the `closed` status left it naming a state nothing can hold, so
    "Recently Closed" could never show a completed job again.

    ⚠ This panel is on the TECHNICIAN dashboard, not the owner's — admins get a
    separate _admin_dashboard() that never builds this key. I first reported it
    as "the dashboard", which would have had Mike looking for a panel his own
    account never renders.
    """
    tech = _role_tech('rc_tech')
    done = WorkOrder.objects.create(work_order_number=WorkOrder.generate_work_order_number(),
                                    client=client_obj, status='completed')
    scrapped = WorkOrder.objects.create(work_order_number=WorkOrder.generate_work_order_number(),
                                        client=client_obj, status='cancelled')
    live = WorkOrder.objects.create(work_order_number=WorkOrder.generate_work_order_number(),
                                    client=client_obj, status='in_progress')
    client.force_login(tech)
    shown = {w.work_order_number for w in client.get(reverse('core:dashboard')).context['recently_closed']}
    assert done.work_order_number in shown, 'a completed job never reaches Recently Closed'
    assert scrapped.work_order_number in shown
    assert live.work_order_number not in shown


@pytest.mark.django_db
def test_finished_work_leaves_the_my_work_sidebar(client, client_obj):
    """Pre-existing, same class: the sidebar never excluded 'completed'.

    It excluded 'closed' and 'cancelled' only — so a finished job stayed in My
    Work indefinitely, while the dashboard's own open-WO query excluded exactly
    those jobs correctly. Two views, two definitions, one of them wrong.
    """
    tech = _role_tech('sidebar_tech', can_edit_workorder=True)
    done = WorkOrder.objects.create(work_order_number=WorkOrder.generate_work_order_number(),
                                    client=client_obj, assigned_to=tech, status='completed')
    live = WorkOrder.objects.create(work_order_number=WorkOrder.generate_work_order_number(),
                                    client=client_obj, assigned_to=tech, status='in_progress')
    client.force_login(tech)
    body = client.get(reverse('core:sidebar_fragment')).content.decode()
    assert live.work_order_number in body
    assert done.work_order_number not in body, 'a completed job stayed in My Work'


@pytest.mark.django_db
def test_no_view_keeps_its_own_copy_of_what_finished_means():
    """One definition per record type, enforced.

    Every regression in this area has been a second hand-written list drifting
    from the constant beside it. This fails if a new one appears.
    """
    import inspect
    from core import views
    src = inspect.getsource(views)
    # Strip the constants' own definitions and the commentary explaining the history.
    body = '\n'.join(
        line for line in src.splitlines()
        if not line.lstrip().startswith('#')
        and 'WO_CLOSED_STATUSES = ' not in line
        and 'TICKET_CLOSED_STATUSES = ' not in line
    )
    strays = re.findall(r"status__in=[\(\[][^\)\]]*['\"](?:completed|cancelled)['\"][^\)\]]*[\)\]]", body)
    assert not strays, (
        'a view is carrying its own list of finished statuses instead of using '
        f'WO_CLOSED_STATUSES: {strays}'
    )


def test_the_suite_never_writes_into_the_real_application_log():
    """A test run must not append to logs/murphys_bench.log.

    The log path used to be fixed per install, so every full run put ~15 records into
    the app's own log, several of them indistinguishable from genuine failures:

        WARNING ... views ... Outbound email test failed for host smtp.example.com:
        535 mail.internal.example: auth failed

    Running the suite on a real box is normal here, and mb-test's log had eight such
    lines in it. That file is what the product now points an operator at when outbound
    email fails, so a manufactured failure sitting in it is the product lying to whoever
    reads it. conftest.py redirects MB_LOG_FILE for the duration of a run.

    The marker assertion below is not decoration: without it this test would also pass
    if logging were broken or silenced entirely, which is the vacuous-pass shape that
    has bitten this suite before.
    """
    import uuid
    from pathlib import Path
    from django.conf import settings

    real_log = _repo_root() / 'logs' / 'murphys_bench.log'
    existed = real_log.exists()
    size_before = real_log.stat().st_size if existed else None

    assert settings.LOG_FILE != real_log, (
        'the log seam is not in effect: settings.LOG_FILE still points at the real '
        f'application log ({real_log}). conftest.py should have redirected it.'
    )

    marker = f'log-isolation-guard-{uuid.uuid4()}'
    logging.getLogger('core').warning(marker)
    for handler in logging.getLogger('core').handlers:
        handler.flush()

    # The write really happened, just somewhere disposable.
    assert marker in Path(settings.LOG_FILE).read_text(), (
        'the core logger did not write to the redirected log, so this test proves '
        'nothing about where log records go'
    )

    if existed:
        assert real_log.stat().st_size == size_before, (
            f'the test run appended to {real_log}'
        )
    else:
        assert not real_log.exists(), f'the test run created {real_log}'
