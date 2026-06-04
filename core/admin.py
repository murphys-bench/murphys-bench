from django.contrib import admin
from .models import (
    User, Client, Contact, Device, Ticket, WorkOrder, WorkOrderNote,
    WorkOrderItem, Mileage, RepairType, Checklist, ChecklistItem, CannedResponse
)


# User Admin
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['username', 'first_name', 'last_name', 'role', 'is_staff', 'is_active']
    list_filter = ['role', 'is_staff', 'is_active', 'date_joined']
    search_fields = ['username', 'first_name', 'last_name', 'email', 'phone']
    fieldsets = (
        ('Login', {'fields': ('username', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'email', 'phone')}),
        ('Permissions', {'fields': ('role', 'is_staff', 'is_active', 'groups', 'user_permissions')}),
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
        ('Status', {'fields': ('is_active',)}),
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
@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['ticket_number', 'client', 'subject', 'status', 'source', 'created_at']
    list_filter = ['status', 'source', 'created_at']
    search_fields = ['ticket_number', 'subject', 'client__name', 'description']
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
