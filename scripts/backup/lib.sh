# The backup and restore steps behind scripts/backup/backup.sh, which sources this file (so can a test). busybox ash in
# the pinned postgres image: pg_dump, pg_restore, psql, busybox tar, wget and nc. SPEC.md section 10.

BACKUP_ROOT=${BACKUP_ROOT:-/backups}
STATUS_DIR=${STATUS_DIR:-/status}
UPLOADS=${UPLOADS:-/data/uploads}
QDRANT_URL=${QDRANT_URL:-http://qdrant:6333}
QDRANT_API_KEY=${QDRANT_API_KEY:-}
# Where Qdrant keeps snapshots inside its own container (the qdrant image's default)
QDRANT_SNAPSHOTS=${QDRANT_SNAPSHOTS_PATH:-/qdrant/snapshots}
PREFIX=${QDRANT_COLLECTION_PREFIX:-rag_chunks}
SCHEDULE=${BACKUP_SCHEDULE:-02:30}
KEEP_DAILY=${BACKUP_KEEP_DAILY:-7}
KEEP_WEEKLY=${BACKUP_KEEP_WEEKLY:-4}
START_DELAY=${BACKUP_START_DELAY_SECONDS:-120}
case "$KEEP_DAILY$KEEP_WEEKLY$START_DELAY" in *[!0-9]*)
  echo "BACKUP_KEEP_DAILY, BACKUP_KEEP_WEEKLY and BACKUP_START_DELAY_SECONDS must be whole numbers" >&2
  exit 2
  ;;
esac
UPLOADS_OWNER_FALLBACK=10001:10001 # the backend image's appuser
HEARTBEAT_SECONDS=300
HTTP_TIMEOUT=3600
KEEP_RECENT_SECONDS=172800 # every backup younger than 48 h is kept
STAGING=-restoring         # the suffix of the collections a restore prepares
NAME_PATTERN='^[0-9]{8}T[0-9]{6}Z-(scheduled|startup|manual|pre-restore)$'
RESPONSE=${TMPDIR:-/tmp}/qdrant.response

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup: $*"; }
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
now() { date -u +%s; }
is_count() { case $1 in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac; }
name_time() { date -u -D %Y%m%dT%H%M%SZ -d "${1%%-*}" +%s; } # a backup's name -> its start, in Unix seconds

# A string for JSON that can't break out of its quotes: only these characters are kept.
safe() { printf '%s' "$1" | tr -cd 'A-Za-z0-9 ._+:/@,()-' | cut -c1-200; }
json_or_null() {
  local value
  value=$(safe "$1")
  if [ -n "$value" ]; then printf '"%s"' "$value"; else printf null; fi
}

# ---------------- Qdrant over plain HTTP (busybox wget for GET and POST, nc for the rest) ----------------

