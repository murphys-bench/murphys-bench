from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('work-orders/', views.WorkOrderListView.as_view(), name='work_order_list'),
]
