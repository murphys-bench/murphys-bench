from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.urls import reverse_lazy
from django.db.models import Q, F as models_F
from django.contrib.contenttypes.models import ContentType
from django.http import FileResponse, Http404
from django.utils import timezone
from .models import (
    WorkOrder, WorkOrderNote, WorkOrderItem, Client, Device, Mileage, Checklist,
    Ticket, TicketReply, TicketLock, TicketLink, Attachment, SiteSettings,
    KBCategory, KBArticle, TicketQueue, DashboardTile, User,
    CustomField, CustomFieldValue,
    QuickLaborItem, WorkPerformed, ContactPhone,
)
from .forms import (WorkOrderForm, ClientForm, ContactForm, ContactPhoneForm, DeviceForm,
                    TicketForm, TicketConvertForm, KBArticleForm, TicketQueueForm, MileageForm,
                    CompanySettingsForm, OutboundEmailSettingsForm, InboundEmailSettingsForm,
                    AttachmentSettingsForm, SecuritySettingsForm, MileageSettingsForm)


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


def _tile_count(tile, user, is_admin):
    """Return the count for a DashboardTile."""
    statuses = tile.status_filter or []
    if tile.row == 'ticket':
        qs = Ticket.objects.all()
        if not is_admin:
            qs = qs.filter(assigned_to=user)
        if statuses:
            qs = qs.filter(status__in=statuses)
        if '/overdue' in tile.link_url or 'overdue=1' in tile.link_url:
            from django.utils import timezone as tz
            qs = qs.filter(due_at__lt=tz.now()).exclude(status__in=['closed', 'resolved', 'converted'])
    else:
        qs = WorkOrder.objects.all()
        if not is_admin:
            qs = qs.filter(assigned_to=user)
        if statuses:
            qs = qs.filter(status__in=statuses)
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
            entry = {'tile': tile, 'count': _tile_count(tile, request.user, is_admin)}
            if tile.row == 'ticket':
                ticket_tiles.append(entry)
            else:
                wo_tiles.append(entry)

        # Recent open work orders (tech sees own, admin sees all)
        wo_qs = WorkOrder.objects.select_related('client', 'assigned_to', 'device').exclude(status__in=['closed', 'cancelled'])
        if not is_admin:
            wo_qs = wo_qs.filter(assigned_to=request.user)
        open_work_orders = wo_qs.order_by('-created_at')[:10]

        recently_closed = WorkOrder.objects.select_related('client', 'assigned_to').filter(
            status__in=['closed', 'cancelled']
        ).order_by('-updated_at')[:5]

        context = {
            'ticket_tiles': ticket_tiles,
            'wo_tiles': wo_tiles,
            'open_work_orders': open_work_orders,
            'recently_closed': recently_closed,
            'active_clients': Client.objects.filter(is_active=True).count(),
            'total_devices': Device.objects.filter(is_active=True).count(),
            'is_admin': is_admin,
        }
        return render(request, self.template_name, context)


class WorkOrderListView(LoginRequiredMixin, ListView):
    """Display list of all work orders with filtering and search"""
    model = WorkOrder
    template_name = 'core/work_order_list.html'
    context_object_name = 'work_orders'
    paginate_by = 25

    def get_queryset(self):
        queryset = WorkOrder.objects.select_related('client', 'assigned_to', 'device')

        # Filter by status if provided
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        # Filter by assigned_to if provided (admins using 'me' see all WOs)
        assigned_to = self.request.GET.get('assigned_to')
        if assigned_to == 'me' and not _is_admin(self.request.user):
            queryset = queryset.filter(assigned_to=self.request.user)
        elif assigned_to and assigned_to != 'me':
            queryset = queryset.filter(assigned_to_id=assigned_to)

        # Search by work order number or client name
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(work_order_number__icontains=search) |
                Q(client__name__icontains=search)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = WorkOrder.STATUS_CHOICES
        context['priority_choices'] = WorkOrder.PRIORITY_CHOICES
        return context


