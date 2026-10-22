# Running Fitness Studio

The application consists of a web server, API, durable task worker, reminder scheduler, MySQL, and Redis. The web server proxies API requests on the same origin so session cookies and CSRF protection work without cross-origin exceptions.

## Docker startup

Create a private `.env` in this directory with these settings. Generate independent random passwords and secrets; do not commit the file. Use URL-safe database passwords because the application connection URL includes them.

```dotenv
DATABASE_PASSWORD=replace-with-a-random-url-safe-password
DATABASE_ROOT_PASSWORD=replace-with-another-random-password
DATABASE_URL=mysql+pymysql://studio:replace-with-a-random-url-safe-password@127.0.0.1:33316/fitness_studio
SESSION_SECRET=replace-with-a-long-random-secret
OWNER_EMAIL=owner@example.test
OWNER_PASSWORD=replace-with-a-long-random-password
GEMINI_API_KEY=
MODEL_CALLS_ENABLED=false
MODEL_RPM_LIMIT=6
MODEL_CONCURRENCY_LIMIT=1
MODEL_DAILY_CALL_LIMIT=30
COOKIE_SECURE=false
```

Run from the application root:

```sh
docker compose -f compose.yaml -f compose.app.yaml up --build -d
```

Open `http://localhost:8088`. Initialization applies migrations and creates the owner account, initial service groups, and a zero global model budget. API readiness requires the current migration revision and reachable Redis. Worker, scheduler, and web startup depend on a healthy API. MySQL and Redis data persist in named volumes.

Sign in with the owner credentials from `.env`; members register their own accounts. Changing the environment owner password does not reset an existing account's password.

## Model calls

Enabling calls requires a Gemini API key, `MODEL_CALLS_ENABLED=true`, and sufficient configured budgets for every applicable scope: global, role, member, and group. Intake before membership uses global and role scopes. The API key stays on the server. Restart API and worker after changing environment settings.

Normal chat makes one inference request per response. Combined training and nutrition generation normally makes two. Viewing pages, approving saved candidates, and deterministic reminders make no model calls. Rate and daily limits are application-wide; Google account limits may be lower. Unknown provider usage retains its reservation until reconciled, rather than being treated as free.

## Processes and diagnostics

```sh
docker compose -f compose.yaml -f compose.app.yaml ps
docker compose -f compose.yaml -f compose.app.yaml logs --tail=100 api worker scheduler
curl --fail http://localhost:8088/health/ready
```

The scheduler checks for due in-app notifications and approved canary observation rules every 60 seconds. Database business keys prevent duplicate notifications within the same reminder period. Its interval does not control model request frequency.

Stop application processes without deleting data:

```sh
docker compose -f compose.yaml -f compose.app.yaml stop
```

## Local development

Start only persistence with `docker compose up -d`. Install the backend lock file and package in a Python 3.13 environment. From `backend`, run migrations, bootstrap, and each application process in its own terminal:

```sh
alembic upgrade head
python -m app.bootstrap
uvicorn app.main:app --host 127.0.0.1 --port 8018
python -m app.worker
python -m app.scheduler
```

From `frontend`, run `npm ci` and `npm run dev`. The development URL is `http://localhost:5178`, with API requests proxied to port 8018.

The supplied Docker port bindings target local use. For a public HTTPS deployment, configure a trusted TLS reverse proxy and `COOKIE_SECURE=true`; keep database and Redis ports private.

## Isolated backend regression tests

Use the installed application virtual environment from the application root:

```sh
.venv/bin/python scripts/test_backend.py
```

Docker must be on `PATH`. This command starts the dedicated `fitness-studio-test` Compose project with MySQL on port 33317 and Redis on port 36380, applies migrations, bootstraps a test owner, and runs pytest. It explicitly disables Gemini and removes the API key from the test process environment. The test database and volumes are separate from the normal application services. Optional pytest arguments follow the script name.

The test services persist for subsequent runs. Stop them without removing their volumes with:

```sh
docker compose -p fitness-studio-test -f compose.yaml stop
```

Application database names and host ports can be overridden with `DATABASE_NAME`, `DATABASE_PORT`, and `REDIS_PORT`. Local Python processes must use a matching `DATABASE_URL` and `REDIS_URL` when those values change. Docker application processes derive their internal database URL from `DATABASE_NAME`.
