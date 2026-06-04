from django.shortcuts import render
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.db.models import Q
from .models import WorkOrder, Client, Device, Mileage


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
