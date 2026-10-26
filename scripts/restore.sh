#!/usr/bin/env bash
# Restores a backup into this stack (scripts/README.md). DESTRUCTIVE: the database, the uploads and Qdrant's
# collections are replaced by the backup's.
#
#   scripts/restore.sh NAME|latest                     # verifies the backup and says what --yes would do
#                                                      # (latest: the newest backup, a safety backup included)
#   scripts/restore.sh NAME|latest --yes               # takes a safety backup of the current data, then restores
#   scripts/restore.sh NAME|latest --yes --no-safety-backup   # when the current data is lost anyway
#   scripts/restore.sh NAME|latest --yes --migrate     # when starting this release migrates the backup's schema
#   scripts/restore.sh NAME|latest --yes --no-start    # leaves the app stopped, to start another release after
#
# Steps: verify the backup (checksums, the dump and its rows, every archived file) and refuse if it fails; compare
# the backup's Alembic revision with this release's migrations (the backend image's), refusing one they don't know,
# and one they would migrate unless --migrate or --no-start is given; stop the app; take a safety backup (a
# "pre-restore" one); prepare every part next to the current one and check it (the database's row counts, the files'
# checksums, the collections' point counts), changing nothing if one fails; swap in Qdrant, the files and last the
# database, undoing the swap if a step of it fails; clear Redis's ingestion queue; migrate the database to this
# release's schema and reconcile documents with their files and vectors (python -m app.ops.reconcile); start the app
# and wait until it is ready. With --no-start it stops after clearing the queue, and the watchdog stays off until you
# delete the maintenance file.
#
# Not in any backup: .env (SECRET_KEY, TOKEN_ENCRYPTION_KEY, provider keys, passwords). Restore it from where you keep
# it (a password manager). Without the same TOKEN_ENCRYPTION_KEY users must reconnect Gmail; a different SECRET_KEY
# signs everyone out. RAG_COMPOSE replaces "docker compose" (e.g. integration/stack.sh); the watchdog
# (scripts/watchdog) leaves the stack alone while RAG_MAINTENANCE_FILE (default .maintenance) exists: it holds this
# script's process id while it runs, and "hold" after a --no-start restore.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MAINTENANCE=${RAG_MAINTENANCE_FILE:-$ROOT/.maintenance}
APP_SERVICES=(frontend monitor backup worker backend)
if [ -d "$HOME/.docker/bin" ]; then
  PATH="$HOME/.docker/bin:$PATH"
fi

name="" yes=0 safety=1 migrate=0 start=1
for argument in "$@"; do
  case "$argument" in
    --yes) yes=1 ;;
    --no-safety-backup) safety=0 ;;
    --migrate) migrate=1 ;;
    --no-start) start=0 ;;
    -h | --help) sed -n '2,26p' "$0"; exit 0 ;;
    -*) echo "Unknown option $argument" >&2; exit 2 ;;
    *)
      [ -z "$name" ] || { echo "Give one backup name" >&2; exit 2; }
      name=$argument
      ;;
  esac
done
if [ -z "$name" ]; then
  echo "Usage: scripts/restore.sh NAME|latest [--yes] [--no-safety-backup] [--migrate] [--no-start]" >&2
  echo "List the backups with: docker compose run --rm restore list" >&2
  exit 2
fi

compose() {
  if [ -n "${RAG_COMPOSE:-}" ]; then
    "$RAG_COMPOSE" "$@"
  else
    (cd "$ROOT" && docker compose "$@")
  fi
}
# A command to suggest, as this script would run it (RAG_COMPOSE included).
suggest() {
  local prefix=""
  [ -z "${RAG_COMPOSE:-}" ] || prefix="RAG_COMPOSE=$(printf '%q' "$RAG_COMPOSE") "
  echo "$prefix$*"
}
compose_command() { if [ -n "${RAG_COMPOSE:-}" ]; then echo "$RAG_COMPOSE"; else echo "docker compose"; fi; }
step() { echo "==> $*"; }
in_restore() { compose run --rm --no-deps -T restore "$@"; }

