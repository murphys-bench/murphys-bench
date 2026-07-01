from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.core.validators import MinValueValidator
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField
from django.conf import settings as django_settings
from django.core.files.storage import FileSystemStorage
import uuid
import os


def attachment_upload_path(instance, filename):
    ct = instance.content_type
    return f'attachments/{ct.app_label}/{ct.model}/{instance.object_id}/{filename}'


class PrivateMediaStorage(FileSystemStorage):
    """Filesystem storage rooted at PRIVATE_MEDIA_ROOT, which is deliberately
    OUTSIDE MEDIA_ROOT so nginx's /media/ alias can never serve these files — the
    authenticated, authorization-checked download view is the only way to them.
    Location is resolved dynamically (not cached at import) so tests can point it
    at a temp dir via the settings fixture."""

    @property
    def base_location(self):
        return django_settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)


def private_attachment_storage():
    """Return the private attachment storage. Passed as a callable so Django keeps
    the storage out of migrations."""
    return PrivateMediaStorage()


def _save_with_unique_number(instance, number_field, generator, save_super, attempts=6):
    """Insert `instance`, retrying number assignment if a concurrent insert
    grabbed the same number first.

    Without this, two near-simultaneous creates (e.g. back-to-back inbound
    emails) can both compute the same `max()+1` number and the second insert
    fails on the unique constraint. Each retry regenerates from a fresh read of
    the database, so it picks up the number the winner just committed.
    """
    from django.db import IntegrityError, transaction
    last_error = None
    for _ in range(attempts):
        if not getattr(instance, number_field):
            setattr(instance, number_field, generator())
        try:
            with transaction.atomic():
                return save_super()
        except IntegrityError as exc:
            last_error = exc
            setattr(instance, number_field, '')  # force regeneration next loop
    raise last_error


class Role(models.Model):
    """Permission role assigned to users."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(
        default=False,
        help_text='System roles cannot be deleted.',
    )

    # Permission flags
    can_manage_settings = models.BooleanField(default=False, help_text='Access admin panel and SiteSettings.')
    can_view_all_tickets = models.BooleanField(default=False, help_text='View all tickets (vs. own only).')
    can_close_tickets = models.BooleanField(default=False, help_text='Resolve and close tickets.')
    can_manage_users = models.BooleanField(default=False, help_text='Create and manage user accounts.')
    can_view_reports = models.BooleanField(default=False, help_text='Access reporting section (Batch 6).')
    can_view_restricted_kb = models.BooleanField(default=False, help_text='View admin-only KB articles.')
    can_manage_kb = models.BooleanField(default=False, help_text='Create and edit KB articles.')
    can_create_ticket = models.BooleanField(default=True)
    can_edit_ticket = models.BooleanField(default=True)
    can_delete_ticket = models.BooleanField(default=False)
    can_assign_ticket = models.BooleanField(default=True)
    can_reply_internal = models.BooleanField(default=True)
    can_reply_customer = models.BooleanField(default=True)
    can_view_device_credentials = models.BooleanField(default=False, help_text='Reveal encrypted device credentials (username/password).')
    can_reset_user_mfa = models.BooleanField(default=False, help_text='Reset (clear) another user\'s two-factor devices for lost-device recovery.')
    can_create_workorder = models.BooleanField(default=True)
    can_edit_workorder = models.BooleanField(default=True)
    can_close_workorder = models.BooleanField(default=True)
    can_view_prospects = models.BooleanField(default=True, help_text='View and manage sales prospects (leads).')
    can_view_estimates = models.BooleanField(default=True, help_text='View and manage sales estimates (quotes).')
    can_view_sales = models.BooleanField(default=True, help_text='View and manage counter sales.')

    class Meta:
        db_table = 'roles'
        ordering = ['name']

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        if self.is_system:
            raise ValueError(f'Cannot delete system role: {self.name}')
        super().delete(*args, **kwargs)


class TechSkill(models.Model):
    """Skills that can be assigned to technicians for future routing."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'tech_skills'
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    """Extended user model for technicians and admins"""

    ROLE_CHOICES = [
        ('admin', 'Administrator'),
        ('technician', 'Technician'),
        ('viewer', 'Viewer (Read-Only)'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='technician')
    role_obj = models.ForeignKey(
        'Role',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        help_text='Role-based permission set. Replaces the legacy role field.',
    )
    phone = models.CharField(max_length=20, blank=True)
    skills = models.ManyToManyField(TechSkill, blank=True, related_name='users')

    LEVEL_CHOICES = [(1, 'Level 1'), (2, 'Level 2'), (3, 'Level 3')]
    level = models.PositiveSmallIntegerField(
        default=1, choices=LEVEL_CHOICES,
        help_text='Escalation level. Tickets escalate upward; higher levels can take over escalated tickets.',
    )

    class Meta:
        db_table = 'users'
        ordering = ['first_name', 'last_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}" or self.username

    def has_perm_flag(self, flag):
        """Check a permission flag on the user's role_obj."""
        if self.role_obj:
            return getattr(self.role_obj, flag, False)
        # Fallback: admin role string = all perms
        if self.role == 'admin':
            return True
        return False


class Client(models.Model):
    """Companies/customers requesting service"""

    CLIENT_TYPE_CHOICES = [
        ('residential', 'Residential'),
        ('business', 'Business'),
    ]

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)
    client_type = models.CharField(max_length=20, choices=CLIENT_TYPE_CHOICES, default='residential')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    address_city = models.CharField(max_length=100, blank=True)
    address_state = models.CharField(max_length=2, blank=True)
    address_zip = models.CharField(max_length=10, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    suppress_emails = models.BooleanField(
        default=False,
        help_text='Suppress all automated outbound emails to this client.',
    )
    is_unsorted = models.BooleanField(
        default=False,
        help_text='System "Unsorted / Unverified" bucket: holds inbound tickets '
                  'from senders not yet matched to a real client, pending triage. '
                  'There should only ever be one.',
    )
    # Invoice Ninja client id, saved after the first push (link once, don't sync).
    # Later pushes use this directly — no re-search, no duplicate IN clients.
    invoice_ninja_id = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'clients'
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_active']),
        ]

    UNSORTED_NAME = 'Unsorted / Unverified'

    def __str__(self):
        return self.name

    @classmethod
    def get_unsorted(cls):
        """The single system bucket for unmatched inbound senders awaiting triage."""
        client = cls.objects.filter(is_unsorted=True).first()
        if client:
            return client
        return cls.objects.create(
            name=cls.UNSORTED_NAME, is_unsorted=True, is_active=True,
        )


class Contact(models.Model):
    """Individual people at client companies"""

    id = models.AutoField(primary_key=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='contacts')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=100, blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    receives_email = models.BooleanField(
        default=True,
        help_text='Uncheck to suppress automated emails to this contact.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contacts'
        ordering = ['client', 'last_name', 'first_name']
        indexes = [
            models.Index(fields=['client', 'is_primary']),
            models.Index(fields=['email']),
            models.Index(fields=['phone']),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.client.name})"


class ContactPhone(models.Model):
    """Additional phone numbers for a contact (beyond the primary phone field)."""

    PHONE_TYPE_CHOICES = [
        ('cell', 'Cell'),
        ('home', 'Home'),
        ('work', 'Work'),
        ('other', 'Other'),
    ]

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='phone_numbers')
    number = models.CharField(max_length=30)
    phone_type = models.CharField(max_length=10, choices=PHONE_TYPE_CHOICES, default='cell')
    label = models.CharField(max_length=50, blank=True, help_text="Optional custom label, e.g. 'Mom's cell'")

    class Meta:
        db_table = 'contact_phones'
        ordering = ['phone_type', 'number']

    def __str__(self):
        return f"{self.number} ({self.get_phone_type_display()})"


