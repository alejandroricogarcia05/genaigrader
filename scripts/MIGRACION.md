# SQLite → PostgreSQL Migration — GenAI Grader

`migrate_sqlite_to_postgres.sh` moves **all data** from the old SQLite
environment (gunicorn + ngrok + tmux) into the **empty PostgreSQL** database
of the Docker production environment (`Docker/docker-compose.prod.yml`).

It is a **one-off** script: once the data is migrated, the old environment
and its `db.sqlite3` remain as a backup.

---

## What it does, step by step

1. **Checks**: Docker available, `.env` and `db.sqlite3` present, and the old
   environment stopped (if gunicorn/qcluster with `settings_ngrok` are still
   running, it aborts and asks you to stop them).
2. **`--reset` (optional)**: deletes the production volumes
   (`down -v`). Asks you to type `BORRAR` to confirm.
3. **Starts the production stack** (`up -d --build`): PostgreSQL creates the
   empty database and the `web` container creates the tables with `migrate`
   (done automatically by `entrypoint.sh`).
4. **Exports** the SQLite data to `datadump.json`:
   - Copies `db.sqlite3` **together with its `-wal`/`-shm` files** to a
     temporary directory. This matters: the most recent data lives in the WAL
     (1.5 MB!) and copying only the `.sqlite3` file would lose it. The WAL is
     also usually owned by root; the copy avoids permission issues and
     **leaves the original untouched**.
   - Starts a temporary container from the `web` image that opens that copy
     and runs Django's `dumpdata`.
5. **Imports** `datadump.json` into PostgreSQL with `loaddata`, inside another
   temporary container (the load is transactional: all or nothing).
6. **Copies `uploaded_files/`** into the `uploaded_files_data` volume.
7. **Verifies** that every PostgreSQL table has exactly the same number of
   rows as objects in the JSON, and shows the result table by table.

In the end, the production database holds **exactly the same data** as the
SQLite one: same users (with the same passwords), same courses, exams,
questions, models, evaluations and task history, keeping the original IDs.

---

## Requirements

- Docker with the **Compose v2** plugin on the machine running the script.
- The repository with its `.env` configured (the same one production uses).
- The old environment **stopped**:
  ```bash
  ./scripts/stop_genaigrader.sh
  ```
- An **empty** destination PostgreSQL. This is the case if:
  - it is a fresh deployment (another machine or a newly created volume), or
  - you wipe it with `--reset` (see below).

No Python, uv or virtualenv is needed on the host: **everything runs inside
containers**.

---

## Usage

```bash
./scripts/migrate_sqlite_to_postgres.sh
```

If the destination database is **not empty** and you want to start from
scratch (⚠️ this destroys the current production data):

```bash
./scripts/migrate_sqlite_to_postgres.sh --reset
```

The script will ask you to type `BORRAR` to confirm.

---

## What is migrated and what is not

| Data | Migrated? | Reason |
|---|---|---|
| Users (with their passwords) | ✅ | Real data |
| Courses, exams, questions, options | ✅ | Real data |
| Families and models (local and external) | ✅ | Real data |
| Evaluations and per-question answers | ✅ | Real data |
| Social accounts, emails, external identities | ✅ | Login bindings (Google, OIDC) |
| `django_q_task` history | ✅ | Needed by the old progress bars |
| `uploaded_files/` | ✅ | Copied into the production volume |
| Web sessions | ❌ | They expire on their own; users log in again |
| `contenttypes`, `auth_permission` | ❌ | Regenerated automatically by `migrate` |
| `django_migrations` | ❌ | Handled by `migrate` |
| Broker queue (`OrmQ`) and `Schedule` | ❌ | Empty; avoids re-running old tasks |

---

## After migrating

1. Open the production site (e.g. `http://localhost:8000`) and log in with
   one of the old users.
2. Check that their courses, exams and historical evaluations are there.
3. `datadump.json` contains password hashes and tokens: it is already in
   `.gitignore`, but **store it somewhere safe or delete it** once you have
   verified everything is fine.
4. The original `db.sqlite3` **has not been modified**: keep it as a backup.
   The old scripts (`start_genaigrader.sh`, etc.) still work against that
   SQLite file in case you ever need to roll back.

---

## Troubleshooting

> Note: the script prints its messages in Spanish. The headings below quote
> the exact error text so you can match it.

### `El entorno antiguo (gunicorn/ngrok) sigue corriendo`

The old environment is still running. Stop it and retry:

```bash
./scripts/stop_genaigrader.sh
./scripts/migrate_sqlite_to_postgres.sh
```

### `Fallo al importar. ¿La BD de destino NO estaba vacía?`

`loaddata` found duplicate keys: the PostgreSQL database already had data.
Nothing was imported (the load is transactional). Options:

- If that data does not matter: `./scripts/migrate_sqlite_to_postgres.sh --reset`
- If it matters: take a backup first
  (`docker exec docker-db-1 pg_dump -U <user> <db> > backup.sql`) and then run with `--reset`.

### `La verificación ha encontrado diferencias`

Some table does not have the expected row count. Run the script again with
`--reset` to start from a clean database. If it persists, check the logs:

```bash
docker compose --env-file .env -f Docker/docker-compose.prod.yml logs web
```

### Odd permissions on `db.sqlite3-wal` (owned by root)

Nothing to do: the script always works on a **temporary copy** of the three
files, so the permissions of the original do not matter.