class WorkOrderDetailView(LoginRequiredMixin, DetailView):
    """Display full details of a single work order"""
    model = WorkOrder
    template_name = 'core/work_order_detail.html'
    context_object_name = 'work_order'

    def get_queryset(self):
        return WorkOrder.objects.select_related(
            'client', 'assigned_to', 'device', 'repair_type', 'ticket'
        ).prefetch_related('notes', 'items', 'notes__created_by')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
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
        # Work Performed entries grouped by category
        wp_entries = (
            WorkPerformed.objects
            .filter(work_order=self.object)
            .select_related('labor_item')
            .order_by('labor_item__category', 'labor_item__label')
        )
        wp_categories = {}
        for entry in wp_entries:
            cat = entry.labor_item.category
            wp_categories.setdefault(cat, []).append(entry)
        context['wp_categories'] = wp_categories
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


class ClientListView(LoginRequiredMixin, ListView):
    """Display list of all clients"""
    model = Client
    template_name = 'core/client_list.html'
    context_object_name = 'clients'
    paginate_by = 25

    def get_queryset(self):
        queryset = Client.objects.all()

        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search)
            )

        active = self.request.GET.get('active')
        if active == 'yes':
            queryset = queryset.filter(is_active=True)
        elif active == 'no':
            queryset = queryset.filter(is_active=False)

        return queryset.order_by('name')


class ClientDetailView(LoginRequiredMixin, DetailView):
    """Display full details of a single client"""
    model = Client
    template_name = 'core/client_detail.html'
    context_object_name = 'client'

    def get_queryset(self):
        return Client.objects.prefetch_related(
            'contacts', 'devices', 'work_orders', 'work_orders__assigned_to'
        )


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
            client.address_street, client.address_city,
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
        parts = [client.address_street, client.address_city, client.address_state, client.address_zip]
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

class WorkOrderItemToggleView(LoginRequiredMixin, View):
    """Toggle a checklist item complete/incomplete — returns updated <li> fragment"""

    def post(self, request, pk):
        item = get_object_or_404(WorkOrderItem, pk=pk)
        item.is_completed = not item.is_completed
        item.save()
        return render(request, 'core/partials/checklist_item.html', {'item': item})


# --- Work Order Create / Edit ---

class WorkOrderCreateView(LoginRequiredMixin, CreateView):
    model = WorkOrder
    form_class = WorkOrderForm
    template_name = 'core/work_order_form.html'

    def form_valid(self, form):
        form.instance.work_order_number = WorkOrder.generate_work_order_number()
        response = super().form_valid(form)
        _save_attachments(self.request, self.object)

        # Optionally apply the default checklist for the selected repair type
        if form.cleaned_data.get('apply_checklist') and self.object.repair_type:
            checklist = self.object.repair_type.checklists.filter(
                is_default=True, is_active=True
            ).first()
            if checklist:
                for item in checklist.items.filter(is_active=True).order_by('sort_order'):
                    WorkOrderItem.objects.create(
                        work_order=self.object,
                        item_type='checklist',
                        description=item.description,
                        quantity=1,
                        is_completed=False,
                    )

        fields = _get_custom_fields_for_workorder(self.object)
        _save_custom_field_values(self.request, self.object, fields)
        return response

    def get_success_url(self):
        return reverse_lazy('core:work_order_detail', kwargs={'pk': self.object.pk})

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('client'):
            initial['client'] = self.request.GET['client']
        if self.request.GET.get('device'):
            initial['device'] = self.request.GET['device']
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

    def form_valid(self, form):
        old_status = WorkOrder.objects.get(pk=self.object.pk).status
        response = super().form_valid(form)

        # Auto-resolve linked ticket when WO closes (if setting is on)
        if self.object.status == 'closed' and old_status != 'closed':
            from django.conf import settings
            if getattr(settings, 'AUTO_RESOLVE_TICKET_ON_WO_CLOSE', False):
                ticket = self.object.ticket
                if ticket and ticket.status not in ('resolved', 'closed', 'converted'):
                    ticket.status = 'resolved'
                    ticket.save()

        # Optionally append the default checklist for the selected repair type
        if form.cleaned_data.get('apply_checklist') and self.object.repair_type:
            checklist = self.object.repair_type.checklists.filter(
                is_default=True, is_active=True
            ).first()
            if checklist:
                for item in checklist.items.filter(is_active=True).order_by('sort_order'):
                    WorkOrderItem.objects.create(
                        work_order=self.object,
                        item_type='checklist',
                        description=item.description,
                        quantity=1,
                        is_completed=False,
                    )

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
        return context


