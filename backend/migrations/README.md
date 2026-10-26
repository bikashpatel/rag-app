# Database migrations

The schema is managed by [Alembic](https://alembic.sqlalchemy.org/) 1.20. The app migrates the database itself when it
starts: you never run a migration by hand on a deploy.

| File | What it is |
|---|---|
| `../alembic.ini` | the command line's settings (for writing migrations) |
| `env.py` | the environment: the models' metadata, the app's engine (`app.db.make_engine`: asyncpg on Postgres, UTC sessions), the same transaction as start-up (`app.db.schema_transaction`), batch operations for SQLite, the comparison options (`app.db.COMPARE_OPTIONS`) |
| `versions/0001_baseline.py` | every table and index as `create_all` made them from the models before migrations |
| `versions/0002_align_legacy_schema.py` | repairs what a database made before migrations may lack (see below); does nothing on a new database |
| `script.py.mako` | the template for new revisions |

## At start-up

The backend and the worker both call `app.db.init_db` when they start. It runs everything in **one transaction**
(`app.db.schema_transaction`, which the command line uses too), so a failure rolls back everything and a database is
never left half migrated. On Postgres the transaction takes an advisory lock (`pg_advisory_xact_lock(4242001)`, the
same key the old `create_all` used): a second process waits for it, then finds the schema up to date.

SQLite (the tests and development) gets the same single transaction, begun explicitly because its Python driver would
run DDL outside one, and runs it with **foreign keys off**. SQLite runs most batch operations as a table copy: it
creates the new table, copies the rows, drops the old table and renames the new one. With foreign keys on, that `DROP
TABLE` deletes every row referring to the table (`ON DELETE CASCADE`) or nulls the reference (`SET NULL`): widening
`users.username` would delete every user's documents, conversations and messages. Instead, `PRAGMA foreign_key_check`
runs before the commit, and if any row refers to a row that doesn't exist, the migration is rolled back with an error
listing them. Foreign keys are on again afterwards. (Postgres alters tables in place and enforces the keys
throughout.)

| The database | What happens |
|---|---|
| empty | `upgrade head`: 0001 creates every table, 0002 has nothing to do. Log: `Database schema migrated from an empty database to revision 0002` |
| has an `alembic_version` table | `upgrade head` (nothing when it is at head: `Database schema up to date (revision 0002)`) |
| has tables but no `alembic_version` (made before migrations) | compared with the baseline, then stamped and aligned, or refused (next section) |

## Databases made before migrations

Before Alembic, every start ran `create_all`, which creates missing tables but never changes an existing one. Such a
database (the live one until its first start with migrations, and every backup taken before that) is compared with the
**baseline**: the schema of `0001_baseline.py` itself, built by `app.db.baseline_metadata`, not the current models, so
the comparison stays right after later migrations change the models. It uses Alembic's autogenerate comparison
(`compare_metadata`): tables, columns, types, nullability, server defaults, indexes, unique constraints, foreign keys
with their `ON DELETE` rules, and named `CHECK` constraints.

- **Repaired** (none of these touches a row), by stamping the database at 0001 and letting 0002 run:
  - a missing table (created as the baseline has it, with its indexes);
  - a missing index (a unique one only on a table that was missing, since existing rows could break it);
  - on Postgres, a foreign key whose `ON DELETE` (or `ON UPDATE`) rule differs from the baseline's: it is dropped and
    created again with the baseline's rule, under its old name. (SQLite can't alter a foreign key in place, so there it
    is refused.)
- **Refused**, anything else: a missing or extra column, a type, nullability or server default change, an extra table,
  index or constraint, a missing unique index or constraint on an existing table. The app logs one error listing each
  difference (`missing` = in the baseline but not in the database, `extra` = the other way round), changes nothing, and
  doesn't start (`app.db.SchemaMismatchError`). Bring the database to the baseline by hand (or restore a backup),
  then start again.

The live database's schema (`pg_dump -s`, 2026-10-25) was identical to the one `create_all` makes from the current
models, with no difference at all. Its first start with migrations, on 2026-10-25 after a verified backup, logged
"stamping revision 0001, 0 difference(s)" and then "migrated from 0001 to revision 0002"; every table kept its row
count, and `alembic check` found nothing. That had been rehearsed on a copy of the safety backup restored into a
throwaway Postgres: the real start-up path stamped and aligned it, every table's contents were unchanged (row counts and
a checksum of every table), and it came out with zero differences from the models.

## On a deploy

1. `touch .maintenance` in the repository root, so the host watchdog leaves the stack alone during the deploy.
2. Back up first, while the old release still runs: `scripts/backup.sh` from the repository root (the database, the
   uploads and Qdrant, verified; `../../scripts/README.md`). Its last line is `BACKUP_NAME=...`: keep the name, that
   backup is the rollback. (On the first deploy with migrations, its manifest says `"alembic_revision": null`.)
3. Deploy as usual (`docker compose up -d --build`, then `rm .maintenance`). The backend migrates while it starts; the
   worker starts after it and finds the schema up to date. Watch `docker compose logs backend` for
   `Database schema migrated ...`.
