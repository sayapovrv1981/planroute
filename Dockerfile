# Образ приложения: фронтенд собирается в первом этапе, в итоговом образе — Python и готовый dist/.
# Сборка и запуск вместе с PostgreSQL — docker-compose.yml.

FROM node:22-alpine AS front
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SH_DATA_DIR=/data
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=front /src/frontend/dist /app/frontend/dist
RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
# Перед стартом — миграции и каталог мест из data/*.json. Один процесс uvicorn: счётчики rate limit в памяти.
# --proxy-headers: адрес клиента — из X-Forwarded-For от nginx; порт 8000 наружу не открываем (см. compose).
CMD ["sh", "-c", "python manage.py setup && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