step "Verifying the backup $name"
compose up -d --wait --wait-timeout 180 postgres redis qdrant > /dev/null
if [ "$name" = latest ]; then
  name=$(in_restore list | tail -1 | cut -d' ' -f1)
  [ -n "$name" ] || { echo "There is no backup to restore." >&2; exit 1; }
  echo "The latest backup is $name"
fi
if ! in_restore verify "$name"; then
  echo "Refusing to restore: the backup $name failed verification (see above)." >&2
  exit 1
fi

# What starting this release does with the backup's schema: the backend migrates the database to its newest revision
# at start-up, and refuses a revision it doesn't know (backend/migrations/README.md).
step "Checking the backup's schema against this release's migrations"
manifest=$(in_restore show "$name")
revision=$(sed -n 's/^  "alembic_revision": "\(.*\)",$/\1/p' <<< "$manifest")
made_by=$(sed -n 's/^  "app_version": "\(.*\)",$/\1/p' <<< "$manifest")
made_by=${made_by:+ (made by app version $made_by)}
# stderr is dropped: a backend that can't load its settings would print them
history=$(compose run --rm --no-deps -T backend alembic history 2> /dev/null) || history=""
head=$(sed -n 's/^.* -> \([^ ,]*\) (head).*$/\1/p' <<< "$history")
migrates=""
if [ -z "$head" ]; then
  echo "Couldn't read this release's migrations (alembic history failed in the backend image)." >&2
  if [ "$start" = 1 ]; then
    echo "Refusing to restore: build the backend image ($(compose_command) build backend), or pass --no-start." >&2
    exit 1
  fi
elif [ "$revision" = "$head" ]; then
  echo "The backup's schema is at revision $head, this release's newest: nothing to migrate."
elif [ -n "$revision" ] && ! sed -n 's/^.* -> \([^ ,]*\).*$/\1/p' <<< "$history" | grep -Fqx "$revision"; then
  echo "Refusing to restore: this release doesn't know the backup's schema revision $revision$made_by, so its backend" \
    "wouldn't start on it. Check out and build the release that made the backup, then restore." >&2
  exit 1
elif [ -n "$revision" ]; then
  migrates="The backup's schema is at revision $revision: starting this release migrates it to revision $head."
else
  migrates="The backup was made before migrations: starting this release adopts its schema and migrates it to"
  migrates="$migrates revision $head."
fi
[ -z "$migrates" ] || echo "$migrates"
again=(scripts/restore.sh "$name" --yes)
[ "$safety" = 1 ] || again+=(--no-safety-backup)
[ -z "$migrates" ] || [ "$start" = 0 ] || again+=(--migrate)
[ "$start" = 1 ] || again+=(--no-start)

if [ "$yes" != 1 ]; then
  stays=""
  [ "$start" = 1 ] || stays=$'\nWith --no-start the app stays stopped afterwards.'
  cat << EOF

The backup $name is intact. Restoring it REPLACES this stack's database, uploaded files and vector index with the
backup's, and stops the app for the duration.$([ $safety = 1 ] && printf '\nA safety backup of the current data is taken first.')
.env is not part of a backup: keep the same TOKEN_ENCRYPTION_KEY, or every user reconnects Gmail.$stays

Refusing without --yes. To restore: $(suggest "${again[@]}")
EOF
  exit 2
fi
if [ -n "$migrates" ] && [ "$migrate" = 0 ] && [ "$start" = 1 ]; then
  cat >&2 << EOF
Refusing without --migrate: the restored database would be migrated forward when the app starts, and the release that
made the backup couldn't use it any more. Rolling back a deploy? Check out and build the previous release first, then
restore (scripts/README.md, "Rolling back a deploy"). To restore into this release and migrate: $(suggest "${again[@]}")
EOF
  exit 2
fi

