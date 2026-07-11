import json
import logging
from datetime import timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.urls import reverse, reverse_lazy
from django.db.models import Q, F as models_F, Max as models_Max, Count
from django.db import IntegrityError
from django.core.exceptions import PermissionDenied
from django.contrib.contenttypes.models import ContentType
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils.html import escape
from .models import (
    WorkOrder, WorkOrderNote, WorkOrderItem, Client, Device, Mileage, Checklist, ChecklistItem,
    Ticket, TicketReply, TicketLock, TicketLink, Attachment, SiteSettings,
    KBCategory, KBArticle, TicketQueue, DashboardTile, User,
    CustomField, CustomFieldChoice, CustomFieldValue,
    CatalogItem, LineItem, ContactPhone,
    Contact, RepairType, RepairTypeCategory,
    CannedResponseCategory, CannedResponse, Invoice,
    OrgCredential, CredentialAccessLog,
    DeviceCredentialAccessLog,
    EmailTemplate, EmailSignature,
    StatusDefinition,
    SuppressedAddress,
    BlockedSender,
    SLAPlan, HelpTopic, TechSkill,
    Notification, Prospect, Estimate, Sale, EstimateOption, PaymentChargeAttempt,
)
from .forms import (WorkOrderForm, ClientForm, ContactForm, ContactPhoneForm, DeviceForm, DeviceQuickAddForm,
                    TicketForm, TicketConvertForm, KBArticleForm, TicketQueueForm, MileageForm,
                    CompanySettingsForm, OutboundEmailSettingsForm, InboundEmailSettingsForm,
                    AttachmentSettingsForm, SecuritySettingsForm, MileageSettingsForm,
                    ColorSettingsForm, InvoiceNinjaSettingsForm, ProspectForm, EstimateForm, SaleForm,
                    SaleCheckoutForm)

logger = logging.getLogger('core')


@csrf_exempt
@require_POST
def csp_report(request):
    """Receive browser CSP violation reports and log them.

    Browsers POST these unauthenticated and without a CSRF token, so the view is
    csrf-exempt and login-free (the MFA middleware also exempts /csp-report/).
    Used during the report-only rollout to see what a policy would block, and kept
    afterward for ongoing monitoring. Bodies are size-capped and never trusted.
    """
    body = request.body[:4096]
    try:
        report = json.loads(body.decode('utf-8', 'replace'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=204)
    # Both the legacy report-uri shape ({"csp-report": {...}}) and bare dicts.
    detail = report.get('csp-report', report) if isinstance(report, dict) else report
    if isinstance(detail, dict):
        logger.warning(
            'CSP violation: blocked=%s directive=%s document=%s',
            detail.get('blocked-uri'),
            detail.get('violated-directive') or detail.get('effective-directive'),
            detail.get('document-uri'),
        )
    else:
        logger.warning('CSP violation (unparsed shape)')
    return HttpResponse(status=204)


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


def _can_view_prospects(user):
    """Prospects are visible to everyone unless a role explicitly turns the flag
    off. Admins always qualify."""
    return _is_admin(user) or user.has_perm_flag('can_view_prospects')


def _can_view_estimates(user):
    """Estimates are visible to everyone unless a role explicitly turns the flag
    off. Admins always qualify."""
    return _is_admin(user) or user.has_perm_flag('can_view_estimates')


def _can_view_sales(user):
    """Sales are visible to everyone unless a role explicitly turns the flag
    off. Admins always qualify."""
    return _is_admin(user) or user.has_perm_flag('can_view_sales')


def _can_process_payments(user):
    """Who may trigger an on-file card charge (Slice 5d). Unlike the can_view_*
    flags (visible-unless-blocked, default True), this is opt-in-only, default
    False — charging money is deliberately NOT granted to admins-by-default the
    way can_manage_settings is. Gated on superuser (same bar as MFA reset,
    _can_reset_mfa) or the dedicated flag."""
    return user.is_superuser or user.has_perm_flag('can_process_payments')


def _can_reset_mfa(user):
    """Who may reset another user's two-factor devices. Gated on the dedicated
    can_reset_user_mfa flag; superusers always qualify so the reset capability
    can never be fully locked out."""
    return user.is_superuser or user.has_perm_flag('can_reset_user_mfa')


def _scope_assignable_for(qs, user):
    """Restrict an assignable queryset (Work Orders) for non-admins: they see their
    own assigned items plus the unassigned pool — so they can still claim new work —
    but never items claimed by another user. Admins see everything.
    """
    if _is_admin(user):
        return qs
    return qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))


def _can_access_attachment(user, attachment):
    """True if `user` may download `attachment`, by resolving its owning object
    (Ticket/TicketReply/WorkOrder/WorkOrderNote) and applying the same visibility
    scoping used on the list/detail views. Admins see everything."""
    if _is_admin(user):
        return True
    obj = attachment.content_object
    if obj is None:
        return False
    # Resolve to the governing ticket or work order
    if isinstance(obj, Ticket):
        return _scope_tickets_for(Ticket.objects.all(), user).filter(pk=obj.pk).exists()
    if isinstance(obj, TicketReply):
        return _scope_tickets_for(Ticket.objects.all(), user).filter(pk=obj.ticket_id).exists()
    if isinstance(obj, WorkOrder):
        return _scope_assignable_for(WorkOrder.objects.all(), user).filter(pk=obj.pk).exists()
    if isinstance(obj, WorkOrderNote):
        return _scope_assignable_for(WorkOrder.objects.all(), user).filter(pk=obj.work_order_id).exists()
    # Unknown owner type — deny by default
    return False


def _scope_tickets_for(qs, user):
    """Ticket visibility for non-admins: their own + the unclaimed pool + tickets
    escalated above their owner's level up to the viewer's level (so a higher-level
    tech can take over an escalated ticket while its original owner still holds it).
    Admins see everything.
    """
    if _is_admin(user):
        return qs
    return qs.filter(
        Q(assigned_to=user)
        | Q(assigned_to__isnull=True)
        | (
            Q(assigned_to__isnull=False)
            & ~Q(assigned_to=user)
            & Q(escalation_level__gt=models_F('assigned_to__level'))
            & Q(escalation_level__lte=user.level)
        )
    )


def _get_scoped_wo_or_404(request, pk, queryset=None):
    """Fetch a WorkOrder by pk, 404ing if it's outside the requesting user's
    visibility (per `_scope_assignable_for`). Pass `queryset` to layer on
    select_related/prefetch_related; defaults to WorkOrder.objects.all()."""
    qs = queryset if queryset is not None else WorkOrder.objects.all()
    return get_object_or_404(_scope_assignable_for(qs, request.user), pk=pk)


def _get_scoped_ticket_or_404(request, pk, queryset=None):
    """Fetch a Ticket by pk, 404ing if it's outside the requesting user's
    visibility (per `_scope_tickets_for`). Pass `queryset` to layer on
    select_related/prefetch_related; defaults to Ticket.objects.all()."""
    qs = queryset if queryset is not None else Ticket.objects.all()
    return get_object_or_404(_scope_tickets_for(qs, request.user), pk=pk)


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
            qs = Ticket.overdue_queryset(qs)
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

        # Unsorted/Unverified triage bucket (admin only) — inbound from senders
        # not yet matched to a real client, awaiting onboarding or rejection.
        triage_count = 0
        if is_admin:
            triage_count = (
                Ticket.objects.filter(client__is_unsorted=True)
                .exclude(status__in=TICKET_CLOSED_STATUSES).count()
            )

        # Tickets escalated above their owner's level, awaiting pickup by someone higher.
        # Admins see all of them; a tech sees those escalated up to their own level.
        escalated_qs = (
            Ticket.objects.select_related('client', 'assigned_to')
            .filter(assigned_to__isnull=False, escalation_level__gt=models_F('assigned_to__level'))
            .exclude(status__in=TICKET_CLOSED_STATUSES)
        )
        if not is_admin:
            escalated_qs = escalated_qs.filter(
                escalation_level__lte=request.user.level
            ).exclude(assigned_to=request.user)
        escalated_to_me = list(escalated_qs.order_by('-updated_at')[:10])

        # Tech-only "My Mileage" card — techs have no Mileage nav link, so this is
        # their entry point to log/view their own miles. (Admins see Team Workload.)
        my_mileage_total = None
        my_mileage_recent = []
        if not is_admin:
            from django.db.models import Sum
            today = timezone.now().date()
            my_mileage_total = Mileage.objects.filter(
                technician=request.user,
                trip_date__year=today.year, trip_date__month=today.month,
            ).aggregate(t=Sum('miles'))['t'] or 0
            my_mileage_recent = list(
                Mileage.objects.filter(technician=request.user).order_by('-trip_date')[:5]
            )

        context = {
            'ticket_tiles': ticket_tiles,
            'wo_tiles': wo_tiles,
            'open_work_orders': open_work_orders,
            'recently_closed': recently_closed,
            'active_clients': Client.objects.filter(is_active=True, is_unsorted=False).count(),
            'total_devices': Device.objects.filter(is_active=True).count(),
            'is_admin': is_admin,
            'team_workload': team_workload,
            'needs_response_count': needs_response_count,
            'triage_count': triage_count,
            'open_tickets': open_tickets,
            'escalated_to_me': escalated_to_me,
            'my_mileage_total': my_mileage_total,
            'my_mileage_recent': my_mileage_recent,
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
        queryset = _scope_assignable_for(queryset, self.request.user)

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
        base_qs = _scope_assignable_for(WorkOrder.objects.all(), self.request.user)
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
        qs = WorkOrder.objects.select_related(
            'client', 'assigned_to', 'device', 'device__assigned_contact',
            'repair_type', 'ticket', 'contact'
        ).prefetch_related('notes', 'items', 'notes__created_by')
        return _scope_assignable_for(qs, self.request.user)

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
        # Catalog picker (Products & Services) grouped by category, services first
        context['catalog_by_category'] = _catalog_by_category()
        context['wp_entries'] = _line_items_for(self.object)
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
        wo = _get_scoped_wo_or_404(request, pk)
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
        wo = _get_scoped_wo_or_404(request, pk)
        p = request.POST

        wo.status       = p.get('status', wo.status)
        wo.priority     = p.get('priority', wo.priority)
        wo.service_type = p.get('service_type', wo.service_type)

        assigned_to_id = p.get('assigned_to')
        wo.assigned_to_id = assigned_to_id if assigned_to_id else None

        contact_id = p.get('contact')
        wo.contact_id = contact_id if contact_id else None

        device_id = p.get('device')
        new_device_id = int(device_id) if device_id else None
        device_changed = new_device_id != wo.device_id
        wo.device_id = new_device_id
        # Reassigning the device re-snapshots its hardware specs onto the WO
        if device_changed:
            wo.apply_device_specs(force=True)

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
        wo = _get_scoped_wo_or_404(request, pk)
        wo.assigned_to = request.user
        wo.save(update_fields=['assigned_to', 'updated_at'])
        return redirect('core:work_order_detail', pk=pk)


class WorkOrderAttachmentUploadView(LoginRequiredMixin, View):
    """Upload files directly to a work order (not tied to a note)."""

    def post(self, request, pk):
        wo = _get_scoped_wo_or_404(request, pk)
        _save_attachments(request, wo)
        return redirect('core:work_order_detail', pk=pk)


class WorkOrderApplyChecklistView(LoginRequiredMixin, View):
    """Re-apply checklist items from the flat bank to an existing WO."""

    def post(self, request, pk):
        wo = _get_scoped_wo_or_404(request, pk)
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
        if self.object.is_managed:
            context['catalog_by_category'] = _catalog_by_category()
            context['recurring_entries'] = _line_items_for(self.object)
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


class MileageDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        entry = get_object_or_404(Mileage, pk=pk)
        if not _is_admin(request.user) and entry.technician != request.user:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        entry.delete()
        return redirect('core:mileage_list')


class WorkOrderMileageCreateView(LoginRequiredMixin, View):
    """Mileage entry form launched from a Work Order detail page."""

    def _context(self, work_order, form):
        settings = SiteSettings.get()
        client = work_order.client
        # Build client full address for destination pre-fill (blank for a
        # walk-in WO with no client — nothing to prefill, tech types it in)
        parts = [
            client.address_line1, client.address_city,
            client.address_state, client.address_zip,
        ] if client else []
        client_address = ', '.join(p for p in parts if p)
        return {
            'form': form,
            'work_order': work_order,
            'shop_address': settings.shop_address,
            'client_address': client_address,
            'has_maps_key': bool(settings.google_maps_api_key),
        }

    def get(self, request, pk):
        work_order = _get_scoped_wo_or_404(request, pk)
        settings = SiteSettings.get()
        client = work_order.client
        parts = [client.address_line1, client.address_city, client.address_state, client.address_zip] if client else []
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
        work_order = _get_scoped_wo_or_404(request, pk)
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

        # Non-admins only ever see their own mileage. Admins see all, with an
        # optional per-technician filter.
        if not _is_admin(self.request.user):
            queryset = queryset.filter(technician=self.request.user)
        else:
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
        work_order = _get_scoped_wo_or_404(request, pk)
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


# --- Internal tech-to-tech messaging + notifications ---

def _notification_admins():
    """Users who should catch a tech message when no specific counterpart is
    assigned: staff or anyone whose role can manage settings."""
    return list(User.objects.filter(
        Q(is_staff=True) | Q(role_obj__can_manage_settings=True)
    ).distinct())


class TechMessageView(LoginRequiredMixin, View):
    """Internal tech-to-tech message about a WO/ticket pair. Records the message
    as an internal note in the ticket thread (the consolidated record) and
    notifies the counterpart tech. One mechanism, two entry points: `source`
    is 'wo' (from the work order) or 'ticket' (from the ticket)."""

    source = 'wo'

    def post(self, request, pk):
        if self.source == 'ticket':
            ticket = _get_scoped_ticket_or_404(request, pk)
            work_order = WorkOrder.objects.filter(ticket=ticket).first()
        else:
            work_order = _get_scoped_wo_or_404(request, pk)
            ticket = work_order.ticket

        if ticket is None:
            return HttpResponse('No linked ticket to message about.', status=400)

        content = request.POST.get('content', '').strip()
        if not content:
            return HttpResponse(status=204)

        # The message lives as an internal note in the ticket thread.
        reply = TicketReply.objects.create(
            ticket=ticket,
            reply_type='internal',
            content=content,
            created_by=request.user,
        )

        # Notify the OTHER role's tech: a message from the WO targets the ticket
        # tech, and vice versa. If that role is unassigned, fall back to admins
        # (a dispatcher picks it up). If that role is held by the sender (one
        # person working both ends), there's no one to notify — and we must NOT
        # spam other admins about a message someone sent to themselves.
        target = (getattr(work_order, 'assigned_to', None)
                  if self.source == 'ticket' else ticket.assigned_to)
        if target is None:
            recipients = {u for u in _notification_admins() if u.id != request.user.id}
        elif target.id != request.user.id:
            recipients = {target}
        else:
            recipients = set()

        sender_name = request.user.get_full_name() or request.user.username
        ref = (work_order.work_order_number if work_order else ticket.ticket_number)
        snippet = content if len(content) <= 80 else content[:77] + '…'
        for u in recipients:
            Notification.objects.create(
                recipient=u,
                actor=request.user,
                kind='tech_message',
                text=f'{sender_name} · {ref}: {snippet}',
                ticket=ticket,
                work_order=work_order,
            )

        if self.source == 'ticket':
            # Append to the visible ticket reply thread.
            return render(request, 'core/partials/ticket_reply_item.html', {'reply': reply})
        # From the WO: a small confirmation (the WO surfaces ticket activity via
        # the cross-visibility panel rather than an inline thread).
        return HttpResponse(
            '<p class="text-sm text-green-700 py-2">✓ Message sent to the ticket tech.</p>'
        )


class NotificationListView(LoginRequiredMixin, View):
    """The notification center page — unread first, then recent."""

    def get(self, request):
        notes = (request.user.notifications
                 .select_related('actor', 'ticket', 'work_order')
                 .order_by('is_read', '-created_at')[:100])
        return render(request, 'core/notifications.html', {'notifications': notes})


class NotificationCountView(LoginRequiredMixin, View):
    """Bell badge fragment — polled via HTMX from the sidebar and page headers."""

    def get(self, request):
        count = request.user.notifications.filter(is_read=False).count()
        template = (
            'core/partials/notification_badge_header.html'
            if request.GET.get('style') == 'header'
            else 'core/partials/notification_badge.html'
        )
        return render(request, template, {'count': count})


class NotificationOpenView(LoginRequiredMixin, View):
    """Mark a single notification read, then redirect to its target."""

    def get(self, request, pk):
        note = get_object_or_404(Notification, pk=pk, recipient=request.user)
        if not note.is_read:
            note.is_read = True
            note.read_at = timezone.now()
            note.save(update_fields=['is_read', 'read_at'])
        return redirect(note.target_url)


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request):
        request.user.notifications.filter(is_read=False).update(
            is_read=True, read_at=timezone.now()
        )
        return redirect('core:notifications')


# --- Work Order Item Toggle (HTMX) ---

