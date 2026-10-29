# Своим ходом (tripplan) — состояние на 2026-10-29

Передача контекста в новый чат. Устройство проекта и API подробно — в `README.md`, правила работы на
сервере — в `CLAUDE.md`, план этапов — в памяти (`roadmap.md`). Здесь только итог последней сессии.

## 1. Архитектура и структура

Монолит: Python-бэкенд на Starlette отдаёт и API, и собранный фронтенд; данные — SQLite и JSON-каталоги.

```
backend/
  app/planner.py      1804 стр. — ядро: каркас дня, отбор мест, пересчёт времени, бюджет, пункты программы
  app/main.py          744 стр. — HTTP API (Starlette Route), раздача фронтенда (FrontFiles, no-cache), RequireLogin
  app/catalog.py                 — каталоги городов из data/*_places.json, haversine_km, maps_link
  app/auth.py                    — почта+пароль (scrypt), сессия в cookie sh_session, сброс пароля
  app/store.py                   — поездки в SQLite, файлы документов в backend/var/
  app/yandexmaps.py              — кафе по ссылке с Яндекс Карт: <script class="state-view"> → место
  app/sutochno.py, tripster.py, schedule.py, prices.py, address.py, bookings.py, wishes_ai.py, mailer.py, net.py
  data/{spb,kazan,nnovgorod,kaliningrad}_places.json — черновые каталоги (indent=1, без \n в конце!)
  tools/yandex_facts.py          — НОВОЕ, не в git: рейтинг и часы заведений из Яндекс Карт в каталог
  tests/                         — 118 юнит-тестов + e2e_smoke.py (Playwright, свой сервер и БД)
frontend/src/
  main.tsx            роутер (path → страница), UserContext, /api/auth/me при старте
  nav.tsx             Logo, UserMenu (имя — ссылка на /account), navigate()
  AccountPages.tsx    LoginPage, ResetPage, MyTripsPage, AccountPage, ChangePassword
  FormPage.tsx        1633 стр. — форма новой поездки
  TripPage.tsx         835 стр. — программа по часам, документы, бюджет
  Picks.tsx           список вариантов «Заменить» (поиск, рейтинг, расстояние)
  styles.css          один файл, без фреймворка
```

Роутинг фронтенда — по `window.location.pathname`: `/` форма, `/trip/<id>`, `/trips`, `/account`, `/login`, `/reset`.
Всё под `/api/` требует входа (`RequireLogin`, исключения в `PUBLIC_API`), поездки — только владельцу.

Запуск: systemd-служба `tripplan`, uvicorn `--reload` на `127.0.0.1:8000`, второй uvicorn не поднимать.
Фронтенд после правок — `cd frontend && npm run build`.

## 2. Что сделано в последних сессиях

Коммиты на `main` (запушены в sayapovrv1981/planroute):

