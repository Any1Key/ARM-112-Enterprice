#!/bin/sh
set -eu
backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_target=/backups/arm112_${backup_stamp}.dump
pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "${backup_target}.partial"
pg_restore -l "${backup_target}.partial" >/dev/null
mv "${backup_target}.partial" "$backup_target"
cp "$backup_target" /backups/latest.dump.partial
mv /backups/latest.dump.partial /backups/latest.dump
printf '%s\n' "$backup_stamp" > /backups/last_success
# Keep the same six-month retention policy for dated database dumps.
find /backups -maxdepth 1 -type f -name 'arm112_*.dump' -mtime +180 -delete
