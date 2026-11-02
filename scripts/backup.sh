#!/usr/bin/env bash
# Резервная копия рабочего сервера: база PostgreSQL и загруженные документы (при STORAGE=local).
# Запуск — systemd-таймер planroute-backup (scripts/planroute-backup.*), каждый день в 04:30.
# Копии — в /opt/backups (KEEP_DAYS дней) и в объектном хранилище reg.ru (REMOTE_KEEP_DAYS дней).
# Восстановление — README, «Резервные копии».
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

# Вторая копия — вне сервера, в объектном хранилище reg.ru: переживёт потерю самого сервера.
# Ключи — в S3_ENV (S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY), в git их нет. Там храним REMOTE_KEEP_DAYS дней.
S3_ENV=${S3_ENV:-/root/backup-s3.env}
REMOTE_KEEP_DAYS=${REMOTE_KEEP_DAYS:-30}
if [ -f "$S3_ENV" ]; then
  set -a; . "$S3_ENV"; set +a
  # клиент AWS: системные сертификаты (в его наборе нет корня GlobalSign R6 у s3.regru.cloud),
  # контрольные суммы — только где обязательны (новые проверки AWS хранилище reg.ru не поддерживает)
  aws() {
    docker run --rm -v /etc/ssl/certs/ca-certificates.crt:/ca.crt:ro -v "$DEST":/b:ro -e AWS_CA_BUNDLE=/ca.crt \
      -e AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY" -e AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY" -e AWS_DEFAULT_REGION=ru-1 \
      -e AWS_REQUEST_CHECKSUM_CALCULATION=when_required -e AWS_RESPONSE_CHECKSUM_VALIDATION=when_required \
      amazon/aws-cli --endpoint-url "$S3_ENDPOINT" "$@"
  }
  aws s3 cp --only-show-errors "/b/db-$stamp.dump" "s3://$S3_BUCKET/db-$stamp.dump"
  [ -f "$DEST/uploads-$stamp.tar.gz" ] && aws s3 cp --only-show-errors "/b/uploads-$stamp.tar.gz" "s3://$S3_BUCKET/uploads-$stamp.tar.gz"
  # старше REMOTE_KEEP_DAYS — удаляем; дата — в имени файла (db-2026-11-02-0430.dump)
  cutoff=$(date -d "-$REMOTE_KEEP_DAYS days" +%F)
  aws s3 ls "s3://$S3_BUCKET/" | awk '{print $4}' | while read -r name; do
    day=$(echo "$name" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1)
    if [ -n "$day" ] && [[ "$day" < "$cutoff" ]]; then aws s3 rm --only-show-errors "s3://$S3_BUCKET/$name"; fi
  done
  echo "в хранилище $S3_BUCKET: $(aws s3 ls "s3://$S3_BUCKET/" | wc -l) файлов"
fi
