#!/usr/bin/env bash
set -euo pipefail

python manage.py migrate --noinput
exec gunicorn aviator_backend.wsgi:application
