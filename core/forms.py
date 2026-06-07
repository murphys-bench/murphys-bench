from django import forms
from .models import WorkOrder, Client, Contact, Device, Ticket, RepairType, HelpTopic, SLAPlan, KBCategory, KBArticle


class WorkOrderForm(forms.ModelForm):
    apply_checklist = forms.BooleanField(
        required=False,
        initial=False,
        label='Apply default checklist for selected repair type',
        widget=forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
    )

    class Meta:
        model = WorkOrder
        fields = [
            'client', 'device', 'repair_type', 'assigned_to',
            'status', 'priority', 'scheduled_date',
            'time_spent_minutes', 'notes_customer_visible', 'notes_internal',
        ]
        widgets = {
            'client': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'device': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'repair_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'assigned_to': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'status': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'priority': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'scheduled_date': forms.DateInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'type': 'date'}),
            'time_spent_minutes': forms.NumberInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'notes_customer_visible': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 4}),
            'notes_internal': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show active clients
        self.fields['client'].queryset = Client.objects.filter(is_active=True).order_by('name')
        # Only show technicians (staff users)
        from .models import User
        self.fields['assigned_to'].queryset = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
        self.fields['assigned_to'].required = False
        self.fields['device'].required = False
        self.fields['repair_type'].required = False
        self.fields['scheduled_date'].required = False
        self.fields['time_spent_minutes'].required = False


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            'name', 'email', 'phone',
            'address_street', 'address_city', 'address_state', 'address_zip',
            'notes', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'email': forms.EmailInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'phone': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'address_street': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'address_city': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'address_state': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'maxlength': '2', 'placeholder': 'OR'}),
            'address_zip': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'notes': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
        }


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = [
            'client', 'name', 'device_type', 'repair_type',
            'manufacturer', 'model', 'serial_number',
            'notes', 'is_active',
        ]
        widgets = {
            'client': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'name': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'placeholder': "e.g. Mike's Laptop"}),
            'device_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'repair_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'manufacturer': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'model': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'serial_number': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'notes': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.filter(is_active=True).order_by('name')
        self.fields['repair_type'].required = False
        self.fields['manufacturer'].required = False
        self.fields['model'].required = False
        self.fields['serial_number'].required = False
        self.fields['notes'].required = False


SELECT_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}
TEXT_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}
TEXTAREA_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 4}


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['client', 'device', 'help_topic', 'sla_plan', 'subject', 'description', 'source', 'status']
        widgets = {
            'client': forms.Select(attrs=SELECT_WIDGET),
            'device': forms.Select(attrs=SELECT_WIDGET),
            'help_topic': forms.Select(attrs=SELECT_WIDGET),
            'sla_plan': forms.Select(attrs=SELECT_WIDGET),
            'subject': forms.TextInput(attrs=TEXT_WIDGET),
            'description': forms.Textarea(attrs=TEXTAREA_WIDGET),
            'source': forms.Select(attrs=SELECT_WIDGET),
            'status': forms.Select(attrs=SELECT_WIDGET),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.filter(is_active=True).order_by('name')
        self.fields['device'].required = False
        self.fields['help_topic'].queryset = HelpTopic.objects.filter(is_active=True).order_by('sort_order', 'name')
        self.fields['help_topic'].required = False
        self.fields['sla_plan'].queryset = SLAPlan.objects.filter(is_active=True).order_by('name')
        self.fields['sla_plan'].required = False

    def save(self, commit=True):
        ticket = super().save(commit=False)
        # If SLA assigned, calculate due_at; if cleared, clear due_at
        if ticket.sla_plan_id:
            from django.utils import timezone
            if not ticket.due_at or 'sla_plan' in self.changed_data:
                ticket.due_at = (ticket.created_at or timezone.now()) + timezone.timedelta(
                    hours=ticket.sla_plan.grace_period_hours
                )
                ticket.overdue_acknowledged_by = None
                ticket.overdue_acknowledged_at = None
        else:
            ticket.due_at = None
        if commit:
            ticket.save()
        return ticket


class KBArticleForm(forms.ModelForm):
    class Meta:
        model = KBArticle
        fields = ['title', 'category', 'article_type', 'content', 'is_active', 'is_restricted']
        widgets = {
            'title': forms.TextInput(attrs=TEXT_WIDGET),
            'category': forms.Select(attrs=SELECT_WIDGET),
            'article_type': forms.Select(attrs=SELECT_WIDGET),
            'content': forms.Textarea(attrs={**TEXTAREA_WIDGET, 'rows': 20}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
            'is_restricted': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = KBCategory.objects.order_by('sort_order', 'name')
        self.fields['category'].required = False


class TicketConvertForm(forms.Form):
    repair_type = forms.ModelChoiceField(
        queryset=RepairType.objects.filter(is_active=True).order_by('name'),
        required=False,
        widget=forms.Select(attrs=SELECT_WIDGET),
    )
    assigned_to = forms.ModelChoiceField(
        queryset=None,
        required=False,
        widget=forms.Select(attrs=SELECT_WIDGET),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import User
        self.fields['assigned_to'].queryset = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
