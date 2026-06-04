from django.shortcuts import render
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.urls import reverse_lazy
from django.db.models import Q
from .models import WorkOrder, Client, Device


class WorkOrderListView(ListView):
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


class WorkOrderDetailView(DetailView):
    """Display full details of a single work order"""
    model = WorkOrder
    template_name = 'core/work_order_detail.html'
    context_object_name = 'work_order'

    def get_queryset(self):
        return WorkOrder.objects.select_related(
            'client', 'assigned_to', 'device', 'repair_type', 'ticket'
        ).prefetch_related('notes', 'items', 'notes__created_by')


class ClientListView(ListView):
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


class ClientDetailView(DetailView):
    """Display full details of a single client"""
    model = Client
    template_name = 'core/client_detail.html'
    context_object_name = 'client'

    def get_queryset(self):
        return Client.objects.prefetch_related(
            'contacts', 'devices', 'work_orders', 'work_orders__assigned_to'
        )