class WorkOrderItemCheckView(LoginRequiredMixin, View):
    """HTMX: update pre_check or post_check on a checklist item, return full checklist."""

    def post(self, request, pk):
        item = get_object_or_404(
            WorkOrderItem.objects.filter(
                work_order__in=_scope_assignable_for(WorkOrder.objects.all(), request.user)
            ),
            pk=pk,
        )
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

    def post(self, request, *args, **kwargs):
        from django.db import transaction

        self.object = None
        form = self.get_form()
        device_form = DeviceQuickAddForm(request.POST, prefix='device')
        if form.is_valid() and device_form.is_valid():
            with transaction.atomic():
                if device_form.cleaned_data.get('name'):
                    device = device_form.save(commit=False)
                    device.client = form.cleaned_data.get('client')  # None = walk-in device
                    device.save()
                    form.instance.device = device
                form.instance.work_order_number = WorkOrder.generate_work_order_number()
                self.object = form.save()
            _save_attachments(self.request, self.object)
            if form.cleaned_data.get('apply_checklist'):
                _apply_checklist_items(self.object)
            fields = _get_custom_fields_for_workorder(self.object)
            _save_custom_field_values(self.request, self.object, fields)
            return redirect(self.get_success_url())
        return self.render_to_response(self.get_context_data(form=form, device_form=device_form))

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
        client_id = self.request.GET.get('client')
        back_client = Client.objects.filter(pk=client_id).first() if client_id else None
        context['back_client'] = back_client
        context['cancel_url'] = (
            reverse_lazy('core:client_detail', kwargs={'pk': back_client.pk})
            if back_client else reverse_lazy('core:work_order_list')
        )
        context['is_create'] = True
        context.setdefault('device_form', DeviceQuickAddForm(prefix='device'))
        fields = _get_custom_fields_for_workorder(None)
        context['custom_field_entries'] = [{'field': f, 'value': ''} for f in fields]
        return context