4. If the backend refuses to start, its log says why (the worker stops too, at once, with the same reason). Nothing
   was changed: fix the cause, or go back to the previous release.

## Rolling back

Migrations only go forward here: a deploy is undone by restoring the backup taken before it, into the previous release.

1. Check out the previous release and build it (`git checkout <release> && docker compose build`), so the backend
   image holds that release's migrations.
2. Restore the pre-deploy backup with that release's script: `scripts/restore.sh NAME --yes`. Before stopping anything
   it compares the backup's `alembic_revision` (in its manifest) with the image's migrations. The pre-deploy backup is
   at the previous release's newest revision, so nothing is migrated, and the previous release starts on it.

The order matters. Restoring with the new release's images still built would start the new backend on the restored
database and migrate it forward again, and the previous release could no longer start on it: a release doesn't start
on a database that a newer one migrated (Alembic stops with `Can't locate revision identified by ...`, changing
nothing). `restore.sh` refuses a backup at a revision the image doesn't know, and one the image would migrate forward
unless `--migrate` is given (for a new machine restoring an older backup into a newer release, say).

**Releases up to `c1da1d0`** (before migrations) are the exception: their `create_all` ignores `alembic_version`, so
they start on a database any later release migrated, without a warning, and recreate any table a migration dropped.
They have no `scripts/restore.sh` either. Go back to one only with a backup at revision 0002 (whose schema is exactly
what `create_all` makes) or from before migrations, restored with the current release's script:

```bash
scripts/restore.sh NAME --yes --no-start           # the current release's script; the app stays stopped
git checkout c1da1d0 && docker compose up -d --build
rm .maintenance                                    # restore.sh left it so the watchdog wouldn't start the app
```

The baseline refuses `alembic downgrade` (it would drop every table); write a `downgrade()` for a later migration only
when it is reviewed and tested like the upgrade, and still prefer the backup for real data.

## Writing a migration

1. Change the models (`app/models.py`). Every schema change needs a revision, a new table included: `create_all` no
   longer runs, so a model without one creates nothing (and the drift guard fails).
2. Generate the revision against a throwaway Postgres at the current head (start one as in `../tests/README.md`,
   "Postgres"; never point this at the app's database). The command line reads the app's settings, so give it
   placeholders for the required ones:

   ```bash
   cd backend
   export DATABASE_URL="postgresql+asyncpg://rag:$password@127.0.0.1:$port/scratch" \
     SECRET_KEY=$(openssl rand -hex 32) ADMIN_USERNAME=admin ADMIN_PASSWORD=placeholder-password LITELLM_MODE=PRODUCTION
   .venv/bin/alembic upgrade head
   .venv/bin/alembic revision --autogenerate --rev-id 0003 -m "add users.nickname"
   ```

3. Review it by hand. Autogenerate writes batch operations (`render_as_batch`), which SQLite runs as a table copy and
   Postgres as plain `ALTER`s. It can't know:
   - a rename: it writes a drop and an add, which loses the column's data. Use `alter_column(..., new_column_name=...)`;
   - how existing rows get a value: a new `NOT NULL` column needs a `server_default` (or a backfill with `op.execute`
     before the constraint);
   - data changes: write them with `op.execute` or `sa.table()`, never with the app's models, which keep changing;
   - constraint names: the models have no naming convention, so Postgres names unnamed constraints itself
     (`documents_owner_id_fkey`, `documents_stored_name_key`); name new constraints explicitly so a later migration can
     drop them, on SQLite too;
   - a `CHECK` constraint without a name, which isn't compared: name every new one;
   - foreign keys on SQLite: the migration runs with them off ("At start-up"), so deleting rows that others refer to
     cascades on Postgres but not on SQLite. Delete or update the referring rows first, in the same migration, or
     SQLite's `PRAGMA foreign_key_check` rolls the migration back.

   Keep the migration self-contained: it must not import `app.models` (a `TypeDecorator` such as `UTCDateTime` is
   written as its database type, `sa.DateTime(timezone=True)`).
4. Test it:
   - `.venv/bin/python -m pytest -q tests/test_migrations.py` (SQLite) and with `TEST_POSTGRES_URL` set,
     `-m postgres` (`../tests/README.md`). The drift guard (`test_the_models_and_the_migrations_agree`) fails while the
     models and the migrations differ; `test_the_migrations_make_exactly_what_create_all_makes` compares every table
     with what `create_all` makes; `test_a_migration_that_copies_a_table_keeps_the_rows_referring_to_it` shows how to
     test a revision with rows in place (the `later_migration` fixture);
   - `.venv/bin/alembic check` on the throwaway database after `upgrade head`: `No new upgrade operations detected`;
   - before deploying, restore the latest backup into a throwaway Postgres and start the new code against it, comparing
     the row counts before and after.
5. Never edit a migration that has run on a real database; write a new one.

The command line connects with the app's settings (`DATABASE_URL`, or the `POSTGRES_*` variables) and migrates in the
same transaction as start-up (the advisory lock; on SQLite foreign keys off and checked). `alembic upgrade` there
doesn't adopt a database made before migrations (it fails on the existing tables, changing nothing); the app's
start-up does that. Offline (`--sql`) mode isn't supported.
