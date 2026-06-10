from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.core.validators import MinValueValidator
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField
import uuid
import os


def attachment_upload_path(instance, filename):
    ct = instance.content_type
    return f'attachments/{ct.app_label}/{ct.model}/{instance.object_id}/{filename}'


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
    can_create_workorder = models.BooleanField(default=True)
    can_edit_workorder = models.BooleanField(default=True)
    can_close_workorder = models.BooleanField(default=True)

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'clients'
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.name


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
    serial_number = models.CharField(max_length=100, blank=True, unique=True)
    model = models.CharField(max_length=100, blank=True)
    manufacturer = models.CharField(max_length=100, blank=True)
    os = models.CharField(max_length=20, choices=OS_CHOICES, blank=True)
    os_version = models.CharField(max_length=100, blank=True)
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
        if self.status in self.CLOSED_STATUSES:
            return False
        if self.sla_plan and self.sla_plan.disable_overdue_alerts:
            return False
        return timezone.now() > self.due_at

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
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='work_orders_assigned')
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPE_CHOICES, default='in_shop')
    status = models.CharField(max_length=50, default='new', db_index=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='normal')
    time_spent_minutes = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    scheduled_date = models.DateField(null=True, blank=True)
    completed_date = models.DateTimeField(null=True, blank=True)
    notes_internal = models.TextField(blank=True, help_text="Technician-only notes")
    notes_customer_visible = models.TextField(blank=True, help_text="What the customer sees")
    invoice_ninja_ref = models.CharField(max_length=100, blank=True, help_text='Invoice Ninja invoice reference number')
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

    def __str__(self):
        return f"{self.work_order_number}: {self.client.name}"

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoices'

    def __str__(self):
        return f'Invoice for {self.work_order.work_order_number} — {self.get_billing_status_display()}'


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
    """Admin-managed labor buttons shown on WO detail. Clicking one logs a WorkPerformed entry."""

    label = models.CharField(max_length=100, help_text='Button label shown to techs, e.g. "Virus / Malware Removal"')
    category = models.CharField(max_length=100, help_text='Groups buttons by category, e.g. "Software"')
    print_description = models.TextField(
        blank=True,
        help_text='Client-facing description printed on the repair report. Leave blank to use the label.',
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


class WorkPerformed(models.Model):
    """Records work logged against a work order — from quick labor bank or custom entry."""

    work_order = models.ForeignKey('WorkOrder', on_delete=models.CASCADE, related_name='work_performed')
    labor_item = models.ForeignKey(QuickLaborItem, on_delete=models.PROTECT, null=True, blank=True, related_name='logged_entries')
    custom_label = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='work_performed_logged')
    logged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'work_performed'
        ordering = ['logged_at']

    def label(self):
        return self.custom_label or (self.labor_item.label if self.labor_item else '—')

    def description(self):
        return self.notes or (self.labor_item.print_description if self.labor_item else '')

    def __str__(self):
        return f"{self.work_order.work_order_number} — {self.label()}"


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
        help_text='PNG, JPG, or SVG. Displayed on repair reports and the nav bar.',
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

    file = models.FileField(upload_to=attachment_upload_path)
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
        help_text='Django template syntax. Variables: {{ ticket.ticket_number }}, {{ ticket.subject }}, {{ client.name }}, {{ status }}, {{ tech_name }}',
    )
    body_template = models.TextField(
        help_text='Plain text body. Same template variables available.',
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


class InboundEmailLog(models.Model):
    """Audit log for every message fetched from the inbound mailbox."""

    STATUS_CHOICES = [
        ('new_ticket', 'Created New Ticket'),
        ('reply', 'Added Reply to Ticket'),
        ('duplicate', 'Duplicate — Already Processed'),
        ('error', 'Processing Error'),
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
