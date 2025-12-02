from django.contrib import admin
from config.models import PlatformSetting

@admin.register(PlatformSetting)
class PlatformSettingAdmin(admin.ModelAdmin):
  list_display = ("key", "value", "description")
  search_fields = ("key", "value")