class Prospect(models.Model):
    """A prospective customer (sales lead), captured contact-first before they
    become a paying Client. Promoted to a Client (+ a primary Contact) when the
    work is accepted. Thin by design — the financial layer's customer spine."""

    STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('quoted', 'Quoted'),
        ('won', 'Won'),
        ('lost', 'Lost'),
    ]

    # The contact comes first — a lead is always a person we're talking to,
    # who may or may not sit within a company.
    contact_first_name = models.CharField(max_length=100)
    contact_last_name = models.CharField(max_length=100, blank=True)
    company = models.CharField(
        max_length=255, blank=True,
        help_text='The company this contact belongs to, if any (required for business).',
    )
    # Known up front, before any quote — drives the company-vs-individual shape.
    client_type = models.CharField(
        max_length=20, choices=Client.CLIENT_TYPE_CHOICES, default='residential',
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')

    # Set when promoted; links the lead to the Client it became.
    promoted_to = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='promoted_from_prospects',
    )
    promoted_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='prospects_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'prospects'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['email']),
        ]

    def __str__(self):
        return self.display_name

    @property
    def contact_name(self):
        return f"{self.contact_first_name} {self.contact_last_name}".strip()

    @property
    def display_name(self):
        """Company — Contact for business; the contact's name for residential."""
        if self.company:
            name = self.contact_name
            return f"{self.company} — {name}" if name else self.company
        return self.contact_name

    @property
    def is_promoted(self):
        return self.promoted_to_id is not None

    def promote_to_client(self):
        """Create a Client (+ a primary Contact) from this lead and link it back.

        Business → Client named for the company; residential → Client named for
        the person. A Contact is always created (contact-first). Idempotent: a
        prospect that's already been promoted returns its existing Client."""
        from django.db import transaction
        from django.utils import timezone

        if self.promoted_to_id:
            return self.promoted_to

        with transaction.atomic():
            if self.client_type == 'business' and self.company:
                client_name = self.company
            else:
                client_name = self.contact_name or self.company or 'Unnamed'

            client = Client.objects.create(
                name=client_name,
                client_type=self.client_type,
                email=self.email,
                phone=self.phone,
                notes=self.notes,
            )
            Contact.objects.create(
                client=client,
                first_name=self.contact_first_name,
                last_name=self.contact_last_name or '',
                email=self.email,
                phone=self.phone,
                is_primary=True,
            )
            self.promoted_to = client
            self.promoted_at = timezone.now()
            self.status = 'won'
            self.save(update_fields=['promoted_to', 'promoted_at', 'status', 'updated_at'])
        return client


class RepairTypeCategory(models.Model):
    """Grouping for repair types, e.g. Hardware, Software, Networking."""

    name = models.CharField(max_length=100, unique=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'repair_type_categories'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class RepairType(models.Model):
    """Categories of repairs (e.g., Laptop Repair, Desktop Repair)"""

    id = models.AutoField(primary_key=True)
    category = models.ForeignKey(
        RepairTypeCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='repair_types'
    )
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'repair_types'
        ordering = ['category__sort_order', 'sort_order', 'name']
        indexes = [
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.name


class Device(models.Model):
    """Equipment being serviced"""

    DEVICE_TYPE_CHOICES = [
        ('laptop', 'Laptop'),
        ('desktop', 'Desktop'),
        ('server', 'Server'),
        ('mobile', 'Mobile Phone'),
        ('tablet', 'Tablet'),
        ('printer', 'Printer'),
        ('other', 'Other'),
    ]

    OS_CHOICES = [
        ('windows', 'Windows'),
        ('macos', 'macOS'),
        ('linux', 'Linux'),
        ('ios', 'iOS'),
        ('android', 'Android'),
        ('chromeos', 'ChromeOS'),
        ('other', 'Other'),
    ]

    CONDITION_CHOICES = [
        ('used', 'Used'),
        ('new', 'New'),
        ('damaged', 'Damaged'),
    ]

    id = models.AutoField(primary_key=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='devices')
    repair_type = models.ForeignKey(RepairType, on_delete=models.SET_NULL, null=True, related_name='devices')
    assigned_contact = models.ForeignKey('Contact', on_delete=models.SET_NULL, null=True, blank=True, related_name='devices')
    name = models.CharField(max_length=255, help_text="e.g., 'Mike\'s Laptop'")
    device_type = models.CharField(max_length=50, choices=DEVICE_TYPE_CHOICES, default='laptop')
    serial_number = models.CharField(max_length=100, blank=True, null=True, unique=True)
    model = models.CharField(max_length=100, blank=True)
    manufacturer = models.CharField(max_length=100, blank=True)
    os = models.CharField(max_length=20, choices=OS_CHOICES, blank=True)
    os_version = models.CharField(max_length=100, blank=True)
    # Hardware specs — free text; values vary too widely to constrain
    cpu = models.CharField(max_length=150, blank=True, help_text="Processor, e.g. 'Intel Core i7-1185G7' or 'Apple M2'")
    ram = models.CharField(max_length=100, blank=True, help_text="Physical memory, e.g. '16 GB'")
    storage = models.CharField(max_length=150, blank=True, help_text="Disk, e.g. '512 GB SSD' or '1 TB NVMe + 2 TB HDD'")
    condition_at_intake = models.CharField(max_length=20, choices=CONDITION_CHOICES, blank=True)
    notes = models.TextField(blank=True)
    # Encrypted device credentials — stored AES-256, revealed via HTMX eye icon
    device_username = EncryptedCharField(max_length=255, blank=True)
    device_password = EncryptedCharField(max_length=255, blank=True)
    credential_notes = EncryptedTextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'devices'
        ordering = ['client', 'name']
        indexes = [
            models.Index(fields=['client', 'is_active']),
            models.Index(fields=['device_type']),
            models.Index(fields=['serial_number']),
        ]

    def __str__(self):
        return f"{self.name} ({self.client.name})"

    def save(self, *args, **kwargs):
        # Store blank serials as NULL so the unique constraint permits many
        # serial-less devices (NULLs are distinct; empty strings are not).
        if not self.serial_number:
            self.serial_number = None
        super().save(*args, **kwargs)


class SLAPlan(models.Model):
    """Service Level Agreement — defines response/resolution deadline."""

    name = models.CharField(max_length=100, unique=True)
    grace_period_hours = models.PositiveIntegerField(
        default=24,
        help_text='Hours from ticket creation until overdue.',
    )
    is_active = models.BooleanField(default=True)
    is_transient = models.BooleanField(
        default=False,
        help_text='Transient SLAs apply to short-lived tickets (e.g. same-day callbacks).',
    )
    disable_overdue_alerts = models.BooleanField(
        default=False,
        help_text='Suppress overdue badges for tickets on this plan.',
    )

    class Meta:
        db_table = 'sla_plans'
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.grace_period_hours}h)'


