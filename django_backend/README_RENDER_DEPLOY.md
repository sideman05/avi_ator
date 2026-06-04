# Render Deployment

This guide deploys the Django backend to Render with:

- One web service for the API
- One worker service for the monitor
- One PostgreSQL database shared by both services

Important: Render does not offer the `free` plan for background workers. The
web API can use `free`, but the monitor worker uses `starter`.

## Files Used

Important backend files:

```text
api/django_backend/render.yaml
api/django_backend/start.sh
api/django_backend/requirements.txt
api/django_backend/manage.py
```

From the full Flutter project, there are two Render YAML files:

```text
api/render.yaml
api/django_backend/render.yaml
```

Use `api/render.yaml` when the deployed repository/root directory is the `api`
folder. It contains `rootDir: django_backend`.

Use `api/django_backend/render.yaml` when the deployed repository/root directory
is already the Django backend folder.

If your GitHub repository root is the full Flutter project folder, create the
Render services manually with the commands below, or move the appropriate YAML
to the repository root before creating a Render Blueprint.


## Option 1: Deploy With render.yaml

In Render:

1. Create a new Blueprint.
2. Connect your GitHub repository.
3. Select the correct `render.yaml`.
4. Let Render create:
   - `aviator-backend-web`
   - `aviator-backend-monitor`
   - `aviator-backend-db`

The web service uses the `free` plan. The worker service uses the `starter`
plan because Render workers cannot use `free`.

The YAML already sets:

```bash
buildCommand: pip install -r requirements.txt && python manage.py collectstatic --noinput
startCommand: bash start.sh
```

The worker starts with:

```bash
python manage.py run_monitor_worker
```

## Option 2: Manual Render Setup

Create a PostgreSQL database first.

If you are using Neon instead of a Render database, skip the Render database
step and copy the Neon direct connection string into `DATABASE_URL` for both
services.

Then create a Python web service:

```bash
Build Command:
pip install -r requirements.txt && python manage.py collectstatic --noinput

Start Command:
bash start.sh
```

Create a Python worker service:

```bash
Build Command:
pip install -r requirements.txt

Start Command:
python manage.py run_monitor_worker
```

## Required Environment Variables

Set these on both the web service and the worker:

```bash
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=.onrender.com
DATABASE_URL=<your Render PostgreSQL connection string>
DJANGO_SECRET_KEY=<one strong secret shared by web and worker>
```

For Neon, use:

```bash
DATABASE_URL=<your Neon direct postgresql://... connection string>
DATABASE_CONN_MAX_AGE=600
```

If you choose the Neon pooled URL, set `DATABASE_CONN_MAX_AGE=0`.

Optional monitor login variables:

```bash
AVIATOR_PHONE=<phone number>
AVIATOR_PASSWORD=<password>
AVIATOR_BROWSER=auto
AVIATOR_CHECK_INTERVAL=0.5
AVIATOR_WAIT_TIMEOUT=45
AVIATOR_MAX_IFRAME_DEPTH=6
```

Optional admin access-key variable:

```bash
AVIATOR_ACCESS_KEY_ADMIN_TOKEN=<secret admin token>
```

## Important Start Command

Use this for the web service:

```bash
bash start.sh
```

Do not use plain Gunicorn unless you also run migrations separately.

`bash start.sh` runs:

```bash
python manage.py migrate --noinput
gunicorn aviator_backend.wsgi:application
```

This prevents missing PostgreSQL tables from causing HTTP 500 errors.

## Flutter API Base URL

When building the Flutter app for the hosted backend, pass your Render URL:

```bash
flutter build apk --dart-define=AVIATOR_API_BASE_URL=https://your-service-name.onrender.com
```

The app will call:

```text
POST /api/access-keys/validate/
GET /api/prediction/
```

## Verify Deployment

After Render finishes deploying, open these URLs:

```text
https://your-service-name.onrender.com/monitor/status/
https://your-service-name.onrender.com/monitor/odds/
```

Expected result:

```json
{"success": true}
```

The prediction endpoint may return `409` until the monitor reports that a round
has ended. That is expected and is not a server crash.

## If You Still Get HTTP 500

Check Render logs for messages like:

```text
relation "api_app_monitorstate" does not exist
relation "api_app_monitorlog" does not exist
no such table
```

That means migrations did not run on the database used by the web service.

Fix:

```bash
python manage.py migrate --noinput
```

Then redeploy the web service with:

```bash
bash start.sh
```
