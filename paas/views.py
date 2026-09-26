import os

import paramiko
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from django.utils.dateparse import parse_duration
from django.views.decorators.http import require_http_methods, require_POST

from .models import ProvisionedApp, RemoteHost, AppDefinition, AppEnvVarPerApp, AppImageTag
from .forms import DeployForm, DeployFormAdmin
from .strategies import LeastLoadStrategy
from .tasks import deploy_app_task, delete_container_task, update_app_task, _gen_x25519_keypair, _b32_encode, \
    _ssh_client, _run_cmd
from core.settings import PLATFORM_NAME, USER_RATELIMIT_PER_HOUR, STRING_TO_ADMIN_PATH
from django_smart_ratelimit import rate_limit
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, HttpResponseNotFound, HttpResponseServerError, \
    HttpResponseBadRequest
from django.contrib import messages
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization
from django.http import HttpResponse, HttpResponseForbidden
import re
from typing import Tuple, Dict, List



def _check_user_limits(user, requested_duration, request):
    # Skip check für superuser
    if request.user.is_superuser:
        return True
    """
    Prüft:
    1. Max. gleichzeitige Apps
    2. Max. gesamt Stunden pro Tag
    3. Max. Dauer pro einzelne Bereitstellung
    """

    requested_duration_dt = parse_duration(requested_duration)

    # 1) gleichzeitige Apps
    active_apps = ProvisionedApp.objects.filter(user=user, status='active')
    if active_apps.count() >= user.deployment_limit.max_concurrent_apps:
        return False

    # 2) gesamt Stunden pro Tag (heute)
    today = timezone.now().date()
    today_total = 0
    for p in active_apps:
        if p.expires_at and p.expires_at.date() == today:
            today_total += (p.expires_at - timezone.now()).total_seconds() / 3600

    # add requested
    if requested_duration is not None:
        today_total += requested_duration_dt.total_seconds() / 3600

    if today_total > user.deployment_limit.max_total_hours_per_day:
        return False

    # 3) max Dauer pro Bereitstellung
    max_dur = user.deployment_limit.max_duration
    if max_dur and requested_duration_dt and requested_duration_dt > max_dur:
        return False

    return True


@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def select_app(request):
  # ──────────────────────── Formular‑Klasse bestimmen ─────────────────────
  FormCls = DeployFormAdmin if request.user.is_superuser else DeployForm

  if request.method == 'POST':

      form = FormCls(request.POST)

      if form.is_valid():

          # 1) Daten aus dem Formular holen
          app_def = form.cleaned_data['app']
          duration = form.cleaned_data['duration']  # timedelta oder None

          # 2) Limits prüfen
          if not _check_user_limits(request.user, duration, request):
              # Rückmeldung an den User
              return render(request, 'paas/select_app.html', {
                  'error': _('Ihre Limits wurden überschritten.'),
                  'form': form,
              })

          # Neue Form‑Instanz mit der ausgewählten App initialisieren
          # (so bleibt die Auswahl sichtbar)
          init_form = FormCls(initial={'app': app_def})

          return render(request, 'paas/deploy_app.html', {
              'form': init_form,
              "PLATFORM_NAME": PLATFORM_NAME,
              'tor_auth_type': app_def.tor_auth_type,
          })
      else:
            form = FormCls()
  else:
    form = FormCls()

  return render(request, 'paas/select_app.html', {
      'form': form,
      "PLATFORM_NAME": PLATFORM_NAME,
  })