class HelpTopic(models.Model):
    """Ticket classification topic with optional default SLA."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    default_sla = models.ForeignKey(
        SLAPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='help_topics',
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'help_topics'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class Ticket(models.Model):
    """Initial service request (starts workflow)"""

    SOURCE_CHOICES = [
        ('email', 'Email'),
        ('phone', 'Phone'),
        ('web', 'Web Form'),
        ('rmm', 'RMM System'),
        ('system', 'System Alert'),
    ]

    STATUS_CHOICES = [
        ('new', 'New'),
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('waiting_on_customer', 'Waiting on Customer'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
        ('converted', 'Converted to Work Order'),
    ]

    id = models.AutoField(primary_key=True)
    ticket_number = models.CharField(max_length=20, unique=True, db_index=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='tickets')
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets')
    help_topic = models.ForeignKey(
        'HelpTopic',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets',
    )
    sla_plan = models.ForeignKey(
        'SLAPlan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets',
    )
    due_at = models.DateTimeField(null=True, blank=True, help_text='Calculated from SLA grace period on assignment.')
    overdue_acknowledged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='overdue_acknowledgments',
    )
    overdue_acknowledged_at = models.DateTimeField(null=True, blank=True)
    first_responded_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text='Set on the first staff customer-visible reply. Once set, the response '
                  'SLA is met and the ticket can no longer go overdue.',
    )
    subject = models.CharField(max_length=255)
    description = models.TextField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='email')
    status = models.CharField(max_length=50, default='open', db_index=True)
    contact = models.ForeignKey(
        'Contact',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets',
    )
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets_assigned',
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='tickets_created')
    needs_response = models.BooleanField(default=False, db_index=True)
    wo_complete = models.BooleanField(default=False, db_index=True)
    escalation_level = models.PositiveSmallIntegerField(
        default=1, db_index=True,
        help_text='Level required to take this ticket. Raised by Escalate; the current '
                  'owner keeps it until a higher-level tech claims it.',
    )
    assignment_unseen = models.BooleanField(
        default=False, db_index=True,
        help_text='True when transferred/assigned to a user by someone else, until that '
                  'user first opens the ticket. Drives the "new to you" indicator.',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tickets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client', 'status']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['source']),
        ]

    def __str__(self):
        return f"{self.ticket_number}: {self.subject}"

    def save(self, *args, **kwargs):
        if self._state.adding:
            return _save_with_unique_number(
                self, 'ticket_number', self.generate_ticket_number,
                lambda: super(Ticket, self).save(*args, **kwargs),
            )
        return super().save(*args, **kwargs)

    @classmethod
    def generate_ticket_number(cls):
        """Generate sequential ticket number like TKT-00001"""
        import re
        existing = cls.objects.filter(
            ticket_number__regex=r'^TKT-\d{5}$'
        ).values_list('ticket_number', flat=True)
        nums = [int(n[4:]) for n in existing if re.match(r'^TKT-\d{5}$', n)]
        next_num = (max(nums) + 1) if nums else 1
        return f"TKT-{next_num:05d}"

    attachments = GenericRelation('Attachment')

    CLOSED_STATUSES = {'resolved', 'closed', 'converted'}

    @property
    def is_overdue(self):
        if not self.due_at:
            return False
        if self.first_responded_at:
            # The only SLA is a response deadline; the first staff reply meets it
            # permanently. The clock never re-arms after that.
            return False
        if self.status in self.CLOSED_STATUSES:
            return False
        if self.sla_plan and self.sla_plan.disable_overdue_alerts:
            return False
        return timezone.now() > self.due_at

    @classmethod
    def overdue_queryset(cls, qs=None):
        """Tickets that are currently overdue — the DB-level mirror of ``is_overdue``.

        Single source of truth for every count/filter (dashboard tile, ticket list
        ?overdue, queue criteria, SLA command) so they can't drift from the property.
        A ticket is overdue when it has a deadline that has passed, no staff reply has
        gone out yet, it isn't closed/resolved/converted, and its SLA isn't muted.
        """
        if qs is None:
            qs = cls.objects.all()
        return qs.filter(
            due_at__isnull=False,
            due_at__lt=timezone.now(),
            first_responded_at__isnull=True,
        ).exclude(
            status__in=cls.CLOSED_STATUSES,
        ).exclude(
            sla_plan__disable_overdue_alerts=True,
        )

    @property
    def overdue_is_acknowledged(self):
        """True if the current overdue period has been acknowledged."""
        return self.is_overdue and self.overdue_acknowledged_at is not None

    def assign_sla(self, sla_plan):
        """Assign an SLA plan and calculate due_at. Clears any prior acknowledgment."""
        self.sla_plan = sla_plan
        if sla_plan:
            self.due_at = self.created_at + timezone.timedelta(hours=sla_plan.grace_period_hours)
        else:
            self.due_at = None
        self.overdue_acknowledged_by = None
        self.overdue_acknowledged_at = None

    def get_linked_tickets(self):
        """Return all tickets linked to this one, regardless of link direction"""
        from django.db.models import Q
        links_a = self.links_as_a.select_related('ticket_b', 'created_by').all()
        links_b = self.links_as_b.select_related('ticket_a', 'created_by').all()
        results = []
        for link in links_a:
            results.append({'ticket': link.ticket_b, 'link_type': link.link_type, 'link_id': link.pk})
        for link in links_b:
            results.append({'ticket': link.ticket_a, 'link_type': link.link_type, 'link_id': link.pk})
        return results

    MAX_LEVEL = 3

    def _holder_level(self):
        """The level the ticket effectively sits at right now — its owner's level,
        or its current escalation level if unassigned."""
        if self.assigned_to_id:
            return self.assigned_to.level or 1
        return self.escalation_level

    @property
    def can_escalate(self):
        """False once the ticket is already at (or above) the top level for whoever
        currently holds it — there's nowhere higher to send it."""
        return max(self.escalation_level, self._holder_level()) < self.MAX_LEVEL

    def escalate(self):
        """Raise the ticket to one level above whoever currently holds it (capped).
        The current owner keeps it until a higher-level tech claims it, so the client
        is never left without a person. Returns False if already at the top."""
        target = min(self.MAX_LEVEL, max(self.escalation_level, self._holder_level()) + 1)
        if target > self.escalation_level:
            self.escalation_level = target
            self.save(update_fields=['escalation_level', 'updated_at'])
            return True
        return False

    @property
    def escalation_pending(self):
        """True when the ticket has been escalated above its current owner's level —
        i.e. it's owned, but waiting for someone higher to take over."""
        if self.assigned_to_id is None:
            return False
        return self.escalation_level > (self.assigned_to.level or 1)


class TicketReply(models.Model):
    """Threaded conversation on a ticket (replies, updates, status changes)"""

    REPLY_TYPE_CHOICES = [
        ('customer_visible', 'Customer Visible'),
        ('internal', 'Internal Only'),
    ]

    id = models.AutoField(primary_key=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='replies')
    reply_type = models.CharField(max_length=20, choices=REPLY_TYPE_CHOICES, default='customer_visible', db_index=True)
    content = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='ticket_replies')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ticket_replies'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['ticket', 'reply_type']),
        ]

    attachments = GenericRelation('Attachment')

    def __str__(self):
        return f"Reply on {self.ticket} by {self.created_by}"


class WorkOrder(models.Model):
    """Repair job (main entity)"""

    STATUS_CHOICES = [
        ('new', 'New'),
        ('assigned', 'Assigned'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    SERVICE_TYPE_CHOICES = [
        ('in_shop', 'In-Shop'),
        ('onsite', 'Onsite'),
        ('remote', 'Remote'),
    ]

    id = models.AutoField(primary_key=True)
    work_order_number = models.CharField(max_length=20, unique=True, db_index=True)
    ticket = models.OneToOneField(Ticket, on_delete=models.SET_NULL, null=True, blank=True, related_name='work_order_created')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='work_orders')
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name='work_orders')
    contact = models.ForeignKey('Contact', on_delete=models.SET_NULL, null=True, blank=True, related_name='work_orders')
    repair_type = models.ForeignKey(RepairType, on_delete=models.SET_NULL, null=True, related_name='work_orders')
    # Free-text presenting problem + any ad-hoc work the client requested. Not every
    # job fits a predefined repair_type; the bench can edit/add to this freeform. Carried
    # from the ticket's description on conversion. Shown on the printed/emailed repair report.
    reported_problem = models.TextField(blank=True, help_text="Client's reported issue and any work requested; shown on the repair report")
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='work_orders_assigned')
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPE_CHOICES, default='in_shop')
    status = models.CharField(max_length=50, default='new', db_index=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='normal')
    time_spent_minutes = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    scheduled_date = models.DateField(null=True, blank=True)
    completed_date = models.DateTimeField(null=True, blank=True)
    # Hardware specs — snapshotted from the device at WO creation so the WO/report
    # reflects the machine as serviced; edits here sync back to the device master.
    cpu = models.CharField(max_length=150, blank=True, help_text="Processor as serviced")
    ram = models.CharField(max_length=100, blank=True, help_text="Physical memory as serviced")
    storage = models.CharField(max_length=150, blank=True, help_text="Disk as serviced")
    notes_internal = models.TextField(blank=True, help_text="Technician-only notes")
    notes_customer_visible = models.TextField(blank=True, help_text="What the customer sees")
    invoice_ninja_ref = models.CharField(max_length=100, blank=True, help_text='Invoice Ninja invoice number (editable — record where the work actually landed if a draft was merged in IN)')
    # IN invoice id returned by the push — the duplicate-guard key. Set = pushed.
    invoice_ninja_id = models.CharField(max_length=64, blank=True, default='')
    # Device credentials — encrypted at rest (AES-256), never shown on printed reports
    device_username = EncryptedCharField(max_length=255, blank=True)
    device_password = EncryptedCharField(max_length=255, blank=True)
    device_pin = EncryptedCharField(max_length=50, blank=True)
    credential_notes = EncryptedTextField(blank=True, help_text='Freeform credential notes, e.g. recovery email, security question answers')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'work_orders'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client', 'status']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['status']),
        ]

    attachments = GenericRelation('Attachment')
    line_items = GenericRelation('LineItem')

    def __str__(self):
        return f"{self.work_order_number}: {self.client.name}"

    @property
    def line_items_total(self):
        """Sum of priced line items on this work order. Unpriced lines (no
        unit_price set) are ignored. MB captures/totals prices only — Invoice
        Ninja remains the billing authority."""
        from decimal import Decimal
        total = Decimal('0')
        for li in self.line_items.all():
            lt = li.line_total
            if lt is not None:
                total += lt
        return total

    SPEC_FIELDS = ('cpu', 'ram', 'storage')

    def apply_device_specs(self, force=False):
        """Copy hardware specs from the linked device onto this WO.
        Fills blank targets only, unless force=True (used when the device is (re)assigned)."""
        if not self.device_id:
            return
        device = self.device
        for f in self.SPEC_FIELDS:
            if force or not getattr(self, f):
                setattr(self, f, getattr(device, f, '') or '')

    def sync_specs_to_device(self):
        """Write this WO's hardware specs back to the device master so it stays current.
        Only non-blank values overwrite; returns the list of fields changed on the device."""
        if not self.device_id:
            return []
        device = self.device
        changed = []
        for f in self.SPEC_FIELDS:
            val = getattr(self, f)
            if val and getattr(device, f) != val:
                setattr(device, f, val)
                changed.append(f)
        if changed:
            device.save(update_fields=changed + ['updated_at'])
        return changed

    def save(self, *args, **kwargs):
        if self._state.adding:
            # Snapshot the device's hardware specs onto the WO at creation
            self.apply_device_specs()
            return _save_with_unique_number(
                self, 'work_order_number', self.generate_work_order_number,
                lambda: super(WorkOrder, self).save(*args, **kwargs),
            )
        return super().save(*args, **kwargs)

    @classmethod
    def generate_work_order_number(cls, from_ticket_number=None):
        """Generate sequential WO number like WO-00001.
        If from_ticket_number is given (e.g. TKT-00042), reuse that sequence: WO-00042."""
        if from_ticket_number:
            seq = from_ticket_number.split('-', 1)[-1]
            candidate = f"WO-{seq}"
            if not cls.objects.filter(work_order_number=candidate).exists():
                return candidate
        # Find the highest existing sequential number and increment
        import re
        existing = cls.objects.filter(
            work_order_number__regex=r'^WO-\d{5}$'
        ).values_list('work_order_number', flat=True)
        nums = [int(n[3:]) for n in existing if re.match(r'^WO-\d{5}$', n)]
        next_num = (max(nums) + 1) if nums else 1
        return f"WO-{next_num:05d}"

    @property
    def time_spent_display(self):
        m = self.time_spent_minutes or 0
        if m == 0:
            return '—'
        h, rem = divmod(m, 60)
        if h and rem:
            return f'{h}h {rem}m'
        elif h:
            return f'{h}h'
        return f'{rem}m'

    def mark_completed(self):
        """Mark work order as completed with timestamp"""
        self.status = 'completed'
        self.completed_date = timezone.now()
        self.save()


