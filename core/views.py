from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.db.models import Q, F as models_F
from django.contrib.contenttypes.models import ContentType
from django.http import FileResponse, Http404
from django.utils import timezone
from .models import (
    WorkOrder, WorkOrderNote, WorkOrderItem, Client, Device, Mileage, Checklist,
    Ticket, TicketReply, TicketLock, TicketLink, Attachment, SiteSettings,
    KBCategory, KBArticle,
)
from .forms import WorkOrderForm, ClientForm, DeviceForm, TicketForm, TicketConvertForm, KBArticleForm


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


class DashboardView(LoginRequiredMixin, ListView):
    """Home page — key metrics and recent work orders"""
    template_name = 'core/dashboard.html'
    context_object_name = 'open_work_orders'

    def get_queryset(self):
        return WorkOrder.objects.select_related(
            'client', 'assigned_to', 'device'
        ).exclude(
            status__in=['closed', 'cancelled']
        ).order_by('-created_at')

    def get_context_data(self, **kwargs):
        from django.db.models import Count
        context = super().get_context_data(**kwargs)

        # Status counts for open work orders
        context['status_counts'] = {
            'new': WorkOrder.objects.filter(status='new').count(),
            'assigned': WorkOrder.objects.filter(status='assigned').count(),
            'in_progress': WorkOrder.objects.filter(status='in_progress').count(),
            'completed': WorkOrder.objects.filter(status='completed').count(),
        }

        # Total open (anything not closed/cancelled)
        context['open_total'] = WorkOrder.objects.exclude(
            status__in=['closed', 'cancelled']
        ).count()

        # Recently closed (last 5)
        context['recently_closed'] = WorkOrder.objects.select_related(
            'client', 'assigned_to'
        ).filter(
            status__in=['closed', 'cancelled']
        ).order_by('-updated_at')[:5]

        # Quick stats
        context['active_clients'] = Client.objects.filter(is_active=True).count()
        context['total_devices'] = Device.objects.filter(is_active=True).count()

        return context


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

        # Filter by assigned_to if provided
        assigned_to = self.request.GET.get('assigned_to')
        if assigned_to:
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
        from auditlog.models import LogEntry
        context['audit_log'] = LogEntry.objects.get_for_object(self.object).select_related('actor')[:50]
        ct = ContentType.objects.get_for_model(WorkOrder)
        context['wo_attachments'] = Attachment.objects.filter(content_type=ct, object_id=self.object.pk)
        # Linked ticket for overdue badge
        context['linked_ticket'] = getattr(self.object, 'ticket', None)
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

        return response

    def get_success_url(self):
        return reverse_lazy('core:work_order_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'New Work Order'
        context['cancel_url'] = reverse_lazy('core:work_order_list')
        context['is_create'] = True
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

        return response

    def get_success_url(self):
        return reverse_lazy('core:work_order_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.work_order_number}'
        context['cancel_url'] = reverse_lazy('core:work_order_detail', kwargs={'pk': self.object.pk})
        context['is_create'] = False
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

    def get_success_url(self):
        return reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'New Device'
        context['cancel_url'] = reverse_lazy('core:device_list')
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
        queryset = Ticket.objects.select_related('client', 'device', 'created_by')

        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

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
        # Audit history
        from auditlog.models import LogEntry
        context['audit_log'] = LogEntry.objects.get_for_object(ticket).select_related('actor')[:50]
        # Ticket-level attachments
        ct = ContentType.objects.get_for_model(Ticket)
        context['ticket_attachments'] = Attachment.objects.filter(content_type=ct, object_id=ticket.pk)
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
        from .email_utils import send_ticket_email
        send_ticket_email('ticket_created', self.object)
        return response

    def get_success_url(self):
        return reverse_lazy('core:ticket_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'New Ticket'
        context['cancel_url'] = reverse_lazy('core:ticket_list')
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
