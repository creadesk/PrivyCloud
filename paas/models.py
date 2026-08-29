from __future__ import annotations  # optional, aber sauberer
from typing import Optional
from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import uuid
import docker
import shlex
import paramiko



class AppDefinition(models.Model):
  """Vordefinierte Apps, die bereitgestellt werden können."""

  TOR_AUTH_CHOICES = [
      ('none', _('ohne')),
      ('cert', _('Zertifikat')),
  ]

  name = models.CharField(max_length=64, unique=True)
  display_name = models.CharField(max_length=128)
  # ──────────────────────────────────────────────────────────────
  # Basis‑Image (ohne Tag).  Die eigentliche Tag‑Liste ist in
  # AppImageTag gespeichert.
  # ──────────────────────────────────────────────────────────────
  docker_image = models.CharField(
      max_length=256,
      help_text="Registry + Image‑Name (ohne Tag), z.B. 'docker.io/louislam/uptime‑kuma'.",
  )

  description = models.TextField(blank=True)
  default_duration = models.PositiveIntegerField(default=1)  # Stunden
  app_port_intern_web = models.PositiveIntegerField(default=80)  # Web-Port, den die App innerhalb des Dockercontainers anbietet
  app_port_intern_api = models.PositiveIntegerField(default=1)  # API-Port, den die App innerhalb des Dockercontainers anbietet
  hiddenservice_port_web = models.PositiveIntegerField(default=80)  # Web-Port für onion-service
  hiddenservice_port_api = models.PositiveIntegerField(default=1)  # API-Port für onion-service
  use_deploy_user = models.BooleanField(default=False, help_text="Container mit User {uid}:{gid} starten")
  no_hidden_service = models.BooleanField(default=False, help_text="ohne Hidden-Service")
  use_hostpid_namespace = models.BooleanField(default=False, help_text="Container verwendet den PID-Namespace des Hosts")

  tor_auth_type = models.CharField(
      max_length=8,
      choices=TOR_AUTH_CHOICES,
      default='none',
      help_text=_('Authentisierung für den Tor‑Hidden‑Service.'),
  )

  class Meta:
      ordering = ['display_name']
      verbose_name = "App Definition"
      verbose_name_plural = "App Definitions"

  def __str__(self):
      return self.display_name

  # ----------------------------------------------------
  # Hilfs‑Property – gibt den vollständigen Image‑String zurück
  # ----------------------------------------------------
  def full_docker_image(self, tag: Optional['AppImageTag'] = None) -> str:
      """
      Gibt den kompletten Docker‑Image‑String zurück.

      >>> app = AppDefinition.objects.get(name='uptime-kuma')
      >>> tag = AppImageTag.objects.get(app_definition=app, tag='2.2.1')
      >>> app.full_docker_image(tag)
      'louislam/uptime-kuma:2.2.1'

      Wenn kein Tag übergeben wird, liefert die Methode den ersten
      Tag der App (falls vorhanden).  Das ist praktisch für
      „default‑Tag“‑Logik in Ihrer Applikation.
      """
      # Falls kein Tag übergeben wurde, wähle den ersten (nach
      # der Standard‑Sortierung des RelatedManager) Tag der App.
      if not tag:
          tag = self.image_tags.first()  # `image_tags` ist die Reverse‑FK

      # `tag` ist entweder ein AppImageTag‑Objekt oder None.
      # Für das Tag‑Objekt greifen wir auf das Feld `tag` zu.
      return f"{self.docker_image}:{tag.tag}" if tag else self.docker_image


class AppImageTag(models.Model):
    """
    Das Tag‑Set einer **eigenen** AppDefinition.
    """

    app_definition = models.ForeignKey(
        AppDefinition,
        on_delete=models.CASCADE,
        related_name='image_tags',          # ↔︎ AppDefinition.image_tags
        verbose_name='App Definition',
        default=1,
    )
    tag = models.CharField(max_length=64)

    class Meta:
        verbose_name = "App Image Tag"
        verbose_name_plural = "App Image Tags"
        ordering = ['tag']
        # Ein Tag darf **nicht** mehr als einmal bei einer App vorkommen
        unique_together = ('app_definition', 'tag')
        # (optional)   ──────────────────────
        # Wenn Sie wollen, dass *globale* Unikate existieren
        # (z.B. „latest“ ist bei allen Apps gleich), entfernen Sie
        # die unique‑together‑Constraint.

    def __str__(self) -> str:
        return f"{self.app_definition.display_name} – {self.tag}"