class WorkOrderNote(models.Model):
    """Comments/updates on a work order"""

    NOTE_TYPE_CHOICES = [
        ('customer_visible', 'Customer Visible'),
        ('internal', 'Internal Only'),
    ]

    id = models.AutoField(primary_key=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='notes')
    note_type = models.CharField(max_length=20, choices=NOTE_TYPE_CHOICES, db_index=True)
    content = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='work_order_notes')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'work_order_notes'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['work_order', 'note_type']),
        ]

    attachments = GenericRelation('Attachment')

    def __str__(self):
        return f"Note on {self.work_order} ({self.note_type})"


class WorkOrderItem(models.Model):
    """Line items on a work order (checklist, parts, time entries)"""

    ITEM_TYPE_CHOICES = [
        ('checklist', 'Checklist Item'),
        ('part', 'Part/Material'),
        ('time', 'Time Entry'),
        ('other', 'Other'),
    ]

    id = models.AutoField(primary_key=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='items')
    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES, db_index=True)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=20, blank=True, help_text="e.g., 'hours', 'each', 'qty'")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    is_completed = models.BooleanField(default=False, db_index=True)
    CHECK_CHOICES = [('', '—'), ('pass', 'Pass'), ('fail', 'Fail'), ('na', 'N/A')]
    pre_check = models.CharField(max_length=10, choices=CHECK_CHOICES, blank=True, default='')
    post_check = models.CharField(max_length=10, choices=CHECK_CHOICES, blank=True, default='')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'work_order_items'
        ordering = ['work_order', 'created_at']
        indexes = [
            models.Index(fields=['work_order', 'item_type']),
            models.Index(fields=['is_completed']),
        ]

    def __str__(self):
        return f"{self.item_type}: {self.description}"


class Invoice(models.Model):
    """Billing state tracker for a WorkOrder. Tracks status only — not an accounting module."""

    BILLING_STATUS_CHOICES = [
        ('uninvoiced', 'Uninvoiced'),
        ('invoiced', 'Invoiced'),
        ('paid', 'Paid'),
        ('paid_direct', 'Paid Direct'),
        ('disputed', 'Disputed'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('check', 'Check'),
        ('card', 'Card'),
        ('transfer', 'Transfer'),
        ('other', 'Other'),
    ]

    work_order = models.OneToOneField(WorkOrder, on_delete=models.CASCADE, related_name='invoice')
    billing_status = models.CharField(max_length=20, choices=BILLING_STATUS_CHOICES, default='uninvoiced', db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    invoiced_date = models.DateField(null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True)
    notes = models.TextField(blank=True)
    # Invoice Ninja read-back — recorded, not authoritative
    invoice_ninja_id = models.CharField(max_length=100, blank=True)
    in_status = models.CharField(max_length=50, blank=True)
    in_status_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoices'

    def __str__(self):
        return f'Invoice for {self.work_order.work_order_number} — {self.get_billing_status_display()}'


class Estimate(models.Model):
    """A priced quote built for a Prospect or Client, ahead of any Work Order.

    MB owns the quote end-to-end (including the customer document) — Invoice
    Ninja is never touched at quote time, so dead/declined quotes never
    clutter IN. Anchors to exactly one of client/prospect (opportunity-shaped,
    not problem-shaped); a ticket is one optional origin, not the anchor."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('expired', 'Expired'),
    ]

    # Anchor — exactly one of these is set (enforced in clean()).
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True, related_name='estimates')
    prospect = models.ForeignKey(Prospect, on_delete=models.SET_NULL, null=True, blank=True, related_name='estimates')

    # Optional context — none of these are required.
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True, related_name='estimates')
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name='estimates')
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name='estimates')

    estimate_number = models.CharField(max_length=20, unique=True)
    scope = models.TextField(blank=True, help_text='What we\'re quoting — free text.')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    expires_on = models.DateField(null=True, blank=True)

    # Decline path (populated in Slice 2c).
    decline_reason = models.TextField(blank=True)

    # Revision chain — a sent/declined estimate freezes; revising creates a new
    # linked estimate (preserves an audit trail of what was offered/declined).
    revision_of = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='revisions',
    )

    # Accept path (populated in Slice 2c).
    accepted_at = models.DateTimeField(null=True, blank=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name='source_estimate')

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='estimates_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    line_items = GenericRelation('LineItem')

    class Meta:
        db_table = 'estimates'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.estimate_number} — {self.display_name}'

    def clean(self):
        from django.core.exceptions import ValidationError
        if bool(self.client_id) == bool(self.prospect_id):
            raise ValidationError('An estimate must be anchored to exactly one of Client or Prospect.')

    @property
    def display_name(self):
        if self.client_id:
            return self.client.name
        if self.prospect_id:
            return self.prospect.display_name
        return 'Unanchored'

    @property
    def is_locked(self):
        """Read-only once accepted (the WO it spawned is the live record now) or
        once superseded by a revision (the new linked estimate is the live one)."""
        return self.status == 'accepted' or self.revisions.exists()

    @property
    def line_items_total(self):
        """Sum of priced line items. Unpriced lines are ignored — mirrors
        WorkOrder.line_items_total so the same vocabulary applies pre-WO."""
        from decimal import Decimal
        total = Decimal('0')
        for li in self.line_items.all():
            lt = li.line_total
            if lt is not None:
                total += lt
        return total

    def save(self, *args, **kwargs):
        if self._state.adding:
            return _save_with_unique_number(
                self, 'estimate_number', self.generate_estimate_number,
                lambda: super(Estimate, self).save(*args, **kwargs),
            )
        return super().save(*args, **kwargs)

    @classmethod
    def generate_estimate_number(cls):
        """Generate sequential estimate number like EST-00001."""
        import re
        existing = cls.objects.filter(
            estimate_number__regex=r'^EST-\d{5}$'
        ).values_list('estimate_number', flat=True)
        nums = [int(n[4:]) for n in existing if re.match(r'^EST-\d{5}$', n)]
        next_num = (max(nums) + 1) if nums else 1
        return f"EST-{next_num:05d}"


class Sale(models.Model):
    """A counter/walk-in sale — Lane B (Counter). Client is optional (nullable):
    a cash walk-in with no client stays MB-only and is never pushed to Invoice
    Ninja (IN needs a client). Reuses the LineItem GenericRelation exactly like
    WorkOrder/Estimate — same edit/delete UI, same vocabulary.

    Payment/checkout fields are defined now but wired up in Slice 3b (checkout +
    Send-to-IN); this slice (3a) only builds the record + line items."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('completed', 'Completed'),
        ('void', 'Void'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('check', 'Check'),
        ('card', 'Card'),
        ('other', 'Other'),
    ]

    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales')
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales')

    sale_number = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    notes = models.TextField(blank=True)

    # Checkout / payment record (Slice 3b).
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True, help_text='Check number or card confirmation reference.')

    # Invoice Ninja push (Slice 3b) — mirrors Invoice/Estimate's read-back trio.
    invoice_ninja_id = models.CharField(max_length=100, blank=True)
    in_status = models.CharField(max_length=50, blank=True)
    in_status_checked_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    line_items = GenericRelation('LineItem')

    class Meta:
        db_table = 'sales'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.sale_number} — {self.display_name}'

    @property
    def display_name(self):
        if self.client_id:
            return self.client.name
        return 'Walk-in'

    @property
    def is_locked(self):
        """Read-only once completed or voided."""
        return self.status in ('completed', 'void')

    @property
    def line_items_total(self):
        """Sum of priced line items. Unpriced lines are ignored — same vocabulary
        as WorkOrder.line_items_total / Estimate.line_items_total."""
        from decimal import Decimal
        total = Decimal('0')
        for li in self.line_items.all():
            lt = li.line_total
            if lt is not None:
                total += lt
        return total

    def save(self, *args, **kwargs):
        if self._state.adding:
            return _save_with_unique_number(
                self, 'sale_number', self.generate_sale_number,
                lambda: super(Sale, self).save(*args, **kwargs),
            )
        return super().save(*args, **kwargs)

    @classmethod
    def generate_sale_number(cls):
        """Generate sequential sale number like SALE-00001."""
        import re
        existing = cls.objects.filter(
            sale_number__regex=r'^SALE-\d{5}$'
        ).values_list('sale_number', flat=True)
        nums = [int(n[5:]) for n in existing if re.match(r'^SALE-\d{5}$', n)]
        next_num = (max(nums) + 1) if nums else 1
        return f"SALE-{next_num:05d}"