echo "pid=$$" > "$MAINTENANCE"
stopped=0 restored=0 held=0 safety_name="" # held: the maintenance file stays until the operator deletes it
finish() {
  if [ "$held" = 1 ]; then
    echo hold > "$MAINTENANCE"
  else
    rm -f "$MAINTENANCE"
  fi
  if [ "$stopped" = 1 ] && [ "$restored" = 0 ] && [ "$held" = 0 ]; then
    echo "The app is stopped. Once it holds what you want, start it with: $(compose_command) up -d" >&2
  fi
}
trap finish EXIT
restart() {
  compose up -d --wait --wait-timeout 300 "${APP_SERVICES[@]}" || true
  stopped=0
}

step "Stopping the app"
compose stop "${APP_SERVICES[@]}"
stopped=1

if [ "$safety" = 1 ]; then
  step "Taking a safety backup of the current data"
  if ! output=$(compose run --rm --no-deps -T backup once pre-restore); then
    echo "$output"
    echo "The safety backup failed, so nothing was restored. Fix it, or pass --no-safety-backup if the current data" \
      "is lost anyway. Starting the app again." >&2
    restart
    exit 1
  fi
  safety_name=$(sed -n 's/^BACKUP_NAME=//p' <<< "$output")
  echo "Safety backup: $safety_name (restore it the same way to undo this restore)"
fi

step "Preparing every part of $name, then swapping them in"
set +e
in_restore restore "$name"
status=$?
set -e
if [ "$status" = 1 ] || [ "$status" = 3 ]; then
  echo "Nothing was changed; starting the app again." >&2
  restart
  exit 1
fi
if [ "$status" != 0 ]; then
  echo "The restore stopped half-way and couldn't put everything back (see above), so the stack holds parts of two" \
    "states. The app stays stopped." >&2
  if [ -n "$safety_name" ]; then
    echo "To go back to the data from before: $(suggest scripts/restore.sh "$safety_name" --yes --no-safety-backup)" >&2
  fi
  echo "To try this restore again: $(suggest scripts/restore.sh "$name" --yes --no-safety-backup)" >&2
  echo "Until you delete $MAINTENANCE, the watchdog leaves the stack alone." >&2
  held=1
  exit 1
fi

step "Clearing Redis's ingestion queue (the worker re-queues unfinished documents from Postgres)"
compose exec -T redis sh -c 'redis-cli DEL rag:ingest_queue rag:worker:heartbeat > /dev/null &&
  redis-cli --scan --pattern "rag:ingest_attempts:*" | xargs -r redis-cli DEL > /dev/null'

if [ "$start" = 0 ]; then # reconciling runs this release's code, which may not be the one started next
  restored=1 held=1
  echo
  echo "Restored $name: row counts, file checksums and point counts equal the backup's. The app is stopped" \
    "(--no-start)."
  echo "Build and start the release you want ($(compose_command) up -d --build), then delete $MAINTENANCE: until then" \
    "the watchdog leaves the stack alone."
  exit 0
fi

# The schema first, as the backend's start would migrate it: reconciling reads it with this release's models.
step "Migrating the database to this release's schema, then reconciling documents with their files and vectors"
reconciled=1
if ! compose run --rm --no-deps -T backend python -m app.ops.reconcile; then
  reconciled=0
  echo "Documents couldn't be checked against their files and vectors (see above). Some may show as ready without" \
    "them: re-index them in Admin -> Documents." >&2
fi

step "Starting the app"
compose up -d --wait --wait-timeout 300 "${APP_SERVICES[@]}"
# Readiness, asked for up to a minute: a check failing for a moment right after the start doesn't fail the restore
ready=$(compose exec -T backend python -c "
import time, urllib.error, urllib.request
for attempt in range(30):
    try:
        status = urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=10).status
    except urllib.error.HTTPError as error:
        status = error.code
    if status == 200:
        break
    time.sleep(2)
print(status)
")
if [ "$ready" != 200 ]; then
  echo "The app started but isn't ready (readiness answered $ready); see Admin -> System." >&2
  exit 1
fi
restored=1
echo
echo "Restored $name: row counts, file checksums and point counts equal the backup's, and the app is ready."
[ "$reconciled" = 1 ] || exit 1
