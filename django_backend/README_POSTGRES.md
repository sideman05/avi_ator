# PostgreSQL Setup

This backend uses PostgreSQL when the `DATABASE_URL` environment variable is set.
If `DATABASE_URL` is missing, Django falls back to the local `db.sqlite3` file.

## Required Package

The PostgreSQL driver is already included in `requirements.txt`:

```bash
psycopg2-binary
```

Install all backend dependencies:

```bash
pip install -r requirements.txt
```

## Environment Variable

Set `DATABASE_URL` to your PostgreSQL connection string:

```bash
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:PORT/DATABASE"
```

For Neon, use the direct connection string from the Neon dashboard.
Prefer the direct URL over the pooled URL for Django. If you must use the
pooled URL, set `DATABASE_CONN_MAX_AGE=0`.

Example:

```bash
export DATABASE_URL="postgresql://aviator_user:secret@localhost:5432/aviator_db"
```

On Render, use the database `Internal Database URL` or the value provided through
`fromDatabase.property: connectionString` in `render.yaml`.

On Render with Neon, set `DATABASE_URL` manually from the Neon database settings
in both the web service and the worker service.

## Local PostgreSQL Setup

Create the database and user:

```bash
createdb aviator_db
```

If you need a separate user:

```bash
createuser aviator_user
```

Then grant access from your PostgreSQL shell:

```sql
GRANT ALL PRIVILEGES ON DATABASE aviator_db TO aviator_user;
```

## Run Migrations

From this directory:

```bash
python manage.py migrate
```

Check that the app migrations are applied:

```bash
python manage.py showmigrations api_app
```

You should see:

```text
[X] 0001_initial
[X] 0002_monitorroundodds
[X] 0003_monitorstate
[X] 0004_monitorlog
```

## Verify Database Connection

Run:

```bash
python manage.py check
```

Then test a database-backed endpoint:

```bash
python manage.py shell -c "from api_app.models import MonitorState; print(MonitorState.get_current()[1])"
```

## Common 500 Error Cause

If Render or the app returns HTTP 500 after deployment, the most common cause is
that migrations did not run against PostgreSQL.

Fix it by running:

```bash
python manage.py migrate --noinput
```

The included `start.sh` also runs migrations automatically before starting
Gunicorn:

```bash
./start.sh
```