class OrgCredential(models.Model):
    """Shared organizational credential vault entry. Encrypted at rest."""

    CATEGORY_CHOICES = [
        ('email',   'Email'),
        ('remote',  'Remote Support'),
        ('cloud',   'Cloud'),
        ('network', 'Network'),
        ('vendor',  'Vendor'),
        ('other',   'Other'),
    ]

    name         = models.CharField(max_length=200)
    username     = EncryptedCharField(max_length=255, blank=True)
    password     = EncryptedCharField(max_length=255, blank=True)
    url          = models.URLField(blank=True)
    category     = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other', db_index=True)
    notes        = EncryptedTextField(blank=True)
    admin_only   = models.BooleanField(default=False, help_text='Restrict to administrators only')
    created_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='org_credentials_created')
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return f'{self.name} ({self.get_category_display()})'


class CredentialAccessLog(models.Model):
    """Audit log — every reveal, copy, edit, or delete of an OrgCredential."""

    ACTION_CHOICES = [
        ('viewed',  'Viewed'),
        ('edited',  'Edited'),
        ('deleted', 'Deleted'),
    ]

    credential  = models.ForeignKey(OrgCredential, on_delete=models.CASCADE, related_name='access_logs')
    user        = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action      = models.CharField(max_length=20, choices=ACTION_CHOICES)
    accessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-accessed_at']

    def __str__(self):
        return f'{self.user} {self.action} "{self.credential.name}" at {self.accessed_at}'


class DeviceCredentialAccessLog(models.Model):
    """Audit log — every reveal or edit of a Device's encrypted credentials."""

    ACTION_CHOICES = [
        ('viewed', 'Viewed'),
        ('edited', 'Edited'),
    ]

    device      = models.ForeignKey('Device', on_delete=models.CASCADE, related_name='credential_logs')
    user        = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action      = models.CharField(max_length=20, choices=ACTION_CHOICES)
    field       = models.CharField(max_length=50, blank=True, help_text='Which field was revealed (username/password)')
    accessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-accessed_at']

    def __str__(self):
        return f'{self.user} {self.action} credentials on "{self.device.name}" at {self.accessed_at}'


class MFAResetLog(models.Model):
    """Audit record — every reset (clearing) of a user's two-factor devices.

    Resets are lost-device recovery and a sensitive action, so each one is
    recorded: who was reset, who did it (null = CLI break-glass), and how.
    """

    SOURCE_CHOICES = [
        ('web', 'Web (admin)'),
        ('cli', 'CLI (break-glass)'),
    ]

    target     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mfa_resets')
    actor      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='mfa_resets_performed',
                                   help_text='User who performed the reset. Null = CLI break-glass.')
    source     = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='web')
    note       = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        who = self.actor or 'CLI break-glass'
        return f'MFA reset for {self.target} by {who} ({self.source}) at {self.created_at}'


def reset_user_mfa(target, actor=None, source='web', note=''):
    """Clear all OTP devices for ``target`` and record an MFAResetLog.

    Shared by the admin web view and the reset_mfa break-glass command so both
    paths leave an identical audit trail. Returns the created MFAResetLog.
    """
    from django_otp import devices_for_user
    count = 0
    for device in list(devices_for_user(target)):
        device.delete()
        count += 1
    if note:
        note = note[:255]
    log = MFAResetLog.objects.create(
        target=target, actor=actor, source=source, note=note,
    )
    return log, count


class StatusDefinition(models.Model):
    """Configurable status labels and colors for tickets and work orders."""

    ENTITY_CHOICES = [
        ('ticket',     'Ticket'),
        ('workorder',  'Work Order'),
    ]

    entity_type = models.CharField(max_length=20, choices=ENTITY_CHOICES, db_index=True)
    slug        = models.CharField(max_length=50)
    label       = models.CharField(max_length=100)
    color       = models.CharField(max_length=7, default='#E5E7EB', help_text='Background color hex (e.g. #DBEAFE)')
    is_system   = models.BooleanField(default=False, help_text='System statuses cannot be deleted.')
    is_active   = models.BooleanField(default=True)
    sort_order  = models.IntegerField(default=0)

    class Meta:
        ordering = ['entity_type', 'sort_order', 'label']
        unique_together = [('entity_type', 'slug')]

    def __str__(self):
        return f'{self.get_entity_type_display()} — {self.label}'

    def text_color(self):
        """Return a contrasting text color (dark or light) for this badge's background."""
        try:
            h = self.color.lstrip('#')
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            return '#1F2937' if luminance > 0.5 else '#F9FAFB'
        except Exception:
            return '#1F2937'


class Mileage(models.Model):
    """Travel logging for billing/expense tracking"""

    id = models.AutoField(primary_key=True)
    technician = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mileage_entries')
    trip_date = models.DateField(db_index=True)
    from_location = models.CharField(max_length=255, blank=True)
    to_location = models.CharField(max_length=255, blank=True)
    TRIP_TYPE_CHOICES = [('one_way', 'One-Way'), ('round_trip', 'Round Trip')]
    miles = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    trip_type = models.CharField(max_length=12, choices=TRIP_TYPE_CHOICES, default='round_trip')
    purpose = models.CharField(max_length=255, blank=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name='mileage')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mileage'
        ordering = ['-trip_date']
        indexes = [
            models.Index(fields=['technician', 'trip_date']),
            models.Index(fields=['work_order']),
        ]

    def __str__(self):
        return f"{self.technician.first_name} - {self.trip_date}: {self.miles} miles"


