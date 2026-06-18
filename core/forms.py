from django import forms
from django.core.files.uploadedfile import UploadedFile
from .models import WorkOrder, Client, Contact, ContactPhone, Device, Ticket, RepairType, HelpTopic, SLAPlan, KBCategory, KBArticle, Mileage, SiteSettings


MAX_LOGO_DIMENSION = 2000  # px on either side — generous; we display-fit anything under this


def validate_logo_upload(f):
    """Reject a newly-uploaded logo larger than MAX_LOGO_DIMENSION on either side.
    Only fresh uploads are checked; an existing stored file passes through untouched."""
    if not isinstance(f, UploadedFile):
        return f
    from PIL import Image
    try:
        img = Image.open(f)
        w, h = img.size
        f.seek(0)
    except Exception:
        raise forms.ValidationError('That file does not appear to be a valid image.')
    if w > MAX_LOGO_DIMENSION or h > MAX_LOGO_DIMENSION:
        raise forms.ValidationError(
            f'Image can be no larger than {MAX_LOGO_DIMENSION} × {MAX_LOGO_DIMENSION} px '
            f'(yours is {w} × {h}). Please downsize it and re-upload.'
        )
    return f


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
            'client', 'contact', 'device', 'repair_type', 'assigned_to',
            'service_type', 'status', 'priority', 'scheduled_date',
            'time_spent_minutes', 'invoice_ninja_ref',
            'notes_customer_visible', 'notes_internal',
        ]
        widgets = {
            'client': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'contact': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'device': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'repair_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'assigned_to': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'service_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'status': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'priority': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'scheduled_date': forms.DateInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'type': 'date'}),
            'time_spent_minutes': forms.NumberInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'invoice_ninja_ref': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'placeholder': 'e.g. INV-0042'}),
            'notes_customer_visible': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 4}),
            'notes_internal': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 4}),
        }

    def __init__(self, *args, client_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.filter(is_active=True).order_by('name')
        from .models import User, StatusDefinition
        self.fields['assigned_to'].queryset = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
        self.fields['assigned_to'].required = False
        self.fields['contact'].required = False
        self.fields['device'].required = False
        self.fields['repair_type'].required = False
        self.fields['scheduled_date'].required = False
        self.fields['time_spent_minutes'].required = False
        self.fields['invoice_ninja_ref'].required = False
        # Dynamic status choices from StatusDefinition
        wo_statuses = list(StatusDefinition.objects.filter(
            entity_type='workorder', is_active=True
        ).order_by('sort_order').values_list('slug', 'label'))
        self.fields['status'] = forms.ChoiceField(
            choices=wo_statuses,
            widget=forms.Select(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
        )

        if client_id:
            self.fields['contact'].queryset = Contact.objects.filter(
                client_id=client_id
            ).order_by('last_name', 'first_name')
        elif self.instance and self.instance.pk:
            self.fields['contact'].queryset = Contact.objects.filter(
                client=self.instance.client
            ).order_by('last_name', 'first_name')
        else:
            self.fields['contact'].queryset = Contact.objects.none()


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            'name', 'client_type', 'email', 'phone',
            'address_line1', 'address_line2', 'address_city', 'address_state', 'address_zip',
            'notes', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'email': forms.EmailInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'phone': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}),
            'address_line1': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'placeholder': 'Street address'}),
            'address_line2': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'placeholder': 'Apt, Suite, etc. (optional)'}),
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
            ).order_by('last_name', 'first_name')
        elif self.instance and self.instance.pk:
            self.fields['assigned_contact'].queryset = Contact.objects.filter(
                client=self.instance.client
            ).order_by('last_name', 'first_name')
        else:
            self.fields['assigned_contact'].queryset = Contact.objects.none()