- `18a5c3e` — рейтинг Яндекса и часы работы в описании у всех заведений, подробно в разделе 3.
- `babae1c` — карточка пользователя вынесена со страницы «Мои поездки» в отдельную страницу `/account`.
  Имя в шапке стало ссылкой ([nav.tsx:40-69](frontend/src/nav.tsx#L40-L69)), `AccountPage` в
  [AccountPages.tsx:384-424](frontend/src/AccountPages.tsx#L384-L424), маршрут в [main.tsx](frontend/src/main.tsx),
  в `e2e_smoke.py` проверка переехала на новую страницу (скриншот `6-account.png`).
- `e27f1c6` — поля «Текущий пароль» / «Новый пароль» в столбик (убран `div.row`), ширину полям задаёт
  `.account-pw .field`: при `align-items: flex-start` они сжимались до ширины подписи.
- `c45763a` — форма смены пароля плотнее: `gap` 10px, подпись 4px, поле 44px, подсказка 13px
  ([styles.css:309-314](frontend/src/styles.css#L309-L314)).

Проверено: 120 unit-тестов + `e2e_smoke.py` проходят, фронтенд собран.

## 3. Рейтинг и часы у заведений (закрыто, `18a5c3e`)

В описании пункта с едой теперь всегда и рейтинг Яндекса, и часы работы. Хвост описания собирает
[planner.py:590-612](backend/app/planner.py#L590-L612) `food_item` из полей места: чек → своё описание →
`rating_text` (`rating`/`reviews` → «★ 5,0 на Яндекс Картах (10154)») → `hours_text` (`hours` → «Работает
09:00–00:00, пт и сб до 01:00», выходные из `closed`, разное закрытие по дням из `late`). У заглушек
(`placeholder`) `hours_text` молчит: их часы — ориентир для подбора, а не настоящие.

Из `yandexmaps.parse` рейтинг в `note` убран, чтобы не дублировался, добавлено поле `reviews`; в «Заменить»
часы идут в `meta` ([main.py:116-117](backend/app/main.py#L116-L117)), рейтинг `Picks` рисует из поля `rating` —
теперь оно есть и у каталожных мест, так что `by_rating_and_distance` сортирует по настоящим данным.

Попутно исправлен дефект: `opening` при закрытии после полуночи («09:00–01:00») возвращала пустой интервал,
то есть «закрыто весь день» ([planner.py:411-415](backend/app/planner.py#L411-L415)).

Данные: в `data/spb_places.json` вписаны `ymaps_id` 11 заведений и прогнан `tools/yandex_facts.py --write`
(скрипт закоммичен, описан в README). Устаревшие приписки в `note` («часы уточните», «в выходные с 9:00
до 21:00») убраны там, где настоящие часы пришли с Карт.

Тесты: 120 проходят, добавлены `test_food_note_has_rating_and_hours` и `test_hours_text`.
`test_budget_tolerance` переписан: жёсткая сумма 58 000 зависела от того, какие места выберет планировщик,
теперь лимит считается от стоимости готовой поездки.

Осталось: `f_singer` (по адресу Карты отдают «Рене», Невский 28 — проверить, то ли это), `f_vasilyevsky2`
(Штолле на 1-й линии В. О. поиск рядом не нашёл) и 2 реальных заведения Казани (`f_tubetey`, `f_usadba`).
В Калининграде и Н. Новгороде все заведения — заглушки, рейтинга у них быть не может.

## 4. Стек

- **Backend:** Python 3.11+, Starlette, uvicorn (`--reload`, systemd), SQLite, python-multipart, pypdf,
  certifi; внешние сервисы — YandexGPT (разбор пожеланий, по умолчанию), Yandex Vision OCR (билеты),
  DaData или Photon (адреса), Трипстер API 2.0, Суточно.ру и Яндекс Карты парсингом страниц, SMTP reg.ru.
- **Frontend:** React 19 + TypeScript 6, сборка esbuild (`build.mjs` → `dist/assets/app.js`, `app.css`),
  без роутера и UI-библиотек, свой `styles.css`.
- **Тесты:** unittest (118), Playwright + Chromium в `backend/.venv` (`tests/e2e_smoke.py`).
- Ключи и настройки — `backend/.env` (образец `.env.example`), после правки `systemctl restart tripplan`.

Команды проверки: `cd backend && .venv/bin/python -m unittest discover -s tests`,
`.venv/bin/python tests/e2e_smoke.py <папка>`, `cd frontend && npx tsc --noEmit`
(ошибка про `./styles.css` в `main.tsx` была и раньше).

## 5. Следующие шаги

1. Разобраться с `f_singer` и `f_vasilyevsky2`; то же для Казани, а Калининград и Н. Новгород — только после
   появления реальных заведений вместо заглушек (раздел 3).
2. По плану (`roadmap.md`) ближайшее — **этап C.0**: настройки из env, PostgreSQL вместо SQLite (Alembic),
   документы в S3, каталог мест в БД, Docker + compose, лимиты загрузки и rate limit; затем VPS в России,
   домен, nginx + Let's Encrypt.
3. Из незакрытого по данным: проверка каталогов Казани, Н. Новгорода, Калининграда; ночные поезда
   (прибытие на следующие сутки); сезонность (`months`); часовые пояса городов.
4. Этап B: черновики карточек мест моделью, объяснение выбора мест, правка программы словами.