class WorkOrderUpdateView(LoginRequiredMixin, UpdateView):
    model = WorkOrder
    form_class = WorkOrderForm
    template_name = 'core/work_order_form.html'

    def get_queryset(self):
        return _scope_assignable_for(WorkOrder.objects.all(), self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['client_id'] = self.object.client_id
        return kwargs

    def form_valid(self, form):
        old_status = WorkOrder.objects.get(pk=self.object.pk).status
        response = super().form_valid(form)
        # Push any edited hardware specs back to the device master so it stays current
        self.object.sync_specs_to_device()
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


class WorkOrderDeleteView(LoginRequiredMixin, View):
    """Hard-delete a work order. Admin only.

    Cleans up attachment files (their rows cascade with the WO, but the files on
    disk don't), reopens a linked ticket stuck in 'converted' so it isn't
    orphaned, then cascades the rest (line items, notes, items, invoice). Mileage
    entries survive (work_order set null) — they're a travel log, not WO-owned.
    """

    def post(self, request, pk):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        wo = get_object_or_404(WorkOrder, pk=pk)
        wo_num = wo.work_order_number
        had_in_push = bool(wo.invoice_ninja_id)
        ticket = wo.ticket  # captured before delete; the ticket row is not removed

        # Delete attachment files from storage first (rows cascade with the WO).
        for att in wo.attachments.all():
            try:
                if att.file:
                    att.file.delete(save=False)
            except Exception:
                logger.warning('Could not delete an attachment file while deleting %s', wo_num)

        wo.delete()

        # A converted ticket whose WO is gone would be stuck in limbo — reopen it.
        if ticket and ticket.status == 'converted':
            ticket.status = 'open'
            ticket.save(update_fields=['status', 'updated_at'])

        msg = f'{wo_num} permanently deleted.'
        if had_in_push:
            msg += ' Note: an Invoice Ninja draft may still exist there — remove it in IN if needed.'
        messages.success(request, msg)
        return redirect('core:work_order_list')


# --- Client Create / Edit ---

class ClientCreateView(LoginRequiredMixin, CreateView):
    model = Client
    form_class = ClientForm
    template_name = 'core/client_form.html'

    def get_success_url(self):
        return reverse_lazy('core:client_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('device_form', DeviceQuickAddForm(prefix='device'))
        context['title'] = 'New Client'
        context['cancel_url'] = reverse_lazy('core:client_list')
        return context

    def post(self, request, *args, **kwargs):
        from django.db import transaction

        self.object = None
        form = self.get_form()
        device_form = DeviceQuickAddForm(request.POST, prefix='device')
        if form.is_valid() and device_form.is_valid():
            with transaction.atomic():
                self.object = form.save()
                if device_form.cleaned_data.get('name'):
                    device = device_form.save(commit=False)
                    device.client = self.object
                    device.save()
            return redirect(self.get_success_url())
        return self.render_to_response(self.get_context_data(form=form, device_form=device_form))


class ClientUpdateView(LoginRequiredMixin, UpdateView):
    model = Client
    form_class = ClientForm
    template_name = 'core/client_form.html'

    def get_success_url(self):
        return reverse_lazy('core:client_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        # The system triage bucket must stay active so inbound can always reach it.
        if self.object.is_unsorted:
            form.instance.is_active = True
        return super().form_valid(form)

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
        if client.is_unsorted:
            messages.error(
                request,
                f'{client.name} is the system triage bucket and cannot be deleted.'
            )
            return redirect('core:client_edit', pk=pk)
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


class ProspectAccessMixin(LoginRequiredMixin):
    """Gate prospect views on the can_view_prospects role flag."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _can_view_prospects(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ProspectListView(ProspectAccessMixin, ListView):
    model = Prospect
    template_name = 'core/prospect_list.html'
    context_object_name = 'prospects'
    paginate_by = 25

    def get_queryset(self):
        qs = Prospect.objects.select_related('promoted_to')
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(
                Q(contact_first_name__icontains=search) |
                Q(contact_last_name__icontains=search) |
                Q(company__icontains=search) |
                Q(email__icontains=search)
            )
        status = self.request.GET.get('status')
        if status in dict(Prospect.STATUS_CHOICES):
            qs = qs.filter(status=status)
        elif not self.request.GET.get('show_closed'):
            # Default view hides the finished leads (won/lost).
            qs = qs.exclude(status__in=['won', 'lost'])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_status'] = self.request.GET.get('status', '')
        ctx['show_closed'] = bool(self.request.GET.get('show_closed'))
        ctx['status_choices'] = Prospect.STATUS_CHOICES
        return ctx


class ProspectDetailView(ProspectAccessMixin, DetailView):
    model = Prospect
    template_name = 'core/prospect_detail.html'
    context_object_name = 'prospect'


class ProspectCreateView(ProspectAccessMixin, CreateView):
    model = Prospect
    form_class = ProspectForm
    template_name = 'core/prospect_form.html'

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('core:prospect_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'New Prospect'
        ctx['cancel_url'] = reverse_lazy('core:prospect_list')
        return ctx


class ProspectUpdateView(ProspectAccessMixin, UpdateView):
    model = Prospect
    form_class = ProspectForm
    template_name = 'core/prospect_form.html'

    def get_success_url(self):
        return reverse_lazy('core:prospect_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = f'Edit {self.object.display_name}'
        ctx['cancel_url'] = reverse_lazy('core:prospect_detail', kwargs={'pk': self.object.pk})
        return ctx


class ProspectPromoteView(ProspectAccessMixin, View):
    def post(self, request, pk):
        prospect = get_object_or_404(Prospect, pk=pk)
        if prospect.is_promoted:
            messages.info(request, f'{prospect.display_name} is already a client.')
            return redirect('core:client_detail', pk=prospect.promoted_to_id)
        try:
            client = prospect.promote_to_client()
        except IntegrityError:
            messages.error(
                request,
                f'A client named "{prospect.company or prospect.contact_name}" already '
                'exists. Rename the prospect or merge manually before promoting.'
            )
            return redirect('core:prospect_detail', pk=pk)
        messages.success(request, f'{prospect.display_name} promoted to client.')
        return redirect('core:client_detail', pk=client.pk)


class ProspectMarkLostView(ProspectAccessMixin, View):
    def post(self, request, pk):
        prospect = get_object_or_404(Prospect, pk=pk)
        if prospect.is_promoted:
            messages.error(request, 'A promoted prospect cannot be marked lost.')
            return redirect('core:prospect_detail', pk=pk)
        prospect.status = 'lost'
        prospect.save(update_fields=['status', 'updated_at'])
        messages.success(request, f'{prospect.display_name} marked lost.')
        return redirect('core:prospect_detail', pk=pk)


class ProspectDeleteView(ProspectAccessMixin, View):
    def post(self, request, pk):
        prospect = get_object_or_404(Prospect, pk=pk)
        if prospect.is_promoted:
            messages.error(
                request,
                'This prospect was promoted to a client and cannot be deleted '
                '(it would orphan the link). Delete the client instead if needed.'
            )
            return redirect('core:prospect_detail', pk=pk)
        name = prospect.display_name
        prospect.delete()
        messages.success(request, f'Prospect {name} deleted.')
        return redirect('core:prospect_list')


class EstimateAccessMixin(LoginRequiredMixin):
    """Gate estimate views on the can_view_estimates role flag."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _can_view_estimates(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class EstimateListView(EstimateAccessMixin, ListView):
    model = Estimate
    template_name = 'core/estimate_list.html'
    context_object_name = 'estimates'
    paginate_by = 25

    def get_queryset(self):
        qs = Estimate.objects.select_related('client', 'prospect')
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(
                Q(estimate_number__icontains=search) |
                Q(client__name__icontains=search) |
                Q(prospect__contact_first_name__icontains=search) |
                Q(prospect__contact_last_name__icontains=search) |
                Q(prospect__company__icontains=search) |
                Q(scope__icontains=search)
            )
        status = self.request.GET.get('status')
        if status in dict(Estimate.STATUS_CHOICES):
            qs = qs.filter(status=status)
        elif not self.request.GET.get('show_closed'):
            # Default view hides the finished estimates.
            qs = qs.exclude(status__in=['accepted', 'declined', 'expired'])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_status'] = self.request.GET.get('status', '')
        ctx['show_closed'] = bool(self.request.GET.get('show_closed'))
        ctx['status_choices'] = Estimate.STATUS_CHOICES
        return ctx


class EstimateDetailView(EstimateAccessMixin, DetailView):
    model = Estimate
    template_name = 'core/estimate_detail.html'
    context_object_name = 'estimate'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['catalog_by_category'] = _catalog_by_category()
        ctx['entries'] = _line_items_for(self.object)
        ctx['options'] = self.object.options.all()
        # Details card: inline edit form (Client/Prospect/Context/Scope), same
        # fields used at creation — editing and creating are the same UI.
        ctx['estimate_form'] = EstimateForm(instance=self.object)
        return ctx


def _render_estimate_details_card(request, estimate):
    """Render the Details card partial — used for the initial page render
    (via include) and returned directly by EstimateQuickUpdateView's auto-save
    POST so the HTMX swap shows the just-saved state."""
    from django.template.loader import render_to_string
    return HttpResponse(render_to_string(request=request, template_name='core/partials/estimate_details_card.html', context={
        'estimate': estimate,
        'estimate_form': EstimateForm(instance=estimate),
    }))


class EstimateCreateView(EstimateAccessMixin, View):
    """Creates a blank draft Estimate and lands directly on its detail page —
    no intermediate form. Client/Prospect/Context/Scope are set later via the
    inline edit card on estimate_detail, the same UI used to edit them
    afterward (Mike's call: creation and editing should be one page, not two,
    mirroring the Sale rebuild)."""

    def post(self, request):
        estimate = Estimate.objects.create(created_by=request.user)
        return redirect('core:estimate_detail', pk=estimate.pk)


class EstimateQuickUpdateView(EstimateAccessMixin, View):
    """Auto-save endpoint for the Estimate detail page's Details card: each
    select saves on change, Scope saves on blur — no button, no separate edit
    page. Client and Prospect are mutually exclusive (an estimate anchors to
    exactly one) — setting either one here clears the other, rather than
    surfacing a validation error the way the old full-page form did, since an
    auto-save flow has nowhere good to show a field error."""

    def post(self, request, pk):
        estimate = get_object_or_404(Estimate, pk=pk)
        if not estimate.is_locked:
            if 'client' in request.POST:
                client_id = request.POST.get('client')
                estimate.client_id = int(client_id) if client_id else None
                if estimate.client_id:
                    estimate.prospect_id = None
                estimate.save(update_fields=['client', 'prospect'])
            if 'prospect' in request.POST:
                prospect_id = request.POST.get('prospect')
                estimate.prospect_id = int(prospect_id) if prospect_id else None
                if estimate.prospect_id:
                    estimate.client_id = None
                estimate.save(update_fields=['client', 'prospect'])
            if 'ticket' in request.POST:
                ticket_id = request.POST.get('ticket')
                estimate.ticket_id = int(ticket_id) if ticket_id else None
                estimate.save(update_fields=['ticket'])
            if 'contact' in request.POST:
                contact_id = request.POST.get('contact')
                estimate.contact_id = int(contact_id) if contact_id else None
                estimate.save(update_fields=['contact'])
            if 'device' in request.POST:
                device_id = request.POST.get('device')
                estimate.device_id = int(device_id) if device_id else None
                estimate.save(update_fields=['device'])
            if 'scope' in request.POST:
                estimate.scope = request.POST.get('scope', '')
                estimate.save(update_fields=['scope'])
            if 'expires_on' in request.POST:
                expires_on = request.POST.get('expires_on') or None
                estimate.expires_on = expires_on
                estimate.save(update_fields=['expires_on'])
        return _render_estimate_details_card(request, estimate)


class EstimateMarkSentView(EstimateAccessMixin, View):
    def post(self, request, pk):
        estimate = get_object_or_404(Estimate, pk=pk)
        if estimate.status != 'draft':
            messages.error(request, f'{estimate.estimate_number} is not a draft.')
            return redirect('core:estimate_detail', pk=pk)
        estimate.status = 'sent'
        estimate.save(update_fields=['status', 'updated_at'])
        messages.success(request, f'{estimate.estimate_number} marked sent.')
        return redirect('core:estimate_detail', pk=pk)


class EstimateDeleteView(EstimateAccessMixin, View):
    def post(self, request, pk):
        estimate = get_object_or_404(Estimate, pk=pk)
        if estimate.status == 'accepted':
            messages.error(
                request,
                f'{estimate.estimate_number} was accepted and cannot be deleted '
                '(it would orphan the linked work order).'
            )
            return redirect('core:estimate_detail', pk=pk)
        number = estimate.estimate_number
        estimate.delete()
        messages.success(request, f'Estimate {number} deleted.')
        return redirect('core:estimate_list')


class EstimateLaborLogView(EstimateAccessMixin, View):
    """HTMX: log a catalog item (Product or Service) against an Estimate."""

    def post(self, request, est_pk, item_pk):
        estimate = get_object_or_404(Estimate, pk=est_pk)
        item = get_object_or_404(CatalogItem, pk=item_pk, is_active=True)
        _log_catalog_item(estimate, item, request.user)
        return _render_line_items(request, estimate)


class EstimateCustomLogView(EstimateAccessMixin, View):
    """HTMX: log a fully custom line item — labor or part — on an Estimate."""

    def post(self, request, est_pk):
        estimate = get_object_or_404(Estimate, pk=est_pk)
        label = request.POST.get('custom_label', '').strip()
        notes = request.POST.get('notes', '').strip()
        kind = request.POST.get('kind', 'labor')
        if kind not in ('labor', 'part'):
            kind = 'labor'
        qty = _parse_qty(request.POST.get('quantity'))
        if label:
            estimate.line_items.create(
                kind=kind,
                description=label[:255],
                quantity=qty if qty is not None else 1,
                unit_price=_parse_price(request.POST.get('unit_price')),
                notes=notes,
                logged_by=request.user,
            )
        return _render_line_items(request, estimate)


class EstimateOptionCreateView(EstimateAccessMixin, View):
    """HTMX: add a named pricing option (e.g. "Budget"/"Standard"/"Premium")
    to an Estimate — see EstimateOption. Blank label is ignored (no accidental
    empty option cards)."""

    def post(self, request, est_pk):
        estimate = get_object_or_404(Estimate, pk=est_pk)
        label = request.POST.get('label', '').strip()
        if label and not estimate.is_locked:
            next_order = estimate.options.count()
            estimate.options.create(label=label[:120], sort_order=next_order)
        return _render_line_items(request, estimate)


class EstimateGeneralLabelUpdateView(EstimateAccessMixin, View):
    """HTMX: rename the General section (auto-saves on blur) so it can read
    like a real option name ("Base", "Common Costs") instead of the generic
    default — only meaningful once EstimateOptions exist, but harmless to
    call regardless. Blank input falls back to 'General' rather than saving
    an empty heading."""

    def post(self, request, pk):
        estimate = get_object_or_404(Estimate, pk=pk)
        if not estimate.is_locked:
            label = request.POST.get('general_label', '').strip()
            estimate.general_label = label[:120] if label else 'General'
            estimate.save(update_fields=['general_label'])
        return _render_line_items(request, estimate)


class EstimateOptionSelectView(EstimateAccessMixin, View):
    """HTMX: mark one option as the client's pick — clears any sibling
    selection (mutually exclusive within the estimate). Rejected options stay
    on record, just unselected (Mike's call — nothing is deleted)."""

    def post(self, request, pk):
        option = get_object_or_404(EstimateOption, pk=pk)
        if not option.estimate.is_locked:
            option.select()
        return _render_line_items(request, option)


class EstimateOptionDeleteView(EstimateAccessMixin, View):
    """HTMX: remove a pricing option and its line items entirely (unlike
    de-selecting, this is a real delete — for a mistakenly-added option)."""

    def post(self, request, pk):
        option = get_object_or_404(EstimateOption, pk=pk)
        estimate = option.estimate
        if not estimate.is_locked:
            option.delete()
        return _render_line_items(request, estimate)


class EstimateOptionLaborLogView(EstimateAccessMixin, View):
    """HTMX: log a catalog item (Product or Service) against one option."""

    def post(self, request, opt_pk, item_pk):
        option = get_object_or_404(EstimateOption, pk=opt_pk)
        item = get_object_or_404(CatalogItem, pk=item_pk, is_active=True)
        if not option.estimate.is_locked:
            _log_catalog_item(option, item, request.user)
        return _render_line_items(request, option)


class EstimateOptionCustomLogView(EstimateAccessMixin, View):
    """HTMX: log a fully custom line item — labor or part — on one option."""

    def post(self, request, opt_pk):
        option = get_object_or_404(EstimateOption, pk=opt_pk)
        label = request.POST.get('custom_label', '').strip()
        notes = request.POST.get('notes', '').strip()
        kind = request.POST.get('kind', 'labor')
        if kind not in ('labor', 'part'):
            kind = 'labor'
        qty = _parse_qty(request.POST.get('quantity'))
        if label and not option.estimate.is_locked:
            option.line_items.create(
                kind=kind,
                description=label[:255],
                quantity=qty if qty is not None else 1,
                unit_price=_parse_price(request.POST.get('unit_price')),
                notes=notes,
                logged_by=request.user,
            )
        return _render_line_items(request, option)


class EstimateQuotePrintView(EstimateAccessMixin, View):
    """Browser preview of the quote (new tab) — same template the emailed PDF uses."""

    def get(self, request, pk):
        estimate = get_object_or_404(
            Estimate.objects.select_related('client', 'prospect', 'contact'), pk=pk,
        )
        if not estimate.client_id and not estimate.prospect_id:
            messages.error(request, 'Choose a Client or Prospect before previewing the quote.')
            return redirect('core:estimate_detail', pk=pk)
        site = SiteSettings.get()
        ctx = _quote_report_context(estimate, site)
        return render(request, 'core/estimate_quote_print.html', ctx)


class EstimateQuoteEmailView(EstimateAccessMixin, View):
    """Email the quote to the customer as a PDF attachment, from the sales address.

    GET shows a small recipient form (client contacts dropdown, or a prefilled
    prospect email, or a custom address); POST renders the quote to PDF and
    sends it. A successful send advances a draft estimate to 'sent' — emailing
    the quote IS sending it.
    """

    def get(self, request, pk):
        estimate = get_object_or_404(Estimate.objects.select_related('client', 'prospect'), pk=pk)
        if not estimate.client_id and not estimate.prospect_id:
            messages.error(request, 'Choose a Client or Prospect before emailing the quote.')
            return redirect('core:estimate_detail', pk=pk)
        contacts = estimate.client.contacts.filter(is_active=True) if estimate.client_id else None
        default_contact = None
        if estimate.client_id:
            default_contact = estimate.contact or estimate.client.contacts.filter(
                is_primary=True, is_active=True, email__gt='',
            ).first()
            default_email = (default_contact.email if default_contact else '') or estimate.client.email
        else:
            default_email = estimate.prospect.email
        return render(request, 'core/estimate_email_quote.html', {
            'estimate': estimate,
            'contacts': contacts,
            'default_contact': default_contact,
            'default_email': default_email,
        })

    def post(self, request, pk):
        estimate = get_object_or_404(Estimate.objects.select_related('client', 'prospect'), pk=pk)
        if not estimate.client_id and not estimate.prospect_id:
            messages.error(request, 'Choose a Client or Prospect before emailing the quote.')
            return redirect('core:estimate_detail', pk=pk)

        from .pdf_utils import render_pdf
        from .email_utils import send_document_email
        from django.template.loader import render_to_string

        # Resolve recipient: a chosen client contact, or a custom address
        # (the only path for a prospect-anchored estimate — no Contact rows).
        contact = None
        to_email = (request.POST.get('custom_email') or '').strip()
        contact_id = request.POST.get('contact')
        if not to_email and contact_id and estimate.client_id:
            contact = estimate.client.contacts.filter(pk=contact_id).first()
            to_email = contact.email if contact else ''
        if not to_email:
            messages.error(request, 'Choose a contact or enter an email address.')
            return redirect('core:estimate_quote_email', pk=pk)

        site = SiteSettings.get()
        ctx = _quote_report_context(estimate, site)
        # Same browser-preview/PDF template trick as the repair report: the
        # @media print rules hide the on-screen controls and show the footer.
        html = render_to_string('core/estimate_quote_print.html', ctx)
        try:
            pdf_bytes = render_pdf(html)
        except Exception:
            logger.exception('PDF render failed for quote %s.', estimate.estimate_number)
            messages.error(request, 'Could not generate the quote PDF. The PDF engine may not be installed on this server.')
            return redirect('core:estimate_detail', pk=pk)

        company = site.company_name or "Murphy's Bench"
        sales_from = site.email_sales_from or site.email_from or None
        scope_snippet = f' — {estimate.scope[:60]}' if estimate.scope else ''
        cover = (
            f"Hello,\n\nPlease find attached your quote {estimate.estimate_number}{scope_snippet}.\n\n"
            f"Thank you for considering us.\n{company}"
        )
        log = send_document_email(
            to_email,
            subject=f"{company}: Quote {estimate.estimate_number}",
            cover_body=cover,
            from_email=sales_from,
            reply_to=sales_from,
            attachments=[(f'Quote-{estimate.estimate_number}.pdf', pdf_bytes, 'application/pdf')],
            client=estimate.client,
            contact=contact,
            trigger='estimate_quote',
            related_ticket=estimate.ticket,
        )
        if log.status == 'sent':
            if estimate.status == 'draft':
                estimate.status = 'sent'
                estimate.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'Quote emailed to {to_email}.')
        else:
            messages.error(request, f'Quote not sent ({log.get_reason_display() or log.status}).')
        return redirect('core:estimate_detail', pk=pk)


def _copy_line_items(source, target, user):
    """Snapshot-copy every LineItem from one host (Estimate/WorkOrder) onto
    another. Prices/quantities are copied as-is; the new lines are re-stamped
    with the acting user (logged_at is auto_now_add)."""
    for li in source.line_items.all():
        target.line_items.create(
            kind=li.kind,
            description=li.description,
            quantity=li.quantity,
            unit_price=li.unit_price,
            catalog_item=li.catalog_item,
            notes=li.notes,
            logged_by=user,
        )


class EstimateAcceptView(EstimateAccessMixin, View):
    """Accept a quote: promote a prospect if needed, spawn a Work Order with the
    estimate's lines copied over, and lock the estimate read-only."""

    def post(self, request, pk):
        from django.db import transaction
        estimate = get_object_or_404(Estimate.objects.select_related('client', 'prospect', 'device', 'contact', 'ticket'), pk=pk)
        if estimate.status not in ('draft', 'sent'):
            messages.error(request, f'{estimate.estimate_number} cannot be accepted from its current status.')
            return redirect('core:estimate_detail', pk=pk)

        selected_option = None
        if estimate.options.exists():
            selected_option = estimate.options.filter(is_selected=True).first()
            if not selected_option:
                messages.error(request, f'{estimate.estimate_number} has multiple options — select one before accepting.')
                return redirect('core:estimate_detail', pk=pk)

        try:
            with transaction.atomic():
                # Promote a prospect-anchored estimate to a real Client, then re-anchor.
                if estimate.prospect_id:
                    client = estimate.prospect.promote_to_client()
                    estimate.client = client
                    estimate.prospect = None
                else:
                    client = estimate.client

                # Named contact: the estimate's, else (for a promoted prospect) the
                # client's new primary contact.
                contact = estimate.contact or client.contacts.filter(is_primary=True).first()

                # WorkOrder.ticket is OneToOne — only link if the ticket has no WO yet.
                link_ticket = None
                if estimate.ticket_id and not WorkOrder.objects.filter(ticket=estimate.ticket).exists():
                    link_ticket = estimate.ticket

                wo = WorkOrder.objects.create(
                    work_order_number=WorkOrder.generate_work_order_number(
                        from_ticket_number=link_ticket.ticket_number if link_ticket else None,
                    ),
                    ticket=link_ticket,
                    client=client,
                    device=estimate.device,
                    contact=contact,
                    reported_problem=estimate.scope or '',
                    status='new',
                )
                _copy_line_items(estimate, wo, request.user)
                if selected_option:
                    _copy_line_items(selected_option, wo, request.user)

                estimate.status = 'accepted'
                estimate.accepted_at = timezone.now()
                estimate.work_order = wo
                estimate.save(update_fields=['status', 'accepted_at', 'work_order', 'client', 'prospect', 'updated_at'])
        except IntegrityError:
            messages.error(
                request,
                'Could not accept: a client with that name already exists. '
                'Reconcile the prospect/client before accepting.'
            )
            return redirect('core:estimate_detail', pk=pk)

        msg = f'{estimate.estimate_number} accepted — created {wo.work_order_number}.'
        if estimate.ticket_id and link_ticket is None:
            msg += ' (The linked ticket already had a work order, so this one is standalone.)'
        messages.success(request, msg)
        return redirect('core:work_order_detail', pk=wo.pk)


class EstimateDeclineView(EstimateAccessMixin, View):
    """Record that a quote was declined, with a reason. No WO, no stock effect."""

    def post(self, request, pk):
        estimate = get_object_or_404(Estimate, pk=pk)
        if estimate.status not in ('draft', 'sent'):
            messages.error(request, f'{estimate.estimate_number} cannot be declined from its current status.')
            return redirect('core:estimate_detail', pk=pk)
        reason = (request.POST.get('decline_reason') or '').strip()
        if not reason:
            messages.error(request, 'A reason is required to decline a quote.')
            return redirect('core:estimate_detail', pk=pk)
        estimate.status = 'declined'
        estimate.decline_reason = reason
        estimate.save(update_fields=['status', 'decline_reason', 'updated_at'])
        messages.success(request, f'{estimate.estimate_number} marked declined.')
        return redirect('core:estimate_detail', pk=pk)


class EstimateReviseView(EstimateAccessMixin, View):
    """Supersede a sent/declined/expired quote with a new linked draft revision."""

    def post(self, request, pk):
        from django.db import transaction
        old = get_object_or_404(Estimate, pk=pk)
        if old.status not in ('sent', 'declined', 'expired'):
            messages.error(request, f'{old.estimate_number} cannot be revised from its current status.')
            return redirect('core:estimate_detail', pk=pk)
        with transaction.atomic():
            new = Estimate.objects.create(
                client=old.client,
                prospect=old.prospect,
                ticket=old.ticket,
                contact=old.contact,
                device=old.device,
                scope=old.scope,
                expires_on=old.expires_on,
                revision_of=old,
                status='draft',
                created_by=request.user,
            )
            _copy_line_items(old, new, request.user)
        messages.success(request, f'Created {new.estimate_number} as a revision of {old.estimate_number}.')
        return redirect('core:estimate_detail', pk=new.pk)


class SaleAccessMixin(LoginRequiredMixin):
    """Gate sale views on the can_view_sales role flag."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _can_view_sales(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class SaleListView(SaleAccessMixin, ListView):
    model = Sale
    template_name = 'core/sale_list.html'
    context_object_name = 'sales'
    paginate_by = 25

    def get_queryset(self):
        qs = Sale.objects.select_related('client')
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(
                Q(sale_number__icontains=search) |
                Q(client__name__icontains=search)
            )
        status = self.request.GET.get('status')
        if status in dict(Sale.STATUS_CHOICES):
            qs = qs.filter(status=status)
        elif not self.request.GET.get('show_closed'):
            # Default view hides voided sales.
            qs = qs.exclude(status='void')
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_status'] = self.request.GET.get('status', '')
        ctx['show_closed'] = bool(self.request.GET.get('show_closed'))
        ctx['status_choices'] = Sale.STATUS_CHOICES
        return ctx


def _render_sale_customer_card(request, sale):
    """Render the Customer card partial — used for the initial page render
    (via include) and returned directly by SaleQuickUpdateView's auto-save
    POST so the HTMX swap shows the just-saved state."""
    from django.template.loader import render_to_string
    return HttpResponse(render_to_string(request=request, template_name='core/partials/sale_customer_card.html', context={
        'sale': sale,
        'sale_form': SaleForm(instance=sale),
    }))


def _sale_checkout_context(sale):
    """Checkout-card context, shared by the initial page render and the
    out-of-band refresh triggered whenever a line item is logged/deleted —
    has_priced_lines and the amount prefill must reflect the CURRENT line
    total, not the total at the time the page first loaded."""
    from decimal import Decimal
    prefill_amount = sale.line_items_total.quantize(Decimal('0.01'))
    return {
        'checkout_form': SaleCheckoutForm(initial={'amount': prefill_amount}),
        'has_priced_lines': sale.line_items_total > 0,
        'invoice_ninja_enabled': SiteSettings.get().invoice_ninja_enabled,
    }


class SaleDetailView(SaleAccessMixin, DetailView):
    model = Sale
    template_name = 'core/sale_detail.html'
    context_object_name = 'sale'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['catalog_by_category'] = _catalog_by_category()
        ctx['entries'] = _line_items_for(self.object)
        # Customer card: inline edit form (Client/Contact/Notes), same fields
        # used at creation — editing and creating are the same UI.
        ctx['sale_form'] = SaleForm(instance=self.object)
        ctx.update(_sale_checkout_context(self.object))
        return ctx


class SaleCreateView(SaleAccessMixin, View):
    """Creates a blank draft Sale and lands directly on its detail page — no
    intermediate form. Customer/Contact/Notes are set later via the inline
    edit card on sale_detail, the same UI used to edit them afterward (Mike's
    call: creation and editing should be one page, not two)."""

    def post(self, request):
        sale = Sale.objects.create(created_by=request.user)
        return redirect('core:sale_detail', pk=sale.pk)


class SaleQuickUpdateView(SaleAccessMixin, View):
    """Auto-save endpoint for the Sale detail page's Customer card: Client
    saves on change, Notes saves on blur — no button, no separate edit step
    (Mike's call: picking a client/walk-in is all that should be required).
    Always returns the freshly-rendered card fragment for the HTMX swap."""

    def post(self, request, pk):
        sale = get_object_or_404(Sale, pk=pk)
        if not sale.is_locked:
            # Client and Notes save independently (different hx-trigger each) —
            # only touch whichever field this particular request actually sent,
            # never both, or a Client-only save would blank out Notes (and
            # vice versa) since a partial POST leaves the other key absent.
            if 'client' in request.POST:
                client_id = request.POST.get('client')
                sale.client_id = int(client_id) if client_id else None
                sale.save(update_fields=['client'])
            if 'notes' in request.POST:
                sale.notes = request.POST.get('notes', '')
                sale.save(update_fields=['notes'])
        return _render_sale_customer_card(request, sale)


class SaleDeleteView(SaleAccessMixin, View):
    def post(self, request, pk):
        sale = get_object_or_404(Sale, pk=pk)
        if sale.status == 'completed':
            messages.error(
                request,
                f'{sale.sale_number} was completed and cannot be deleted.'
            )
            return redirect('core:sale_detail', pk=pk)
        number = sale.sale_number
        sale.delete()
        messages.success(request, f'Sale {number} deleted.')
        return redirect('core:sale_list')


def _recurring_sale_this_month(client, today=None):
    """This calendar month's recurring Sale for a client, or None. The idempotency
    key for the whole lane — one recurring charge per client per month."""
    today = today or timezone.localdate()
    return Sale.objects.filter(
        client=client, is_recurring=True,
        created_at__year=today.year, created_at__month=today.month,
    ).first()


def _monthly_row_state(sale):
    """Derive a managed client's state for this month from its recurring Sale:
      not_prepared → no sale yet
      prepared     → sale exists in MB, not yet pushed to IN
      draft_in_in  → pushed as a draft invoice, awaiting payment
      paid         → IN reports it Paid (read back)
    """
    if sale is None:
        return 'not_prepared'
    if not sale.invoice_ninja_id:
        return 'prepared'
    if (sale.in_status or '').lower() == 'paid':
        return 'paid'
    return 'draft_in_in'


def _prepare_recurring_sale(client, user, today=None):
    """Create this month's recurring draft Sale by CLONING the client's recurring
    template lines (named catalog services / custom lines, each with qty + price)
    into it. A client with no template lines falls back to a single generic
    'Monthly Service' line at its flat monthly_amount — so simple clients stay
    simple. Idempotent per calendar month. Nothing touches Invoice Ninja here."""
    sale = _recurring_sale_this_month(client, today)
    if sale is not None:
        return sale, False
    sale = Sale.objects.create(client=client, is_recurring=True, created_by=user)
    template = list(client.line_items.all())
    if template:
        for tl in template:
            LineItem.objects.create(
                content_object=sale, kind=tl.kind, description=tl.description,
                quantity=tl.quantity, unit_price=tl.unit_price,
                catalog_item=tl.catalog_item, notes=tl.notes, logged_by=user,
            )
    else:
        LineItem.objects.create(
            content_object=sale, kind='labor', description='Monthly Service',
            quantity=1, unit_price=client.monthly_amount,
            logged_by=user,
        )
    return sale, True


class MonthlyClientsListView(SaleAccessMixin, ListView):
    """The recurring-billing worklist (Lane C): every is_managed client, each with
    this month's recurring Sale resolved to a state (Not prepared / Prepared /
    Draft in IN / Paid), their own billing day, and whether they're due yet.
    Reuses can_view_sales — a recurring charge is just a kind of Sale."""

    model = Client
    template_name = 'core/monthly_clients_list.html'
    context_object_name = 'clients'

    def get_queryset(self):
        return Client.objects.filter(is_managed=True, is_active=True).order_by('billing_day', 'name')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        sales_this_month = {
            sale.client_id: sale
            for sale in Sale.objects.filter(
                client__in=ctx['clients'], is_recurring=True,
                created_at__year=today.year, created_at__month=today.month,
            )
        }
        rows = []
        due_unprepared = 0        # due, no sale yet → the batch "Prepare all due" target
        prepared_unsent = 0       # prepared in MB, not yet pushed → "Send drafts" target
        for client in ctx['clients']:
            sale = sales_this_month.get(client.id)
            state = _monthly_row_state(sale)
            is_due = client.is_billing_due(today)
            rows.append({
                'client': client,
                'sale': sale,
                'state': state,
                'is_due': is_due,
                'billing_date': client.effective_billing_date(today.year, today.month),
            })
            if is_due and state == 'not_prepared':
                due_unprepared += 1
            if state == 'prepared':
                prepared_unsent += 1
        ctx['rows'] = rows
        ctx['due_unprepared_count'] = due_unprepared
        ctx['prepared_unsent_count'] = prepared_unsent
        ctx['invoice_ninja_enabled'] = SiteSettings.get().invoice_ninja_enabled
        ctx['today'] = today
        return ctx


class MonthlyPrepareView(SaleAccessMixin, View):
    """Single 'Prepare draft' on the worklist — creates this month's editable
    recurring Sale for one client and lands on it so the amount can be reviewed.
    Idempotent per month. Nothing is sent to Invoice Ninja here."""

    def post(self, request, pk):
        client = get_object_or_404(Client, pk=pk, is_managed=True)
        sale, _created = _prepare_recurring_sale(client, request.user)
        return redirect('core:sale_detail', pk=sale.pk)


class SaleDraftSendView(SaleAccessMixin, View):
    """Send ONE recurring sale to Invoice Ninja as an unpaid DRAFT (no payment
    posted, nothing charged). Phase-1 path: MB creates the draft, the operator
    charges the card by hand in IN. Duplicate-guarded like the counter re-send."""

    def post(self, request, pk):
        from . import invoice_ninja
        sale = get_object_or_404(Sale, pk=pk, is_recurring=True)
        if not SiteSettings.get().invoice_ninja_enabled:
            messages.error(request, 'Invoice Ninja is not enabled in Settings.')
            return redirect('core:sale_detail', pk=pk)
        if sale.line_items_total <= 0:
            messages.error(request, 'Add a priced line item before sending a draft.')
            return redirect('core:sale_detail', pk=pk)
        if sale.invoice_ninja_id and request.POST.get('confirm_resend') != '1':
            messages.warning(
                request,
                f'{sale.sale_number} was already sent as a draft '
                f'(#{sale.invoice_ninja_ref or sale.invoice_ninja_id}). Use "Re-send draft" to push again.'
            )
            return redirect('core:sale_detail', pk=pk)
        try:
            ref = invoice_ninja.push_sale(sale, draft=True)
            messages.success(
                request,
                f'{sale.sale_number} sent to Invoice Ninja as a draft — invoice #{ref}. '
                'Charge the card in Invoice Ninja, then use "Check IN" to record payment.'
            )
        except invoice_ninja.InvoiceNinjaError as e:
            messages.error(request, f'Sending the draft to Invoice Ninja failed: {e}')
        return redirect('core:sale_detail', pk=pk)


class SaleCheckINView(SaleAccessMixin, View):
    """Read a recurring sale's invoice status back from IN (Draft/Paid/…) so the
    worklist reflects payments the operator made by hand in IN, without re-entry."""

    def post(self, request, pk):
        from . import invoice_ninja
        sale = get_object_or_404(Sale, pk=pk)
        next_url = request.POST.get('next') or reverse('core:sale_detail', kwargs={'pk': pk})
        if not sale.invoice_ninja_id:
            messages.error(request, f'{sale.sale_number} has not been sent to Invoice Ninja yet.')
            return redirect(next_url)
        try:
            label = invoice_ninja.check_sale_status(sale)
            messages.success(request, f'{sale.sale_number}: Invoice Ninja reports {label}.')
        except invoice_ninja.InvoiceNinjaError as e:
            messages.error(request, f'Could not read status from Invoice Ninja: {e}')
        return redirect(next_url)


class SaleChargeView(SaleAccessMixin, View):
    """Slice 5d — trigger Invoice Ninja to charge the client's card on file for
    an already-pushed sale. GET renders a confirmation screen (server-computed
    amount, explicit warning) before anything fires; POST performs the guarded
    charge. Gated on can_process_payments (opt-in, default off — see
    _can_process_payments), separately from can_view_sales, since viewing sales
    and moving money are very different privilege levels.

    Every attempt (success or failure) writes an immutable PaymentChargeAttempt
    row. The charge itself only PROVES the trigger was queued in IN — it never
    marks the sale Paid; that only ever comes from the check_sale_status
    read-back this view calls afterward (see invoice_ninja.charge_sale_on_file)."""

    def _amount(self, sale):
        # Server-side only, from the sale's current priced line items — the
        # same total that was on the invoice when it was pushed to IN (mirrors
        # _sale_checkout_context's prefill). sale.amount is a counter-lane-only
        # field (set by SaleCheckoutView) and doesn't apply to recurring sales,
        # so it is deliberately NOT used here. Never trust an amount from the request.
        from decimal import Decimal
        return sale.line_items_total.quantize(Decimal('0.01'))

    def get(self, request, pk):
        if not _can_process_payments(request.user):
            return HttpResponse('Forbidden', status=403)
        sale = get_object_or_404(Sale, pk=pk)
        if not sale.invoice_ninja_id:
            messages.error(request, f'{sale.sale_number} has not been sent to Invoice Ninja yet — send it as a draft first.')
            return redirect('core:sale_detail', pk=pk)
        if (sale.in_status or '').strip().lower() == 'paid':
            messages.info(request, f'{sale.sale_number} is already marked Paid.')
            return redirect('core:sale_detail', pk=pk)
        return render(request, 'core/sale_charge_confirm.html', {
            'sale': sale,
            'amount': self._amount(sale),
        })

    # A charge is async on IN's side; a second trigger fired before the first
    # settles could double-charge. Refuse another charge on the same sale within
    # this window of a prior *successful* trigger (kills double-clicks, back-button
    # resubmits, and the in-flight race the stored-status check can't see). A
    # legitimate retry after a confirmed decline is still possible once it lapses.
    _CHARGE_COOLDOWN = timedelta(minutes=5)

    def post(self, request, pk):
        from . import invoice_ninja
        if not _can_process_payments(request.user):
            return HttpResponse('Forbidden', status=403)
        sale = get_object_or_404(Sale, pk=pk)
        if not SiteSettings.get().invoice_ninja_enabled:
            messages.error(request, 'Invoice Ninja is not enabled in Settings.')
            return redirect('core:sale_detail', pk=pk)

        recent = PaymentChargeAttempt.objects.filter(
            sale=sale, result='success',
            initiated_at__gte=timezone.now() - self._CHARGE_COOLDOWN,
        ).exists()
        if recent:
            messages.warning(
                request,
                f'{sale.sale_number} was charged moments ago. Use "Check IN" to confirm '
                'it posted before charging again.'
            )
            return redirect('core:sale_detail', pk=pk)

        amount = self._amount(sale)
        try:
            label = invoice_ninja.charge_sale_on_file(sale)
            PaymentChargeAttempt.objects.create(
                sale=sale, invoice_ninja_id=sale.invoice_ninja_id, amount=amount,
                initiated_by=request.user, result='success', in_status_after=label,
            )
            messages.success(
                request,
                f'{sale.sale_number}: charge initiated in Invoice Ninja (${amount}). '
                f'Invoice Ninja currently reports "{label}" — the charge runs '
                'asynchronously, so use "Check IN" in a moment to confirm it posted.'
            )
        except invoice_ninja.InvoiceNinjaError as e:
            PaymentChargeAttempt.objects.create(
                sale=sale, invoice_ninja_id=sale.invoice_ninja_id, amount=amount,
                initiated_by=request.user, result='failed', error_message=str(e),
            )
            messages.error(request, f'Charging the card on file failed: {e}')
        return redirect('core:sale_detail', pk=pk)


class MonthlyBatchPrepareView(SaleAccessMixin, View):
    """Batch 'Prepare all due' — create this month's editable draft Sale for every
    managed+active client whose billing day has arrived and who has no sale yet.
    Nothing touches IN; the operator reviews the worklist before sending."""

    def post(self, request):
        today = timezone.localdate()
        clients = Client.objects.filter(is_managed=True, is_active=True)
        prepared = 0
        for client in clients:
            if not client.is_billing_due(today):
                continue
            _sale, created = _prepare_recurring_sale(client, request.user, today)
            if created:
                prepared += 1
        if prepared:
            messages.success(request, f'Prepared {prepared} draft{"s" if prepared != 1 else ""}. Review amounts, then send to Invoice Ninja.')
        else:
            messages.info(request, 'No new drafts to prepare — every due client already has one.')
        return redirect('core:monthly_clients_list')


class MonthlyBatchSendView(SaleAccessMixin, View):
    """Batch 'Send prepared drafts to Invoice Ninja' — the safety catch. GET shows
    a confirmation listing exactly what will be created in IN (each client, amount,
    billing date, and the grand total); POST (confirmed) pushes each as a DRAFT.
    Only prepared-but-not-yet-pushed sales are eligible — never re-pushes."""

    def _prepared_sales(self, today):
        return list(
            Sale.objects.filter(
                is_recurring=True, invoice_ninja_id='',
                created_at__year=today.year, created_at__month=today.month,
                client__is_managed=True, client__is_active=True,
            ).select_related('client').order_by('client__billing_day', 'client__name')
        )

    def get(self, request):
        from decimal import Decimal
        if not SiteSettings.get().invoice_ninja_enabled:
            messages.error(request, 'Invoice Ninja is not enabled in Settings.')
            return redirect('core:monthly_clients_list')
        today = timezone.localdate()
        sales = self._prepared_sales(today)
        rows = [{
            'sale': s,
            'client': s.client,
            'amount': s.line_items_total,
            'billing_date': s.client.effective_billing_date(today.year, today.month),
        } for s in sales]
        total = sum((r['amount'] for r in rows), Decimal('0'))
        return render(request, 'core/monthly_batch_send_confirm.html', {
            'rows': rows, 'total': total, 'count': len(rows),
        })

    def post(self, request):
        from . import invoice_ninja
        if not SiteSettings.get().invoice_ninja_enabled:
            messages.error(request, 'Invoice Ninja is not enabled in Settings.')
            return redirect('core:monthly_clients_list')
        today = timezone.localdate()
        sales = self._prepared_sales(today)
        sent, failed = 0, []
        for sale in sales:
            if sale.line_items_total <= 0:
                failed.append(f'{sale.client.name} (no priced line)')
                continue
            try:
                invoice_ninja.push_sale(sale, draft=True)
                sent += 1
            except invoice_ninja.InvoiceNinjaError as e:
                failed.append(f'{sale.client.name} ({e})')
        if sent:
            messages.success(request, f'Sent {sent} draft{"s" if sent != 1 else ""} to Invoice Ninja.')
        if failed:
            messages.warning(request, 'Some drafts were not sent: ' + '; '.join(failed))
        if not sent and not failed:
            messages.info(request, 'No prepared drafts to send.')
        return redirect('core:monthly_clients_list')


class ClientRecurringLogView(SaleAccessMixin, View):
    """HTMX: add a catalog item to a client's recurring monthly template."""

    def post(self, request, client_pk, item_pk):
        client = get_object_or_404(Client, pk=client_pk)
        item = get_object_or_404(CatalogItem, pk=item_pk, is_active=True)
        _log_catalog_item(client, item, request.user)
        return _render_line_items(request, client)


class ClientRecurringCustomLogView(SaleAccessMixin, View):
    """HTMX: add a fully custom line to a client's recurring monthly template."""

    def post(self, request, client_pk):
        client = get_object_or_404(Client, pk=client_pk)
        label = request.POST.get('custom_label', '').strip()
        notes = request.POST.get('notes', '').strip()
        kind = request.POST.get('kind', 'labor')
        if kind not in ('labor', 'part'):
            kind = 'labor'
        qty = _parse_qty(request.POST.get('quantity'))
        if label:
            client.line_items.create(
                kind=kind,
                description=label[:255],
                quantity=qty if qty is not None else 1,
                unit_price=_parse_price(request.POST.get('unit_price')),
                notes=notes,
                logged_by=request.user,
            )
        return _render_line_items(request, client)


class SaleLaborLogView(SaleAccessMixin, View):
    """HTMX: log a catalog item (Product or Service) against a Sale."""

    def post(self, request, sale_pk, item_pk):
        sale = get_object_or_404(Sale, pk=sale_pk)
        item = get_object_or_404(CatalogItem, pk=item_pk, is_active=True)
        _log_catalog_item(sale, item, request.user)
        return _render_line_items(request, sale)


class SaleCustomLogView(SaleAccessMixin, View):
    """HTMX: log a fully custom line item — labor or part — on a Sale."""

    def post(self, request, sale_pk):
        sale = get_object_or_404(Sale, pk=sale_pk)
        label = request.POST.get('custom_label', '').strip()
        notes = request.POST.get('notes', '').strip()
        kind = request.POST.get('kind', 'part')
        if kind not in ('labor', 'part'):
            kind = 'part'
        qty = _parse_qty(request.POST.get('quantity'))
        if label:
            sale.line_items.create(
                kind=kind,
                description=label[:255],
                quantity=qty if qty is not None else 1,
                unit_price=_parse_price(request.POST.get('unit_price')),
                notes=notes,
                logged_by=request.user,
            )
        return _render_line_items(request, sale)


class SaleCheckoutView(SaleAccessMixin, View):
    """Complete a counter sale: record how it was paid and (if IN is enabled) push
    a PAID invoice to Invoice Ninja in one action. Bundled one-click: if the IN push
    fails, the sale still completes locally and a retry button appears — the recorded
    payment is never lost. MB never charges anything; it mirrors a payment already taken."""

    def post(self, request, pk):
        from django.db import transaction
        from . import invoice_ninja
        sale = get_object_or_404(Sale, pk=pk)

        if sale.is_locked:
            messages.error(request, f'{sale.sale_number} is already {sale.get_status_display().lower()}.')
            return redirect('core:sale_detail', pk=pk)
        if sale.line_items_total <= 0:
            messages.error(request, 'Add at least one priced line item before checkout.')
            return redirect('core:sale_detail', pk=pk)

        form = SaleCheckoutForm(request.POST, instance=sale)
        if not form.is_valid():
            for errors in form.errors.values():
                for err in errors:
                    messages.error(request, err)
            return redirect('core:sale_detail', pk=pk)

        with transaction.atomic():
            sale = form.save(commit=False)
            sale.paid_at = timezone.now()
            sale.status = 'completed'
            sale.save()

        # Bundled push (client sale → its IN client; anonymous → the "Walk-In" client).
        if SiteSettings.get().invoice_ninja_enabled:
            try:
                ref = invoice_ninja.push_sale(sale)
                messages.success(request, f'{sale.sale_number} completed and sent to Invoice Ninja as paid — invoice #{ref}.')
            except invoice_ninja.InvoiceNinjaError as e:
                messages.warning(
                    request,
                    f'{sale.sale_number} was completed and the payment recorded, but sending to '
                    f'Invoice Ninja failed: {e} Use "Retry Send to Invoice Ninja".'
                )
        else:
            messages.success(request, f'{sale.sale_number} completed. Payment recorded.')
        return redirect('core:sale_detail', pk=pk)


class SaleSendINView(SaleAccessMixin, View):
    """Retry / re-send a completed sale to Invoice Ninja as a paid invoice.
    Used when the bundled checkout push failed, or to deliberately re-send."""

    def post(self, request, pk):
        from . import invoice_ninja
        sale = get_object_or_404(Sale, pk=pk)
        if not SiteSettings.get().invoice_ninja_enabled:
            messages.error(request, 'Invoice Ninja is not enabled in Settings.')
            return redirect('core:sale_detail', pk=pk)
        if sale.status != 'completed':
            messages.error(request, 'Only a completed sale can be sent to Invoice Ninja.')
            return redirect('core:sale_detail', pk=pk)
        # Duplicate guard: already pushed and not an explicit confirmed re-send.
        if sale.invoice_ninja_id and request.POST.get('confirm_resend') != '1':
            messages.warning(
                request,
                f'{sale.sale_number} was already sent to Invoice Ninja '
                f'(#{sale.invoice_ninja_ref or sale.invoice_ninja_id}). Use "Re-send" to push again.'
            )
            return redirect('core:sale_detail', pk=pk)
        try:
            ref = invoice_ninja.push_sale(sale)
            messages.success(request, f'Sent to Invoice Ninja as paid — invoice #{ref}.')
        except invoice_ninja.InvoiceNinjaError as e:
            messages.error(request, f'Could not send to Invoice Ninja: {e}')
        return redirect('core:sale_detail', pk=pk)


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
                reverse_lazy('core:work_order_create') + f'?device={self.object.pk}'
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
        client_id = self.request.GET.get('client')
        context['back_client'] = Client.objects.filter(pk=client_id).first() if client_id else None
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
                reverse_lazy('core:work_order_create') + f'?device={self.object.pk}'
            )
        return redirect(reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk}))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.name}'
        context['cancel_url'] = reverse_lazy('core:device_detail', kwargs={'pk': self.object.pk})
        return context


