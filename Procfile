release: python django_backend/manage.py migrate --noinput
web: gunicorn --chdir django_backend aviator_backend.wsgi:application --bind 0.0.0.0:$PORT --worker-class gthread --threads 4 --workers 2 --timeout 120

worker: python django_backend/manage.py run_monitor_worker
