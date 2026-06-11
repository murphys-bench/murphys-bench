from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.urls import reverse_lazy
from django.db.models import Q, F as models_F, Max as models_Max, Count
from django.contrib.contenttypes.models import ContentType
from django.http import FileResponse, Http404
from django.utils import timezone
from .models import (
    WorkOrder, WorkOrderNote, WorkOrderItem, Client, Device, Mileage, Checklist, ChecklistItem,
    Ticket, TicketReply, TicketLock, TicketLink, Attachment, SiteSettings,
    KBCategory, KBArticle, TicketQueue, DashboardTile, User,
    CustomField, CustomFieldChoice, CustomFieldValue,
    QuickLaborItem, WorkPerformed, ContactPhone,
    Contact, RepairType, RepairTypeCategory,
    CannedResponseCategory, CannedResponse, Invoice,
    OrgCredential, CredentialAccessLog,
    DeviceCredentialAccessLog,
    EmailTemplate,
    StatusDefinition,
    SuppressedAddress,
    BlockedSender,
    SLAPlan, HelpTopic, TechSkill,
)
from .forms import (WorkOrderForm, ClientForm, ContactForm, ContactPhoneForm, DeviceForm,
                    TicketForm, TicketConvertForm, KBArticleForm, TicketQueueForm, MileageForm,
                    CompanySettingsForm, OutboundEmailSettingsForm, InboundEmailSettingsForm,
                    AttachmentSettingsForm, SecuritySettingsForm, MileageSettingsForm,
                    ColorSettingsForm)


def _audit_entries(obj):
    """Return audit log entries with changes pre-converted to a list of (field, old, new) tuples.

    Avoids Django template dict-key-vs-method collision when changes_dict contains
    keys like 'items' that shadow dict.items().
    """
    from auditlog.models import LogEntry
    entries = LogEntry.objects.get_for_object(obj).select_related('actor')[:50]
    result = []
    for entry in entries:
        changes = []
        for field, values in (entry.changes or {}).items():
            old_val = values[0] if len(values) > 0 else ''
            new_val = values[1] if len(values) > 1 else ''
            changes.append({'field': field, 'old': old_val, 'new': new_val})
        result.append({'entry': entry, 'changes': changes})
    return result


def _save_attachments(request, obj):
    """Validate and save uploaded files as Attachments linked to obj."""
    files = request.FILES.getlist('attachments')
    if not files:
        return []
    site = SiteSettings.get()
    max_bytes = site.max_attachment_size_mb * 1024 * 1024
    blocked = site.get_blocked_extensions()
    ct = ContentType.objects.get_for_model(obj)
    saved = []
    for f in files:
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext in blocked:
            continue
        if f.size > max_bytes:
            continue
        att = Attachment.objects.create(
            content_type=ct,
            object_id=obj.pk,
            file=f,
            original_filename=f.name,
            mime_type=f.content_type or '',
            size_bytes=f.size,
            uploaded_by=request.user,
        )
        saved.append(att)
    return saved


def _is_admin(user):
    return user.is_staff or user.has_perm_flag('can_manage_settings')


def _get_custom_fields_for_ticket(ticket_or_none):
    """Return active CustomFields applicable to a ticket (and its help topic if set)."""
    help_topic = getattr(ticket_or_none, 'help_topic', None) if ticket_or_none else None
    from django.db.models import Q as DQ
    qs = CustomField.objects.filter(is_active=True).filter(
        DQ(applies_to='ticket') | DQ(applies_to='both')
    ).filter(
        DQ(scoped_to_help_topic__isnull=True) |
        DQ(scoped_to_help_topic=help_topic) if help_topic else DQ(scoped_to_help_topic__isnull=True)
    ).prefetch_related('choices').order_by('sort_order', 'label')
    return list(qs)


def _get_custom_fields_for_workorder(wo_or_none):
    """Return active CustomFields applicable to a work order (and its repair type if set)."""
    repair_type = getattr(wo_or_none, 'repair_type', None) if wo_or_none else None
    from django.db.models import Q as DQ
    qs = CustomField.objects.filter(is_active=True).filter(
        DQ(applies_to='workorder') | DQ(applies_to='both')
    ).filter(
        DQ(scoped_to_repair_type__isnull=True) |
        DQ(scoped_to_repair_type=repair_type) if repair_type else DQ(scoped_to_repair_type__isnull=True)
    ).prefetch_related('choices').order_by('sort_order', 'label')
    return list(qs)


def _get_custom_field_values(obj):
    """Return dict of {field_id: value_str} for an existing object."""
    ct = ContentType.objects.get_for_model(obj)
    values = CustomFieldValue.objects.filter(content_type=ct, object_id=obj.pk).select_related('field')
    return {v.field_id: v.value for v in values}


def _save_custom_field_values(request, obj, fields):
    """Save POSTed custom field values for obj. fields = list of CustomField instances."""
    if not fields:
        return
    ct = ContentType.objects.get_for_model(obj)
    for field in fields:
        key = f'cf_{field.pk}'
        if field.field_type == 'checkbox':
            value = '1' if request.POST.get(key) else '0'
        else:
            value = request.POST.get(key, '').strip()
        CustomFieldValue.objects.update_or_create(
            content_type=ct,
            object_id=obj.pk,
            field=field,
            defaults={'value': value},
        )


def _custom_fields_with_values(fields, obj):
    """Return list of {field, value} dicts for display on detail views."""
    values = _get_custom_field_values(obj)
    result = []
    for field in fields:
        value = values.get(field.pk, '')
        if value or field.is_required:
            result.append({'field': field, 'value': value})
    return result


_TILE_COLOR_CLASSES = {
    'blue':   {'card': 'bg-blue-50 border-l-4 border-blue-400',   'num': 'text-blue-700',   'icon': 'text-blue-400'},
    'yellow': {'card': 'bg-yellow-50 border-l-4 border-yellow-400', 'num': 'text-yellow-700', 'icon': 'text-yellow-400'},
    'red':    {'card': 'bg-red-50 border-l-4 border-red-500',     'num': 'text-red-700',    'icon': 'text-red-400'},
    'green':  {'card': 'bg-green-50 border-l-4 border-green-400', 'num': 'text-green-700',  'icon': 'text-green-500'},
    'gray':   {'card': 'bg-gray-50 border-l-4 border-gray-400',   'num': 'text-gray-600',   'icon': 'text-gray-400'},
}


def _tile_color(tile):
    if 'overdue=1' in tile.link_url or '/overdue' in tile.link_url:
        return 'red'
    statuses = set(tile.status_filter or [])
    active = {'open', 'in_progress', 'assigned'}
    waiting = {'waiting_on_customer', 'waiting_on_parts', 'waiting'}
    completed = {'completed', 'closed', 'resolved'}
    if statuses & completed and not (statuses & (active | waiting)):
        return 'green'
    if statuses & waiting and not (statuses & active):
        return 'yellow'
    if statuses == {'new'}:
        return 'gray'
    return 'blue'


def _tile_count(tile, user, is_admin):
    """Return the count for a DashboardTile."""
    statuses = tile.status_filter or []
    if tile.row == 'ticket':
        qs = Ticket.objects.all()
        if not is_admin:
            qs = qs.filter(assigned_to=user)
        if statuses:
            qs = qs.filter(status__in=statuses)
        else:
            qs = qs.exclude(status__in=TICKET_CLOSED_STATUSES)
        if '/overdue' in tile.link_url or 'overdue=1' in tile.link_url:
            from django.utils import timezone as tz
            qs = qs.filter(due_at__lt=tz.now()).exclude(status__in=['closed', 'resolved'])
    else:
        qs = WorkOrder.objects.all()
        if not is_admin:
            qs = qs.filter(assigned_to=user)
        if statuses:
            qs = qs.filter(status__in=statuses)
        else:
            qs = qs.exclude(status__in=WO_CLOSED_STATUSES)
    return qs.count()


class DashboardView(LoginRequiredMixin, View):
    template_name = 'core/dashboard.html'

    def get(self, request):
        is_admin = _is_admin(request.user)

        tiles_qs = DashboardTile.objects.filter(is_active=True)
        ticket_tiles = []
        wo_tiles = []
        for tile in tiles_qs:
            if tile.visible_to == 'admin' and not is_admin:
                continue
            if tile.visible_to == 'tech' and is_admin:
                continue
            entry = {
                'tile': tile,
                'count': _tile_count(tile, request.user, is_admin),
                'colors': _TILE_COLOR_CLASSES[_tile_color(tile)],
            }
            if tile.row == 'ticket':
                ticket_tiles.append(entry)
            else:
                wo_tiles.append(entry)

        # Recent open work orders (tech sees own, admin sees all)
        wo_qs = WorkOrder.objects.select_related('client', 'assigned_to', 'device').exclude(status__in=WO_CLOSED_STATUSES)
        if not is_admin:
            wo_qs = wo_qs.filter(assigned_to=request.user)
        open_work_orders = wo_qs.order_by('-created_at')[:10]

        recently_closed = WorkOrder.objects.select_related('client', 'assigned_to').filter(
            status__in=['closed', 'cancelled']
        ).order_by('-updated_at')[:5]

        # Team workload (admin only)
        team_workload = []
        if is_admin:
            from django.db.models import Count
            techs = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
            open_wo_statuses = ['new', 'in_progress', 'waiting_on_customer', 'on_hold']
            open_ticket_statuses = ['new', 'open', 'in_progress', 'waiting_on_customer', 'converted']
            for tech in techs:
                open_wos = WorkOrder.objects.filter(assigned_to=tech, status__in=open_wo_statuses).count()
                open_tickets = Ticket.objects.filter(assigned_to=tech, status__in=open_ticket_statuses).count()
                if open_wos or open_tickets:
                    team_workload.append({
                        'tech': tech,
                        'open_wos': open_wos,
                        'open_tickets': open_tickets,
                        'total': open_wos + open_tickets,
                    })
            team_workload.sort(key=lambda x: -x['total'])

        ticket_qs = Ticket.objects.select_related('client', 'assigned_to').exclude(status__in=TICKET_CLOSED_STATUSES)
        if not is_admin:
            ticket_qs = ticket_qs.filter(assigned_to=request.user)
        open_tickets = ticket_qs.order_by('-created_at')[:10]

        needs_response_qs = Ticket.objects.filter(needs_response=True)
        if not is_admin:
            needs_response_qs = needs_response_qs.filter(assigned_to=request.user)
        needs_response_count = needs_response_qs.count()

        context = {
            'ticket_tiles': ticket_tiles,
            'wo_tiles': wo_tiles,
            'open_work_orders': open_work_orders,
            'recently_closed': recently_closed,
            'active_clients': Client.objects.filter(is_active=True).count(),
            'total_devices': Device.objects.filter(is_active=True).count(),
            'is_admin': is_admin,
            'team_workload': team_workload,
            'needs_response_count': needs_response_count,
            'open_tickets': open_tickets,
        }
        return render(request, self.template_name, context)


WO_CLOSED_STATUSES = ['completed', 'cancelled']


class WorkOrderListView(LoginRequiredMixin, ListView):
    """Display list of all work orders with filtering and search"""
    model = WorkOrder
    template_name = 'core/work_order_list.html'
    context_object_name = 'work_orders'
    paginate_by = 25

    def get_queryset(self):
        queryset = WorkOrder.objects.select_related('client', 'assigned_to', 'device')

        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        else:
            tab = self.request.GET.get('tab', 'active')
            if tab == 'closed':
                queryset = queryset.filter(status__in=WO_CLOSED_STATUSES)
            else:
                queryset = queryset.exclude(status__in=WO_CLOSED_STATUSES)

        assigned_to = self.request.GET.get('assigned_to')
        if assigned_to == 'me' and not _is_admin(self.request.user):
            queryset = queryset.filter(assigned_to=self.request.user)
        elif assigned_to and assigned_to != 'me':
            queryset = queryset.filter(assigned_to_id=assigned_to)

        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(work_order_number__icontains=search) |
                Q(client__name__icontains=search)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = list(
            StatusDefinition.objects.filter(entity_type='workorder', is_active=True)
            .order_by('sort_order').values_list('slug', 'label')
        )
        context['priority_choices'] = WorkOrder.PRIORITY_CHOICES
        base_qs = WorkOrder.objects.all()
        context['active_count'] = base_qs.exclude(status__in=WO_CLOSED_STATUSES).count()
        context['closed_count'] = base_qs.filter(status__in=WO_CLOSED_STATUSES).count()
        context['current_tab'] = self.request.GET.get('tab', 'active')
        return context


class WorkOrderDetailView(LoginRequiredMixin, DetailView):
    """Display full details of a single work order"""
    model = WorkOrder
    template_name = 'core/work_order_detail.html'
    context_object_name = 'work_order'

    def get_queryset(self):
        return WorkOrder.objects.select_related(
            'client', 'assigned_to', 'device', 'device__assigned_contact',
            'repair_type', 'ticket', 'contact'
        ).prefetch_related('notes', 'items', 'notes__created_by')

    def get_context_data(self, **kwargs):
        from django.utils import timezone
        context = super().get_context_data(**kwargs)
        wo = self.object
        # Days open
        end = wo.completed_date.date() if wo.completed_date else timezone.now().date()
        context['days_open'] = (end - wo.created_at.date()).days
        context['audit_log'] = _audit_entries(self.object)
        ct = ContentType.objects.get_for_model(WorkOrder)
        context['wo_attachments'] = Attachment.objects.filter(content_type=ct, object_id=self.object.pk)
        # Linked ticket for overdue badge
        context['linked_ticket'] = getattr(self.object, 'ticket', None)
        # Custom fields
        fields = _get_custom_fields_for_workorder(self.object)
        context['custom_field_values'] = _custom_fields_with_values(fields, self.object)
        # Quick Labor buttons grouped by category
        labor_items = QuickLaborItem.objects.filter(is_active=True).order_by('category', 'sort_order', 'label')
        labor_by_category = {}
        for item in labor_items:
            labor_by_category.setdefault(item.category, []).append(item)
        context['labor_by_category'] = labor_by_category
        context['wp_entries'] = _wp_entries_for(self.object)
        # Inline update form data
        context['all_users'] = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
        context['all_repair_types'] = RepairType.objects.filter(is_active=True).order_by('name')
        context['client_contacts'] = Contact.objects.filter(client=wo.client).order_by('last_name', 'first_name')
        context['client_devices'] = Device.objects.filter(client=wo.client).order_by('name')
        # Open tickets for this client (cross-visibility panel)
        linked_ticket_pk = getattr(self.object.ticket, 'pk', None) if hasattr(self.object, 'ticket') else None
        open_tickets = (
            Ticket.objects
            .filter(client=wo.client)
            .exclude(status__in=('resolved', 'closed'))
            .select_related('created_by')
            .prefetch_related('replies')
            .order_by('-created_at')
        )
        if linked_ticket_pk:
            open_tickets = open_tickets.exclude(pk=linked_ticket_pk)
        context['client_open_tickets'] = open_tickets
        checklist_items = list(self.object.items.filter(item_type='checklist').order_by('created_at'))
        context['checklist_items'] = checklist_items
        context['checklist_checked_count'] = sum(1 for i in checklist_items if i.pre_check or i.post_check)
        # Billing
        invoice, _ = Invoice.objects.get_or_create(work_order=self.object)
        context['invoice'] = invoice
        # Dynamic status choices for inline status dropdown
        context['wo_status_choices'] = list(
            StatusDefinition.objects.filter(entity_type='workorder', is_active=True)
            .order_by('sort_order').values_list('slug', 'label')
        )
        return context


class WorkOrderAddTimeView(LoginRequiredMixin, View):
    """Add minutes to a work order's time_spent_minutes. Returns updated display fragment."""

    def post(self, request, pk):
        wo = get_object_or_404(WorkOrder, pk=pk)
        try:
            minutes = max(0, int(request.POST.get('minutes', 0)))
        except (ValueError, TypeError):
            minutes = 0
        if minutes > 0:
            WorkOrder.objects.filter(pk=pk).update(
                time_spent_minutes=models_F('time_spent_minutes') + minutes
            )
            wo.refresh_from_db(fields=['time_spent_minutes'])
        return render(request, 'core/partials/wo_time_spent.html', {'work_order': wo})


