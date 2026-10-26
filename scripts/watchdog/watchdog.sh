#!/usr/bin/env bash
# Host-level watchdog for the RAG Assistant (scripts/watchdog/README.md). The monitor service can't notice Docker
# itself stopping (Docker Desktop quitting, the Mac restarting); this runs outside Docker, every 5 minutes, from launchd
# (macOS) or a systemd timer (Linux):
#
#   1. GET $RAG_APP_URL (default http://127.0.0.1:8080/api/health). Answering: done (and "resolved" if it was down).
#   2. Otherwise ask again $RAG_WATCHDOG_RETRY_SECONDS (30) later: one slow answer restarts nothing.
#   3. Still nothing: start Docker (Docker Desktop on macOS) if it isn't running, then start the stack's containers as
#      they are (`docker compose start`, then `docker compose restart` if that wasn't enough) in $RAG_APP_DIR. It never
#      builds or recreates a container, so it can't deploy a checkout that wasn't deployed. Whether that worked is
#      decided by asking the app again, for up to $RAG_WATCHDOG_WAIT_SECONDS (300).
#   4. Alert through the same channels as the monitor (ALERT_WEBHOOK_URL, ALERT_NTFY_URL, ALERT_SMTP_* in .env): once
#      when it went down (restarted or not), every ALERT_REPEAT_HOURS while it stays down, and when it is back.
#
#   watchdog.sh            # one check (what launchd and systemd run)
#   watchdog.sh --test     # send a test alert to every configured channel
#
# It never prints .env values. It does nothing while $RAG_MAINTENANCE_FILE (default $RAG_APP_DIR/.maintenance) exists:
# deploys set it (README.md section 8), and so does scripts/restore.sh, writing "pid=<its process id>" while it runs
# and "hold" when it leaves the app stopped on purpose. A maintenance file older than $RAG_MAINTENANCE_MAX_MINUTES (120)
# is taken as left behind and ignored, unless it says "hold" or names a process that still runs. State lives in
# $RAG_WATCHDOG_STATE (default ~/.rag-watchdog-state).
set -uo pipefail

