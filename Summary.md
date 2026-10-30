# Своим ходом (tripplan) — состояние на 2026-10-29 (вечер)

Передача контекста в новый чат. Устройство проекта и API подробно — в `README.md`, правила работы —
в `CLAUDE.md`, план этапов — в памяти прежнего сервера (`roadmap.md`, в репозитории его нет). Здесь только итог
последних сессий.

## 1. Архитектура и структура

Монолит: Python-бэкенд на Starlette отдаёт и API, и собранный фронтенд. Данные — PostgreSQL (аккаунты, поездки,
каталог мест), файлы документов — на диске или в S3. Главная копия каталога — JSON в git, в базу его загружает
`manage.py import-catalog`.

```
backend/
  app/planner.py      ядро: каркас дня, отбор мест, пересчёт времени, бюджет, пункты программы
  app/main.py         HTTP API (Starlette Route), раздача фронтенда, RequireLogin, /api/health с проверкой базы
  app/config.py       НОВОЕ — настройки сервера из env (+ чтение backend/.env): DATABASE_URL, STORAGE, S3_*, лимиты
  app/db.py           НОВОЕ — SQLAlchemy Core + psycopg 3, таблицы users, sessions, resets, trips, cities, places
  app/storage.py      НОВОЕ — LocalStorage (var/uploads) и S3Storage (boto3), ключ «папка/имя»
  app/limits.py       НОВОЕ — BodyLimit (413) и RateLimit (429, счётчики в памяти процесса)
  app/catalog.py      каталог из таблиц cities/places (lru_cache), import_json() из data/*_places.json
  app/store.py        поездки в PostgreSQL, файлы через storage
  app/auth.py         почта+пароль (scrypt), сессия в cookie sh_session, сброс пароля
  manage.py           НОВОЕ — devdb start|stop|status, migrate, import-catalog, setup, sqlite-to-pg, files-to-s3
  migrations/         НОВОЕ — Alembic, 0001_initial
  tools/devpg.py      НОВОЕ — PostgreSQL 16 из колеса pgserver (PyPI) без установки в систему
  tools/yandex_facts.py  рейтинг и часы заведений из Яндекс Карт
  data/*_places.json  черновые каталоги (indent=1, без \n в конце!)
  tests/              129 юнит-тестов + e2e_smoke.py; tests/testdb.py — временный PostgreSQL на прогон
frontend/src/         без изменений в этой сессии (см. README)
Dockerfile, docker-compose.yml, .dockerignore   НОВОЕ — образ приложения, compose: app + postgres (+ minio)
```

Роутинг фронтенда — по `window.location.pathname`: `/` форма, `/trip/<id>`, `/trips`, `/account`, `/login`, `/reset`.
Всё под `/api/` требует входа (`RequireLogin`, исключения в `PUBLIC_API`), поездки — только владельцу.

## 2. Где идёт работа

С этой сессии — на Mac пользователя: `/Users/ruslan/planroute`, Python 3.15, Node 26, нет Docker, Homebrew и
системного PostgreSQL. Окружение: `backend/.venv`, база разработки `manage.py devdb start` (порт 54329,
`backend/var/postgres`), сервер — `bash scripts/run.sh` или F5 в VS Code (задача «Перед запуском» поднимает базу).
Локальные аккаунт и 2 поездки пользователя перенесены из SQLite в PostgreSQL (`manage.py sqlite-to-pg`).


## 3. Что сделано в этой сессии

Коммит `ce0eaca` — данные заведений (пункт 1 прошлого плана):
- `f_singer`: «ЗингерЪ» на Картах закрыт навсегда, кафе в Доме Зингера теперь «Рене» (Невский, 28) — переименовано.
- `f_vasilyevsky2`: координаты были в 600 м от адреса (поэтому поиск в радиусе 400 м его не находил) — исправлены.
- Казань: `f_tubetey`, `f_usadba` — ymaps_id, точные координаты, рейтинг и часы.
- Найдены ещё два закрытых навсегда, хотя рейтинг у них был: `f_stolle_kon` (Конюшенный пер., 1/6) и `f_petrograd`
  (Большой П. С., 64) — заменены на ближайшие открытые «Штолле» (Невский, 11) и «Теремок» (Льва Толстого, 1-3).
