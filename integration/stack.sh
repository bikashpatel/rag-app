#!/usr/bin/env bash
# docker compose for the integration stack only, e.g. integration/stack.sh ps, integration/stack.sh logs backend.
# Always a rag-it project with compose.it.yml and the generated env file: never the live rag-app project, never .env.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT=${IT_PROJECT:-rag-it}
ENV_FILE="$ROOT/integration/.run/it.env"
MARKER=rag.it.stack

if [[ ! $PROJECT =~ ^rag-it(-[a-z0-9]+)*$ ]]; then
  echo "integration/stack.sh: refusing the Compose project '$PROJECT' (only rag-it or rag-it-<suffix>)" >&2
  exit 2
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "integration/stack.sh: $ENV_FILE is missing; run integration/run.sh (or integration/gen_env.py) first" >&2
  exit 2
fi
if [ -d "$HOME/.docker/bin" ]; then
  PATH="$HOME/.docker/bin:$PATH"
fi

# Everything already in the project must carry compose.it.yml's marker label: another stack that happens to use the
# name is never changed or removed.
label="label=com.docker.compose.project=$PROJECT"
if ! listed=$(
  docker ps -a --filter "$label" --format "container {{.Names}} {{.Label \"$MARKER\"}}" &&
    docker volume ls --filter "$label" --format "volume {{.Name}} {{.Label \"$MARKER\"}}" &&
    docker network ls --filter "$label" --format "network {{.Name}} {{.Label \"$MARKER\"}}"
); then
  echo "integration/stack.sh: docker couldn't list the $PROJECT project" >&2
  exit 2
fi
foreign=$(awk 'NF && $3 != "1" { print "  " $1 " " $2 }' <<< "$listed")
if [ -n "$foreign" ]; then
  echo "integration/stack.sh: refusing: the Compose project '$PROJECT' has objects compose.it.yml didn't create:" >&2
  echo "$foreign" >&2
  exit 2
fi

# Interpolation must come only from the env file: the shell's values would win over it.
exec env -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_DB -u APP_PORT -u WORKER_MEM_LIMIT \
  -u MAX_UPLOAD_MB_HARD -u IT_FAKE_LLM_PORT -u IT_FAKE_GOOGLE_PORT -u APP_VERSION -u QDRANT_API_KEY \
  -u QDRANT_COLLECTION_PREFIX -u BACKUP_DIR -u BACKUP_SCHEDULE -u BACKUP_KEEP_DAILY -u BACKUP_KEEP_WEEKLY \
  -u BACKUP_START_DELAY_SECONDS -u BACKUP_UID -u BACKUP_GID \
  -u COMPOSE_FILE -u COMPOSE_PROJECT_NAME -u COMPOSE_ENV_FILES -u COMPOSE_PROFILES \
  docker compose -p "$PROJECT" --project-directory "$ROOT" \
  -f "$ROOT/docker-compose.yml" -f "$ROOT/integration/compose.it.yml" --env-file "$ENV_FILE" "$@"