@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def deploy_app(request):
    # ──────────────────────── Formular‑Klasse bestimmen ─────────────────────
    FormCls = DeployFormAdmin if request.user.is_superuser else DeployForm

    """
    Deploy‑View mit sauberer Aufteilung in Helfer‑Funktionen.
    """
    if request.method == 'POST':

        do_it = 'doIT' in request.POST  # Hidden‑Field
        form = FormCls(request.POST)

        if not do_it:
            if form.is_valid():
                """Verarbeitet ein gültiges Formular und führt das Deploy noch nicht aus."""
                app_def = form.cleaned_data.get('app')
                duration = form.cleaned_data['duration']  # timedelta oder None
                target_host = form.cleaned_data.get('target_host')

                # nur editierbare Umgeb.Variablen an den Client schicken
                app_env_vars = AppEnvVarPerApp.objects.filter(app=app_def, editable=True) if app_def else []

                # Alle kompletten Image‑Strings für die gewählte App generieren
                tags_qs = app_def.image_tags.all().order_by('-tag')
                full_images = [app_def.full_docker_image(tag) for tag in tags_qs]

                # Nur die Vorschau anzeigen – kein Deploy
                context = {
                    'app_selected': app_def,
                    'duration_selected': duration,
                    'target_host_selected': target_host,
                    'app_description': app_def.description,
                    'readonly': True,
                    'app_env_vars': app_env_vars,
                    'images': full_images,
                    'tor_auth_type': app_def.tor_auth_type,
                    "PLATFORM_NAME": PLATFORM_NAME,
                }
                return render(request, 'paas/deploy_app.html', context)
            else:
                # Form had errors; they'll be displayed in the template
                pass
            return render(request, 'paas/select_app.html', {
                'form': form,
                "PLATFORM_NAME": PLATFORM_NAME,
            })
        else:
            return _handle_deploy(request,form)



# ----------------------------------------------------------------------
# Helper‑Funktionen für deploy_app
# ----------------------------------------------------------------------
def _handle_deploy(request, form):

    app_selected = request.POST.get('app_selected')
    print(app_selected)

    # Das ausgewählte Image holen
    selected_image = request.POST.get('image_choice')

    try:
        app_def = AppDefinition.objects.get(name=app_selected)
    except AppDefinition.DoesNotExist:
        app_def = None  # oder andere Fehlerbehandlung

    # Alle gültigen Image‑Strings der App erzeugen
    valid_images = {
        app_def.full_docker_image(tag)
        for tag in app_def.image_tags.all()
    }

    # 4. Prüfen, ob das ausgewählte Image gültig ist
    if selected_image not in valid_images:
        return render_deploy(
            request,
            error=("Das ausgewählte Docker‑Image ist nicht gültig."),
            app_def=app_def,
        )

    # --- alles OK mit der image Auswahl--------------------------------------------------

    duration = request.POST.get('duration_selected', 1)

    target_host_selected = request.POST.get('target_host_selected')

    try:
        target_host = RemoteHost.objects.get(hostname=target_host_selected)
    except RemoteHost.DoesNotExist:
        target_host = None

    # ---------- Host‑Restriktion prüfen --------------------
    if target_host and target_host.nur_superuser and not request.user.is_superuser:
        # Normal‑Users dürfen diesen Host NICHT auswählen.
        # Wir geben eine klare Fehlermeldung zurück – keine neue Provision wird angelegt.
        return render_deploy(
            request,
            error=("Der ausgewählte Host ist ausschließlich für Super‑Users "
                   "reserviert. Bitte wähle einen anderen Host."),
            app_def=app_def,
        )

    # 1) Editierbare Environment‑Variablen extrahieren
    env_vars = {
        k[4:]: v  # key beginnt mit „env_“
        for k, v in request.POST.items()
        if k.startswith('env_')
    }

    # 1.1) Nicht‑editierbare Umgebungsvariablen ergänzen
    non_editable_qs = AppEnvVarPerApp.objects.filter(app=app_def, editable=False)

    for var in non_editable_qs:
        # Falls die Variable bereits als editierbar vorkommt, behalten wir
        # den editierbaren Wert (setdefault tut genau das)
        env_vars.setdefault(var.key, var.value)

    # 2) Validierung der Env‑Variablen
    errors = _validate_env_vars(app_def, env_vars)
    if errors:
        return render_deploy(
            request, error=' '.join(errors), app_def=app_def
        )


    # 3) Zielhost wählen (Wahlfreiheit für superuser + entsprechende Auswahlstrategie (strategies.py) für normale user)
    #host = target_host if request.user.is_superuser else RemoteHost.objects.first()
    if request.user.is_superuser:
        host = target_host  # Super‑User wählt aus der UI
    else:
        # Normal‑User – Strategie
        strategy = LeastLoadStrategy()  # ggf. RoundRobinStrategy()
        host = strategy.select_target(request, request.user,
                                      RemoteHost.objects.all())

    # 4) Limits prüfen
    if not _check_user_limits(request.user, duration, request):
        # Rückmeldung an den User
        return render_deploy(
            request, error=' '.join("Ihre Limits wurden überschritten."), app_def=app_def
        )

    # 5) expires_at berechnen
    expires_at = None
    print('duration:')
    print(duration)
    if duration is not None and duration != 'None':
        duration_delta = parse_duration(duration)
        expires_at = timezone.now() + duration_delta

    # 6) Authentisierung
    #tor_auth_type = request.POST.get('tor_auth_type', 'none')
    tor_auth_type = app_def.tor_auth_type
    tor_auth_value = None

    tor_pub_b32 = None
    tor_priv_b32 = None
    if tor_auth_type == "cert":
        priv_bytes, pub_bytes = _gen_x25519_keypair()
        tor_priv_b32 = _b32_encode(priv_bytes)
        tor_pub_b32 = _b32_encode(pub_bytes)
        # Der private Key wird nicht in die DB geschrieben – nur im Kontext weitergegeben

    # 7) Provision‑Objekt erzeugen
    print(request.user)
    print(app_def)
    print(host)
    print(expires_at)
    provision = ProvisionedApp.objects.create(
        user=request.user,
        app=app_def,
        host=host,
        expires_at=expires_at,
        status='pending',
    )

    # 8) Deploy‑Task starten
    deploy_app_task(provision.id, selected_image, env_vars, tor_auth_type=tor_auth_type, tor_pub_key=tor_pub_b32,)
    provision.refresh_from_db()

    # 9) Erfolgspage
    return render(request, 'paas/deploy_success.html', {
        'provision': provision,
        'app_env_vars': env_vars,
        "PLATFORM_NAME": PLATFORM_NAME,
        'tor_private_key': tor_priv_b32,
    })