class WorkOrderQuickUpdateView(LoginRequiredMixin, View):
    """Inline update of key WO fields from the detail page sidebar panel."""

    def post(self, request, pk):
        wo = get_object_or_404(WorkOrder, pk=pk)
        p = request.POST

        wo.status       = p.get('status', wo.status)
        wo.priority     = p.get('priority', wo.priority)
        wo.service_type = p.get('service_type', wo.service_type)

        assigned_to_id = p.get('assigned_to')
        wo.assigned_to_id = assigned_to_id if assigned_to_id else None

        contact_id = p.get('contact')
        wo.contact_id = contact_id if contact_id else None

        device_id = p.get('device')
        wo.device_id = device_id if device_id else None

        repair_type_id = p.get('repair_type')
        if repair_type_id == 'custom':
            custom_name = p.get('custom_repair_type', '').strip()
            if custom_name:
                rt, _ = RepairType.objects.get_or_create(name=custom_name, defaults={'is_active': True})
                wo.repair_type_id = rt.pk
        else:
            wo.repair_type_id = repair_type_id if repair_type_id else None

        scheduled_date = p.get('scheduled_date')
        wo.scheduled_date = scheduled_date if scheduled_date else None

        wo.invoice_ninja_ref = p.get('invoice_ninja_ref', wo.invoice_ninja_ref)

        # Auto-set completed_date when status flips to completed
        if wo.status == 'completed' and not wo.completed_date:
            from django.utils import timezone as tz
            wo.completed_date = tz.now()
        elif wo.status not in ('completed', 'cancelled') and wo.completed_date:
            wo.completed_date = None

        wo.save()
        _flag_ticket_wo_complete(wo)
        return redirect('core:work_order_detail', pk=pk)


def _flag_ticket_wo_complete(wo):
    """Arm wo_complete on linked ticket when WO is completed/closed; clear it if WO re-opened."""
    try:
        ticket = wo.ticket
    except Exception:
        return
    if not ticket:
        return
    if wo.status in WO_CLOSED_STATUSES:
        if not ticket.wo_complete:
            ticket.wo_complete = True
            ticket.save(update_fields=['wo_complete', 'updated_at'])
    else:
        if ticket.wo_complete:
            ticket.wo_complete = False
            ticket.save(update_fields=['wo_complete', 'updated_at'])


class WorkOrderClaimView(LoginRequiredMixin, View):
    """Self-assign a work order to the current user."""

    def post(self, request, pk):
        wo = get_object_or_404(WorkOrder, pk=pk)
        wo.assigned_to = request.user
        wo.save(update_fields=['assigned_to', 'updated_at'])
        return redirect('core:work_order_detail', pk=pk)


class WorkOrderAttachmentUploadView(LoginRequiredMixin, View):
    """Upload files directly to a work order (not tied to a note)."""

    def post(self, request, pk):
        wo = get_object_or_404(WorkOrder, pk=pk)
        _save_attachments(request, wo)
        return redirect('core:work_order_detail', pk=pk)


class WorkOrderApplyChecklistView(LoginRequiredMixin, View):
    """Re-apply checklist items from the flat bank to an existing WO."""

    def post(self, request, pk):
        wo = get_object_or_404(WorkOrder, pk=pk)
        _apply_checklist_items(wo)
        return redirect('core:work_order_detail', pk=pk)


class ClientListView(LoginRequiredMixin, ListView):
    """Display list of all clients"""
    model = Client
    template_name = 'core/client_list.html'
    context_object_name = 'clients'
    paginate_by = 25

    def get_queryset(self):
        queryset = Client.objects.annotate(
            device_count=Count('devices', distinct=True),
            wo_count=Count('work_orders', distinct=True),
        )

        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search)
            )

        client_type = self.request.GET.get('type')
        if client_type in ('residential', 'business'):
            queryset = queryset.filter(client_type=client_type)

        if not self.request.GET.get('show_inactive'):
            queryset = queryset.filter(is_active=True)

        return queryset.order_by('name')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['show_inactive'] = bool(self.request.GET.get('show_inactive'))
        ctx['active_type'] = self.request.GET.get('type', '')
        return ctx


class ClientDetailView(LoginRequiredMixin, DetailView):
    """Display full details of a single client"""
    model = Client
    template_name = 'core/client_detail.html'
    context_object_name = 'client'

    def get_queryset(self):
        return Client.objects.prefetch_related(
            'contacts', 'devices', 'work_orders', 'work_orders__assigned_to',
            'work_orders__invoice',
        )

    def get_context_data(self, **kwargs):
        from django.db.models import Sum
        context = super().get_context_data(**kwargs)
        outstanding = Invoice.objects.filter(
            work_order__client=self.object,
            billing_status__in=['uninvoiced', 'invoiced'],
            amount__isnull=False,
        ).aggregate(total=Sum('amount'))['total']
        context['outstanding_balance'] = outstanding
        return context


class DeviceListView(LoginRequiredMixin, ListView):
    """Display list of all devices"""
    model = Device
    template_name = 'core/device_list.html'
    context_object_name = 'devices'
    paginate_by = 25

    def get_queryset(self):
        queryset = Device.objects.select_related('client', 'repair_type')

        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(serial_number__icontains=search) |
                Q(model__icontains=search) |
                Q(client__name__icontains=search)
            )

        device_type = self.request.GET.get('device_type')
        if device_type:
            queryset = queryset.filter(device_type=device_type)

        return queryset.order_by('client__name', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['device_type_choices'] = Device.DEVICE_TYPE_CHOICES
        return context


class DeviceDetailView(LoginRequiredMixin, DetailView):
    """Display full details of a single device"""
    model = Device
    template_name = 'core/device_detail.html'
    context_object_name = 'device'

    def get_queryset(self):
        return Device.objects.select_related(
            'client', 'repair_type'
        ).prefetch_related(
            'work_orders', 'work_orders__assigned_to'
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['is_admin'] = _is_admin(self.request.user)
        ctx['can_view'] = _can_view_device_creds(self.request.user)
        device = self.object
        ctx['has_creds'] = bool(device.device_username or device.device_password or device.credential_notes)
        return ctx


class MileageDistanceView(LoginRequiredMixin, View):
    """Server-side proxy to Google Distance Matrix API — keeps API key out of browser."""

    def post(self, request):
        import json, urllib.request, urllib.parse
        try:
            data = json.loads(request.body)
            origin = data.get('origin', '').strip()
            destination = data.get('destination', '').strip()
        except (json.JSONDecodeError, AttributeError):
            return JsonResponse({'error': 'Invalid request'}, status=400)

        api_key = SiteSettings.get().google_maps_api_key
        if not api_key:
            return JsonResponse({'error': 'Google Maps API key not configured in Site Settings.'}, status=400)
        if not origin or not destination:
            return JsonResponse({'error': 'Origin and destination are required.'}, status=400)

        url = 'https://maps.googleapis.com/maps/api/distancematrix/json?' + urllib.parse.urlencode({
            'origins': origin,
            'destinations': destination,
            'units': 'imperial',
            'key': api_key,
        })
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            return JsonResponse({'error': f'Distance Matrix request failed: {e}'}, status=502)

        try:
            element = result['rows'][0]['elements'][0]
            if element['status'] != 'OK':
                return JsonResponse({'error': f"Google returned: {element['status']}"}, status=400)
            meters = element['distance']['value']
            miles_one_way = round(meters / 1609.344, 1)
            miles_round_trip = round(miles_one_way * 2, 1)
            return JsonResponse({'one_way': miles_one_way, 'round_trip': miles_round_trip})
        except (KeyError, IndexError):
            return JsonResponse({'error': 'Could not parse Distance Matrix response.'}, status=502)


class MileageCreateView(LoginRequiredMixin, View):
    def get(self, request):
        form = MileageForm(initial={'trip_date': timezone.now().date()})
        return render(request, 'core/mileage_form.html', {'form': form})

    def post(self, request):
        form = MileageForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.technician = request.user
            entry.save()
            return redirect('core:mileage_list')
        return render(request, 'core/mileage_form.html', {'form': form})


class MileageUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk):
        entry = get_object_or_404(Mileage, pk=pk)
        if not _is_admin(request.user) and entry.technician != request.user:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = MileageForm(instance=entry)
        return render(request, 'core/mileage_form.html', {'form': form, 'entry': entry})

    def post(self, request, pk):
        entry = get_object_or_404(Mileage, pk=pk)
        if not _is_admin(request.user) and entry.technician != request.user:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = MileageForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            return redirect('core:mileage_list')
        return render(request, 'core/mileage_form.html', {'form': form, 'entry': entry})


class WorkOrderMileageCreateView(LoginRequiredMixin, View):
    """Mileage entry form launched from a Work Order detail page."""

    def _context(self, work_order, form):
        settings = SiteSettings.get()
        client = work_order.client
        # Build client full address for destination pre-fill
        parts = [
            client.address_line1, client.address_city,
            client.address_state, client.address_zip,
        ]
        client_address = ', '.join(p for p in parts if p)
        return {
            'form': form,
            'work_order': work_order,
            'shop_address': settings.shop_address,
            'client_address': client_address,
            'has_maps_key': bool(settings.google_maps_api_key),
        }

    def get(self, request, pk):
        work_order = get_object_or_404(WorkOrder, pk=pk)
        settings = SiteSettings.get()
        client = work_order.client
        parts = [client.address_line1, client.address_city, client.address_state, client.address_zip]
        client_address = ', '.join(p for p in parts if p)
        form = MileageForm(initial={
            'trip_date': timezone.now().date(),
            'from_location': settings.shop_address,
            'to_location': client_address,
            'purpose': 'Onsite service call',
            'work_order': work_order,
            'trip_type': 'round_trip',
        })
        return render(request, 'core/mileage_wo_form.html', self._context(work_order, form))

    def post(self, request, pk):
        work_order = get_object_or_404(WorkOrder, pk=pk)
        form = MileageForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.technician = request.user
            entry.save()
            return redirect('core:work_order_detail', pk=work_order.pk)
        return render(request, 'core/mileage_wo_form.html', self._context(work_order, form))


class MileageListView(LoginRequiredMixin, ListView):
    """Display mileage log with totals"""
    model = Mileage
    template_name = 'core/mileage_list.html'
    context_object_name = 'entries'
    paginate_by = 50

    def get_queryset(self):
        queryset = Mileage.objects.select_related('technician', 'work_order')

        # Filter by technician
        technician = self.request.GET.get('technician')
        if technician:
            queryset = queryset.filter(technician_id=technician)

        # Filter by month
        month = self.request.GET.get('month')
        if month:
            try:
                year, mo = month.split('-')
                queryset = queryset.filter(trip_date__year=year, trip_date__month=mo)
            except ValueError:
                pass

        return queryset.order_by('-trip_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Total miles for current filtered queryset
        from django.db.models import Sum
        total = self.get_queryset().aggregate(total=Sum('miles'))['total'] or 0
        context['total_miles'] = total
        return context


# --- Work Order Note (HTMX) ---

class WorkOrderNoteCreateView(LoginRequiredMixin, View):
    """Add a note to a work order — returns HTML fragment for HTMX"""

    def post(self, request, pk):
        work_order = get_object_or_404(WorkOrder, pk=pk)
        note_type = request.POST.get('note_type', 'internal')
        content = request.POST.get('content', '').strip()

        if not content:
            return HttpResponse(status=204)  # Nothing to add

        note = WorkOrderNote.objects.create(
            work_order=work_order,
            note_type=note_type,
            content=content,
            created_by=request.user,
        )
        _save_attachments(request, note)
        return render(request, 'core/partials/note_item.html', {'note': note})


# --- Work Order Item Toggle (HTMX) ---

class WorkOrderItemCheckView(LoginRequiredMixin, View):
    """HTMX: update pre_check or post_check on a checklist item, return full checklist."""

    def post(self, request, pk):
        item = get_object_or_404(WorkOrderItem, pk=pk)
        field = request.POST.get('field')
        value = request.POST.get('value', '')
        if field in ('pre_check', 'post_check'):
            setattr(item, field, value)
            # Mark completed when post_check is set to pass or fail
            if field == 'post_check':
                item.is_completed = value in ('pass', 'fail', 'na')
            item.save()
        items = item.work_order.items.filter(item_type='checklist').order_by('created_at')
        checked = sum(1 for i in items if i.pre_check or i.post_check)
        return render(request, 'core/partials/checklist_list.html', {
            'items': items,
            'checked_count': checked,
            'work_order': item.work_order,
        })


def _apply_checklist_items(work_order):
    """Populate WorkOrderItems from the flat ChecklistItem bank for the WO's device type."""
    device_type = None
    if work_order.device_id:
        try:
            device_type = Device.objects.values_list('device_type', flat=True).get(pk=work_order.device_id)
        except Device.DoesNotExist:
            pass
    items = ChecklistItem.objects.filter(is_active=True).order_by('sort_order', 'name')
    for item in items:
        if item.applies_to(device_type):
            WorkOrderItem.objects.get_or_create(
                work_order=work_order,
                item_type='checklist',
                description=item.name,
                defaults={'quantity': 1, 'is_completed': False},
            )


# --- Work Order Create / Edit ---

class WorkOrderCreateView(LoginRequiredMixin, CreateView):
    model = WorkOrder
    form_class = WorkOrderForm
    template_name = 'core/work_order_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        client_id = self.request.GET.get('client') or self.request.POST.get('client')
        if not client_id and self.request.GET.get('device'):
            try:
                client_id = Device.objects.values_list('client_id', flat=True).get(pk=self.request.GET['device'])
            except Device.DoesNotExist:
                pass
        if client_id:
            kwargs['client_id'] = client_id
        return kwargs

    def form_valid(self, form):
        form.instance.work_order_number = WorkOrder.generate_work_order_number()
        response = super().form_valid(form)
        _save_attachments(self.request, self.object)

        # Optionally apply checklist items from the flat bank filtered by device type
        if form.cleaned_data.get('apply_checklist'):
            _apply_checklist_items(self.object)

        fields = _get_custom_fields_for_workorder(self.object)
        _save_custom_field_values(self.request, self.object, fields)
        return response

    def get_success_url(self):
        return reverse_lazy('core:work_order_detail', kwargs={'pk': self.object.pk})

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('client'):
            initial['client'] = self.request.GET['client']
        if self.request.GET.get('contact'):
            initial['contact'] = self.request.GET['contact']
        if self.request.GET.get('device'):
            initial['device'] = self.request.GET['device']
            try:
                device = Device.objects.select_related('assigned_contact').get(pk=self.request.GET['device'])
                if device.assigned_contact_id and not initial.get('contact'):
                    initial['contact'] = device.assigned_contact_id
                if not initial.get('client'):
                    initial['client'] = device.client_id
            except Device.DoesNotExist:
                pass
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'New Work Order'
        context['cancel_url'] = reverse_lazy('core:work_order_list')
        context['is_create'] = True
        fields = _get_custom_fields_for_workorder(None)
        context['custom_field_entries'] = [{'field': f, 'value': ''} for f in fields]
        return context


class WorkOrderUpdateView(LoginRequiredMixin, UpdateView):
    model = WorkOrder
    form_class = WorkOrderForm
    template_name = 'core/work_order_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['client_id'] = self.object.client_id
        return kwargs

    def form_valid(self, form):
        old_status = WorkOrder.objects.get(pk=self.object.pk).status
        response = super().form_valid(form)
        _flag_ticket_wo_complete(self.object)

        # Optionally append checklist items from the flat bank filtered by device type
        if form.cleaned_data.get('apply_checklist'):
            _apply_checklist_items(self.object)

        fields = _get_custom_fields_for_workorder(self.object)
        _save_custom_field_values(self.request, self.object, fields)
        return response

    def get_success_url(self):
        return reverse_lazy('core:work_order_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.work_order_number}'
        context['cancel_url'] = reverse_lazy('core:work_order_detail', kwargs={'pk': self.object.pk})
        context['is_create'] = False
        fields = _get_custom_fields_for_workorder(self.object)
        values = _get_custom_field_values(self.object)
        context['custom_field_entries'] = [{'field': f, 'value': values.get(f.pk, '')} for f in fields]
        return context


# --- Client Create / Edit ---

class ClientCreateView(LoginRequiredMixin, CreateView):
    model = Client
    form_class = ClientForm
    template_name = 'core/client_form.html'

    def get_success_url(self):
        return reverse_lazy('core:client_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'New Client'
        context['cancel_url'] = reverse_lazy('core:client_list')
        return context


class ClientUpdateView(LoginRequiredMixin, UpdateView):
    model = Client
    form_class = ClientForm
    template_name = 'core/client_form.html'

    def get_success_url(self):
        return reverse_lazy('core:client_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.name}'
        context['cancel_url'] = reverse_lazy('core:client_detail', kwargs={'pk': self.object.pk})
        context['client'] = self.object
        context['wo_count'] = self.object.work_orders.count()
        return context


class ClientDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        client = get_object_or_404(Client, pk=pk)
        wo_count = client.work_orders.count()
        if wo_count > 0:
            messages.error(
                request,
                f'Cannot delete {client.name} — this client has {wo_count} work order'
                f'{"s" if wo_count != 1 else ""}. Deactivate the client instead.'
            )
            return redirect('core:client_edit', pk=pk)
        confirm_name = request.POST.get('confirm_name', '').strip()
        if confirm_name != client.name:
            messages.error(request, 'Name did not match. Client was not deleted.')
            return redirect('core:client_edit', pk=pk)
        client.delete()
        messages.success(request, f'{client.name} has been permanently deleted.')
        return redirect('core:client_list')


class InvoiceExportView(LoginRequiredMixin, View):
    """Export invoice records for a client as CSV."""

    def get(self, request, pk):
        import csv
        client = get_object_or_404(Client, pk=pk)
        status_filter = request.GET.get('status', '')

        qs = Invoice.objects.filter(
            work_order__client=client
        ).select_related(
            'work_order', 'work_order__device', 'work_order__assigned_to'
        ).order_by('work_order__created_at')

        if status_filter:
            qs = qs.filter(billing_status=status_filter)

        safe_name = client.name.replace(' ', '_').replace('/', '_')
        filename = f'invoices_{safe_name}.csv'
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([
            'WO #', 'Client', 'Device', 'Assigned To',
            'Billing Status', 'Amount', 'Invoiced Date', 'Paid Date',
            'Payment Method', 'WO Created', 'WO Closed', 'Notes',
        ])

        for inv in qs:
            wo = inv.work_order
            writer.writerow([
                wo.work_order_number,
                client.name,
                str(wo.device) if wo.device else '',
                wo.assigned_to.get_full_name() if wo.assigned_to else '',
                inv.get_billing_status_display(),
                inv.amount if inv.amount is not None else '',
                inv.invoiced_date or '',
                inv.paid_date or '',
                inv.get_payment_method_display() if inv.payment_method else '',
                wo.created_at.date() if wo.created_at else '',
                wo.completed_date.date() if wo.completed_date else '',
                inv.notes,
            ])

        return response


# --- Device Create / Edit ---

class DeviceCreateView(LoginRequiredMixin, CreateView):
    model = Device
    form_class = DeviceForm
    template_name = 'core/device_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        client_id = self.request.GET.get('client') or self.request.POST.get('client')
        if client_id:
            kwargs['client_id'] = client_id
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('client'):
            initial['client'] = self.request.GET['client']
        return initial

    def form_valid(self, form):
        self.object = form.save()
        if self.request.POST.get('save_and_create_wo'):
            return redirect(
                reverse_lazy('core:workorder_create') + f'?device={self.object.pk}'
            )
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return redirect(next_url)
        return redirect(reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk}))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'New Device'
        next_url = self.request.GET.get('next', '')
        context['cancel_url'] = next_url or str(reverse_lazy('core:device_list'))
        context['next_url'] = next_url
        return context


class DeviceUpdateView(LoginRequiredMixin, UpdateView):
    model = Device
    form_class = DeviceForm
    template_name = 'core/device_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['client_id'] = self.object.client_id
        return kwargs

    def form_valid(self, form):
        self.object = form.save()
        if self.request.POST.get('save_and_create_wo'):
            return redirect(
                reverse_lazy('core:workorder_create') + f'?device={self.object.pk}'
            )
        return redirect(reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk}))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.name}'
        context['cancel_url'] = reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk})
        return context


