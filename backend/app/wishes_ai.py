"""Разбор свободного текста пожеланий языковой моделью.

Текст вроде «хочется спокойно, без музеев, но с хорошей едой и обязательно в Свияжск» превращается
в настройки планировщика: интересы, флаги пожеланий, чего избегать, какие места обязательно,
темп. Расписание по-прежнему считает алгоритм в planner.py — модель только понимает слова.

Модели (backend/.env):
- YandexGPT (по умолчанию, доступна в России): YANDEX_GPT_API_KEY и YANDEX_FOLDER_ID из Yandex Cloud;
  YANDEX_GPT_MODEL — yandexgpt/latest (Pro, по умолчанию) или yandexgpt-lite/latest.
- Claude: ANTHROPIC_API_KEY; если заданы оба ключа — LLM_PROVIDER=anthropic.

Без ключей или при ошибке остаётся разбор по ключевым словам (planner.parse_wishes). Вызов один —
при создании поездки; результат хранится в trip["input"]["wishes_ai"] вместе с текстом, по которому получен.
"""
from __future__ import annotations

import json
import os
import urllib.request

from . import catalog, net, planner

CLAUDE_MODEL = "claude-opus-5-5"
YANDEX_URL = os.environ.get("YANDEX_GPT_URL", "https://ai.api.cloud.yandex.net/foundationModels/v1/completion")
TIMEOUT = 30
# Флаги, которые понимает планировщик (ключи parse_wishes, кроме интересов)
WISH_FLAGS = ["theatre", "opera", "circus", "cheap", "no_long_walks", "boat", "vegetarian"]
AVOID_CATEGORIES = ["museum", "walk", "landmark", "theatre", "daytrip"]

SYSTEM = """Ты помогаешь планировщику самостоятельных поездок по России. Пользователь написал пожелания \
к поездке свободным текстом. Переведи их в настройки планировщика и ответь только JSON по схеме.

- interests — что человеку интересно: history (история), art (искусство), architecture (архитектура), \
nature (природа и парки), views (виды на город), local_food (местная кухня, попробовать местные блюда).
- wishes — флаги: theatre (хочет в театр или на концерт), opera (опера или балет), circus (цирк), \
cheap (подешевле), no_long_walks (мало ходить пешком), boat (прогулка на теплоходе), \
vegetarian (вегетарианская еда).
- avoid — категории, которых просят избегать: museum, walk (пешие прогулки), landmark, theatre, \
daytrip (выезды за город).
- must_visit — id мест из списка, которые человек прямо назвал или явно хочет посетить.
- skip_places — id мест из списка, куда человек не хочет.
- pace — темп, если о нём сказано: max (увидеть побольше), balanced (2–3 места в день), \
calm (1–2 места в день, спокойно), relaxed (на расслабоне, без спешки). Если о темпе ничего нет — пустая строка.
- summary — одно-два коротких предложения по-русски для пользователя: как ты понял пожелания. \
Без вступлений, начни с сути, например: «Спокойный темп, без музеев, побольше местной кухни; Свияжск — обязательно».

Главное правило: отмечай только то, что прямо сказано в тексте. Ничего не додумывай — пустой список лучше лишнего.
- avoid — только при явном отказе: «без музеев», «не хотим в театр», «никаких выездов». Желание чего-то другого \
(«к морю», «подешевле», «ребёнок устаёт») — не повод что-то избегать.
- vegetarian — только если упомянуты вегетарианство, веганство или пост. «Хорошая еда» — это local_food.
- Отрицания важны: «без театра» — avoid theatre, а не wishes theatre. «На балет» — wishes opera и theatre, avoid без theatre.
- interests — только названные интересы; по одному названию места интерес не выводи.
- pace — только если о темпе или усталости сказано; иначе пустая строка.
- В must_visit и skip_places — только id из списка мест. Названный театр («в Мариинку», «в театр Камала») — тоже must_visit.

Пример. «Хотим на балет и в Раифу, в Свияжск не поедем» -> wishes: [opera, theatre], must_visit: [raifa], \
skip_places: [sviyazhsk], остальные списки пустые, pace: "".
Пример. «Спокойно, без музеев, но с хорошей едой» -> interests: [local_food], avoid: [museum], pace: calm, \
wishes: [], остальные списки пустые."""


def provider() -> str | None:
    """yandex / anthropic / None (ключей нет — разбор по ключевым словам)."""
    has_yandex = bool(os.environ.get("YANDEX_GPT_API_KEY") and os.environ.get("YANDEX_FOLDER_ID"))
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    want = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if want == "anthropic" and has_claude:
        return "anthropic"
    if want == "yandex" and has_yandex:
        return "yandex"
    return "yandex" if has_yandex else "anthropic" if has_claude else None


def available() -> bool:
    return provider() is not None


def _places(city_file: str) -> list[dict]:
    data = catalog.load(city_file)
    return [p for p in data["places"] if p["category"] in (*planner.SIGHTS, "theatre")]


