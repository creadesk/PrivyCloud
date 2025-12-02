from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

import dashboard.views
from django.conf import settings

import authent.views
import paas.views
from core.settings import STRING_TO_ADMIN_PATH

urlpatterns = [
    path(STRING_TO_ADMIN_PATH, admin.site.urls, name="admin"),
    path('',dashboard.views.dashboard, name="dashboard"),
    path('verify-2fa/', authent.views.verify_2fa, name='verify_2fa'),
    path('register/', authent.views.register_view,name='register'),
    path('login/', authent.views.login_view,name='login'),
    path('logout/', authent.views.logout_view,name='logout'),
    path('captcha/', include('captcha.urls')),
    path('paas/',paas.views.my_apps, name="paas_my_apps"),
    path('paas/select_app',paas.views.select_app, name="paas_select_app"),
    path('paas/deploy_app',paas.views.deploy_app, name="paas_deploy_app"),
    path('paas/delete_app/<int:pk>/', paas.views.delete_app, name="paas_delete_app"),
]

#this is only for development purpose
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)