class DeviceDeleteView(LoginRequiredMixin, View):
    """Hard-delete a device. Admin only.

    Used to clean up duplicate/erroneous device records. Linked work orders and
    tickets survive — both FKs are SET_NULL, and work orders keep their snapshotted
    hardware specs (as-serviced history). The credential access log cascades.
    Redirects back to the owning client (the device-centric hub).
    """

    def post(self, request, pk):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        device = get_object_or_404(Device, pk=pk)
        client_pk = device.client_id
        name = device.name
        wo_count = device.work_orders.count()
        device.delete()
        msg = f'Device "{name}" deleted.'
        if wo_count:
            msg += (f' {wo_count} work order(s) kept their as-serviced record '
                    f'but no longer link to a device.')
        messages.success(request, msg)
        return redirect('core:client_detail', pk=client_pk)


# --- Ticket Views ---

TICKET_CLOSED_STATUSES = ['resolved', 'closed']


class TicketListView(LoginRequiredMixin, ListView):
    model = Ticket
    template_name = 'core/ticket_list.html'
    context_object_name = 'tickets'
    paginate_by = 25

    def get_queryset(self):
        queryset = Ticket.objects.select_related('client', 'device', 'created_by', 'assigned_to')
        queryset = _scope_tickets_for(queryset, self.request.user)

        triage = self.request.GET.get('triage')
        needs_response = self.request.GET.get('needs_response')
        if triage:
            # Unsorted/Unverified bucket — inbound senders awaiting onboarding.
            queryset = queryset.filter(client__is_unsorted=True)
        elif needs_response:
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
            queryset = Ticket.overdue_queryset(queryset)

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
        base_qs = _scope_tickets_for(Ticket.objects.all(), self.request.user)
        context['active_count'] = base_qs.exclude(status__in=TICKET_CLOSED_STATUSES).count()
        context['closed_count'] = base_qs.filter(status__in=TICKET_CLOSED_STATUSES).count()
        context['current_tab'] = self.request.GET.get('tab', 'active')
        context['needs_response_filter'] = self.request.GET.get('needs_response', '')
        context['triage_filter'] = self.request.GET.get('triage', '')
        return context


