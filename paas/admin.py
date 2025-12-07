from django.contrib import admin
from .models import (
  AppDefinition,
  RemoteHost,
  ProvisionedApp,
  AppEnvVarPerApp,
  AppVolumePerApp,
  ConfigPatch,
  UserDeploymentLimit,
)

@admin.register(AppDefinition)
class AppDefinitionAdmin(admin.ModelAdmin):
  list_display = ('name', 'display_name', 'docker_image', 'default_duration', 'app_port_intern_web', 'app_port_intern_api', 'hiddenservice_port_web', 'hiddenservice_port_api', 'use_deploy_user')
  search_fields = ('name', 'display_name')

@admin.register(RemoteHost)
class RemoteHostAdmin(admin.ModelAdmin):
  list_display = ('hostname', 'ip_address')
  search_fields = ('hostname', 'ip_address')

@admin.register(ProvisionedApp)
class ProvisionedAppAdmin(admin.ModelAdmin):
    list_display = ('user', 'app', 'host', 'status', 'expires_at')
    list_filter = ('status', 'expires_at')
    search_fields = ('user__username', 'app__name', 'host__hostname')

@admin.register(UserDeploymentLimit)
class UserDeploymentLimitAdmin(admin.ModelAdmin):
    list_display = ('user', 'max_concurrent_apps', 'max_total_hours_per_day', 'max_duration')
    search_fields = ('user__username',)

@admin.register(AppEnvVarPerApp)
class AppEnvVarPerAppAdmin(admin.ModelAdmin):
  list_display = ('app', 'key', 'value', 'editable')
  list_filter = ('editable',)
  search_fields = ('app__name', 'key')

@admin.register(AppVolumePerApp)
class AppVolumePerAppAdmin(admin.ModelAdmin):
  list_display = ('app', 'host_path', 'container_path')
  search_fields = ('app__name', 'host_path', 'container_path')

@admin.register(ConfigPatch)
class ConfigPatchAdmin(admin.ModelAdmin):
  list_display = ('app', 'volume', 'target_file', 'action')
  list_filter = ('action',)
  search_fields = ('app__name', 'volume__app__name', 'target_file')