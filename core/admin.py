from django.contrib import admin
from .models import (
    User, Client, Contact, Device, Ticket, TicketReply, WorkOrder, WorkOrderNote,
    WorkOrderItem, Mileage, RepairType, Checklist, ChecklistItem, CannedResponse,
    SiteSettings, Attachment, EmailTemplate, SuppressedAddress, EmailSendLog,
    Role, TechSkill, SLAPlan, HelpTopic, KBCategory, KBArticle,
    InboundEmailLog,
)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_system', 'can_manage_settings', 'can_view_all_tickets', 'can_close_tickets', 'can_manage_users']
    list_filter = ['is_system']
    fieldsets = (
        (None, {'fields': ('name', 'description', 'is_system')}),
        ('Ticket Permissions', {'fields': (
            'can_create_ticket', 'can_edit_ticket', 'can_delete_ticket',
            'can_assign_ticket', 'can_reply_internal', 'can_reply_customer',
            'can_close_tickets', 'can_view_all_tickets',
        )}),
        ('Work Order Permissions', {'fields': ('can_create_workorder', 'can_edit_workorder', 'can_close_workorder')}),
        ('System Permissions', {'fields': (
            'can_manage_settings', 'can_manage_users', 'can_view_reports',
            'can_manage_kb', 'can_view_restricted_kb',
        )}),
    )


@admin.register(TechSkill)
class TechSkillAdmin(admin.ModelAdmin):
    list_display = ['name', 'description']
    search_fields = ['name']


# User Admin
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['username', 'first_name', 'last_name', 'role', 'role_obj', 'is_staff', 'is_active']
    list_filter = ['role', 'role_obj', 'is_staff', 'is_active', 'date_joined']
    search_fields = ['username', 'first_name', 'last_name', 'email', 'phone']
    filter_horizontal = ['skills']
    fieldsets = (
        ('Login', {'fields': ('username', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'email', 'phone')}),
        ('Role & Skills', {'fields': ('role', 'role_obj', 'skills')}),
        ('Permissions', {'fields': ('is_staff', 'is_active', 'groups', 'user_permissions')}),
        ('Important Dates', {'fields': ('last_login', 'date_joined'), 'classes': ('collapse',)}),
    )