class TicketDetailView(LoginRequiredMixin, DetailView):
    model = Ticket
    template_name = 'core/ticket_detail.html'
    context_object_name = 'ticket'

    def get_queryset(self):
        qs = Ticket.objects.select_related(
            'client', 'device', 'created_by'
        ).prefetch_related('replies', 'replies__created_by')
        # A tech can't open another tech's ticket by URL — same rule as the list.
        return _scope_tickets_for(qs, self.request.user)

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        ticket = self.object
        # Once the assignee opens it, it's no longer "new to you".
        if ticket.assigned_to_id == request.user.id and ticket.assignment_unseen:
            ticket.assignment_unseen = False
            ticket.save(update_fields=['assignment_unseen'])
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
        context['ticket_statuses'] = StatusDefinition.objects.filter(
            entity_type='ticket', is_active=True
        ).order_by('sort_order')
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

    def get_queryset(self):
        return _scope_tickets_for(Ticket.objects.all(), self.request.user)

    def form_valid(self, form):
        new_status = form.cleaned_data.get('status')
        # NOTE: MB deliberately does NOT block closing a ticket whose linked WO is
        # still open. How a shop sequences ticket-close vs WO-completion is a policy
        # decision that belongs to the shop, not the software. (A WO completing still
        # never auto-closes its ticket — see AUTO_RESOLVE_TICKET_ON_WO_CLOSE.)
        # If client changed, clear device (it belongs to the old client)
        new_client = form.cleaned_data.get('client')
        # Ticket.objects.get() (not self.object) — by the time form_valid runs,
        # form.is_valid()'s _post_clean() has already mutated self.object (client,
        # status, everything in Meta.fields) in memory to the NEW values, so
        # self.object no longer reflects what's still in the DB. Read the
        # pre-save row fresh to get the real old client/status. (This same bug
        # previously meant `old_status = self.object.status` below was always
        # equal to the new status — status-changed emails from this edit form
        # never fired. Fixed here alongside the Slice 4 closed_at stamping that
        # needs the real old status too.)
        old_client_id, old_client_was_unsorted, old_status = Ticket.objects.filter(
            pk=self.object.pk
        ).values_list('client_id', 'client__is_unsorted', 'status').first()
        client_changed = bool(new_client) and old_client_id != new_client.pk
        if client_changed:
            form.instance.device = None
        # Triage: an Unsorted ticket picks up its real client's type-default SLA
        # the moment it's reassigned off the bucket — unless this same edit also
        # picked an SLA plan by hand, which always wins.
        triage_resnapshot = (
            client_changed and old_client_was_unsorted
            and 'sla_plan' not in form.changed_data
        )
        # Stamp/clear closed_at before the save (TicketForm.save() writes the
        # whole instance, so this rides the same DB write — no extra query).
        if new_status:
            if new_status in Ticket.CLOSED_AT_STATUSES and old_status not in Ticket.CLOSED_AT_STATUSES:
                form.instance.closed_at = timezone.now()
            elif new_status not in Ticket.CLOSED_AT_STATUSES:
                form.instance.closed_at = None
        response = super().form_valid(form)
        if triage_resnapshot:
            self.object.assign_default_sla_for_client()
            self.object.save(update_fields=['sla_plan', 'due_at', 'overdue_acknowledged_by', 'overdue_acknowledged_at'])
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
        ticket = _get_scoped_ticket_or_404(request, pk)
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
            update_fields = []
            if ticket.needs_response:
                ticket.needs_response = False
                update_fields.append('needs_response')
            if ticket.first_responded_at is None:
                # First staff response meets the response SLA permanently.
                ticket.first_responded_at = timezone.now()
                update_fields.append('first_responded_at')
            if update_fields:
                ticket.save(update_fields=update_fields + ['updated_at'])
            from .email_utils import send_ticket_email
            prior_replies = list(
                ticket.replies.filter(reply_type='customer_visible')
                .exclude(pk=reply.pk)
                .order_by('created_at')
            )
            cc_raw = request.POST.get('cc_emails', '')
            extra = [e.strip() for e in cc_raw.split(',') if e.strip()]
            # Default to BCC; CC only when explicitly chosen.
            mode = request.POST.get('cc_mode', 'bcc')
            send_kwargs = {'cc': extra} if mode == 'cc' else {'bcc': extra}
            send_ticket_email('reply_added', ticket, {
                'reply': reply,
                'prior_replies': prior_replies,
            }, **send_kwargs)
        return render(request, 'core/partials/ticket_reply_item.html', {'reply': reply})


class TicketReplyResendView(LoginRequiredMixin, View):
    """Resend a specific reply email to a chosen address."""

    def post(self, request, pk, reply_pk):
        ticket = _get_scoped_ticket_or_404(request, pk)
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
        ticket = _get_scoped_ticket_or_404(request, pk)
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


class TicketReopenView(LoginRequiredMixin, View):
    """One-click reopen for a closed/resolved ticket flagged by a client reply
    (Slice 4 — the reply threads in and flags but deliberately does not reopen
    on its own; a human decides Reopen vs Dismiss). Leaves needs_response as-is
    — Reopen makes the ticket active again; actually replying is what clears
    the flag, same as any other needs_response ticket."""

    def post(self, request, pk):
        ticket = _get_scoped_ticket_or_404(request, pk)
        if ticket.status not in Ticket.CLOSED_AT_STATUSES:
            messages.info(request, 'Ticket is not closed.')
            return redirect('core:ticket_detail', pk=pk)
        ticket.apply_status_change('open')
        ticket.save(update_fields=['status', 'closed_at', 'updated_at'])
        messages.success(request, f'{ticket.ticket_number} reopened.')
        return redirect('core:ticket_detail', pk=pk)


class TicketConvertView(LoginRequiredMixin, View):
    """Convert a ticket to a work order"""

    def get(self, request, pk):
        ticket = _get_scoped_ticket_or_404(request, pk)
        if ticket.status == 'converted':
            return redirect('core:ticket_detail', pk=pk)
        form = TicketConvertForm()
        return render(request, 'core/ticket_convert.html', {'ticket': ticket, 'form': form})

    def post(self, request, pk):
        ticket = _get_scoped_ticket_or_404(request, pk)
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
            reported_problem=ticket.description or '',
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
        ticket = _get_scoped_ticket_or_404(request, pk)
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
        ticket = _get_scoped_ticket_or_404(request, pk)
        ticket_number = request.POST.get('ticket_number', '').strip()
        link_type = request.POST.get('link_type', 'related')
        error = None

        try:
            other = _scope_tickets_for(Ticket.objects.all(), request.user).get(
                ticket_number__iexact=ticket_number
            )
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
        if not _can_access_attachment(request.user, attachment):
            raise Http404
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
        ticket = _get_scoped_ticket_or_404(request, pk)
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
    """Claim / Transfer / unassign a ticket. POST claim=1 to self-assign, or
    assigned_to=<pk> (or '' to unassign) to transfer."""

    def post(self, request, pk):
        # Can only act on a ticket the user is allowed to see.
        ticket = get_object_or_404(_scope_tickets_for(Ticket.objects.all(), request.user), pk=pk)
        if request.POST.get('claim'):
            ticket.assigned_to = request.user
            ticket.assignment_unseen = False
        else:
            uid = request.POST.get('assigned_to', '').strip()
            if uid:
                ticket.assigned_to_id = int(uid)
                # Flag "new to you" only when handed to someone other than the actor.
                ticket.assignment_unseen = (int(uid) != request.user.id)
            else:
                ticket.assigned_to_id = None
                ticket.assignment_unseen = False
        ticket.save(update_fields=['assigned_to', 'assignment_unseen', 'updated_at'])
        # If they transferred it away and can no longer see it, send them to the list.
        if not _is_admin(request.user) and not _scope_tickets_for(
            Ticket.objects.filter(pk=pk), request.user
        ).exists():
            return redirect('core:ticket_list')
        return redirect('core:ticket_detail', pk=pk)


class TicketEscalateView(LoginRequiredMixin, View):
    """Raise a ticket one level. The current owner keeps it until a higher-level
    tech claims it — so the client is never left without a person."""

    def post(self, request, pk):
        ticket = get_object_or_404(_scope_tickets_for(Ticket.objects.all(), request.user), pk=pk)
        if ticket.escalate():
            TicketReply.objects.create(
                ticket=ticket, reply_type='internal',
                content=f'[Escalated to Level {ticket.escalation_level}] by '
                        f'{request.user.get_full_name() or request.user.username}',
                created_by=request.user,
            )
            messages.success(request, f'{ticket.ticket_number} escalated to Level {ticket.escalation_level}.')
        else:
            messages.info(request, f'{ticket.ticket_number} is already at the highest level.')
        return redirect('core:ticket_detail', pk=pk)


class TicketAcknowledgeOverdueView(LoginRequiredMixin, View):
    """Acknowledge an overdue ticket with a required internal note."""

    def get(self, request, pk):
        ticket = _get_scoped_ticket_or_404(request, pk)
        return render(request, 'core/partials/overdue_ack_form.html', {'ticket': ticket})

    def post(self, request, pk):
        ticket = _get_scoped_ticket_or_404(request, pk)
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
        devices = []
        if client_id:
            contacts = Contact.objects.filter(client_id=client_id, is_active=True).order_by('last_name', 'first_name')
            devices = Device.objects.filter(client_id=client_id, is_active=True).order_by('name')
        opts = '<option value="">---------</option>'
        for c in contacts:
            opts += f'<option value="{c.pk}">{escape(c.first_name)} {escape(c.last_name)}</option>'
        # Out-of-band swap to narrow the device dropdown to this client's devices.
        dev_opts = '<option value="">---------</option>'
        for d in devices:
            dev_opts += f'<option value="{d.pk}">{escape(str(d))}</option>'
        select_cls = 'w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500'
        opts += f'<select name="device" id="id_device" hx-swap-oob="true" class="{select_cls}">{dev_opts}</select>'
        return HttpResponse(opts)


class TicketCloseView(LoginRequiredMixin, View):
    """Set a ticket to 'resolved' status. Used when WO is complete and tech has contacted client."""

    def post(self, request, pk):
        ticket = _get_scoped_ticket_or_404(request, pk)
        if ticket.status in TICKET_CLOSED_STATUSES:
            messages.info(request, 'Ticket is already closed.')
            return redirect('core:ticket_detail', pk=pk)
        ticket.apply_status_change('resolved')
        ticket.wo_complete = False
        ticket.save(update_fields=['status', 'closed_at', 'wo_complete', 'updated_at'])
        messages.success(request, f'{ticket.ticket_number} resolved.')
        return redirect('core:ticket_detail', pk=pk)


class TicketDeleteView(LoginRequiredMixin, View):
    """Hard-delete a ticket. Admin only. Blocked if a work order is linked."""

    def post(self, request, pk):
        if not request.user.is_staff:
            return HttpResponse('Forbidden', status=403)
        ticket = get_object_or_404(Ticket, pk=pk)
        if WorkOrder.objects.filter(ticket=ticket).exists():
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
        qs = Ticket.overdue_queryset(qs)
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

def _median(values):
    """Median of a list of numbers, or None if empty. Median (not mean) so one
    disaster ticket doesn't define the metric."""
    import statistics
    return round(statistics.median(values), 1) if values else None


def _sla_breakdown_by(tickets_with_sla, key_func, label_func):
    """Group an SLA-eligible ticket queryset (due_at set) by an arbitrary key,
    returning per-group judged/on-time/rate + median first-response hours.
    Shared by the by-tech and by-client breakdowns — same math, different grouping."""
    from django.utils import timezone
    now = timezone.now()
    groups = {}
    for t in tickets_with_sla:
        key = key_func(t)
        if key is None:
            continue
        g = groups.setdefault(key, {'label': label_func(t), 'judged': 0, 'on_time': 0, 'response_hours': []})
        is_judged = bool(t.first_responded_at) or (t.due_at and t.due_at < now)
        if is_judged:
            g['judged'] += 1
            if t.first_responded_at and t.first_responded_at <= t.due_at:
                g['on_time'] += 1
        if t.first_responded_at:
            g['response_hours'].append((t.first_responded_at - t.created_at).total_seconds() / 3600)
    rows = []
    for g in groups.values():
        rows.append({
            'label': g['label'],
            'judged': g['judged'],
            'on_time': g['on_time'],
            'sla_rate': round(100 * g['on_time'] / g['judged'], 1) if g['judged'] else None,
            'median_response_hours': _median(g['response_hours']),
        })
    rows.sort(key=lambda r: r['label'] or '')
    return rows


