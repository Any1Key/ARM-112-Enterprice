#!/bin/sh
set -eu
umask 077
mkdir -p /backups
if [ "${BACKUP_LOCK_HELD:-0}" != 1 ]; then
    exec 9>/backups/.backup.lock
    flock -n 9 || { echo 'Backup already running'; exit 0; }
fi
backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_bundle=arm112_${backup_stamp}_$$
backup_work=/backups/.partial_${backup_stamp}_$$
backup_ok=0
cleanup() {
    rm -f /backups/.backup-running
    [ "$backup_ok" = 1 ] || printf '%s\n' 'Создание резервной копии не завершено. Проверьте логи backup.' > /backups/last_error
    [ ! -d "$backup_work" ] || rm -rf "$backup_work"
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP
touch /backups/.backup-running
rmdir /backups/.backup-pending 2>/dev/null || true
mkdir "$backup_work"
pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$backup_work/database.dump"
pg_restore -l "$backup_work/database.dump" >/dev/null
tar --exclude='./.restore_stage_*' --exclude='./.restore_old_*' -czf "$backup_work/media.tar.gz" -C /backup-media .
tar --exclude='./.restore_stage_*' --exclude='./.restore_old_*' -czf "$backup_work/runtime.tar.gz" -C /backup-runtime .
tar -tzf "$backup_work/media.tar.gz" >/dev/null
tar -tzf "$backup_work/runtime.tar.gz" >/dev/null
(cd "$backup_work" && sha256sum database.dump media.tar.gz runtime.tar.gz > SHA256SUMS && sha256sum -c SHA256SUMS)
backup_size=$(($(wc -c < "$backup_work/database.dump") + $(wc -c < "$backup_work/media.tar.gz") + $(wc -c < "$backup_work/runtime.tar.gz")))
backup_created=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"version":1,"bundle":"%s","created_at":"%s","size_bytes":%s,"scope":["database","media","runtime"]}\n' "$backup_bundle" "$backup_created" "$backup_size" > "$backup_work/manifest.json"
mv "$backup_work" "/backups/$backup_bundle"
cp "/backups/$backup_bundle/database.dump" /backups/latest.dump.partial
mv /backups/latest.dump.partial /backups/latest.dump
cp "/backups/$backup_bundle/manifest.json" /backups/latest.json.partial
mv /backups/latest.json.partial /backups/latest.json
printf '%s\n' "$backup_bundle" > /backups/latest.bundle.partial
mv /backups/latest.bundle.partial /backups/latest.bundle
printf '%s\n' "$backup_created" > /backups/last_success
rm -f /backups/last_error
backup_ok=1
# Only remove dated backups after a new complete copy was successfully published.
find /backups -maxdepth 1 -type f -name 'arm112_*.dump' -mtime +180 -delete
find /backups -maxdepth 1 -type d -name 'arm112_*' -mtime +180 -exec rm -rf '{}' ';'
printf 'Full backup completed: %s (%s bytes)\n' "$backup_bundle" "$backup_size"