def _validate_env_vars(app, env_vars):
    """
    Prüft die übergebenen Env‑Variablen gegen die DB‑Definitionen.
    Gibt eine Liste von Fehlermeldungen zurück.
    """
    qs = AppEnvVarPerApp.objects.filter(app=app)
    defined_keys = set(qs.values_list('key', flat=True))
    required_keys = set(qs.filter(optional=False).values_list('key', flat=True))

    errors = []

    # a) Undefinierte Variablen
    for key in env_vars.keys() - defined_keys:
        errors.append(f"Undefinierte Umgebungsvariable '{key}'.")

    # b) Fehlende erforderliche Variablen
    for key in required_keys - env_vars.keys():
        errors.append(f"Erforderliche Umgebungsvariable '{key}' fehlt.")

    # c) Leere Werte für erforderliche Variablen
    for key in required_keys & env_vars.keys():
        if not env_vars[key].strip():
            errors.append(f"Erforderliche Umgebungsvariable '{key}' ist leer.")

    return errors

def render_deploy(request, error=None, app_def=None):
    """Render‑Wrapper für die Deploy‑Template‑Seite."""
    context = {
        'readonly': True,
        'app_env_vars': AppEnvVarPerApp.objects.filter(app=app_def) if app_def else [],
        "PLATFORM_NAME": PLATFORM_NAME,
    }
    if error:
        context['error'] = error
    return render(request, 'paas/deploy_app.html', context)



@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def deploy_success(request, pk):
  provision = ProvisionedApp.objects.get(pk=pk, user=request.user)
  # Der Task hat eventuell ein Attribut `._tor_private_key` angehängt.
  private_key = getattr(provision, "_tor_private_key", None)
  return render(request, 'paas/deploy_success.html', {
      'provision': provision,
      "PLATFORM_NAME": PLATFORM_NAME,
      'private_key': private_key,  # None, falls keine Zertifikat‑Auth
  })

