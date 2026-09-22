from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
#from django.conf.urls.static import static
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    # Django's own auth views — login, logout, and the whole password-reset
    # flow — left un-namespaced so that LOGIN_URL = 'login' and
    # {% url 'logout' %} resolve to them directly. Customer registration lives
    # in shop.urls, since it collects shop-specific details too.
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('shop.urls')),
]

urlpatterns += [
    re_path(
        r"^media/(?P<path>.*)$",
        serve,
        {"document_root": settings.MEDIA_ROOT},
    )
]