class Checklist(models.Model):
    """Templates of standard tasks for repair types"""

    id = models.AutoField(primary_key=True)
    repair_type = models.ForeignKey(RepairType, on_delete=models.CASCADE, related_name='checklists', null=True, blank=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Use as default for repair type")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'checklists'
        ordering = ['repair_type', 'name']
        indexes = [
            models.Index(fields=['repair_type', 'is_active']),
        ]

    def __str__(self):
        return self.name


class ChecklistItem(models.Model):
    """Flat bank of checklist tasks, each scoped to one or more device types."""

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    # JSON list of device_type keys from Device.DEVICE_TYPE_CHOICES, e.g. ["laptop","desktop"]
    # Empty list means "applies to all device types"
    device_types = models.JSONField(default=list, blank=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'checklist_items'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name

    def applies_to(self, device_type):
        """Returns True if this item should appear for the given device type."""
        return not self.device_types or device_type in self.device_types


class TicketLock(models.Model):
    """Tracks who is currently viewing/editing a ticket to prevent collision"""

    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name='lock')
    locked_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ticket_locks')
    locked_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ticket_locks'

    def __str__(self):
        return f"{self.ticket.ticket_number} locked by {self.locked_by}"

    def is_expired(self):
        from django.conf import settings
        timeout = getattr(settings, 'TICKET_LOCK_TIMEOUT_MINUTES', 10)
        return (timezone.now() - self.locked_at).total_seconds() > timeout * 60


class TicketLink(models.Model):
    """Links two related or duplicate tickets together"""

    LINK_TYPE_CHOICES = [
        ('related', 'Related'),
        ('duplicate', 'Duplicate'),
    ]

    ticket_a = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='links_as_a')
    ticket_b = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='links_as_b')
    link_type = models.CharField(max_length=20, choices=LINK_TYPE_CHOICES, default='related')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='ticket_links_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ticket_links'
        unique_together = [('ticket_a', 'ticket_b')]

    def __str__(self):
        return f"{self.ticket_a.ticket_number} ↔ {self.ticket_b.ticket_number} ({self.link_type})"


class QuickLaborItem(models.Model):
    """Admin-managed labor buttons shown on WO detail. Clicking one logs a labor LineItem."""

    label = models.CharField(max_length=100, help_text='Button label shown to techs, e.g. "Virus / Malware Removal"')
    category = models.CharField(max_length=100, help_text='Groups buttons by category, e.g. "Software"')
    print_description = models.TextField(
        blank=True,
        help_text='Client-facing description printed on the repair report. Leave blank to use the label.',
    )
    default_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Optional. Prefills the price when this button logs a line item. Leave blank for no price.',
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'quick_labor_items'
        ordering = ['category', 'sort_order', 'label']

    def __str__(self):
        return f"{self.category} / {self.label}"

    def get_print_description(self):
        return self.print_description.strip() if self.print_description.strip() else self.label


class LineItem(models.Model):
    """A priced line on a work order (and, in a future phase, a quote).

    Deliberately GENERIC/attachable via a GenericForeignKey so the same primitive
    can hang off a WorkOrder now and a Quote later without a rebuild. MB captures
    and totals prices here; Invoice Ninja remains the billing authority — MB only
    suggests, it does not invoice."""

    KIND_CHOICES = [
        ('labor', 'Labor'),
        ('part', 'Part / Material'),
    ]

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='labor', db_index=True)
    description = models.CharField(max_length=255, help_text='Line label, e.g. "Virus removal" or "1TB SSD".')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(0)])
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)],
        help_text='Per-unit price. Blank = unpriced (not counted in the total).',
    )
    # Where a labor line came from (a QuickLabor button), kept for the report's
    # client-facing print description fallback. Null for custom or part lines.
    source_labor_item = models.ForeignKey(
        QuickLaborItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='line_items',
    )
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='line_items_logged')
    logged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'line_items'
        ordering = ['logged_at']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['kind']),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.description}"

    @property
    def line_total(self):
        """quantity × unit_price, or None when the line is unpriced."""
        if self.unit_price is None:
            return None
        return self.quantity * self.unit_price

    def print_description(self):
        """Client-facing description for the repair report: explicit notes win,
        else the source button's print description."""
        if self.notes.strip():
            return self.notes.strip()
        if self.source_labor_item:
            return self.source_labor_item.get_print_description()
        return ''


class SiteSettings(models.Model):
    """Singleton — site-wide configuration editable from admin."""

    STORAGE_CHOICES = [
        ('local', 'Local Filesystem'),
        ('s3', 'S3-Compatible (AWS, B2, MinIO, Wasabi)'),
    ]

    max_attachment_size_mb = models.IntegerField(
        default=25,
        help_text='Maximum file upload size in megabytes.',
    )
    blocked_extensions = models.TextField(
        default='exe,bat,sh,ps1,cmd,vbs,jar,msi,scr,pif',
        help_text='Comma-separated list of blocked file extensions (without dots).',
    )
    storage_backend = models.CharField(
        max_length=10, choices=STORAGE_CHOICES, default='local',
        help_text='Changing backends requires updating ATTACHMENT_STORAGE_BACKEND in .env and restarting.',
    )
    local_storage_path = models.CharField(
        max_length=500, blank=True,
        help_text='Override for local storage path. Leave blank to use MEDIA_ROOT.',
    )
    s3_bucket_name = models.CharField(max_length=255, blank=True)
    s3_access_key = models.CharField(max_length=255, blank=True)
    s3_secret_key = models.CharField(max_length=255, blank=True)
    s3_endpoint_url = models.CharField(
        max_length=255, blank=True,
        help_text='For B2/MinIO/Wasabi. Leave blank for AWS S3.',
    )
    s3_region = models.CharField(max_length=50, blank=True)

    # Email config
    email_enabled = models.BooleanField(
        default=False,
        help_text='Master switch — disabling this stops all automated outbound email.',
    )
    email_host = models.CharField(max_length=255, blank=True, help_text='e.g. mail.yourdomain.com')
    email_port = models.IntegerField(default=587)
    email_use_tls = models.BooleanField(default=True)
    email_username = models.CharField(max_length=255, blank=True)
    email_password = EncryptedCharField(max_length=255, blank=True)
    email_from = models.EmailField(blank=True, help_text='From address shown to clients. e.g. support@yourdomain.com')
    email_sales_from = models.EmailField(blank=True, help_text='From address for quotes/estimates. Blank = use the support From address above.')

    # Auto-responder suppression patterns (newline-separated fnmatch patterns)
    email_suppression_patterns = models.TextField(
        default='noreply@*\nno-reply@*\ndonotreply@*\nmailer-daemon@*\npostmaster@*\nbounce@*',
        help_text='One fnmatch pattern per line. Emails matching any pattern are suppressed.',
    )

    # Inbound email (IMAP / POP3)
    INBOUND_PROTOCOL_CHOICES = [
        ('imap', 'IMAP'),
        ('pop3', 'POP3'),
    ]
    inbound_email_enabled = models.BooleanField(
        default=False,
        help_text='Enable polling the mailbox for incoming tickets and replies.',
    )
    inbound_protocol = models.CharField(
        max_length=10, choices=INBOUND_PROTOCOL_CHOICES, default='imap',
    )
    inbound_host = models.CharField(max_length=255, blank=True, help_text='e.g. mail.yourdomain.com')
    inbound_port = models.IntegerField(default=993, help_text='993 for IMAP SSL, 995 for POP3 SSL, 143/110 without SSL.')
    inbound_ssl = models.BooleanField(default=True)
    inbound_username = models.CharField(max_length=255, blank=True)
    inbound_password = EncryptedCharField(max_length=255, blank=True)
    inbound_folder = models.CharField(max_length=100, default='INBOX', help_text='IMAP folder to poll. Ignored for POP3.')
    inbound_delete_after_fetch = models.BooleanField(
        default=False,
        help_text='Delete messages after processing (IMAP). POP3 always deletes. Default: mark as read.',
    )
    strip_quoted_replies = models.BooleanField(
        default=True,
        help_text='Strip quoted reply text (> lines, On … wrote: blocks) from inbound replies.',
    )

    # MFA enforcement
    require_mfa = models.BooleanField(
        default=False,
        help_text='Force all users to enroll in TOTP two-factor authentication before accessing the app.',
    )

    inbound_default_client_name = models.CharField(
        max_length=255, blank=True, default='',
        help_text='When no client matches the sender email, new tickets are filed under a new client. Leave blank to auto-name from the sender email domain.',
    )

    # Company Info (used in repair report header and nav)
    company_name = models.CharField(max_length=255, blank=True, default='',
        help_text='Your business name. Appears on repair reports and the nav bar.')
    company_address_line1 = models.CharField(max_length=255, blank=True, default='',
        help_text='Street address, e.g. 235 Coolidge St.')
    company_address_line2 = models.CharField(max_length=255, blank=True, default='',
        help_text='City, State ZIP, e.g. Silverton, OR 97381')
    company_phone = models.CharField(max_length=50, blank=True, default='')
    company_email = models.EmailField(blank=True, default='')
    company_logo = models.ImageField(
        upload_to='company/', blank=True, null=True,
        help_text='PNG, JPG, or SVG. Displayed on repair reports.',
    )

    # Email branding — independent of the app's look. Blank values fall back to
    # the company logo / app Title Bar color, so existing installs are unaffected.
    email_logo = models.ImageField(
        upload_to='email/', blank=True, null=True,
        help_text='Logo shown in outgoing emails. Leave blank to use the company logo.',
    )
    email_header_color = models.CharField(
        max_length=7, blank=True, default='',
        help_text='Header bar color for outgoing emails (e.g. #1f5f5b). Leave blank to use the app Title Bar color.',
    )

    # Mileage / Google Maps
    google_maps_api_key = models.CharField(
        max_length=255, blank=True,
        help_text='Google Maps API key with Distance Matrix API enabled. Restrict to Distance Matrix API in Google Cloud Console.',
    )
    shop_address = models.CharField(
        max_length=255, blank=True,
        help_text='Shop address used as the origin for onsite mileage calculations (e.g. 235 Coolidge St. Silverton Oregon 97381).',
    )

    # Invoice Ninja integration (Phase B — one-directional draft push from a WO).
    # MB captures/totals prices; IN stays the billing authority.
    invoice_ninja_enabled = models.BooleanField(
        default=False, help_text='Enable the "Send to Invoice Ninja" action on work orders.',
    )
    invoice_ninja_url = models.CharField(
        max_length=255, blank=True,
        help_text='Invoice Ninja API base URL. Cloud: https://invoicing.co — self-hosted: your server URL.',
    )
    invoice_ninja_token = EncryptedCharField(
        max_length=255, blank=True,
        help_text='IN API token (Settings → Account Management → Integrations → API Tokens). Stored encrypted.',
    )

    # Status badge colors — hex values rendered as CSS variables
    color_status_new         = models.CharField(max_length=7, default='#dbeafe', blank=True)  # blue-100
    color_status_assigned    = models.CharField(max_length=7, default='#ede9fe', blank=True)  # violet-100
    color_status_in_progress = models.CharField(max_length=7, default='#fef9c3', blank=True)  # yellow-100
    color_status_completed   = models.CharField(max_length=7, default='#dcfce7', blank=True)  # green-100
    color_status_closed      = models.CharField(max_length=7, default='#f3f4f6', blank=True)  # gray-100
    color_status_cancelled   = models.CharField(max_length=7, default='#fee2e2', blank=True)  # red-100

    # Site logo (displayed in nav bar; separate from company_logo on reports)
    site_logo = models.ImageField(
        upload_to='site/', blank=True, null=True,
        help_text='Logo shown in the nav bar. PNG or SVG recommended. Leave blank to show text.',
    )

    # Login page logo (its own slot — the login screen has room for a richer/larger mark
    # than the small nav bar; detailed logos look good here, simple ones in the nav).
    login_logo = models.ImageField(
        upload_to='login/', blank=True, null=True,
        help_text='Logo shown on the login page. Leave blank to show text. Detailed logos work well here.',
    )

    # Site palette
    color_primary     = models.CharField(max_length=7, default='#111827', blank=True)  # gray-900 — nav/toolbar bg
    color_nav_text    = models.CharField(max_length=7, default='#ffffff', blank=True)  # white — nav link text
    color_accent      = models.CharField(max_length=7, default='#2563eb', blank=True)  # blue-600 — links, buttons
    color_sidebar_bg  = models.CharField(max_length=7, default='#1f2937', blank=True)  # gray-800 — sidebar bg
    color_sidebar_text = models.CharField(max_length=7, default='#ffffff', blank=True)  # white — sidebar text
    color_page_bg        = models.CharField(max_length=7, default='#f1f5f9', blank=True)  # page background
    color_page_title     = models.CharField(max_length=7, default='#111827', blank=True)  # page heading text color
    color_title_bar      = models.CharField(max_length=7, default='#ffffff', blank=True)  # page title bar background
    color_section_header      = models.CharField(max_length=7, default='#f8fafc', blank=True)  # section header bar bg
    color_section_header_text = models.CharField(max_length=7, default='#111827', blank=True)  # section header text + links

    class Meta:
        db_table = 'site_settings'
        verbose_name = 'Site Settings'
        verbose_name_plural = 'Site Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # Singleton — prevent deletion

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def get_blocked_extensions(self):
        return [e.strip().lower().lstrip('.') for e in self.blocked_extensions.split(',') if e.strip()]