# --- Ticket Views ---

TICKET_CLOSED_STATUSES = ['resolved', 'closed']


class TicketListView(LoginRequiredMixin, ListView):
    model = Ticket
    template_name = 'core/ticket_list.html'
    context_object_name = 'tickets'
    paginate_by = 25

    def get_queryset(self):
        queryset = Ticket.objects.select_related('client', 'device', 'created_by', 'assigned_to')

        needs_response = self.request.GET.get('needs_response')
        if needs_response:
            queryset = queryset.filter(needs_response=True)
            if not _is_admin(self.request.user):
                queryset = queryset.filter(assigned_to=self.request.user)
        else:
            status = self.request.GET.get('status')
            if status:
                queryset = queryset.filter(status=status)
            else:
                tab = self.request.GET.get('tab', 'active')
                if tab == 'closed':
                    queryset = queryset.filter(status__in=TICKET_CLOSED_STATUSES)
                else:
                    queryset = queryset.exclude(status__in=TICKET_CLOSED_STATUSES)

        assigned_to = self.request.GET.get('assigned_to')
        if assigned_to == 'me' and not _is_admin(self.request.user):
            queryset = queryset.filter(assigned_to=self.request.user)
        elif assigned_to and assigned_to != 'me':
            queryset = queryset.filter(assigned_to_id=assigned_to)

        overdue = self.request.GET.get('overdue')
        if overdue:
            queryset = queryset.filter(due_at__lt=timezone.now()).exclude(status__in=TICKET_CLOSED_STATUSES)

        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(ticket_number__icontains=search) |
                Q(subject__icontains=search) |
                Q(client__name__icontains=search)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = list(
            StatusDefinition.objects.filter(entity_type='ticket', is_active=True)
            .order_by('sort_order').values_list('slug', 'label')
        )
        base_qs = Ticket.objects.all()
        context['active_count'] = base_qs.exclude(status__in=TICKET_CLOSED_STATUSES).count()
        context['closed_count'] = base_qs.filter(status__in=TICKET_CLOSED_STATUSES).count()
        context['current_tab'] = self.request.GET.get('tab', 'active')
        context['needs_response_filter'] = self.request.GET.get('needs_response', '')
        return context


class TicketDetailView(LoginRequiredMixin, DetailView):
    model = Ticket
    template_name = 'core/ticket_detail.html'
    context_object_name = 'ticket'

    def get_queryset(self):
        return Ticket.objects.select_related(
            'client', 'device', 'created_by'
        ).prefetch_related('replies', 'replies__created_by')

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        ticket = self.object
        try:
            lock = ticket.lock
            if lock.is_expired() or lock.locked_by == request.user:
                lock.locked_by = request.user
                lock.save()
        except TicketLock.DoesNotExist:
            TicketLock.objects.create(ticket=ticket, locked_by=request.user)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ticket = self.object
        # Determine lock warning state
        try:
            lock = ticket.lock
            if not lock.is_expired() and lock.locked_by != self.request.user:
                context['lock_user'] = lock.locked_by
        except TicketLock.DoesNotExist:
            pass
        # WO dependency context
        wo = getattr(ticket, 'work_order_created', None)
        if wo:
            context['linked_wo'] = wo
            context['wo_is_open'] = wo.status not in WO_CLOSED_STATUSES
        # Linked tickets
        context['linked_tickets'] = ticket.get_linked_tickets()
        context['audit_log'] = _audit_entries(ticket)
        # Ticket-level attachments
        ct = ContentType.objects.get_for_model(Ticket)
        context['ticket_attachments'] = Attachment.objects.filter(content_type=ct, object_id=ticket.pk)
        # Custom fields
        fields = _get_custom_fields_for_ticket(ticket)
        context['custom_field_values'] = _custom_fields_with_values(fields, ticket)
        # Open WOs for this client (cross-visibility panel)
        linked_wo_pk = wo.pk if wo else None
        open_wos = (
            WorkOrder.objects
            .filter(client=ticket.client)
            .exclude(status__in=('completed', 'closed', 'cancelled'))
            .select_related('assigned_to', 'repair_type')
            .prefetch_related('notes')
            .order_by('-created_at')
        )
        if linked_wo_pk:
            open_wos = open_wos.exclude(pk=linked_wo_pk)
        context['client_open_wos'] = open_wos
        context['all_users'] = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
        context['is_admin'] = _is_admin(self.request.user)
        return context