# Client & Contact Admin
class ContactInline(admin.TabularInline):
    model = Contact
    extra = 1
    fields = ['first_name', 'last_name', 'email', 'phone', 'title', 'is_primary', 'is_active']


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'phone', 'address_city', 'is_active']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'email', 'phone']
    inlines = [ContactInline]
    fieldsets = (
        ('Company Info', {'fields': ('name', 'email', 'phone')}),
        ('Address', {'fields': ('address_street', 'address_city', 'address_state', 'address_zip')}),
        ('Status', {'fields': ('is_active', 'suppress_emails')}),
        ('Notes', {'fields': ('notes',), 'classes': ('collapse',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'client', 'email', 'phone', 'is_primary', 'is_active']
    list_filter = ['client', 'is_primary', 'is_active']
    search_fields = ['first_name', 'last_name', 'email', 'phone', 'client__name']
    fieldsets = (
        ('Contact Info', {'fields': ('client', 'first_name', 'last_name', 'email', 'phone', 'title')}),
        ('Status', {'fields': ('is_primary', 'is_active')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['created_at', 'updated_at']


# Device Admin
@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ['name', 'client', 'device_type', 'serial_number', 'is_active']
    list_filter = ['device_type', 'is_active', 'client']
    search_fields = ['name', 'serial_number', 'model', 'manufacturer', 'client__name']
    fieldsets = (
        ('Device Info', {'fields': ('client', 'name', 'device_type', 'repair_type')}),
        ('Details', {'fields': ('manufacturer', 'model', 'serial_number')}),
        ('Status', {'fields': ('is_active',)}),
        ('Notes', {'fields': ('notes',), 'classes': ('collapse',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['created_at', 'updated_at']


# Repair Type Admin
@admin.register(RepairType)
class RepairTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']
    fieldsets = (
        ('Repair Type', {'fields': ('name', 'is_active')}),
        ('Description', {'fields': ('description',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['created_at', 'updated_at']


# Ticket Admin
# Ticket Reply Inline
class TicketReplyInline(admin.TabularInline):
    model = TicketReply
    extra = 1
    fields = ['reply_type', 'content', 'created_by', 'created_at']
    readonly_fields = ['created_by', 'created_at']


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['ticket_number', 'client', 'subject', 'status', 'source', 'created_at']
    list_filter = ['status', 'source', 'created_at']
    search_fields = ['ticket_number', 'subject', 'client__name', 'description']
    inlines = [TicketReplyInline]
    fieldsets = (
        ('Ticket Info', {'fields': ('ticket_number', 'client', 'device', 'source')}),
        ('Issue', {'fields': ('subject', 'description')}),
        ('Status', {'fields': ('status', 'created_by')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['ticket_number', 'created_at', 'updated_at']

    def save_model(self, request, obj, form, change):
        if not change:  # Creating new ticket
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(TicketReply)
class TicketReplyAdmin(admin.ModelAdmin):
    list_display = ['ticket', 'reply_type', 'created_by', 'created_at']
    list_filter = ['reply_type', 'created_at']
    search_fields = ['ticket__ticket_number', 'content']
    readonly_fields = ['created_at', 'updated_at']


# Work Order Notes & Items Inlines
class WorkOrderNoteInline(admin.TabularInline):
    model = WorkOrderNote
    extra = 1
    fields = ['note_type', 'content', 'created_by', 'created_at']
    readonly_fields = ['created_by', 'created_at']


class WorkOrderItemInline(admin.TabularInline):
    model = WorkOrderItem
    extra = 1
    fields = ['item_type', 'description', 'quantity', 'unit', 'unit_price', 'is_completed']


# Work Order Admin (Main Entity)
@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ['work_order_number', 'client', 'device', 'assigned_to', 'status', 'priority', 'created_at']
    list_filter = ['status', 'priority', 'assigned_to', 'created_at']
    search_fields = ['work_order_number', 'client__name', 'device__name']
    inlines = [WorkOrderItemInline, WorkOrderNoteInline]

    fieldsets = (
        ('Work Order Info', {'fields': ('work_order_number', 'ticket', 'client', 'device')}),
        ('Assignment', {'fields': ('assigned_to', 'status', 'priority')}),
        ('Repair', {'fields': ('repair_type',)}),
        ('Timing', {'fields': ('scheduled_date', 'time_spent_minutes', 'completed_date')}),
        ('Notes - Customer Visible', {'fields': ('notes_customer_visible',)}),
        ('Notes - Internal Only', {'fields': ('notes_internal',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['work_order_number', 'created_at', 'updated_at']

    def save_model(self, request, obj, form, change):
        if not change:  # Creating new work order
            if not obj.work_order_number:
                obj.work_order_number = WorkOrder.generate_work_order_number()
        super().save_model(request, obj, form, change)


# Mileage Admin
@admin.register(Mileage)
class MileageAdmin(admin.ModelAdmin):
    list_display = ['trip_date', 'technician', 'from_location', 'to_location', 'miles', 'purpose']
    list_filter = ['trip_date', 'technician', 'work_order']
    search_fields = ['technician__first_name', 'technician__last_name', 'purpose', 'from_location', 'to_location']
    fieldsets = (
        ('Trip Info', {'fields': ('technician', 'trip_date', 'from_location', 'to_location', 'miles')}),
        ('Details', {'fields': ('purpose', 'work_order', 'notes')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['created_at', 'updated_at']


# Checklist & Items Inlines
class ChecklistItemInline(admin.TabularInline):
    model = ChecklistItem
    extra = 1
    fields = ['description', 'sort_order', 'is_active']


@admin.register(Checklist)
class ChecklistAdmin(admin.ModelAdmin):
    list_display = ['name', 'repair_type', 'is_default', 'is_active']
    list_filter = ['repair_type', 'is_default', 'is_active']
    search_fields = ['name', 'description']
    inlines = [ChecklistItemInline]
    fieldsets = (
        ('Checklist Info', {'fields': ('name', 'repair_type', 'is_default', 'is_active')}),
        ('Description', {'fields': ('description',), 'classes': ('collapse',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['created_at', 'updated_at']


# Canned Response Admin
@admin.register(CannedResponse)
class CannedResponseAdmin(admin.ModelAdmin):
    list_display = ['title', 'repair_type', 'is_active']
    list_filter = ['repair_type', 'is_active']
    search_fields = ['title', 'content']
    fieldsets = (
        ('Response Info', {'fields': ('title', 'repair_type', 'is_active')}),
        ('Content', {'fields': ('content',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ['created_at', 'updated_at']


# Site Settings (singleton)
@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Outbound Email', {
            'fields': ('email_enabled', 'email_from', 'email_host', 'email_port', 'email_use_tls', 'email_username', 'email_password'),
        }),
        ('Email Suppression Patterns', {
            'fields': ('email_suppression_patterns',),
            'description': 'One fnmatch pattern per line (e.g. noreply@*). Emails matching any pattern are suppressed and logged.',
        }),
        ('Attachments — Limits', {
            'fields': ('max_attachment_size_mb', 'blocked_extensions'),
        }),
        ('Attachments — Storage', {
            'fields': ('storage_backend', 'local_storage_path'),
            'description': 'Changing the storage backend requires updating ATTACHMENT_STORAGE_BACKEND in .env and restarting.',
        }),
        ('S3-Compatible Storage Credentials', {
            'fields': ('s3_bucket_name', 's3_access_key', 's3_secret_key', 's3_endpoint_url', 's3_region'),
            'classes': ('collapse',),
        }),
        ('Inbound Email', {
            'fields': (
                'inbound_email_enabled', 'inbound_protocol',
                'inbound_host', 'inbound_port', 'inbound_ssl',
                'inbound_username', 'inbound_password',
                'inbound_folder', 'inbound_delete_after_fetch',
                'strip_quoted_replies', 'inbound_default_client_name',
            ),
            'description': (
                'Polling command: python manage.py fetch_inbound_email — run every 1-5 minutes via cron. '
                'New emails create tickets; replies matching [TKT-…] thread into existing tickets.'
            ),
        }),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ['original_filename', 'content_type', 'object_id', 'size_bytes', 'uploaded_by', 'created_at']
    list_filter = ['content_type', 'created_at']
    search_fields = ['original_filename', 'uploaded_by__username']
    readonly_fields = ['content_type', 'object_id', 'file', 'original_filename', 'mime_type', 'size_bytes', 'uploaded_by', 'created_at']


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ['get_trigger_display', 'subject_template', 'is_active']
    list_filter = ['is_active', 'trigger']
    fieldsets = (
        ('Template', {'fields': ('trigger', 'is_active', 'subject_template', 'body_template')}),
        ('Available Variables', {
            'description': (
                '{{ ticket.ticket_number }} — {{ ticket.subject }} — {{ ticket.get_status_display }} — '
                '{{ client.name }} — {{ tech_name }} — {{ status }} — {{ site_name }}'
            ),
            'fields': (),
        }),
    )


@admin.register(SuppressedAddress)
class SuppressedAddressAdmin(admin.ModelAdmin):
    list_display = ['email', 'reason', 'created_at']
    search_fields = ['email', 'reason']
    readonly_fields = ['created_at']


@admin.register(EmailSendLog)
class EmailSendLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'trigger', 'to_email', 'status', 'reason', 'ticket']
    list_filter = ['status', 'trigger', 'created_at']
    search_fields = ['to_email', 'ticket__ticket_number']
    readonly_fields = ['ticket', 'to_email', 'trigger', 'status', 'reason', 'detail', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(InboundEmailLog)
class InboundEmailLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'status', 'from_email', 'subject', 'ticket', 'detail']
    list_filter = ['status', 'created_at']
    search_fields = ['from_email', 'subject', 'ticket__ticket_number', 'message_id']
    readonly_fields = ['message_id', 'from_email', 'subject', 'ticket', 'status', 'detail', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SLAPlan)
class SLAPlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'grace_period_hours', 'is_active', 'is_transient', 'disable_overdue_alerts']
    list_filter = ['is_active', 'is_transient']
    search_fields = ['name']


@admin.register(HelpTopic)
class HelpTopicAdmin(admin.ModelAdmin):
    list_display = ['name', 'default_sla', 'is_active', 'sort_order']
    list_filter = ['is_active', 'default_sla']
    search_fields = ['name']


class KBArticleInline(admin.TabularInline):
    model = KBArticle
    extra = 0
    fields = ['title', 'article_type', 'is_active', 'is_restricted']


@admin.register(KBCategory)
class KBCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'sort_order']
    search_fields = ['name']
    inlines = [KBArticleInline]


@admin.register(KBArticle)
class KBArticleAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'article_type', 'author', 'is_active', 'is_restricted', 'updated_at']
    list_filter = ['article_type', 'category', 'is_active', 'is_restricted']
    search_fields = ['title', 'content']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        (None, {'fields': ('title', 'category', 'article_type', 'author', 'is_active', 'is_restricted')}),
        ('Content', {'fields': ('content',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
