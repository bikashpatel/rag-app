#!/usr/bin/env bash
# Runs the integration suite against an isolated copy of the whole stack (see integration/README.md):
# generates a throwaway env file, builds and starts the rag-it Compose project with the fake model and Google
# servers, runs pytest on integration/tests, and always tears the stack down (volumes included) unless --keep.
#
#   integration/run.sh                 # build, start, test, tear down
#   integration/run.sh --keep          # leave the stack up afterwards (integration/stack.sh down -v to remove it)
#   integration/run.sh --no-build      # reuse the images of an earlier run
#   integration/run.sh --rmi           # also remove the images built for it
#   integration/run.sh -- -k chat -x   # arguments after -- go to pytest
#
# Exit code: pytest's; 2 when the stack couldn't be set up; 3 when the tests passed but the teardown left something.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STACK="$ROOT/integration/stack.sh"
ENV_FILE="$ROOT/integration/.run/it.env"
export IT_PROJECT=${IT_PROJECT:-rag-it}
PY=${IT_PYTHON:-$ROOT/backend/.venv/bin/python}
LOGS=${IT_LOGS:-$ROOT/integration/.run/stack-logs.txt}  # written when a run fails; kept until the next failure
KEEP=0
BUILD=1
RMI=0
PYTEST_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP=1 ;;
    --no-build) BUILD=0 ;;
    --rmi) RMI=1 ;;
    --) shift; PYTEST_ARGS+=("$@"); break ;;
    -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option $1 (pytest arguments go after --)" >&2; exit 2 ;;
  esac
  shift
done

if [[ ! $IT_PROJECT =~ ^rag-it(-[a-z0-9]+)*$ ]]; then
  echo "Refusing the Compose project '$IT_PROJECT' (only rag-it or rag-it-<suffix>)" >&2
  exit 2
fi
if [ -d "$HOME/.docker/bin" ]; then
  export PATH="$HOME/.docker/bin:$PATH"
fi
if ! docker version --format '{{.Server.Version}}' > /dev/null 2>&1; then
  echo "Docker isn't running (docker version failed)" >&2
  exit 2
fi
if [ ! -x "$PY" ]; then
  echo "No Python at $PY. Create backend/.venv (see backend/tests/README.md) or set IT_PYTHON." >&2
  exit 2
fi

# Containers, volumes and networks of the project still there, one "<kind> <name>" per line.
leftovers() {
  local label="label=com.docker.compose.project=$IT_PROJECT"
  docker ps -a --filter "$label" --format 'container {{.Names}}' &&
    docker volume ls --filter "$label" --format 'volume {{.Name}}' &&
    docker network ls --filter "$label" --format 'network {{.Name}}'
}

# The backups of a run (BACKUP_DIR=integration/.run/backups) are removed from inside a container of the backup image
# (on Linux root owns them), which mounts only that folder.
clear_backups() {
  local image
  image=$(sed -n 's/^  image: \(postgres:.*\)$/\1/p' "$ROOT/docker-compose.yml" | head -1)
  mkdir -p "$ROOT/integration/.run/backups"
  docker run --rm --network none -v "$ROOT/integration/.run/backups:/backups" "$image" \
    find /backups -mindepth 1 -delete > /dev/null 2>&1 || echo "Couldn't remove the test backups in integration/.run/backups" >&2
}

save_logs() {
  mkdir -p "$(dirname "$LOGS")"
  "$STACK" logs --no-color > "$LOGS" 2>&1 || true
  echo "Stack logs saved to $LOGS" >&2
}

if ! left=$(leftovers); then
  echo "docker couldn't list the $IT_PROJECT project" >&2
  exit 2
fi
if [ -n "$left" ]; then
  echo "Removing a leftover $IT_PROJECT stack"
  "$STACK" down -v --remove-orphans || exit 2
  if ! left=$(leftovers) || [ -n "$left" ]; then
    printf 'The leftover %s stack is still there:\n%s\n' "$IT_PROJECT" "${left:-(docker failed to list it)}" >&2
    exit 2
  fi
fi
"$PY" "$ROOT/integration/gen_env.py" || exit 2
clear_backups

teardown() {
  local status=$? left
  trap - EXIT INT TERM
  if [ "$KEEP" = 1 ]; then
    local port
    port=$(sed -n 's/^APP_PORT=//p' "$ENV_FILE")
    echo "--keep: the $IT_PROJECT stack is still up (app on http://127.0.0.1:$port)."
    echo "Remove it with: integration/stack.sh down -v --remove-orphans"
    exit "$status"
  fi
  echo "Tearing down the $IT_PROJECT stack"
  clear_backups
  local down=(down -v --remove-orphans)
  if [ "$RMI" = 1 ]; then
    down+=(--rmi local)
  fi
  "$STACK" "${down[@]}" || echo "docker compose down failed" >&2
  if ! left=$(leftovers) || [ -n "$left" ]; then
    printf 'The teardown left this behind (remove it with integration/stack.sh down -v --remove-orphans):\n%s\n' \
      "${left:-(docker failed to list the project)}" >&2
    if [ "$status" = 0 ]; then
      status=3
    fi
  fi
  exit "$status"
}
trap teardown EXIT
trap 'exit 130' INT TERM

BUILD_SECONDS=0
if [ "$BUILD" = 1 ]; then
  echo "Building the images"
  SECONDS=0
  if ! "$STACK" build; then
    echo "The build failed" >&2
    exit 2
  fi
  BUILD_SECONDS=$SECONDS
fi

echo "Starting the $IT_PROJECT stack"
SECONDS=0
if ! "$STACK" up -d --wait --wait-timeout 300; then
  echo "The stack didn't become healthy" >&2
  "$STACK" ps -a >&2 || true
  save_logs
  exit 2
fi
UP_SECONDS=$SECONDS

echo "Running the tests"
SECONDS=0
set +e
"$PY" -m pytest -c "$ROOT/integration/pytest.ini" --rootdir "$ROOT/integration" "$ROOT/integration/tests" \
  ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
RESULT=$?
set -e
TEST_SECONDS=$SECONDS

if [ "$RESULT" != 0 ]; then
  save_logs
fi
echo "Timings: build ${BUILD_SECONDS}s, start ${UP_SECONDS}s, tests ${TEST_SECONDS}s (pytest exit code $RESULT)"
exit "$RESULT"
