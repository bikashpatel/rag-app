#!/usr/bin/env bash
# Backs up the stack now (scripts/README.md): Postgres, the uploads and Qdrant's collections into BACKUP_DIR, verified.
#
#   scripts/backup.sh            # prints the new backup's name on the last line (BACKUP_NAME=...)
#
# Runs in the backup service's container (docker compose exec when it is running, else a one-off docker compose run).
# Never reads or prints .env itself: Docker Compose reads it. RAG_COMPOSE replaces "docker compose" (from the repository
# root), e.g. RAG_COMPOSE=integration/stack.sh for the integration stack. Exit code 3: another backup is running.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ -d "$HOME/.docker/bin" ]; then
  PATH="$HOME/.docker/bin:$PATH"
fi

case "${1:-}" in
  "") ;;
  -h | --help) sed -n '2,9p' "$0"; exit 0 ;;
  *) echo "Usage: scripts/backup.sh" >&2; exit 2 ;;
esac

compose() {
  if [ -n "${RAG_COMPOSE:-}" ]; then
    "$RAG_COMPOSE" "$@"
  else
    (cd "$ROOT" && docker compose "$@")
  fi
}

# The manifest's app version: APP_VERSION from this shell, else the stack's, else this checkout's `git describe`.
versions=(-e "APP_VERSION_FALLBACK=$(git -C "$ROOT" describe --always --dirty 2> /dev/null || true)")
if [ -n "${APP_VERSION:-}" ]; then
  versions+=(-e "APP_VERSION=$APP_VERSION")
fi
if compose ps --status running --services 2> /dev/null | grep -qx backup; then
  compose exec -T "${versions[@]}" backup /bin/sh /opt/backup/backup.sh once manual
else
  compose run --rm --no-deps -T "${versions[@]}" backup once manual
fi