# --- Device Create / Edit ---

class DeviceCreateView(LoginRequiredMixin, CreateView):
    model = Device
    form_class = DeviceForm
    template_name = 'core/device_form.html'

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('client'):
            initial['client'] = self.request.GET['client']
        return initial

    def get_success_url(self):
        # If launched from a client page, go back there after saving
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return next_url
        return reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk})

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

    def get_success_url(self):
        return reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.name}'
        context['cancel_url'] = reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk})
        return context


# --- Ticket Views ---

class TicketListView(LoginRequiredMixin, ListView):
    model = Ticket
    template_name = 'core/ticket_list.html'
    context_object_name = 'tickets'
    paginate_by = 25

    def get_queryset(self):
        queryset = Ticket.objects.select_related('client', 'device', 'created_by', 'assigned_to')

        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        assigned_to = self.request.GET.get('assigned_to')
        if assigned_to == 'me' and not _is_admin(self.request.user):
            queryset = queryset.filter(assigned_to=self.request.user)
        elif assigned_to and assigned_to != 'me':
            queryset = queryset.filter(assigned_to_id=assigned_to)

        overdue = self.request.GET.get('overdue')
        if overdue:
            queryset = queryset.filter(due_at__lt=timezone.now()).exclude(status__in=['closed', 'resolved', 'converted'])

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
        context['status_choices'] = Ticket.STATUS_CHOICES
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
            context['wo_is_open'] = wo.status not in ('closed', 'cancelled')
        # Linked tickets
        context['linked_tickets'] = ticket.get_linked_tickets()
        context['audit_log'] = _audit_entries(ticket)
        # Ticket-level attachments
        ct = ContentType.objects.get_for_model(Ticket)
        context['ticket_attachments'] = Attachment.objects.filter(content_type=ct, object_id=ticket.pk)
        # Custom fields
        fields = _get_custom_fields_for_ticket(ticket)
        context['custom_field_values'] = _custom_fields_with_values(fields, ticket)
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
            if wo and wo.status not in ('closed', 'cancelled'):
                form.add_error('status', f'Cannot close this ticket — linked work order {wo.work_order_number} is still open.')
                return self.form_invalid(form)
        old_status = self.object.status
        response = super().form_valid(form)
        fields = _get_custom_fields_for_ticket(self.object)
        _save_custom_field_values(self.request, self.object, fields)
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
            from .email_utils import send_ticket_email
            send_ticket_email('reply_added', ticket, {'reply': reply})
        return render(request, 'core/partials/ticket_reply_item.html', {'reply': reply})


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
            work_order_number=WorkOrder.generate_work_order_number(),
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

        ticket_qs = Ticket.objects.select_related('client').exclude(
            status__in=['closed', 'resolved', 'converted']
        )
        if is_admin:
            # Admins see all open tickets
            ticket_qs = ticket_qs.order_by('-updated_at')
        else:
            ticket_qs = ticket_qs.filter(
                Q(assigned_to=request.user) | Q(created_by=request.user)
            ).distinct().order_by('-updated_at')

        wo_qs = WorkOrder.objects.select_related('client').exclude(
            status__in=['closed', 'cancelled']
        )
        if is_admin:
            # Admins see all open work orders
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
                writer.writerow([f"{r['technician__first_name']} {r['technician__last_name']}", r['month'].strftime('%Y-%m'), r['miles']])

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
# Quick Labor / Work Performed
# ---------------------------------------------------------------------------

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
        # Return updated work-performed partial
        entries = (
            WorkPerformed.objects
            .filter(work_order=work_order)
            .select_related('labor_item', 'logged_by')
            .order_by('labor_item__category', 'labor_item__label')
        )
        categories = {}
        for entry in entries:
            cat = entry.labor_item.category
            categories.setdefault(cat, []).append(entry)
        return render(request, 'core/partials/work_performed.html', {
            'work_order': work_order,
            'categories': categories,
        })


