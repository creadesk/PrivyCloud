from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse

from django.utils.dateparse import parse_duration
from .models import ProvisionedApp, RemoteHost, AppDefinition, AppEnvVarPerApp
from .forms import DeployForm, DeployFormAdmin
from .tasks import deploy_app_task, delete_container_task
from core.settings import PLATFORM_NAME, USER_RATELIMIT_PER_HOUR
from django_smart_ratelimit import rate_limit


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
        today_total += requested_duration.total_seconds() / 3600

    if today_total > user.deployment_limit.max_total_hours_per_day:
        return False

    # 3) max Dauer pro Bereitstellung
    max_dur = user.deployment_limit.max_duration
    if max_dur and requested_duration and requested_duration > max_dur:
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

                # Nur die Vorschau anzeigen – kein Deploy
                context = {
                    'app_selected': app_def,
                    'duration_selected': duration,
                    'target_host_selected': target_host,
                    'app_description': app_def.description,
                    'readonly': True,
                    'app_env_vars': AppEnvVarPerApp.objects.filter(app=app_def, editable=True) if app_def else [], # nur editierbare Umgeb.Variablen an den Client schicken
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
    try:
        app_def = AppDefinition.objects.get(name=app_selected)
    except AppDefinition.DoesNotExist:
        app_def = None  # oder andere Fehlerbehandlung

    duration = request.POST.get('duration_selected', 1)

    target_host_selected = request.POST.get('target_host_selected')

    try:
        target_host = RemoteHost.objects.get(hostname=target_host_selected)
    except RemoteHost.DoesNotExist:
        target_host = None  # oder andere Fehlerbehandlung

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


    # 3) Zielhost wählen (hier einfaches Round‑Robin für normale user + Wahlfreiheit für superuser)
    host = target_host if request.user.is_superuser else RemoteHost.objects.first()

    # 4) Limits prüfen
    if not _check_user_limits(request.user, duration, request):
        # Rückmeldung an den User
        return render_deploy(
            request, error=' '.join("Ihre Limits wurden überschritten."), app_def=app_def
        )

    # 5) expires_at berechnen
    expires_at = None
    if duration is not None:
        duration_delta = parse_duration(duration)
        expires_at = timezone.now() + duration_delta

    # 6) Provision‑Objekt erzeugen
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

    # 7) Deploy‑Task starten
    deploy_app_task(provision.id, env_vars)
    provision.refresh_from_db()

    # 8) Erfolgspage
    return render(request, 'paas/deploy_success.html', {
        'provision': provision,
        'app_env_vars': env_vars,
        "PLATFORM_NAME": PLATFORM_NAME,
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
  return render(request, 'paas/deploy_success.html', {
      'provision': provision,
      "PLATFORM_NAME": PLATFORM_NAME,
  })

@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def my_apps(request):
  provisions = ProvisionedApp.objects.filter(user=request.user).order_by('-started_at')
  return render(request, 'paas/my_apps.html', {
      'provisions': provisions,
      "PLATFORM_NAME": PLATFORM_NAME,
  })

@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def delete_app(request, pk):
    """
    2‑Schritt‑Delete: Erstes POST → Bestätigungsseite, zweites POST → Löschen
    """
    provision = get_object_or_404(ProvisionedApp, pk=pk, user=request.user)
    provisions = ProvisionedApp.objects.filter(user=request.user).order_by('-started_at')

    # App darf nur laufen oder gelöscht werden
    if provision.status not in ('running', 'deleting'):
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
        if provision.status not in ('running', 'deleting'):
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