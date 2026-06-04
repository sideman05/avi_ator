import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-secret')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('1', 'true', 'yes', 'on')

default_allowed_hosts = 'localhost,127.0.0.1,.onrender.com' if not DEBUG else '*'
ALLOWED_HOSTS = [host.strip() for host in os.environ.get('DJANGO_ALLOWED_HOSTS', default_allowed_hosts).split(',') if host.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'api_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]

ROOT_URLCONF = 'aviator_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'api_app' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    }
]

WSGI_APPLICATION = 'aviator_backend.wsgi.application'

DATABASE_URL = os.environ.get('DATABASE_URL')
DATABASE_CONN_MAX_AGE = int(os.environ.get('DATABASE_CONN_MAX_AGE', '600'))


def _database_config_from_url(database_url: str):
    parsed = urlparse(database_url)
    scheme = parsed.scheme.split('+', 1)[0]
    if scheme in ('postgres', 'postgresql'):
        engine = 'django.db.backends.postgresql'
    else:
        engine = 'django.db.backends.sqlite3'

    config = {
        'ENGINE': engine,
        'NAME': parsed.path.lstrip('/'),
        'USER': unquote(parsed.username or ''),
        'PASSWORD': unquote(parsed.password or ''),
        'HOST': parsed.hostname or '',
        'PORT': str(parsed.port or ''),
        'CONN_MAX_AGE': DATABASE_CONN_MAX_AGE,
    }
    if not DEBUG:
        config['OPTIONS'] = {'sslmode': 'require'}
    return config


if DATABASE_URL:
    DATABASES = {'default': _database_config_from_url(DATABASE_URL)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('AVIATOR_TIME_ZONE', 'Africa/Dar_es_Salaam')
USE_I18N = True
USE_TZ = True

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
