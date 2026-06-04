from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('work-orders/', views.WorkOrderListView.as_view(), name='work_order_list'),
    path('work-orders/<int:pk>/', views.WorkOrderDetailView.as_view(), name='work_order_detail'),
    path('clients/', views.ClientListView.as_view(), name='client_list'),
    path('clients/<int:pk>/', views.ClientDetailView.as_view(), name='client_detail'),
]