class TicketCreateView(LoginRequiredMixin, CreateView):
    model = Ticket
    form_class = TicketForm
    template_name = 'core/ticket_form.html'

    def form_valid(self, form):
        form.instance.ticket_number = Ticket.generate_ticket_number()
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        _save_attachments(self.request, self.object)
        fields = _get_custom_fields_for_ticket(self.object)
        _save_custom_field_values(self.request, self.object, fields)
        from .email_utils import send_ticket_email
        send_ticket_email('ticket_created', self.object)
        return response

    def get_success_url(self):
        return reverse_lazy('core:ticket_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'New Ticket'
        context['cancel_url'] = reverse_lazy('core:ticket_list')
        fields = _get_custom_fields_for_ticket(None)
        context['custom_field_entries'] = [{'field': f, 'value': ''} for f in fields]
        return context


class TicketUpdateView(LoginRequiredMixin, UpdateView):
    model = Ticket
    form_class = TicketForm
    template_name = 'core/ticket_form.html'

    def form_valid(self, form):
        new_status = form.cleaned_data.get('status')
        if new_status in ('resolved', 'closed'):
            wo = getattr(self.object, 'work_order_created', None)
            if wo and wo.status not in WO_CLOSED_STATUSES:
                form.add_error('status', f'Cannot close this ticket — linked work order {wo.work_order_number} is still open.')
                return self.form_invalid(form)
        # If client changed, clear device (it belongs to the old client)
        new_client = form.cleaned_data.get('client')
        if new_client and self.object.client_id != new_client.pk:
            form.instance.device = None
        old_status = self.object.status
        response = super().form_valid(form)
        fields = _get_custom_fields_for_ticket(self.object)
        _save_custom_field_values(self.request, self.object, fields)
        if self.object.status in ('resolved', 'closed') and self.object.wo_complete:
            self.object.wo_complete = False
            self.object.save(update_fields=['wo_complete'])
        from .email_utils import send_ticket_email
        if self.object.status != old_status:
            send_ticket_email('status_changed', self.object, {'old_status': old_status})
            if self.object.status == 'resolved':
                send_ticket_email('ticket_resolved', self.object)
        return response

    def get_success_url(self):
        return reverse_lazy('core:ticket_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.ticket_number}'
        context['cancel_url'] = reverse_lazy('core:ticket_detail', kwargs={'pk': self.object.pk})
        fields = _get_custom_fields_for_ticket(self.object)
        values = _get_custom_field_values(self.object)
        context['custom_field_entries'] = [{'field': f, 'value': values.get(f.pk, '')} for f in fields]
        return context


class TicketReplyCreateView(LoginRequiredMixin, View):
    """Add a reply to a ticket — returns HTML fragment for HTMX"""

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        reply_type = request.POST.get('reply_type', 'internal')
        content = request.POST.get('content', '').strip()

        if not content:
            return HttpResponse(status=204)

        reply = TicketReply.objects.create(
            ticket=ticket,
            reply_type=reply_type,
            content=content,
            created_by=request.user,
        )
        _save_attachments(request, reply)
        if reply.reply_type == 'customer_visible':
            if ticket.needs_response:
                ticket.needs_response = False
                ticket.save(update_fields=['needs_response', 'updated_at'])
            from .email_utils import send_ticket_email
            prior_replies = list(
                ticket.replies.filter(reply_type='customer_visible')
                .exclude(pk=reply.pk)
                .order_by('created_at')
            )
            cc_raw = request.POST.get('cc_emails', '')
            cc_list = [e.strip() for e in cc_raw.split(',') if e.strip()]
            send_ticket_email('reply_added', ticket, {
                'reply': reply,
                'prior_replies': prior_replies,
            }, cc=cc_list)
        return render(request, 'core/partials/ticket_reply_item.html', {'reply': reply})


class TicketReplyResendView(LoginRequiredMixin, View):
    """Resend a specific reply email to a chosen address."""

    def post(self, request, pk, reply_pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        reply = get_object_or_404(TicketReply, pk=reply_pk, ticket=ticket)
        to_email = request.POST.get('to_email', '').strip()
        if to_email == '__custom__':
            to_email = request.POST.get('to_email_custom', '').strip()
        if not to_email:
            messages.error(request, 'No email address provided.')
            return redirect('core:ticket_detail', pk=pk)
        from .email_utils import send_ticket_email
        prior_replies = list(
            ticket.replies.filter(reply_type='customer_visible')
            .exclude(pk=reply.pk)
            .order_by('created_at')
        )
        send_ticket_email('reply_added', ticket, {
            'reply': reply,
            'prior_replies': prior_replies,
            '_override_to': to_email,
        })
        messages.success(request, f'Reply resent to {to_email}.')
        return redirect('core:ticket_detail', pk=pk)


class TicketDismissNeedsResponseView(LoginRequiredMixin, View):
    """Manually dismiss the needs_response flag with a required note."""

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        note = request.POST.get('note', '').strip()
        if not note:
            messages.error(request, 'A note is required to dismiss the response flag.')
            return redirect('core:ticket_detail', pk=pk)
        TicketReply.objects.create(
            ticket=ticket,
            reply_type='internal',
            content=f'[Response flag dismissed] {note}',
            created_by=request.user,
        )
        ticket.needs_response = False
        ticket.save(update_fields=['needs_response', 'updated_at'])
        messages.success(request, 'Response flag dismissed.')
        return redirect('core:ticket_detail', pk=pk)


class TicketConvertView(LoginRequiredMixin, View):
    """Convert a ticket to a work order"""

    def get(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        if ticket.status == 'converted':
            return redirect('core:ticket_detail', pk=pk)
        form = TicketConvertForm()
        return render(request, 'core/ticket_convert.html', {'ticket': ticket, 'form': form})

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        if ticket.status == 'converted':
            return redirect('core:ticket_detail', pk=pk)

        form = TicketConvertForm(request.POST)
        if not form.is_valid():
            return render(request, 'core/ticket_convert.html', {'ticket': ticket, 'form': form})

        work_order = WorkOrder.objects.create(
            work_order_number=WorkOrder.generate_work_order_number(from_ticket_number=ticket.ticket_number),
            ticket=ticket,
            client=ticket.client,
            device=ticket.device,
            repair_type=form.cleaned_data.get('repair_type'),
            assigned_to=form.cleaned_data.get('assigned_to'),
            status='new',
        )

        ticket.status = 'converted'
        ticket.save()

        return redirect('core:work_order_detail', pk=work_order.pk)


# --- Collision Avoidance (TicketLock) ---

class TicketLockReleaseView(LoginRequiredMixin, View):
    """Release a ticket lock — called via JS beforeunload"""

    def post(self, request, pk):
        try:
            lock = TicketLock.objects.get(ticket_id=pk, locked_by=request.user)
            lock.delete()
        except TicketLock.DoesNotExist:
            pass
        return HttpResponse(status=204)


class TicketLockStatusView(LoginRequiredMixin, View):
    """Return lock banner HTML fragment — polled every 30s by HTMX"""

    def get(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        lock_user = None
        try:
            lock = ticket.lock
            if not lock.is_expired() and lock.locked_by != request.user:
                lock_user = lock.locked_by
        except TicketLock.DoesNotExist:
            pass
        return render(request, 'core/partials/ticket_lock_banner.html', {'lock_user': lock_user})


# --- Ticket Linking ---

class TicketLinkAddView(LoginRequiredMixin, View):
    """Add a link between two tickets — returns updated linked tickets partial"""

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        ticket_number = request.POST.get('ticket_number', '').strip()
        link_type = request.POST.get('link_type', 'related')
        error = None

        try:
            other = Ticket.objects.get(ticket_number__iexact=ticket_number)
        except Ticket.DoesNotExist:
            error = f'Ticket "{ticket_number}" not found.'
            other = None

        if other and other.pk == ticket.pk:
            error = 'A ticket cannot be linked to itself.'
            other = None

        if other and not error:
            # Check for existing link in either direction
            exists = (
                TicketLink.objects.filter(ticket_a=ticket, ticket_b=other).exists() or
                TicketLink.objects.filter(ticket_a=other, ticket_b=ticket).exists()
            )
            if exists:
                error = f'{ticket_number} is already linked to this ticket.'
            else:
                TicketLink.objects.create(
                    ticket_a=ticket,
                    ticket_b=other,
                    link_type=link_type,
                    created_by=request.user,
                )

        return render(request, 'core/partials/ticket_linked_list.html', {
            'ticket': ticket,
            'linked_tickets': ticket.get_linked_tickets(),
            'link_error': error,
        })


class AttachmentDownloadView(LoginRequiredMixin, View):
    """Serve an attachment file, enforcing authentication."""

    def get(self, request, pk):
        attachment = get_object_or_404(Attachment, pk=pk)
        from django.conf import settings as django_settings
        if getattr(django_settings, 'ATTACHMENT_STORAGE_BACKEND', 'local') == 's3':
            import boto3
            from botocore.config import Config
            site = SiteSettings.get()
            client = boto3.client(
                's3',
                aws_access_key_id=site.s3_access_key,
                aws_secret_access_key=site.s3_secret_key,
                endpoint_url=site.s3_endpoint_url or None,
                region_name=site.s3_region or None,
                config=Config(signature_version='s3v4'),
            )
            url = client.generate_presigned_url(
                'get_object',
                Params={'Bucket': site.s3_bucket_name, 'Key': attachment.file.name},
                ExpiresIn=60,
            )
            from django.shortcuts import redirect as dj_redirect
            return dj_redirect(url)
        else:
            try:
                return FileResponse(
                    attachment.file.open('rb'),
                    as_attachment=True,
                    filename=attachment.original_filename,
                )
            except FileNotFoundError:
                raise Http404


class TicketLinkRemoveView(LoginRequiredMixin, View):
    """Remove a ticket link — returns updated linked tickets partial"""

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        link_id = request.POST.get('link_id')
        try:
            link = TicketLink.objects.get(pk=link_id)
            if link.ticket_a == ticket or link.ticket_b == ticket:
                link.delete()
        except TicketLink.DoesNotExist:
            pass

        return render(request, 'core/partials/ticket_linked_list.html', {
            'ticket': ticket,
            'linked_tickets': ticket.get_linked_tickets(),
            'link_error': None,
        })


# ---------------------------------------------------------------------------
# SLA — Overdue Acknowledgment
# ---------------------------------------------------------------------------

class TicketAssignView(LoginRequiredMixin, View):
    """Assign or claim a ticket. POST with assigned_to=<pk> or assigned_to='' to unassign, or claim=1 to self-assign."""

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        if request.POST.get('claim'):
            ticket.assigned_to = request.user
        else:
            uid = request.POST.get('assigned_to', '').strip()
            ticket.assigned_to_id = uid if uid else None
        ticket.save(update_fields=['assigned_to', 'updated_at'])
        return redirect('core:ticket_detail', pk=pk)


class TicketAcknowledgeOverdueView(LoginRequiredMixin, View):
    """Acknowledge an overdue ticket with a required internal note."""

    def get(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        return render(request, 'core/partials/overdue_ack_form.html', {'ticket': ticket})

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        note_text = request.POST.get('note', '').strip()
        if not note_text:
            # Re-render the ack form with an error
            return render(request, 'core/partials/overdue_ack_form.html', {
                'ticket': ticket,
                'ack_error': 'A note is required to acknowledge the overdue status.',
            })
        TicketReply.objects.create(
            ticket=ticket,
            reply_type='internal',
            content=f'[Overdue Acknowledged] {note_text}',
            created_by=request.user,
        )
        ticket.overdue_acknowledged_by = request.user
        ticket.overdue_acknowledged_at = timezone.now()
        ticket.save(update_fields=['overdue_acknowledged_by', 'overdue_acknowledged_at'])
        return render(request, 'core/partials/overdue_badge.html', {'ticket': ticket})


class TicketContactsByClientView(LoginRequiredMixin, View):
    """HTMX: return <option> elements for contacts belonging to a given client."""

    def get(self, request):
        client_id = request.GET.get('client_id') or request.GET.get('client')
        contacts = []
        if client_id:
            contacts = Contact.objects.filter(client_id=client_id, is_active=True).order_by('last_name', 'first_name')
        opts = '<option value="">---------</option>'
        for c in contacts:
            opts += f'<option value="{c.pk}">{c.first_name} {c.last_name}</option>'
        return HttpResponse(opts)


class TicketCloseView(LoginRequiredMixin, View):
    """Set a ticket to 'resolved' status. Used when WO is complete and tech has contacted client."""

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        if ticket.status in TICKET_CLOSED_STATUSES:
            messages.info(request, 'Ticket is already closed.')
            return redirect('core:ticket_detail', pk=pk)
        ticket.status = 'resolved'
        ticket.wo_complete = False
        ticket.save(update_fields=['status', 'wo_complete', 'updated_at'])
        messages.success(request, f'{ticket.ticket_number} resolved.')
        return redirect('core:ticket_detail', pk=pk)


class TicketDeleteView(LoginRequiredMixin, View):
    """Hard-delete a ticket. Admin only. Blocked if a work order is linked."""

    def post(self, request, pk):
        if not request.user.is_staff:
            return HttpResponse('Forbidden', status=403)
        ticket = get_object_or_404(Ticket, pk=pk)
        if hasattr(ticket, 'work_order'):
            messages.error(request, f'Cannot delete {ticket.ticket_number} — it has a linked work order.')
            return redirect('core:ticket_detail', pk=pk)
        ticket_num = ticket.ticket_number
        ticket.delete()
        messages.success(request, f'{ticket_num} permanently deleted.')
        return redirect('core:ticket_list')


# ---------------------------------------------------------------------------
# Knowledge Base
# ---------------------------------------------------------------------------

class KBListView(LoginRequiredMixin, View):
    def get(self, request):
        q = request.GET.get('q', '').strip()
        category_id = request.GET.get('category', '')
        article_type = request.GET.get('type', '')

        articles = KBArticle.objects.select_related('category', 'author').filter(is_active=True)

        # Restrict admin-only articles
        can_view_restricted = (
            request.user.is_staff or request.user.has_perm_flag('can_view_restricted_kb')
        )
        if not can_view_restricted:
            articles = articles.filter(is_restricted=False)

        if q:
            articles = articles.filter(Q(title__icontains=q) | Q(content__icontains=q))
        if category_id:
            articles = articles.filter(category_id=category_id)
        if article_type:
            articles = articles.filter(article_type=article_type)

        categories = KBCategory.objects.order_by('sort_order', 'name')
        return render(request, 'core/kb_list.html', {
            'articles': articles.order_by('-updated_at'),
            'categories': categories,
            'article_types': KBArticle.ARTICLE_TYPE_CHOICES,
            'q': q,
            'selected_category': category_id,
            'selected_type': article_type,
        })


class KBDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        article = get_object_or_404(KBArticle, pk=pk, is_active=True)
        can_view_restricted = (
            request.user.is_staff or request.user.has_perm_flag('can_view_restricted_kb')
        )
        if article.is_restricted and not can_view_restricted:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return render(request, 'core/kb_detail.html', {'article': article})


class KBArticleCreateView(LoginRequiredMixin, View):
    def get(self, request):
        can_manage = request.user.is_staff or request.user.has_perm_flag('can_manage_kb')
        if not can_manage:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = KBArticleForm()
        return render(request, 'core/kb_form.html', {'form': form, 'action': 'Create'})

    def post(self, request):
        can_manage = request.user.is_staff or request.user.has_perm_flag('can_manage_kb')
        if not can_manage:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = KBArticleForm(request.POST)
        if form.is_valid():
            article = form.save(commit=False)
            article.author = request.user
            article.save()
            return redirect('core:kb_detail', pk=article.pk)
        return render(request, 'core/kb_form.html', {'form': form, 'action': 'Create'})


class KBArticleEditView(LoginRequiredMixin, View):
    def get(self, request, pk):
        can_manage = request.user.is_staff or request.user.has_perm_flag('can_manage_kb')
        if not can_manage:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        article = get_object_or_404(KBArticle, pk=pk)
        form = KBArticleForm(instance=article)
        return render(request, 'core/kb_form.html', {'form': form, 'article': article, 'action': 'Edit'})

    def post(self, request, pk):
        can_manage = request.user.is_staff or request.user.has_perm_flag('can_manage_kb')
        if not can_manage:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        article = get_object_or_404(KBArticle, pk=pk)
        form = KBArticleForm(request.POST, instance=article)
        if form.is_valid():
            form.save()
            return redirect('core:kb_detail', pk=article.pk)
        return render(request, 'core/kb_form.html', {'form': form, 'article': article, 'action': 'Edit'})


# ─── Custom Queues ────────────────────────────────────────────────────────────

def _apply_queue_filters(qs, criteria, user):
    """Apply filter_criteria dict to a Ticket queryset."""
    if not criteria:
        return qs
    statuses = criteria.get('status')
    if statuses:
        qs = qs.filter(status__in=statuses)
    assigned_to = criteria.get('assigned_to')
    if assigned_to is None and 'assigned_to' in criteria:
        qs = qs.filter(assigned_to__isnull=True)
    elif assigned_to:
        qs = qs.filter(assigned_to_id=assigned_to)
    help_topic = criteria.get('help_topic')
    if help_topic:
        qs = qs.filter(help_topic_id=help_topic)
    sla_plan = criteria.get('sla_plan')
    if sla_plan:
        qs = qs.filter(sla_plan_id=sla_plan)
    client = criteria.get('client')
    if client:
        qs = qs.filter(client_id=client)
    if criteria.get('overdue'):
        qs = qs.filter(due_at__lt=timezone.now()).exclude(status__in=['closed', 'resolved', 'converted'])
    return qs


class QueueListView(LoginRequiredMixin, View):
    def get(self, request):
        system_queues = TicketQueue.objects.filter(owner=None, is_active=True)
        my_queues = TicketQueue.objects.filter(owner=request.user, is_active=True)
        return render(request, 'core/queue_list.html', {
            'system_queues': system_queues,
            'my_queues': my_queues,
        })


class QueueDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        queue = get_object_or_404(TicketQueue, pk=pk)
        if queue.owner and queue.owner != request.user and not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        sort_prefix = '' if queue.sort_direction == 'asc' else '-'
        sort_field = sort_prefix + queue.sort_field
        tickets = _apply_queue_filters(
            Ticket.objects.select_related('client', 'assigned_to', 'sla_plan', 'help_topic'),
            queue.filter_criteria,
            request.user,
        ).order_by(sort_field)
        return render(request, 'core/queue_detail.html', {
            'queue': queue,
            'tickets': tickets,
        })


class QueueCreateView(LoginRequiredMixin, View):
    def get(self, request):
        from .forms import TicketQueueForm
        is_admin = _is_admin(request.user)
        form = TicketQueueForm(is_admin=is_admin)
        return render(request, 'core/queue_form.html', {'form': form, 'action': 'Create'})

    def post(self, request):
        from .forms import TicketQueueForm
        is_admin = _is_admin(request.user)
        form = TicketQueueForm(request.POST, is_admin=is_admin)
        if form.is_valid():
            queue = form.save(commit=False)
            if not is_admin:
                queue.owner = request.user
            queue.save()
            return redirect('core:queue_detail', pk=queue.pk)
        return render(request, 'core/queue_form.html', {'form': form, 'action': 'Create'})


class QueueEditView(LoginRequiredMixin, View):
    def _get_queue(self, request, pk):
        queue = get_object_or_404(TicketQueue, pk=pk)
        is_admin = _is_admin(request.user)
        if queue.owner is None and not is_admin:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if queue.owner and queue.owner != request.user and not is_admin:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return queue, is_admin

    def get(self, request, pk):
        from .forms import TicketQueueForm
        queue, is_admin = self._get_queue(request, pk)
        form = TicketQueueForm(instance=queue, is_admin=is_admin)
        return render(request, 'core/queue_form.html', {'form': form, 'queue': queue, 'action': 'Edit'})

    def post(self, request, pk):
        from .forms import TicketQueueForm
        queue, is_admin = self._get_queue(request, pk)
        form = TicketQueueForm(request.POST, instance=queue, is_admin=is_admin)
        if form.is_valid():
            form.save()
            return redirect('core:queue_detail', pk=queue.pk)
        return render(request, 'core/queue_form.html', {'form': form, 'queue': queue, 'action': 'Edit'})


class QueueDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        queue = get_object_or_404(TicketQueue, pk=pk)
        is_admin = _is_admin(request.user)
        if queue.owner is None and not is_admin:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if queue.owner and queue.owner != request.user and not is_admin:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        queue.delete()
        return redirect('core:queue_list')


# ─── Sidebar Fragment ─────────────────────────────────────────────────────────

class SidebarFragmentView(LoginRequiredMixin, View):
    """HTMX endpoint: returns sidebar content (my tickets + my WOs)."""
    def get(self, request):
        is_admin = _is_admin(request.user)

        ticket_qs = Ticket.objects.select_related('client').prefetch_related('replies').exclude(
            status__in=['closed', 'resolved']
        )
        if is_admin:
            ticket_qs = ticket_qs.order_by('-updated_at')
        else:
            ticket_qs = ticket_qs.filter(
                Q(assigned_to=request.user) | Q(created_by=request.user)
            ).distinct().order_by('-updated_at')

        wo_qs = WorkOrder.objects.select_related('client').prefetch_related('notes').exclude(
            status__in=['closed', 'cancelled']
        )
        if is_admin:
            wo_qs = wo_qs.order_by('-updated_at')
        else:
            wo_qs = wo_qs.filter(assigned_to=request.user).order_by('-updated_at')

        return render(request, 'core/partials/sidebar_content.html', {
            'my_tickets': list(ticket_qs[:20]),
            'my_wos': list(wo_qs[:20]),
            'is_admin': is_admin,
        })


# ─── Reports ──────────────────────────────────────────────────────────────────

class ReportsView(LoginRequiredMixin, View):
    def get(self, request):
        if not (_is_admin(request.user) or request.user.has_perm_flag('can_view_reports')):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        from datetime import timedelta, date
        from django.db.models import Count, Avg, F, Sum, ExpressionWrapper, DurationField, FloatField
        from django.db.models.functions import TruncDay, TruncWeek

        # Date range
        end_date_str = request.GET.get('end_date', '')
        start_date_str = request.GET.get('start_date', '')
        try:
            end_date = date.fromisoformat(end_date_str)
        except ValueError:
            end_date = date.today()
        try:
            start_date = date.fromisoformat(start_date_str)
        except ValueError:
            start_date = end_date - timedelta(days=30)

        start_dt = timezone.make_aware(
            timezone.datetime(start_date.year, start_date.month, start_date.day)
        )
        end_dt = timezone.make_aware(
            timezone.datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59)
        )

        tickets_in_range = Ticket.objects.filter(created_at__range=(start_dt, end_dt))

        # 1. Ticket volume over time
        volume_by_day = list(
            tickets_in_range.annotate(day=TruncDay('created_at'))
            .values('day').annotate(count=Count('id')).order_by('day')
        )
        volume_labels = [str(r['day'].date()) for r in volume_by_day]
        volume_data = [r['count'] for r in volume_by_day]

        # 2. Open tickets by status
        status_counts = list(
            Ticket.objects.exclude(status__in=['closed', 'resolved', 'converted'])
            .values('status').annotate(count=Count('id'))
        )
        status_labels = [r['status'] for r in status_counts]
        status_data = [r['count'] for r in status_counts]

        # 3. Tickets by client
        by_client = list(
            tickets_in_range.values('client__name').annotate(count=Count('id')).order_by('-count')[:15]
        )

        # 4. Tickets by technician (workload)
        by_tech = list(
            tickets_in_range.filter(assigned_to__isnull=False)
            .values('assigned_to__first_name', 'assigned_to__last_name', 'assigned_to__username')
            .annotate(count=Count('id')).order_by('-count')
        )

        # 5. Average resolution time (seconds → hours)
        closed_tickets = tickets_in_range.filter(
            status__in=['closed', 'resolved'],
            updated_at__isnull=False,
        )
        # By tech
        resolution_by_tech = []
        for entry in (
            closed_tickets.filter(assigned_to__isnull=False)
            .values('assigned_to__first_name', 'assigned_to__last_name')
            .annotate(count=Count('id'))
        ):
            resolution_by_tech.append(entry)

        # Compute avg resolution hours per tech using Python (SQLite compat)
        tech_resolution = []
        for entry in closed_tickets.filter(assigned_to__isnull=False).select_related('assigned_to'):
            delta = (entry.updated_at - entry.created_at).total_seconds() / 3600
            tech_name = entry.assigned_to.get_full_name() or entry.assigned_to.username
            tech_resolution.append((tech_name, delta))
        tech_res_agg = {}
        for name, hours in tech_resolution:
            tech_res_agg.setdefault(name, []).append(hours)
        avg_res_by_tech = [
            {'name': k, 'avg_hours': round(sum(v) / len(v), 1), 'count': len(v)}
            for k, v in tech_res_agg.items()
        ]

        # 6. SLA compliance
        tickets_with_sla = tickets_in_range.filter(due_at__isnull=False)
        total_sla = tickets_with_sla.count()
        closed_on_time = tickets_with_sla.filter(
            status__in=['closed', 'resolved'],
            updated_at__lte=F('due_at'),
        ).count()
        sla_rate = round(100 * closed_on_time / total_sla, 1) if total_sla else None

        # 7. Ticket → WO conversion rate
        total_tickets = tickets_in_range.count()
        converted_count = tickets_in_range.filter(status='converted').count()
        conversion_rate = round(100 * converted_count / total_tickets, 1) if total_tickets else None

        # 8. Mileage by tech and month
        from django.db.models.functions import TruncMonth
        mileage_data = list(
            Mileage.objects.filter(trip_date__range=(start_date, end_date))
            .annotate(month=TruncMonth('trip_date'))
            .values('technician__first_name', 'technician__last_name', 'month')
            .annotate(miles=Sum('miles'))
            .order_by('month', 'technician__last_name')
        )

        # 9. Billing / financial summary
        invoiced_total = Invoice.objects.filter(
            invoiced_date__range=(start_date, end_date),
            amount__isnull=False,
        ).aggregate(total=Sum('amount'))['total'] or 0

        paid_total = Invoice.objects.filter(
            paid_date__range=(start_date, end_date),
            billing_status__in=['paid', 'paid_direct'],
            amount__isnull=False,
        ).aggregate(total=Sum('amount'))['total'] or 0

        # Outstanding is a current snapshot — not date-filtered
        outstanding_total = Invoice.objects.filter(
            billing_status__in=['uninvoiced', 'invoiced'],
            amount__isnull=False,
        ).aggregate(total=Sum('amount'))['total'] or 0

        outstanding_by_client = list(
            Invoice.objects.filter(
                billing_status__in=['uninvoiced', 'invoiced'],
                amount__isnull=False,
            )
            .values('work_order__client__name', 'work_order__client__pk')
            .annotate(total=Sum('amount'))
            .order_by('-total')
        )

        # 10. Technician performance
        tech_perf = []
        for tech in User.objects.filter(is_active=True).order_by('first_name', 'last_name'):
            wos_in_range = WorkOrder.objects.filter(
                assigned_to=tech, created_at__range=(start_dt, end_dt)
            )
            total_wos = wos_in_range.count()
            completed_wos = wos_in_range.filter(status='completed').count()
            completion_rate = round(100 * completed_wos / total_wos) if total_wos else None

            # Avg resolution time in hours (Python-side for DB compat)
            closed_wos = wos_in_range.filter(status='completed', completed_date__isnull=False)
            hours_list = [
                (wo.completed_date - wo.created_at).total_seconds() / 3600
                for wo in closed_wos
                if wo.completed_date and wo.created_at
            ]
            avg_hours = round(sum(hours_list) / len(hours_list), 1) if hours_list else None

            open_wos = WorkOrder.objects.filter(
                assigned_to=tech,
                status__in=['new', 'in_progress', 'waiting_on_customer', 'on_hold']
            ).count()

            if total_wos or open_wos:
                tech_perf.append({
                    'tech': tech,
                    'total_wos': total_wos,
                    'completed_wos': completed_wos,
                    'completion_rate': completion_rate,
                    'avg_hours': avg_hours,
                    'open_wos': open_wos,
                })

        context = {
            'start_date': start_date,
            'end_date': end_date,
            # 1
            'volume_labels': volume_labels,
            'volume_data': volume_data,
            # 2
            'status_labels': status_labels,
            'status_data': status_data,
            # 3
            'by_client': by_client,
            # 4
            'by_tech': by_tech,
            # 5
            'avg_res_by_tech': avg_res_by_tech,
            # 6
            'sla_rate': sla_rate,
            'total_sla': total_sla,
            'closed_on_time': closed_on_time,
            # 7
            'conversion_rate': conversion_rate,
            'converted_count': converted_count,
            'total_tickets': total_tickets,
            # 8
            'mileage_data': mileage_data,
            # 9
            'invoiced_total': invoiced_total,
            'paid_total': paid_total,
            'outstanding_total': outstanding_total,
            'outstanding_by_client': outstanding_by_client,
            # 10
            'tech_perf': tech_perf,
        }
        return render(request, 'core/reports.html', context)


class ReportsCSVView(LoginRequiredMixin, View):
    """Download a specific report as CSV."""
    def get(self, request, report):
        if not (_is_admin(request.user) or request.user.has_perm_flag('can_view_reports')):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        import csv
        from datetime import timedelta, date

        end_date_str = request.GET.get('end_date', '')
        start_date_str = request.GET.get('start_date', '')
        try:
            end_date = date.fromisoformat(end_date_str)
        except ValueError:
            end_date = date.today()
        try:
            start_date = date.fromisoformat(start_date_str)
        except ValueError:
            start_date = end_date - timedelta(days=30)
        start_dt = timezone.make_aware(timezone.datetime(start_date.year, start_date.month, start_date.day))
        end_dt = timezone.make_aware(timezone.datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59))

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="report_{report}.csv"'
        writer = csv.writer(response)

        tickets_in_range = Ticket.objects.filter(created_at__range=(start_dt, end_dt))
        from django.db.models import Count, F, Sum
        from django.db.models.functions import TruncDay, TruncMonth

        if report == 'volume':
            writer.writerow(['Date', 'Tickets Created'])
            for r in tickets_in_range.annotate(day=TruncDay('created_at')).values('day').annotate(count=Count('id')).order_by('day'):
                writer.writerow([r['day'].date(), r['count']])

        elif report == 'status':
            writer.writerow(['Status', 'Count'])
            for r in Ticket.objects.exclude(status__in=['closed', 'resolved', 'converted']).values('status').annotate(count=Count('id')):
                writer.writerow([r['status'], r['count']])

        elif report == 'by_client':
            writer.writerow(['Client', 'Tickets'])
            for r in tickets_in_range.values('client__name').annotate(count=Count('id')).order_by('-count'):
                writer.writerow([r['client__name'], r['count']])

        elif report == 'by_tech':
            writer.writerow(['Tech', 'Tickets Assigned'])
            for r in tickets_in_range.filter(assigned_to__isnull=False).values('assigned_to__first_name', 'assigned_to__last_name').annotate(count=Count('id')).order_by('-count'):
                writer.writerow([f"{r['assigned_to__first_name']} {r['assigned_to__last_name']}", r['count']])

        elif report == 'resolution':
            writer.writerow(['Tech', 'Tickets Closed', 'Avg Resolution (hours)'])
            closed = tickets_in_range.filter(status__in=['closed', 'resolved']).select_related('assigned_to')
            tech_res_agg = {}
            for t in closed.filter(assigned_to__isnull=False):
                name = t.assigned_to.get_full_name() or t.assigned_to.username
                tech_res_agg.setdefault(name, []).append((t.updated_at - t.created_at).total_seconds() / 3600)
            for name, hours in tech_res_agg.items():
                writer.writerow([name, len(hours), round(sum(hours) / len(hours), 1)])

        elif report == 'sla':
            writer.writerow(['Metric', 'Value'])
            t = tickets_in_range.filter(due_at__isnull=False)
            total = t.count()
            on_time = t.filter(status__in=['closed', 'resolved'], updated_at__lte=F('due_at')).count()
            writer.writerow(['Total tickets with SLA', total])
            writer.writerow(['Closed on time', on_time])
            writer.writerow(['Compliance rate', f"{round(100*on_time/total,1) if total else 'N/A'}%"])

        elif report == 'conversion':
            writer.writerow(['Metric', 'Value'])
            total = tickets_in_range.count()
            converted = tickets_in_range.filter(status='converted').count()
            writer.writerow(['Total tickets', total])
            writer.writerow(['Converted to WO', converted])
            writer.writerow(['Conversion rate', f"{round(100*converted/total,1) if total else 'N/A'}%"])

        elif report == 'mileage':
            writer.writerow(['Tech', 'Month', 'Miles'])
            for r in Mileage.objects.filter(trip_date__range=(start_date, end_date)).annotate(month=TruncMonth('trip_date')).values('technician__first_name', 'technician__last_name', 'month').annotate(miles=Sum('miles')).order_by('month', 'technician__last_name'):
                writer.writerow([f"{r['technician__first_name']} {r['technician__last_name']}", r['month'].strftime('%Y-%m'), round(float(r['miles']), 1)])

        elif report == 'tech_perf':
            writer.writerow(['Technician', 'WOs (period)', 'Completed', 'Completion %', 'Avg Resolution (hrs)', 'Open Now'])
            for tech in User.objects.filter(is_active=True).order_by('first_name', 'last_name'):
                wos_in_range = WorkOrder.objects.filter(assigned_to=tech, created_at__range=(start_dt, end_dt))
                total_wos = wos_in_range.count()
                completed_wos = wos_in_range.filter(status='completed').count()
                completion_rate = round(100 * completed_wos / total_wos) if total_wos else ''
                closed_wos = wos_in_range.filter(status='completed', completed_date__isnull=False)
                hours_list = [(wo.completed_date - wo.created_at).total_seconds() / 3600 for wo in closed_wos if wo.completed_date and wo.created_at]
                avg_hours = round(sum(hours_list) / len(hours_list), 1) if hours_list else ''
                open_wos = WorkOrder.objects.filter(assigned_to=tech, status__in=['new', 'in_progress', 'waiting_on_customer', 'on_hold']).count()
                if total_wos or open_wos:
                    writer.writerow([tech.get_full_name() or tech.username, total_wos, completed_wos, completion_rate, avg_hours, open_wos])

        elif report == 'billing':
            writer.writerow(['WO #', 'Client', 'Billing Status', 'Amount', 'Invoiced Date', 'Paid Date', 'Payment Method'])
            for inv in Invoice.objects.filter(
                amount__isnull=False,
            ).select_related('work_order', 'work_order__client').order_by('work_order__client__name', 'work_order__created_at'):
                wo = inv.work_order
                writer.writerow([
                    wo.work_order_number,
                    wo.client.name if wo.client else '',
                    inv.get_billing_status_display(),
                    inv.amount,
                    inv.invoiced_date or '',
                    inv.paid_date or '',
                    inv.get_payment_method_display() if inv.payment_method else '',
                ])

        else:
            writer.writerow(['Error', 'Unknown report'])

        return response


# --- MFA / Security Profile Views ---

class SecurityProfileView(LoginRequiredMixin, View):
    """User-facing MFA status and setup links. Redirects to two_factor:profile."""

    def get(self, request):
        return redirect('two_factor:profile')


class AdminBackupTokensView(LoginRequiredMixin, View):
    """Backup tokens — admin users only. Wraps two_factor.views.BackupTokensView."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        from two_factor.views import BackupTokensView
        return BackupTokensView.as_view()(request, *args, **kwargs)


class UserListView(LoginRequiredMixin, View):
    """Admin-only: list all users with MFA status."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from django_otp import devices_for_user
        users = User.objects.select_related('role_obj').order_by('first_name', 'last_name')
        user_rows = []
        for u in users:
            devices = list(devices_for_user(u))
            user_rows.append({
                'user': u,
                'has_mfa': bool(devices),
                'device_count': len(devices),
            })
        return render(request, 'core/user_list.html', {
            'user_rows': user_rows,
            'require_mfa': SiteSettings.get().require_mfa,
        })


class AdminMFAResetView(LoginRequiredMixin, View):
    """Admin-only: clear all OTP devices for a user (lost device recovery)."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        from django_otp import devices_for_user
        from django.contrib import messages
        target = get_object_or_404(User, pk=pk)
        for device in list(devices_for_user(target)):
            device.delete()
        messages.success(
            request,
            f'MFA reset for {target.get_full_name() or target.username}. '
            'They will be prompted to re-enroll on next login.'
        )
        return redirect('core:user_list')


# ---------------------------------------------------------------------------
# User CRUD (admin only)
# ---------------------------------------------------------------------------

class UserCreateView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from .forms import UserCreateForm
        return render(request, 'core/user_form.html', {
            'form': UserCreateForm(),
            'title': 'New User',
            'cancel_url': reverse_lazy('core:user_list'),
        })

    def post(self, request):
        from .forms import UserCreateForm
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f'User {user.get_full_name() or user.username} created.')
            return redirect('core:user_list')
        return render(request, 'core/user_form.html', {
            'form': form,
            'title': 'New User',
            'cancel_url': reverse_lazy('core:user_list'),
        })


class UserEditView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        from .forms import UserEditForm
        target = get_object_or_404(User, pk=pk)
        return render(request, 'core/user_form.html', {
            'form': UserEditForm(instance=target),
            'target': target,
            'title': f'Edit {target.get_full_name() or target.username}',
            'cancel_url': reverse_lazy('core:user_list'),
        })

    def post(self, request, pk):
        from .forms import UserEditForm
        target = get_object_or_404(User, pk=pk)
        form = UserEditForm(request.POST, instance=target)
        if form.is_valid():
            form.save()
            messages.success(request, f'User {target.get_full_name() or target.username} updated.')
            return redirect('core:user_list')
        return render(request, 'core/user_form.html', {
            'form': form,
            'target': target,
            'title': f'Edit {target.get_full_name() or target.username}',
            'cancel_url': reverse_lazy('core:user_list'),
        })


class UserSetPasswordView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        from .forms import UserSetPasswordForm
        target = get_object_or_404(User, pk=pk)
        return render(request, 'core/user_set_password.html', {
            'form': UserSetPasswordForm(),
            'target': target,
        })

    def post(self, request, pk):
        from .forms import UserSetPasswordForm
        target = get_object_or_404(User, pk=pk)
        form = UserSetPasswordForm(request.POST)
        if form.is_valid():
            target.set_password(form.cleaned_data['password1'])
            target.save()
            messages.success(request, f'Password updated for {target.get_full_name() or target.username}.')
            return redirect('core:user_list')
        return render(request, 'core/user_set_password.html', {
            'form': form,
            'target': target,
        })


# ---------------------------------------------------------------------------
# Role CRUD (admin only — lives in Settings)
# ---------------------------------------------------------------------------

_ROLE_FLAGS = [
    ('can_manage_settings',        'Manage Settings'),
    ('can_manage_users',           'Manage Users'),
    ('can_view_all_tickets',       'View All Tickets'),
    ('can_create_ticket',          'Create Tickets'),
    ('can_edit_ticket',            'Edit Tickets'),
    ('can_close_tickets',          'Close/Resolve Tickets'),
    ('can_delete_ticket',          'Delete Tickets'),
    ('can_assign_ticket',          'Assign Tickets'),
    ('can_reply_internal',         'Internal Replies'),
    ('can_reply_customer',         'Customer Replies'),
    ('can_create_workorder',       'Create Work Orders'),
    ('can_edit_workorder',         'Edit Work Orders'),
    ('can_close_workorder',        'Close Work Orders'),
    ('can_view_reports',           'View Reports'),
    ('can_view_restricted_kb',     'View Restricted KB'),
    ('can_manage_kb',              'Manage KB'),
    ('can_view_device_credentials','View Device Credentials'),
]


class RoleListView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from .forms import RoleForm
        from .models import Role
        roles = Role.objects.prefetch_related('users').order_by('name')
        return render(request, 'core/role_list.html', {
            'roles': roles,
            'new_form': RoleForm(),
            'role_flags': _ROLE_FLAGS,
        })


class RoleCreateView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        from .forms import RoleForm
        form = RoleForm(request.POST)
        if form.is_valid():
            role = form.save()
            messages.success(request, f'Role "{role.name}" created.')
        else:
            messages.error(request, 'Could not create role — check the form.')
        return redirect('core:role_list')


class RoleEditView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        from .forms import RoleForm
        from .models import Role
        role = get_object_or_404(Role, pk=pk)
        return render(request, 'core/role_form.html', {
            'form': RoleForm(instance=role),
            'role': role,
            'role_flags': _ROLE_FLAGS,
        })

    def post(self, request, pk):
        from .forms import RoleForm
        from .models import Role
        role = get_object_or_404(Role, pk=pk)
        form = RoleForm(request.POST, instance=role)
        if form.is_valid():
            form.save()
            messages.success(request, f'Role "{role.name}" updated.')
            return redirect('core:role_list')
        return render(request, 'core/role_form.html', {
            'form': form,
            'role': role,
            'role_flags': _ROLE_FLAGS,
        })


class RoleDeleteView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        from .models import Role
        role = get_object_or_404(Role, pk=pk)
        if role.is_system:
            messages.error(request, f'"{role.name}" is a system role and cannot be deleted.')
        elif role.users.exists():
            messages.error(request, f'"{role.name}" has users assigned — reassign them first.')
        else:
            role.delete()
            messages.success(request, f'Role "{role.name}" deleted.')
        return redirect('core:role_list')


# ---------------------------------------------------------------------------
# Quick Labor / Work Performed
# ---------------------------------------------------------------------------

def _wp_entries_for(work_order):
    return (
        WorkPerformed.objects
        .filter(work_order=work_order)
        .select_related('labor_item', 'logged_by')
        .order_by('logged_at')
    )


class WorkPerformedLogView(LoginRequiredMixin, View):
    """HTMX: log a QuickLaborItem against a WorkOrder."""

    def post(self, request, wo_pk, item_pk):
        work_order = get_object_or_404(WorkOrder, pk=wo_pk)
        item = get_object_or_404(QuickLaborItem, pk=item_pk, is_active=True)
        WorkPerformed.objects.create(
            work_order=work_order,
            labor_item=item,
            logged_by=request.user,
        )
        return render(request, 'core/partials/work_performed.html', {
            'work_order': work_order,
            'entries': _wp_entries_for(work_order),
        })


class WorkPerformedDeleteView(LoginRequiredMixin, View):
    """HTMX: remove a logged WorkPerformed entry."""

    def post(self, request, pk):
        entry = get_object_or_404(WorkPerformed, pk=pk)
        work_order = entry.work_order
        entry.delete()
        return render(request, 'core/partials/work_performed.html', {
            'work_order': work_order,
            'entries': _wp_entries_for(work_order),
        })


class WorkPerformedUpdateView(LoginRequiredMixin, View):
    """HTMX: update label and notes on a logged WorkPerformed entry."""

    def post(self, request, pk):
        entry = get_object_or_404(WorkPerformed, pk=pk)
        entry.custom_label = request.POST.get('custom_label', '').strip()
        entry.notes = request.POST.get('notes', '').strip()
        entry.save()
        return render(request, 'core/partials/work_performed.html', {
            'work_order': entry.work_order,
            'entries': _wp_entries_for(entry.work_order),
        })


class WorkPerformedCustomLogView(LoginRequiredMixin, View):
    """HTMX: log a fully custom (free-text) work entry."""

    def post(self, request, wo_pk):
        work_order = get_object_or_404(WorkOrder, pk=wo_pk)
        label = request.POST.get('custom_label', '').strip()
        notes = request.POST.get('notes', '').strip()
        if label:
            WorkPerformed.objects.create(
                work_order=work_order,
                labor_item=None,
                custom_label=label,
                notes=notes,
                logged_by=request.user,
            )
        return render(request, 'core/partials/work_performed.html', {
            'work_order': work_order,
            'entries': _wp_entries_for(work_order),
        })


# ---------------------------------------------------------------------------
# Repair Report (print view)
# ---------------------------------------------------------------------------

class WorkOrderPrintView(LoginRequiredMixin, View):
    """Print-optimised repair report for handing to the customer."""

    def get(self, request, pk):
        work_order = get_object_or_404(
            WorkOrder.objects.select_related(
                'client', 'device', 'repair_type', 'assigned_to', 'contact'
            ),
            pk=pk,
        )
        site = SiteSettings.get()
        report_type = request.GET.get('type', 'repair')  # 'repair' or 'claim'

        # Customer-visible notes only
        notes = work_order.notes.filter(note_type='customer_visible').order_by('created_at')

        # Work performed entries grouped by category
        wp_entries = (
            WorkPerformed.objects
            .filter(work_order=work_order)
            .select_related('labor_item')
            .order_by('labor_item__category', 'labor_item__label')
        )
        categories = {}
        for entry in wp_entries:
            cat = entry.labor_item.category
            categories.setdefault(cat, []).append(entry)

        # Repair type tags
        repair_types = []
        if work_order.repair_type:
            repair_types = [work_order.repair_type]

        # Named contact: use WO contact FK, fall back to client's primary contact
        contact = work_order.contact
        if not contact:
            contact = work_order.client.contacts.filter(is_primary=True).first()

        from django.utils import timezone
        return render(request, 'core/work_order_print.html', {
            'work_order': work_order,
            'site': site,
            'notes': notes,
            'wp_categories': categories,
            'repair_types': repair_types,
            'report_type': report_type,
            'contact': contact,
            'print_date': timezone.now(),
        })


# ---------------------------------------------------------------------------
# Contact management (HTMX inline on client detail)
# ---------------------------------------------------------------------------

class ContactCreateView(LoginRequiredMixin, View):
    def post(self, request, client_pk):
        client = get_object_or_404(Client, pk=client_pk)
        form = ContactForm(request.POST)
        if form.is_valid():
            contact = form.save(commit=False)
            contact.client = client
            contact.save()
            # Handle extra phone numbers
            _save_contact_phones(request, contact)
        return redirect('core:client_detail', pk=client_pk)


class ContactUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        contact = get_object_or_404(Contact, pk=pk)
        form = ContactForm(request.POST, instance=contact)
        if form.is_valid():
            form.save()
            _save_contact_phones(request, contact)
        return redirect('core:client_detail', pk=contact.client_id)


class ContactDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        contact = get_object_or_404(Contact, pk=pk)
        client_pk = contact.client_id
        contact.delete()
        return redirect('core:client_detail', pk=client_pk)


def _save_contact_phones(request, contact):
    """Save phone numbers POSTed as phone_number[] / phone_type[] / phone_label[] arrays."""
    contact.phone_numbers.all().delete()
    numbers = request.POST.getlist('phone_number')
    types = request.POST.getlist('phone_type')
    labels = request.POST.getlist('phone_label')
    for i, number in enumerate(numbers):
        number = number.strip()
        if number:
            ContactPhone.objects.create(
                contact=contact,
                number=number,
                phone_type=types[i] if i < len(types) else 'cell',
                label=labels[i].strip() if i < len(labels) else '',
            )


class ContactSetPrimaryView(LoginRequiredMixin, View):
    def post(self, request, pk):
        contact = get_object_or_404(Contact, pk=pk)
        Contact.objects.filter(client=contact.client).update(is_primary=False)
        contact.is_primary = True
        contact.save(update_fields=['is_primary'])
        return redirect('core:client_detail', pk=contact.client_id)


# ---------------------------------------------------------------------------
# Credentials (HTMX on WO detail)
# ---------------------------------------------------------------------------

class WorkOrderCredentialsSaveView(LoginRequiredMixin, View):
    """Save device credentials inline on WO detail."""

    def post(self, request, pk):
        wo = get_object_or_404(WorkOrder, pk=pk)
        wo.device_username = request.POST.get('device_username', '').strip()
        wo.device_password = request.POST.get('device_password', '').strip()
        wo.device_pin = request.POST.get('device_pin', '').strip()
        wo.credential_notes = request.POST.get('credential_notes', '').strip()
        wo.save(update_fields=['device_username', 'device_password', 'device_pin', 'credential_notes'])
        return render(request, 'core/partials/credentials_display.html', {'work_order': wo})


class WorkOrderBillingUpdateView(LoginRequiredMixin, View):
    """HTMX: update billing state inline on WO detail."""

    def post(self, request, pk):
        from django.utils import timezone
        wo = get_object_or_404(WorkOrder, pk=pk)
        invoice, _ = Invoice.objects.get_or_create(work_order=wo)

        billing_status = request.POST.get('billing_status', '').strip()
        valid_statuses = dict(Invoice.BILLING_STATUS_CHOICES)
        if billing_status in valid_statuses:
            invoice.billing_status = billing_status

        today = timezone.now().date()
        if billing_status == 'invoiced' and not invoice.invoiced_date:
            invoice.invoiced_date = today
        if billing_status in ('paid', 'paid_direct') and not invoice.paid_date:
            invoice.paid_date = today

        if request.POST.get('full_edit'):
            amount = request.POST.get('amount', '').strip()
            invoice.amount = amount if amount else None

            invoiced_date = request.POST.get('invoiced_date', '').strip()
            invoice.invoiced_date = invoiced_date if invoiced_date else None

            paid_date = request.POST.get('paid_date', '').strip()
            invoice.paid_date = paid_date if paid_date else None

            payment_method = request.POST.get('payment_method', '').strip()
            if payment_method in dict(Invoice.PAYMENT_METHOD_CHOICES) or payment_method == '':
                invoice.payment_method = payment_method

            invoice.notes = request.POST.get('notes', '').strip()

        invoice.save()
        return render(request, 'core/partials/billing_card.html', {
            'work_order': wo,
            'invoice': invoice,
        })


# ---------------------------------------------------------------------------
# Native Settings UI (/settings/)
# ---------------------------------------------------------------------------

SETTINGS_TABS = [
    ('company',      'Company',        CompanySettingsForm),
    ('outbound',     'Outbound Email', OutboundEmailSettingsForm),
    ('inbound',      'Inbound Email',  InboundEmailSettingsForm),
    ('attachments',  'Attachments',    AttachmentSettingsForm),
    ('security',     'Security',       SecuritySettingsForm),
    ('mileage',      'Mileage',        MileageSettingsForm),
    ('repair_types',     'Repair Types',     None),
    ('canned_responses', 'Canned Responses', None),
    ('quick_labor',      'Quick Labor',      None),
    ('checklist_items',  'Checklist Items',  None),
    ('colors',           'Colors',           ColorSettingsForm),
    ('display',          'Display',          None),
    ('credentials',      'Credentials',      None),
    ('email_templates',  'Email Templates',  None),
    ('statuses',         'Statuses',         None),
    ('kb_categories',    'KB Categories',    None),
    ('sla_plans',        'SLA Plans',        None),
    ('help_topics',      'Help Topics',      None),
    ('tech_skills',      'Tech Skills',      None),
    ('dashboard_tiles',  'Dashboard Tiles',  None),
    ('custom_fields',    'Custom Fields',    None),
    ('users',            'Users',            None),
    ('roles',            'Roles',            None),
    ('logs',             'Logs',             None),
]

SETTINGS_NAV_TABS = [(key, label) for key, label, _ in SETTINGS_TABS]


def _repair_types_context():
    categories = RepairTypeCategory.objects.prefetch_related(
        'repair_types'
    ).order_by('sort_order', 'name')
    uncategorised = RepairType.objects.filter(
        category__isnull=True, is_active=True
    ).order_by('sort_order', 'name')
    return {'rt_categories': categories, 'rt_uncategorised': uncategorised}


class SuppressedAddressAddView(LoginRequiredMixin, View):
    def post(self, request):
        if not request.user.is_staff:
            return HttpResponse('Forbidden', status=403)
        email = request.POST.get('email', '').strip().lower()
        reason = request.POST.get('reason', '').strip()
        if email:
            SuppressedAddress.objects.get_or_create(email=email, defaults={'reason': reason})
        return redirect(f"{reverse('core:settings')}?tab=outbound")


class SuppressedAddressDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_staff:
            return HttpResponse('Forbidden', status=403)
        SuppressedAddress.objects.filter(pk=pk).delete()
        return redirect(f"{reverse('core:settings')}?tab=outbound")


class EmailTestOutboundView(LoginRequiredMixin, View):
    """HTMX: send a test email using saved SMTP settings."""

    def post(self, request):
        import smtplib, ssl
        from email.mime.text import MIMEText
        s = SiteSettings.get()
        to_addr = request.POST.get('to', '').strip()
        if not to_addr:
            return HttpResponse('<p class="text-red-600 text-sm mt-2">Please enter a recipient address.</p>')
        if not s.email_host or not s.email_username:
            return HttpResponse('<p class="text-red-600 text-sm mt-2">SMTP settings not configured.</p>')
        try:
            msg = MIMEText("This is a test email from Murphy's Bench. Your outbound email is working correctly.")
            msg['Subject'] = "Murphy's Bench — Outbound Email Test"
            msg['From'] = s.email_from or s.email_username
            msg['To'] = to_addr
            ctx = ssl.create_default_context()
            # Port 465 = implicit SSL (SMTP_SSL); port 587 = STARTTLS
            if s.email_port == 465:
                with smtplib.SMTP_SSL(s.email_host, s.email_port, context=ctx, timeout=10) as server:
                    server.login(s.email_username, s.email_password)
                    server.sendmail(msg['From'], [to_addr], msg.as_string())
            elif s.email_use_tls:
                with smtplib.SMTP(s.email_host, s.email_port, timeout=10) as server:
                    server.starttls(context=ctx)
                    server.login(s.email_username, s.email_password)
                    server.sendmail(msg['From'], [to_addr], msg.as_string())
            else:
                with smtplib.SMTP(s.email_host, s.email_port, timeout=10) as server:
                    if s.email_password:
                        server.login(s.email_username, s.email_password)
                    server.sendmail(msg['From'], [to_addr], msg.as_string())
            return HttpResponse(f'<p class="text-green-600 text-sm mt-2">✓ Test email sent to {to_addr}.</p>')
        except Exception as e:
            return HttpResponse(f'<p class="text-red-600 text-sm mt-2">✗ Failed: {e}</p>')


class EmailTestInboundView(LoginRequiredMixin, View):
    """HTMX: test inbound email connection using saved IMAP/POP3 settings."""

    def post(self, request):
        import imaplib, poplib, ssl
        s = SiteSettings.get()
        if not s.inbound_host or not s.inbound_username:
            return HttpResponse('<p class="text-red-600 text-sm mt-2">Inbound settings not configured.</p>')
        try:
            if s.inbound_protocol == 'imap':
                if s.inbound_ssl:
                    conn = imaplib.IMAP4_SSL(s.inbound_host, s.inbound_port, timeout=10)
                else:
                    conn = imaplib.IMAP4(s.inbound_host, s.inbound_port)
                conn.login(s.inbound_username, s.inbound_password)
                status, msgs = conn.select(s.inbound_folder or 'INBOX', readonly=True)
                count = msgs[0].decode() if msgs else '?'
                conn.logout()
                return HttpResponse(f'<p class="text-green-600 text-sm mt-2">✓ Connected. {count} message(s) in {s.inbound_folder or "INBOX"}.</p>')
            else:  # pop3
                if s.inbound_ssl:
                    conn = poplib.POP3_SSL(s.inbound_host, s.inbound_port)
                else:
                    conn = poplib.POP3(s.inbound_host, s.inbound_port)
                conn.user(s.inbound_username)
                conn.pass_(s.inbound_password)
                count = len(conn.list()[1])
                conn.quit()
                return HttpResponse(f'<p class="text-green-600 text-sm mt-2">✓ Connected. {count} message(s) in mailbox.</p>')
        except Exception as e:
            return HttpResponse(f'<p class="text-red-600 text-sm mt-2">✗ Failed: {e}</p>')


class SettingsView(LoginRequiredMixin, View):
    """Native settings UI — admin/can_manage_settings only."""

    def _check_permission(self, request):
        if not (request.user.is_staff or (
            hasattr(request.user, 'role_obj') and request.user.role_obj and
            request.user.role_obj.can_manage_settings
        )):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

    def get(self, request):
        self._check_permission(request)
        site = SiteSettings.get()
        active_tab = request.GET.get('tab', 'company')
        forms_map = {
            key: FormClass(instance=site, prefix=key)
            for key, _, FormClass in SETTINGS_TABS if FormClass
        }
        ctx = {
            'settings': site,
            'active_tab': active_tab,
            'tabs': SETTINGS_NAV_TABS,
            'forms': forms_map,
        }
        if active_tab == 'repair_types':
            ctx.update(_repair_types_context())
        if active_tab == 'canned_responses':
            ctx.update(_canned_responses_context())
        if active_tab == 'quick_labor':
            ctx.update(_quick_labor_context())
        if active_tab == 'checklist_items':
            ctx.update(_checklist_items_context())
        if active_tab == 'colors':
            ctx.update(_colors_context(forms_map.get('colors')))
        if active_tab == 'display':
            ctx.update(_display_context())
        if active_tab == 'credentials':
            ctx['credentials'] = OrgCredential.objects.all()
            ctx['credential_categories'] = OrgCredential.CATEGORY_CHOICES
        if active_tab == 'statuses':
            ctx['ticket_statuses'] = StatusDefinition.objects.filter(entity_type='ticket').order_by('sort_order')
            ctx['wo_statuses'] = StatusDefinition.objects.filter(entity_type='workorder').order_by('sort_order')
        if active_tab == 'kb_categories':
            ctx.update(_kb_categories_context())
        if active_tab == 'outbound':
            ctx['suppressed_addresses'] = SuppressedAddress.objects.all()
        if active_tab == 'inbound':
            ctx['blocked_senders'] = BlockedSender.objects.all()
        if active_tab == 'sla_plans':
            ctx['sla_plans'] = SLAPlan.objects.all()
        if active_tab == 'help_topics':
            ctx['help_topics'] = HelpTopic.objects.all()
            ctx['sla_plans_all'] = SLAPlan.objects.filter(is_active=True)
        if active_tab == 'tech_skills':
            ctx['tech_skills'] = TechSkill.objects.all()
        if active_tab == 'dashboard_tiles':
            ctx['dashboard_tiles'] = DashboardTile.objects.all()
        if active_tab == 'custom_fields':
            ctx['custom_fields'] = CustomField.objects.prefetch_related('choices').all()
            ctx['help_topics_all'] = HelpTopic.objects.filter(is_active=True)
            ctx['repair_types_all'] = RepairType.objects.filter(is_active=True)
        if active_tab == 'logs':
            from .models import EmailSendLog, InboundEmailLog
            from auditlog.models import LogEntry
            ctx['email_send_logs'] = EmailSendLog.objects.select_related('ticket').order_by('-created_at')[:200]
            ctx['inbound_logs'] = InboundEmailLog.objects.select_related('ticket').order_by('-created_at')[:200]
            ctx['credential_logs'] = CredentialAccessLog.objects.select_related('credential', 'user').order_by('-accessed_at')[:200]
            ctx['device_cred_logs'] = DeviceCredentialAccessLog.objects.select_related('device', 'user').order_by('-accessed_at')[:200]
            ctx['audit_log_entries'] = LogEntry.objects.select_related('actor', 'content_type').order_by('-timestamp')[:200]
        if active_tab == 'email_templates':
            # Ensure all 4 trigger templates exist
            for trigger, _ in EmailTemplate.TRIGGER_CHOICES:
                EmailTemplate.objects.get_or_create(
                    trigger=trigger,
                    defaults={
                        'subject_template': f'[{{{{ ticket.ticket_number }}}}] {{{{ ticket.subject }}}}',
                        'body_template': f'Hi {{{{ client.name }}}},\n\nYour ticket {{{{ ticket.ticket_number }}}} has been updated.\n\nThank you,\n{{{{ tech_name }}}}',
                        'is_active': False,
                    }
                )
            ctx['email_templates'] = EmailTemplate.objects.all()
        return render(request, 'core/settings.html', ctx)

    def post(self, request):
        self._check_permission(request)
        site = SiteSettings.get()
        tab = request.POST.get('tab', 'company')
        FormClass = next((fc for key, _, fc in SETTINGS_TABS if key == tab), None)
        if FormClass is None:
            return redirect(f"{request.path}?tab={tab}")

        form = FormClass(request.POST, request.FILES, instance=site, prefix=tab)
        if form.is_valid():
            form.save()
            messages.success(request, 'Settings saved.')
            return redirect(f"{request.path}?tab={tab}")

        forms_map = {}
        for key, _, FC in SETTINGS_TABS:
            if not FC:
                continue
            forms_map[key] = form if key == tab else FC(instance=site, prefix=key)
        ctx = {
            'settings': site,
            'active_tab': tab,
            'tabs': SETTINGS_NAV_TABS,
            'forms': forms_map,
        }
        if tab == 'repair_types':
            ctx.update(_repair_types_context())
        if tab == 'canned_responses':
            ctx.update(_canned_responses_context())
        if tab == 'quick_labor':
            ctx.update(_quick_labor_context())
        if tab == 'checklist_items':
            ctx.update(_checklist_items_context())
        if tab == 'colors':
            ctx.update(_colors_context(forms_map.get('colors') or form))
        if tab == 'display':
            ctx.update(_display_context())
        return render(request, 'core/settings.html', ctx)

# ---------------------------------------------------------------------------
# Settings — Repair Types CRUD
# ---------------------------------------------------------------------------

REPAIR_TYPES_REDIRECT = 'core:settings'
REPAIR_TYPES_TAB = '?tab=repair_types'

# ---------------------------------------------------------------------------
# Settings — KB Categories CRUD
# ---------------------------------------------------------------------------

KB_CAT_REDIRECT = 'core:settings'
KB_CAT_TAB = '?tab=kb_categories'


def _kb_categories_context():
    return {'kb_categories': KBCategory.objects.order_by('sort_order', 'name')}


class KBCategoryCreateView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        desc = request.POST.get('description', '').strip()
        if name:
            max_order = KBCategory.objects.aggregate(m=models_Max('sort_order'))['m'] or 0
            KBCategory.objects.get_or_create(name=name, defaults={'description': desc, 'sort_order': max_order + 10})
        return redirect(reverse_lazy(KB_CAT_REDIRECT) + KB_CAT_TAB)


class KBCategoryUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        cat = get_object_or_404(KBCategory, pk=pk)
        name = request.POST.get('name', '').strip()
        if name:
            cat.name = name
            cat.description = request.POST.get('description', '').strip()
            cat.save(update_fields=['name', 'description'])
        return redirect(reverse_lazy(KB_CAT_REDIRECT) + KB_CAT_TAB)


class KBCategoryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        cat = get_object_or_404(KBCategory, pk=pk)
        KBArticle.objects.filter(category=cat).update(category=None)
        cat.delete()
        return redirect(reverse_lazy(KB_CAT_REDIRECT) + KB_CAT_TAB)


class RepairTypeCategoryCreateView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        if name:
            from .models import RepairTypeCategory
            max_order = RepairTypeCategory.objects.aggregate(
                m=models_Max('sort_order')
            )['m'] or 0
            RepairTypeCategory.objects.get_or_create(
                name=name, defaults={'sort_order': max_order + 10}
            )
        return redirect(reverse_lazy(REPAIR_TYPES_REDIRECT) + REPAIR_TYPES_TAB)


class RepairTypeCategoryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        cat = get_object_or_404(RepairTypeCategory, pk=pk)
        RepairType.objects.filter(category=cat).update(category=None)
        cat.delete()
        return redirect(reverse_lazy(REPAIR_TYPES_REDIRECT) + REPAIR_TYPES_TAB)


class RepairTypeCategoryReorderView(LoginRequiredMixin, View):
    def post(self, request, pk):
        direction = request.POST.get('direction')
        cat = get_object_or_404(RepairTypeCategory, pk=pk)
        cats = list(RepairTypeCategory.objects.order_by('sort_order', 'name'))
        idx = next((i for i, c in enumerate(cats) if c.pk == cat.pk), None)
        if idx is None:
            return redirect(reverse_lazy(REPAIR_TYPES_REDIRECT) + REPAIR_TYPES_TAB)
        swap = idx - 1 if direction == 'up' else idx + 1
        if 0 <= swap < len(cats):
            cats[idx].sort_order, cats[swap].sort_order = cats[swap].sort_order, cats[idx].sort_order
            if cats[idx].sort_order == cats[swap].sort_order:
                cats[idx].sort_order = swap * 10
                cats[swap].sort_order = idx * 10
            cats[idx].save(update_fields=['sort_order'])
            cats[swap].save(update_fields=['sort_order'])
        return redirect(reverse_lazy(REPAIR_TYPES_REDIRECT) + REPAIR_TYPES_TAB)


class RepairTypeCreateView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        cat_id = request.POST.get('category_id') or None
        if name:
            cat = RepairTypeCategory.objects.filter(pk=cat_id).first() if cat_id else None
            RepairType.objects.get_or_create(name=name, defaults={'category': cat, 'is_active': True})
        return redirect(reverse_lazy(REPAIR_TYPES_REDIRECT) + REPAIR_TYPES_TAB)


class RepairTypeUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        rt = get_object_or_404(RepairType, pk=pk)
        name = request.POST.get('name', '').strip()
        cat_id = request.POST.get('category_id') or None
        if name:
            rt.name = name
            rt.category = RepairTypeCategory.objects.filter(pk=cat_id).first() if cat_id else None
            rt.is_active = request.POST.get('is_active') == '1'
            rt.save(update_fields=['name', 'category', 'is_active'])
        return redirect(reverse_lazy(REPAIR_TYPES_REDIRECT) + REPAIR_TYPES_TAB)


class RepairTypeDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        rt = get_object_or_404(RepairType, pk=pk)
        rt.delete()
        return redirect(reverse_lazy(REPAIR_TYPES_REDIRECT) + REPAIR_TYPES_TAB)

# ---------------------------------------------------------------------------
# Settings — Canned Responses CRUD
# ---------------------------------------------------------------------------

CANNED_REDIRECT = 'core:settings'
CANNED_TAB = '?tab=canned_responses'


def _canned_responses_context():
    cr_streams = []
    for stream_key, stream_label in [('customer', 'Customer Notes'), ('internal', 'Tech Notes (Internal)')]:
        cats = CannedResponseCategory.objects.filter(
            stream=stream_key
        ).prefetch_related('responses').order_by('sort_order', 'name')
        uncategorised = CannedResponse.objects.filter(
            stream=stream_key, category__isnull=True
        ).order_by('sort_order', 'label')
        cr_streams.append((stream_key, stream_label, cats, uncategorised))
    return {'cr_streams': cr_streams}


class CannedResponseCategoryCreateView(LoginRequiredMixin, View):
    def post(self, request):
        stream = request.POST.get('stream', 'customer')
        name = request.POST.get('name', '').strip()
        if name and stream in ('customer', 'internal'):
            CannedResponseCategory.objects.create(stream=stream, name=name)
        return redirect(reverse_lazy(CANNED_REDIRECT) + CANNED_TAB)


class CannedResponseCategoryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        cat = get_object_or_404(CannedResponseCategory, pk=pk)
        # reassign orphaned responses to no category before deleting
        cat.responses.update(category=None)
        cat.delete()
        return redirect(reverse_lazy(CANNED_REDIRECT) + CANNED_TAB)


class CannedResponseCategoryReorderView(LoginRequiredMixin, View):
    def post(self, request, pk):
        direction = request.POST.get('direction')
        cat = get_object_or_404(CannedResponseCategory, pk=pk)
        siblings = list(
            CannedResponseCategory.objects.filter(stream=cat.stream).order_by('sort_order', 'name')
        )
        idx = next((i for i, c in enumerate(siblings) if c.pk == cat.pk), None)
        if direction == 'up' and idx and idx > 0:
            siblings[idx - 1], siblings[idx] = siblings[idx], siblings[idx - 1]
        elif direction == 'down' and idx is not None and idx < len(siblings) - 1:
            siblings[idx + 1], siblings[idx] = siblings[idx], siblings[idx + 1]
        for i, c in enumerate(siblings):
            c.sort_order = i
            c.save(update_fields=['sort_order'])
        return redirect(reverse_lazy(CANNED_REDIRECT) + CANNED_TAB)


class CannedResponseCreateView(LoginRequiredMixin, View):
    def post(self, request):
        stream = request.POST.get('stream', 'customer')
        label = request.POST.get('label', '').strip()
        body = request.POST.get('body', '').strip()
        cat_id = request.POST.get('category_id') or None
        category = None
        if cat_id:
            category = CannedResponseCategory.objects.filter(pk=cat_id, stream=stream).first()
        if label and body and stream in ('customer', 'internal'):
            CannedResponse.objects.create(
                stream=stream, label=label, body=body, category=category
            )
        return redirect(reverse_lazy(CANNED_REDIRECT) + CANNED_TAB)


class CannedResponseUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        cr = get_object_or_404(CannedResponse, pk=pk)
        label = request.POST.get('label', '').strip()
        body = request.POST.get('body', '').strip()
        cat_id = request.POST.get('category_id') or None
        category = None
        if cat_id:
            category = CannedResponseCategory.objects.filter(pk=cat_id, stream=cr.stream).first()
        if label and body:
            cr.label = label
            cr.body = body
            cr.category = category
            cr.save(update_fields=['label', 'body', 'category'])
        return redirect(reverse_lazy(CANNED_REDIRECT) + CANNED_TAB)


class CannedResponseDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        cr = get_object_or_404(CannedResponse, pk=pk)
        cr.delete()
        return redirect(reverse_lazy(CANNED_REDIRECT) + CANNED_TAB)


class CannedResponsePickerView(LoginRequiredMixin, View):
    """Returns JSON list of canned responses for a given stream (HTMX/fetch)."""
    def get(self, request):
        from django.http import JsonResponse
        stream = request.GET.get('stream', 'customer')
        cats = CannedResponseCategory.objects.filter(
            stream=stream
        ).prefetch_related('responses').order_by('sort_order', 'name')
        uncategorised = CannedResponse.objects.filter(
            stream=stream, category__isnull=True
        ).order_by('sort_order', 'label')

        result = []
        for cat in cats:
            items = list(cat.responses.order_by('sort_order', 'label').values('id', 'label', 'body'))
            if items:
                result.append({'category': cat.name, 'items': items})
        if uncategorised.exists():
            result.append({
                'category': 'Uncategorised',
                'items': list(uncategorised.values('id', 'label', 'body'))
            })
        return JsonResponse({'groups': result})

# ---------------------------------------------------------------------------
# Settings — Quick Labor CRUD
# ---------------------------------------------------------------------------

QL_REDIRECT = 'core:settings'
QL_TAB = '?tab=quick_labor'

QUICK_LABOR_CATEGORIES = ['Software', 'Hardware', 'Data', 'Maintenance', 'General']


def _quick_labor_context():
    from .models import QuickLaborItem
    items = QuickLaborItem.objects.order_by('category', 'sort_order', 'label')
    grouped = {}
    for cat in QUICK_LABOR_CATEGORIES:
        grouped[cat] = [i for i in items if i.category == cat]
    # catch any items with unknown categories
    known = set(QUICK_LABOR_CATEGORIES)
    other = [i for i in items if i.category not in known]
    return {
        'ql_grouped': grouped,
        'ql_categories': QUICK_LABOR_CATEGORIES,
        'ql_other': other,
    }


class QuickLaborCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .models import QuickLaborItem
        label = request.POST.get('label', '').strip()
        category = request.POST.get('category', '').strip()
        print_description = request.POST.get('print_description', '').strip()
        if label and category:
            QuickLaborItem.objects.create(
                label=label,
                category=category,
                print_description=print_description,
            )
        return redirect(reverse_lazy(QL_REDIRECT) + QL_TAB)


class QuickLaborUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from .models import QuickLaborItem
        item = get_object_or_404(QuickLaborItem, pk=pk)
        label = request.POST.get('label', '').strip()
        category = request.POST.get('category', '').strip()
        print_description = request.POST.get('print_description', '').strip()
        is_active = request.POST.get('is_active') == '1'
        if label and category:
            item.label = label
            item.category = category
            item.print_description = print_description
            item.is_active = is_active
            item.save(update_fields=['label', 'category', 'print_description', 'is_active'])
        return redirect(reverse_lazy(QL_REDIRECT) + QL_TAB)


class QuickLaborDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from .models import QuickLaborItem
        item = get_object_or_404(QuickLaborItem, pk=pk)
        item.delete()
        return redirect(reverse_lazy(QL_REDIRECT) + QL_TAB)

# ---------------------------------------------------------------------------
# Settings — Checklist Items CRUD
# ---------------------------------------------------------------------------

CLI_REDIRECT = 'core:settings'
CLI_TAB = '?tab=checklist_items'

DEVICE_TYPE_CHOICES = [
    ('laptop', 'Laptop'),
    ('desktop', 'Desktop'),
    ('server', 'Server'),
    ('mobile', 'Mobile Phone'),
    ('tablet', 'Tablet'),
    ('printer', 'Printer'),
    ('other', 'Other'),
]


def _checklist_items_context():
    items = ChecklistItem.objects.order_by('sort_order', 'name')
    return {
        'cli_items': items,
        'cli_device_types': DEVICE_TYPE_CHOICES,
    }


_STATUS_COLOR_ROWS = [
    ('new',         'New',         'color_status_new',         '#dbeafe'),
    ('assigned',    'Assigned',    'color_status_assigned',    '#ede9fe'),
    ('in_progress', 'In Progress', 'color_status_in_progress', '#fef9c3'),
    ('completed',   'Completed',   'color_status_completed',   '#dcfce7'),
    ('closed',      'Closed',      'color_status_closed',      '#f3f4f6'),
    ('cancelled',   'Cancelled',   'color_status_cancelled',   '#fee2e2'),
]


def _colors_context(form):
    """Build color_status_rows with current values from the bound/unbound form."""
    rows = []
    for status_key, status_label, field_name, default_hex in _STATUS_COLOR_ROWS:
        if form:
            field = form[field_name]
            current = field.value() or default_hex
        else:
            current = default_hex
        rows.append((status_key, status_label, field_name, current))
    return {'color_status_rows': rows}


def _display_context():
    font_sizes = [('11px','11'), ('12px','12'), ('13px','13'), ('14px','14'), ('15px','15'), ('16px','16'), ('18px','18')]
    nav_sizes  = [('11px','11'), ('12px','12'), ('13px','13'), ('14px','14'), ('15px','15'), ('16px','16')]
    return {
        'display_font_sizes': font_sizes,
        'display_nav_sizes':  nav_sizes,
    }


class ChecklistItemCreateView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        device_types = request.POST.getlist('device_types')
        if name:
            ChecklistItem.objects.create(name=name, device_types=device_types)
        return redirect(reverse_lazy(CLI_REDIRECT) + CLI_TAB)


class ChecklistItemUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(ChecklistItem, pk=pk)
        name = request.POST.get('name', '').strip()
        device_types = request.POST.getlist('device_types')
        is_active = request.POST.get('is_active') == '1'
        if name:
            item.name = name
            item.device_types = device_types
            item.is_active = is_active
            item.save(update_fields=['name', 'device_types', 'is_active'])
        return redirect(reverse_lazy(CLI_REDIRECT) + CLI_TAB)


class ChecklistItemDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(ChecklistItem, pk=pk)
        item.delete()
        return redirect(reverse_lazy(CLI_REDIRECT) + CLI_TAB)


# --- Org Credentials Vault ---

CRED_REDIRECT = 'core:settings'
CRED_TAB = '?tab=credentials'


class OrgCredentialCreateView(LoginRequiredMixin, View):
    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        name     = request.POST.get('name', '').strip()
        category = request.POST.get('category', 'other')
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        url      = request.POST.get('url', '').strip()
        notes    = request.POST.get('notes', '').strip()
        admin_only = request.POST.get('admin_only') == '1'
        if name:
            cred = OrgCredential.objects.create(
                name=name, category=category, username=username,
                password=password, url=url, notes=notes,
                admin_only=admin_only, created_by=request.user,
            )
            CredentialAccessLog.objects.create(credential=cred, user=request.user, action='edited')
        return redirect(reverse_lazy(CRED_REDIRECT) + CRED_TAB)


class OrgCredentialUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        cred = get_object_or_404(OrgCredential, pk=pk)
        cred.name      = request.POST.get('name', cred.name).strip()
        cred.category  = request.POST.get('category', cred.category)
        cred.username  = request.POST.get('username', '').strip()
        cred.url       = request.POST.get('url', '').strip()
        cred.notes     = request.POST.get('notes', '').strip()
        cred.admin_only = request.POST.get('admin_only') == '1'
        new_pw = request.POST.get('password', '').strip()
        if new_pw:
            cred.password = new_pw
        cred.save()
        CredentialAccessLog.objects.create(credential=cred, user=request.user, action='edited')
        return redirect(reverse_lazy(CRED_REDIRECT) + CRED_TAB)


class OrgCredentialDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        cred = get_object_or_404(OrgCredential, pk=pk)
        CredentialAccessLog.objects.create(credential=cred, user=request.user, action='deleted')
        cred.delete()
        return redirect(reverse_lazy(CRED_REDIRECT) + CRED_TAB)


class OrgCredentialRevealView(LoginRequiredMixin, View):
    """HTMX: return plaintext credential value and log the access."""
    def get(self, request, pk, field):
        cred = get_object_or_404(OrgCredential, pk=pk)
        if cred.admin_only and not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if field == 'password':
            value = cred.password
        elif field == 'username':
            value = cred.username
        else:
            return HttpResponse('', status=400)
        CredentialAccessLog.objects.create(credential=cred, user=request.user, action='viewed')
        return HttpResponse(value or '(empty)', content_type='text/plain')


# --- Device Credentials ---

def _can_view_device_creds(user):
    return _is_admin(user) or user.has_perm_flag('can_view_device_credentials')


class DeviceCredentialRevealView(LoginRequiredMixin, View):
    """HTMX: return plaintext device credential value and log the access."""
    def get(self, request, pk, field):
        if not _can_view_device_creds(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        device = get_object_or_404(Device, pk=pk)
        if field == 'username':
            value = device.device_username
        elif field == 'password':
            value = device.device_password
        else:
            return HttpResponse('', status=400)
        DeviceCredentialAccessLog.objects.create(device=device, user=request.user, action='viewed', field=field)
        return HttpResponse(value or '(empty)', content_type='text/plain')


class DeviceCredentialUpdateView(LoginRequiredMixin, View):
    """HTMX: save device credentials (admin only). Returns updated credential card."""
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        device = get_object_or_404(Device, pk=pk)
        device.device_username = request.POST.get('device_username', '').strip()
        new_pw = request.POST.get('device_password', '').strip()
        if new_pw:
            device.device_password = new_pw
        device.credential_notes = request.POST.get('credential_notes', '').strip()
        device.save(update_fields=['device_username', 'device_password', 'credential_notes', 'updated_at'])
        DeviceCredentialAccessLog.objects.create(device=device, user=request.user, action='edited', field='all')
        is_admin = _is_admin(request.user)
        can_view = _can_view_device_creds(request.user)
        has_creds = bool(device.device_username or device.device_password or device.credential_notes)
        return render(request, 'core/partials/device_credential_card.html', {
            'device': device, 'is_admin': is_admin, 'can_view': can_view, 'has_creds': has_creds,
        })


# --- Email Template Manager ---

class EmailTemplateUpdateView(LoginRequiredMixin, View):
    """Settings: update a single email template's subject, body, and active state."""

    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        tmpl = get_object_or_404(EmailTemplate, pk=pk)
        tmpl.subject_template = request.POST.get('subject_template', tmpl.subject_template).strip()
        tmpl.body_template = request.POST.get('body_template', tmpl.body_template).strip()
        tmpl.is_active = request.POST.get('is_active') == '1'
        tmpl.save()
        messages.success(request, f'Email template "{tmpl.get_trigger_display()}" saved.')
        return redirect(reverse_lazy('core:settings') + '?tab=email_templates')


# --- Status Management ---

STATUS_REDIRECT = 'core:settings'
STATUS_TAB = '?tab=statuses'


class StatusDefinitionCreateView(LoginRequiredMixin, View):
    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        entity_type = request.POST.get('entity_type', '').strip()
        slug = request.POST.get('slug', '').strip().lower().replace(' ', '_')
        label = request.POST.get('label', '').strip()
        color = request.POST.get('color', '#E5E7EB').strip()
        if entity_type and slug and label:
            StatusDefinition.objects.get_or_create(
                entity_type=entity_type, slug=slug,
                defaults=dict(label=label, color=color, is_system=False, sort_order=100),
            )
            from .templatetags.mb_icons import invalidate_status_cache
            invalidate_status_cache()
        return redirect(reverse_lazy(STATUS_REDIRECT) + STATUS_TAB)


class StatusDefinitionUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        sd = get_object_or_404(StatusDefinition, pk=pk)
        sd.label = request.POST.get('label', sd.label).strip()
        sd.color = request.POST.get('color', sd.color).strip()
        sd.save()
        from .templatetags.mb_icons import invalidate_status_cache
        invalidate_status_cache()
        return redirect(reverse_lazy(STATUS_REDIRECT) + STATUS_TAB)


class StatusDefinitionDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        sd = get_object_or_404(StatusDefinition, pk=pk)
        if sd.is_system:
            messages.error(request, 'System statuses cannot be deleted.')
        else:
            sd.delete()
            from .templatetags.mb_icons import invalidate_status_cache
            invalidate_status_cache()
        return redirect(reverse_lazy(STATUS_REDIRECT) + STATUS_TAB)


# ---------------------------------------------------------------------------
# Blocked Senders (inbound email filter)
# ---------------------------------------------------------------------------

class BlockedSenderAddView(LoginRequiredMixin, View):
    def post(self, request):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        pattern = request.POST.get('pattern', '').strip().lower()
        reason = request.POST.get('reason', '').strip()
        if pattern:
            BlockedSender.objects.get_or_create(pattern=pattern, defaults={'reason': reason})
        return redirect(f"{reverse('core:settings')}?tab=inbound")


class BlockedSenderDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        BlockedSender.objects.filter(pk=pk).delete()
        return redirect(f"{reverse('core:settings')}?tab=inbound")


# ---------------------------------------------------------------------------
# SLA Plans
# ---------------------------------------------------------------------------

_SLA_REDIRECT = 'core:settings'
_SLA_TAB = '?tab=sla_plans'


class SLAPlanCreateView(LoginRequiredMixin, View):
    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        name = request.POST.get('name', '').strip()
        grace = request.POST.get('grace_period_hours', '24').strip()
        is_active = request.POST.get('is_active') == 'on'
        is_transient = request.POST.get('is_transient') == 'on'
        disable_alerts = request.POST.get('disable_overdue_alerts') == 'on'
        if name:
            try:
                grace_int = int(grace)
            except ValueError:
                grace_int = 24
            SLAPlan.objects.get_or_create(name=name, defaults=dict(
                grace_period_hours=grace_int,
                is_active=is_active,
                is_transient=is_transient,
                disable_overdue_alerts=disable_alerts,
            ))
        return redirect(reverse_lazy(_SLA_REDIRECT) + _SLA_TAB)


class SLAPlanUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        plan = get_object_or_404(SLAPlan, pk=pk)
        plan.name = request.POST.get('name', plan.name).strip()
        try:
            plan.grace_period_hours = int(request.POST.get('grace_period_hours', plan.grace_period_hours))
        except ValueError:
            pass
        plan.is_active = request.POST.get('is_active') == 'on'
        plan.is_transient = request.POST.get('is_transient') == 'on'
        plan.disable_overdue_alerts = request.POST.get('disable_overdue_alerts') == 'on'
        plan.save()
        return redirect(reverse_lazy(_SLA_REDIRECT) + _SLA_TAB)


class SLAPlanDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        plan = get_object_or_404(SLAPlan, pk=pk)
        if plan.help_topics.exists():
            messages.error(request, f'Cannot delete "{plan.name}" — it is referenced by help topics.')
        else:
            plan.delete()
        return redirect(reverse_lazy(_SLA_REDIRECT) + _SLA_TAB)


# ---------------------------------------------------------------------------
# Help Topics
# ---------------------------------------------------------------------------

_HT_REDIRECT = 'core:settings'
_HT_TAB = '?tab=help_topics'


class HelpTopicCreateView(LoginRequiredMixin, View):
    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        sla_id = request.POST.get('default_sla') or None
        is_active = request.POST.get('is_active') == 'on'
        sort_order = request.POST.get('sort_order', '0').strip()
        if name:
            try:
                sort_int = int(sort_order)
            except ValueError:
                sort_int = 0
            sla = SLAPlan.objects.filter(pk=sla_id).first() if sla_id else None
            HelpTopic.objects.get_or_create(name=name, defaults=dict(
                description=description,
                default_sla=sla,
                is_active=is_active,
                sort_order=sort_int,
            ))
        return redirect(reverse_lazy(_HT_REDIRECT) + _HT_TAB)


class HelpTopicUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        topic = get_object_or_404(HelpTopic, pk=pk)
        topic.name = request.POST.get('name', topic.name).strip()
        topic.description = request.POST.get('description', '').strip()
        sla_id = request.POST.get('default_sla') or None
        topic.default_sla = SLAPlan.objects.filter(pk=sla_id).first() if sla_id else None
        topic.is_active = request.POST.get('is_active') == 'on'
        try:
            topic.sort_order = int(request.POST.get('sort_order', topic.sort_order))
        except ValueError:
            pass
        topic.save()
        return redirect(reverse_lazy(_HT_REDIRECT) + _HT_TAB)


class HelpTopicDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        topic = get_object_or_404(HelpTopic, pk=pk)
        if topic.tickets.exists():
            messages.error(request, f'Cannot delete "{topic.name}" — it is used by existing tickets.')
        else:
            topic.delete()
        return redirect(reverse_lazy(_HT_REDIRECT) + _HT_TAB)


# ---------------------------------------------------------------------------
# Tech Skills
# ---------------------------------------------------------------------------

_TS_REDIRECT = 'core:settings'
_TS_TAB = '?tab=tech_skills'


class TechSkillCreateView(LoginRequiredMixin, View):
    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if name:
            TechSkill.objects.get_or_create(name=name, defaults={'description': description})
        return redirect(reverse_lazy(_TS_REDIRECT) + _TS_TAB)


class TechSkillDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        TechSkill.objects.filter(pk=pk).delete()
        return redirect(reverse_lazy(_TS_REDIRECT) + _TS_TAB)


# ---------------------------------------------------------------------------
# Dashboard Tiles
# ---------------------------------------------------------------------------

_DT_REDIRECT = 'core:settings'
_DT_TAB = '?tab=dashboard_tiles'


class DashboardTileUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        import json as _json
        tile = get_object_or_404(DashboardTile, pk=pk)
        tile.label = request.POST.get('label', tile.label).strip()
        tile.visible_to = request.POST.get('visible_to', tile.visible_to)
        tile.is_active = request.POST.get('is_active') == 'on'
        try:
            tile.sort_order = int(request.POST.get('sort_order', tile.sort_order))
        except ValueError:
            pass
        raw_filter = request.POST.get('status_filter', '').strip()
        if raw_filter:
            try:
                tile.status_filter = _json.loads(raw_filter)
            except Exception:
                tile.status_filter = [s.strip() for s in raw_filter.split(',') if s.strip()]
        else:
            tile.status_filter = []
        tile.save()
        return redirect(reverse_lazy(_DT_REDIRECT) + _DT_TAB)


# ---------------------------------------------------------------------------
# Custom Fields
# ---------------------------------------------------------------------------

_CF_REDIRECT = 'core:settings'
_CF_TAB = '?tab=custom_fields'


class CustomFieldCreateView(LoginRequiredMixin, View):
    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        label = request.POST.get('label', '').strip()
        if not label:
            return redirect(reverse_lazy(_CF_REDIRECT) + _CF_TAB)
        field_type = request.POST.get('field_type', 'text')
        applies_to = request.POST.get('applies_to', 'ticket')
        is_required = request.POST.get('is_required') == 'on'
        help_text = request.POST.get('help_text', '').strip()
        try:
            sort_order = int(request.POST.get('sort_order', '0'))
        except ValueError:
            sort_order = 0
        sth_id = request.POST.get('scoped_to_help_topic') or None
        str_id = request.POST.get('scoped_to_repair_type') or None
        cf = CustomField.objects.create(
            label=label,
            field_type=field_type,
            applies_to=applies_to,
            is_required=is_required,
            help_text=help_text,
            sort_order=sort_order,
            scoped_to_help_topic=HelpTopic.objects.filter(pk=sth_id).first() if sth_id else None,
            scoped_to_repair_type=RepairType.objects.filter(pk=str_id).first() if str_id else None,
        )
        # Seed choices for select fields
        choices_raw = request.POST.get('choices', '').strip()
        if cf.field_type == 'select' and choices_raw:
            for i, c in enumerate(choices_raw.splitlines()):
                c = c.strip()
                if c:
                    CustomFieldChoice.objects.create(field=cf, label=c, sort_order=i)
        return redirect(reverse_lazy(_CF_REDIRECT) + _CF_TAB)


class CustomFieldUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        cf = get_object_or_404(CustomField, pk=pk)
        cf.label = request.POST.get('label', cf.label).strip()
        cf.field_type = request.POST.get('field_type', cf.field_type)
        cf.applies_to = request.POST.get('applies_to', cf.applies_to)
        cf.is_required = request.POST.get('is_required') == 'on'
        cf.help_text = request.POST.get('help_text', '').strip()
        try:
            cf.sort_order = int(request.POST.get('sort_order', cf.sort_order))
        except ValueError:
            pass
        cf.is_active = request.POST.get('is_active') == 'on'
        sth_id = request.POST.get('scoped_to_help_topic') or None
        str_id = request.POST.get('scoped_to_repair_type') or None
        cf.scoped_to_help_topic = HelpTopic.objects.filter(pk=sth_id).first() if sth_id else None
        cf.scoped_to_repair_type = RepairType.objects.filter(pk=str_id).first() if str_id else None
        cf.save()
        return redirect(reverse_lazy(_CF_REDIRECT) + _CF_TAB)


class CustomFieldDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied; raise PermissionDenied
        get_object_or_404(CustomField, pk=pk).delete()
        return redirect(reverse_lazy(_CF_REDIRECT) + _CF_TAB)


class CustomFieldChoiceAddView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        cf = get_object_or_404(CustomField, pk=pk)
        label = request.POST.get('label', '').strip()
        if label:
            max_order = cf.choices.aggregate(m=models_Max('sort_order'))['m'] or 0
            CustomFieldChoice.objects.create(field=cf, label=label, sort_order=max_order + 10)
        return redirect(reverse_lazy(_CF_REDIRECT) + _CF_TAB)


class CustomFieldChoiceDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        CustomFieldChoice.objects.filter(pk=pk).delete()
        return redirect(reverse_lazy(_CF_REDIRECT) + _CF_TAB)
