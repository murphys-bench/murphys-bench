from django.shortcuts import render
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.db import models
from .models import WorkOrder, Client, Device


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
                models.Q(work_order_number__icontains=search) |
                models.Q(client__name__icontains=search)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = WorkOrder.STATUS_CHOICES
        context['priority_choices'] = WorkOrder.PRIORITY_CHOICES
        return context
