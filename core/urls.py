from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('csp-report/', views.csp_report, name='csp_report'),
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('notifications/', views.NotificationListView.as_view(), name='notifications'),
    path('notifications/count/', views.NotificationCountView.as_view(), name='notification_count'),
    path('notifications/read-all/', views.NotificationMarkAllReadView.as_view(), name='notification_read_all'),
    path('notifications/<int:pk>/open/', views.NotificationOpenView.as_view(), name='notification_open'),
    path('work-orders/<int:pk>/message-tech/', views.TechMessageView.as_view(source='wo'), name='wo_message_tech'),
    path('tickets/<int:pk>/message-tech/', views.TechMessageView.as_view(source='ticket'), name='ticket_message_tech'),
    path('work-orders/', views.WorkOrderListView.as_view(), name='work_order_list'),
    path('work-orders/<int:pk>/', views.WorkOrderDetailView.as_view(), name='work_order_detail'),
    path('clients/', views.ClientListView.as_view(), name='client_list'),
    path('clients/<int:pk>/', views.ClientDetailView.as_view(), name='client_detail'),
    path('devices/', views.DeviceListView.as_view(), name='device_list'),
    path('devices/new/', views.DeviceCreateView.as_view(), name='device_create'),
    path('devices/<int:pk>/', views.DeviceDetailView.as_view(), name='device_detail'),
    path('devices/<int:pk>/edit/', views.DeviceUpdateView.as_view(), name='device_edit'),
    path('devices/<int:pk>/delete/', views.DeviceDeleteView.as_view(), name='device_delete'),
    path('mileage/', views.MileageListView.as_view(), name='mileage_list'),
    path('mileage/new/', views.MileageCreateView.as_view(), name='mileage_create'),
    path('mileage/<int:pk>/edit/', views.MileageUpdateView.as_view(), name='mileage_edit'),
    path('mileage/<int:pk>/delete/', views.MileageDeleteView.as_view(), name='mileage_delete'),
    path('mileage/calculate/', views.MileageDistanceView.as_view(), name='mileage_calculate'),
    path('work-orders/<int:pk>/add-mileage/', views.WorkOrderMileageCreateView.as_view(), name='wo_mileage_create'),
    path('work-orders/new/', views.WorkOrderCreateView.as_view(), name='work_order_create'),
    path('work-orders/<int:pk>/edit/', views.WorkOrderUpdateView.as_view(), name='work_order_edit'),
    path('work-orders/<int:pk>/delete/', views.WorkOrderDeleteView.as_view(), name='work_order_delete'),
    path('work-orders/<int:pk>/notes/add/', views.WorkOrderNoteCreateView.as_view(), name='work_order_note_add'),
    path('work-orders/<int:pk>/add-time/', views.WorkOrderAddTimeView.as_view(), name='work_order_add_time'),
    path('work-orders/<int:pk>/quick-update/', views.WorkOrderQuickUpdateView.as_view(), name='work_order_quick_update'),
    path('work-orders/<int:pk>/apply-checklist/', views.WorkOrderApplyChecklistView.as_view(), name='work_order_apply_checklist'),
    path('work-orders/<int:pk>/upload/', views.WorkOrderAttachmentUploadView.as_view(), name='work_order_upload'),
    path('work-orders/items/<int:pk>/check/', views.WorkOrderItemCheckView.as_view(), name='work_order_item_check'),
    path('clients/new/', views.ClientCreateView.as_view(), name='client_create'),
    path('clients/<int:pk>/edit/', views.ClientUpdateView.as_view(), name='client_edit'),
    path('clients/<int:pk>/delete/', views.ClientDeleteView.as_view(), name='client_delete'),
    path('clients/<int:pk>/invoices.csv', views.InvoiceExportView.as_view(), name='client_invoice_export'),
    path('prospects/', views.ProspectListView.as_view(), name='prospect_list'),
    path('prospects/new/', views.ProspectCreateView.as_view(), name='prospect_create'),
    path('prospects/<int:pk>/', views.ProspectDetailView.as_view(), name='prospect_detail'),
    path('prospects/<int:pk>/edit/', views.ProspectUpdateView.as_view(), name='prospect_edit'),
    path('prospects/<int:pk>/promote/', views.ProspectPromoteView.as_view(), name='prospect_promote'),
    path('prospects/<int:pk>/mark-lost/', views.ProspectMarkLostView.as_view(), name='prospect_mark_lost'),
    path('prospects/<int:pk>/delete/', views.ProspectDeleteView.as_view(), name='prospect_delete'),

    path('estimates/', views.EstimateListView.as_view(), name='estimate_list'),
    path('estimates/new/', views.EstimateCreateView.as_view(), name='estimate_create'),
    path('estimates/<int:pk>/', views.EstimateDetailView.as_view(), name='estimate_detail'),
    path('estimates/<int:pk>/edit/', views.EstimateUpdateView.as_view(), name='estimate_edit'),
    path('estimates/<int:pk>/mark-sent/', views.EstimateMarkSentView.as_view(), name='estimate_mark_sent'),
    path('estimates/<int:pk>/delete/', views.EstimateDeleteView.as_view(), name='estimate_delete'),
    path('estimates/<int:est_pk>/log-labor/<int:item_pk>/', views.EstimateLaborLogView.as_view(), name='estimate_labor_log'),
    path('estimates/<int:est_pk>/log-custom/', views.EstimateCustomLogView.as_view(), name='estimate_custom_log'),
    path('estimates/<int:pk>/quote/', views.EstimateQuotePrintView.as_view(), name='estimate_quote_print'),
    path('estimates/<int:pk>/quote/email/', views.EstimateQuoteEmailView.as_view(), name='estimate_quote_email'),
    path('tickets/', views.TicketListView.as_view(), name='ticket_list'),
    path('tickets/new/', views.TicketCreateView.as_view(), name='ticket_create'),
    path('tickets/<int:pk>/', views.TicketDetailView.as_view(), name='ticket_detail'),
    path('tickets/<int:pk>/edit/', views.TicketUpdateView.as_view(), name='ticket_edit'),
    path('tickets/<int:pk>/replies/add/', views.TicketReplyCreateView.as_view(), name='ticket_reply_add'),
    path('tickets/<int:pk>/replies/<int:reply_pk>/resend/', views.TicketReplyResendView.as_view(), name='ticket_reply_resend'),
    path('tickets/<int:pk>/convert/', views.TicketConvertView.as_view(), name='ticket_convert'),
    path('tickets/<int:pk>/lock/release/', views.TicketLockReleaseView.as_view(), name='ticket_lock_release'),
    path('tickets/<int:pk>/lock/status/', views.TicketLockStatusView.as_view(), name='ticket_lock_status'),
    path('tickets/<int:pk>/links/add/', views.TicketLinkAddView.as_view(), name='ticket_link_add'),
    path('tickets/<int:pk>/links/remove/', views.TicketLinkRemoveView.as_view(), name='ticket_link_remove'),
    path('attachments/<int:pk>/download/', views.AttachmentDownloadView.as_view(), name='attachment_download'),
    path('tickets/<int:pk>/acknowledge-overdue/', views.TicketAcknowledgeOverdueView.as_view(), name='ticket_acknowledge_overdue'),
    path('tickets/<int:pk>/dismiss-response/', views.TicketDismissNeedsResponseView.as_view(), name='ticket_dismiss_response'),
    path('tickets/<int:pk>/assign/', views.TicketAssignView.as_view(), name='ticket_assign'),
    path('tickets/<int:pk>/escalate/', views.TicketEscalateView.as_view(), name='ticket_escalate'),
    path('tickets/<int:pk>/close/', views.TicketCloseView.as_view(), name='ticket_close'),
    path('tickets/<int:pk>/status/', views.TicketStatusUpdateView.as_view(), name='ticket_status_update'),
    path('tickets/<int:pk>/delete/', views.TicketDeleteView.as_view(), name='ticket_delete'),
    path('tickets/contacts-by-client/', views.TicketContactsByClientView.as_view(), name='ticket_contacts_by_client'),
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
    path('users/new/', views.UserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/edit/', views.UserEditView.as_view(), name='user_edit'),
    path('users/<int:pk>/set-password/', views.UserSetPasswordView.as_view(), name='user_set_password'),
    path('users/<int:pk>/delete/', views.UserDeleteView.as_view(), name='user_delete'),
    path('users/<int:pk>/reset-mfa/', views.AdminMFAResetView.as_view(), name='user_mfa_reset'),
    # Role management (admin only)
    path('roles/', views.RoleListView.as_view(), name='role_list'),
    path('roles/new/', views.RoleCreateView.as_view(), name='role_create'),
    path('roles/<int:pk>/edit/', views.RoleEditView.as_view(), name='role_edit'),
    path('roles/<int:pk>/delete/', views.RoleDeleteView.as_view(), name='role_delete'),

    # Invoice Ninja (Phase B)
    path('settings/invoice-ninja/test/', views.InvoiceNinjaTestView.as_view(), name='invoice_ninja_test'),
    path('work-orders/<int:pk>/send-to-invoice-ninja/', views.WorkOrderSendToINView.as_view(), name='work_order_send_in'),

    # Quick Labor / Work Performed (HTMX)
    path('work-orders/<int:wo_pk>/log-labor/<int:item_pk>/', views.WorkPerformedLogView.as_view(), name='work_performed_log'),
    path('work-orders/<int:wo_pk>/log-custom/', views.WorkPerformedCustomLogView.as_view(), name='work_performed_custom'),
    path('work-performed/<int:pk>/delete/', views.WorkPerformedDeleteView.as_view(), name='work_performed_delete'),
    path('work-performed/<int:pk>/update/', views.WorkPerformedUpdateView.as_view(), name='work_performed_update'),

    # Repair Report (print view + email-as-PDF)
    path('work-orders/<int:pk>/print/', views.WorkOrderPrintView.as_view(), name='work_order_print'),
    path('work-orders/<int:pk>/email-report/', views.WorkOrderReportEmailView.as_view(), name='work_order_email_report'),

    # Credentials + Billing (HTMX on WO detail)
    path('work-orders/<int:pk>/credentials/', views.WorkOrderCredentialsSaveView.as_view(), name='wo_credentials_save'),
    path('work-orders/<int:pk>/billing/', views.WorkOrderBillingUpdateView.as_view(), name='wo_billing_update'),
    path('work-orders/<int:pk>/billing/check-in-status/', views.WorkOrderBillingCheckINView.as_view(), name='wo_billing_check_in'),
    path('work-orders/<int:pk>/claim/', views.WorkOrderClaimView.as_view(), name='wo_claim'),

    # Contact management (HTMX inline on client detail)
    path('clients/<int:client_pk>/contacts/new/', views.ContactCreateView.as_view(), name='contact_create'),
    path('contacts/<int:pk>/edit/', views.ContactUpdateView.as_view(), name='contact_edit'),
    path('contacts/<int:pk>/delete/', views.ContactDeleteView.as_view(), name='contact_delete'),
    path('contacts/<int:pk>/set-primary/', views.ContactSetPrimaryView.as_view(), name='contact_set_primary'),

    # Native Settings UI
    path('settings/', views.SettingsView.as_view(), name='settings'),
    path('settings/test-outbound-email/', views.EmailTestOutboundView.as_view(), name='settings_test_outbound'),
    path('settings/suppressed-addresses/add/', views.SuppressedAddressAddView.as_view(), name='suppressed_address_add'),
    path('settings/suppressed-addresses/<int:pk>/delete/', views.SuppressedAddressDeleteView.as_view(), name='suppressed_address_delete'),
    path('settings/blocked-senders/add/', views.BlockedSenderAddView.as_view(), name='blocked_sender_add'),
    path('settings/blocked-senders/<int:pk>/delete/', views.BlockedSenderDeleteView.as_view(), name='blocked_sender_delete'),
    path('settings/test-inbound-email/', views.EmailTestInboundView.as_view(), name='settings_test_inbound'),

    # Settings — In-app updates (admin only)
    path('settings/updates/status/', views.UpdateStatusView.as_view(), name='update_status'),
    path('settings/updates/check/', views.UpdateCheckView.as_view(), name='update_check'),
    path('settings/updates/start/', views.UpdateTriggerView.as_view(), name='update_start'),

    # Settings — Repair Types CRUD
    path('settings/repair-type-categories/new/', views.RepairTypeCategoryCreateView.as_view(), name='rt_category_create'),
    path('settings/repair-type-categories/<int:pk>/delete/', views.RepairTypeCategoryDeleteView.as_view(), name='rt_category_delete'),
    path('settings/repair-type-categories/<int:pk>/reorder/', views.RepairTypeCategoryReorderView.as_view(), name='rt_category_reorder'),
    path('settings/repair-types/new/', views.RepairTypeCreateView.as_view(), name='rt_create'),
    path('settings/repair-types/<int:pk>/edit/', views.RepairTypeUpdateView.as_view(), name='rt_update'),
    path('settings/repair-types/<int:pk>/delete/', views.RepairTypeDeleteView.as_view(), name='rt_delete'),

    # Settings — KB Categories CRUD
    path('settings/kb-categories/new/', views.KBCategoryCreateView.as_view(), name='kb_category_create'),
    path('settings/kb-categories/<int:pk>/edit/', views.KBCategoryUpdateView.as_view(), name='kb_category_update'),
    path('settings/kb-categories/<int:pk>/delete/', views.KBCategoryDeleteView.as_view(), name='kb_category_delete'),

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

    # Settings — Email Templates
    path('settings/email-templates/<int:pk>/edit/', views.EmailTemplateUpdateView.as_view(), name='email_template_update'),
    path('settings/email-branding/save/', views.EmailBrandingUpdateView.as_view(), name='email_branding_save'),

    # Settings — Email Signatures
    path('settings/email-signatures/new/', views.EmailSignatureCreateView.as_view(), name='email_sig_create'),
    path('settings/email-signatures/<int:pk>/edit/', views.EmailSignatureUpdateView.as_view(), name='email_sig_update'),
    path('settings/email-signatures/<int:pk>/delete/', views.EmailSignatureDeleteView.as_view(), name='email_sig_delete'),

    # Settings — Org Credentials Vault
    path('settings/credentials/new/', views.OrgCredentialCreateView.as_view(), name='cred_create'),
    path('settings/credentials/<int:pk>/edit/', views.OrgCredentialUpdateView.as_view(), name='cred_update'),
    path('settings/credentials/<int:pk>/delete/', views.OrgCredentialDeleteView.as_view(), name='cred_delete'),
    path('settings/credentials/<int:pk>/reveal/<str:field>/', views.OrgCredentialRevealView.as_view(), name='cred_reveal'),

    # Device Credentials
    path('devices/<int:pk>/credentials/reveal/<str:field>/', views.DeviceCredentialRevealView.as_view(), name='device_cred_reveal'),
    path('devices/<int:pk>/credentials/', views.DeviceCredentialUpdateView.as_view(), name='device_cred_update'),

    # Settings — SLA Plans
    path('settings/sla-plans/new/', views.SLAPlanCreateView.as_view(), name='sla_plan_create'),
    path('settings/sla-plans/<int:pk>/edit/', views.SLAPlanUpdateView.as_view(), name='sla_plan_update'),
    path('settings/sla-plans/<int:pk>/delete/', views.SLAPlanDeleteView.as_view(), name='sla_plan_delete'),

    # Settings — Help Topics
    path('settings/help-topics/new/', views.HelpTopicCreateView.as_view(), name='help_topic_create'),
    path('settings/help-topics/<int:pk>/edit/', views.HelpTopicUpdateView.as_view(), name='help_topic_update'),
    path('settings/help-topics/<int:pk>/delete/', views.HelpTopicDeleteView.as_view(), name='help_topic_delete'),

    # Settings — Tech Skills
    path('settings/tech-skills/new/', views.TechSkillCreateView.as_view(), name='tech_skill_create'),
    path('settings/tech-skills/<int:pk>/delete/', views.TechSkillDeleteView.as_view(), name='tech_skill_delete'),

    # Settings — Dashboard Tiles
    path('settings/dashboard-tiles/<int:pk>/edit/', views.DashboardTileUpdateView.as_view(), name='dashboard_tile_update'),

    # Settings — Custom Fields
    path('settings/custom-fields/new/', views.CustomFieldCreateView.as_view(), name='cf_create'),
    path('settings/custom-fields/<int:pk>/edit/', views.CustomFieldUpdateView.as_view(), name='cf_update'),
    path('settings/custom-fields/<int:pk>/delete/', views.CustomFieldDeleteView.as_view(), name='cf_delete'),
    path('settings/custom-fields/<int:pk>/choices/add/', views.CustomFieldChoiceAddView.as_view(), name='cf_choice_add'),
    path('settings/custom-field-choices/<int:pk>/delete/', views.CustomFieldChoiceDeleteView.as_view(), name='cf_choice_delete'),

    # Status Management
    path('settings/statuses/new/', views.StatusDefinitionCreateView.as_view(), name='status_create'),
    path('settings/statuses/<int:pk>/edit/', views.StatusDefinitionUpdateView.as_view(), name='status_update'),
    path('settings/statuses/<int:pk>/delete/', views.StatusDefinitionDeleteView.as_view(), name='status_delete'),
]