class ReportsView(LoginRequiredMixin, View):
    def get(self, request):
        if not (_is_admin(request.user) or request.user.has_perm_flag('can_view_reports')):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        from datetime import timedelta, date
        from django.db.models import Count, Avg, F, Q, Sum, ExpressionWrapper, DurationField, FloatField
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

        # 6. SLA compliance — a RESPONSE SLA (due_at = created_at + grace period): met when
        # the first staff customer-visible reply lands before the deadline (first_responded_at),
        # NOT by when the ticket was closed. A ticket is only "judged" once its outcome is
        # decided — it has been answered, or its deadline has already passed. A still-in-window,
        # unanswered ticket is set aside (its clock is still running; it hasn't failed yet).
        sla_now = timezone.now()
        tickets_with_sla = tickets_in_range.filter(due_at__isnull=False)
        total_sla = tickets_with_sla.count()
        responded_on_time = tickets_with_sla.filter(
            first_responded_at__isnull=False,
            first_responded_at__lte=F('due_at'),
        ).count()
        judged_sla = tickets_with_sla.filter(
            Q(first_responded_at__isnull=False) | Q(due_at__lt=sla_now)
        ).count()
        pending_sla = total_sla - judged_sla
        sla_rate = round(100 * responded_on_time / judged_sla, 1) if judged_sla else None

        # 6b. Median first-response time (magnitude, not just pass/fail — median
        # not mean so one disaster ticket doesn't define it).
        response_hours = [
            (t.first_responded_at - t.created_at).total_seconds() / 3600
            for t in tickets_in_range.filter(first_responded_at__isnull=False).only('first_responded_at', 'created_at')
        ]
        median_response_hours = _median(response_hours)

        # 6c. SLA % + median response time broken down by tech and by client
        # (help-topic breakdown deferred — help topic has no bearing on the SLA itself).
        sla_tickets_qs = list(
            tickets_with_sla.select_related('assigned_to', 'client')
            .only('due_at', 'first_responded_at', 'created_at', 'assigned_to__first_name',
                  'assigned_to__last_name', 'assigned_to__username', 'client__name')
        )
        sla_by_tech = _sla_breakdown_by(
            [t for t in sla_tickets_qs if t.assigned_to_id],
            key_func=lambda t: t.assigned_to_id,
            label_func=lambda t: t.assigned_to.get_full_name() or t.assigned_to.username,
        )
        sla_by_client = _sla_breakdown_by(
            sla_tickets_qs,
            key_func=lambda t: t.client_id,
            label_func=lambda t: t.client.name,
        )

        # 6d. Backlog health — a live, forward-looking snapshot (NOT date-range
        # filtered; "how much is on the plate right now", not historical).
        now = timezone.now()
        open_tickets_qs = Ticket.objects.exclude(status__in=Ticket.CLOSED_STATUSES).only('created_at')
        backlog_open_count = 0
        backlog_buckets = {'lt_1d': 0, '1_3d': 0, '3_7d': 0, '7d_plus': 0}
        for t in open_tickets_qs:
            backlog_open_count += 1
            age_days = (now - t.created_at).total_seconds() / 86400
            if age_days < 1:
                backlog_buckets['lt_1d'] += 1
            elif age_days < 3:
                backlog_buckets['1_3d'] += 1
            elif age_days < 7:
                backlog_buckets['3_7d'] += 1
            else:
                backlog_buckets['7d_plus'] += 1

        # 6e. Created vs. closed in the period — "are we keeping up?"
        created_in_period = tickets_in_range.count()
        closed_in_period = Ticket.objects.filter(
            status__in=['closed', 'resolved'], updated_at__range=(start_dt, end_dt)
        ).count()

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

        # WO-side paid total. "Invoiced" and "Outstanding" stay Work-Order-only
        # below — they're accrual concepts (an invoice sent but not yet paid)
        # that don't map onto a counter sale, which is either not-yet-done
        # (draft) or paid on the spot, with no "invoiced but waiting" state.
        wo_paid_total = Invoice.objects.filter(
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

        # 11. Counter sales (Register history — Sale is no longer a nav tab;
        # this is where that history lives now, per Mike). Recurring/Lane-C
        # sales are excluded — they're managed via the Monthly Clients worklist,
        # a different reporting concern.
        counter_sales_qs = Sale.objects.filter(
            status='completed', is_recurring=False, paid_at__range=(start_dt, end_dt),
        ).select_related('client').order_by('-paid_at')
        counter_sales_total = counter_sales_qs.aggregate(total=Sum('amount'))['total'] or 0
        counter_sales_count = counter_sales_qs.count()
        counter_sales_list = list(counter_sales_qs[:100])

        # "Collected" in Billing Summary is TRUE revenue in the door — WO
        # payments + counter sales combined. Mike caught that it read $0 for a
        # shop running mostly counter sales, which looked like "no revenue"
        # instead of "no WO revenue." Invoiced/Outstanding stay WO-only (see
        # above) — only Collected is an honestly combinable figure.
        paid_total = wo_paid_total + counter_sales_total

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
            'responded_on_time': responded_on_time,
            'judged_sla': judged_sla,
            'pending_sla': pending_sla,
            'median_response_hours': median_response_hours,
            'sla_by_tech': sla_by_tech,
            'sla_by_client': sla_by_client,
            'backlog_open_count': backlog_open_count,
            'backlog_buckets': backlog_buckets,
            'created_in_period': created_in_period,
            'closed_in_period': closed_in_period,
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
            # 11
            'counter_sales_total': counter_sales_total,
            'counter_sales_count': counter_sales_count,
            'counter_sales_list': counter_sales_list,
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
            from django.db.models import Q
            t = tickets_in_range.filter(due_at__isnull=False)
            total = t.count()
            on_time = t.filter(first_responded_at__isnull=False, first_responded_at__lte=F('due_at')).count()
            judged = t.filter(Q(first_responded_at__isnull=False) | Q(due_at__lt=timezone.now())).count()
            pending = total - judged
            writer.writerow(['Total tickets with SLA', total])
            writer.writerow(['Answered on time', on_time])
            writer.writerow(['Judged (answered or deadline passed)', judged])
            writer.writerow(['Still within SLA window', pending])
            writer.writerow(['Compliance rate', f"{round(100*on_time/judged,1) if judged else 'N/A'}%"])
            response_hours = [
                (r.first_responded_at - r.created_at).total_seconds() / 3600
                for r in t.filter(first_responded_at__isnull=False).only('first_responded_at', 'created_at')
            ]
            median_hours = _median(response_hours)
            writer.writerow(['Median first-response (hours)', median_hours if median_hours is not None else 'N/A'])

        elif report == 'sla_breakdown':
            writer.writerow(['Group', 'Name', 'Judged', 'On Time', 'SLA Rate', 'Median Response (hrs)'])
            sla_tickets_qs = list(
                tickets_in_range.filter(due_at__isnull=False)
                .select_related('assigned_to', 'client')
            )
            for row in _sla_breakdown_by(
                [t for t in sla_tickets_qs if t.assigned_to_id],
                key_func=lambda t: t.assigned_to_id,
                label_func=lambda t: t.assigned_to.get_full_name() or t.assigned_to.username,
            ):
                writer.writerow(['Tech', row['label'], row['judged'], row['on_time'],
                                  f"{row['sla_rate']}%" if row['sla_rate'] is not None else 'N/A',
                                  row['median_response_hours'] if row['median_response_hours'] is not None else 'N/A'])
            for row in _sla_breakdown_by(
                sla_tickets_qs,
                key_func=lambda t: t.client_id,
                label_func=lambda t: t.client.name,
            ):
                writer.writerow(['Client', row['label'], row['judged'], row['on_time'],
                                  f"{row['sla_rate']}%" if row['sla_rate'] is not None else 'N/A',
                                  row['median_response_hours'] if row['median_response_hours'] is not None else 'N/A'])

        elif report == 'backlog':
            writer.writerow(['Metric', 'Value'])
            now = timezone.now()
            open_tickets_qs = Ticket.objects.exclude(status__in=Ticket.CLOSED_STATUSES).only('created_at')
            buckets = {'lt_1d': 0, '1_3d': 0, '3_7d': 0, '7d_plus': 0}
            total_open = 0
            for t in open_tickets_qs:
                total_open += 1
                age_days = (now - t.created_at).total_seconds() / 86400
                if age_days < 1:
                    buckets['lt_1d'] += 1
                elif age_days < 3:
                    buckets['1_3d'] += 1
                elif age_days < 7:
                    buckets['3_7d'] += 1
                else:
                    buckets['7d_plus'] += 1
            writer.writerow(['Open tickets (now)', total_open])
            writer.writerow(['Under 1 day old', buckets['lt_1d']])
            writer.writerow(['1-3 days old', buckets['1_3d']])
            writer.writerow(['3-7 days old', buckets['3_7d']])
            writer.writerow(['7+ days old', buckets['7d_plus']])
            writer.writerow(['Created in period', tickets_in_range.count()])
            writer.writerow(['Closed in period', Ticket.objects.filter(status__in=['closed', 'resolved'], updated_at__range=(start_dt, end_dt)).count()])

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

        elif report == 'counter_sales':
            writer.writerow(['Sale #', 'Customer', 'Amount', 'Payment Method', 'Reference', 'Paid Date'])
            for sale in Sale.objects.filter(
                status='completed', is_recurring=False, paid_at__range=(start_dt, end_dt),
            ).select_related('client').order_by('-paid_at'):
                writer.writerow([
                    sale.sale_number,
                    sale.client.name if sale.client else 'Walk-in',
                    sale.amount or '',
                    sale.get_payment_method_display() if sale.payment_method else '',
                    sale.reference or '',
                    sale.paid_at.date() if sale.paid_at else '',
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
            'can_reset_mfa': _can_reset_mfa(request.user),
        })


class AdminMFAResetView(LoginRequiredMixin, View):
    """Clear all OTP devices for a user (lost device recovery).

    Gated on the can_reset_user_mfa flag (superusers always qualify); every
    reset is recorded via reset_user_mfa -> MFAResetLog.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _can_reset_mfa(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        from django.contrib import messages
        from .models import reset_user_mfa
        target = get_object_or_404(User, pk=pk)
        reset_user_mfa(target, actor=request.user, source='web')
        messages.success(
            request,
            f'MFA reset for {target.get_full_name() or target.username}. '
            'They will be prompted to re-enroll on next login.'
        )
        return redirect('core:user_list')


class UserDeleteView(LoginRequiredMixin, View):
    """Admin-only: permanently delete a user. Guards against removing yourself or
    the last superuser. Operational records (tickets/WOs/line items they touched)
    survive — those FKs are SET_NULL, so history is kept, just unattributed."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        if target.pk == request.user.pk:
            messages.error(request, 'You cannot delete your own account.')
            return redirect('core:user_list')
        if target.is_superuser and User.objects.filter(is_superuser=True).count() <= 1:
            messages.error(request, 'Cannot delete the only superuser account.')
            return redirect('core:user_list')
        label = target.get_full_name() or target.username
        target.delete()
        messages.success(request, f'User "{label}" permanently deleted.')
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
    ('can_view_org_credentials',   'View Org Credential Vault'),
    ('can_reset_user_mfa',         'Reset User MFA'),
    ('can_view_prospects',         'View Prospects'),
    ('can_view_estimates',         'View Estimates'),
    ('can_view_sales',             'View Sales'),
    ('can_process_payments',       'Process Payments'),
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

def _line_items_for(work_order):
    return (
        work_order.line_items
        .select_related('catalog_item', 'logged_by')
        .order_by('kind', 'logged_at')
    )


def _catalog_by_category():
    """Active catalog items grouped by category for the inline picker on WO /
    Estimate / Sale detail. Services lead (services-first), then products —
    '-item_type' puts 'service' before 'product' since s > p descending."""
    items = CatalogItem.objects.filter(is_active=True).order_by(
        '-item_type', 'category', 'sort_order', 'name')
    grouped = {}
    for item in items:
        grouped.setdefault(item.category, []).append(item)
    return grouped


def _log_catalog_item(host, item, user):
    """Create a LineItem on `host` from a catalog item — a labor line for a
    Service, a part line for a Product — prefilled with its name + default
    price, and linked back to the catalog item for the print-description
    fallback. Shared by all four host log views (WO/Estimate/Option/Sale)."""
    return host.line_items.create(
        kind=item.line_kind,
        description=item.name[:255],
        quantity=1,
        unit_price=item.default_price,
        catalog_item=item,
        logged_by=user,
    )


def _parse_price(val):
    """Parse a money string to a non-negative Decimal, or None if blank/invalid."""
    from decimal import Decimal, InvalidOperation
    val = (val or '').strip()
    if not val:
        return None
    try:
        d = Decimal(val)
    except InvalidOperation:
        return None
    return d if d >= 0 else None


def _parse_qty(val):
    """Parse a quantity to a non-negative Decimal, defaulting to 1."""
    d = _parse_price(val)
    return d if d is not None else None


def _render_line_items(request, host):
    """Render the logged-line-items partial for a WorkOrder, an Estimate, a
    Sale, or an EstimateOption (a named pricing option nested under an
    Estimate — see EstimateOption). All four hosts attach LineItem via the
    same GenericRelation; only the template (and its context var name)
    differs. An option-scoped edit/delete/log always re-renders the WHOLE
    Estimate line-items section (general items + every option), not just the
    one option, since they share a single #estimate-line-items-section swap
    target on the page."""
    from django.template.loader import render_to_string
    if isinstance(host, EstimateOption):
        host = host.estimate
    if isinstance(host, Client):
        # Client hosts recurring billing TEMPLATE lines (Lane C) — same editable
        # line UI as everywhere else, its own swap target.
        return render(request, 'core/partials/client_recurring_lines.html', {
            'client': host,
            'entries': _line_items_for(host),
        })
    if isinstance(host, Estimate):
        return render(request, 'core/partials/estimate_line_items.html', {
            'estimate': host,
            'entries': _line_items_for(host),
            'options': host.options.all(),
        })
    if isinstance(host, Sale):
        # The Checkout card lives outside #sale-line-items-section (the swap
        # target), so its "has priced lines" gate and amount prefill go stale
        # after an add/edit/delete unless explicitly refreshed. Append it as
        # an out-of-band swap alongside the in-band line-items fragment.
        items_html = render_to_string(request=request, template_name='core/partials/sale_line_items.html', context={
            'sale': host,
            'entries': _line_items_for(host),
        })
        checkout_html = render_to_string(request=request, template_name='core/partials/sale_checkout_card.html', context={
            'sale': host,
            'oob': True,
            **_sale_checkout_context(host),
        })
        return HttpResponse(items_html + checkout_html)
    return render(request, 'core/partials/work_performed.html', {
        'work_order': host,
        'entries': _line_items_for(host),
    })


class WorkPerformedLogView(LoginRequiredMixin, View):
    """HTMX: log a catalog item (Product or Service) against a WorkOrder.
    The item's optional default_price prefills the line price."""

    def post(self, request, wo_pk, item_pk):
        work_order = _get_scoped_wo_or_404(request, wo_pk)
        item = get_object_or_404(CatalogItem, pk=item_pk, is_active=True)
        _log_catalog_item(work_order, item, request.user)
        return _render_line_items(request, work_order)


class WorkPerformedDeleteView(LoginRequiredMixin, View):
    """HTMX: remove a logged line item."""

    def post(self, request, pk):
        entry = get_object_or_404(LineItem, pk=pk)
        host = entry.content_object
        entry.delete()
        return _render_line_items(request, host)


class WorkPerformedUpdateView(LoginRequiredMixin, View):
    """HTMX: update label, notes, quantity and price on a logged line item."""

    def post(self, request, pk):
        entry = get_object_or_404(LineItem, pk=pk)
        label = request.POST.get('custom_label', '').strip()
        if label:
            entry.description = label[:255]
        entry.notes = request.POST.get('notes', '').strip()
        qty = _parse_qty(request.POST.get('quantity'))
        entry.quantity = qty if qty is not None else 1
        entry.unit_price = _parse_price(request.POST.get('unit_price'))
        entry.save(update_fields=['description', 'notes', 'quantity', 'unit_price'])
        return _render_line_items(request, entry.content_object)


class WorkPerformedCustomLogView(LoginRequiredMixin, View):
    """HTMX: log a fully custom line item — labor or part — with optional price."""

    def post(self, request, wo_pk):
        work_order = _get_scoped_wo_or_404(request, wo_pk)
        label = request.POST.get('custom_label', '').strip()
        notes = request.POST.get('notes', '').strip()
        kind = request.POST.get('kind', 'labor')
        if kind not in ('labor', 'part'):
            kind = 'labor'
        qty = _parse_qty(request.POST.get('quantity'))
        if label:
            work_order.line_items.create(
                kind=kind,
                description=label[:255],
                quantity=qty if qty is not None else 1,
                unit_price=_parse_price(request.POST.get('unit_price')),
                notes=notes,
                logged_by=request.user,
            )
        return _render_line_items(request, work_order)


# ---------------------------------------------------------------------------
# Repair Report (print view)
# ---------------------------------------------------------------------------

def _repair_report_context(work_order, site, report_type='repair'):
    """Build the repair-report render context once, shared by the print page and
    the emailed PDF so the two can never drift apart on content."""
    from django.utils import timezone

    # Customer-visible notes only
    notes = work_order.notes.filter(note_type='customer_visible').order_by('created_at')

    # Line items grouped by category for the report. Labor lines group under
    # their source button's category; parts under "Parts"; custom labor under "Other".
    line_items = (
        work_order.line_items
        .select_related('catalog_item')
        .order_by('kind', 'logged_at')
    )
    categories = {}
    for entry in line_items:
        if entry.kind == 'part':
            cat = 'Parts'
        elif entry.catalog_item:
            cat = entry.catalog_item.category
        else:
            cat = 'Other'
        categories.setdefault(cat, []).append(entry)

    repair_types = [work_order.repair_type] if work_order.repair_type else []

    # Named contact: use WO contact FK, fall back to client's primary contact
    contact = work_order.contact
    if not contact and work_order.client_id:
        contact = work_order.client.contacts.filter(is_primary=True).first()

    return {
        'work_order': work_order,
        'site': site,
        'notes': notes,
        'wp_categories': categories,
        'repair_types': repair_types,
        'report_type': report_type,
        'contact': contact,
        'print_date': timezone.now(),
    }


def _quote_report_context(estimate, site):
    """Build the quote-print context once, shared by the print page and the
    emailed PDF. Resolves a unified bill-to block for either anchor — a
    Prospect has no Contact rows, only inline email/phone fields."""
    from django.utils import timezone

    line_items = (
        estimate.line_items
        .select_related('catalog_item')
        .order_by('kind', 'logged_at')
    )
    categories = {}
    for entry in line_items:
        if entry.kind == 'part':
            cat = 'Parts'
        elif entry.catalog_item:
            cat = entry.catalog_item.category
        else:
            cat = 'Other'
        categories.setdefault(cat, []).append(entry)

    if estimate.client_id:
        client = estimate.client
        contact = estimate.contact or client.contacts.filter(is_primary=True).first()
        bill_to = {
            'name': client.name,
            'address_line1': client.address_line1,
            'address_line2': client.address_line2,
            'address_city': client.address_city,
            'address_state': client.address_state,
            'address_zip': client.address_zip,
            'contact_name': f'{contact.first_name} {contact.last_name}'.strip() if contact else '',
            'email': (contact.email if contact else '') or client.email,
            'phone': (contact.phone if contact else '') or client.phone,
        }
    else:
        prospect = estimate.prospect
        bill_to = {
            'name': prospect.display_name,
            'address_line1': '', 'address_line2': '', 'address_city': '', 'address_state': '', 'address_zip': '',
            'contact_name': prospect.contact_name,
            'email': prospect.email,
            'phone': prospect.phone,
        }

    option_blocks = []
    for option in estimate.options.select_related().order_by('sort_order', 'created_at'):
        opt_items = option.line_items.select_related('catalog_item').order_by('kind', 'logged_at')
        opt_categories = {}
        for entry in opt_items:
            if entry.kind == 'part':
                cat = 'Parts'
            elif entry.catalog_item:
                cat = entry.catalog_item.category
            else:
                cat = 'Other'
            opt_categories.setdefault(cat, []).append(entry)
        option_blocks.append({
            'option': option,
            'categories': opt_categories,
            'total': option.total,
        })

    return {
        'estimate': estimate,
        'site': site,
        'wp_categories': categories,
        'option_blocks': option_blocks,
        'bill_to': bill_to,
        'print_date': timezone.now(),
    }


def _receipt_context(sale, site):
    """Build the receipt-print context once, shared by the print page and the
    emailed PDF. Sold-to is the client (with its primary/sale contact) or a
    bare 'Walk-in' label for an anonymous counter sale."""
    line_items = (
        sale.line_items
        .select_related('catalog_item')
        .order_by('kind', 'logged_at')
    )
    categories = {}
    for entry in line_items:
        if entry.kind == 'part':
            cat = 'Parts'
        elif entry.catalog_item:
            cat = entry.catalog_item.category
        else:
            cat = 'Other'
        categories.setdefault(cat, []).append(entry)

    if sale.client_id:
        client = sale.client
        contact = client.contacts.filter(is_primary=True).first()
        bill_to = {
            'name': client.name,
            'address_line1': client.address_line1,
            'address_line2': client.address_line2,
            'address_city': client.address_city,
            'address_state': client.address_state,
            'address_zip': client.address_zip,
            'contact_name': f'{contact.first_name} {contact.last_name}'.strip() if contact else '',
            'email': (contact.email if contact else '') or client.email,
            'phone': (contact.phone if contact else '') or client.phone,
        }
    else:
        bill_to = {
            'name': 'Walk-in', 'address_line1': '', 'address_line2': '',
            'address_city': '', 'address_state': '', 'address_zip': '',
            'contact_name': '', 'email': '', 'phone': '',
        }

    return {
        'sale': sale,
        'site': site,
        'wp_categories': categories,
        'bill_to': bill_to,
    }


class SaleReceiptPrintView(SaleAccessMixin, View):
    """Browser preview of the receipt (new tab) — same template the emailed PDF uses.
    Only meaningful once the sale has been paid."""

    def get(self, request, pk):
        sale = get_object_or_404(Sale.objects.select_related('client'), pk=pk)
        if sale.status != 'completed':
            messages.error(request, 'This sale has not been completed yet — no receipt to print.')
            return redirect('core:sale_detail', pk=pk)
        site = SiteSettings.get()
        ctx = _receipt_context(sale, site)
        return render(request, 'core/sale_receipt_print.html', ctx)


class SaleReceiptEmailView(SaleAccessMixin, View):
    """Email the receipt to the customer as a PDF attachment.

    GET shows a small recipient form (client contacts dropdown, or a custom
    address — the only path for an anonymous walk-in sale); POST renders the
    receipt to PDF and sends it. Only available once the sale is completed."""

    def get(self, request, pk):
        sale = get_object_or_404(Sale.objects.select_related('client'), pk=pk)
        if sale.status != 'completed':
            messages.error(request, 'This sale has not been completed yet — no receipt to send.')
            return redirect('core:sale_detail', pk=pk)
        contacts = sale.client.contacts.filter(is_active=True) if sale.client_id else None
        default_contact = None
        default_email = ''
        if sale.client_id:
            default_contact = sale.client.contacts.filter(
                is_primary=True, is_active=True, email__gt='',
            ).first()
            default_email = (default_contact.email if default_contact else '') or sale.client.email
        return render(request, 'core/sale_email_receipt.html', {
            'sale': sale,
            'contacts': contacts,
            'default_contact': default_contact,
            'default_email': default_email,
        })

    def post(self, request, pk):
        sale = get_object_or_404(Sale.objects.select_related('client'), pk=pk)
        if sale.status != 'completed':
            messages.error(request, 'This sale has not been completed yet — no receipt to send.')
            return redirect('core:sale_detail', pk=pk)

        from .pdf_utils import render_pdf
        from .email_utils import send_document_email
        from django.template.loader import render_to_string

        contact = None
        to_email = (request.POST.get('custom_email') or '').strip()
        contact_id = request.POST.get('contact')
        if not to_email and contact_id and sale.client_id:
            contact = sale.client.contacts.filter(pk=contact_id).first()
            to_email = contact.email if contact else ''
        if not to_email:
            messages.error(request, 'Choose a contact or enter an email address.')
            return redirect('core:sale_receipt_email', pk=pk)

        site = SiteSettings.get()
        ctx = _receipt_context(sale, site)
        html = render_to_string('core/sale_receipt_print.html', ctx)
        try:
            pdf_bytes = render_pdf(html)
        except Exception:
            logger.exception('PDF render failed for receipt %s.', sale.sale_number)
            messages.error(request, 'Could not generate the receipt PDF. The PDF engine may not be installed on this server.')
            return redirect('core:sale_detail', pk=pk)

        company = site.company_name or "Murphy's Bench"
        sales_from = site.email_sales_from or site.email_from or None
        cover = (
            f"Hello,\n\nThank you for your business. Please find attached your receipt "
            f"{sale.sale_number} for ${sale.amount or 0}.\n\n{company}"
        )
        log = send_document_email(
            to_email,
            subject=f"{company}: Receipt {sale.sale_number}",
            cover_body=cover,
            from_email=sales_from,
            reply_to=sales_from,
            attachments=[(f'Receipt-{sale.sale_number}.pdf', pdf_bytes, 'application/pdf')],
            client=sale.client,
            contact=contact,
            trigger='sale_receipt',
        )
        if log.status == 'sent':
            messages.success(request, f'Receipt emailed to {to_email}.')
        else:
            messages.error(request, f'Receipt not sent ({log.get_reason_display() or log.status}).')
        return redirect('core:sale_detail', pk=pk)


# ---------------------------------------------------------------------------
# Light POS — the unified register for closed Work Orders and counter Sales.
# Settlement is POS-only (Mike's call): the WO's old "Send to Invoice Ninja"
# button and the Sale detail page's inline checkout card are retired in favor
# of this single register. A tech closes a WO and never sends it anywhere;
# the cashier finds it here by WO# or customer name and settles it. The Sale
# side reuses the existing, unchanged, tested checkout code (sale_checkout /
# sale_send_draft) — only its entry point moves from Sale detail to the POS.
# See memory project_mb_pos_light_register for the full design.
# ---------------------------------------------------------------------------

class POSAccessMixin(LoginRequiredMixin):
    """Gate POS (register) views on the can_view_sales role flag — the POS
    supersedes the old Sale-detail checkout, so it inherits the same gate."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _can_view_sales(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


# A work order is eligible for the register once the tech has finished it. MB's
# real terminal state is 'completed' (mark_completed(); the status dropdown's
# common choice); 'closed' is a rarely-used dropdown option that also means
# "done". Both make a WO ready to ring up.
POS_SETTLE_STATUSES = ('completed', 'closed')


class POSHomeView(POSAccessMixin, View):
    """The register's landing screen: search finished (completed/closed) work
    orders by number or customer name, or start a new counter Sale."""

    def get(self, request):
        q = (request.GET.get('q') or '').strip()
        results = []
        if q:
            results = list(
                WorkOrder.objects.filter(status__in=POS_SETTLE_STATUSES)
                .filter(Q(work_order_number__icontains=q) | Q(client__name__icontains=q))
                .select_related('client', 'invoice')
                .order_by('-completed_date', '-created_at')[:25]
            )
        return render(request, 'core/pos_home.html', {'query': q, 'results': results})


class POSSaleStartView(POSAccessMixin, View):
    """Start a new counter Sale from the register and land directly on its POS
    settle screen (mirrors SaleCreateView; only the redirect target differs —
    settlement now happens at the POS, not the Sale detail page)."""

    def post(self, request):
        sale = Sale.objects.create(created_by=request.user)
        return redirect('core:pos_sale_settle', pk=sale.pk)


class POSSaleSettleView(POSAccessMixin, DetailView):
    """The register screen for a counter Sale — line items (reusing the exact
    catalog/custom-entry UI from Sale detail) plus the existing, unchanged
    checkout card (sale_checkout_card.html posts to the same sale_checkout /
    sale_send_draft endpoints it always has). Only the entry point moved."""
    model = Sale
    template_name = 'core/pos_sale_settle.html'
    context_object_name = 'sale'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['catalog_by_category'] = _catalog_by_category()
        ctx['entries'] = _line_items_for(self.object)
        ctx['sale_form'] = SaleForm(instance=self.object)
        ctx.update(_sale_checkout_context(self.object))
        return ctx


class POSWorkOrderSettleView(POSAccessMixin, View):
    """The register screen for a closed Work Order. GET shows the amount,
    current Invoice Ninja state, and payment options; POST performs the
    settlement. This is the capability the WO never had before Slice 1 — the
    old WorkOrderSendToINView only ever created an unpaid draft and left the
    rest to a trip into Invoice Ninja.

    State-aware by design (the plan's 'one job = one invoice' rule): if the WO
    already has an invoice_ninja_id (e.g. a draft sent earlier), settlement
    reuses that invoice rather than pushing a second one. The server always
    computes the amount from the WO's own priced lines — never trusts a
    posted amount."""

    def get(self, request, pk):
        wo = get_object_or_404(WorkOrder.objects.select_related('client', 'invoice'), pk=pk)
        if wo.status not in POS_SETTLE_STATUSES:
            messages.error(request, f'{wo.work_order_number} must be completed before it can be settled at the register.')
            return redirect('core:pos_home')
        return render(request, 'core/pos_wo_settle.html', {
            'wo': wo,
            'invoice': wo.invoice,
            'amount': wo.line_items_total,
            'payment_methods': Invoice.PAYMENT_METHOD_CHOICES,
            'invoice_ninja_enabled': SiteSettings.get().invoice_ninja_enabled,
        })

    def post(self, request, pk):
        from . import invoice_ninja
        wo = get_object_or_404(WorkOrder.objects.select_related('client', 'invoice'), pk=pk)
        if wo.status not in POS_SETTLE_STATUSES:
            messages.error(request, f'{wo.work_order_number} must be completed before it can be settled at the register.')
            return redirect('core:pos_home')
        if wo.invoice.billing_status == 'paid':
            messages.info(request, f'{wo.work_order_number} is already marked Paid.')
            return redirect('core:pos_wo_settle', pk=pk)
        if not SiteSettings.get().invoice_ninja_enabled:
            messages.error(request, 'Invoice Ninja is not enabled in Settings.')
            return redirect('core:pos_wo_settle', pk=pk)

        amount = wo.line_items_total  # server-computed — never trust the request
        if amount <= 0:
            messages.error(request, 'This work order has no priced line items — nothing to settle.')
            return redirect('core:pos_wo_settle', pk=pk)

        action = request.POST.get('action')  # 'draft' or 'pay'
        method = (request.POST.get('payment_method') or '').strip()
        reference = (request.POST.get('reference') or '').strip()
        valid_methods = {c[0] for c in Invoice.PAYMENT_METHOD_CHOICES}

        if action == 'pay' and method not in valid_methods:
            messages.error(request, 'Choose a valid payment method.')
            return redirect('core:pos_wo_settle', pk=pk)
        if action not in ('draft', 'pay'):
            messages.error(request, 'Choose how to settle this work order.')
            return redirect('core:pos_wo_settle', pk=pk)

        try:
            # State-aware: create the IN invoice only if this WO has never been
            # pushed. A just-created invoice can't be paid yet; an already-pushed
            # one might already be paid in IN (see the read-back below).
            in_client_id = None
            newly_pushed = False
            if not wo.invoice_ninja_id:
                in_id, in_ref, in_client_id = invoice_ninja.push_host_invoice(
                    wo, po_number=wo.work_order_number,
                    client=wo.client, is_walkin=not wo.client_id,
                )
                wo.invoice_ninja_id = in_id
                wo.invoice_ninja_ref = in_ref
                wo.save(update_fields=['invoice_ninja_id', 'invoice_ninja_ref'])
                newly_pushed = True

            if action == 'draft':
                if newly_pushed:
                    messages.success(
                        request,
                        f'{wo.work_order_number} sent to Invoice Ninja as a draft — '
                        f'invoice #{wo.invoice_ninja_ref or wo.invoice_ninja_id}.'
                    )
                else:
                    messages.info(
                        request,
                        f'{wo.work_order_number} is already in Invoice Ninja as '
                        f'invoice #{wo.invoice_ninja_ref or wo.invoice_ninja_id}.'
                    )
                return redirect('core:pos_wo_settle', pk=pk)

            # Pay path. If the invoice PRE-EXISTED, it may already have been paid
            # directly in IN (e.g. a WO settled in IN before the POS) — MB's
            # stored billing_status wouldn't know. Read IN's current status and
            # refuse a duplicate payment, self-healing MB's record. (Mirrors the
            # Slice 5d fresh-read-back guard; an unreachable IN aborts, not posts.)
            if not newly_pushed:
                current = invoice_ninja.check_invoice_status(wo)
                if current.strip().lower() == 'paid':
                    inv = wo.invoice
                    inv.billing_status = 'paid'
                    inv.in_status = 'Paid'
                    inv.save(update_fields=['billing_status', 'in_status'])
                    messages.info(
                        request,
                        f'{wo.work_order_number} is already Paid in Invoice Ninja — '
                        'recorded here, no second payment posted.'
                    )
                    return redirect('core:pos_wo_settle', pk=pk)
                in_client_id = (
                    invoice_ninja.find_or_create_client(wo.client) if wo.client_id
                    else invoice_ninja.find_or_create_walkin_client()
                )

            method_label = dict(Invoice.PAYMENT_METHOD_CHOICES).get(method, method)
            invoice_ninja.post_payment(
                wo.invoice_ninja_id, in_client_id,
                amount=amount, method_label=method_label, reference=reference,
            )
            invoice = wo.invoice
            invoice.billing_status = 'paid'
            invoice.in_status = 'Paid'
            invoice.amount = amount
            invoice.payment_method = method
            invoice.reference = reference
            invoice.paid_date = timezone.localdate()
            invoice.paid_at = timezone.now()
            invoice.invoice_ninja_id = wo.invoice_ninja_id
            invoice.save()
            messages.success(
                request,
                f'{wo.work_order_number} settled — ${amount} paid, '
                f'invoice #{wo.invoice_ninja_ref or wo.invoice_ninja_id}.'
            )
            return redirect('core:pos_wo_receipt', pk=pk)
        except invoice_ninja.InvoiceNinjaError as e:
            messages.error(request, f'Could not settle at Invoice Ninja: {e}')
            return redirect('core:pos_wo_settle', pk=pk)


def _wo_receipt_context(work_order, site):
    """WO-side mirror of _receipt_context(sale, site) — the POS-generated
    receipt for a settled Work Order. Kept as its own function (rather than
    generalizing _receipt_context) so Sale's existing, tested receipt path is
    left untouched."""
    entries = (
        work_order.line_items
        .select_related('catalog_item')
        .order_by('kind', 'logged_at')
    )
    categories = {}
    for entry in entries:
        if entry.kind == 'part':
            cat = 'Parts'
        elif entry.catalog_item:
            cat = entry.catalog_item.category
        else:
            cat = 'Other'
        categories.setdefault(cat, []).append(entry)

    if work_order.client_id:
        client = work_order.client
        contact = client.contacts.filter(is_primary=True).first()
        bill_to = {
            'name': client.name,
            'address_line1': client.address_line1,
            'address_line2': client.address_line2,
            'address_city': client.address_city,
            'address_state': client.address_state,
            'address_zip': client.address_zip,
            'contact_name': f'{contact.first_name} {contact.last_name}'.strip() if contact else '',
            'email': (contact.email if contact else '') or client.email,
            'phone': (contact.phone if contact else '') or client.phone,
        }
    else:
        bill_to = {
            'name': 'Walk-in', 'address_line1': '', 'address_line2': '',
            'address_city': '', 'address_state': '', 'address_zip': '',
            'contact_name': '', 'email': '', 'phone': '',
        }

    return {
        'work_order': work_order,
        'invoice': work_order.invoice,
        'site': site,
        'wp_categories': categories,
        'bill_to': bill_to,
    }


class POSWorkOrderChargeView(POSAccessMixin, View):
    """Slice 6 — trigger Invoice Ninja to charge the client's card on file for a
    completed Work Order, from the Register, as a first-class settlement action
    alongside Mark Paid / Bill Later. Mirrors SaleChargeView's charge discipline
    (confirm-then-charge, cooldown, audit row) but adds a push-then-charge path:
    if the WO hasn't been pushed to IN yet, Confirm creates the draft invoice
    first, then charges it — one click from the settle screen, no separate
    'Bill Later then find the button' detour.

    Charging is deliberately Register-only (the WO detail page has no charge
    action) and client-only (a walk-in has no card on file). Stacks
    _can_process_payments on top of POSAccessMixin's can_view_sales gate."""

    def _amount(self, wo):
        # Server-side only, from the WO's current priced line items — never
        # trust an amount from the request. Mirrors SaleChargeView._amount.
        from decimal import Decimal
        return wo.line_items_total.quantize(Decimal('0.01'))

    def _guard(self, request, wo):
        """Shared GET/POST preconditions. Returns a redirect response to bail,
        or None to proceed. Order matters — cheapest/most-fundamental first."""
        if wo.status not in POS_SETTLE_STATUSES:
            messages.error(request, f'{wo.work_order_number} must be completed before it can be settled at the register.')
            return redirect('core:pos_home')
        if not wo.client_id:
            messages.error(request, 'A walk-in work order has no card on file to charge.')
            return redirect('core:pos_wo_settle', pk=wo.pk)
        if (wo.invoice.in_status or '').strip().lower() == 'paid':
            messages.info(request, f'{wo.work_order_number} is already marked Paid.')
            return redirect('core:pos_wo_settle', pk=wo.pk)
        if self._amount(wo) <= 0:
            messages.error(request, 'This work order has no priced line items — nothing to charge.')
            return redirect('core:pos_wo_settle', pk=wo.pk)
        return None

    def get(self, request, pk):
        if not _can_process_payments(request.user):
            return HttpResponse('Forbidden', status=403)
        wo = get_object_or_404(WorkOrder.objects.select_related('client', 'invoice'), pk=pk)
        bail = self._guard(request, wo)
        if bail:
            return bail
        return render(request, 'core/pos_wo_charge_confirm.html', {
            'wo': wo,
            'amount': self._amount(wo),
        })

    # Same double-charge cooldown window as SaleChargeView — see that class's
    # comment for the rationale (async charge, back-button resubmits, etc.).
    _CHARGE_COOLDOWN = timedelta(minutes=5)

    def post(self, request, pk):
        from . import invoice_ninja
        if not _can_process_payments(request.user):
            return HttpResponse('Forbidden', status=403)
        wo = get_object_or_404(WorkOrder.objects.select_related('client', 'invoice'), pk=pk)
        if not SiteSettings.get().invoice_ninja_enabled:
            messages.error(request, 'Invoice Ninja is not enabled in Settings.')
            return redirect('core:pos_wo_settle', pk=pk)
        bail = self._guard(request, wo)
        if bail:
            return bail

        recent = PaymentChargeAttempt.objects.filter(
            work_order=wo, result='success',
            initiated_at__gte=timezone.now() - self._CHARGE_COOLDOWN,
        ).exists()
        if recent:
            messages.warning(
                request,
                f'{wo.work_order_number} was charged moments ago. Use "Check IN" to confirm '
                'it posted before charging again.'
            )
            return redirect('core:pos_wo_settle', pk=pk)

        amount = self._amount(wo)

        # Push a draft first if this WO has never been sent to IN — a card-on-file
        # charge needs an invoice to charge against. Kept in its own try so a PUSH
        # failure is reported plainly and does NOT get logged as a charge attempt
        # (the audit table is for charge attempts only). If the push succeeds but
        # the charge then fails, the WO stays pushed (a draft in IN) — same
        # keep-the-push behavior as the settle 'pay' path.
        if not wo.invoice_ninja_id:
            try:
                in_id, in_ref, _ = invoice_ninja.push_host_invoice(
                    wo, po_number=wo.work_order_number, client=wo.client, is_walkin=False,
                )
                wo.invoice_ninja_id = in_id
                wo.invoice_ninja_ref = in_ref
                wo.save(update_fields=['invoice_ninja_id', 'invoice_ninja_ref'])
            except invoice_ninja.InvoiceNinjaError as e:
                messages.error(request, f'Could not send {wo.work_order_number} to Invoice Ninja: {e}')
                return redirect('core:pos_wo_settle', pk=pk)

        try:
            label = invoice_ninja.charge_on_file(wo)
            PaymentChargeAttempt.objects.create(
                work_order=wo, invoice_ninja_id=wo.invoice_ninja_id, amount=amount,
                initiated_by=request.user, result='success', in_status_after=label,
            )
            messages.success(
                request,
                f'{wo.work_order_number}: charge initiated in Invoice Ninja (${amount}). '
                f'Invoice Ninja currently reports "{label}" — the charge runs '
                'asynchronously, so use "Check IN" in a moment to confirm it posted.'
            )
        except invoice_ninja.InvoiceNinjaError as e:
            PaymentChargeAttempt.objects.create(
                work_order=wo, invoice_ninja_id=wo.invoice_ninja_id, amount=amount,
                initiated_by=request.user, result='failed', error_message=str(e),
            )
            messages.error(request, f'Charging the card on file failed: {e}')
        return redirect('core:pos_wo_settle', pk=pk)


class POSWorkOrderReceiptPrintView(POSAccessMixin, View):
    """Print/PDF-preview the MB-generated receipt for a settled Work Order —
    replaces Invoice Ninja as the customer-facing receipt for POS-settled
    work, and (unlike IN's) prints the transaction reference."""

    def get(self, request, pk):
        wo = get_object_or_404(WorkOrder.objects.select_related('client', 'invoice'), pk=pk)
        if wo.invoice.billing_status != 'paid':
            messages.error(request, 'This work order has not been paid yet — no receipt to print.')
            return redirect('core:pos_wo_settle', pk=pk)
        site = SiteSettings.get()
        ctx = _wo_receipt_context(wo, site)
        return render(request, 'core/pos_wo_receipt_print.html', ctx)


def _report_recipient_contact(work_order):
    """The contact a report email is addressed to: the WO's contact (with an
    email), else the client's primary/any active emailable contact."""
    contact = work_order.contact
    if contact and contact.email:
        return contact
    if not work_order.client_id:
        return contact
    return (work_order.client.contacts.filter(is_primary=True, is_active=True, email__gt='').first()
            or work_order.client.contacts.filter(is_active=True, email__gt='').first()
            or contact)


class WorkOrderPrintView(LoginRequiredMixin, View):
    """Print-optimised repair report for handing to the customer."""

    def get(self, request, pk):
        work_order = _get_scoped_wo_or_404(
            request, pk,
            queryset=WorkOrder.objects.select_related(
                'client', 'device', 'repair_type', 'assigned_to', 'contact'
            ),
        )
        site = SiteSettings.get()
        report_type = request.GET.get('type', 'repair')  # 'repair' or 'claim'
        ctx = _repair_report_context(work_order, site, report_type)
        return render(request, 'core/work_order_print.html', ctx)


class WorkOrderReportEmailView(LoginRequiredMixin, View):
    """Email the repair report to the customer as a PDF attachment.

    GET shows a small recipient form (pick a contact on the client, or type a
    custom address); POST renders the report to PDF and sends it via
    send_document_email. Scoped like the rest of the WO views.
    """

    def _get_wo(self, request, pk):
        wo = get_object_or_404(
            WorkOrder.objects.select_related('client', 'device', 'repair_type', 'assigned_to', 'contact'),
            pk=pk,
        )
        # Same visibility scoping as other WO actions.
        if not _scope_assignable_for(WorkOrder.objects.all(), request.user).filter(pk=wo.pk).exists():
            return None
        return wo

    def get(self, request, pk):
        wo = self._get_wo(request, pk)
        if wo is None:
            raise Http404
        default = _report_recipient_contact(wo)
        contacts = wo.client.contacts.filter(is_active=True) if wo.client_id else Contact.objects.none()
        default_email = (default.email if default else '') or (wo.client.email if wo.client_id else '')
        return render(request, 'core/work_order_email_report.html', {
            'work_order': wo,
            'contacts': contacts,
            'default_contact': default,
            'default_email': default_email,
        })

    def post(self, request, pk):
        wo = self._get_wo(request, pk)
        if wo is None:
            raise Http404

        from .pdf_utils import render_pdf
        from .email_utils import send_document_email
        from django.template.loader import render_to_string

        # Resolve recipient: a chosen contact, or a custom address.
        contact = None
        to_email = (request.POST.get('custom_email') or '').strip()
        contact_id = request.POST.get('contact')
        if not to_email and contact_id and wo.client_id:
            contact = wo.client.contacts.filter(pk=contact_id).first()
            to_email = contact.email if contact else ''
        if not to_email:
            messages.error(request, 'Choose a contact or enter an email address.')
            return redirect('core:work_order_email_report', pk=pk)

        site = SiteSettings.get()
        ctx = _repair_report_context(wo, site, report_type='repair')
        # The print template already carries an @media print stylesheet that
        # WeasyPrint honors (hides the on-screen Print/Close controls, shows the
        # footer) — render it straight to PDF rather than maintaining a second copy.
        html = render_to_string('core/work_order_print.html', ctx)
        try:
            pdf_bytes = render_pdf(html)
        except Exception:
            logger.exception('PDF render failed for repair report %s.', wo.work_order_number)
            messages.error(request, 'Could not generate the report PDF. The PDF engine may not be installed on this server.')
            return redirect('core:work_order_detail', pk=pk)

        company = site.company_name or "Murphy's Bench"
        cover = (
            f"Hello,\n\nPlease find attached the repair report for "
            f"{wo.work_order_number}{(' — ' + wo.device.name) if wo.device else ''}.\n\n"
            f"Thank you for your business.\n{company}"
        )
        log = send_document_email(
            to_email,
            subject=f"{company}: Repair Report {wo.work_order_number}",
            cover_body=cover,
            attachments=[(f'Repair-Report-{wo.work_order_number}.pdf', pdf_bytes, 'application/pdf')],
            client=wo.client,
            contact=contact,
            trigger='wo_report',
            related_ticket=wo.ticket,
        )
        if log.status == 'sent':
            messages.success(request, f'Repair report emailed to {to_email}.')
        else:
            messages.error(request, f'Report not sent ({log.get_reason_display() or log.status}).')
        return redirect('core:work_order_detail', pk=pk)


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
        wo = _get_scoped_wo_or_404(request, pk)
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
        wo = _get_scoped_wo_or_404(request, pk)
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


class WorkOrderBillingCheckINView(LoginRequiredMixin, View):
    """HTMX: pull current invoice status from Invoice Ninja and record it."""

    def post(self, request, pk):
        from . import invoice_ninja
        wo = _get_scoped_wo_or_404(request, pk)
        invoice, _ = Invoice.objects.get_or_create(work_order=wo)
        try:
            invoice_ninja.check_invoice_status(wo)
            invoice.refresh_from_db()
        except invoice_ninja.InvoiceNinjaError as e:
            messages.error(request, str(e))
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
    ('invoice_ninja', 'Invoice Ninja', InvoiceNinjaSettingsForm),
    ('repair_types',     'Repair Types',     None),
    ('canned_responses', 'Canned Responses', None),
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
    ('updates',          'Updates',          None),
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


class InvoiceNinjaTestView(LoginRequiredMixin, View):
    """Settings 'Test Connection' — admin only. Hits IN's API and reports back."""

    def post(self, request):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        from . import invoice_ninja
        try:
            msg = invoice_ninja.test_connection()
            messages.success(request, msg)
        except invoice_ninja.InvoiceNinjaError as e:
            messages.error(request, f'Invoice Ninja test failed: {e}')
        return redirect('/settings/?tab=invoice_ninja')


def _update_status_context():
    """Shared context for the Settings → Updates section + status fragment."""
    from . import update_ops
    return {
        'update_current': update_ops.current_version(),
        'update_available': update_ops.available_version(),
        'update_is_available': update_ops.is_update_available(),
        'update_status': update_ops.read_status(),
    }


class UpdateStatusView(LoginRequiredMixin, View):
    """HTMX-polled fragment showing current/available version + last run status.
    Admin only. Polled every few seconds while a run is in progress."""

    def get(self, request):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        return render(request, 'core/partials/update_status.html', _update_status_context())


class UpdateCheckView(LoginRequiredMixin, View):
    """Fetch tags from origin so 'available version' is fresh, then re-render the
    status fragment. Admin only. Read-only git — no sudo, no code change."""

    def post(self, request):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        from . import update_ops
        if not update_ops.fetch_tags():
            messages.warning(request, 'Could not reach the update source to check for new versions.')
        return render(request, 'core/partials/update_status.html', _update_status_context())


class UpdateTriggerView(LoginRequiredMixin, View):
    """Queue an update to the latest release. Drops the trigger file the systemd
    .path unit watches (the actual update runs out-of-band — a web request can't
    restart its own gunicorn). Admin only; refuses if a run is already going."""

    def post(self, request):
        if not _is_admin(request.user):
            return HttpResponse('Forbidden', status=403)
        from . import update_ops
        if not update_ops.request_update():
            messages.warning(request, 'An update is already in progress.')
        return render(request, 'core/partials/update_status.html', _update_status_context())


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
            from .forms import SLADefaultsForm
            ctx['sla_defaults_form'] = SLADefaultsForm(instance=site)
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
        if active_tab == 'updates':
            ctx.update(_update_status_context())
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
                        'body_template': f'Hi {{{{ customer_name }}}},\n\nYour ticket {{{{ ticket.ticket_number }}}} has been updated.\n\nThank you,\n{{{{ tech_name }}}}',
                        'is_active': False,
                    }
                )
            ctx['email_templates'] = EmailTemplate.objects.select_related('signature').all()
            ctx['email_signatures'] = EmailSignature.objects.all()
            from .forms import EmailBrandingForm
            from .email_utils import _email_header_color
            ctx['email_branding_form'] = EmailBrandingForm(instance=site)
            ctx['email_header_color_effective'] = _email_header_color(site)
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
# Products & Services catalog (top-level section; management replaces the old
# Settings → Quick Labor tab). Viewing is open to any logged-in user (the
# picker already exposes these items); create/edit/delete is admin-gated.
# ---------------------------------------------------------------------------