APP_DIR=${RAG_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
URL=${RAG_APP_URL:-http://127.0.0.1:8080/api/health}
MAINTENANCE=${RAG_MAINTENANCE_FILE:-$APP_DIR/.maintenance}
MAINTENANCE_MAX_MINUTES=${RAG_MAINTENANCE_MAX_MINUTES:-120}
STATE=${RAG_WATCHDOG_STATE:-$HOME/.rag-watchdog-state}
RETRY_SECONDS=${RAG_WATCHDOG_RETRY_SECONDS:-30}
WAIT_SECONDS=${RAG_WATCHDOG_WAIT_SECONDS:-300}
ENV_FILE=$APP_DIR/.env
DOCKER_START_SECONDS=180
# Appended: launchd's and systemd's PATH lack Docker Desktop's folder; one already on PATH comes first.
PATH="$PATH:$HOME/.docker/bin:/usr/local/bin:/opt/homebrew/bin"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) watchdog: $*"; }

# A value from .env as Compose reads it (the last assignment, surrounding quotes removed); never printed.
setting() {
  local value
  value=$( [ -r "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -1)
  value=${value%$'\r'}
  if [[ $value == \"*\" || $value == \'*\' ]]; then
    value=${value:1:${#value}-2}
  fi
  printf '%s' "${value:-${2:-}}"
}

json_text() { # a JSON string of $1 (the messages are this script's own sentences)
  local text=${1//\\/\\\\}
  text=${text//\"/\\\"}
  printf '"%s"' "${text//$'\n'/\\n}"
}

# curl reads the URL and any token from its config on stdin, so they never show in the process list.
post() { # config-lines curl-arguments...
  local config=$1
  shift
  curl -fsS -m 30 -o /dev/null -K - "$@" <<< "$config" 2> /dev/null
}

send() { # kind title body
  local kind=$1 title=$2 body=$3 sent=0 webhook ntfy token host config priority=default
  webhook=$(setting ALERT_WEBHOOK_URL)
  if [ -n "$webhook" ]; then
    if post "url = \"$webhook\"" -H "Content-Type: application/json" \
      --data "{\"text\": $(json_text "$title"$'\n'"$body")}"; then
      sent=1
    else
      log "The webhook alert failed"
    fi
  fi
  ntfy=$(setting ALERT_NTFY_URL)
  if [ -n "$ntfy" ]; then
    config="url = \"$ntfy\""
    token=$(setting ALERT_NTFY_TOKEN)
    [ -n "$token" ] && config+=$'\n'"header = \"Authorization: Bearer $token\""
    [ "$kind" = problem ] && priority=high
    if post "$config" -H "Title: $title" -H "Priority: $priority" --data-binary "$body"; then
      sent=1
    else
      log "The ntfy alert failed"
    fi
  fi
  host=$(setting ALERT_SMTP_HOST)
  if [ -n "$host" ] && [ -n "$(setting ALERT_SMTP_FROM)" ] && [ -n "$(setting ALERT_SMTP_TO)" ]; then
    if send_email "$host" "$title" "$body"; then sent=1; else log "The email alert failed"; fi
  fi
  [ $sent = 1 ] || log "No alert channel delivered: $title"
}

send_email() { # host title body: curl's SMTP client; the password goes through a private netrc file, not argv
  local host=$1 title=$2 body=$3 port security from address user scheme work status recipient
  port=$(setting ALERT_SMTP_PORT 587)
  security=$(setting ALERT_SMTP_SECURITY starttls)
  from=$(setting ALERT_SMTP_FROM)
  address=${from##*<}
  address=${address%>}
  user=$(setting ALERT_SMTP_USERNAME)
  work=$(mktemp -d) || return 1
  chmod 700 "$work"
  local -a args=(-fsS -m 30 --mail-from "$address")
  IFS=',' read -ra recipients <<< "$(setting ALERT_SMTP_TO)"
  for recipient in "${recipients[@]}"; do
    recipient=${recipient// /}
    [ -n "$recipient" ] && args+=(--mail-rcpt "$recipient")
  done
  scheme=smtp
  case $security in
    ssl) scheme=smtps ;;
    starttls) args+=(--ssl-reqd) ;;
  esac
  if [ -n "$user" ]; then
    (umask 077 && printf 'machine %s login %s password %s\n' "$host" "$user" "$(setting ALERT_SMTP_PASSWORD)" > "$work/netrc")
    args+=(--netrc-file "$work/netrc")
  fi
  printf 'From: %s\r\nTo: %s\r\nSubject: %s\r\nDate: %s\r\n\r\n%s\r\n' "$from" "$(setting ALERT_SMTP_TO)" "$title" \
    "$(date -R 2> /dev/null || date)" "$body" > "$work/message"
  curl "${args[@]}" -T "$work/message" "$scheme://$host:$port" > /dev/null 2>&1
  status=$?
  rm -f "$work/netrc" "$work/message"
  rmdir "$work"
  return $status
}

healthy() { curl -fsS -m 10 -o /dev/null "$URL" 2> /dev/null; }

healthy_within() { # seconds: ask every 5 s until the app answers
  local waited=0
  until healthy; do
    [ "$waited" -ge "$1" ] && return 1
    sleep 5
    waited=$((waited + 5))
  done
}

# A deploy or a restore is running, or the app is stopped on purpose (see the top)
in_maintenance() {
  local holder=""
  [ -e "$MAINTENANCE" ] || return 1
  read -r holder < "$MAINTENANCE" 2> /dev/null
  case $holder in
    hold)
      log "The app is held stopped ($MAINTENANCE): not checking until the file is deleted"
      return 0
      ;;
    pid=*)
      if ps -p "${holder#pid=}" > /dev/null 2>&1; then
        log "Maintenance in progress ($MAINTENANCE, process ${holder#pid=}): not checking"
        return 0
      fi
      ;;
  esac
  if [ -n "$(find "$MAINTENANCE" -mmin "+$MAINTENANCE_MAX_MINUTES" 2> /dev/null)" ]; then
    log "Ignoring $MAINTENANCE: it is older than $MAINTENANCE_MAX_MINUTES minutes, so it was left behind"
    return 1
  fi
  log "Maintenance in progress ($MAINTENANCE): not checking"
}

# Starts the stack's existing containers as they are: never a build, never a recreated container.
start_stack() {
  (cd "$APP_DIR" && docker compose start > /dev/null 2>&1)
  healthy_within "$WAIT_SECONDS" && return 0
  log "Still no answer after starting the containers; restarting them"
  (cd "$APP_DIR" && docker compose restart > /dev/null 2>&1)
  healthy_within "$WAIT_SECONDS"
}

start_docker() {
  docker info > /dev/null 2>&1 && return 0
  log "Docker isn't running; starting it"
  if [ "$(uname)" = Darwin ]; then
    open -a Docker || return 1
  elif [ "$(id -u)" = 0 ]; then
    systemctl start docker || return 1
  else
    log "Can't start Docker as $(id -un); systemd restarts it on its own when it is enabled"
    return 1
  fi
  local waited=0
  until docker info > /dev/null 2>&1; do
    [ $waited -ge $DOCKER_START_SECONDS ] && return 1
    sleep 5
    waited=$((waited + 5))
  done
}

name=$(setting ALERT_NAME "RAG Assistant")
if [ "${1:-}" = --test ]; then
  send test "[$name] Test alert (watchdog)" "This is a test alert from the host watchdog on $(hostname). If you can read it, this channel works."
  exit 0
fi
if in_maintenance; then
  exit 0
fi

state=up since=0 notified=0
if [ -r "$STATE" ]; then
  read -r state since notified < "$STATE"
fi
now=$(date +%s)
answers() {
  if [ "$state" = down ]; then
    log "The app answers again"
    send recovery "[$name] Resolved: the app answers again" "The app at $URL answers again after $(((now - since) / 60)) min."
  fi
  echo "up $now 0" > "$STATE"
  exit 0
}
if healthy; then
  answers
fi

log "The app doesn't answer at $URL; asking again in $RETRY_SECONDS s"
sleep "$RETRY_SECONDS"
if healthy; then
  log "It answered the second time"
  answers
fi
if in_maintenance; then
  exit 0
fi

log "The app still doesn't answer; restarting it"
reason="The app didn't answer"
if ! docker info > /dev/null 2>&1; then
  reason="Docker wasn't running, so the app didn't answer"
fi
if start_docker && start_stack; then
  log "Restarted; the app answers"
  send problem "[$name] Problem: the app was down and was restarted" \
    "$reason at $URL, so the watchdog on $(hostname) started it again; it answers now. See docker compose logs."
  echo "up $now 0" > "$STATE"
  exit 0
fi

repeat=$(setting ALERT_REPEAT_HOURS 6)
repeat=${repeat%%.*}
if [ "$state" != down ]; then
  log "Still down after a restart"
  send problem "[$name] Problem: the app is down" \
    "$reason at $URL, and the watchdog on $(hostname) couldn't start it again. Check Docker and docker compose logs."
  echo "down $now $now" > "$STATE"
elif [ "${repeat:-0}" -gt 0 ] && [ $((now - notified)) -ge $((repeat * 3600)) ]; then
  send reminder "[$name] Still failing: the app is down" "The app at $URL has been down for $(((now - since) / 60)) min."
  echo "down $since $now" > "$STATE"
fi
exit 1
