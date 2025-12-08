import os
from datetime import timedelta
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv   # <- nur für lokale/Dev-Umgebung

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
# ------------------------------------------------------------------
# Laden der Umgebungsvariablen (nur wenn .env existiert)
# ------------------------------------------------------------------
dotenv_path = BASE_DIR / '.env'
if dotenv_path.exists():
  load_dotenv(dotenv_path)

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
  raise RuntimeError('SECRET_KEY is not set in environment!')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'False').lower() in ('1', 'true', 'yes')

# Split ALLOWED_HOSTS by comma, strip spaces
allowed_hosts_raw = os.getenv('ALLOWED_HOSTS', '127.0.0.1')
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_raw.split(',')]

STRING_TO_ADMIN_PATH = os.getenv('STRING_TO_ADMIN_PAGE', 'admin/')

ADMIN_IP_LIMITER_ENABLED = os.getenv('ADMIN_IP_LIMITER_ENABLED','True').lower() in ('1', 'true', 'yes')

private_ip_ranges_raw = os.getenv('PRIVATE_IP_RANGES', '127.0.0.1')
PRIVATE_IP_RANGES = [h.strip() for h in private_ip_ranges_raw.split(',')]

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'dashboard',
    'django_otp',
    'django_otp.plugins.otp_totp',
    'authent',
    'captcha',
    'django_smart_ratelimit',
    'paas.apps.PaasConfig',
    'config',
    'django_celery_beat',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'middleware.AdminOnlyFromPrivateIPMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_otp.middleware.OTPMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [ BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases
# ------------------------------------------------------------------
# Datenbank (SQL‑Alchemy‑kompatibel)
# ------------------------------------------------------------------
DATABASES = {
  'default': {
      'ENGINE': os.getenv('DB_ENGINE', 'django.db.backends.sqlite3'),
      'NAME': os.getenv('DB_NAME', BASE_DIR / 'db/db.sqlite3'),
      'USER': os.getenv('DB_USER', ''),
      'PASSWORD': os.getenv('DB_PASSWORD', ''),
      'HOST': os.getenv('DB_HOST', ''),
      'PORT': os.getenv('DB_PORT', ''),
  }
}


# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = 'static/'
#STATIC_ROOT =  os.path.join(BASE_DIR,'static/')
STATICFILES_DIRS = [
    BASE_DIR / "static",
]


# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# where to save uploaded files
MEDIA_ROOT = os.path.join(BASE_DIR, "media")
# under which url to serve uploaded files
MEDIA_URL = "/media/"

LOGIN_URL = 'login/'

PLATFORM_NAME = 'PrivyCloud'
OTP_TOTP_ISSUER = PLATFORM_NAME

CAPTCHA_FONT_SIZE = 40
CAPTCHA_LENGTH = 8
#CAPTCHA_CHALLENGE_FUNCT = 'captcha.helpers.random_char_challenge'
CAPTCHA_CHALLENGE_FUNCT = 'captcha.helpers.math_challenge'

REDIS_SERVER_IP = os.getenv('REDIS_SERVER_IP', '127.0.0.1')
REDIS_SERVER_PORT = os.getenv('REDIS_SERVER_PORT', '6379')
REDIS_SERVER_DB = os.getenv('REDIS_SERVER_DB', '0')


RATELIMIT_BACKEND = 'redis'
RATELIMIT_REDIS = {
    'host': REDIS_SERVER_IP,
    'port': REDIS_SERVER_PORT,
    'db': REDIS_SERVER_DB,
}
USER_RATELIMIT_PER_HOUR = 100
IP_RATELIMIT_PER_MINUTE = 10

# -------------------------------------------------------------
# Celery Basic Settings – Redis TTL = 900 Sekunden (15 Minuten)
# -------------------------------------------------------------

