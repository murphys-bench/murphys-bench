from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('work-orders/', views.WorkOrderListView.as_view(), name='work_order_list'),
    path('work-orders/<int:pk>/', views.WorkOrderDetailView.as_view(), name='work_order_detail'),
    path('clients/', views.ClientListView.as_view(), name='client_list'),
    path('clients/<int:pk>/', views.ClientDetailView.as_view(), name='client_detail'),
    path('devices/', views.DeviceListView.as_view(), name='device_list'),
    path('devices/new/', views.DeviceCreateView.as_view(), name='device_create'),
    path('devices/<int:pk>/', views.DeviceDetailView.as_view(), name='device_detail'),
    path('devices/<int:pk>/edit/', views.DeviceUpdateView.as_view(), name='device_edit'),
    path('mileage/', views.MileageListView.as_view(), name='mileage_list'),
    path('work-orders/new/', views.WorkOrderCreateView.as_view(), name='work_order_create'),
    path('work-orders/<int:pk>/edit/', views.WorkOrderUpdateView.as_view(), name='work_order_edit'),
    path('work-orders/<int:pk>/notes/add/', views.WorkOrderNoteCreateView.as_view(), name='work_order_note_add'),
    path('work-orders/items/<int:pk>/toggle/', views.WorkOrderItemToggleView.as_view(), name='work_order_item_toggle'),
    path('clients/new/', views.ClientCreateView.as_view(), name='client_create'),
    path('clients/<int:pk>/edit/', views.ClientUpdateView.as_view(), name='client_edit'),
    path('tickets/', views.TicketListView.as_view(), name='ticket_list'),
    path('tickets/new/', views.TicketCreateView.as_view(), name='ticket_create'),
    path('tickets/<int:pk>/', views.TicketDetailView.as_view(), name='ticket_detail'),
    path('tickets/<int:pk>/edit/', views.TicketUpdateView.as_view(), name='ticket_edit'),
    path('tickets/<int:pk>/replies/add/', views.TicketReplyCreateView.as_view(), name='ticket_reply_add'),
    path('tickets/<int:pk>/convert/', views.TicketConvertView.as_view(), name='ticket_convert'),
    path('tickets/<int:pk>/lock/release/', views.TicketLockReleaseView.as_view(), name='ticket_lock_release'),
    path('tickets/<int:pk>/lock/status/', views.TicketLockStatusView.as_view(), name='ticket_lock_status'),
    path('tickets/<int:pk>/links/add/', views.TicketLinkAddView.as_view(), name='ticket_link_add'),
    path('tickets/<int:pk>/links/remove/', views.TicketLinkRemoveView.as_view(), name='ticket_link_remove'),
    path('attachments/<int:pk>/download/', views.AttachmentDownloadView.as_view(), name='attachment_download'),
]
