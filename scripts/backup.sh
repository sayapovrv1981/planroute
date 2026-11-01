#!/usr/bin/env bash
# Резервная копия рабочего сервера: база PostgreSQL и загруженные документы (при STORAGE=local).
# Запуск — systemd-таймер planroute-backup (scripts/planroute-backup.*), каждый день в 04:30.
# Копии — в /opt/backups, хранятся KEEP_DAYS дней. Восстановление — README, «Резервные копии».
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/planroute}
DEST=${DEST:-/opt/backups}
KEEP_DAYS=${KEEP_DAYS:-14}
stamp=$(date +%F-%H%M)

mkdir -p "$DEST"
chmod 700 "$DEST"
cd "$APP_DIR"

# база: формат pg_dump -Fc (сжатый, восстанавливается pg_restore); сначала во временный файл — оборванная копия не останется
docker compose exec -T db pg_dump -U tripplan -d tripplan -Fc > "$DEST/db-$stamp.dump.part"
mv "$DEST/db-$stamp.dump.part" "$DEST/db-$stamp.dump"
# проверка, что копия читается
docker compose exec -T db pg_restore --list < "$DEST/db-$stamp.dump" > /dev/null

# документы пользователей, если они хранятся на диске сервера (кеш Трипстера не нужен — соберётся заново)
if docker compose exec -T app test -d /data/uploads; then
  docker compose exec -T app tar -C /data -czf - uploads > "$DEST/uploads-$stamp.tar.gz.part"
  mv "$DEST/uploads-$stamp.tar.gz.part" "$DEST/uploads-$stamp.tar.gz"
fi

find "$DEST" -maxdepth 1 -type f \( -name 'db-*.dump' -o -name 'uploads-*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete
find "$DEST" -maxdepth 1 -type f -name '*.part' -delete
echo "копия $stamp: $(du -sh "$DEST/db-$stamp.dump" | cut -f1) база; всего в $DEST: $(du -sh "$DEST" | cut -f1)"
