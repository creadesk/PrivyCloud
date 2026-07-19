'''
from django.contrib import admin
from config.models import PlatformSetting

@admin.register(PlatformSetting)
class PlatformSettingAdmin(admin.ModelAdmin):
  list_display = ("key", "value", "description")
  search_fields = ("key", "value")
'''

from django.contrib import admin
from django.apps import apps

app_config = apps.get_app_config("django_smart_ratelimit")

for model in app_config.get_models():
    try:
        admin.site.unregister(model)
    except admin.sites.NotRegistered:
        pass