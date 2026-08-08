#!/usr/bin/env bash
set -euo pipefail

trap 'kill 0' SIGTERM SIGINT EXIT

: "${DJANGO_SETTINGS_MODULE:=core.settings}"
: "${DJANGO_SECRET_KEY:='dev-secret-key'}"
: "${DJANGO_ALLOWED_HOSTS:=*}"

echo "Running migrations..."
python manage.py migrate --noinput

if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    echo "Preparing to create superuser: ${DJANGO_SUPERUSER_USERNAME}"

    USER_EXISTS=$(python manage.py check_superuser)

    if [ "$USER_EXISTS" = "False" ]; then
        echo "Superuser does not exist – creating..."
        python manage.py createsuperuser \
            --noinput \
            --username "${DJANGO_SUPERUSER_USERNAME}" \
            --email "${DJANGO_SUPERUSER_EMAIL:-admin@example.com}"
    else
        echo "Superuser ${DJANGO_SUPERUSER_USERNAME} already exists – skipping creation."
    fi
else
    echo "DJANGO_SUPERUSER_USERNAME and/or DJANGO_SUPERUSER_PASSWORD not set – skipping superuser creation."
fi

python manage.py db_start_config

echo "Starte Celery Worker"
celery -A core.celery worker \
    --beat \
    --loglevel info \
    --concurrency 2 \
    --without-gossip \
    --without-mingle \
    --logfile /app/logs/celery.log &

echo "Starte Flower"
celery -A core.celery flower \
    --port=5555 \
    --loglevel info \
    --logfile /app/logs/flower.log &

echo "Starte Gunicorn"
gunicorn core.wsgi:application \
    --timeout 550 \
    --bind 0.0.0.0:8000 \
    --workers 3 &

wait -n

kill 0
wait