qdrant_address() {
  local rest
  rest=${QDRANT_URL#http://}
  if [ "$rest" = "$QDRANT_URL" ]; then
    log "QDRANT_URL must be http:// (the stack's own Qdrant)"
    return 1
  fi
  rest=${rest%%/*}
  Q_HOST=${rest%%:*}
  Q_PORT=${rest##*:}
  [ "$Q_PORT" = "$rest" ] && Q_PORT=80
  return 0
}

qwget() { # wget arguments...
  if [ -n "$QDRANT_API_KEY" ]; then
    wget -q --header "api-key: $QDRANT_API_KEY" "$@"
  else
    wget -q "$@"
  fi
}

q_get() { qwget -T 60 -O - "${QDRANT_URL%/}$1"; }

connected() { # pid: the process holds a TCP connection whose other end hasn't closed it (ESTABLISHED)
  local inodes tables
  inodes=$(ls -l "/proc/$1/fd" 2> /dev/null | sed -n 's/.*socket:\[\([0-9]*\)\]$/\1/p')
  [ -n "$inodes" ] || return 1
  tables=/proc/net/tcp
  [ -r /proc/net/tcp6 ] && tables="$tables /proc/net/tcp6"
  # shellcheck disable=SC2086
  echo "$inodes" | awk 'NR == FNR { mine[$1] = 1; next } FNR > 1 && $4 == "01" && ($10 in mine) { found = 1 }
    END { exit !found }' - $tables
}

# METHOD PATH [CONTENT_TYPE FILE...]: one request to Qdrant, the files sent as its body. Qdrant's answer goes to
# $RESPONSE, its status code to stdout ("000" when there is none). nc's input stays open until Qdrant has closed the
# connection: busybox nc half-closes the connection as soon as its input ends, and Qdrant then refuses, now and then,
# a request whose body it hasn't read yet ("Payload error").
q_request() {
  local method path type length file fifo pid
  qdrant_address || { echo 000; return 1; }
  method=$1 path=$2 type="" length=0
  shift 2
  if [ $# -gt 0 ]; then
    type=$1
    shift
    for file in "$@"; do length=$((length + $(stat -c %s "$file"))); done
  fi
  fifo=$(mktemp -u "${TMPDIR:-/tmp}/qdrant-request.XXXXXX")
  mkfifo -m 600 "$fifo" || { echo 000; return 1; }
  nc -w "$HTTP_TIMEOUT" "$Q_HOST" "$Q_PORT" < "$fifo" > "$RESPONSE" &
  pid=$!
  (
    printf '%s %s HTTP/1.1\r\nHost: %s:%s\r\nConnection: close\r\nContent-Length: %s\r\n' \
      "$method" "$path" "$Q_HOST" "$Q_PORT" "$length"
    [ -z "$type" ] || printf 'Content-Type: %s\r\n' "$type"
    [ -z "$QDRANT_API_KEY" ] || printf 'api-key: %s\r\n' "$QDRANT_API_KEY"
    printf '\r\n'
    [ $# -eq 0 ] || cat "$@"
    waited=0
    while connected "$pid" && [ $waited -lt $((HTTP_TIMEOUT * 10)) ]; do
      usleep 100000
      waited=$((waited + 1))
    done
  ) > "$fifo"
  wait "$pid"
  rm -f "$fifo"
  sed -n '1s/^HTTP\/1\.[01] \([0-9][0-9][0-9]\).*/\1/p' "$RESPONSE" | grep . || echo 000
}

collections() { # the app's collections (<prefix>_<embedding model id>), one per line
  q_get /collections | grep -o '"name":"[^"]*"' | sed 's/^"name":"//; s/"$//' | grep -E "^${PREFIX}_[0-9]+\$"
}

staging_collections() { # what an unfinished restore prepared
  q_get /collections | grep -o '"name":"[^"]*"' | sed 's/^"name":"//; s/"$//' | grep -E "^${PREFIX}_[0-9]+${STAGING}\$"
}

points() { q_get "/collections/$1" | sed -n 's/.*"points_count":\([0-9]*\).*/\1/p'; }

# COLLECTION SNAPSHOT CHECKSUM: recovers the collection from the snapshot file (it is created when missing). Qdrant
# keeps the file as $QDRANT_SNAPSHOTS/<collection>/restore-<collection>.snapshot. The status code on stdout.
upload_snapshot() {
  local boundary opening closing status
  boundary=ragbackup$(now)$$
  opening=$(mktemp) closing=$(mktemp)
  printf -- '--%s\r\nContent-Disposition: form-data; name="snapshot"; filename="restore-%s.snapshot"\r\n' \
    "$boundary" "$1" > "$opening"
  printf 'Content-Type: application/octet-stream\r\n\r\n' >> "$opening"
  printf -- '\r\n--%s--\r\n' "$boundary" > "$closing"
  status=$(q_request POST "/collections/$1/snapshots/upload?wait=true&priority=snapshot&checksum=$3" \
    "multipart/form-data; boundary=$boundary" "$opening" "$2" "$closing")
  rm -f "$opening" "$closing"
  echo "$status"
}

recover_collection() { # collection snapshot checksum: upload_snapshot, up to 3 tries
  local attempt status reason
  for attempt in 1 2 3; do
    status=$(upload_snapshot "$1" "$2" "$3")
    [ "$status" = 200 ] && return 0
    reason=$(sed -n 's/.*"error":"\([^"]*\)".*/\1/p' "$RESPONSE" | head -1)
    log "Qdrant refused the snapshot for $1 (attempt $attempt, HTTP $status: $(safe "${reason:-$(tail -1 "$RESPONSE")}"))"
    [ "$attempt" = 3 ] || sleep $((attempt * 3))
  done
  return 1
}

drop_collection() { # collection: it and the snapshot a restore uploaded into it
  q_request DELETE "/collections/$1/snapshots/restore-$1.snapshot" > /dev/null
  [ "$(q_request DELETE "/collections/$1")" = 200 ]
}

points_match() { # collection count exact
  local got
  got=$(points "$1")
  [ "$got" = "$2" ] && return 0
  if [ "$3" = 1 ]; then
    log "$1 has ${got:-no} points, the backup $2"
    return 1
  fi
  log "$1 has ${got:-no} points; the backup counted about $2 (documents were being indexed during it)"
}

# ---------------- Status ----------------

latest_backup() { backups | tail -1; }

backups() { # complete backups, oldest first
  [ -d "$BACKUP_ROOT" ] || return 0
  ls -1 "$BACKUP_ROOT" | grep -E "$NAME_PATTERN" | while read -r name; do
    [ -f "$BACKUP_ROOT/$name/manifest.json" ] && echo "$name"
  done
}

manifest_value() { # manifest key: a top-level string or number
  sed -n "s/^  \"$2\": \"*\([^\",]*\)\"*,*\$/\1/p" "$1" | head -1
}

write_status() {
  local stamp success latest manifest finished size duration failure at message scheduler seen schedule next due catch_up
  local running since count total name disk
  stamp=$(now)
  success=null
  latest=$(latest_backup)
  if [ -n "$latest" ]; then
    manifest=$BACKUP_ROOT/$latest/manifest.json
    finished=$(manifest_value "$manifest" finished_at)
    size=$(manifest_value "$manifest" size_bytes)
    duration=$(manifest_value "$manifest" duration_seconds)
    success=$(printf '{"name": "%s", "finished_at": "%s", "size_bytes": %s, "duration_seconds": %s}' \
      "$latest" "$(safe "$finished")" "${size:-0}" "${duration:-0}")
  fi
  failure=null
  if [ -f "$STATUS_DIR/last-failure" ]; then
    IFS='	' read -r at message < "$STATUS_DIR/last-failure"
    is_count "$at" && failure=$(printf '{"at": "%s", "error": "%s"}' "$(iso "$at")" "$(safe "$message")")
  fi
  scheduler=null
  if [ -f "$STATUS_DIR/scheduler" ]; then
    read -r seen schedule next due < "$STATUS_DIR/scheduler"
    if is_count "$seen" && is_count "$next"; then
      catch_up=null
      is_count "${due:-}" && [ "$due" -gt 0 ] && catch_up="\"$(iso "$due")\""
      scheduler=$(printf '{"seen_at": "%s", "schedule": "%s", "next_run_at": "%s", "catch_up_at": %s}' \
        "$(iso "$seen")" "$(safe "$schedule")" "$(iso "$next")" "$catch_up")
    fi
  fi
  running=null
  if [ -f "$STATUS_DIR/running" ]; then
    read -r since < "$STATUS_DIR/running"
    is_count "$since" && running="\"$(iso "$since")\""
  fi
  count=0 total=0
  for name in $(backups); do
    size=$(manifest_value "$BACKUP_ROOT/$name/manifest.json" size_bytes)
    count=$((count + 1))
    total=$((total + ${size:-0}))
  done
  disk=$(df -P -k "$BACKUP_ROOT" 2> /dev/null | awk 'NR == 2 { printf "{\"free_bytes\": %.0f, \"total_bytes\": %.0f}", $4 * 1024, $2 * 1024 }')
  chmod 755 "$STATUS_DIR" 2> /dev/null
  printf '{"format": 1, "updated_at": "%s", "scheduler": %s, "running_since": %s, "last_success": %s, "last_failure": %s, "backups": {"count": %s, "total_bytes": %s}, "disk": %s}\n' \
    "$(iso "$stamp")" "$scheduler" "$running" "$success" "$failure" "$count" "$total" "${disk:-null}" \
    > "$STATUS_DIR/.status.json.tmp"
  chmod 644 "$STATUS_DIR/.status.json.tmp"
  mv -f "$STATUS_DIR/.status.json.tmp" "$STATUS_DIR/status.json"
}

# ---------------- Backup ----------------
#
# The parts are taken one after the other while the app runs: the uploads, then Qdrant, then Postgres. A document
# deleted meanwhile is then missing from the dump instead of coming back without its file, and one added meanwhile
# comes back with its row. What still differs (a file or vectors the row doesn't match) is fixed after a restore by
# `python -m app.ops.reconcile` (scripts/restore.sh).

count_rows() { # dump rows-file [revision-file]: rows per table, counted from the dump itself, and the Alembic revision
  pg_restore --data-only --file=- "$1" | awk -v rows="$2" -v rev="${3:-/dev/null}" '
    /^COPY .* FROM stdin;$/ { table = $2; sub(/^public\./, "", table); gsub(/"/, "", table); n = 0; copying = 1; next }
    copying && $0 == "\\." { print table, n > rows; copying = 0; next }
    copying { n++; if (table == "alembic_version" && n == 1) print $1 > rev }
  ' || return 1
  [ -s "$2" ] || return 1
  sort -o "$2" "$2"
}

dump_postgres() { # dir
  pg_dump --format=custom --file="$1/postgres.dump" && count_rows "$1/postgres.dump" "$1/postgres.rows" "$1/.revision"
}

hash_tree() { # dir: "<sha256>  ./<path>" for every file under it, by path
  (cd "$1" && find . -type f | sort | tr '\n' '\0' | xargs -0 -r sha256sum)
}

# The archive, then each file's checksum and size from the archive itself, so they agree whatever happened meanwhile:
# a file still being written is listed as it was archived, one deleted meanwhile isn't in either.
archive_uploads() { # dir
  local dir check
  dir=$1 check=$1/.uploads.check
  # Dot-folders are a restore's (.restore-staging, .restore-previous), never the app's.
  (cd "$UPLOADS" && find . -name '.?*' -prune -o -type f -print | sort) > "$dir/.uploads.list" || return 1
  if [ -s "$dir/.uploads.list" ]; then
    # Its exit status is no guide: it is 1 when a listed file was deleted meanwhile. The checks below are.
    tar -czf "$dir/uploads.tar.gz" -C "$UPLOADS" -T "$dir/.uploads.list" 2> /dev/null
  else
    tar -czf "$dir/uploads.tar.gz" -C "$UPLOADS" --no-recursion . || return 1 # busybox refuses an empty archive
  fi
  rm -rf "$check" && mkdir "$check" || return 1
  if ! tar -xzf "$dir/uploads.tar.gz" -C "$check"; then
    rm -rf "$check"
    return 1
  fi
  (cd "$check" && find . -type f | sort) > "$dir/.uploads.archived"
  # A listed file the archive lacks must be gone: one that is still there couldn't be read.
  comm -23 "$dir/.uploads.list" "$dir/.uploads.archived" > "$dir/.uploads.missing"
  if [ -n "$(while read -r file; do [ ! -e "$UPLOADS/$file" ] || echo "$file"; done < "$dir/.uploads.missing")" ]; then
    log "Some uploaded files couldn't be read"
    rm -rf "$check"
    return 1
  fi
  [ ! -s "$dir/.uploads.missing" ] ||
    log "$(wc -l < "$dir/.uploads.missing") uploaded files were deleted while the backup ran; they aren't in it"
  hash_tree "$check" > "$dir/uploads.sha256" || { rm -rf "$check"; return 1; }
  (cd "$check" && find . -type f -exec stat -c %s {} +) |
    awk '{ total += $1 } END { printf "%d %.0f\n", NR, total }' > "$dir/.uploads.size"
  rm -rf "$check" "$dir/.uploads.archived" "$dir/.uploads.missing"
}

snapshot_qdrant() { # dir
  local names name before after response snapshot checksum got exact
  mkdir "$1/qdrant" || return 1
  : > "$1/qdrant.collections"
  names=$(collections) || names=""
  if [ -z "$names" ] && ! q_get /collections > /dev/null; then
    return 1
  fi
  for name in $names; do
    before=$(points "$name")
    response=$(qwget -T 3600 -O - --post-data '' "${QDRANT_URL%/}/collections/$name/snapshots?wait=true") || return 1
    after=$(points "$name")
    snapshot=$(echo "$response" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p')
    checksum=$(echo "$response" | sed -n 's/.*"checksum":"\([0-9a-f]*\)".*/\1/p')
    [ -n "$snapshot" ] || return 1
    qwget -T 3600 -O "$1/qdrant/$name.snapshot" "${QDRANT_URL%/}/collections/$name/snapshots/$snapshot"
    got=$?
    [ "$(q_request DELETE "/collections/$name/snapshots/$snapshot")" = 200 ] || log "Couldn't delete Qdrant's copy of $snapshot"
    [ $got = 0 ] || return 1
    if [ -n "$checksum" ] && [ "$(sha256sum "$1/qdrant/$name.snapshot" | cut -d' ' -f1)" != "$checksum" ]; then
      return 1
    fi
    exact=0
    [ -n "$before" ] && [ "$before" = "$after" ] && exact=1
    echo "$name ${after:-0} $exact" >> "$1/qdrant.collections"
  done
}

listed_files() { # dir: its files by path, SHA256SUMS, manifest.json and dot-files aside
  (cd "$1" && find . -name '.?*' -prune -o -type f ! -name SHA256SUMS ! -name manifest.json -print) | sed 's|^\./||' | sort
}

write_checksums() { # dir
  (cd "$1" && listed_files . | tr '\n' '\0' | xargs -0 sha256sum) > "$1/SHA256SUMS"
}

write_manifest() { # dir name trigger started finished
  local dir size sum file files bytes revision tables qdrant checksums server
  dir=$1
  size=0
  while read -r sum file; do
    size=$((size + $(stat -c %s "$dir/$file")))
  done < "$dir/SHA256SUMS"
  read -r files bytes < "$dir/.uploads.size"
  revision=""
  [ -f "$dir/.revision" ] && revision=$(cat "$dir/.revision")
  tables=$(awk '{ printf "%s\"%s\": %d", (NR > 1 ? ", " : ""), $1, $2 }' "$dir/postgres.rows")
  qdrant=$(awk '{ printf "%s\"%s\": {\"points\": %d, \"exact\": %s}", (NR > 1 ? ", " : ""), $1, $2, ($3 == 1 ? "true" : "false") }' \
    "$dir/qdrant.collections")
  checksums=""
  while read -r sum file; do
    checksums="$checksums${checksums:+, }\"$file\": {\"sha256\": \"$sum\", \"bytes\": $(stat -c %s "$dir/$file")}"
  done < "$dir/SHA256SUMS"
  server=$(psql -X -tAc 'SHOW server_version' 2> /dev/null | cut -d' ' -f1)
  {
    printf '{\n'
    printf '  "format": 1,\n'
    printf '  "name": "%s",\n' "$2"
    printf '  "trigger": "%s",\n' "$3"
    printf '  "started_at": "%s",\n' "$(iso "$4")"
    printf '  "finished_at": "%s",\n' "$(iso "$5")"
    printf '  "duration_seconds": %s,\n' "$(($5 - $4))"
    printf '  "app_version": %s,\n' "$(json_or_null "${APP_VERSION:-${APP_VERSION_FALLBACK:-}}")"
    printf '  "alembic_revision": %s,\n' "$(json_or_null "$revision")"
    printf '  "size_bytes": %s,\n' "$size"
    printf '  "postgres": {"server_version": %s, "database": %s, "tables": {%s}},\n' \
      "$(json_or_null "$server")" "$(json_or_null "${PGDATABASE:-}")" "$tables"
    printf '  "uploads": {"files": %s, "bytes": %s},\n' "${files:-0}" "${bytes:-0}"
    printf '  "qdrant": {"collections": {%s}},\n' "$qdrant"
    printf '  "files": {%s}\n' "$checksums"
    printf '}\n'
  } > "$dir/manifest.json"
}

# DIR [SCRATCH_PARENT]: every checksum; the dump's table of contents and its rows against postgres.rows; every archived
# file against uploads.sha256 (extracted into a scratch folder in SCRATCH_PARENT, default $TMPDIR); the snapshots.
verify_dir() {
  local dir scratch file sum name count exact status
  dir=$1
  for file in manifest.json SHA256SUMS postgres.dump postgres.rows uploads.tar.gz uploads.sha256 qdrant.collections; do
    [ -f "$dir/$file" ] || { log "verify: $file is missing"; return 1; }
  done
  (cd "$dir" && sha256sum -c -s SHA256SUMS) || { log "verify: a checksum doesn't match"; return 1; }
  while read -r sum file; do
    grep -q "\"$file\": {\"sha256\": \"$sum\"" "$dir/manifest.json" || { log "verify: manifest.json disagrees on $file"; return 1; }
  done < "$dir/SHA256SUMS"
  for file in $(listed_files "$dir"); do
    grep -q "  $file\$" "$dir/SHA256SUMS" || { log "verify: $file isn't in SHA256SUMS"; return 1; }
  done
  pg_restore --list "$dir/postgres.dump" > /dev/null || { log "verify: pg_restore can't read the dump"; return 1; }
  while read -r name count exact; do
    [ -f "$dir/qdrant/$name.snapshot" ] || { log "verify: the snapshot of $name is missing"; return 1; }
  done < "$dir/qdrant.collections"
  scratch=$(mktemp -d "${2:-${TMPDIR:-/tmp}}/.verify.XXXXXX") || { log "verify: no room for a scratch folder"; return 1; }
  verify_contents "$dir" "$scratch"
  status=$?
  rm -rf "$scratch"
  return $status
}

verify_contents() { # dir scratch
  local files expected
  count_rows "$1/postgres.dump" "$2/rows" || { log "verify: the dump's rows can't be counted"; return 1; }
  cmp -s "$2/rows" "$1/postgres.rows" || { log "verify: postgres.rows disagrees with the dump"; return 1; }
  mkdir "$2/uploads" && tar -xzf "$1/uploads.tar.gz" -C "$2/uploads" ||
    { log "verify: the uploads archive can't be extracted"; return 1; }
  files=$(cd "$2/uploads" && find . -type f | wc -l)
  expected=$(wc -l < "$1/uploads.sha256")
  [ "$files" -eq "$expected" ] || { log "verify: the uploads archive has $files files, uploads.sha256 $expected"; return 1; }
  [ "$expected" -eq 0 ] || (cd "$2/uploads" && sha256sum -c -s "$1/uploads.sha256") ||
    { log "verify: an archived file doesn't match uploads.sha256"; return 1; }
}

record_failure() { # message
  printf '%s\t%s\n' "$(now)" "$1" > "$STATUS_DIR/last-failure"
  log "FAILED: $1"
}

backup_now() { # label: the body of a backup, under the lock
  local label started name tmp failure
  label=$1
  started=$(now)
  name=$(date -u -d "@$started" +%Y%m%dT%H%M%SZ)-$label
  while [ -e "$BACKUP_ROOT/$name" ]; do
    sleep 1
    started=$(now)
    name=$(date -u -d "@$started" +%Y%m%dT%H%M%SZ)-$label
  done
  tmp=$BACKUP_ROOT/.tmp-$name
  echo "$started" > "$STATUS_DIR/running"
  write_status
  mkdir -m 700 "$tmp" || { record_failure "can't write to the backup folder"; return 1; }
  log "Backing up to $name"
  failure=""
  if ! archive_uploads "$tmp" && ! { log "Retrying the uploads archive once" && archive_uploads "$tmp"; }; then
    failure="uploads archive failed"
  elif ! snapshot_qdrant "$tmp"; then
    failure="Qdrant snapshot failed"
  elif ! dump_postgres "$tmp"; then
    failure="pg_dump failed"
  elif ! write_checksums "$tmp" || ! write_manifest "$tmp" "$name" "$label" "$started" "$(now)"; then
    failure="manifest failed"
  elif ! verify_dir "$tmp" "$tmp"; then
    failure="verification failed"
  fi
  if [ -n "$failure" ]; then
    rm -rf "$tmp"
    rm -f "$STATUS_DIR/running"
    record_failure "$failure"
    write_status
    return 1
  fi
  rm -f "$tmp/.uploads.list" "$tmp/.uploads.size" "$tmp/.revision"
  mv "$tmp" "$BACKUP_ROOT/$name" || { rm -rf "$tmp"; record_failure "can't write to the backup folder"; return 1; }
  if [ -n "${BACKUP_UID:-}" ]; then
    chown -R "$BACKUP_UID:${BACKUP_GID:-$BACKUP_UID}" "$BACKUP_ROOT/$name" 2> /dev/null ||
      log "Couldn't give the backup to $BACKUP_UID"
  fi
  rm -f "$STATUS_DIR/running"
  prune
  write_status
  log "Backup $name done ($(manifest_value "$BACKUP_ROOT/$name/manifest.json" size_bytes) bytes in $(($(now) - started)) s)"
  echo "BACKUP_NAME=$name"
}

run_backup() { # label: one backup at a time (the lock lives on the status volume, shared by every container)
  mkdir -p "$BACKUP_ROOT" "$STATUS_DIR" && chmod 700 "$BACKUP_ROOT"
  if [ -n "${BACKUP_UID:-}" ]; then
    chown "$BACKUP_UID:${BACKUP_GID:-$BACKUP_UID}" "$BACKUP_ROOT" 2> /dev/null
  fi
  (
    flock -n 9 || { log "Another backup or restore is running"; exit 3; }
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -name '.tmp-*' -exec rm -rf {} +
    backup_now "$1"
  ) 9> "$STATUS_DIR/backup.lock"
}

# Keeps every backup younger than 48 h; manual and pre-restore backups for BACKUP_KEEP_DAILY days; of the scheduled
# and start-up backups, the newest of each of the last BACKUP_KEEP_DAILY days and BACKUP_KEEP_WEEKLY weeks (UTC, weeks
# from Monday) that have one; and always the newest backup. Runs under the lock.
prune() {
  local keep name
  keep=$(backups | sort -r | while read -r name; do
    echo "$name $(name_time "$name")"
  done | awk -v daily="$KEEP_DAILY" -v weekly="$KEEP_WEEKLY" -v now="$(now)" -v recent="$KEEP_RECENT_SECONDS" '
    { keep = NR == 1 || now - $2 < recent }
    $1 ~ /-(manual|pre-restore)$/ { if (keep || now - $2 < daily * 86400) print $1; next }
    {
      day = substr($1, 1, 8)
      week = int((int($2 / 86400) + 3) / 7)
      by_day = !(day in days) && kept_days < daily
      by_week = !(week in weeks) && kept_weeks < weekly
      kept_days += by_day
      kept_weeks += by_week
      days[day] = 1
      weeks[week] = 1
      if (keep || by_day || by_week) print $1
    }
  ' | sort -u)
  for name in $(backups); do
    if ! echo "$keep" | grep -qx "$name"; then
      log "Removing $name (retention: BACKUP_KEEP_DAILY=$KEEP_DAILY, BACKUP_KEEP_WEEKLY=$KEEP_WEEKLY)"
      rm -rf "${BACKUP_ROOT:?}/$name"
    fi
  done
}

# ---------------- Schedule ----------------

valid_schedule() {
  echo "$SCHEDULE" | grep -Eq '^([01][0-9]|2[0-3]):[0-5][0-9](,([01][0-9]|2[0-3]):[0-5][0-9])*$'
}

slot() { # direction (1: the next scheduled time after now, -1: the latest at or before now), in Unix seconds
  local stamp midnight best time hours minutes at
  stamp=$(now)
  midnight=$((stamp - stamp % 86400))
  best=""
  for time in $(echo "$SCHEDULE" | tr ',' ' '); do
    hours=${time%%:*} minutes=${time##*:}
    at=$((midnight + ${hours#0} * 3600 + ${minutes#0} * 60))
    if [ "$1" = 1 ]; then
      [ "$at" -le "$stamp" ] && at=$((at + 86400))
      if [ -z "$best" ] || [ "$at" -lt "$best" ]; then best=$at; fi
    else
      [ "$at" -gt "$stamp" ] && at=$((at - 86400))
      if [ -z "$best" ] || [ "$at" -gt "$best" ]; then best=$at; fi
    fi
  done
  echo "$best"
}

catch_up_needed() { # no backup began since the latest scheduled time (pre-restore backups don't count)
  local newest
  newest=$(backups | grep -v -- '-pre-restore$' | tail -1)
  [ -z "$newest" ] || [ "$(name_time "$newest")" -lt "$(slot -1)" ]
}

heartbeat() { # next [catch-up due]
  echo "$(now) $SCHEDULE $1 ${2:-0}" > "$STATUS_DIR/scheduler"
  write_status
}

pause() { # seconds, interruptible by SIGTERM
  sleep "$1" &
  wait $!
}

# Backs up at every BACKUP_SCHEDULE time. At start, when no backup began since the latest scheduled time (none at all,
# or the stack was down then), a "startup" backup follows BACKUP_START_DELAY_SECONDS later.
schedule() {
  local next due target label left newest
  trap 'log "Stopping"; exit 0' TERM INT
  if ! valid_schedule; then
    log "BACKUP_SCHEDULE must be HH:MM (UTC), or several separated by commas, e.g. 02:30 or 02:30,14:30"
    exit 2
  fi
  mkdir -p "$BACKUP_ROOT" "$STATUS_DIR" && chmod 700 "$BACKUP_ROOT"
  next=$(slot 1)
  log "Scheduled daily at $SCHEDULE UTC; next at $(iso "$next"); keeping $KEEP_DAILY daily and $KEEP_WEEKLY weekly"
  due=0
  if catch_up_needed; then
    due=$(($(now) + START_DELAY))
    newest=$(backups | grep -v -- '-pre-restore$' | tail -1)
    if [ -z "$newest" ]; then
      log "No backup yet: one starts in $START_DELAY s"
    else
      log "No backup since the scheduled $(iso "$(slot -1)") (the last is $newest): one starts in $START_DELAY s"
    fi
  fi
  while :; do
    next=$(slot 1)
    target=$next label=scheduled
    if [ "$due" -gt 0 ] && [ "$due" -lt "$next" ]; then target=$due label=startup; fi
    while [ "$(now)" -lt "$target" ]; do
      heartbeat "$next" "$due"
      left=$((target - $(now)))
      pause $((left < HEARTBEAT_SECONDS ? (left > 0 ? left : 1) : HEARTBEAT_SECONDS))
    done
    if [ "$label" = scheduled ] || catch_up_needed; then
      run_backup "$label"
    fi
    due=0
    heartbeat "$(slot 1)"
  done
}

# ---------------- Restore (the restore service: uploads read-write, backups read-only) ----------------
#
# Every part is prepared and checked before any is replaced: the database in <db>_restoring, the files in
# .restore-staging on the uploads volume, each collection in <name>-restoring, and a snapshot of every current collection
# kept inside Qdrant. Then Qdrant, the files and last the database are swapped in. A swap that fails is undone: the
# previous files come back from .restore-previous and each collection from its kept snapshot (the database is swapped
# last, in one transaction, so it is still the previous one).

KEPT=${TMPDIR:-/tmp}/kept.collections # "<collection> <kept snapshot> <points>" of the collections before the swap
MOVED_ASIDE=0                         # the current files are all in .restore-previous

backup_dir() { # name -> its directory, or exit
  local name
  case $1 in
    latest) name=$(latest_backup) ;;
    *) name=$1 ;;
  esac
  if [ -z "$name" ] || ! echo "$name" | grep -Eq "$NAME_PATTERN" || [ ! -f "$BACKUP_ROOT/$name/manifest.json" ]; then
    log "No backup named '$(safe "$1")' in the backup folder"
    exit 1
  fi
  echo "$BACKUP_ROOT/$name"
}

sql_identifier() { echo "$1" | grep -Eq '^[a-z_][a-z0-9_]*$'; }

counts_match() { # database rows-file: every table holds the dump's rows, and there are no other tables
  local query table count tables
  query=""
  while read -r table count; do
    sql_identifier "$table" || { log "Unexpected table name in the backup"; return 1; }
    query="$query${query:+ UNION ALL }SELECT '$table', count(*) FROM public.\"$table\""
  done < "$2"
  psql -X -q -v ON_ERROR_STOP=1 -tA -F ' ' -d "$1" -c "$query" | sort > /tmp/restored.rows || return 1
  if ! cmp -s /tmp/restored.rows "$2"; then
    log "Row counts differ from the backup's:"
    diff "$2" /tmp/restored.rows | sed -n 's/^[-+][^-+]/  &/p'
    return 1
  fi
  tables=$(psql -X -tA -d "$1" -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
  [ "$tables" -eq "$(wc -l < "$2")" ] || { log "The restored database has $tables tables, the backup $(wc -l < "$2")"; return 1; }
}

prepare_db() { # dir
  local staging
  staging=${PGDATABASE}_restoring
  log "Restoring the database into $staging"
  psql -X -q -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE IF EXISTS \"$staging\" WITH (FORCE)" \
    -c "CREATE DATABASE \"$staging\"" || return 1
  if ! pg_restore --no-owner --no-privileges --exit-on-error --single-transaction -d "$staging" "$1/postgres.dump"; then
    log "pg_restore failed"
    return 1
  fi
  counts_match "$staging" "$1/postgres.rows" || return 1
  log "Database prepared: $(wc -l < "$1/postgres.rows") tables, row counts equal the backup's"
}

prepare_uploads() { # dir
  local staging restored
  staging=$UPLOADS/.restore-staging
  rm -rf "$staging" && mkdir "$staging" || return 1
  log "Extracting the uploads"
  tar -xzof "$1/uploads.tar.gz" -C "$staging" || { log "The uploads archive can't be extracted"; return 1; }
  if [ -s "$1/uploads.sha256" ] && ! (cd "$staging" && sha256sum -c -s "$1/uploads.sha256"); then
    log "Restored files don't match their checksums"
    return 1
  fi
  restored=$(cd "$staging" && find . -type f | wc -l)
  [ "$restored" -eq "$(wc -l < "$1/uploads.sha256")" ] || { log "The archive holds files the checksum list doesn't"; return 1; }
  log "Uploads prepared: $restored files, every checksum equals the backup's"
}

prepare_qdrant() { # dir
  local name count exact checksum
  q_get /collections > /dev/null || { log "Qdrant doesn't answer"; return 1; }
  while read -r name count exact; do
    log "Preparing the collection $name ($count points)"
    checksum=$(grep "  qdrant/$name.snapshot\$" "$1/SHA256SUMS" | cut -d' ' -f1)
    recover_collection "$name$STAGING" "$1/qdrant/$name.snapshot" "$checksum" || return 1
    points_match "$name$STAGING" "$count" "$exact" || return 1
  done < "$1/qdrant.collections"
}

keep_qdrant() { # a snapshot of every current collection, inside Qdrant, to undo the swap with
  local names name response snapshot
  : > "$KEPT"
  names=$(collections) || names=""
  if [ -z "$names" ] && ! q_get /collections > /dev/null; then
    return 1
  fi
  for name in $names; do
    response=$(qwget -T "$HTTP_TIMEOUT" -O - --post-data '' "${QDRANT_URL%/}/collections/$name/snapshots?wait=true") ||
      { log "Couldn't keep a copy of the collection $name"; return 1; }
    snapshot=$(echo "$response" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p')
    [ -n "$snapshot" ] || { log "Couldn't keep a copy of the collection $name"; return 1; }
    echo "$name $snapshot $(points "$name")" >> "$KEPT"
  done
  log "Kept a copy of the $(wc -l < "$KEPT") current collections, to undo the swap if it fails"
}

drop_kept() { # what keep_qdrant made
  local name snapshot count
  [ -f "$KEPT" ] || return 0
  while read -r name snapshot count; do
    q_request DELETE "/collections/$name/snapshots/$snapshot" > /dev/null
  done < "$KEPT"
  rm -f "$KEPT"
}

discard_staging() { # what a restore prepared
  local name
  psql -X -q -d postgres -c "DROP DATABASE IF EXISTS \"${PGDATABASE}_restoring\" WITH (FORCE)" || return 1
  rm -rf "$UPLOADS/.restore-staging"
  for name in $(staging_collections); do
    drop_collection "$name" || { log "Couldn't drop the collection $name"; return 1; }
  done
  drop_kept
}

recover_from() { # collection location checksum: Qdrant recovers the collection from a snapshot file it holds
  local body status
  body=$(mktemp)
  printf '{"location": "file://%s", "priority": "snapshot"%s}' "$2" "${3:+, \"checksum\": \"$3\"}" > "$body"
  status=$(q_request PUT "/collections/$1/snapshots/recover?wait=true" application/json "$body")
  rm -f "$body"
  [ "$status" = 200 ]
}

swap_qdrant() { # dir: each collection replaced by its prepared copy (Qdrant reads the file it holds)
  local name count exact staging checksum
  while read -r name count exact; do
    staging=$name$STAGING
    checksum=$(grep "  qdrant/$name.snapshot\$" "$1/SHA256SUMS" | cut -d' ' -f1)
    if ! recover_from "$name" "$QDRANT_SNAPSHOTS/$staging/restore-$staging.snapshot" "$checksum"; then
      log "Qdrant couldn't recover $name from its prepared copy; uploading the snapshot again"
      recover_collection "$name" "$1/qdrant/$name.snapshot" "$checksum" || return 1
      q_request DELETE "/collections/$name/snapshots/restore-$name.snapshot" > /dev/null
    fi
    points_match "$name" "$count" "$exact" || return 1
    drop_collection "$staging" || log "Couldn't drop the collection $staging"
  done < "$1/qdrant.collections"
  log "Qdrant restored: $(wc -l < "$1/qdrant.collections") collections, point counts equal the backup's"
}

swap_uploads() { # the prepared files replace the current ones, which wait in .restore-previous
  local staging previous restored
  staging=$UPLOADS/.restore-staging previous=$UPLOADS/.restore-previous
  restored=$(cd "$staging" && find . -type f | wc -l)
  rm -rf "$previous" && mkdir "$previous" || return 1
  find "$UPLOADS" -mindepth 1 -maxdepth 1 ! -name .restore-staging ! -name .restore-previous \
    -exec sh -c 'mv "$@" "$0"' "$previous" {} + || return 1
  MOVED_ASIDE=1
  find "$staging" -mindepth 1 -maxdepth 1 -exec sh -c 'mv "$@" "$0"' "$UPLOADS" {} + || return 1
  rmdir "$staging" || return 1
  chown -R "$(uploads_owner)" "$UPLOADS"
  log "Uploads restored: $restored files, every checksum equals the backup's"
}

uploads_owner() {
  local owner
  owner=$(stat -c %u:%g "$UPLOADS")
  [ "$owner" = 0:0 ] && owner=$UPLOADS_OWNER_FALLBACK
  echo "$owner"
}

swap_db() { # dir: <db>_restoring becomes <db>, in one transaction
  local database staging previous
  database=$PGDATABASE staging=${PGDATABASE}_restoring previous=${PGDATABASE}_previous
  psql -X -q -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE IF EXISTS \"$previous\" WITH (FORCE)" \
    -c "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = '$database'" > /dev/null || return 1
  psql -X -q -v ON_ERROR_STOP=1 -1 -d postgres -c "ALTER DATABASE \"$database\" RENAME TO \"$previous\"" \
    -c "ALTER DATABASE \"$staging\" RENAME TO \"$database\"" || return 1
  psql -X -q -d postgres -c "DROP DATABASE \"$previous\" WITH (FORCE)" || log "Couldn't drop the replaced database $previous"
  log "Database restored: $(wc -l < "$1/postgres.rows") tables, row counts equal the backup's"
}

finish_swap() { # dir: the collections the backup doesn't have go, and so do the copies kept to undo the swap
  local name snapshot count
  while read -r name snapshot count; do
    q_request DELETE "/collections/$name/snapshots/$snapshot" > /dev/null
    grep -q "^$name " "$1/qdrant.collections" && continue
    log "Removing the collection $name, which the backup doesn't have"
    [ "$(q_request DELETE "/collections/$name")" = 200 ] || log "Couldn't remove the collection $name"
  done < "$KEPT"
  rm -f "$KEPT"
  rm -rf "$UPLOADS/.restore-previous"
}

undo_uploads() { # the files from before the swap come back
  local previous
  previous=$UPLOADS/.restore-previous
  [ -d "$previous" ] || return 0
  if [ "$MOVED_ASIDE" = 1 ]; then # what is in the uploads now came from the backup
    find "$UPLOADS" -mindepth 1 -maxdepth 1 ! -name .restore-staging ! -name .restore-previous -exec rm -rf {} + ||
      return 1
  fi
  find "$previous" -mindepth 1 -maxdepth 1 -exec sh -c 'mv "$@" "$0"' "$UPLOADS" {} + || return 1
  rmdir "$previous" && log "Uploads put back as they were"
}

undo_qdrant() { # every collection from before the swap comes back from its kept snapshot; those it added go
  local name snapshot count failed
  failed=0
  while read -r name snapshot count; do
    if recover_from "$name" "$QDRANT_SNAPSHOTS/$name/$snapshot" && [ "$(points "$name")" = "$count" ]; then
      q_request DELETE "/collections/$name/snapshots/$snapshot" > /dev/null
    else
      log "Couldn't put the collection $name back (its copy is $QDRANT_SNAPSHOTS/$name/$snapshot in Qdrant)"
      failed=1
    fi
  done < "$KEPT"
  for name in $(collections); do
    grep -q "^$name " "$KEPT" && continue
    [ "$(q_request DELETE "/collections/$name")" = 200 ] || { log "Couldn't remove the collection $name"; failed=1; }
  done
  [ $failed = 0 ] && log "Qdrant put back as it was"
}

# NAME: exit code 0 when restored, 1 when nothing was changed (or a failed swap was undone), 4 when only some parts were
# replaced.
restore_backup() {
  local dir failed
  dir=$(backup_dir "$1") || return 1
  sql_identifier "${PGDATABASE:-}" || { log "PGDATABASE must be a plain name"; return 1; }
  export PGOPTIONS="-c client_min_messages=warning"
  qdrant_address || return 1
  discard_staging || { log "Couldn't remove what an earlier restore left; nothing was changed"; return 1; }
  if ! prepare_db "$dir" || ! prepare_uploads "$dir" || ! prepare_qdrant "$dir" || ! keep_qdrant; then
    discard_staging || log "Couldn't remove everything the restore prepared"
    log "The backup couldn't be prepared; nothing was changed"
    return 1
  fi
  if swap_qdrant "$dir" && swap_uploads && swap_db "$dir"; then
    finish_swap "$dir"
    return 0
  fi
  log "The swap failed; putting back what it replaced"
  failed=0
  undo_uploads || failed=1
  undo_qdrant || failed=1
  if [ $failed = 0 ] && discard_staging; then
    log "Everything is as it was before the restore; nothing was changed"
    return 1
  fi
  log "Putting everything back failed too: the stack holds parts of two states (the database is the previous one)"
  return 4
}
