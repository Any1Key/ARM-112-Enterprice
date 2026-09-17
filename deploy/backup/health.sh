#!/bin/sh
set -eu
[ -f /backups/latest.dump ] && [ -f /backups/latest.bundle ]
backup_bundle=$(cat /backups/latest.bundle)
case "$backup_bundle" in arm112_*) ;; *) exit 1;; esac
case "$backup_bundle" in *[!a-zA-Z0-9_]*) exit 1;; esac
[ "$(($(date +%s)-$(stat -c %Y /backups/latest.dump)))" -lt 86400 ]
for backup_file in database.dump media.tar.gz runtime.tar.gz SHA256SUMS manifest.json; do
    [ -s "/backups/$backup_bundle/$backup_file" ]
done
pg_restore -l /backups/latest.dump >/dev/null
# During a long copy the foreground worker replaces the scheduler heartbeat.
[ -f /backups/.backup-running ] || [ "$(($(date +%s)-$(stat -c %Y /backups/.scheduler-heartbeat)))" -lt 30 ]