def _schema(ids: list[str]) -> dict:
    def arr(values: list[str]) -> dict:
        return {"type": "array", "items": {"type": "string", "enum": values}}

    return {
        "type": "object",
        "properties": {
            "interests": arr(list(planner.INTERESTS)),
            "wishes": arr(WISH_FLAGS),
            "avoid": arr(AVOID_CATEGORIES),
            "must_visit": arr(ids),
            "skip_places": arr(ids),
            "pace": {"type": "string", "enum": ["", *planner.PACE_CAPACITY]},
            "summary": {"type": "string"},
        },
        "required": ["interests", "wishes", "avoid", "must_visit", "skip_places", "pace", "summary"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------ providers

def _post_json(req: urllib.request.Request) -> dict:
    return net.get_json(req, TIMEOUT)


def ask_yandex(user: str, schema: dict, system: str = SYSTEM, max_tokens: int = 2000) -> dict | None:
    """YandexGPT, синхронный completion со структурированным ответом (jsonSchema)."""
    folder = os.environ["YANDEX_FOLDER_ID"].strip()
    model = os.environ.get("YANDEX_GPT_MODEL", "").strip() or "yandexgpt/latest"
    body = {
        "modelUri": f"gpt://{folder}/{model}",
        "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": str(max_tokens)},
        "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
        "jsonSchema": {"schema": schema},
    }
    req = urllib.request.Request(YANDEX_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={
        "Content-Type": "application/json",
        "Authorization": "Api-Key " + os.environ["YANDEX_GPT_API_KEY"].strip(),
        "x-folder-id": folder,
    })
    data = _post_json(req)
    data = data.get("result", data)  # REST отдаёт ответ в result; в справочнике — без обёртки
    alt = (data.get("alternatives") or [{}])[0]
    if alt.get("status") != "ALTERNATIVE_STATUS_FINAL":  # обрезан, фильтр содержимого — по ключевым словам
        print(f"[wishes_ai] yandex status={alt.get('status')}", flush=True)
        return None
    return json.loads(alt["message"]["text"])


def _anthropic_client():
    import anthropic  # только когда выбран Claude: без него приложение работает и без пакета

    return anthropic.Anthropic(timeout=float(TIMEOUT), max_retries=1)


def ask_anthropic(user: str, schema: dict, system: str = SYSTEM, max_tokens: int = 4000) -> dict | None:
    response = _anthropic_client().beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason != "end_turn":  # refusal, max_tokens — оставляем разбор по ключевым словам
        print(f"[wishes_ai] claude stop_reason={response.stop_reason}", flush=True)
        return None
    return json.loads(next(b.text for b in response.content if b.type == "text"))


ASK = {"yandex": ask_yandex, "anthropic": ask_anthropic}


def ask(system: str, user: str, schema: dict, max_tokens: int = 2000) -> dict | None:
    """Структурированный ответ выбранной модели (YandexGPT или Claude); None — нет ключей или отказ."""
    name = provider()
    return ASK[name](user, schema, system, max_tokens) if name else None


def clean(raw: dict, ids: list[str], theatres: set[str] = frozenset()) -> dict:
    """Только допустимые значения (схему соблюдает не каждая модель) и без противоречий."""
    def pick(key: str, allowed) -> list[str]:
        vals = raw.get(key) if isinstance(raw.get(key), list) else []
        return list(dict.fromkeys(v for v in vals if v in allowed))

    wishes, avoid, must = pick("wishes", WISH_FLAGS), pick("avoid", AVOID_CATEGORIES), pick("must_visit", ids)
    if set(wishes) & {"theatre", "opera", "circus"} or any(i in theatres for i in must):
        avoid = [a for a in avoid if a != "theatre"]  # просьба «на балет» важнее «без театра» в том же ответе
    pace = raw.get("pace") if raw.get("pace") in planner.PACE_CAPACITY else ""
    summary = raw.get("summary") if isinstance(raw.get("summary"), str) else ""
    return {"interests": pick("interests", planner.INTERESTS), "wishes": wishes, "avoid": avoid, "must_visit": must,
            "skip_places": pick("skip_places", ids), "pace": pace, "summary": summary.strip()[:400]}


def parse(text: str, city: str) -> dict | None:
    """Пожелания -> настройки; None, если разобрать не удалось (нет ключа, отказ модели)."""
    fn, name = catalog.normalize_city(city), provider()
    if not fn or not text.strip() or not name:
        return None
    places = _places(fn)
    ids = [p["id"] for p in places]
    listing = "\n".join(f"{p['id']}: {p['name']}" for p in places)
    user = (f"Город: {catalog.load(fn)['city']}\n\nМеста в каталоге (id: название):\n{listing}\n\n"
            f"Пожелания пользователя:\n{text.strip()}")
    raw = ASK[name](user, _schema(ids))
    if not isinstance(raw, dict):
        return None
    theatres = {p["id"] for p in places if p["category"] == "theatre"}
    return dict(clean(raw, ids, theatres), text=text.strip(), city=catalog.load(fn)["city"], source=name)


def apply(body: dict) -> dict:
    """Перед построением поездки: разобрать пожелания (если текст новый) и подставить темп.

    Темп из пожеланий подставляем, только если в форме оставлен темп по умолчанию."""
    text = (body.get("wishes") or "").strip()
    prev = body.get("wishes_ai")
    if not text:
        body["wishes_ai"] = None
        return body
    fn = catalog.normalize_city(body.get("city", ""))
    city = catalog.load(fn)["city"] if fn else None
    if isinstance(prev, dict) and prev.get("text") == text and prev.get("city") == city:
        return body  # уже разобрано — например, поездку перестраивают из вводных
    body["wishes_ai"] = None
    if not available():
        return body
    try:
        ai = parse(text, body.get("city", ""))
    except Exception as e:  # сеть, ключ, лимиты, неверный JSON — поездка строится и без этого
        print(f"[wishes_ai] {provider()}: {type(e).__name__}: {e}", flush=True)
        return body
    if ai is None:
        return body
    if ai["pace"] and body.get("pace", "balanced") == "balanced" and ai["pace"] != "balanced":
        body["pace"] = ai["pace"]
        ai["pace_applied"] = True
    body["wishes_ai"] = ai
    return body
