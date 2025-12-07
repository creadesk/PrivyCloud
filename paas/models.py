from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import uuid
from core.settings import PAAS_MAX_FREE_APPS_PER_USER

class AppDefinition(models.Model):
  """Vordefinierte Apps, die bereitgestellt werden können."""
  name = models.CharField(max_length=64, unique=True)
  display_name = models.CharField(max_length=128)
  docker_image = models.CharField(max_length=256)
  description = models.TextField(blank=True)
  default_duration = models.PositiveIntegerField(default=1)  # Stunden
  app_port_intern_web = models.PositiveIntegerField(default=80)  # Web-Port, den die App innerhalb des Dockercontainers anbietet
  app_port_intern_api = models.PositiveIntegerField(default=1)  # API-Port, den die App innerhalb des Dockercontainers anbietet
  hiddenservice_port_web = models.PositiveIntegerField(default=80)  # Web-Port für onion-service
  hiddenservice_port_api = models.PositiveIntegerField(default=1)  # API-Port für onion-service
  use_deploy_user = models.BooleanField(default=False, help_text="Container mit User {uid}:{gid} starten")

  class Meta:
      ordering = ['display_name']
      verbose_name = "App Definition"
      verbose_name_plural = "App Definitions"

  def __str__(self):
      return self.display_name


class RemoteHost(models.Model):
  """Liste der Debian‑Hosts, auf denen Container laufen können."""
  hostname = models.CharField(max_length=128, unique=True)
  ip_address = models.GenericIPAddressField()
  ssh_user = models.CharField(max_length=32, default='root')
  ssh_key_path = models.CharField(max_length=256)   # Pfad zur privaten Schlüsseldatei
  # Optionale Felder: Last, free_port_range etc.

  class Meta:
      verbose_name = "Remote Host"
      verbose_name_plural = "Remote Hosts"

  def __str__(self):
      #return f"{self.hostname} ({self.ip_address})"
      return f"{self.hostname}"


class ProvisionedApp(models.Model):
  """Aufgezeichnete Bereitstellungen."""
  user = models.ForeignKey(
      settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
      related_name='provisioned_apps',
  )
  app = models.ForeignKey(AppDefinition, on_delete=models.CASCADE)
  host = models.ForeignKey(RemoteHost, on_delete=models.CASCADE)
  container_id = models.CharField(max_length=64, blank=True, null=True)
  container_name = models.CharField(max_length=128, blank=True, null=True)
  started_at = models.DateTimeField(default=timezone.now)
  # `expires_at` ist jetzt nullable → „Ohne Limit“ kann als `None` gespeichert werden.
  expires_at = models.DateTimeField(
      null=True, blank=True,
      help_text=_('Zeitpunkt, zu dem die Bereitstellung endet. `None` = kein Limit.'),
  )
  port = models.PositiveIntegerField(blank=True, null=True)
  # Status: pending, running, finished, error, deleted
  status = models.CharField(max_length=32, default='pending')
  log = models.TextField(blank=True, null=True)
  onion_address = models.CharField(
      max_length=100, blank=True, null=True,
      help_text="Onion‑Adresse des Tor‑Hidden‑Services (falls erstellt)"
  )
  last_modified = models.DateTimeField(auto_now=True)

  class Meta:
  #   unique_together = ('user', 'app', 'host')   # keine Duplikate
      verbose_name = "Provisioned App"
      verbose_name_plural = "Provisioned Apps"

  def is_active(self):
      return self.status == 'active' and (self.expires_at is None or self.expires_at > timezone.now())

  def __str__(self):
      return f"{self.user} – {self.app} on {self.host}"


