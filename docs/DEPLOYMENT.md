# Deployment

This guide covers a single-site deployment on one host or one container platform. Rate limits are shared through Redis, so the API can run as several processes.

## What runs

| Service | What | Notes |
|---|---|---|
| PostgreSQL 18 with pgvector | Application data | The `pgvector/pgvector:pg18` image is what CI and `docker-compose.yml` use. |
| Redis 8 | Celery broker and results, and the shared rate-limit windows | Append-only persistence is on in `docker-compose.yml`. |
| API | `uvicorn prefix_api.main:create_app --factory --port 8000` | Any number of processes: rate limits live in Redis. |
| Worker | `celery --app prefix_worker.celery_app worker` | Reminders, risk scores, translations, the nightly retention purge and demo reset. |
| Beat | `celery --app prefix_worker.celery_app beat` | **Exactly one** beat process, or every job runs twice. |
| Web | `next build`, then `next start --port 3000` | Put TLS in front of both the web app and the API. |

Migrations are a separate step. Run `alembic -c apps/api/alembic.ini upgrade head` (`make migrate` does the same) once per release, before the new API starts. `docker-compose.yml` does not run them for you.

## Environment

Every setting is read from the environment and validated at start-up (`apps/api/src/prefix_api/core/config.py`). The API refuses to start if a value is missing, or if a development key or placeholder secret is still set in production. `.env.example` lists them all.

| Variable | Production value |
|---|---|
| `APP_ENV` | `production`. Turns on HSTS, turns off `/docs` and `/openapi.json`. |
| `DATABASE_URL` | The PostgreSQL URL, with a non-default password. |
| `REDIS_URL` | The Redis URL for the worker and the API's rate limits. Required outside development. The PostgreSQL broker that `scripts/dev_worker.py` uses when it is empty is for local development only, and the settings refuse it in staging and production. |
| `JWT_SECRET` | At least 32 random characters. Changing it signs everyone out. |
| `PII_ENCRYPTION_KEYS` | Comma-separated keys. The first encrypts; all of them decrypt. Create one with `uv run python -c "from prefix_api.core.crypto import generate_key; print(generate_key())"`. |
| `PII_BLIND_INDEX_KEY` | 32 or more random characters. Changing it breaks lookups by phone, e-mail and MRN. |
| `LLM_MODEL` | The Claude model ID. Required; never hard-coded. |
| `ANTHROPIC_API_KEY` | For the AI assistant. Without it, questions go straight to a nurse. |
| `MESSAGING_PROVIDER` | `live` to send, refusing to start without Twilio. The default `auto` sends only when `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER` are set, and otherwise logs with recipients masked ("demo mode", shown in the staff app). `console` always logs. |
| `TWILIO_*`, `SMTP_*` | The provider details. `TWILIO_WHATSAPP_FROM` is the WhatsApp sender if it is not the SMS number. |
| `PUBLIC_API_BASE_URL` | The public HTTPS address of the API, for the Twilio webhook. |
| `WEB_BASE_URL` | The public HTTPS address of the web app, for links in messages. |
| `CORS_ORIGINS` | The web app's origin only. |
| `ENABLE_GUEST_LOGIN` | `false`. The guest demo is for demonstrations, not for a hospital's real patients. |
| `API_INTERNAL_URL` | Where the web server reaches the API, e.g. `http://api:8000`. |

## Steps

1. Create PostgreSQL and Redis. Enable encryption of the database volume and of backups at the platform level.
2. Set the variables above. Keep the keys in a secret manager, not in the repository or the image.
3. Build the images with `docker compose build`. CI runs the same build on every push.
4. Run the migrations once against the production database.
5. Start the API, the worker, one beat process and the web app.
6. Check `GET /health` on the API and `GET /api/health` on the web app. Both should return `200`.
7. Put TLS in front of both. The API and web app send `Strict-Transport-Security` in production; the certificate must be valid for the whole domain before HSTS is enabled.

## First admin

Set up a real hospital from the server, after the migrations:

```bash
uv run prefix-admin create-org --name "City Hospital" --slug city-hospital   --country IN --timezone Asia/Kolkata --baseline-rate 12.5
uv run prefix-admin create-first-admin --org city-hospital   --email admin@city-hospital.example --name "Asha Rao"
```

The password is asked twice at a hidden prompt (at least 12 characters). It is never echoed, logged or printed, and there is no flag for it, so it cannot end up in shell history. Both steps are audited. The command refuses a second first admin; that admin adds the rest of the staff in the app. Then, in the app's **Compliance** page, set the retention period.

`prefix_api.seed` and `prefix_api.seed_demo` create demonstration data. Do not run them against a database that holds real patients.

## Backups and keys

- Back up the database every night with `pg_dump` (or your platform's snapshots), and test a restore each month. Backups hold encrypted personal fields, and the keys to read them are separate, so keep `PII_ENCRYPTION_KEYS` and `PII_BLIND_INDEX_KEY` in the secret manager, never in the backup.
- **Rotating a PII key:** add the new key at the front of `PII_ENCRYPTION_KEYS`. Existing rows stay readable because every listed key decrypts. Keep old keys in the list until every row is re-encrypted. A re-encryption job is not built yet.
- **Rotating `JWT_SECRET`** signs everyone out. Do it outside clinic hours.

## Known limits

- **Rate limits fall back to one process when Redis is down.** Each API process then counts on its own until Redis answers again, and logs `rate_limit.redis_unavailable`.
- **The script policy uses a nonce per request** (`src/proxy.ts`), so no inline script runs without it. Every page renders per request for this; prerendered static shells would carry no nonce. Styles still allow inline values, which animations need.
- **Erasure does not reach backups.** It overwrites the live database. Older copies stay in backups until they expire, so keep backup retention short and documented (see [COMPLIANCE.md](COMPLIANCE.md)).
- **Staff tokens are kept in `httpOnly` cookies** on the web origin, so page scripts cannot read them. Access tokens are short-lived and rotate on refresh.
- **Redis down means no reminders.** The worker and beat need Redis. Reminders are stored in the database before they are due, and the dispatcher catches up when Redis returns, so nothing is lost, but patients may get a reminder late.