class Attachment(models.Model):
    """File attachment linked to any model via GenericForeignKey."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    file = models.FileField(upload_to=attachment_upload_path, storage=private_attachment_storage)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='attachments',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'attachments'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
        ]

    def __str__(self):
        return self.original_filename

    @property
    def size_display(self):
        if self.size_bytes < 1024:
            return f'{self.size_bytes} B'
        elif self.size_bytes < 1024 ** 2:
            return f'{self.size_bytes / 1024:.1f} KB'
        else:
            return f'{self.size_bytes / 1024 ** 2:.1f} MB'

    @property
    def extension(self):
        return os.path.splitext(self.original_filename)[1].lstrip('.').lower()


class CannedResponseCategory(models.Model):
    STREAM_CUSTOMER = 'customer'
    STREAM_INTERNAL = 'internal'
    STREAM_CHOICES = [
        (STREAM_CUSTOMER, 'Customer Notes'),
        (STREAM_INTERNAL, 'Tech Notes (Internal)'),
    ]

    stream = models.CharField(max_length=20, choices=STREAM_CHOICES)
    name = models.CharField(max_length=100)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'canned_response_categories'
        ordering = ['stream', 'sort_order', 'name']

    def __str__(self):
        return self.name


class CannedResponse(models.Model):
    STREAM_CUSTOMER = 'customer'
    STREAM_INTERNAL = 'internal'
    STREAM_CHOICES = [
        (STREAM_CUSTOMER, 'Customer Notes'),
        (STREAM_INTERNAL, 'Tech Notes (Internal)'),
    ]

    stream = models.CharField(max_length=20, choices=STREAM_CHOICES)
    category = models.ForeignKey(
        CannedResponseCategory, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='responses'
    )
    label = models.CharField(max_length=100)
    body = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'canned_responses'
        ordering = ['stream', 'category__sort_order', 'sort_order', 'label']

    def __str__(self):
        return self.label


class EmailSignature(models.Model):
    """Reusable email signature blocks. One can be marked as default."""

    name        = models.CharField(max_length=100, unique=True)
    body        = models.TextField(help_text='Plain text. Use blank lines between paragraphs. Rendered with line breaks in HTML emails.')
    is_default  = models.BooleanField(default=False, help_text='Used when a template has no signature assigned.')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'email_signatures'
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Enforce single default
        if self.is_default:
            EmailSignature.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class EmailTemplate(models.Model):
    """Trigger-based email templates sent to clients on ticket events."""

    TRIGGER_CHOICES = [
        ('ticket_created', 'Ticket Created (auto-responder)'),
        ('reply_added', 'Customer-Visible Reply Added'),
        ('status_changed', 'Status Changed'),
        ('ticket_resolved', 'Ticket Resolved'),
    ]

    trigger = models.CharField(max_length=30, choices=TRIGGER_CHOICES, unique=True)
    subject_template = models.CharField(
        max_length=255,
        help_text='Django template syntax. Variables: {{ ticket.ticket_number }}, {{ ticket.subject }}, {{ customer_name }}, {{ client.name }}, {{ contact.first_name }}, {{ status }}, {{ tech_name }}',
    )
    body_template = models.TextField(
        help_text='Plain text body. Same template variables available.',
    )
    signature = models.ForeignKey(
        'EmailSignature',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='templates',
        help_text='Leave blank to use the default signature.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'email_templates'
        ordering = ['trigger']

    def __str__(self):
        return f'{self.get_trigger_display()}'


class BlockedSender(models.Model):
    """Inbound email senders that are silently dropped — no ticket or reply created."""

    pattern = models.CharField(
        max_length=255, unique=True,
        help_text='Exact address or fnmatch pattern (e.g. spam@example.com or *@badomain.com).'
    )
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'blocked_senders'
        ordering = ['pattern']

    def __str__(self):
        return self.pattern


class SuppressedAddress(models.Model):
    """Exact email addresses that should never receive automated emails."""

    email = models.EmailField(unique=True)
    reason = models.CharField(max_length=255, blank=True, help_text='Why this address is suppressed.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'suppressed_addresses'
        ordering = ['email']
        verbose_name_plural = 'Suppressed Addresses'

    def __str__(self):
        return self.email


class EmailSendLog(models.Model):
    """Audit log of every attempted outbound email — sent or suppressed."""

    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('suppressed', 'Suppressed'),
        ('failed', 'Failed'),
    ]

    REASON_CHOICES = [
        ('', 'N/A'),
        ('client_flag', 'Client suppress_emails flag'),
        ('pattern', 'Matched suppression pattern'),
        ('exact_address', 'Exact address suppression list'),
        ('no_address', 'No recipient address found'),
        ('no_template', 'No active template for trigger'),
        ('send_error', 'SMTP send error'),
        ('email_disabled', 'Outbound email disabled'),
    ]

    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, related_name='email_log')
    to_email = models.EmailField(blank=True)
    trigger = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    reason = models.CharField(max_length=50, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'email_send_log'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.trigger} → {self.to_email} [{self.status}]'


class Notification(models.Model):
    """An in-app alert for a user — e.g. an internal tech message that needs a
    timely response. Surfaced via the sidebar bell with an unread count."""

    KIND_CHOICES = [
        ('tech_message', 'Tech Message'),
        ('system_alert', 'System Alert'),
    ]

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    kind = models.CharField(max_length=30, choices=KIND_CHOICES, default='tech_message')
    text = models.CharField(max_length=255)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, null=True, blank=True, related_name='notifications')
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, null=True, blank=True, related_name='notifications')
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['recipient', 'is_read'])]

    def __str__(self):
        return f'{self.kind} → {self.recipient} [{"read" if self.is_read else "unread"}]'

    @property
    def target_url(self):
        from django.urls import reverse
        if self.ticket_id:
            return reverse('core:ticket_detail', args=[self.ticket_id])
        if self.work_order_id:
            return reverse('core:work_order_detail', args=[self.work_order_id])
        return reverse('core:notifications')


class InboundEmailLog(models.Model):
    """Audit log for every message fetched from the inbound mailbox."""

    STATUS_CHOICES = [
        ('new_ticket', 'Created New Ticket'),
        ('reply', 'Added Reply to Ticket'),
        ('duplicate', 'Duplicate — Already Processed'),
        ('error', 'Processing Error'),
        ('processing', 'Processing (claimed)'),
    ]

    message_id = models.CharField(max_length=500, blank=True, db_index=True, help_text='Email Message-ID header.')
    from_email = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True, related_name='inbound_log')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'inbound_email_log'
        ordering = ['-created_at']
        constraints = [
            # Atomic dedup: two log rows can never share a non-empty Message-ID.
            # This is what makes inbound dedup race-proof (claim-by-insert).
            models.UniqueConstraint(
                fields=['message_id'],
                condition=~models.Q(message_id=''),
                name='uniq_inbound_message_id',
            ),
        ]

    def __str__(self):
        return f'{self.from_email} → {self.status} ({self.created_at:%Y-%m-%d %H:%M})'


class KBCategory(models.Model):
    """Knowledge base article category."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'kb_categories'
        ordering = ['sort_order', 'name']
        verbose_name = 'KB Category'
        verbose_name_plural = 'KB Categories'

    def __str__(self):
        return self.name


