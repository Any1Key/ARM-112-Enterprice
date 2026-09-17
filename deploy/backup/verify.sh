#!/bin/sh
set -eu
umask 077
backup_bundle=$(cat /backups/latest.bundle)
case "$backup_bundle" in arm112_*) ;; *) exit 1;; esac
case "$backup_bundle" in *[!a-zA-Z0-9_]*) exit 1;; esac
backup_root=/backups/$backup_bundle
(cd "$backup_root" && sha256sum -c SHA256SUMS)
verify_db=arm112_restore_check_$(date -u +%Y%m%d%H%M%S)_$$
verify_work=$(mktemp -d /tmp/arm112-restore.XXXXXX)
verify_created=0
cleanup() {
    if [ "$verify_created" = 1 ]; then dropdb -h db -U "$POSTGRES_USER" --if-exists "$verify_db"; fi
    rm -rf "$verify_work"
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP
createdb -h db -U "$POSTGRES_USER" "$verify_db"
verify_created=1
pg_restore -h db -U "$POSTGRES_USER" -d "$verify_db" --exit-on-error --no-owner --no-privileges "$backup_root/database.dump"
psql -h db -U "$POSTGRES_USER" -d "$verify_db" -v ON_ERROR_STOP=1 -At -c 'SELECT count(*) AS users FROM users; SELECT count(*) AS cards FROM session_runs; SELECT count(*) AS scenarios FROM scenarios;'
mkdir "$verify_work/media" "$verify_work/runtime"
tar -xzf "$backup_root/media.tar.gz" -C "$verify_work/media"
tar -xzf "$backup_root/runtime.tar.gz" -C "$verify_work/runtime"
printf 'Restored media files: %s\n' "$(find "$verify_work/media" -type f | wc -l)"
printf 'Restored runtime files: %s\n' "$(find "$verify_work/runtime" -type f | wc -l)"
printf '{"bundle":"%s","verified_at":"%s"}\n' "$backup_bundle" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /backups/last_restore_check.json.partial
mv /backups/last_restore_check.json.partial /backups/last_restore_check.json
echo 'Restore drill passed; production database was not modified'
