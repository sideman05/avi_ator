#!/usr/bin/env bash
set -euo pipefail

# Ensure static files are collected and database is migrated before starting
python manage.py collectstatic --noinput || true
python manage.py migrate --noinput

# Bind to Heroku-provided $PORT when available
exec gunicorn aviator_backend.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 3