class KBArticle(models.Model):
    """Internal knowledge base article."""

    ARTICLE_TYPE_CHOICES = [
        ('troubleshooting', 'Troubleshooting'),
        ('how_to', 'How-To'),
        ('vendor', 'Vendor Info'),
        ('internal', 'Internal Procedure'),
    ]

    title = models.CharField(max_length=255)
    content = models.TextField()
    category = models.ForeignKey(
        KBCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='articles',
    )
    article_type = models.CharField(max_length=20, choices=ARTICLE_TYPE_CHOICES, default='how_to', db_index=True)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='kb_articles')
    is_active = models.BooleanField(default=True, db_index=True)
    is_restricted = models.BooleanField(
        default=False,
        help_text='Restricted articles are visible only to users with can_view_restricted_kb.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'kb_articles'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['category', 'is_active']),
            models.Index(fields=['article_type', 'is_active']),
        ]

    def __str__(self):
        return self.title


class TicketQueue(models.Model):
    """Saved, filterable ticket views. owner=None means system queue (visible to all)."""

    SORT_DIRECTION_CHOICES = [('asc', 'Ascending'), ('desc', 'Descending')]

    name = models.CharField(max_length=100)
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='queues',
        help_text='Leave blank for a system queue visible to all users.',
    )
    filter_criteria = models.JSONField(
        default=dict,
        blank=True,
        help_text='Keys: status (list), assigned_to (int), help_topic (int), sla_plan (int), overdue (bool), client (int)',
    )
    sort_field = models.CharField(max_length=50, default='created_at')
    sort_direction = models.CharField(max_length=4, choices=SORT_DIRECTION_CHOICES, default='desc')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ticket_queues'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def is_system_queue(self):
        return self.owner is None


class DashboardTile(models.Model):
    """Configurable tile on the dashboard. Two rows: ticket and workorder."""

    ROW_CHOICES = [('ticket', 'Tickets'), ('workorder', 'Work Orders')]
    VISIBLE_TO_CHOICES = [('all', 'All Users'), ('admin', 'Admins Only'), ('tech', 'Techs Only')]

    row = models.CharField(max_length=12, choices=ROW_CHOICES)
    label = models.CharField(max_length=100)
    status_filter = models.JSONField(
        default=list,
        help_text='List of status values to count. Empty = count all.',
    )
    link_url = models.CharField(
        max_length=200,
        blank=True,
        help_text='URL the tile links to. Use relative paths.',
    )
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    visible_to = models.CharField(max_length=10, choices=VISIBLE_TO_CHOICES, default='all')
    icon = models.CharField(max_length=10, blank=True, help_text='Optional emoji icon.')

    class Meta:
        db_table = 'dashboard_tiles'
        ordering = ['row', 'sort_order']

    def __str__(self):
        return f'{self.get_row_display()} — {self.label}'


class CustomField(models.Model):
    """Admin-defined extra fields for Tickets or Work Orders."""

    FIELD_TYPE_CHOICES = [
        ('text', 'Text (single line)'),
        ('textarea', 'Text Area (multi-line)'),
        ('select', 'Dropdown Select'),
        ('checkbox', 'Checkbox (yes/no)'),
        ('date', 'Date'),
    ]

    APPLIES_TO_CHOICES = [
        ('ticket', 'Tickets'),
        ('workorder', 'Work Orders'),
        ('both', 'Both'),
    ]

    label = models.CharField(max_length=100)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default='text')
    applies_to = models.CharField(max_length=10, choices=APPLIES_TO_CHOICES, default='ticket')
    is_required = models.BooleanField(default=False)
    help_text = models.CharField(max_length=255, blank=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    # Optional scope — if set, field only appears when this topic/type is selected
    scoped_to_help_topic = models.ForeignKey(
        HelpTopic,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='custom_fields',
        help_text='Only show on tickets with this help topic. Leave blank for all tickets.',
    )
    scoped_to_repair_type = models.ForeignKey(
        RepairType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='custom_fields',
        help_text='Only show on work orders with this repair type. Leave blank for all work orders.',
    )

    class Meta:
        db_table = 'custom_fields'
        ordering = ['applies_to', 'sort_order', 'label']

    def __str__(self):
        return f'{self.label} ({self.get_applies_to_display()})'

    def applies_to_tickets(self):
        return self.applies_to in ('ticket', 'both')

    def applies_to_workorders(self):
        return self.applies_to in ('workorder', 'both')


class CustomFieldChoice(models.Model):
    """Options for select-type CustomFields."""

    field = models.ForeignKey(CustomField, on_delete=models.CASCADE, related_name='choices')
    label = models.CharField(max_length=100)
    sort_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'custom_field_choices'
        ordering = ['sort_order', 'label']

    def __str__(self):
        return f'{self.field.label}: {self.label}'


class CustomFieldValue(models.Model):
    """EAV storage — one row per (object, field) pair."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    field = models.ForeignKey(CustomField, on_delete=models.CASCADE, related_name='values')
    value = models.TextField(blank=True)

    class Meta:
        db_table = 'custom_field_values'
        unique_together = [('content_type', 'object_id', 'field')]
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
        ]

    def __str__(self):
        return f'{self.field.label}={self.value}'


# --- Audit Log Registration ---
from auditlog.registry import auditlog
auditlog.register(Ticket)
auditlog.register(TicketReply)
auditlog.register(WorkOrder)
auditlog.register(WorkOrderNote)


from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=WorkOrder)
def create_invoice_for_new_work_order(sender, instance, created, **kwargs):
    if created:
        Invoice.objects.get_or_create(work_order=instance)