@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def my_apps(request):
  """
  Zeigt die Apps des Benutzers an. Vor dem Rendern wird für jede
  ProvisionedApp der aktuelle Container‑Status abgefragt und in die
  Datenbank geschrieben, sodass die Vorlage immer den aktuellen Zustand
  anzeigt. Außerdem wird die Liste der für jede Provision verfügbaren
  Images aufgebaut, damit die Front‑End‑Tabelle diese direkt ausgeben kann.
  """
  provisions = ProvisionedApp.objects.filter(user=request.user).order_by('-started_at')

  # Status aktualisieren
  for p in provisions:
      p.refresh_status()
      # Nur dann speichern, wenn sich etwas geändert hat
      p.save(update_fields=['status'])

      # ------------------------------------------------------------------
      # Berechne die verfügbaren Images: <registry>/<user>/<imagename>:<tag>
      # ------------------------------------------------------------------
      # 1. Docker‑Image ohne Tag (z.B. „docker.io/louislam/uptime-kuma“)
      base_img = p.app.docker_image
      # 2. Alle Tags der App durchlaufen
      tags = p.app.image_tags.all()
      # 3. Für jeden Tag einen vollständigen Image‑String erzeugen
      p.available_images = [
          f"{base_img}:{t.tag}"
          for t in tags
      ]
      print(p.status)

  return render(request, 'paas/my_apps.html', {
      'provisions': provisions,
      "PLATFORM_NAME": PLATFORM_NAME,
      "STRING_TO_ADMIN_PATH": STRING_TO_ADMIN_PATH,
  })

@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def delete_app(request, pk):
    """
    2‑Schritt‑Delete: Erstes POST → Bestätigungsseite, zweites POST → Löschen
    """
    provision = get_object_or_404(ProvisionedApp, pk=pk, user=request.user)
    provisions = ProvisionedApp.objects.filter(user=request.user).order_by('-started_at')

    print(provision.status)

    # App darf nur laufen oder gelöscht werden
    if provision.status not in ('running', 'deleting', 'stopped', 'error', 'restarting'):
        # Nicht‑zulässige App – einfach weiterleiten
        return render(request, 'paas/my_apps.html', {
            'provisions': provisions,
            "PLATFORM_NAME": PLATFORM_NAME,
        })

    # ---------- 1. Schritt – Bestätigungsseite ----------
    if request.method == 'POST' and 'confirmed' not in request.POST:
        # Der erste POST (ohne Flag) bedeutet: Zeige Bestätigungsseite
        return render(request, 'paas/confirm_delete_app.html',{
            'provision': provision,
            "PLATFORM_NAME": PLATFORM_NAME,
        })

    # ---------- 2. Schritt – Löschen ----------
    if request.method == 'POST' and 'confirmed' in request.POST:
        # Sicherheits‑Check: der Benutzer muss wieder die App besitzen
        if provision.status not in ('running', 'deleting', 'stopped', 'error'):
            return render(request, 'paas/my_apps.html', {
                'provisions': provisions,
                "PLATFORM_NAME": PLATFORM_NAME,
            })

        provision.status = 'deleting'
        provision.save()

        # Synchron‑Delete
        delete_container_task(provision.id)

        # Nach erfolgreichem Löschen Weiterleitung
        return render(request, 'paas/my_apps.html', {
            'provisions': provisions,
            "PLATFORM_NAME": PLATFORM_NAME,
        })

    # Für jede andere Methode (z.B. GET) leiten wir einfach weiter
    return render(request, 'paas/my_apps.html', {
        'provisions': provisions,
        "PLATFORM_NAME": PLATFORM_NAME,
    })


@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def stop_app(request, pk):
    provision = get_object_or_404(ProvisionedApp, pk=pk, user=request.user)
    provisions = ProvisionedApp.objects.filter(user=request.user).order_by('-started_at')

    # ---------- Stoppen ----------
    if request.method == 'POST':
        # Sicherheits‑Check: der Benutzer muss wieder die App besitzen
        if provision.status not in ('running'):
            return redirect("paas_my_apps")

        provision.status = 'stopping'
        provision.save()

        provision.stop_container()

        # Nach erfolgreichem stoppen Weiterleitung
        return redirect("paas_my_apps")

    # Für jede andere Methode (z.B. GET) leiten wir einfach weiter
    return redirect("paas_my_apps")


