#!/usr/bin/env bash
set -e pipefail
trap "kill 0" SIGTERM SIGINT

# 1. Optional: Datenbank‑URL aus Umgebungs‑Variablen setzen (default SQLite)
: "${DJANGO_SETTINGS_MODULE:=core.settings}"
: "${DJANGO_SECRET_KEY:='dev-secret-key'}"
: "${DJANGO_ALLOWED_HOSTS:=*}"

# 2. Datenbank‑migrationen durchführen (bei SQLite sind sie trivial)
echo "Running migrations..."
python manage.py migrate --noinput

# ------------------------------------------------------------------
# 3. Super‑User erzeugen (falls noch nicht vorhanden)
# ------------------------------------------------------------------
# Nur wenn sowohl Benutzername als auch Passwort definiert sind
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "Preparing to create superuser: ${DJANGO_SUPERUSER_USERNAME}"

  # Prüfen, ob der User schon existiert (Python‑Shell)
  USER_EXISTS=$(python manage.py check_superuser)

  if [ "$USER_EXISTS" = "False" ]; then
      echo "Superuser does not exist – creating…"

      # Das Passwort wird intern aus DJANGO_SUPERUSER_PASSWORD gelesen
      DJANGO_SUPERUSER_EMAIL="${DJANGO_SUPERUSER_EMAIL:-admin@example.com}" \
      python manage.py createsuperuser \
          --noinput \
          --username "${DJANGO_SUPERUSER_USERNAME}" \
          --email "${DJANGO_SUPERUSER_EMAIL}"
  else
      echo "Superuser ${DJANGO_SUPERUSER_USERNAME} already exists – skipping creation."
  fi
else
  echo "DJANGO_SUPERUSER_USERNAME and/or DJANGO_SUPERUSER_PASSWORD not set – skipping superuser creation."
fi

# 4. Startkonfiguration der Datenbank (siehe "paas/management/commands/db_start_config.py")
python manage.py db_start_config

echo "Starte Celery Worker"
celery -A core.celery worker \
       --beat \
       --loglevel info \
       --concurrency 2 \
       --without-gossip --without-mingle \
       --logfile /app/logs/celery.log &

echo "Starte Flower"
celery -A core.celery flower \
       --port=5555 \
       --loglevel info \
       --logfile /app/logs/flower.log &

echo "Starte Gunicorn (foreground)"
exec gunicorn core.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers 3