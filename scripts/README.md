# Backups, restore and the watchdog

| Script | What |
|---|---|
| `scripts/backup.sh` | back up now (the `backup` service also does it daily) |
| `scripts/restore.sh NAME --yes` | restore a backup into this stack, after verifying it (destructive) |
| `scripts/backup/backup.sh` | the backup and restore steps, run inside the `backup` and `restore` services, never on the host |
| `scripts/watchdog/` | an optional host watchdog that restarts Docker and the stack ([README](watchdog/README.md)) |

## What a backup holds, and what it doesn't

A backup is a folder `BACKUP_DIR/<UTC time>-<label>` (default `./backups`, git-ignored). The label is `scheduled`,
`startup`, `manual` or `pre-restore`. The folder is mode 700 and its files 600:

| File | Content |
|---|---|
| `postgres.dump` | `pg_dump -Fc` of the database: users, conversations, settings, usage, encrypted Gmail tokens |
| `postgres.rows` | the rows of every table, counted from the dump itself |
| `uploads.tar.gz`, `uploads.sha256` | every uploaded or generated file, and each file's checksum |
| `qdrant/<collection>.snapshot`, `qdrant.collections` | Qdrant snapshots of the app's collections (`rag_chunks_<model id>`) and their point counts, so a restore needs no paid re-embedding |
| `manifest.json` | the time, the app version (`APP_VERSION`), the Alembic revision, rows per table, file counts and bytes, points per collection, and each file's sha256 and size |
| `SHA256SUMS` | the same checksums, for `sha256sum -c` |