@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def start_app(request, pk):
    provision = get_object_or_404(ProvisionedApp, pk=pk, user=request.user)
    provisions = ProvisionedApp.objects.filter(user=request.user).order_by('-started_at')

    # ---------- Stoppen ----------
    if request.method == 'POST':
        # Sicherheits‑Check: der Benutzer muss wieder die App besitzen
        if provision.status not in ('stopped'):
            return redirect("paas_my_apps")

        provision.status = 'starting'
        provision.save()

        provision.start_container()

        # Nach erfolgreichem stoppen Weiterleitung
        return redirect("paas_my_apps")

    # Für jede andere Methode (z.B. GET) leiten wir einfach weiter
    return redirect("paas_my_apps")



@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def update_provisioned_app(request):
    """
    POST‑Request, um einen laufenden Container sofort auf ein neues
    Docker‑Image zu aktualisieren.
    Dabei wird geprüft, dass die `ProvisionedApp` dem angemeldeten User
    gehört.  Falls die ID nicht existiert oder der User nicht der Besitzer
    ist, wird ein 404 (oder einfach Redirect) zurückgegeben.
    """

    if request.method != 'POST':
        return redirect('paas_my_apps')

    # POST‑Daten holen & validieren
    try:
        provision_id = int(request.POST['provision_id'])
    except (KeyError, ValueError):
        print("Ungültige Provision‑ID.")
        return redirect('paas_my_apps')

    new_image = request.POST.get('new_image')
    if not new_image:
        print("Das neue Image wurde nicht angegeben.")
        return redirect('paas_my_apps')

    # ------------------------------------------------------------------
    # Validierung von new_image
    # ------------------------------------------------------------------
    # Syntax prüfen: <image>:<tag>
    if ':' not in new_image:
        print("Image muss im Format <image>:<tag> sein.")
        return redirect('paas_my_apps')

    image_part, tag_part = new_image.split(':', 1)  # nur das erste ':' teilen

    # Prüfen, ob das Image in AppDefinition existiert
    try:
        app_def = AppDefinition.objects.get(docker_image=image_part)
    except AppDefinition.DoesNotExist:
        print(f"Image '{image_part}' ist nicht in den Referenzdaten vorhanden.")
        return redirect('paas_my_apps')

    # Prüfen, ob das Tag zu diesem Image gehört
    if not AppImageTag.objects.filter(app_definition=app_def, tag=tag_part).exists():
        print(f"Tag '{tag_part}' für Image '{image_part}' existiert nicht.")
        return redirect('paas_my_apps')

    # Sicherstellen, dass die Provision zu diesem User gehört
    provision = get_object_or_404(
        ProvisionedApp,
        pk=provision_id,
        user=request.user,          # ←  Eigentümerschaft prüfen
    )

    # Task synchron ausführen
    #    (Kein .delay() – die Task läuft im aktuellen Prozess)
    update_app_task(provision_id, new_image)

    # 4. Weiterleitung
    print(f"Provision '{provision.container_name}' wird auf {new_image} aktualisiert.")
    return redirect('paas_my_apps')



