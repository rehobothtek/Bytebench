from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    # Django's own auth views — login, logout, and the whole password-reset
    # flow — left un-namespaced so that LOGIN_URL = 'login' and
    # {% url 'logout' %} resolve to them directly. Customer registration lives
    # in shop.urls, since it collects shop-specific details too.
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('shop.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