CELERY_BROKER_URL = os.getenv(
    'CELERY_BROKER_URL',
    f'redis://{REDIS_SERVER_IP}:{REDIS_SERVER_PORT}/{REDIS_SERVER_DB}'
)
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', CELERY_BROKER_URL)

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# Standard-Queue Name
CELERY_TASK_DEFAULT_QUEUE = 'celery'

# WICHTIG: Alle Queues mit TTL = 900 Sekunden (15 Minuten)
CELERY_TASK_QUEUES = {
    'celery': {
        'exchange': 'celery',
        'routing_key': 'celery',
        'queue_arguments': {'x-message-ttl': 900_000},   # 900 Sekunden in Millisekunden!
    },
    # Optional: falls du celery beat separat laufen lässt
    'celerybeat': {
        'exchange': 'celerybeat',
        'routing_key': 'celerybeat',
        'queue_arguments': {'x-message-ttl': 900_000},
    },
}

# Damit Celery die Queues automatisch anlegt (wichtig bei x-message-ttl!)
CELERY_TASK_CREATE_MISSING_QUEUES = True

# Optional: Task-Ergebnisse auch nur 60 Minuten behalten (statt 24h)
CELERY_TASK_RESULT_EXPIRES = 3600
CELERY_RESULT_EXPIRES = 3600               # Abwärtskompatibilität
CELERY_REDIS_RESULT_KEY_EXPIRES = 3600     # Die eigentliche Lösung ab Celery 4+

# Beat Schedule
CELERY_BEAT_SCHEDULE = {
    'cleanup-expired-provisions-every-1min': {
        'task': 'paas.tasks.sweep_expired_containers',
        'schedule': timedelta(minutes=1),
        'options': {
            'expires': 180,      # Task wird nach max. 3 Minuten als verloren markiert
            'queue': 'celery',   # explizit in die TTL-Queue schicken
        },
    },
    'update-remote-loads': {
        'task': 'paas.tasks.update_remote_loads',
        #'schedule': crontab(minute='*/5'),   # 0,5,10,15,...
        'schedule': timedelta(minutes=5),
        'options': {
            'expires': 600,       # Task wird nach max. 10 Minuten als verloren markiert
            'queue': 'celery',
        },
    },
}

CELERY_TIMEZONE = 'UTC'

# -------------------------------------------------------------
# Logging – separate Log‑File für Celery
# -------------------------------------------------------------
# Pfad für Log‑Datei – relative Pfad im Projektverzeichnis
CELERY_LOG_FILE = os.path.join(BASE_DIR, 'logs', 'celery.log')

# RotatingFileHandler konfigurieren (max 5MB, 3 alte Log‑Dateien behalten)
celery_handler = RotatingFileHandler(
  CELERY_LOG_FILE,
  maxBytes=5*1024*1024,
  backupCount=3,
  encoding='utf-8'
)
celery_handler.setLevel(logging.INFO)
celery_handler.setFormatter(
  logging.Formatter(
      fmt='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
      datefmt='%Y-%m-%d %H:%M:%S'
  )
)

LOGGING = {
  'version': 1,
  'disable_existing_loggers': False,
  'formatters': {
      'verbose': {
          'format': '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
          'datefmt': '%Y-%m-%d %H:%M:%S',
      },
  },
  'handlers': {
      # Standard‑Django‑Handler
      'console': {
          'class': 'logging.StreamHandler',
          'formatter': 'verbose',
      },
      # Celery‑Handler
      'celery_file': {
          'class': 'logging.handlers.RotatingFileHandler',
          'filename': CELERY_LOG_FILE,
          'maxBytes': 5 * 1024 * 1024,
          'backupCount': 3,
          'formatter': 'verbose',
          'encoding': 'utf-8',
      },
  },
  'loggers': {
      # Django‑Standard‑Logger
      'django': {
          'handlers': ['console'],
          'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
          'propagate': False,
      },
      # Celery‑Logger
      'celery': {
          'handlers': ['celery_file'],
          'level': 'DEBUG',
          'propagate': True,
      },
      # Optional: weitere Logger
  },
}



