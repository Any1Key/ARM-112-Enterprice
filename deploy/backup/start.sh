#!/bin/sh
set -eu
backup_interval=${BACKUP_INTERVAL_HOURS:-23}
case "$backup_interval" in ''|*[!0-9]*) echo 'BACKUP_INTERVAL_HOURS must be 1..23' >&2; exit 1;; esac
[ "$backup_interval" -ge 1 ] && [ "$backup_interval" -le 23 ] || { echo 'BACKUP_INTERVAL_HOURS must be 1..23' >&2; exit 1; }
# A recreated container has no surviving worker from its old PID namespace.
rm -f /backups/.backup-running
while :; do
    touch /backups/.scheduler-heartbeat
    if [ -f /backups/.restore-request ]; then
        python3 /restore.py || echo 'Restore worker interrupted; retrying with maintenance gate closed' >&2
    fi
    if [ -f /backups/.restore-maintenance ]; then sleep 5; continue; fi
    backup_now=$(date +%s)
    backup_modified=$(stat -c %Y /backups/latest.json 2>/dev/null || echo 0)
    if [ -f /backups/last_error ] || [ -d /backups/.backup-pending ] || [ "$backup_modified" = 0 ] || [ "$((backup_now-backup_modified))" -ge "$((backup_interval*3600))" ]; then
        /backup.sh || echo 'Backup failed; will retry automatically' >&2
    fi
    sleep 5
done