class WorkPerformedDeleteView(LoginRequiredMixin, View):
    """HTMX: remove a logged WorkPerformed entry."""

    def post(self, request, pk):
        entry = get_object_or_404(WorkPerformed, pk=pk)
        work_order = entry.work_order
        entry.delete()
        entries = (
            WorkPerformed.objects
            .filter(work_order=work_order)
            .select_related('labor_item', 'logged_by')
            .order_by('labor_item__category', 'labor_item__label')
        )
        categories = {}
        for entry in entries:
            cat = entry.labor_item.category
            categories.setdefault(cat, []).append(entry)
        return render(request, 'core/partials/work_performed.html', {
            'work_order': work_order,
            'categories': categories,
        })


# ---------------------------------------------------------------------------
# Repair Report (print view)
# ---------------------------------------------------------------------------

class WorkOrderPrintView(LoginRequiredMixin, View):
    """Print-optimised repair report for handing to the customer."""

    def get(self, request, pk):
        work_order = get_object_or_404(
            WorkOrder.objects.select_related('client', 'device', 'repair_type', 'assigned_to'),
            pk=pk,
        )
        site = SiteSettings.get()

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

        return render(request, 'core/work_order_print.html', {
            'work_order': work_order,
            'site': site,
            'notes': notes,
            'wp_categories': categories,
            'repair_types': repair_types,
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
    """Save phone numbers POSTed as phone_number_X / phone_type_X arrays."""
    # Delete existing and re-save from POST
    contact.phone_numbers.all().delete()
    numbers = request.POST.getlist('phone_number')
    types = request.POST.getlist('phone_type')
    for number, phone_type in zip(numbers, types):
        number = number.strip()
        if number:
            ContactPhone.objects.create(
                contact=contact,
                number=number,
                phone_type=phone_type or 'cell',
            )


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
        wo.save(update_fields=['device_username', 'device_password', 'device_pin'])
        return render(request, 'core/partials/credentials_display.html', {'work_order': wo})


# ---------------------------------------------------------------------------
# Native Settings UI (/settings/)
# ---------------------------------------------------------------------------

SETTINGS_TABS = [
    ('company',  'Company',        CompanySettingsForm),
    ('outbound', 'Outbound Email', OutboundEmailSettingsForm),
    ('inbound',  'Inbound Email',  InboundEmailSettingsForm),
    ('attachments', 'Attachments', AttachmentSettingsForm),
    ('security', 'Security',       SecuritySettingsForm),
    ('mileage',  'Mileage',        MileageSettingsForm),
]


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
        settings = SiteSettings.get()
        active_tab = request.GET.get('tab', 'company')
        forms_map = {key: FormClass(instance=settings, prefix=key) for key, _, FormClass in SETTINGS_TABS}
        return render(request, 'core/settings.html', {
            'settings': settings,
            'active_tab': active_tab,
            'tabs': [(key, label) for key, label, _ in SETTINGS_TABS],
            'forms': forms_map,
        })

    def post(self, request):
        self._check_permission(request)
        settings = SiteSettings.get()
        tab = request.POST.get('tab', 'company')
        FormClass = next((fc for key, _, fc in SETTINGS_TABS if key == tab), None)
        if FormClass is None:
            return redirect('core:settings')

        form = FormClass(request.POST, request.FILES, instance=settings, prefix=tab)
        if form.is_valid():
            form.save()
            messages.success(request, 'Settings saved.')
            return redirect(f"{request.path}?tab={tab}")

        # Re-render with errors
        forms_map = {}
        for key, _, FC in SETTINGS_TABS:
            if key == tab:
                forms_map[key] = form
            else:
                forms_map[key] = FC(instance=settings, prefix=key)
        return render(request, 'core/settings.html', {
            'settings': settings,
            'active_tab': tab,
            'tabs': [(key, label) for key, label, _ in SETTINGS_TABS],
            'forms': forms_map,
        })