@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def images(request):
    """
    Liefert alle heruntergeladenen images pro host.
    Dies darf nur der Superuser.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("Keine Berechtigung.")
        # oder still ablehnen:
        # return redirect('dashboard')

    # Alle Hosts holen
    hosts = RemoteHost.objects.all()

    # Für jeden Host die Images abfragen
    host_images = []
    for host in hosts:
        host_images.append({
            'host': host,
            'images': host.docker_images(),  # ← Hilfsmethode aus dem Modell
        })

    return render(request, 'paas/images.html', {
      'images': host_images,
      "PLATFORM_NAME": PLATFORM_NAME,
    })

@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def delete_image(request, host_id, image_id):
    """
    Löscht ein Docker‑Image, das auf dem Host **nicht** mehr verwendet wird.
    Nur Superuser können dies tun.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("Keine Berechtigung.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # Host holen
    host = get_object_or_404(RemoteHost, pk=host_id)

    # Image‑Existenz & Status prüfen
    images = host.docker_images()                     # ← Model‑Helper
    image = next((img for img in images if img['image_id'] == image_id), None)

    if image is None:
        messages.error(request, f"Image {image_id} nicht gefunden auf Host {host}.")
        return redirect('paas_images')

    if image['used']:
        messages.error(request, f"Image {image_id} ist im Einsatz auf Host {host}.")
        return redirect('paas_images')

    # SSH‑Verbindung & Docker‑Remove
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host.hostname,
            username=host.ssh_user,
            key_filename=host.ssh_key_path,  # ausschließlich das File nutzen
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        cmd = f'docker rmi {image_id}'
        _, stdout, stderr = client.exec_command(cmd)

        err = stderr.read().decode().strip()
        if err:
            messages.error(request, f"Löschen fehlgeschlagen auf Host {host}: {err}")
        else:
            messages.success(request, f"Image {image_id} erfolgreich gelöscht auf Host {host}.")

    except Exception as exc:
        messages.error(request, f"SSH error on {host}: {exc}")

    finally:
        client.close()

    return redirect('paas_images')



@login_required
@user_passes_test(lambda u: u.is_superuser)
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def docker_logs(request, provision_id: int) -> HttpResponse:
    """
    Zeigt die letzten <lines> Zeilen des Docker‑Logs des Container‑IDs,
    der in der ProvisionedApp hinterlegt ist.
    Nur Super‑User haben Zugriff.
    """
    provision = get_object_or_404(ProvisionedApp, id=provision_id)

    # ------------------------------------------------------------------
    # Parameter verarbeiten
    # ------------------------------------------------------------------
    lines_param = request.GET.get('lines', None)
    try:
        tail = int(lines_param) if lines_param else None
        if tail is not None and tail <= 0:
            raise ValueError
    except ValueError:
        # Fallback zu 100 Zeilen, falls ungültiger Wert eingegeben wurde
        tail = 100

    # ------------------------------------------------------------------
    # Docker‑Client über SSH initialisieren
    # ------------------------------------------------------------------
    client = provision._docker_client()
    if client is None:
        return HttpResponse(
            'Docker‑Client konnte nicht initialisiert werden.',
            status=500,
            content_type='text/plain',
        )

    # ------------------------------------------------------------------
    # Logs abrufen
    # ------------------------------------------------------------------
    if not provision.container_id:
        logs_text = 'Kein Container‑ID vorhanden.'
    else:
        try:
            container = client.containers.get(provision.container_id)
            # stream=False liefert das komplette Log‑Blob
            logs_bytes = container.logs(tail=tail, stream=False)
            logs_text = logs_bytes.decode('utf-8', errors='replace')
        except Exception as exc:
            logs_text = f'Fehler beim Auslesen der Logs: {exc}'

    # ------------------------------------------------------------------
    # Ausgabe als plain‑text (im Browser als neuer Tab)
    # ------------------------------------------------------------------
    response = HttpResponse(
        logs_text,
        content_type='text/plain; charset=utf-8',
    )
    response['Content-Disposition'] = (
        f'inline; filename="docker-logs-{provision.id}.txt"'
    )
    return response


# ---------------------------------
# Utility: sichere Pfad‑Validierung
# ---------------------------------
_LOG_PATH_RE = re.compile(r'^/(?!.*\.\./).*')   # absolut, keine '..'

def _validate_log_path(path: str) -> bool:
    return bool(path) and bool(_LOG_PATH_RE.match(path))

def _run_cmd_bytes(ssh, command: str) -> Tuple[int, bytes, bytes]:
    """
    Führt einen Befehl auf dem Remote‑Host aus und gibt (exit_code, stdout, stderr)
    zurück – **stdout / stderr sind bytes**.
    """
    stdin, stdout_f, stderr_f = ssh.exec_command(command)
    # read() liefert bytes
    stdout_data = stdout_f.read()
    stderr_data = stderr_f.read()
    exit_status = stdout_f.channel.recv_exit_status()
    return exit_status, stdout_data, stderr_data

# ---------------------------------
# Applikations‑Logs
# ---------------------------------
@login_required
@user_passes_test(lambda u: u.is_superuser)
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def application_logs(request, pk):
    provision = get_object_or_404(ProvisionedApp, pk=pk)

    log_path = provision.application_log_path
    if not _validate_log_path(log_path):
        return HttpResponseNotFound(
            "Kein gültiger Log‑Pfad für diese App definiert."
        )

    # Zeilenanzahl holen
    try:
        lines = int(request.GET.get('lines', '200'))
        if lines <= 0:
            raise ValueError
    except ValueError:
        lines = 200

    # 1. Prüfen, ob die Datei im Container existiert (ls‑Check)
    cmd_check = f"docker exec {provision.container_id} test -f {log_path}"
    try:
        with _ssh_client(provision.host) as ssh:
            exit_code, _, _ = _run_cmd_bytes(ssh, cmd_check)
    except Exception as exc:
        return HttpResponseNotFound(f"Fehler beim Prüfen des Log‑Pfads: {exc}")

    if exit_code != 0:
        return HttpResponseNotFound(
            f"Log‑Datei `{log_path}` existiert nicht im Container."
        )

    # 2. Tail‑Befehl ausführen
    cmd_tail = (
        f"docker exec {provision.container_id} sh -c "
        f"\"tail -n {lines} {log_path}\""
    )

    try:
        with _ssh_client(provision.host) as ssh:
            exit_code, stdout, stderr = _run_cmd(ssh, cmd_tail)
    except Exception as exc:
        return HttpResponseNotFound(f"Fehler beim Auslesen des Log‑Pfads: {exc}")

    if exit_code != 0:
        # Falls tail>0 (z.B. Datei leer? – dann exit‑code 0, aber keine Ausgabe)
        # Wir geben trotzdem die Fehlermeldung aus.
        err_msg = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else stderr
        return HttpResponseNotFound(
            f"Fehler beim Auslesen des Logs: {err_msg}"
        )

    # 3. Bytes / str typ‑sicher ausgeben
    if isinstance(stdout, bytes):
        content = stdout.decode('utf-8', errors='replace')
    else:                     # falls stdout bereits ein str
        content = stdout

    return HttpResponse(content, content_type='text/plain')



def _list_remote_dirs(ssh, home_dir: str) -> List[str]:
    """
    Return a list of *non‑hidden* directory names that are present in the
    given *home_dir* on the remote host.
    Hidden directories (starting with '.') are skipped.
    """
    cmd = f'find {home_dir} -mindepth 1 -maxdepth 1 -type d -printf "%f\n"'
    exit_code, out, err = _run_cmd(ssh, cmd)
    if exit_code != 0:
        # In case of error (permission, timeout, …) we return an empty list
        return []

    # `out` is a string containing one directory name per line.
    dirs = [d for d in out.splitlines() if d and not d.startswith('.')]
    return dirs



@login_required
@user_passes_test(lambda u: u.is_superuser)
@require_http_methods(["GET", "POST"])
def data_corpses(request):
    """
    Show a list of directories in the *deploy user* home directory of
    each RemoteHost that have no corresponding ProvisionedApp entry.
    """
    # Map of host → list of corpse directory names
    corpses_by_host: Dict[RemoteHost, List[str]] = {}
    hosts_all = RemoteHost.objects.all()
    for host in hosts_all:
        try:
            # --- connect to the host ------------------------------------
            with _ssh_client(host) as ssh:
                # determine the absolute path of the deploy user’s home
                # → either /home/<ssh_user> or /root for the root user
                home_dir = f"/home/{host.ssh_user}" if host.ssh_user != "root" else "/root"

                # --- list all sub‑directories of that home directory ----
                dirs = _list_remote_dirs(ssh, home_dir)

            # --- filter out the directories that are already known -------
            unknown_dirs = []
            for dirname in dirs:
                # A ProvisionedApp entry exists if it points to this host
                # *and* its container_name equals the directory name
                if not ProvisionedApp.objects.filter(
                    host=host, container_name=dirname
                ).exists():
                    unknown_dirs.append(dirname)

            if unknown_dirs:
                corpses_by_host[host] = unknown_dirs

        except Exception as exc:            # pragma: no cover – SSH errors
            # Log the exception – in production you would use logging
            # logging.exception("Error while inspecting host %s: %s", host, exc)
            # Continue with the next host – a single host failure
            # should not break the whole view.
            print("Error while inspecting host %s: %s", host, exc)
            continue

    context = {
        "corpses_by_host": corpses_by_host,
        "PLATFORM_NAME": PLATFORM_NAME,
        "hosts_all": hosts_all,
    }

    return render(request, "paas/data_corpses.html", context)



@login_required
@user_passes_test(lambda u: u.is_superuser)
@require_POST
def mark_delete(request):
    """
    Rename a directory on the target host by prefixing it with
    ``delete_me_``.  After the operation the user is redirected back
    to the data‑corpses page.
    """
    host_id = request.POST.get("host_id")
    dirname = request.POST.get("dirname")

    # Basic validation – nothing is allowed to contain a slash or be empty
    if not host_id or not dirname or "/" in dirname or ".." in dirname:
        messages.error(request, "Ungültige Eingabe.")
        return redirect(reverse("paas_data_corpses"))

    host = get_object_or_404(RemoteHost, pk=host_id)

    # Der Ziel‑Pfad
    home_dir = f"/home/{host.ssh_user}" if host.ssh_user != "root" else "/root"
    old_path = os.path.join(home_dir, dirname)
    new_name = f"delete_me_{dirname}"
    new_path = os.path.join(home_dir, new_name)

    # SSH‑Rename
    try:
        with _ssh_client(host) as ssh:
            # Prüfen, ob die Ziel‑Datei bereits existiert
            check_cmd = f'test -e {new_path}'
            exit_code, _, _ = _run_cmd(ssh, check_cmd)
            if exit_code == 0:
                messages.warning(
                    request,
                    f"'{new_name}' existiert bereits – Rename‑Vorgang abgebrochen.",
                )
                return redirect(reverse("paas_data_corpses"))

            # Rename durchführen
            rename_cmd = f'mv "{old_path}" "{new_path}"'
            exit_code, _, err = _run_cmd(ssh, rename_cmd)
            if exit_code != 0:
                messages.error(
                    request,
                    f"Fehler beim Umbenennen von '{dirname}': {err or 'unbekannter Fehler'}",
                )
                return redirect(reverse("paas_data_corpses"))

    except Exception as exc:        # pragma: no cover – Netzwerk‑/SSH‑Fehler
        # In einer Produktionsumgebung würde man hier logging.exception(...)
        messages.error(request, f"SSH‑Fehler: {exc}")
        return redirect(reverse("paas_data_corpses"))

    messages.success(request, f"'{dirname}' wurde zu '{new_name}' umbenannt und zur Löschung vorgemerkt.")
    return redirect(reverse("paas_data_corpses"))



@login_required
@user_passes_test(lambda u: u.is_superuser)
@require_http_methods(["GET"])
def paas_log_cleanup(request, host_id):
    """Return the cleanup log of a specific RemoteHost."""
    # Only super‑admins may use this endpoint
    if not request.user.is_superuser:
        raise PermissionDenied("Nur Superadmins dürfen Logs einsehen.")

    host = get_object_or_404(RemoteHost, pk=host_id)

    # Build absolute path – it is already safe because we use the ssh_user
    log_path = f"/home/{host.ssh_user}/cleanup_data_corpses.log"

    if not _validate_log_path(log_path):
        return HttpResponseBadRequest("Ungültiger Log‑Pfad.")

    # Pull the file via SSH
    try:
        with _ssh_client(host) as ssh:
            exit_code, stdout, stderr = _run_cmd_bytes(
                ssh, f"cat {log_path}"
            )
    except Exception as exc:
        # Log the exception in real projects – here we keep it simple
        return HttpResponseServerError(
            f"SSH‑Verbindung fehlgeschlagen: {exc}"
        )

    if exit_code != 0:
        # Provide the stderr content – useful for “file not found”
        err_text = stderr.decode("utf‑8", errors="replace")
        return HttpResponseServerError(
            f"Fehler beim Lesen der Logdatei:\n{err_text}"
        )

    # Successful read – stream the file to the browser
    response = HttpResponse(
        stdout, content_type="text/plain"
    )
    # Let the browser display it inline; a descriptive filename is handy
    response["Content-Disposition"] = (
        f'inline; filename="cleanup_data_corpses_{host.hostname}.log"'
    )
    return response