from django.contrib import admin
from .models import (
  AppImageTag,
  AppDefinition,
  RemoteHost,
  ProvisionedApp,
  AppEnvVarPerApp,
  AppVolumePerApp,
  ConfigPatch,
  UserDeploymentLimit,
)
from django.contrib import admin
from django_celery_beat.models import (
    PeriodicTask, IntervalSchedule, CrontabSchedule,
    SolarSchedule, ClockedSchedule
)

@admin.register(AppImageTag)
class AppImageTagAdmin(admin.ModelAdmin):
  list_display = ('id', 'tag', 'app_definition')
  search_fields = ('id', 'tag', 'app_definition')

@admin.register(AppDefinition)
class AppDefinitionAdmin(admin.ModelAdmin):
  list_display = ('id', 'name', 'display_name', 'docker_image', 'default_duration', 'app_port_intern_web', 'app_port_intern_api', 'hiddenservice_port_web', 'hiddenservice_port_api', 'use_deploy_user', 'no_hidden_service', 'use_hostpid_namespace', 'tor_auth_type')
  search_fields = ('name', 'display_name')

@admin.register(RemoteHost)
class RemoteHostAdmin(admin.ModelAdmin):
  list_display = ('id', 'hostname', 'ip_address', 'ssh_user', 'ssh_key_path', 'current_load', 'nur_superuser')
  list_filter = ('current_load',)
  search_fields = ('hostname', 'ip_address')

@admin.register(ProvisionedApp)
class ProvisionedAppAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'app', 'host', 'status', 'expires_at')
    list_filter = ('status', 'expires_at')
    search_fields = ('user__username', 'app__name', 'host__hostname')

@admin.register(UserDeploymentLimit)
class UserDeploymentLimitAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'max_concurrent_apps', 'max_total_hours_per_day', 'max_duration')
    search_fields = ('user__username',)

@admin.register(AppEnvVarPerApp)
class AppEnvVarPerAppAdmin(admin.ModelAdmin):
  list_display = ('id', 'app', 'key', 'value', 'editable', 'optional')
  list_filter = ('editable',)
  search_fields = ('app__name', 'key')

@admin.register(AppVolumePerApp)
class AppVolumePerAppAdmin(admin.ModelAdmin):
  list_display = ('id', 'app', 'host_path', 'container_path')
  search_fields = ('app__name', 'host_path', 'container_path')

@admin.register(ConfigPatch)
class ConfigPatchAdmin(admin.ModelAdmin):
  list_display = ('id', 'app', 'volume', 'target_file', 'action')
  list_filter = ('action',)
  search_fields = ('app__name', 'volume__app__name', 'target_file')


for model in [PeriodicTask, IntervalSchedule, CrontabSchedule, SolarSchedule, ClockedSchedule]:
    try:
        admin.site.unregister(model)
    except admin.sites.NotRegistered:
        pass