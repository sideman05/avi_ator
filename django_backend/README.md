# Aviator Django Backend

This backend provides:

- Access key generation and validation APIs
- Prediction generation and proxy endpoints
- A monitor UI and streaming endpoint

Local setup:

1. Create a virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Apply migrations and run the server:

```bash
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Endpoints:

- `POST /api/access-keys/generate/`
- `POST /api/access-keys/validate/`
- `GET /api/prediction/`
- `GET /api/prediction-proxy/`
- `GET /monitor/`

Render deployment:

Detailed guides:

- PostgreSQL setup: `README_POSTGRES.md`
- Render deployment: `README_RENDER_DEPLOY.md`

1. Use `render.yaml` or create the services manually.
   - If your GitHub repo root is `api/`, set Render Root Directory to `django_backend` (or use the repo-root `render.yaml` which already sets `rootDir: django_backend`).
2. Create a PostgreSQL database.
3. Keep the same `DJANGO_SECRET_KEY` value on both services.
4. Set these environment variables on both the web service and the worker:
   - `DJANGO_SECRET_KEY`
   - `DJANGO_DEBUG=False`
   - `DJANGO_ALLOWED_HOSTS=.onrender.com`
   - `DATABASE_URL` from the Render database or Neon
   - Optional: `DATABASE_CONN_MAX_AGE=0` if you use a Neon pooled URL
5. Web service start command:
   - `./start.sh`
6. Worker start command:
   - `python manage.py run_monitor_worker`
7. The web service runs `python manage.py migrate --noinput` before Gunicorn starts. If you configure Render manually, keep `./start.sh` as the web start command so missing tables do not cause 500 responses after deploys.

The app uses PostgreSQL automatically when `DATABASE_URL` is present. Static files are served with WhiteNoise, and the monitor state and logs are persisted in PostgreSQL so the web service and worker share the same state.
