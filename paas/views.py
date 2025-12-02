from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.urls import reverse
from .models import ProvisionedApp, RemoteHost, AppDefinition, AppEnvVarPerApp, UserAppLimit
from .forms import DeployForm, DeployFormAdmin
from .tasks import deploy_app_task, delete_container_task
from core.settings import PLATFORM_NAME, USER_RATELIMIT_PER_HOUR
from django_smart_ratelimit import rate_limit


def _get_user_max_apps(user):
    """
    Liefert die maximale Anzahl an gleichzeitig laufenden Apps für
    ``user``.  Superusers erhalten praktisch keine Begrenzung.
    Für normale Benutzer gilt:
    - Falls ein expliziter Limit‑Eintrag existiert → dessen Wert
    - Sonst → ``settings.PAAS_MAX_FREE_APPS_PER_USER``
    """
    # Superuser → keine Begrenzung (oder ein sehr hoher Standardwert)
    if getattr(user, "is_superuser", False):
        # Option 1: Ein „praktisch unbegrenzter“ Integer
        return settings.PAAS_MAX_SUPERUSER_APPS

        # Option 2: Explizit ``None`` und die Logik, die die Rückgabe nutzt,
        #           müsste ``None`` als “unlimitiert” behandeln.

    # Für reguläre Nutzer
    limit = UserAppLimit.objects.filter(user=user).first()
    return limit.max_apps if limit else settings.PAAS_MAX_FREE_APPS_PER_USER


@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def select_app(request):
  # ──────────────────────── Formular‑Klasse bestimmen ─────────────────────
  FormCls = DeployFormAdmin if request.user.is_superuser else DeployForm

  if request.method == 'POST':

      form = FormCls(request.POST)

      if form.is_valid():

          app_def = form.cleaned_data['app']
          duration = int(form.cleaned_data['duration'])

          # max x laufende Apps prüfen

          # aktuell laufende apps pro user
          active = ProvisionedApp.objects.filter(
              user=request.user,
              status='running'
          ).count()

          # limit holen
          max_allowed = _get_user_max_apps(request.user)

          # limit prüfen
          if active >= max_allowed:
              return render(request, 'paas/select_app.html', {
                  'form': form,
                  'error': f'Du hast bereits {max_allowed} Apps laufen.'
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

        if not do_it and form.is_valid():
            """Verarbeitet ein gültiges Formular und führt das Deploy noch nicht aus."""
            app_def = form.cleaned_data.get('app')
            duration = int(form.cleaned_data.get('duration'))
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
            return _handle_deploy(request)



# ----------------------------------------------------------------------
# Helper‑Funktionen für deploy_app
# ----------------------------------------------------------------------
def _handle_deploy(request):

    app_selected = request.POST.get('app_selected')
    try:
        app_def = AppDefinition.objects.get(name=app_selected)
    except AppDefinition.DoesNotExist:
        app_def = None  # oder andere Fehlerbehandlung
    duration = int(request.POST.get('duration_selected', 1))

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

    # 3) Max. laufende Apps prüfen
    active = ProvisionedApp.objects.filter(
        user=request.user, status='running'
    ).count()
    max_allowed = _get_user_max_apps(request.user)

    if active >= max_allowed:
        return render_deploy(
            request,
            error=f'Du hast bereits {max_allowed} Apps laufen.',
            app_def=app_def,
        )

    # 4) Zielhost wählen (hier einfaches Round‑Robin für normale user + Wahlfreiheit für superuser)
    host = target_host if request.user.is_superuser else RemoteHost.objects.first()
    expires_at = timezone.now() + timezone.timedelta(hours=duration)

    # 5) Provision‑Objekt erzeugen
    provision = ProvisionedApp.objects.create(
        user=request.user,
        app=app_def,
        host=host,
        expires_at=expires_at,
        status='pending',
    )

    # 6) Deploy‑Task starten
    deploy_app_task(provision.id, env_vars)
    provision.refresh_from_db()

    # 7) Erfolgspage
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
  provisions = ProvisionedApp.objects.filter(user=request.user).order_by('-started_at')
  provision = ProvisionedApp.objects.get(pk=pk, user=request.user)
  if provision.status != 'running' and provision.status != 'deleting':
      return render(request, 'paas/my_apps.html', {
          'provisions': provisions,
          "PLATFORM_NAME": PLATFORM_NAME,
      })

  provision.status = 'deleting'
  provision.save()

  #synchron
  delete_container_task(provision.id)

  return render(request, 'paas/my_apps.html', {
      'provisions': provisions,
      "PLATFORM_NAME": PLATFORM_NAME,
  })