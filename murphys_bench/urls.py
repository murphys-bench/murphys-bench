from django.contrib import admin
from django.urls import path, include
from two_factor.urls import urlpatterns as tf_urlpatterns
from core.views import MFASetupView, AdminOnlyBackupTokensView

urlpatterns = [
    path('admin/', admin.site.urls),
    # Overrides two_factor's own 'setup' route (same path/name) — must come
    # before the include below, since the first matching pattern wins. See
    # MFASetupView for why the stock Cancel link needs suppressing.
    path('account/two_factor/setup/', MFASetupView.as_view(), name='setup'),
    # Same first-match-wins override as 'setup' above: replaces two_factor's
    # ungated backup-tokens view with the admin-only one. Must stay ABOVE the
    # include or upstream's version silently takes over again.
    path('account/two_factor/backup/tokens/', AdminOnlyBackupTokensView.as_view(),
         name='backup_tokens'),
    path('', include(tf_urlpatterns)),
    path('', include('core.urls')),
    path('accounts/', include('accounts.urls')),
]