- `yandex_facts.py`: печатает «! … закрыто» вместо обновления, повторяет запрос при ошибке Карт (лимит частоты).

Коммит этапа C.0 (кроме VPS и домена):
- PostgreSQL вместо SQLite. Тип колонок данных — `json`, не `jsonb`: jsonb переставляет ключи объектов, а порядок
  ключей в поездке и каталоге мог влиять на вывод. Время — `timestamptz`.
- Регистрация берёт `lock table users`: иначе две одновременные могли обе стать «первым пользователем».
  `reset_password` помечает ссылку использованной условием `used is null` — одна ссылка не сработает дважды.
- Каталог в БД: ключ города — прежнее имя файла (`spb_places.json`), порядок мест — колонка `pos`.
  Каталог из базы сверен с JSON побайтно.
- Файлы: `store.read_file` вместо `file_path`, отдача через `Response` с тем же Content-Disposition, что у FileResponse.
- Лимиты: файл 15 МБ, запрос с файлами 60 МБ, без файлов 512 КБ; частота — правила `limits.RULES`.
- Docker: образ на python:3.12-slim, при старте `manage.py setup`, uvicorn одним процессом с `--proxy-headers`.
  **Docker-файлы не проверены сборкой** — на Mac нет Docker. YAML compose синтаксически читается.

Проверено: 129 юнит-тестов (9 новых в `test_infra.py`: хранилище на диске и в S3 через moto, лимиты, импорт каталога,
перенос из SQLite) и `e2e_smoke.py` проходят на PostgreSQL.

## 4. Стек

- **Backend:** Python 3.11+, Starlette, uvicorn, PostgreSQL 16 (SQLAlchemy Core 2, psycopg 3, Alembic), boto3,
  python-multipart, pypdf, certifi; внешние сервисы — YandexGPT, Yandex Vision OCR, DaData или Photon,
  Трипстер API 2.0, Суточно.ру и Яндекс Карты парсингом страниц, SMTP reg.ru.
- **Frontend:** React 19 + TypeScript 6, esbuild (`build.mjs` → `dist/assets/app.js`, `app.css`).
- **Тесты:** unittest (129), moto для S3, Playwright + Chromium (`tests/e2e_smoke.py`).
- Ключи и настройки — `backend/.env` (образец `.env.example`, в конце — новые настройки сервера).

Команды проверки (из `backend`): `.venv/bin/python -m unittest discover -s tests`,
`.venv/bin/python tests/e2e_smoke.py <папка>`, `cd ../frontend && npx tsc --noEmit`.

## 5. Следующие шаги

1. (сделано) GitHub с Mac подключён по SSH (ключ ~/.ssh/id_ed25519), коммиты отправлены.
2. Проверить Docker-сборку там, где есть Docker (`docker compose up -d --build`), и бакет Yandex Object Storage
   (`STORAGE=s3`, сервисный аккаунт с ролью storage.editor).
3. Остаток этапа C.0: VPS в России, домен, nginx + Let's Encrypt; перенос данных с прежнего сервера —
   `sqlite-to-pg` и `files-to-s3` (порядок — в комментарии `docker-compose.yml` и в CLAUDE.md).
   Rate limit в памяти — при нескольких процессах перенести в Redis или nginx `limit_req`.
4. Данные: новые города — Золотое кольцо (Сергиев Посад, Переславль-Залесский, Ростов Великий, Ярославль, Кострома,
   Иваново, Суздаль, Владимир), Сочи, Краснодар; сезонность (`months`), особенно Сочи. Сделано: настоящие
   заведения и отели Калининграда и Н. Новгорода, Выборг целиком (каталог, ROUTES из Москвы и Петербурга, ссылки,
   код Расписаний c969), ночные поезда, часовой пояс Калининграда (`tz`). Код города в Расписаниях без ключа API —
   suggests.rasp.yandex.net/all_suggests?format=old&part=<город>. Адреса новых
   городов сверены: Трипстер (Sergiyev_Posad, Pereslavl_Zalessky, Rostov — остальные по-английски как есть),
   Авито (sergiev_posad, pereslavl-zalesskiy, rostov), Островок (russia/sergiyev_posad, russia/pereslavl-zalesskiy,
   russia/rostov, russia/nizhniy_novgorod).
5. Этап B: черновики карточек мест моделью, объяснение выбора мест, правка программы словами.
