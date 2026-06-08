from django import forms
from .models import WorkOrder, Client, Contact, ContactPhone, Device, Ticket, RepairType, HelpTopic, SLAPlan, KBCategory, KBArticle, Mileage, SiteSettings


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
            'service_type', 'status', 'priority', 'scheduled_date',
            'time_spent_minutes', 'notes_customer_visible', 'notes_internal',
        ]
        widgets = {
            'client': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'device': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'repair_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'assigned_to': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'service_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
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
            'name', 'client_type', 'email', 'phone',
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
            'client_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
        }


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = [
            'client', 'assigned_contact', 'name', 'device_type', 'repair_type',
            'manufacturer', 'model', 'serial_number',
            'os', 'os_version', 'condition_at_intake',
            'notes', 'is_active',
        ]
        widgets = {
            'client': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'assigned_contact': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'name': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'placeholder': "e.g. Mike's Laptop"}),
            'device_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'repair_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'manufacturer': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'model': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'serial_number': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'os': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'os_version': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'placeholder': 'e.g. 11 Pro 10.0.26200.0'}),
            'condition_at_intake': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'notes': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
        }

    def __init__(self, *args, client_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.filter(is_active=True).order_by('name')
        self.fields['repair_type'].required = False
        self.fields['assigned_contact'].required = False
        self.fields['manufacturer'].required = False
        self.fields['model'].required = False
        self.fields['serial_number'].required = False
        self.fields['os'].required = False
        self.fields['os_version'].required = False
        self.fields['condition_at_intake'].required = False
        self.fields['notes'].required = False

        # Filter assigned_contact to the selected client's contacts
        if client_id:
            self.fields['assigned_contact'].queryset = Contact.objects.filter(
                client_id=client_id
            ).order_by('name')
        elif self.instance and self.instance.pk:
            self.fields['assigned_contact'].queryset = Contact.objects.filter(
                client=self.instance.client
            ).order_by('name')
        else:
            self.fields['assigned_contact'].queryset = Contact.objects.none()


SELECT_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}
TEXT_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}
TEXTAREA_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 4}


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['client', 'device', 'help_topic', 'sla_plan', 'assigned_to', 'subject', 'description', 'source', 'status']
        widgets = {
            'client': forms.Select(attrs=SELECT_WIDGET),
            'device': forms.Select(attrs=SELECT_WIDGET),
            'help_topic': forms.Select(attrs=SELECT_WIDGET),
            'sla_plan': forms.Select(attrs=SELECT_WIDGET),
            'assigned_to': forms.Select(attrs=SELECT_WIDGET),
            'subject': forms.TextInput(attrs=TEXT_WIDGET),
            'description': forms.Textarea(attrs=TEXTAREA_WIDGET),
            'source': forms.Select(attrs=SELECT_WIDGET),
            'status': forms.Select(attrs=SELECT_WIDGET),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import User as UserModel
        self.fields['client'].queryset = Client.objects.filter(is_active=True).order_by('name')
        self.fields['device'].required = False
        self.fields['help_topic'].queryset = HelpTopic.objects.filter(is_active=True).order_by('sort_order', 'name')
        self.fields['help_topic'].required = False
        self.fields['sla_plan'].queryset = SLAPlan.objects.filter(is_active=True).order_by('name')
        self.fields['sla_plan'].required = False
        self.fields['assigned_to'].queryset = UserModel.objects.filter(is_active=True).order_by('first_name', 'last_name')
        self.fields['assigned_to'].required = False

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


class TicketQueueForm(forms.ModelForm):
    class Meta:
        from .models import TicketQueue
        model = TicketQueue
        fields = ['name', 'sort_field', 'sort_direction', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs=TEXT_WIDGET),
            'sort_field': forms.Select(attrs=SELECT_WIDGET, choices=[
                ('created_at', 'Date Created'),
                ('updated_at', 'Last Updated'),
                ('due_at', 'Due Date'),
                ('subject', 'Subject'),
            ]),
            'sort_direction': forms.Select(attrs=SELECT_WIDGET),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
        }

    def __init__(self, *args, is_admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_admin = is_admin
        # owner field only shown to admins (for system queue creation)
        if is_admin:
            from .models import User as UserModel
            self.fields['owner'] = forms.ModelChoiceField(
                queryset=UserModel.objects.filter(is_active=True).order_by('first_name', 'last_name'),
                required=False,
                widget=forms.Select(attrs=SELECT_WIDGET),
                label='Owner (leave blank for system queue)',
            )
            self.Meta.fields = ['name', 'owner', 'sort_field', 'sort_direction', 'is_active']


class MileageForm(forms.ModelForm):
    class Meta:
        model = Mileage
        fields = ['trip_date', 'trip_type', 'miles', 'from_location', 'to_location', 'purpose', 'work_order', 'notes']
        widgets = {
            'trip_date': forms.DateInput(attrs={**TEXT_WIDGET, 'type': 'date'}),
            'trip_type': forms.Select(attrs=SELECT_WIDGET),
            'miles': forms.NumberInput(attrs={**TEXT_WIDGET, 'step': '0.1', 'min': '0'}),
            'from_location': forms.TextInput(attrs=TEXT_WIDGET),
            'to_location': forms.TextInput(attrs=TEXT_WIDGET),
            'purpose': forms.TextInput(attrs=TEXT_WIDGET),
            'work_order': forms.Select(attrs=SELECT_WIDGET),
            'notes': forms.Textarea(attrs={**TEXTAREA_WIDGET, 'rows': 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['from_location'].required = False
        self.fields['to_location'].required = False
        self.fields['purpose'].required = False
        self.fields['work_order'].required = False
        self.fields['notes'].required = False
        self.fields['work_order'].queryset = WorkOrder.objects.select_related('client').order_by('-created_at')
        self.fields['work_order'].label_from_instance = lambda wo: f'{wo.work_order_number} — {wo.client.name}'


_INPUT = 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'
_TEXTAREA = _INPUT
_CHECK = 'h-4 w-4 text-blue-600 border-gray-300 rounded'


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ['first_name', 'last_name', 'email', 'phone', 'title', 'is_primary', 'receives_email', 'notes']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': _INPUT}),
            'last_name': forms.TextInput(attrs={'class': _INPUT}),
            'email': forms.EmailInput(attrs={'class': _INPUT}),
            'phone': forms.TextInput(attrs={'class': _INPUT}),
            'title': forms.TextInput(attrs={'class': _INPUT}),
            'is_primary': forms.CheckboxInput(attrs={'class': _CHECK}),
            'receives_email': forms.CheckboxInput(attrs={'class': _CHECK}),
            'notes': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
        }


class ContactPhoneForm(forms.ModelForm):
    class Meta:
        model = ContactPhone
        fields = ['number', 'phone_type']
        widgets = {
            'number': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '503-555-1234'}),
            'phone_type': forms.Select(attrs={'class': 'px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
        }


# ---------------------------------------------------------------------------
# Site Settings — per-tab forms
# ---------------------------------------------------------------------------

_SS_INPUT = 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm'
_SS_CHECK = 'h-4 w-4 text-blue-600 border-gray-300 rounded'
_SS_SELECT = 'px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm'


class CompanySettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ['company_name', 'company_address', 'company_phone', 'company_email', 'company_logo']
        widgets = {
            'company_name': forms.TextInput(attrs={'class': _SS_INPUT}),
            'company_address': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': '235 Coolidge St., Silverton, OR 97381'}),
            'company_phone': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': '503-555-0100'}),
            'company_email': forms.EmailInput(attrs={'class': _SS_INPUT}),
        }


class OutboundEmailSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            'email_enabled', 'email_host', 'email_port', 'email_use_tls',
            'email_username', 'email_password', 'email_from',
            'email_suppression_patterns',
        ]
        widgets = {
            'email_enabled': forms.CheckboxInput(attrs={'class': _SS_CHECK}),
            'email_host': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': 'mail.yourdomain.com'}),
            'email_port': forms.NumberInput(attrs={'class': _SS_INPUT}),
            'email_use_tls': forms.CheckboxInput(attrs={'class': _SS_CHECK}),
            'email_username': forms.TextInput(attrs={'class': _SS_INPUT}),
            'email_password': forms.PasswordInput(attrs={'class': _SS_INPUT}, render_value=True),
            'email_from': forms.EmailInput(attrs={'class': _SS_INPUT, 'placeholder': 'support@yourdomain.com'}),
            'email_suppression_patterns': forms.Textarea(attrs={'class': _SS_INPUT, 'rows': 5}),
        }


class InboundEmailSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            'inbound_email_enabled', 'inbound_protocol', 'inbound_host', 'inbound_port',
            'inbound_ssl', 'inbound_username', 'inbound_password', 'inbound_folder',
            'inbound_delete_after_fetch', 'strip_quoted_replies',
            'inbound_default_client_name',
        ]
        widgets = {
            'inbound_email_enabled': forms.CheckboxInput(attrs={'class': _SS_CHECK}),
            'inbound_protocol': forms.Select(attrs={'class': _SS_SELECT}),
            'inbound_host': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': 'mail.yourdomain.com'}),
            'inbound_port': forms.NumberInput(attrs={'class': _SS_INPUT}),
            'inbound_ssl': forms.CheckboxInput(attrs={'class': _SS_CHECK}),
            'inbound_username': forms.TextInput(attrs={'class': _SS_INPUT}),
            'inbound_password': forms.PasswordInput(attrs={'class': _SS_INPUT}, render_value=True),
            'inbound_folder': forms.TextInput(attrs={'class': _SS_INPUT}),
            'inbound_delete_after_fetch': forms.CheckboxInput(attrs={'class': _SS_CHECK}),
            'strip_quoted_replies': forms.CheckboxInput(attrs={'class': _SS_CHECK}),
            'inbound_default_client_name': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': 'Walk-in / Unknown'}),
        }


class AttachmentSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            'max_attachment_size_mb', 'blocked_extensions', 'storage_backend',
            'local_storage_path', 's3_bucket_name', 's3_access_key', 's3_secret_key',
            's3_endpoint_url', 's3_region',
        ]
        widgets = {
            'max_attachment_size_mb': forms.NumberInput(attrs={'class': _SS_INPUT}),
            'blocked_extensions': forms.TextInput(attrs={'class': _SS_INPUT}),
            'storage_backend': forms.Select(attrs={'class': _SS_SELECT}),
            'local_storage_path': forms.TextInput(attrs={'class': _SS_INPUT}),
            's3_bucket_name': forms.TextInput(attrs={'class': _SS_INPUT}),
            's3_access_key': forms.TextInput(attrs={'class': _SS_INPUT}),
            's3_secret_key': forms.PasswordInput(attrs={'class': _SS_INPUT}, render_value=True),
            's3_endpoint_url': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': 'https://s3.us-west-001.backblazeb2.com'}),
            's3_region': forms.TextInput(attrs={'class': _SS_INPUT}),
        }


class SecuritySettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ['require_mfa']
        widgets = {
            'require_mfa': forms.CheckboxInput(attrs={'class': _SS_CHECK}),
        }


class MileageSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ['google_maps_api_key', 'shop_address']
        widgets = {
            'google_maps_api_key': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': 'AIza...'}),
            'shop_address': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': '235 Coolidge St. Silverton Oregon 97381'}),
        }
