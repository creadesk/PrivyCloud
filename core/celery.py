from __future__ import absolute_import, unicode_literals
from os import environ
from celery import Celery
from celery.schedules import crontab
from django.conf import settings
import logging

# Celery‑App erzeugen
environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
app = Celery('core')

# Celery‑Konfiguration aus Django‑Settings laden
app.config_from_object('django.conf:settings', namespace='CELERY')

# Importiere alle Aufgaben (Tasks)
app.autodiscover_tasks(['paas.tasks'])

# Celery-Logger explizit setzen
logger = logging.getLogger('celery')
logger.setLevel(logging.DEBUG)  # Stelle sicher, dass Celery die Logs schreibt
