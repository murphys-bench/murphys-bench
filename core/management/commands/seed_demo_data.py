"""Populate an install with obviously-fake demo data.

Replaces the 8-step manual checklist in SETUP.md §10. Two real uses:

  * A rebuilt test box, or a fresh evaluation install, becomes usable in one
    command instead of an evening of clicking.
  * A clean-room install check can then prove the app WORKS (a ticket converts, a
    report renders) rather than only that it starts.

⚠ SAFETY. This writes client-shaped records, so it must never land on a real
install. Two independent guards, both requiring --force to override:
  1. refuses when DEBUG=False (i.e. a production-style install), and
  2. refuses when the database already holds clients it did not create.

Neither guard is cosmetic — the seeded names are unmistakably fake, but fake
records mixed into a real client list are still a mess someone has to clean up
by hand. To clear an existing test box first, use `reset_operational_data`, which
removes operational records while keeping configuration.

Usage:
    python manage.py seed_demo_data
    python manage.py seed_demo_data --force        # override the guards
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core import factories
from core.models import Client, Invoice, LineItem, Ticket, WorkOrder


class Command(BaseCommand):
    help = 'Create obviously-fake demo data for a test or evaluation install.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Seed even when DEBUG=False or clients already exist. '
                 'Never use this on a production install.',
        )
        parser.add_argument(
            '--new-install', action='store_true',
            help="For scripts/install.sh. Skips the DEBUG=False check ONLY — the "
                 "existing-clients check still applies, so re-running the installer "
                 "on a live shop cannot inject demo data.",
        )

    def handle(self, *args, **options):
        force = options['force']
        self._check_safe(force, options['new_install'])

        with transaction.atomic():
            summary = self._seed()

        self.stdout.write(self.style.SUCCESS('\nDemo data created:'))
        for label, count in summary:
            self.stdout.write(f'  {count:>3}  {label}')
        self.stdout.write(
            '\nEvery record is fake: example.com/example.org addresses, 555 phone\n'
            'numbers, invented business names. Remove it all with:\n'
            '  manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"\n'
        )

    def _check_safe(self, force, new_install=False):
        """Fail loud rather than quietly seeding something real."""
        if force:
            self.stdout.write(self.style.WARNING(
                '--force: skipping the production and existing-data guards.'))
            return

        # ⚠ --new-install deliberately relaxes ONLY the DEBUG check, never the
        # existing-clients check. scripts/install.sh writes DEBUG=False, so it needs
        # the first waived to seed a fresh box at all — but install.sh is documented
        # as safe to re-run over an existing install (it is the v0.4.52 recovery
        # path), and a re-run on a live shop must not inject demo records into real
        # client data. The second guard is what makes that impossible.
        if not settings.DEBUG and not new_install:
            raise CommandError(
                'Refusing to seed: DEBUG=False, which means this looks like a real\n'
                'install. Demo data mixed into real client records has to be cleaned\n'
                'up by hand. If this genuinely is a test box, re-run with --force.'
            )

        existing = Client.objects.filter(is_unsorted=False).count()
        if existing:
            raise CommandError(
                f'Refusing to seed: {existing} client(s) already exist. Seeding on top\n'
                'would interleave fake records with whatever is already here.\n'
                'Clear the box first:\n'
                '  manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"\n'
                'or re-run with --force to add demo data anyway.'
            )

    def _seed(self):
        # Catalog first — priced lines reference it.
        catalog = [factories.CatalogItemFactory() for _ in range(5)]
        diagnostic = catalog[0]

        # Two business clients and one residential, each with contacts + devices,
        # so both client types and the walk-in-less path are represented.
        business_a = factories.ClientFactory()
        business_b = factories.ClientFactory()
        residential = factories.ResidentialClientFactory()

        contacts = []
        for client in (business_a, business_b, residential):
            primary = factories.ContactFactory(client=client, is_primary=True)
            factories.ContactPhoneFactory(contact=primary)
            contacts.append(primary)
        # A second contact on one business, since real shops have more than one.
        contacts.append(factories.ContactFactory(client=business_a))

        devices = [
            factories.CredentialedDeviceFactory(
                client=business_a, assigned_contact=contacts[0]),
            factories.DeviceFactory(client=business_b, device_type='laptop',
                                    name='Reception Laptop'),
            factories.DeviceFactory(client=residential, device_type='desktop',
                                    name='Home Office Tower'),
        ]

        # An open ticket that stays a ticket, and one converted to a work order,
        # which is the spine of the app.
        open_ticket = factories.TicketFactory(
            client=business_b, contact=contacts[1], device=devices[1])

        converted_ticket = factories.TicketFactory(
            client=business_a, contact=contacts[0], device=devices[0],
            status='converted')
        work_order = factories.WorkOrderFactory(
            client=business_a, contact=contacts[0], device=devices[0],
            ticket=converted_ticket, reported_problem=converted_ticket.description)

        # Priced labour on the work order, so totals and the repair report have
        # something to show.
        factories.LineItemFactory(
            content_object=work_order, description='Bench diagnostic',
            catalog_item=diagnostic, unit_price=diagnostic.default_price)
        factories.LineItemFactory(
            content_object=work_order, kind='part',
            description='Replacement power supply', unit_price='48.50')

        # A residential walk-in style repair with no originating ticket — work does
        # not always arrive as a ticket, and MB supports that deliberately.
        standalone_wo = factories.WorkOrderFactory(
            client=residential, contact=contacts[2], device=devices[2],
            service_type='onsite', status='completed',
            completed_date=timezone.now(),   # DateTimeField, not a date
            reported_problem='Slow boot and fan noise. Demo work order.')
        factories.LineItemFactory(
            content_object=standalone_wo, description='Onsite hour',
            unit_price='110.00')

        # A managed client: the Contract is what MAKES it managed.
        contract = factories.ContractFactory(client=business_a)
        factories.LineItemFactory(
            content_object=contract, kind='labor',
            description='Managed endpoint (monthly)', quantity=12,
            unit_price='35.00')

        # A counter sale with no client, exercising the anonymous lane.
        counter_sale = factories.SaleFactory()
        factories.LineItemFactory(
            content_object=counter_sale, kind='part',
            description='USB-C cable, 2m', unit_price='19.99')

        return [
            ('catalog items', len(catalog)),
            ('clients (2 business, 1 residential)', 3),
            ('contacts', len(contacts)),
            ('devices (1 with demo credentials)', len(devices)),
            ('tickets (1 open, 1 converted)', Ticket.objects.count()),
            ('work orders (1 from ticket, 1 standalone)', WorkOrder.objects.count()),
            ('priced line items', LineItem.objects.count()),
            ('managed contracts', 1),
            ('counter sales', 1),
            ('invoices (auto-created per work order)', Invoice.objects.count()),
        ]
