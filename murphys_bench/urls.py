from django.contrib import admin
from django.urls import path, include
from two_factor.urls import urlpatterns as tf_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include(tf_urlpatterns)),
    path('', include('core.urls')),
    path('accounts/', include('accounts.urls')),
]
