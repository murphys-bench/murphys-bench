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
    path('mileage/calculate/', views.MileageDistanceView.as_view(), name='mileage_calculate'),
    path('work-orders/<int:pk>/add-mileage/', views.WorkOrderMileageCreateView.as_view(), name='wo_mileage_create'),
    path('work-orders/new/', views.WorkOrderCreateView.as_view(), name='work_order_create'),
    path('work-orders/<int:pk>/edit/', views.WorkOrderUpdateView.as_view(), name='work_order_edit'),
    path('work-orders/<int:pk>/notes/add/', views.WorkOrderNoteCreateView.as_view(), name='work_order_note_add'),
    path('work-orders/<int:pk>/add-time/', views.WorkOrderAddTimeView.as_view(), name='work_order_add_time'),
    path('work-orders/<int:pk>/quick-update/', views.WorkOrderQuickUpdateView.as_view(), name='work_order_quick_update'),
    path('work-orders/<int:pk>/apply-checklist/', views.WorkOrderApplyChecklistView.as_view(), name='work_order_apply_checklist'),
    path('work-orders/<int:pk>/upload/', views.WorkOrderAttachmentUploadView.as_view(), name='work_order_upload'),
    path('work-orders/items/<int:pk>/check/', views.WorkOrderItemCheckView.as_view(), name='work_order_item_check'),
    path('clients/new/', views.ClientCreateView.as_view(), name='client_create'),
    path('clients/<int:pk>/edit/', views.ClientUpdateView.as_view(), name='client_edit'),
    path('clients/<int:pk>/delete/', views.ClientDeleteView.as_view(), name='client_delete'),
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

    # Quick Labor / Work Performed (HTMX)
    path('work-orders/<int:wo_pk>/log-labor/<int:item_pk>/', views.WorkPerformedLogView.as_view(), name='work_performed_log'),
    path('work-orders/<int:wo_pk>/log-custom/', views.WorkPerformedCustomLogView.as_view(), name='work_performed_custom'),
    path('work-performed/<int:pk>/delete/', views.WorkPerformedDeleteView.as_view(), name='work_performed_delete'),
    path('work-performed/<int:pk>/update/', views.WorkPerformedUpdateView.as_view(), name='work_performed_update'),

    # Repair Report (print view)
    path('work-orders/<int:pk>/print/', views.WorkOrderPrintView.as_view(), name='work_order_print'),

    # Credentials (HTMX on WO detail)
    path('work-orders/<int:pk>/credentials/', views.WorkOrderCredentialsSaveView.as_view(), name='wo_credentials_save'),

    # Contact management (HTMX inline on client detail)
    path('clients/<int:client_pk>/contacts/new/', views.ContactCreateView.as_view(), name='contact_create'),
    path('contacts/<int:pk>/edit/', views.ContactUpdateView.as_view(), name='contact_edit'),
    path('contacts/<int:pk>/delete/', views.ContactDeleteView.as_view(), name='contact_delete'),
    path('contacts/<int:pk>/set-primary/', views.ContactSetPrimaryView.as_view(), name='contact_set_primary'),

    # Native Settings UI
    path('settings/', views.SettingsView.as_view(), name='settings'),
    path('settings/test-outbound-email/', views.EmailTestOutboundView.as_view(), name='settings_test_outbound'),
    path('settings/test-inbound-email/', views.EmailTestInboundView.as_view(), name='settings_test_inbound'),

    # Settings — Repair Types CRUD
    path('settings/repair-type-categories/new/', views.RepairTypeCategoryCreateView.as_view(), name='rt_category_create'),
    path('settings/repair-type-categories/<int:pk>/delete/', views.RepairTypeCategoryDeleteView.as_view(), name='rt_category_delete'),
    path('settings/repair-type-categories/<int:pk>/reorder/', views.RepairTypeCategoryReorderView.as_view(), name='rt_category_reorder'),
    path('settings/repair-types/new/', views.RepairTypeCreateView.as_view(), name='rt_create'),
    path('settings/repair-types/<int:pk>/edit/', views.RepairTypeUpdateView.as_view(), name='rt_update'),
    path('settings/repair-types/<int:pk>/delete/', views.RepairTypeDeleteView.as_view(), name='rt_delete'),

    # Settings — Canned Responses CRUD
    path('settings/canned-response-categories/new/', views.CannedResponseCategoryCreateView.as_view(), name='cr_category_create'),
    path('settings/canned-response-categories/<int:pk>/delete/', views.CannedResponseCategoryDeleteView.as_view(), name='cr_category_delete'),
    path('settings/canned-response-categories/<int:pk>/reorder/', views.CannedResponseCategoryReorderView.as_view(), name='cr_category_reorder'),
    path('settings/canned-responses/new/', views.CannedResponseCreateView.as_view(), name='cr_create'),
    path('settings/canned-responses/<int:pk>/edit/', views.CannedResponseUpdateView.as_view(), name='cr_update'),
    path('settings/canned-responses/<int:pk>/delete/', views.CannedResponseDeleteView.as_view(), name='cr_delete'),
    path('settings/canned-responses/picker/', views.CannedResponsePickerView.as_view(), name='cr_picker'),

    # Settings — Quick Labor CRUD
    path('settings/quick-labor/new/', views.QuickLaborCreateView.as_view(), name='ql_create'),
    path('settings/quick-labor/<int:pk>/edit/', views.QuickLaborUpdateView.as_view(), name='ql_update'),
    path('settings/quick-labor/<int:pk>/delete/', views.QuickLaborDeleteView.as_view(), name='ql_delete'),

    # Settings — Checklist Items CRUD
    path('settings/checklist-items/new/', views.ChecklistItemCreateView.as_view(), name='cli_create'),
    path('settings/checklist-items/<int:pk>/edit/', views.ChecklistItemUpdateView.as_view(), name='cli_update'),
    path('settings/checklist-items/<int:pk>/delete/', views.ChecklistItemDeleteView.as_view(), name='cli_delete'),
]