**`.env` is not in any backup.** It holds `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, the provider keys and the passwords.
Keep a copy in a password manager, apart from the backups:

- With a different `TOKEN_ENCRYPTION_KEY`, Gmail tokens can't be decrypted, and every user must reconnect Gmail.
- With a different `SECRET_KEY`, everyone is signed out (nothing is lost).
- `POSTGRES_PASSWORD` only needs to match the new stack's own database.

Redis isn't backed up. It holds only short-lived state (the ingestion queue, throttles), and the worker re-queues
unfinished documents from Postgres.

Backups hold personal data. Keep `BACKUP_DIR` outside the Docker build contexts (`./backend`, `./frontend`) and out of
git, and never send a backup anywhere unencrypted.

## Schedule, retention, status

The `backup` service backs up daily at `BACKUP_SCHEDULE`. The value is in UTC, `HH:MM`, with several times separated
by commas; the default is `02:30`, which is 08:00 in India. When it starts and no backup began since the latest
scheduled time (there is none yet, or the stack was down then), a `startup` backup follows `BACKUP_START_DELAY_SECONDS`
(120) later; safety (`pre-restore`) backups don't count. While that backup is due, the monitor reports a missing or old
backup as `unknown`, so a first deploy or a stack back from a long stop sends no alert.

Each backup is written to `.tmp-<name>`, then verified:

- every snapshot against the checksum Qdrant reported, as it is downloaded;
- every checksum in `SHA256SUMS`, and the same ones in `manifest.json`;
- `pg_restore --list` on the dump, and its rows counted again against `postgres.rows`;
- every archived file, extracted into a scratch folder, against `uploads.sha256`.

Only then is it renamed. A failed backup is deleted and reported.

Retention keeps every backup younger than 48 hours; manual and pre-restore backups for `BACKUP_KEEP_DAILY` days; of the
scheduled and start-up backups, the newest of each of the last `BACKUP_KEEP_DAILY` (7) days and of the last
`BACKUP_KEEP_WEEKLY` (4) weeks (Monday to Sunday, UTC) that have one; and always the newest backup.

One backup or restore runs at a time. The service writes its status (last success, last failure, size, schedule, free
disk) to the `backupstatus` volume. The backend and the monitor read it. Admin → System shows it, and the monitor
alerts when the last good backup is older than `BACKUP_MAX_AGE_HOURS` (26) or the last attempt failed.

```bash
scripts/backup.sh                                  # back up now; the last line is BACKUP_NAME=...
docker compose run --rm restore list               # the backups and their sizes
docker compose run --rm restore verify NAME        # check one without restoring it
docker compose run --rm restore show NAME          # its manifest
docker compose logs backup                         # what the scheduled backups did
```

On Linux, root owns the backup files. Set `BACKUP_UID` and `BACKUP_GID` (from `id -u` and `id -g`) to give them to
the user that copies them off the machine. On Docker Desktop they show as yours anyway.

## Restore

```bash
scripts/restore.sh NAME            # verifies the backup and says what --yes would do; changes nothing
scripts/restore.sh NAME --yes      # restores ("latest" works as a name too)
```

The restore refuses without `--yes`, and it refuses a backup that fails verification. It also checks the backup's
schema against this release's, before stopping anything: the backend migrates the database to its newest revision when
it starts (`backend/migrations/README.md`), so the restore compares the manifest's `alembic_revision` with the backend
image's migrations (`alembic history`).

| The backup's revision | What the restore does |
|---|---|
| this release's newest | restores |
| older, or none (made before migrations) | refuses (exit 2) unless `--migrate`: starting this release would migrate the restored database forward, and the release that made the backup couldn't use it any more |
| unknown to this release (a newer release made the backup) | refuses (exit 1): this backend wouldn't start on it. Check out and build the release that made the backup |

`--no-start` restores and leaves the app stopped (no `--migrate` needed, since nothing starts; it skips step 6
below, which runs this release's code), and leaves the maintenance file `.maintenance` in place, saying `hold`, so the
watchdog doesn't start this release. Start the release you want, then delete the file.

Then it:

1. Stops the app.
2. Takes a `pre-restore` safety backup of the current data (`--no-safety-backup` skips it when there is nothing to
   keep). Restoring the safety backup undoes the restore.
3. Prepares every part next to the current one and checks it: Postgres in a fresh database (`<db>_restoring`; every
   table's row count must equal the backup's), the uploads in a staging folder (every file's checksum must match), and
   each Qdrant collection as `<name>-restoring` from its snapshot (each upload tried up to 3 times; the point counts
   must match). It also keeps a snapshot of every current collection inside Qdrant (so Qdrant needs room for a second
   copy of its collections for the duration). If any part fails, what was prepared is removed and nothing is changed.
4. Swaps in Qdrant's collections, then the uploads (the current files wait in `.restore-previous`), last the database
   (renamed in one transaction), then removes the collections the backup doesn't have. If a step of the swap fails, it
   is undone: the files come back from `.restore-previous` and each collection from its kept snapshot, and the
   database, swapped last, is still the current one.
5. Clears Redis's ingestion queue.
6. Migrates the database to this release's schema, as the backend's start would (`--migrate`), then reconciles every
   document with its file and vectors (`python -m app.ops.reconcile`): the parts of a backup are taken one after the
   other while the app runs, so a document added or deleted meanwhile can come back with only some of them. A document
   whose file is missing (or has another size) is marked failed and asks for the upload again; a ready document whose
   vectors don't number its chunks is re-indexed; vectors of deleted or failed documents are removed, and so are files
   no document has. It names documents by id only.
7. Starts the app and waits until `GET /api/health/ready` answers.

If preparing fails, or a swap fails and is undone, nothing was changed and the app starts again (exit 1). Only if
undoing a swap fails too does the stack hold parts of two states: the app stays stopped, the maintenance file says
`hold` so the watchdog leaves it alone, and the script prints the commands to restore the safety backup or to try
again.

**A new machine** (the old one is gone):

1. Clone the repo.
2. Put the saved `.env` back.
3. Copy the backup folder into `./backups` (or point `BACKUP_DIR` at it).
4. Run `docker compose up -d --build`, then `scripts/restore.sh latest --yes --no-safety-backup` (add `--migrate` if
   the restore says this release would migrate the backup's schema).

**Rolling back a deploy**: check out and build the previous release first (`git checkout <release> && docker compose
build`), then restore the backup taken before the deploy with that release's script (`scripts/restore.sh NAME --yes`).
Restoring with the new release's images would migrate the restored database forward again. Releases up to `c1da1d0`
predate migrations and this script; going back to one is in `backend/migrations/README.md`, "Rolling back".

The restore drill in `integration/tests/test_14_backup_restore.py` runs on every CI run: it backs up, destroys the
stack with `down -v`, starts an empty one and restores into it. After the restore, the logins work, documents download
with the same bytes, search returns the same passages without re-embedding, conversations are there, and Gmail tokens
decrypt. It also covers the hard cases:

- a restore over a stack that changed since the backup (a new and a deleted document, re-indexing into another
  embedding model's collection, an extra collection): the stack comes back exactly as backed up;
- a backup taken while a document was deleted and another added: the restore reconciles both;
- a restore with `--migrate` of a backup at an older revision: reconciling starts only after the migration;
- failure injection, after which the stack must hold exactly its data from before, running: a corrupt or missing
  snapshot, a corrupt archive and a checksum list that disagrees with the archive (verification refuses them before
  anything stops), a dump that fails to restore and a snapshot Qdrant refuses half-way through (refused before anything
  is replaced), and the swap failing at the collections, the uploads and the database (undone);
- 24 snapshot uploads to a Qdrant limited to half a CPU, each of which must succeed the first time.

## Off-site copies

A backup on the same disk as the app doesn't survive losing that disk. Copy `BACKUP_DIR` somewhere else after the
daily backup, for example with a cron job or systemd timer at 03:30 UTC, an hour after the 02:30 default. Encrypt
anything that leaves your control:

- **rclone to S3, Backblaze B2, Google Drive, …**: create a `crypt` remote over the storage remote (`rclone config`),
  so files are encrypted before upload, then copy:
  `rclone copy --max-age 48h ./backups b2-crypt:rag-backups`.
  `rclone sync` would also mirror the local retention; use a bucket lifecycle rule if you want a longer history off-site.
- **An external drive**: use an encrypted volume (APFS encrypted on macOS, LUKS on Linux), then
  `rsync -a --delete ./backups/ /Volumes/Backup/rag-backups/`.
- **restic or borg**: both encrypt and deduplicate, so many daily copies take little space:
  `restic -r <repo> backup ./backups`.

Test a restore from the off-site copy now and then, on a spare machine or a throwaway Compose project, never on the
live stack.