class CatalogAdminMixin(LoginRequiredMixin):
    """Editing the catalog is admin-only (matches how it was gated under
    Settings); the list/browse view is intentionally NOT gated by this."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _is_admin(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class CatalogListView(LoginRequiredMixin, ListView):
    model = CatalogItem
    template_name = 'core/catalog_list.html'
    context_object_name = 'items'

    def get_queryset(self):
        from django.db.models.functions import Lower
        # Alphabetical by name within each category (case-insensitive), ignoring
        # sort_order — the catalog list is a browse/reference view, not a curated
        # ordering (sort_order carries legacy QuickLaborItem values with no UI to edit).
        qs = CatalogItem.objects.order_by(Lower('category'), Lower('name'))
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(category__icontains=search))
        if not self.request.GET.get('show_inactive'):
            qs = qs.filter(is_active=True)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Split into Services and Products, each grouped by category (a divider
        # per category inside the card). Insertion order follows the queryset
        # (category, name — both case-insensitive), so categories render
        # alphabetically and items within a category are alphabetical by name.
        services, products = {}, {}
        for item in ctx['items']:
            bucket = products if item.item_type == 'product' else services
            bucket.setdefault(item.category, []).append(item)
        ctx['services_by_category'] = services
        ctx['products_by_category'] = products
        ctx['services_count'] = sum(len(v) for v in services.values())
        ctx['products_count'] = sum(len(v) for v in products.values())
        ctx['show_inactive'] = bool(self.request.GET.get('show_inactive'))
        ctx['type_choices'] = CatalogItem.ITEM_TYPE_CHOICES
        ctx['can_edit'] = _is_admin(self.request.user)
        return ctx


class CatalogCreateView(CatalogAdminMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        category = request.POST.get('category', '').strip()
        item_type = request.POST.get('item_type', 'service')
        if item_type not in dict(CatalogItem.ITEM_TYPE_CHOICES):
            item_type = 'service'
        if name and category:
            CatalogItem.objects.create(
                name=name,
                item_type=item_type,
                category=category,
                print_description=request.POST.get('print_description', '').strip(),
                default_price=_parse_price(request.POST.get('default_price')),
            )
            messages.success(request, f'Added "{name}" to the catalog.')
        else:
            messages.error(request, 'Name and category are required.')
        return redirect('core:catalog_list')


class CatalogUpdateView(CatalogAdminMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(CatalogItem, pk=pk)
        name = request.POST.get('name', '').strip()
        category = request.POST.get('category', '').strip()
        item_type = request.POST.get('item_type', item.item_type)
        if item_type not in dict(CatalogItem.ITEM_TYPE_CHOICES):
            item_type = item.item_type
        if name and category:
            item.name = name
            item.item_type = item_type
            item.category = category
            item.print_description = request.POST.get('print_description', '').strip()
            item.is_active = request.POST.get('is_active') == '1'
            item.default_price = _parse_price(request.POST.get('default_price'))
            item.save(update_fields=['name', 'item_type', 'category',
                                     'print_description', 'is_active', 'default_price'])
            messages.success(request, f'Updated "{name}".')
        return redirect('core:catalog_list')


class CatalogDeleteView(CatalogAdminMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(CatalogItem, pk=pk)
        name = item.name
        item.delete()
        messages.success(request, f'Deleted "{name}" from the catalog.')
        return redirect('core:catalog_list')

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


def _can_view_org_creds(user):
    """Who may reveal the shared org credential vault. Mirrors the device-cred
    gate (_can_view_device_creds): admins always; other users only with the
    explicit can_view_org_credentials flag. Closes the prior hole where ANY
    logged-in user could reveal a non-admin_only vault entry by hitting the
    endpoint directly (the Settings UI is admin-only, but the endpoint wasn't)."""
    return _is_admin(user) or user.has_perm_flag('can_view_org_credentials')


class OrgCredentialRevealView(LoginRequiredMixin, View):
    """HTMX: return plaintext credential value and log the access."""
    def get(self, request, pk, field):
        from django.core.exceptions import PermissionDenied
        cred = get_object_or_404(OrgCredential, pk=pk)
        # Baseline: must be permitted to view the vault at all.
        if not _can_view_org_creds(request.user):
            raise PermissionDenied
        # Extra tier: an entry marked admin_only stays admin-only regardless.
        if cred.admin_only and not _is_admin(request.user):
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
        sig_id = request.POST.get('signature')
        tmpl.signature_id = int(sig_id) if sig_id else None
        tmpl.save()
        messages.success(request, f'Email template "{tmpl.get_trigger_display()}" saved.')
        return redirect(reverse_lazy('core:settings') + '?tab=email_templates')


# --- Email Signatures ---

def _sig_redirect():
    return reverse('core:settings') + '?tab=email_templates'


class EmailSignatureCreateView(LoginRequiredMixin, View):
    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        name = request.POST.get('name', '').strip()
        body = request.POST.get('body', '').strip()
        is_default = request.POST.get('is_default') == '1'
        if name and body:
            sig = EmailSignature(name=name, body=body, is_default=is_default)
            sig.save()
            messages.success(request, f'Signature "{name}" created.')
        return redirect(_sig_redirect())


class EmailSignatureUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        sig = get_object_or_404(EmailSignature, pk=pk)
        sig.name = request.POST.get('name', sig.name).strip()
        sig.body = request.POST.get('body', sig.body).strip()
        sig.is_default = request.POST.get('is_default') == '1'
        sig.save()
        messages.success(request, f'Signature "{sig.name}" saved.')
        return redirect(_sig_redirect())


class EmailSignatureDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        sig = get_object_or_404(EmailSignature, pk=pk)
        name = sig.name
        sig.delete()
        messages.success(request, f'Signature "{name}" deleted.')
        return redirect(_sig_redirect())


class EmailBrandingUpdateView(LoginRequiredMixin, View):
    """Settings: outgoing-email header color + logo (Email Templates tab)."""

    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        from .forms import EmailBrandingForm
        site = SiteSettings.get()
        # Allow clearing the header color back to "use app Title Bar color".
        form = EmailBrandingForm(request.POST, request.FILES, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, 'Email branding saved.')
        else:
            messages.error(request, 'Could not save email branding — check the values.')
        return redirect(reverse('core:settings') + '?tab=email_templates')


class SLADefaultsUpdateView(LoginRequiredMixin, View):
    """Settings: client-type default SLA (SLA Plans tab)."""

    def post(self, request):
        if not _is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        from .forms import SLADefaultsForm
        site = SiteSettings.get()
        form = SLADefaultsForm(request.POST, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, 'SLA defaults saved.')
        else:
            messages.error(request, 'Could not save SLA defaults — check the values.')
        return redirect(reverse('core:settings') + '?tab=sla_plans')


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


class TicketStatusUpdateView(LoginRequiredMixin, View):
    """Quick status change from ticket detail page."""
    def post(self, request, pk):
        ticket = _get_scoped_ticket_or_404(request, pk)
        new_status = request.POST.get('status', '').strip()
        if not new_status:
            return redirect('core:ticket_detail', pk=pk)
        # No block on closing a ticket with an open linked WO — see TicketUpdateView.form_valid.
        old_status = ticket.status
        ticket.apply_status_change(new_status)
        if new_status in TICKET_CLOSED_STATUSES and ticket.wo_complete:
            ticket.wo_complete = False
        ticket.save(update_fields=['status', 'closed_at', 'wo_complete', 'updated_at'])
        if new_status != old_status:
            from .email_utils import send_ticket_email
            send_ticket_email('status_changed', ticket, {'old_status': old_status})
            if new_status == 'resolved':
                send_ticket_email('ticket_resolved', ticket)
        messages.success(request, f'Status updated to {new_status.replace("_", " ").title()}.')
        return redirect('core:ticket_detail', pk=pk)
