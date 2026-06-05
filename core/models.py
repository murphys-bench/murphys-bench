from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.core.validators import MinValueValidator
import uuid


class User(AbstractUser):
    """Extended user model for technicians and admins"""

    ROLE_CHOICES = [
        ('admin', 'Administrator'),
        ('technician', 'Technician'),
        ('viewer', 'Viewer (Read-Only)'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='technician')
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        db_table = 'users'
        ordering = ['first_name', 'last_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}" or self.username


class Client(models.Model):
    """Companies/customers requesting service"""

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address_street = models.CharField(max_length=255, blank=True)
    address_city = models.CharField(max_length=100, blank=True)
    address_state = models.CharField(max_length=2, blank=True)
    address_zip = models.CharField(max_length=10, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
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


class RepairType(models.Model):
    """Categories of repairs (e.g., Laptop Repair, Desktop Repair)"""

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'repair_types'
        ordering = ['name']
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

    id = models.AutoField(primary_key=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='devices')
    repair_type = models.ForeignKey(RepairType, on_delete=models.SET_NULL, null=True, related_name='devices')
    name = models.CharField(max_length=255, help_text="e.g., 'Mike\'s Laptop'")
    device_type = models.CharField(max_length=50, choices=DEVICE_TYPE_CHOICES, default='laptop')
    serial_number = models.CharField(max_length=100, blank=True, unique=True)
    model = models.CharField(max_length=100, blank=True)
    manufacturer = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
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
    subject = models.CharField(max_length=255)
    description = models.TextField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='email')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open', db_index=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='tickets_created')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tickets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client', 'status']),
            models.Index(fields=['source']),
        ]

    def __str__(self):
        return f"{self.ticket_number}: {self.subject}"

    @classmethod
    def generate_ticket_number(cls):
        """Generate unique ticket number like TKT-20260604-0001"""
        from django.utils import timezone
        today = timezone.now().strftime('%Y%m%d')
        count = cls.objects.filter(created_at__date=timezone.now().date()).count() + 1
        return f"TKT-{today}-{count:04d}"


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

    id = models.AutoField(primary_key=True)
    work_order_number = models.CharField(max_length=20, unique=True, db_index=True)
    ticket = models.OneToOneField(Ticket, on_delete=models.SET_NULL, null=True, blank=True, related_name='work_order_created')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='work_orders')
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name='work_orders')
    repair_type = models.ForeignKey(RepairType, on_delete=models.SET_NULL, null=True, related_name='work_orders')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='work_orders_assigned')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new', db_index=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='normal')
    time_spent_minutes = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    scheduled_date = models.DateField(null=True, blank=True)
    completed_date = models.DateTimeField(null=True, blank=True)
    notes_internal = models.TextField(blank=True, help_text="Technician-only notes")
    notes_customer_visible = models.TextField(blank=True, help_text="What the customer sees")
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

    def __str__(self):
        return f"{self.work_order_number}: {self.client.name}"

    @classmethod
    def generate_work_order_number(cls):
        """Generate unique work order number like WO-20260604-0001"""
        from django.utils import timezone
        today = timezone.now().strftime('%Y%m%d')
        count = cls.objects.filter(created_at__date=timezone.now().date()).count() + 1
        return f"WO-{today}-{count:04d}"

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


class Mileage(models.Model):
    """Travel logging for billing/expense tracking"""

    id = models.AutoField(primary_key=True)
    technician = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mileage_entries')
    trip_date = models.DateField(db_index=True)
    from_location = models.CharField(max_length=255, blank=True)
    to_location = models.CharField(max_length=255, blank=True)
    miles = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
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
    """Individual tasks in a checklist template"""

    id = models.AutoField(primary_key=True)
    checklist = models.ForeignKey(Checklist, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=255)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'checklist_items'
        ordering = ['checklist', 'sort_order']
        indexes = [
            models.Index(fields=['checklist', 'is_active']),
        ]

    def __str__(self):
        return f"{self.checklist.name}: {self.description}"


class CannedResponse(models.Model):
    """Template responses for common situations"""

    id = models.AutoField(primary_key=True)
    repair_type = models.ForeignKey(RepairType, on_delete=models.SET_NULL, null=True, blank=True, related_name='canned_responses')
    title = models.CharField(max_length=100)
    content = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'canned_responses'
        ordering = ['repair_type', 'title']
        indexes = [
            models.Index(fields=['repair_type', 'is_active']),
        ]

    def __str__(self):
        return self.title
