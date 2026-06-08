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
    path('mileage/new/', views.MileageCreateView.as_view(), name='mileage_create'),
    path('mileage/<int:pk>/edit/', views.MileageUpdateView.as_view(), name='mileage_edit'),
    path('work-orders/new/', views.WorkOrderCreateView.as_view(), name='work_order_create'),
    path('work-orders/<int:pk>/edit/', views.WorkOrderUpdateView.as_view(), name='work_order_edit'),
    path('work-orders/<int:pk>/notes/add/', views.WorkOrderNoteCreateView.as_view(), name='work_order_note_add'),
    path('work-orders/<int:pk>/add-time/', views.WorkOrderAddTimeView.as_view(), name='work_order_add_time'),
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
    path('tickets/<int:pk>/acknowledge-overdue/', views.TicketAcknowledgeOverdueView.as_view(), name='ticket_acknowledge_overdue'),
    path('kb/', views.KBListView.as_view(), name='kb_list'),
    path('kb/new/', views.KBArticleCreateView.as_view(), name='kb_create'),
    path('kb/<int:pk>/', views.KBDetailView.as_view(), name='kb_detail'),
    path('kb/<int:pk>/edit/', views.KBArticleEditView.as_view(), name='kb_edit'),
    # Queues
    path('queues/', views.QueueListView.as_view(), name='queue_list'),
    path('queues/new/', views.QueueCreateView.as_view(), name='queue_create'),
    path('queues/<int:pk>/', views.QueueDetailView.as_view(), name='queue_detail'),
    path('queues/<int:pk>/edit/', views.QueueEditView.as_view(), name='queue_edit'),
    path('queues/<int:pk>/delete/', views.QueueDeleteView.as_view(), name='queue_delete'),
    # Sidebar fragment
    path('sidebar/', views.SidebarFragmentView.as_view(), name='sidebar_fragment'),
    # Reports
    path('reports/', views.ReportsView.as_view(), name='reports'),
    path('reports/csv/<str:report>/', views.ReportsCSVView.as_view(), name='reports_csv'),

    path('profile/security/', views.SecurityProfileView.as_view(), name='security_profile'),
    path('profile/security/backup-tokens/', views.AdminBackupTokensView.as_view(), name='backup_tokens'),

    # User management (admin only)
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('users/<int:pk>/reset-mfa/', views.AdminMFAResetView.as_view(), name='user_mfa_reset'),
]