'''
> 1. **max_concurrent_apps** – verhindert, dass ein User zu viele Apps gleichzeitig laufen hat.  
> 2. **max_total_hours_per_day** – verhindert, dass ein User die Systemkapazität überstrapaziert.  
> 3. **max_duration** – limitiert einzelne Bereitstellungen (z.B. keine 6‑Monats‑Apps für Junior‑Admins).
'''
class UserDeploymentLimit(models.Model):
    """
    Speichert pro User, wie viele Apps gleichzeitig und wie lange
    ein User bereitstellen darf.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='deployment_limit',
    )
    max_concurrent_apps = models.PositiveIntegerField(
        default=3,
        help_text=_('Maximale Anzahl gleichzeitiger Bereitstellungen.'),
    )
    max_total_hours_per_day = models.PositiveIntegerField(
        default=48,
        help_text=_('Maximale Gesamtzeit (in Stunden) pro Tag.'),
    )
    max_duration = models.DurationField(
        null=True, blank=True,
        help_text=_('Maximale Dauer pro Bereitstellung. `None` = unbegrenzt.'),
    )

    def __str__(self):
        return f'{self.user} – {self.max_concurrent_apps} Apps'

    class Meta:
        verbose_name = _('Deployment‑Limit')
        verbose_name_plural = _('Deployment‑Limits')



# alle möglichen docker Umgebungsvariablen pro app mit Standardwerten
class AppEnvVarPerApp(models.Model):
    app = models.ForeignKey(AppDefinition, related_name='env_vars',
                            on_delete=models.CASCADE)
    key = models.CharField(max_length=64)
    value = models.CharField(max_length=256)

    optional = models.BooleanField(default=False)

    editable = models.BooleanField(default=False)

    class Meta:
        unique_together = ('app', 'key')
        verbose_name = "App Environment Variable"
        verbose_name_plural = "App Environment Variables"


class AppVolumePerApp(models.Model):
    """
    Alle möglichen Docker‑Volumes pro App mit Standardwerten.

    - **app**          : Verweis auf die App, für die das Volume gilt.
    - **host_path**    : Pfad auf dem Host‑Dateisystem.
    - **container_path** : Zielpfad im Docker‑Container.
    """
    app = models.ForeignKey(
        AppDefinition,
        related_name='volumes',
        on_delete=models.CASCADE,
        help_text="Die App, für die dieses Volume definiert ist."
    )
    host_path = models.CharField(
        max_length=256,
        help_text="Pfad auf dem Host‑Dateisystem."
    )
    container_path = models.CharField(
        max_length=256,
        help_text="Zielpfad im Docker‑Container."
    )

    class Meta:
        unique_together = ('app', 'host_path', 'container_path')
        verbose_name = "App Volume"
        verbose_name_plural = "App Volumes"

    def __str__(self):
        return f"{self.app.name}: {self.host_path} → {self.container_path}"


class ConfigPatch(models.Model):
    """
    Ein einzelner “Patch” für eine Konfigurations‑Datei.

    * target_file    – Pfad relativ zum Home‑Verzeichnis des Deploy‑Users
    * pattern        – regulärer Ausdruck (Python/grep‑syntax)
    * action         – „comment“, „replace“, „delete“
    * replacement    – optional, wird nur für „replace“ benötigt

    Der Patch kann an einer App oder an einem Volume gebunden werden.

    ### Hinweise

    * **`app`** *oder* **`volume`** ist Pflicht – das Modell weiß, wo die Regel gilt.
      Du kannst später beide Felder nutzen, falls eine App mehrere Volumes hat und jedes ein anderes Patch benötigt.
      Wenn du möchtest, dass die Regel immer an die App gebunden ist, lasse `volume` leer und nutze immer `app`.

    * **`pattern`** kann ein einfacher String (`^https:`) oder ein vollwertiger regulärer Ausdruck (`^(https|cert|key):`) sein.
      Wir nutzen `grep -E` bzw. `sed -E` auf dem Server, damit das alles in einer Zeile funktioniert.

    """
    ACTION_COMMENT = 'comment'
    ACTION_REPLACE = 'replace'
    ACTION_DELETE = 'delete'

    ACTION_CHOICES = [
        (ACTION_COMMENT, 'Zeile auskommentieren'),
        (ACTION_REPLACE, 'Zeile ersetzen'),
        (ACTION_DELETE, 'Zeile löschen'),
    ]

    app = models.ForeignKey(
        AppDefinition,
        related_name='config_patches',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    volume = models.ForeignKey(
        AppVolumePerApp,
        related_name='config_patches',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    target_file = models.CharField(
        max_length=256,
        help_text='Pfad relativ zum Home‑Verzeichnis des Deploy‑Users'
    )
    pattern = models.CharField(
        max_length=256,
        help_text='Regulärer Ausdruck, der die zu bearbeitende Zeile identifiziert'
    )
    action = models.CharField(
        max_length=8,
        choices=ACTION_CHOICES,
        default=ACTION_COMMENT
    )
    replacement = models.CharField(
        max_length=512,
        blank=True,
        null=True,
        help_text='Nur für “replace” nötig – der neue Zeilentext (ohne Zeilenumbruch)'
    )

    class Meta:
        verbose_name = "Patch Configuration"
        verbose_name_plural = "Patch Configurations"

    def __str__(self):
        owner = self.app or self.volume
        return f'{owner}: {self.target_file} – {self.action}'