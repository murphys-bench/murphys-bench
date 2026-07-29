"""factory_boy factories for obviously-fake demo and test data.

Two consumers:
  * `manage.py seed_demo_data` — populates a fresh install or a rebuilt test box
    so it can be evaluated without an 8-step manual checklist (SETUP.md §10).
  * tests, which may use these instead of hand-building objects.

⚠ EVERY VALUE MUST BE UNMISTAKABLY FAKE. This data lands on demo boxes and test
VMs that other people see, and MB is a public project. Follow the conventions the
July 2026 repo-hygiene pass established (it had to scrub real prod IPs and a real
name out of the test fixtures):

  * emails      -> example.com / example.org  (RFC 2606 reserved)
  * phones      -> 555 prefixes               (reserved for fiction)
  * IPs         -> 192.0.2.x                  (RFC 5737 documentation range)
  * businesses  -> invented names that cannot be mistaken for a real client
  * people      -> invented names, never anyone real

`factory-boy` was already pinned in requirements.txt and entirely unused before
this module existed.
"""
import factory
from django.utils import timezone

from .models import (
    CatalogItem, Client, Contact, ContactPhone, Contract, Device, LineItem,
    Sale, Ticket, WorkOrder,
)

# Invented businesses and people. Deliberately a little whimsical: a reader
# should never have to wonder whether a record is real.
BUSINESS_NAMES = [
    'Rivet & Rose Bakery', 'Thornbury Dental Group', 'Kestrel Freight Co',
    'Lantern Hollow Books', 'Pemberton Tile & Stone',
]
PEOPLE = [
    ('Dora', 'Whitfield'), ('Amos', 'Pettigrew'), ('Ingrid', 'Salcedo'),
    ('Rupert', 'Danforth'), ('Marisol', 'Okonkwo'), ('Felix', 'Barrowman'),
]


class ClientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Client
        django_get_or_create = ('name',)

    name = factory.Iterator(BUSINESS_NAMES)
    client_type = 'business'
    email = factory.LazyAttribute(
        lambda o: f"office@{o.name.split()[0].lower().strip('&')}.example.com")
    phone = factory.Sequence(lambda n: f'555-0{100 + n:03d}')
    address_line1 = factory.Sequence(lambda n: f'{100 + n} Example Street')
    address_city = 'Silverton'
    address_state = 'OR'
    address_zip = '97381'
    is_active = True


class ResidentialClientFactory(ClientFactory):
    """Residential clients are named for the person, per MB's own convention."""
    name = factory.Iterator([f'{f} {l}' for f, l in PEOPLE])
    client_type = 'residential'
    email = factory.LazyAttribute(lambda o: f"{o.name.split()[0].lower()}@example.org")


class ContactFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Contact

    client = factory.SubFactory(ClientFactory)
    first_name = factory.Iterator([p[0] for p in PEOPLE])
    last_name = factory.Iterator([p[1] for p in PEOPLE])
    email = factory.LazyAttribute(
        lambda o: f'{o.first_name.lower()}.{o.last_name.lower()}@example.com')
    phone = factory.Sequence(lambda n: f'555-0{200 + n:03d}')
    is_primary = False
    receives_email = True


class ContactPhoneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ContactPhone

    contact = factory.SubFactory(ContactFactory)
    number = factory.Sequence(lambda n: f'555-0{300 + n:03d}')
    phone_type = 'cell'


class DeviceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Device

    client = factory.SubFactory(ClientFactory)
    name = factory.Sequence(lambda n: f'Front Desk PC {n + 1}')
    device_type = 'desktop'
    manufacturer = factory.Iterator(['Dell', 'Lenovo', 'HP', 'Apple'])
    model = factory.Sequence(lambda n: f'ExampleModel {1000 + n}')
    serial_number = factory.Sequence(lambda n: f'FAKESN{5000 + n}')
    os = 'Windows 11 Pro'
    cpu = 'Intel i5-12400'
    ram = '16 GB'
    storage = '512 GB NVMe'
    condition_at_intake = 'Light scuffing on the lid, no visible damage.'
    is_active = True


class CredentialedDeviceFactory(DeviceFactory):
    """Exercises the encrypted-credential path with a fake secret.

    Values here are encrypted at rest by FIELD_ENCRYPTION_KEY exactly like real
    ones, so keep them non-secrets — never a password pattern anyone reuses.
    """
    device_username = 'localadmin'
    device_password = 'not-a-real-password-demo-only'
    credential_notes = 'Demo credential. Safe to delete.'


class TicketFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Ticket

    client = factory.SubFactory(ClientFactory)
    subject = factory.Iterator([
        'Workstation will not boot past the logo screen',
        'Printer queue keeps stalling after the last update',
        'Laptop battery drains overnight while shut down',
        'Cannot reach the shared drive from the back office',
    ])
    description = ('Reported by phone. Started sometime after last week. '
                   'Demo ticket — not a real customer issue.')
    source = 'phone'
    status = 'open'


class WorkOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WorkOrder

    client = factory.SubFactory(ClientFactory)
    reported_problem = 'No power at the bench. Suspect PSU. Demo work order.'
    service_type = 'in_shop'
    status = 'in_progress'
    priority = 'normal'


class CatalogItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CatalogItem
        django_get_or_create = ('name',)

    name = factory.Iterator([
        'Bench Diagnostic', 'Operating System Reinstall', 'Data Transfer',
        'Onsite Hour', 'Managed Endpoint (monthly)',
    ])
    item_type = 'service'
    default_price = factory.Iterator(['60.00', '120.00', '95.00', '110.00', '35.00'])
    is_active = True


class ContractFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Contract

    client = factory.SubFactory(ClientFactory)
    title = 'Managed Services — Demo Agreement'
    status = 'active'
    billing_cadence = 'monthly'
    billing_day = 1
    start_date = factory.LazyFunction(lambda: timezone.now().date().replace(day=1))
    auto_renew = True


class SaleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Sale

    client = None          # a counter sale is legitimately client-less
    status = 'draft'
    notes = 'Demo counter sale.'


class LineItemFactory(factory.django.DjangoModelFactory):
    """Generic — pass `content_object` (WorkOrder / Sale / Contract / Estimate)."""
    class Meta:
        model = LineItem

    kind = 'labor'
    description = 'Bench diagnostic'
    quantity = 1
    unit_price = '60.00'
