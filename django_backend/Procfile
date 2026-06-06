release: python django_backend/manage.py migrate --noinput
web: gunicorn --chdir django_backend aviator_backend.wsgi:application --bind 0.0.0.0:$PORT --workers 3
