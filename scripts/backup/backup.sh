#!/bin/sh
# Backups of AskNuvexusDoc (SPEC.md section 10), run by the backup and restore services of docker-compose.yml in
# the pinned postgres image (busybox, pg_dump, pg_restore, psql). Never run on the host: use scripts/backup.sh and
# scripts/restore.sh. The steps are in lib.sh.
#
#   backup.sh schedule             back up at BACKUP_SCHEDULE (UTC); at start too when none began since the last time
#   backup.sh once [LABEL]         back up now (LABEL: manual, pre-restore)
#   backup.sh list | verify NAME | show NAME | prune | health
#   backup.sh restore NAME         (the restore service only) exit code 0 restored, 1 nothing changed, 3 busy,
#                                  4 only some parts replaced
#
# A backup is a directory /backups/<UTC time>-<label> (mode 700, files 600): uploads.tar.gz and uploads.sha256 (every
# archived file's checksum), qdrant/<collection>.snapshot and qdrant.collections (points per collection),
# postgres.dump (pg_dump -Fc), postgres.rows (rows per table, counted from the dump itself), manifest.json and
# SHA256SUMS. It is written to /backups/.tmp-<name>, verified, then renamed. /status/status.json (no personal data)
# tells the backend and the monitor how backups are going.

set -u
umask 077
. "$(dirname "$0")/lib.sh"

command=${1:-schedule}
[ $# -gt 0 ] && shift
case $command in
  schedule) schedule ;;
  once)
    label=${1:-manual}
    case $label in manual | pre-restore) ;; *) log "Unknown label $(safe "$label")"; exit 2 ;; esac
    run_backup "$label"
    ;;
  list)
    for name in $(backups); do
      echo "$name $(manifest_value "$BACKUP_ROOT/$name/manifest.json" size_bytes)"
    done
    ;;
  verify)
    dir=$(backup_dir "${1:-}") || exit 1
    verify_dir "$dir" && log "$(basename "$dir") verified"
    ;;
  show)
    dir=$(backup_dir "${1:-}") || exit 1
    cat "$dir/manifest.json"
    ;;
  prune) (flock -n 9 || exit 3; prune; write_status) 9> "$STATUS_DIR/backup.lock" ;;
  health)
    read -r seen rest < "$STATUS_DIR/scheduler" 2> /dev/null || exit 1
    is_count "$seen" && [ $(($(now) - seen)) -lt $((HEARTBEAT_SECONDS * 3)) ] || [ -f "$STATUS_DIR/running" ]
    ;;
  restore)
    (
      flock -n 9 || { log "A backup or another restore is running"; exit 3; }
      restore_backup "${1:-}"
    ) 9> "$STATUS_DIR/backup.lock"
    ;;
  *)
    sed -n '2,10p' "$0"
    exit 2
    ;;
esac