SELECT_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}
TEXT_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'}
TEXTAREA_WIDGET = {'class': 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500', 'rows': 4}


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['client', 'contact', 'device', 'help_topic', 'sla_plan', 'assigned_to', 'subject', 'description', 'source', 'status']
        widgets = {
            'client': forms.Select(attrs=SELECT_WIDGET),
            'contact': forms.Select(attrs=SELECT_WIDGET),
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
        from .models import User as UserModel, StatusDefinition
        self.fields['client'].queryset = Client.objects.filter(is_active=True).order_by('name')
        self.fields['contact'].required = False
        # Use POSTed client_id when present so reassigning a ticket to a different client
        # doesn't fail validation (old instance.client_id would scope contacts to the wrong client)
        data = args[0] if args else kwargs.get('data')
        posted_client_id = data.get('client') if data else None
        instance = kwargs.get('instance')
        effective_client_id = posted_client_id or (instance.client_id if instance else None)
        if effective_client_id:
            self.fields['contact'].queryset = Contact.objects.filter(client_id=effective_client_id, is_active=True).order_by('last_name', 'first_name')
        else:
            self.fields['contact'].queryset = Contact.objects.none()
        self.fields['device'].required = False
        self.fields['help_topic'].queryset = HelpTopic.objects.filter(is_active=True).order_by('sort_order', 'name')
        self.fields['help_topic'].required = False
        self.fields['sla_plan'].queryset = SLAPlan.objects.filter(is_active=True).order_by('name')
        self.fields['sla_plan'].required = False
        self.fields['assigned_to'].queryset = UserModel.objects.filter(is_active=True).order_by('first_name', 'last_name')
        self.fields['assigned_to'].required = False
        # Dynamic status choices from StatusDefinition
        ticket_statuses = list(StatusDefinition.objects.filter(
            entity_type='ticket', is_active=True
        ).order_by('sort_order').values_list('slug', 'label'))
        self.fields['status'] = forms.ChoiceField(
            choices=ticket_statuses,
            widget=forms.Select(attrs=SELECT_WIDGET),
        )

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
        fields = ['company_name', 'company_address_line1', 'company_address_line2', 'company_phone', 'company_email', 'company_logo']
        widgets = {
            'company_name': forms.TextInput(attrs={'class': _SS_INPUT}),
            'company_address_line1': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': '235 Coolidge St.'}),
            'company_address_line2': forms.TextInput(attrs={'class': _SS_INPUT, 'placeholder': 'Silverton, OR 97381'}),
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


_HEX_INPUT = 'w-20 border border-gray-300 rounded px-2 py-1 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-blue-500'


class ColorSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            'site_logo', 'login_logo',
            'color_primary', 'color_nav_text', 'color_accent',
            'color_sidebar_bg', 'color_sidebar_text',
            'color_page_bg', 'color_page_title', 'color_title_bar', 'color_section_header', 'color_section_header_text',
            'color_status_new', 'color_status_assigned', 'color_status_in_progress',
            'color_status_completed', 'color_status_closed', 'color_status_cancelled',
        ]
        _hex_fields = [
            'color_primary', 'color_nav_text', 'color_accent',
            'color_sidebar_bg', 'color_sidebar_text',
            'color_page_bg', 'color_page_title', 'color_title_bar', 'color_section_header', 'color_section_header_text',
            'color_status_new', 'color_status_assigned', 'color_status_in_progress',
            'color_status_completed', 'color_status_closed', 'color_status_cancelled',
        ]
        widgets = {f: forms.TextInput(attrs={'class': _HEX_INPUT, 'maxlength': 7, 'placeholder': '#rrggbb'})
                   for f in _hex_fields}

    def clean_site_logo(self):
        return validate_logo_upload(self.cleaned_data.get('site_logo'))

    def clean_login_logo(self):
        return validate_logo_upload(self.cleaned_data.get('login_logo'))


class EmailBrandingForm(forms.ModelForm):
    """Outgoing-email appearance — independent of the app's look."""
    class Meta:
        model = SiteSettings
        fields = ['email_header_color', 'email_logo']
        widgets = {
            'email_header_color': forms.TextInput(attrs={
                'class': _HEX_INPUT, 'maxlength': 7, 'placeholder': '#rrggbb',
            }),
            'email_logo': forms.ClearableFileInput(attrs={'class': 'text-sm'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email_header_color'].required = False
        self.fields['email_logo'].required = False


# ---------------------------------------------------------------------------
# User management forms
# ---------------------------------------------------------------------------

_INPUT = 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'

class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(label='Password', widget=forms.PasswordInput(attrs={'class': _INPUT}))
    password2 = forms.CharField(label='Confirm password', widget=forms.PasswordInput(attrs={'class': _INPUT}))

    class Meta:
        from .models import User as _User
        model = _User
        fields = ['first_name', 'last_name', 'username', 'email', 'phone', 'level', 'role_obj', 'is_staff', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': _INPUT}),
            'last_name': forms.TextInput(attrs={'class': _INPUT}),
            'username': forms.TextInput(attrs={'class': _INPUT}),
            'email': forms.EmailInput(attrs={'class': _INPUT}),
            'phone': forms.TextInput(attrs={'class': _INPUT}),
            'role_obj': forms.Select(attrs={'class': _INPUT}),
            'level': forms.Select(attrs={'class': _INPUT}),
            'is_staff': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Role
        self.fields['role_obj'].queryset = Role.objects.all().order_by('name')
        self.fields['role_obj'].required = False
        self.fields['role_obj'].label = 'Role'
        self.fields['is_staff'].label = 'Admin (can access Settings)'
        self.fields['phone'].required = False
        self.fields['email'].required = False

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        p2 = cleaned.get('password2')
        if p1 and p1 != p2:
            self.add_error('password2', 'Passwords do not match.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    class Meta:
        from .models import User as _User
        model = _User
        fields = ['first_name', 'last_name', 'username', 'email', 'phone', 'level', 'role_obj', 'is_staff', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': _INPUT}),
            'last_name': forms.TextInput(attrs={'class': _INPUT}),
            'username': forms.TextInput(attrs={'class': _INPUT}),
            'email': forms.EmailInput(attrs={'class': _INPUT}),
            'phone': forms.TextInput(attrs={'class': _INPUT}),
            'role_obj': forms.Select(attrs={'class': _INPUT}),
            'level': forms.Select(attrs={'class': _INPUT}),
            'is_staff': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Role
        self.fields['role_obj'].queryset = Role.objects.all().order_by('name')
        self.fields['role_obj'].required = False
        self.fields['role_obj'].label = 'Role'
        self.fields['is_staff'].label = 'Admin (can access Settings)'
        self.fields['phone'].required = False
        self.fields['email'].required = False


class UserSetPasswordForm(forms.Form):
    password1 = forms.CharField(label='New password', widget=forms.PasswordInput(attrs={'class': _INPUT}))
    password2 = forms.CharField(label='Confirm password', widget=forms.PasswordInput(attrs={'class': _INPUT}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password1') != cleaned.get('password2'):
            self.add_error('password2', 'Passwords do not match.')
        return cleaned


# ---------------------------------------------------------------------------
# Role management form
# ---------------------------------------------------------------------------

class RoleForm(forms.ModelForm):
    class Meta:
        from .models import Role
        model = Role
        fields = [
            'name', 'description',
            'can_manage_settings', 'can_view_all_tickets', 'can_close_tickets',
            'can_manage_users', 'can_view_reports', 'can_view_restricted_kb',
            'can_manage_kb', 'can_create_ticket', 'can_edit_ticket',
            'can_delete_ticket', 'can_assign_ticket', 'can_reply_internal',
            'can_reply_customer', 'can_view_device_credentials',
            'can_create_workorder', 'can_edit_workorder', 'can_close_workorder',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': _INPUT}),
            'description': forms.TextInput(attrs={'class': _INPUT, 'placeholder': 'Optional description'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _cb = {'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'}
        for fname, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update(_cb)
        self.fields['description'].required = False
