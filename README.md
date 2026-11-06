# NexaWork

**Connect. Collaborate. Work smarter.**

NexaWork is a modular Django workplace collaboration MVP for organizations with employees across teams and branches. The code is organized into Django apps for accounts, organization, communications, documents, and the workplace assistant.

## What works in this MVP

- Employee ID and password sign-in, plus a separate employer sign-in.
- Organization, branch, region, department, team, job-role, and employee administration.
- Organization-wide and membership-restricted channels with messages that update in the browser every three seconds.
- Branch-aware announcements and per-user notifications.
- Meeting scheduling, participant roles, host/co-host controls, meeting locks, participant muting, and host/admin removal with a 10-minute rejoin restriction.
- Optional LiveKit integration for browser audio/video, short-lived room tokens, provider-side participant controls, and webhook-driven meeting state.
- Organization/branch-scoped file library, restricted-file access lists, uploads capped at 25 MB, and authenticated download views.
- A workplace assistant that answers common platform questions and stores each user's recent exchanges.
- Django admin and configurable role/permission management for employer-side administration.
- Celery tasks for meeting reminders and notification email, with Redis-backed Channels support.

The assistant uses built-in guidance in this MVP; no external AI provider is called. The current channel page refreshes messages by authenticated polling; authenticated Channels consumers and notification WebSockets are available for real-time clients. Meeting state and access authorization are managed by Django. When LiveKit is configured, audio/video travels through LiveKit rather than Django, and server-side participant removal/muting is available. If an external provider URL is used instead, NexaWork cannot enforce provider-side participant removal or rejoin restrictions.

Uploaded files are stored locally under `private_media/` by default and are never exposed using a public media URL. Django's storage backend is configurable with `DJANGO_FILE_STORAGE_BACKEND`; production can select a private S3-compatible backend while continuing to use the same authorized download view.

The Files page keeps previous file contents when an authorized user uploads a new version; earlier versions can be viewed and downloaded from each file's version history. Run `python scripts/backup.py` to save a database snapshot and a copy of local private uploads under `backups/`. The script retains 30 days of backups by default; set `BACKUP_DIR` or `BACKUP_RETENTION_DAYS` to change those defaults. Schedule the command with your operating system's task scheduler for regular backups. If you configure remote object storage, back up that storage separately. These backups are on the same host and are not protection against host or disk loss; copy them to separate storage for disaster recovery. Protect the backup directory because it contains private files and database data.

## Run locally

Requires Python 3.10+ and pip.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Use the employer sign-in for the superuser. Create an organization in Django admin, then edit the employer account and assign it to that organization. Create employees and channels in admin; employees sign in at `/login/`.

The default database is SQLite. Set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, and `POSTGRES_PORT` to use PostgreSQL. Set a strong `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, and production `DJANGO_ALLOWED_HOSTS` before deployment. With debug disabled, HTTPS redirects, secure session/CSRF cookies, and HSTS are enabled by default; configure the reverse proxy to terminate TLS correctly.

## Services

The default setup uses SQLite and private files on the local filesystem. To use PostgreSQL, install and start PostgreSQL separately, then export the `POSTGRES_*` environment variables. For cross-process Channels and Celery, install and start Redis separately and export `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND`. Run the Celery worker and scheduler in separate terminals with `celery -A nexawork worker --loglevel=info` and `celery -A nexawork beat --loglevel=info`. Integrated meeting audio/video is optional; install and run a LiveKit server separately and export its URL and credentials. Environment variables must be exported by your shell or configured by your process manager; Django does not load `.env` automatically.

## Tests and checks

```bash
python manage.py check
python manage.py test
```