class RemoteHost(models.Model):
    """Liste der Debian‑Hosts, auf denen Container laufen können."""
    hostname        = models.CharField(max_length=128, unique=True)
    ip_address      = models.GenericIPAddressField()
    ssh_user        = models.CharField(max_length=32, default='root')
    ssh_key_path    = models.CharField(max_length=256, null=True, blank=True)

    # ----------   Neues Feld  ------------------------------------
    nur_superuser   = models.BooleanField(
        default=False,
        help_text="Nur Superuser dürfen auf diesem Host deployen."
    )

    # ----------   Optional: last‑Feld  ----------------------------
    current_load = models.FloatField(
        default=0.0,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(10.0),
        ],
        help_text="Aktuelle CPU‑Last des Hosts (0.0 – 10.0)."
    )

    class Meta:
        verbose_name        = "Target-Host"
        verbose_name_plural = "Target-Hosts"

    def __str__(self):
        return f"{self.hostname}"

    # ----------------------------------------------------------------
    #  Hilfs‑Methode, die über SSH die Docker‑Images eines Hosts
    #  zurückliefert.  Für jeden Image‑Eintrag wird zusätzlich
    #  ermittelt, ob das Image gerade in einem laufenden Container
    #  benutzt wird.
    # ----------------------------------------------------------------
    def docker_images(self):
        """
        Rückgabe: Liste von Dictionaries

        [
            {
                'repository': 'myapp',
                'tag': 'v1.0',
                'image_id': '2c6c9d0b9f3a',
                'size': '512MB',
                'used': True,
            },
            ...
        ]
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            # -- 1. SSH‑Verbindung herstellen
            client.connect(
                hostname=self.hostname,
                username=self.ssh_user,
                key_filename=self.ssh_key_path,  # ausschließlich das File benutzen
                timeout=10,
                allow_agent=False,  # keine Agent‑Keys
                look_for_keys=False,  # keine weiteren Keys suchen
            )
        except Exception as exc:
            # Verbindungs‑ oder Authentifizierungs‑Fehler → leere Liste
            client.close()
            return []

        try:
            # --------------------------------------------------------------
            # 2. Welche Images werden gerade verwendet? (docker ps)
            # --------------------------------------------------------------
            used_cmd = r'docker ps --format "{{.Image}}"'
            stdin, stdout, stderr = client.exec_command(used_cmd)
            used_imgs = set(line.strip() for line in stdout.read().decode().splitlines())

            # --------------------------------------------------------------
            # 3. Alle Images holen (docker images)
            # --------------------------------------------------------------
            img_cmd = (
                r'docker images '
                r'--format "{{.Repository}} {{.Tag}} {{.ID}} {{.Size}}"'
            )
            stdin, stdout, stderr = client.exec_command(img_cmd)
            raw = stdout.read().decode()

            images = []
            for line in raw.splitlines():
                if not line.strip():
                    continue
                repo, tag, img_id, size = line.split(maxsplit=3)
                used = f"{repo}:{tag}" in used_imgs
                images.append({
                    'repository': repo,
                    'tag': tag,
                    'image_id': img_id,
                    'size': size,
                    'used': used,
                })
            return images
        finally:
            client.close()

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
  port2 = models.PositiveIntegerField(blank=True, null=True)
  # Status: pending, running, finished, error, deleted
  status = models.CharField(max_length=32, default='pending')
  log = models.TextField(blank=True, null=True)
  onion_address = models.CharField(
      max_length=100, blank=True, null=True,
      help_text="Onion‑Adresse des Tor‑Hidden‑Services (falls erstellt)"
  )
  last_modified = models.DateTimeField(auto_now=True)
  docker_run_cmd = models.CharField(max_length=5000, blank=True, null=True)
  image = models.CharField(max_length=250, blank=True, null=True)

  class Meta:
  #   unique_together = ('user', 'app', 'host')   # keine Duplikate
      verbose_name = "Provisioned App"
      verbose_name_plural = "Provisioned Apps"

  def is_active(self):
      return self.status == 'active' and (self.expires_at is None or self.expires_at > timezone.now())

  def __str__(self):
      return f"{self.user} – {self.app} on {self.host}"

  # --------------------------------------------------------------------
  # Docker‑Client über SSH bauen – nutzt die Daten aus `RemoteHost`
  # --------------------------------------------------------------------
  def _docker_client(self):
      """
      Baut einen Docker‑Client, der über SSH mit dem zugehörigen
        RemoteHost verbindet. Der SDK‑Parameter `use_ssh_client=True`
        übernimmt die SSH‑Authentifizierung; die Standard‑SSH‑Keys des
        Benutzers werden automatisch verwendet. Für eine benutzerdefinierte
        Key‑Datei muss eine entsprechende SSH‑Konfiguration im Host
        (z.B. in ~/.ssh/config) eingerichtet werden.
      """
      if not self.host:
          print(f'_docker_client, Fehler-kein host {self.host}')
          return None

      print(f'_docker_client, Host: {self.host}')

      base_url = f"ssh://{self.host.ssh_user}@{self.host.hostname}"

      print(f'_docker_client, base_url: {base_url}')

      try:
          client = docker.DockerClient(base_url=base_url, use_ssh_client=True)
          return client
      except Exception as e:
          print(f'Verbindung konnte nicht aufgebaut werden – keine Docker‑Operation: {e}')
          return None

  # --------------------------------------------------------------------
  # Aktuellen Container‑Status abfragen und in `self.status` schreiben
  # --------------------------------------------------------------------
  def refresh_status(self):
      """
      Liest den aktuellen Status des zugehörigen Docker‑Containers
      (falls vorhanden) und aktualisiert `self.status`. Die Methode
      **speichert nicht** – das wird im View‑Code erledigt.
      """
      if not self.container_id:
          print('container - refresh status: Kein Container – Status unverändert lassen')
          return

      client = None
      try:
          client = self._docker_client()
          if client is None:
              print('container - refresh status: Fehler-keine Verbindung möglich')
              return

          container = client.containers.get(self.container_id)
          docker_status = container.status  # z.B. 'running', 'exited', 'dead'

          print(f'container - refresh status, container: {container}')
          print(f'container - refresh status, container status: {docker_status}')

          # Mapping zu unserem internen Status‑Wert
          if docker_status == 'running':
              new_status = 'running'
          elif docker_status in ('exited', 'dead', 'created'):
              new_status = 'stopped'
          else:
              new_status = docker_status

          # Nur bei Änderung aktualisieren
          if new_status != self.status:
              self.status = new_status

      except docker.errors.NotFound:
          # Container existiert nicht mehr → als gelöscht kennzeichnen
          self.status = 'deleted'
      except Exception:
          # Alle anderen Fehler → Fehlerstatus setzen
          self.status = 'error'
      finally:
          if client:
              client.close()


  # ----------------------------------------------------------------------
  # Starten des Containers
  # ----------------------------------------------------------------------
  def start_container(self):
      """
      Startet den bereits vorhandenen Container (falls nicht bereits laufen).
      """
      if not self.container_id:
          print("start_container: Kein Container‑ID hinterlegt – nichts zu starten")
          self.status = 'error'
          return False

      client = None
      try:
          client = self._docker_client()
          if client is None:
              print("start_container: keine Docker‑Verbindung")
              self.status = 'error'
              return False

          container = client.containers.get(self.container_id)

          if container.status == 'running':
              print("start_container: Container läuft bereits – keine Aktion nötig")
              self.status = 'running'
              return True

          # Container starten
          print(f"start_container: Starte Container {self.container_id}")
          container.start()
          # Warte, bis Docker das neue State meldet
          #container.wait(condition='running')

          self.started_at = timezone.now()
          self.status = 'running'
          self.log += f"\nContainer {self.container_id} gestartet."
          return True

      except docker.errors.NotFound:
          print("start_container: Container nicht gefunden → als gelöscht markieren")
          self.status = 'deleted'
          return False
      except docker.errors.APIError as exc:
          print(f"start_container: Docker API‑Fehler – {exc}")
          self.log = f"API error: {exc}"
          self.status = 'error'
          return False
      except Exception as exc:
          print(f"start_container: unerwarteter Fehler – {exc}")
          self.log = f"Unexpected error: {exc}"
          self.status = 'error'
          return False
      finally:
          if client:
              client.close()

  # ----------------------------------------------------------------------
  # Stoppen des Containers
  # ----------------------------------------------------------------------
  def stop_container(self, timeout=30):
      """
      Stoppt den laufenden Container.
      """
      if not self.container_id:
          print("stop_container: Kein Container‑ID hinterlegt – nichts zu stoppen")
          self.status = 'stopped'
          return False

      client = None
      try:
          client = self._docker_client()
          if client is None:
              print("stop_container: keine Docker‑Verbindung")
              self.status = 'error'
              return False

          container = client.containers.get(self.container_id)

          if container.status != 'running' and container.status != 'stopping':
              print(f"stop_container: Container läuft nicht (status={container.status})")
              self.status = 'stopped'
              return True

          print(f"stop_container: Stoppe Container {self.container_id}")
          container.stop(timeout=timeout)
          # Optional: warten bis Docker `exited` meldet
          #container.wait(condition='exited')

          self.status = 'stopped'
          self.log += f"\nContainer {self.container_id} gestoppt."
          return True

      except docker.errors.NotFound:
          print("stop_container: Container nicht gefunden → als gelöscht markieren")
          self.status = 'deleted'
          return False
      except docker.errors.APIError as exc:
          print(f"stop_container: Docker API‑Fehler – {exc}")
          self.log = f"API error: {exc}"
          self.status = 'error'
          return False
      except Exception as exc:
          print(f"stop_container: unerwarteter Fehler – {exc}")
          self.log = f"Unexpected error: {exc}"
          self.status = 'error'
          return False
      finally:
          if client:
              client.close()



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
    ACTION_ADD = 'add'

    ACTION_CHOICES = [
        (ACTION_COMMENT, 'Zeile auskommentieren'),
        (ACTION_REPLACE, 'Zeile ersetzen'),
        (ACTION_DELETE, 'Zeile löschen'),
        (ACTION_ADD, 'Zeile hinzufügen'),
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
        help_text='Nur für “replace” und "add" nötig – der neue Zeilentext (ohne Zeilenumbruch)'
    )

    class Meta:
        verbose_name = "Patch Configuration"
        verbose_name_plural = "Patch Configurations"

    def __str__(self):
        owner = self.app or self.volume
        return f'{owner}: {self.target_file} – {self.action}'